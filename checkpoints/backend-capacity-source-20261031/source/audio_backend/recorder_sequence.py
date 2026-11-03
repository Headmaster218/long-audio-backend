"""Bounded read-only declaration reconciliation over explicit owned snapshots.

No inference, database, source deletion, clock repair, gap fill or ingestion.
Matching declarations are not proof of physical continuity or capture authenticity.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from .recorder_snapshot import inspect_snapshot, _deadline, _open_directory
from .recorder_delivery import parse_envelope, Refused, MAX_METADATA
from .recorder_metadata import decode, InvalidMetadata

MAX_SNAPSHOTS = 32
MAX_INPUT_BYTES = 512 * 1024 * 1024


def require(ok,reason):
    if not ok:raise Refused(reason)


def _record(path,index,remaining):
    observed=inspect_snapshot(path,deadline_seconds=remaining())
    delivery=observed['delivery']
    dfd=_open_directory(path)
    try:
        fd=os.open('segment.v1.bin',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=dfd)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_size<=MAX_METADATA,'sequence_metadata_size')
            remaining();data=stream.read(MAX_METADATA+1)
    finally:os.close(dfd)
    local=parse_envelope(data,delivery,return_local=True)
    try: metadata=decode(local)
    except InvalidMetadata as exc:raise Refused(str(exc)) from exc
    return dict(input_index=index,delivery=delivery,metadata=metadata,
                local_metadata_sha256=hashlib.sha256(local).hexdigest())


def _record_warnings(metadata):
    anchors=metadata['anchors']
    if anchors is None:return ['anchor_observations_absent']
    warnings=[]
    if anchors['flags'] & ~1 or anchors['anomaly_count']>0:warnings.append('anchor_anomaly_observations_present')
    if anchors['unavailable_count']>0:warnings.append('anchor_queries_unavailable')
    return warnings


def _edge(previous,current,conflicted):
    a,b=previous['metadata'],current['metadata']
    reasons=[];warnings=[];missing=b['sequence']-a['sequence']-1
    if missing:reasons.append('missing_ordinals')
    if a['sequence'] in conflicted or b['sequence'] in conflicted:reasons.append('conflicting_ordinal_content')
    if b['epoch']<a['epoch']:reasons.append('epoch_regression')
    elif b['epoch']>a['epoch']:
        reasons.append('epoch_boundary_unmapped')
        if b['epoch']>a['epoch']+1:reasons.append('missing_epoch_declarations')
        if not missing and b['frame_start']!=0:reasons.append('new_epoch_nonzero_start')
        if b['boundary']=='START':reasons.append('new_epoch_start_boundary')
        if b['trusted_previous_seam']:reasons.append('cross_epoch_seam_conflict')
        if b['run_id']!=a['run_id'] and b['boundary']!='RESTART':reasons.append('new_run_without_restart')
        if b['run_id']==a['run_id'] and b['boundary']=='RESTART':reasons.append('restart_without_new_run')
    else:
        if any(a[k]!=b[k] for k in ('run_id','sample_rate_hz','channels','requested_input','actual_input','boundary','target_frames')):
            reasons.append('same_epoch_declaration_conflict')
        delta=b['frame_start']-(a['frame_start']+a['frames'])
        if delta>0:reasons.append('declared_frame_gap')
        elif delta<0:reasons.append('declared_frame_overlap')
        if a['close_reason']!='ROTATION':reasons.append('previous_segment_not_rotation')
        if not b['trusted_previous_seam']:reasons.append('previous_seam_not_trusted')
        if b['close_reason']=='RECOVERED_TAIL':reasons.append('recovered_tail')
        if missing==0:
            aa,bb=a['anchors'],b['anchors']
            if (aa is None)!=(bb is None):reasons.append('observation_mode_changed')
            elif aa is not None:
                last_read,first_read=aa['observations'][1],bb['observations'][0]
                if last_read['domain']!=first_read['domain']:reasons.append('anchor_clock_domain_changed')
                if aa['sample_end']!=bb['sample_start']:reasons.append('anchor_run_samples_discontinuous')
                step=first_read['ordinal']-last_read['ordinal']
                if step==0:
                    if last_read!=first_read:reasons.append('shared_read_observation_conflict')
                elif step==1:
                    if last_read['samples_after']!=first_read['samples_before']:reasons.append('anchor_read_samples_discontinuous')
                else:reasons.append('anchor_read_ordinals_discontinuous')
    for record in (a,b):warnings.extend(_record_warnings(record))
    return dict(previous_input=previous['input_index'],current_input=current['input_index'],
                previous_sequence=a['sequence'],current_sequence=b['sequence'],
                relation='declared_adjacent' if not reasons else 'unresolved_boundary',
                missing_ordinal_count=missing,reasons=reasons,warnings=sorted(set(warnings)),
                physical_continuity_proven=False,hardware_frame_mapping='UNESTABLISHED')


def _report(records):
    """Pure declaration analysis; only reconcile_snapshots validates input files."""
    grouped={}
    for record in records:
        capture=record['delivery']['capture_identity_sha256']
        grouped.setdefault(capture,[]).append(record)
    captures=[]
    for capture,rows in sorted(grouped.items()):
        ordered=sorted(rows,key=lambda row:(row['metadata']['sequence'],row['input_index']))
        first={};duplicates=[];conflicted=set()
        for row in ordered:
            seq=row['metadata']['sequence']
            if seq not in first:first[seq]=row;continue
            previous=first[seq]
            same=(row['local_metadata_sha256']==previous['local_metadata_sha256'] and
                  row['delivery']['wav_sha256']==previous['delivery']['wav_sha256'])
            if not same:conflicted.add(seq)
            duplicates.append(dict(first_input=previous['input_index'],repeated_input=row['input_index'],sequence=seq,
                                   relation='same_content_repeat' if same else 'conflicting_ordinal_content'))
        unique=list(first.values())
        edges=[_edge(a,b,conflicted) for a,b in zip(unique,unique[1:])]
        first_seq=unique[0]['metadata']['sequence']
        captures.append(dict(capture_identity_sha256=capture,input_count=len(rows),unique_ordinal_count=len(unique),
                             first_supplied_sequence=first_seq,prefix_not_supplied=first_seq!=0,
                             first_supplied_frame_start=unique[0]['metadata']['frame_start'],
                             predecessor_not_supplied=(first_seq!=0 or unique[0]['metadata']['frame_start']!=0 or unique[0]['metadata']['trusted_previous_seam']),
                             last_supplied_close=unique[-1]['metadata']['close_reason'],capture_completeness_proven=False,
                             conflicting_ordinals=sorted(conflicted),duplicates=duplicates,edges=edges,
                             warning_codes=sorted({warning for row in rows for warning in _record_warnings(row['metadata'])})))
    return dict(schema='recorder-sequence-observation-v1',snapshot_count=len(records),captures=captures,
                observations_only=True,physical_continuity_proven=False,
                hardware_frame_mapping='UNESTABLISHED',strict_import_ready=False,
                gap_repair_performed=False)


def _verified_records(paths,remaining):
    require(type(paths) in (list,tuple) and 1<=len(paths)<=MAX_SNAPSHOTS,'sequence_snapshot_count')
    records=[];total=0
    for index,path in enumerate(paths):
        # Point-in-time precheck bounds ordinary inputs; not a hard global I/O
        # meter against concurrent same-user mutation. Each inspector also bounds
        # every file and shares the remaining cooperative wall-time budget.
        info=os.stat(Path(path)/'audio.wav',follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_size>44,'sequence_audio_regular')
        require(total+info.st_size<=MAX_INPUT_BYTES,'sequence_input_bytes')
        row=_record(path,index,remaining)
        require(row['delivery']['wav_bytes']==info.st_size,'sequence_input_changed')
        total+=row['delivery']['wav_bytes'];records.append(row)
    remaining()
    return records,total


def reconcile_snapshots(paths,*,deadline_seconds=120):
    records,total=_verified_records(paths,_deadline(deadline_seconds))
    report=_report(records);report['observed_wav_bytes']=total
    return report

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshots',nargs='+',type=Path)
    args=parser.parse_args(argv)
    try:result=reconcile_snapshots(args.snapshots)
    except (OSError,Refused) as exc:
        print(json.dumps(dict(error=str(exc) if isinstance(exc,Refused) else 'sequence_file_access_failed',strict_import_ready=False)))
        return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
