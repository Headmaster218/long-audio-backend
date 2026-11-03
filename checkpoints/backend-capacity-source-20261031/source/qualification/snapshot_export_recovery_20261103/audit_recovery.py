"""Independent same-job snapshot export audit. Never starts services or inference.

Canonical/native checks adapted narrowly from the pinned 2026-11-03 audit_export,
SHA256 9a6d6e1b18e5d0a668cce4a6fdda63c9263c3286f824271157959ef6ff264f6e.
Historical nine-input/watch-success assertions are deliberately not reused.
Only already-closed copied databases are opened. Original sources are read only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import wave
P=Path
sys.path.insert(0,str(Path(__file__).resolve().parent))


def need(value,reason):
    if not value:raise RuntimeError(reason)


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def load(path):
    path=Path(path);need(path.stat().st_size<=4*1024**2,'json_size')
    return json.loads(path.read_bytes())


def pcm(path):
    with wave.open(str(path),'rb') as source:
        need((source.getnchannels(),source.getsampwidth(),source.getframerate(),source.getcomptype())==(1,2,16000,'NONE'),'pcm_format')
        need(source.getnframes()==144000,'pcm_frames');data=source.readframes(144001)
        need(len(data)==288000,'pcm_extent');return data


def original_inventory(root):
    root=Path(root);result={}
    for path in sorted(root.rglob('*')):
        need(not path.is_symlink(),'original_symlink')
        if path.is_file():
            info=path.stat();result[str(path.relative_to(root))]={'bytes':info.st_size,
                'mtime':info.st_mtime_ns,'ctime':info.st_ctime_ns,'sha256':sha(path)}
    return result


def artifact_pair(first,second):
    for root in (Path(first),Path(second)):
        need(root.is_dir() and not root.is_symlink() and stat.S_IMODE(root.stat().st_mode)==0o700,'private_export_directory')
        need({p.name for p in root.iterdir()}=={'export.json','transcript.txt','complete.json'},'export_exact_inventory')
        for path in root.iterdir():
            info=path.lstat();need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600
                and info.st_nlink==1 and info.st_uid==os.geteuid(),'private_export_file')
        manifest=load(root/'complete.json')
        need(manifest['schema']=='recorder-incremental-span-export-v1' and manifest['status']=='exported'
             and set(manifest['artifacts'])=={'export.json','transcript.txt'},'export_manifest')
        for name,pin in manifest['artifacts'].items():
            need(pin=={'bytes':(root/name).stat().st_size,'sha256':sha(root/name)},'export_artifact_hash')
    for name in ('export.json','transcript.txt','complete.json'):
        need((Path(first)/name).read_bytes()==(Path(second)/name).read_bytes(),'exact_export_repeat')
    return load(Path(first)/'complete.json')


def audit_canonical(export_dir,db,cells_copy,work,meta,status,sid):
    from audio_backend import transcript_contract as c, recording_primary_provenance as pp
    from audio_backend import native_owned_core_seams as core
    import unicodedata
    document=load(export_dir/'export.json');manifest=load(export_dir/'complete.json');units=document['units']
    need(document['completion']==status,'export_status_linkage')
    need(document['summary']=={k:manifest[k] for k in document['summary']},'export_summary')
    need(manifest['frames']==144000 and manifest['transcript_pages']==1 and manifest['units']==len(units) and manifest['nonempty_text_units']==sum(bool(u['text'].strip()) for u in units)==5,'export_count')
    need(document['projection']=='primary-baseline-v1' and document['sample_rate']==16000 and document['remote_read_only'] is True and document['complete_content'] is False and document['eos'] is None,'export_scope')
    need(all(document[k] is False for k in ['speaker_identity_valid','word_alignment_verified','accuracy_verified']) and document['word_times'] is None,'export_no_accuracy_claim')
    need(document['capture_gap_policy']=='before_and_after_unknown_no_gap_filling' and document['speaker_policy']=='anonymous_source_local_no_cross_scope_identity' and document['optional_transcript_and_speaker_links']=='not_exported','export_uncertainty')
    need(manifest['complete_content'] is False and manifest['observation_atomic'] is False,'export_nonatomic')
    fields=('id','chunk_id','start','end','text','raw_text','asr_backend','timestamp_kind','confidence','speaker_local_id','speaker_window_start')
    segments=[dict(row) for row in db.execute('SELECT '+','.join(fields)+' FROM segments ORDER BY start,id')]
    need(len(segments)==len(units) and len({u['source_ref'] for u in units})==len(units),'exact_segment_inventory')
    expected_refs=set()
    by_id={v['id']:v for v in segments}
    def canonical(v):return json.dumps(v,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(',',':')).encode('ascii')
    def timestamp(seconds):
        micros=round(seconds*1000000);whole,fraction=divmod(micros,1000000);minutes,second=divmod(whole,60);hour,minute=divmod(minutes,60)
        return f'{hour:02d}:{minute:02d}:{second:02d}.{fraction:06d}'
    for u in units:
        value=u['canonical_asr'];row=by_id[value['id']];expected=dict(session_id=sid,**row);digest=hashlib.sha256(canonical(expected)).hexdigest()
        need(value==expected and u['content_sha256']==digest and u['source_ref']==f"p.{row['id']}.{digest}" and u['source']=='primary','canonical_asr_identity')
        expected_refs.add(u['source_ref'])
        need(row['chunk_id']==work[0]['chunk_id'] and row['asr_backend']=='sensevoice' and row['timestamp_kind']=='Native CTC token starts; interval ends are estimated, not forced alignment','actual_asr_segment')
        need(u['text']==row['text'] and u['raw_text']==row['raw_text'] and u['start_seconds']==row['start'] and u['end_seconds']==row['end'],'exact_export_text_times')
        need(u['start_timestamp']==timestamp(row['start']) and u['end_timestamp']==timestamp(row['end']) and 0<=u['start_frame']<u['end_frame']<=144000,'export_timing_geometry')
        need(u['speaker_identity_valid'] is False and u['accuracy_verified'] is False and u['word_times'] is None and u['person_identity']=='not_projected','export_unit_uncertainty')
        expected_scope=[row['chunk_id'],row['speaker_window_start'],row['speaker_local_id']] if row['speaker_local_id'] is not None and row['speaker_window_start'] is not None else None
        need(u['speaker_scope']==expected_scope,'anonymous_source_local_speaker')
    def quote(value):
        raw=json.dumps(value,ensure_ascii=False)
        return ''.join(('\\u%04x'%ord(ch)) if unicodedata.category(ch) in ('Cc','Cf','Zl','Zp') else ch for ch in raw)
    expected_text=('PRIMARY BASELINE TRANSCRIPT / SELECTED SPAN ONLY\n'
        'Original ASR segment timestamps, not verified word alignment or recognition.\n'
        'Speakers are anonymous source-local labels, never a global person identity.\n'
        'Before/after capture gaps are unknown. Missing text does not establish silence.\n'
        'Optional MOSS substitutions and recording speaker links are not exported.\n'
        'Requires complete.json. Each transcript string is quoted; controls are escaped.\n\n')
    for u in units:
        scope=quote(u['speaker_scope']) if u['speaker_scope'] is not None else 'unknown'
        expected_text+=f"[{u['start_timestamp']} - {u['end_timestamp']}] anonymous source-local {scope}: {quote(u['text'])}\n"
    expected_text+='\nWork chunks without primary text: 0/1. No silence or completeness claim.\n'
    need((export_dir/'transcript.txt').read_bytes()==expected_text.encode('utf-8'),'human_text_exact_linkage')
    coverage=document['work_text_coverage'];need(len(coverage)==1 and coverage[0]==dict(chunk_id=work[0]['chunk_id'],span_start_frame=0,span_end_frame=144000,primary_units=len(units),nonempty_text_units=sum(bool(u['text'].strip()) for u in units),text_status='primary_text_observed'),'work_text_coverage')
    need(meta['asr_runtime'].get('provider')=='cpu' and meta['asr_runtime'].get('model')=='SenseVoiceSmall int8' and meta['stage_seconds']['asr']>0 and meta['asr_turn_batches']>0 and not meta.get('asr_suppressed_as_nonspeech',False),'actual_asr_runtime_receipt')
    packets=[dict(r) for r in db.execute('SELECT * FROM recording_primary_packets_v1')]
    outcomes=[dict(r) for r in db.execute('SELECT * FROM strict_recording_outcome ORDER BY start_frame')]
    commits={r['slot']:dict(r) for r in db.execute('SELECT * FROM native_core_commits')}
    need(len(outcomes)==len(commits)==len(packets)==2 and all(row['status']=='retained' for row in outcomes),'native_core_outcomes');position=0;bound_refs=set();pcmbytes=pcm(work[0]['path']);by_slot={r['core_slot']:r for r in packets}
    db.execute('BEGIN')
    try:
        def source_current(part):return part['session_id']==sid and part['chunk_id']==work[0]['chunk_id'] and part['source_sha256']==work[0]['digest'] and part['source_frames']==144000
        for out in outcomes:
            need(out['cid']==work[0]['chunk_id'] and out['source']==work[0]['digest'] and out['start_frame']==position and position<out['end_frame']<=144000,'native_outcome_coverage');position=out['end_frame']
            commitrow=commits[out['slot']];commit=json.loads(commitrow['body']);core.validate_commit(commit)
            need(commitrow['state']=='none' and commitrow['pending'] is None and core.slot(commit)==out['slot'] and core.canonical(commit,core.COMPACT_BYTES)==commitrow['body'] and source_current(commit['part']),'native_compact_binding')
            need(commit['part']['source_start']==out['start_frame'] and commit['part']['source_end']==out['end_frame'],'native_compact_range')
            if out['status'] in ('retained','existing'):
                packetrow=by_slot[out['slot']];packet=json.loads(packetrow['body']);header={k:packetrow[k] for k in packetrow if k!='body'};saved=dict(header=header,packet=packet)
                pp.validate_packet(packet,header)
                need(hashlib.sha256(packetrow['body']).hexdigest()==packetrow['digest']==out['packet'] and packet['commit']==commit and pp.current(db,saved,source_current=source_current),'primary_packet_linkage')
                need(packet['owned_pcm16le_sha256']==hashlib.sha256(pcmbytes[out['start_frame']*2:out['end_frame']*2]).hexdigest(),'primary_packet_pcm')
                bound_refs.update(r[0] for r in packet['refs'])
            else:
                need(out['status'] in ('empty','unknown_only') and out['packet'] is None and out['slot'] not in by_slot,'packetless_outcome')
                row=db.execute('SELECT * FROM strict_payload_core_witness WHERE slot=?',(out['slot'],)).fetchone();need(row is not None,'packetless_witness')
                doc=json.loads(row['body']);need(hashlib.sha256(row['body']).hexdigest()==row['digest'] and doc['commit']==commit and doc['status']==out['status'],'packetless_binding')
                need(doc['owned_pcm16le_sha256']==hashlib.sha256(pcmbytes[out['start_frame']*2:out['end_frame']*2]).hexdigest(),'packetless_pcm')
                for ident,digest,size in doc['primary_refs']:
                    row=by_id[ident];need(hashlib.sha256(canonical({k:row[k] for k in fields if k!='id'})).hexdigest()==digest and row['speaker_local_id'] is None,'packetless_primary_hash')
                    bound_refs.add(c.primary_evidence(sid,row)['source_ref'])
        need(position==144000 and bound_refs==expected_refs,'all_exported_text_receipt_bound')
    finally:db.rollback()
    need(all(db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]==0 for t in ['moss_automatic_intents_v1','moss_retained_results_v1']),'primary_only_no_moss')
    from audio_backend.native_owned_cell_store import Receipt
    copy_cells=Path(cells_copy).resolve()
    cells=sqlite3.connect(copy_cells.as_uri()+'?mode=ro&immutable=1',uri=True);cells.row_factory=sqlite3.Row
    try:
        need(cells.execute('PRAGMA integrity_check').fetchone()[0]=='ok','receipt_database_integrity')
        sources=[dict(v) for v in cells.execute('SELECT * FROM sources')];need(len(sources)==1,'native_receipt_count')
        receipt=Receipt.decode(sources[0]['receipt']);info=P(work[0]['path']).stat()
        need(sources[0]['cid']==receipt.chunk_id==work[0]['chunk_id'] and sources[0]['sid']==receipt.session_id==sid and sources[0]['digest']==receipt.digest==work[0]['digest'],'native_receipt_identity')
        need(receipt.frames==144000 and receipt.timeline_start==0.0 and receipt.signature==(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns),'native_receipt_source_signature')
        need(cells.execute('SELECT COUNT(*) FROM cells').fetchone()[0]==0,'primary_only_no_owned_pcm_cells')
    finally:cells.close()
    return dict(native_source_receipt_id=receipt.receipt_id,native_source_receipt_exact=True,passed=True,export_operations_get_only=2,export_transcript_pages=1,primary_units=len(units),nonempty_primary_units=sum(bool(u['text'].strip()) for u in units),exact_repeat_artifacts=True,private_directory_mode='0700',private_file_mode='0600',canonical_asr_db_identity_exact=True,all_exported_refs_native_receipt_bound=True,retained_primary_packets=len(packets),native_core_outcomes=len(outcomes),native_core_pcm_coverage_exact=True,asr_runtime=meta['asr_runtime'],asr_stage_seconds=meta['stage_seconds']['asr'],asr_turn_batches=meta['asr_turn_batches'],asr_backend='sensevoice',speaker_scope_count=len({tuple(u['speaker_scope']) for u in units if u['speaker_scope'] is not None}),accuracy_verified=False,speaker_identity_verified=False,capture_completeness_proven=False,artifacts=manifest['artifacts'],complete_manifest_sha256=sha(export_dir/'complete.json'))


def check_get_phases(trace,roots,sid,manifest,*,capture_id=None,job_dir=None):
    from audio_backend.transcript_contract import cursor
    need(isinstance(trace,list) and 0<len(trace)<=256,'host_trace_bound')
    need(all(set(row)=={'method','path','status'} and row['method']=='GET' and row['status']==200
             and row['path'].startswith('/sessions/'+sid+'/') for row in trace),'get_only_actual_reads')
    offset=0;counts=[]
    for root in roots:
        log=Path(root)/'recovery.jsonl';need(log.is_file() and log.stat().st_size<=16384,'recovery_log')
        events=[json.loads(line) for line in log.read_text().splitlines()]
        need(len(events)==3 and events[0]['remote_read_only'] is True and events[0]['inference_replay'] is False
             and events[0]['session_id']==sid,'recovery_header')
        if capture_id is not None:need(events[0]['capture_id']==capture_id,'recovery_capture')
        if job_dir is not None:need(events[0]['job_dir']==str(job_dir),'recovery_job')
        need(events[1]['attempt']==1 and events[1]['status']=='started' and events[1]['output']=='attempt-01','single_recovery_attempt')
        terminal=events[2];need(terminal['attempt']==1 and terminal['status']=='exported'
             and terminal['manifest']=='attempt-01/complete.json' and terminal['transcript_revision']==manifest['transcript_revision'],'recovery_commit')
        count=terminal['requests'];need(type(count) is int and 0<count<=128,'recovery_request_count')
        phase=trace[offset:offset+count];need(len(phase)==count,'host_phase_count');offset+=count;counts.append(count)
        transcripts=[row['path'] for row in phase if '/transcript?' in row['path']]
        base='/sessions/'+sid+'/transcript?'
        need(transcripts==[base+'block=0&projection=whole-primary-v1',
            base+'cursor='+cursor(sid,0,manifest['transcript_revision'])+'&projection=whole-primary-v1'],'actual_two_pass_transcript_reads')
    need(offset==len(trace),'unaccounted_host_requests')
    return counts


def audit(spec):
    from snapshot_http import verify_source
    root=verify_source(spec['source_root'],spec['source_manifest'],spec['source_manifest_sha256'])
    need(Path(__file__).resolve().is_relative_to(root),'auditor_outside_pinned_source')
    sys.path.insert(0,str(root))
    original=Path(spec['original_run']).resolve();before=original_inventory(original)
    need(sha(spec['original_launch_spec'])==spec['original_launch_spec_sha256'],'original_launch_spec_pin')
    launch=load(spec['original_launch_spec'])
    for pin in launch['pins']:
        path=Path(pin['path'])
        need(path.is_file() and not path.is_symlink() and path.stat().st_size==pin['bytes'] and sha(path)==pin['sha256'],'original_supplemental_pin')
    need(before==load(spec['original_before']),'original_changed_since_failure_audit')
    jobs=Path(spec['jobs_copy']).resolve();cells=Path(spec['cells_copy']).resolve()
    need(not jobs.is_relative_to(original) and not cells.is_relative_to(original),'copies_outside_original')
    need(Path(spec['jobs_copy']).absolute()==jobs and Path(spec['cells_copy']).absolute()==cells,'copy_path_alias')
    for copy,source in [(jobs,original/'data/jobs.sqlite3'),(cells,original/'data/native-owned-cells-bounded-v1/cells.sqlite3')]:
        need(copy.is_file() and not copy.is_symlink() and sha(copy)==sha(source),'closed_database_exact_copy')
        for suffix in ('-wal','-journal'):
            side=Path(str(source)+suffix);need(not side.exists() or side.stat().st_size==0,'original_database_closed')
    db=sqlite3.connect(jobs.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    try:
        need(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','jobs_integrity')
        sessions=db.execute('SELECT id FROM sessions WHERE deleted=0').fetchall()
        need(len(sessions)==1 and sessions[0]['id']==spec['session_id'],'same_session');sid=spec['session_id']
        chunks=[dict(row) for row in db.execute('SELECT * FROM chunks')]
        need(len(chunks)==1 and chunks[0]['status']=='completed' and chunks[0]['duration']==9.0
             and chunks[0]['next_offset']==9.0 and chunks[0]['error'] is None,'native_work_completed')
        work=[dict(row) for row in db.execute('SELECT w.*,c.path,c.digest,c.status FROM recording_work w JOIN chunks c ON c.id=w.chunk_id')]
        need(len(work)==1 and work[0]['frames']==144000 and work[0]['capture_start_frame']==0,'same_work')
        need(sha(work[0]['path'])==work[0]['digest'],'work_source_bytes')
        meta=json.loads(chunks[0]['metadata'])
        first=Path(spec['recovery_root'])/'attempt-01';second=Path(spec['repeat_root'])/'attempt-01'
        manifest=artifact_pair(first,second)
        # Historical partial completion is bound to the same immutable job and DB,
        # not accepted as a completed export. Verify completion shape independently.
        status=load(original/'export/export.json.partial')['completion']
        need(status['session_id']==sid and status['capture_id']==spec['capture_id'] and status['status']=='completed'
             and status['frames']==144000 and status['inputs']==1 and status['pages']==1 and status['work_chunks']==1
             and status['analysis_completion_verified'] is True and status['blocked_chunks']==[],'same_completed_job')
        canonical=audit_canonical(first,db,cells,work,meta,status,sid)
        trace_path=Path(spec['host_output'])/'requests.jsonl'
        need(trace_path.stat().st_size<=1024*1024,'host_trace_bytes')
        trace=[json.loads(line) for line in trace_path.read_text().splitlines()]
        counts=check_get_phases(trace,[spec['recovery_root'],spec['repeat_root']],sid,manifest,
            capture_id=spec['capture_id'],job_dir=original/'intake')
        host=load(Path(spec['host_output'])/'RESULT.json')
        need(host['passed'] is True and host['snapshot_only'] is True and host['native_execution'] is False
             and host['requests']==len(trace) and host['response_leases_remaining']==0
             and host['original_tree_unchanged'] is True and host['snapshot_unchanged'] is True
             and host['protected_write_denials']==0 and host['server_task_awaited'] is True
             and host['active_requests']==0 and host['traffic_failure'] is None,'host_cleanup_bounds')
        result={'schema':'same-job-snapshot-recovery-independent-audit-v1','passed':True,
            'snapshot_projection_only':True,'original_watch_passed':False,'native_reexecution':False,
            'live_deployment_verified':False,'accuracy_verified':False,'device_verified':False,
            'capture_completeness_proven':False,'same_original_session_and_job':True,'original_supplemental_pins_verified':len(launch['pins']),
            'get_only_request_counts':counts,'source_manifest_sha256':spec['source_manifest_sha256'],
            'copied_jobs_sha256':sha(jobs),'copied_cells_sha256':sha(cells),'canonical':canonical,
            'auditor_sha256':sha(Path(__file__))}
    finally:db.close()
    need(original_inventory(original)==before,'original_changed_during_audit')
    verify_source(spec['source_root'],spec['source_manifest'],spec['source_manifest_sha256'])
    result['original_tree_unchanged']=True
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('spec',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--spec-sha256',required=True);args=parser.parse_args();os.umask(0o077)
    need(sha(args.spec)==args.spec_sha256,'audit_spec_pin');spec=load(args.spec)
    output=args.output.resolve()
    need(args.output.absolute()==output,'audit_output_alias')
    for key in ('jobs_copy','cells_copy'):
        need(not output.is_relative_to(Path(spec[key]).resolve().parent),'audit_output_in_snapshot_parent')
    for key in ('source_root','original_run','recovery_root','repeat_root','host_output'):
        need(not output.is_relative_to(Path(spec[key]).resolve()),'audit_output_protected')
    need(not output.exists(),'audit_output_fresh')
    result=audit(spec)
    with output.open('x') as stream:json.dump(result,stream,sort_keys=True,indent=2);stream.write('\n')
    return 0

if __name__=='__main__':raise SystemExit(main())
