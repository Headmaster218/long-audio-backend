"""Independent saved-byte public-speech audit; originals never opened by SQLite.

Run only against the frozen closed native envelope. Output includes private DB
copies and runtime details, must be fresh and outside all source/input trees,
and must never be included in source backups. No native launch or remote calls.
"""
import argparse, os, stat, subprocess, hashlib, importlib.util, json, pathlib, re, shutil, sqlite3, sys, wave
P=pathlib.Path

def need(ok,why):
    if not ok: raise RuntimeError(why)
def sha(p):
    h=hashlib.sha256()
    with P(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
def load(p):
    p=P(p);need(p.stat().st_size<=4*1024**2,'json_size');return json.loads(p.read_bytes())
def inv(root, times=False):
    rows={};total=0
    for p in sorted(P(root).rglob('*')):
        need(not p.is_symlink(),'symlink')
        if p.is_file():
            s=p.stat();total+=s.st_size;need(total<1024**3 and len(rows)<20000,'evidence_bound')
            v=dict(bytes=s.st_size,sha256=sha(p))
            if times:v.update(mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns)
            rows[str(p)]=v
    return rows

def pcm(p):
    with wave.open(str(p),'rb') as w:
        need((w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getcomptype())==(1,2,16000,'NONE'),'pcm_format')
        n=w.getnframes();need(0<n<168000,'pcm_bound');v=w.readframes(n+1);need(len(v)==2*n,'pcm_length');return v

def chain(root,kind,count,expected_head,job):
    previous='0'*64;values=[]
    for n in range(1,count+1):
        p=root/f'{kind}-{n:04}.json';v=load(p)
        need(v['kind']==kind and v['ordinal']==n and v['job']==job and v['previous']==previous,'archive_chain')
        need(v['policy']=='immutable-files-contiguous-mono16k-v3','archive_policy')
        need(json.dumps(v,sort_keys=True,separators=(',',':')).encode()==p.read_bytes(),'archive_canonical')
        previous=sha(p);values.append(v['payload'])
    need(previous==expected_head,'archive_head');return values

def audit_export(envelope,run,db,work,meta):
    from collections import Counter
    import unicodedata
    from audio_backend import transcript_contract as c, recording_primary_provenance as pp
    from audio_backend import native_owned_core_seams as core
    result=load(run/'RESULT.json');status=result['status_observation'];sid=result['session_id'];phases=[]
    for name in ['export','export-repeat']:
        phase=load(run/f'incremental-{name}.json');wait=load(run/f'incremental-{name}-wait.json');phases.append(phase)
        need(phase['phase']==name and phase['passed'] and phase['local_files_unchanged'],'export_phase')
        need(wait==dict(pid=phase['pid'],original_popen_wait_completed=True,exit_code=0),'export_original_wait')
        need(0<len(phase['requests'])<=64 and all(r['method']=='GET' and r['key'] is None and r['body'] is None and r['file_sha256'] is None and r['path'].startswith('/sessions/'+sid+'/') for r in phase['requests']),'export_get_only_bound')
        transcript=[r for r in phase['requests'] if '/transcript?' in r['path']]
        need(len(transcript)==2 and all(r['path'].endswith('&projection=whole-primary-v1') for r in transcript),'explicit_primary_transcript')
        root=run/name;need(not root.is_symlink() and stat.S_IMODE(root.stat().st_mode)==0o700,'private_export_directory')
        need({p.name for p in root.iterdir()}=={'export.json','transcript.txt','complete.json'},'export_exact_inventory')
        for path in root.iterdir():
            info=path.lstat();need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1 and info.st_uid==os.geteuid(),'private_export_file')
        manifest=load(root/'complete.json');need(manifest==phase['result'],'export_manifest_binding')
        need(manifest['schema']=='recorder-incremental-span-export-v1' and manifest['status']=='exported' and set(manifest['artifacts'])=={'export.json','transcript.txt'},'export_manifest')
        for name2,pin in manifest['artifacts'].items():need(pin==dict(bytes=(root/name2).stat().st_size,sha256=sha(root/name2)),'export_artifact_hash')
    pids=[load(run/f'incremental-{n}.json')['pid'] for n in ['first','second','finalize','repeat','status','status-repeat','export','export-repeat']]
    need(len(set(pids))==8,'eight_distinct_child_processes')
    for name in ['export.json','transcript.txt','complete.json']:need((run/'export'/name).read_bytes()==(run/'export-repeat'/name).read_bytes(),'exact_export_repeat')
    document=load(run/'export/export.json');manifest=phases[0]['result'];units=document['units']
    need(document['completion']==status,'export_status_linkage')
    need(document['summary']=={k:manifest[k] for k in document['summary']},'export_summary')
    need(manifest['frames']==144000 and manifest['transcript_pages']==1 and manifest['units']==len(units) and manifest['nonempty_text_units']==sum(bool(u['text'].strip()) for u in units)>0,'export_count')
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
    need((run/'export/transcript.txt').read_bytes()==expected_text.encode('utf-8'),'human_text_exact_linkage')
    coverage=document['work_text_coverage'];need(len(coverage)==1 and coverage[0]==dict(chunk_id=work[0]['chunk_id'],span_start_frame=0,span_end_frame=144000,primary_units=len(units),nonempty_text_units=sum(bool(u['text'].strip()) for u in units),text_status='primary_text_observed'),'work_text_coverage')
    need(meta['asr_runtime'].get('provider')=='cpu' and meta['asr_runtime'].get('model')=='SenseVoiceSmall int8' and meta['stage_seconds']['asr']>0 and meta['asr_turn_batches']>0 and not meta.get('asr_suppressed_as_nonspeech',False),'actual_asr_runtime_receipt')
    packets=[dict(r) for r in db.execute('SELECT * FROM recording_primary_packets_v1')]
    outcomes=[dict(r) for r in db.execute('SELECT * FROM strict_recording_outcome ORDER BY start_frame')]
    commits={r['slot']:dict(r) for r in db.execute('SELECT * FROM native_core_commits')}
    need(outcomes and len(commits)==len(outcomes),'native_core_outcomes');position=0;bound_refs=set();pcmbytes=pcm(work[0]['path']);by_slot={r['core_slot']:r for r in packets}
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
    original_cells=run/'data/native-owned-cells-bounded-v1/cells.sqlite3'
    audit_root=P(db.execute('PRAGMA database_list').fetchone()['file']).parent
    copy_cells=audit_root/'cells.sqlite3'
    need(not copy_cells.exists(),'fresh_receipt_audit_copy')
    shutil.copyfile(original_cells,copy_cells);need(sha(copy_cells)==sha(original_cells),'receipt_database_copy')
    for suffix in ['-wal','-shm','-journal']:
        original=P(str(original_cells)+suffix)
        if original.exists():
            need(suffix=='-shm' or original.stat().st_size==0,'receipt_database_nonempty_recovery_sidecar')
            copied=P(str(copy_cells)+suffix);shutil.copyfile(original,copied);need(sha(copied)==sha(original),'receipt_sidecar_copy')
    cells=sqlite3.connect(copy_cells.as_uri()+'?mode=ro&immutable=1',uri=True);cells.row_factory=sqlite3.Row
    try:
        need(cells.execute('PRAGMA integrity_check').fetchone()[0]=='ok','receipt_database_integrity')
        sources=[dict(v) for v in cells.execute('SELECT * FROM sources')];need(len(sources)==1,'native_receipt_count')
        receipt=Receipt.decode(sources[0]['receipt']);info=P(work[0]['path']).stat()
        need(sources[0]['cid']==receipt.chunk_id==work[0]['chunk_id'] and sources[0]['sid']==receipt.session_id==sid and sources[0]['digest']==receipt.digest==work[0]['digest'],'native_receipt_identity')
        need(receipt.frames==144000 and receipt.timeline_start==0.0 and receipt.signature==(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns),'native_receipt_source_signature')
        need(cells.execute('SELECT COUNT(*) FROM cells').fetchone()[0]==0,'primary_only_no_owned_pcm_cells')
    finally:cells.close()
    return dict(native_source_receipt_id=receipt.receipt_id,native_source_receipt_exact=True,passed=True,export_processes_get_only=2,distinct_clean_child_processes=8,export_request_counts=[len(p['requests']) for p in phases],export_transcript_pages=1,primary_units=len(units),nonempty_primary_units=sum(bool(u['text'].strip()) for u in units),exact_repeat_artifacts=True,private_directory_mode='0700',private_file_mode='0600',canonical_asr_db_identity_exact=True,all_exported_refs_native_receipt_bound=True,retained_primary_packets=len(packets),native_core_outcomes=len(outcomes),native_core_pcm_coverage_exact=True,asr_runtime=meta['asr_runtime'],asr_stage_seconds=meta['stage_seconds']['asr'],asr_turn_batches=meta['asr_turn_batches'],asr_backend='sensevoice',speaker_scope_count=len({tuple(u['speaker_scope']) for u in units if u['speaker_scope'] is not None}),accuracy_verified=False,speaker_identity_verified=False,capture_completeness_proven=False,artifacts=manifest['artifacts'],complete_manifest_sha256=sha(run/'export/complete.json'))


def audit(envelope,output,preflight):
    e=P(envelope).resolve();o=P(output).resolve();pre=load(preflight);plan=load(e/'LAUNCH_PLAN.json');run=e/'run'
    need(not o.exists() and run not in o.parents,'fresh_audit_directory')
    need(sha(e/'LAUNCH_PLAN.json')==pre['launch_plan_sha256'],'launch_plan_changed')
    source=P(plan['source']);candidate=P(plan['candidate']);fixture=load(plan['fixture_path']);need(all(o!=root and root not in o.parents for root in (source,candidate,P(plan['fixture_path']).parent,e)),'audit_output_in_protected_tree')
    need(sha(plan['fixture_path'])==plan['fixture_sha256']=='735895855f8de14f84992216ee7b28fb7359ce74fb30a86aed6ed5e93e80dd56','fixture_changed')
    before=inv(run,True);originals={k:v for r in fixture['deliveries'] for k,v in inv(r['path'],True).items()}
    need(originals==pre['original_delivery_files'],'originals_changed_since_prelaunch')
    envelope_hashes={n:sha(e/n) for n in ['LAUNCH_PLAN.json','launch.sh','ASSETS.json','CANDIDATE_SOURCE_PINS.json','CANDIDATE_ALL_PINS.json','OUTER_WAIT.json','timing.txt']}
    for n,k in [('ASSETS.json','asset_manifest_sha256'),('CANDIDATE_SOURCE_PINS.json','candidate_pins_sha256'),('CANDIDATE_ALL_PINS.json','candidate_all_pins_sha256')]:need(envelope_hashes[n]==plan[k],'envelope_pin')
    for n,h in pre['reviewed_source_files'].items():need(sha(candidate/n)==h and sha(source/n)==h,'reviewed_source_changed')
    pins=load(e/'CANDIDATE_ALL_PINS.json');deltas=[]
    baseline_path=e/'SOURCE_BASELINE_PINS.json';need(sha(baseline_path)==plan['source_baseline_pins_sha256'],'baseline_pin')
    baseline={row['path']:row for row in load(baseline_path)}
    git=lambda *args:subprocess.check_output(['git','-C',str(source),*args])
    repository=P(git('rev-parse','--show-toplevel').decode().strip());prefix=str(source.relative_to(repository));commit=plan['published_source_commit']
    git=lambda *args:subprocess.check_output(['git','-C',str(repository),*args])
    need(commit=='3a3bcfc8dd792ae549c33931a3baf55e40b1803a' and git('rev-parse',commit+':'+prefix).decode().strip()==plan['published_source_tree'],'published_source_binding')
    blobs={}
    for record in git('ls-tree','-r','-z',commit+':'+prefix).split(b'\0'):
        if record:
            info,name=record.split(b'\t');mode,kind,oid=info.decode().split();need(kind=='blob' and mode in ('100644','100755'),'committed_regular_file');blobs[name.decode()]=oid
    need(set(baseline)==set(blobs)=={row['path'] for row in pins},'committed_source_inventory')
    for path,row in baseline.items():
        current=source/path
        # Documentation may have advanced after the run. Validate frozen source
        # against its immutable Git object, never treat later docs as run inputs.
        raw=current.read_bytes() if current.is_file() and sha(current)==row['sha256'] else git('show',commit+':'+prefix+'/'+path)
        need(len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256'] and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==blobs[path],'committed_baseline_bytes')
    need({str(p.relative_to(candidate)) for p in candidate.rglob('*') if p.is_file() and '__pycache__' not in p.parts}=={r['path'] for r in pins},'candidate_file_inventory')
    for r in pins:
        p=candidate/r['path'];need(p.stat().st_size==r['bytes'] and sha(p)==r['sha256'],'candidate_pin')
        if baseline[r['path']]['sha256']!=r['sha256']:deltas.append(r['path'])
    need(deltas==['audio_backend/native_owned_cell_store.py'],'candidate_delta')
    s=source/deltas[0];c=candidate/deltas[0];need(sha(s)==plan['production_native_store_sha256']=='efbd83e985482d47d3337e7290cddfacc680ca77c49452c904c18457211393d8','production_pin')
    expected=s.read_text().replace("evidence_sha256='d1eafbcf6bde0071d301f488ba82e69fa3bbadc446c62775276e4998efbdbe67'","evidence_sha256='27e9f806302f01da31d2f3bd2be0de327f195ccc63d0fd93c6b18ccb760a5589'").replace("sqlite3_package_sha256='096bb1a3902a75a2c3723f2d7863a8d3e9ed627bd862e6538eca2bc517d6e29b'","sqlite3_package_sha256='6ac9a15ee2c393bd994e6b3784ed3f5fb97c9b4f986cbce03dccbad6226778df'")
    need(c.read_text()==expected,'exact_patch')
    assets=[r for r in load(e/'ASSETS.json')['files'] if r.get('role')!='sample'];need(load(run/'ASSET_VERIFIED.json')==assets,'asset_receipt')
    for r in assets:need(P(r['path']).stat().st_size==r['bytes'] and sha(r['path'])==r['sha256'],'asset_changed')
    sys.path.insert(0,str(candidate));sys.dont_write_bytecode=True
    from audio_backend.native_owned_cell_store import qualified_runtime,runtime_fingerprints,Budget
    from audio_backend.recorder_delivery import inspect_delivery
    from audio_backend.recorder_snapshot import inspect_snapshot
    from audio_backend.recorder_sequence import reconcile_snapshots
    q=qualified_runtime(run/'data');need(q is not None and (run/'data').stat().st_dev==27,'runtime_device');observed=runtime_fingerprints(Budget())
    need(all(getattr(q,k)==v for k,v in observed.items()),'runtime_fingerprints')
    db=sqlite3.connect(':memory:')
    try:q.verify(db,run/'data',Budget())
    finally:db.close()
    r=load(run/'RESULT.json');terminal=load(run/'terminal.json');need(r['passed'] and r['input_kind']=='explicit_recorder_incremental_span','run_failed')
    need(not any(k.endswith('error') for k in r),'run_error');need(r['runtime_binary_differences']=={},'runtime_diff')
    need(all(r['startup'][k] is True for k in ['strict','inherited_lock','worker_alive']),'native_startup')
    need(r['shutdown']['exit_code']==0 and r['shutdown']['terminal']==terminal,'terminal_binding')
    need(terminal['error'] is None and terminal['service_cleanup_error'] is None and terminal['shutdown_errors']=={},'cleanup_errors')
    need(all(terminal[k] for k in ['lock_closed','store_safe','permit_clear']) and not terminal['worker_alive'] and all(terminal['shutdown']['predicates'].values()) and terminal['committed_claim_late_count']==0,'cleanup_predicates')
    need(load(e/'OUTER_WAIT.json')==dict(observed_exit_code=0,original_shell_wait_completed=True),'outer_wait')
    m=re.fullmatch(r'real_seconds=([0-9.]+) user_seconds=([0-9.]+) sys_seconds=([0-9.]+)\n?',(e/'timing.txt').read_text());need(m is not None,'timing');wall,user,system=map(float,m.groups());need(0<wall<480,'outer_timeout')
    need(0<r['peak_sampled_rss']<=6*1024**3,'rss_limit');need(r['input_unchanged'] and r['source_unchanged'] and r['external_batch_unchanged'] and r['original_span_inventory_unchanged'],'run_immutability')
    phases={n:load(run/f'incremental-{n}.json') for n in ['first','second','finalize','repeat','status','status-repeat']}
    pids=[]
    for n,v in phases.items():
        need(v['phase']==n and v['passed'],'phase_failed');wait=load(run/f'incremental-{n}-wait.json');need(wait==dict(pid=v['pid'],original_popen_wait_completed=True,exit_code=0),'child_wait');pids.append(v['pid'])
    need(len(set(pids))==6,'distinct_processes')
    stages=phases['first']['stages']+phases['second']['stages'];need(len(phases['first']['stages'])==4 and len(phases['second']['stages'])==5,'split_count')
    need(stages==r['incremental_stages'],'stage_binding')
    for i,v in enumerate(stages):need(v['inputs']==i+1 and v['frames']==16000*(i+1) and not v['capture_finalized'] and v['published_frames']==0 and v['status']=='delivery_staged_without_eos','stage_geometry')
    need(phases['second']['midpoint_replay']==stages[3],'midpoint_replay');need(all(x['method']=='GET' for n in ['first','second'] for x in phases[n]['requests']),'staging_mutation')
    restart=r['incremental_clean_process_restart'];need(restart['first_pid']==pids[0] and restart['second_pid']==pids[1] and restart['abrupt_crash_test'] is False and restart['service_restart_test'] is False,'restart_claim')
    job=run/'intake';header=load(job/'capture.json');checkpoint=load(job/'uploader/uploader.json')
    need(header['schema']=='explicit-incremental-recorder-span-intake-v1' and header['acknowledgments']==dict(exclusive_writer=True,span_local_closure=True),'header_scope')
    need(header['span_origin']['epoch']==7 and header['span_origin']['first_sequence']==2 and header['span_origin']['epoch_frame_start']==32000,'header_origin')
    need(checkpoint['policy']=='immutable-files-contiguous-mono16k-v3' and checkpoint['complete'] and checkpoint['finalize_requested'],'completed_v3')
    need(checkpoint['inputs_done']==checkpoint['input_count']==checkpoint['source_count']==9 and checkpoint['pages_done']==checkpoint['page_count']==1,'checkpoint_counts')
    need(all(checkpoint[k]==144000 for k in ['archived_frames','completed_frames','emitted_frames','published_frames']),'checkpoint_frames')
    need(all(checkpoint[k] is None for k in ['active_input','active_page','archive_intent','retirement']) and checkpoint['spool']==[],'pending_work')
    history=job/'uploader/uploader.history';inputs=chain(history,'input',9,checkpoint['inputs_head'],checkpoint['job']);pages=chain(history,'page',1,checkpoint['pages_head'],checkpoint['job']);page=pages[0]
    need(len(page['pieces'])==9 and page['released'] and page['seal']['eos'] and page['seal']['end_frame']==144000,'saved_page')
    combined=[];snapshots=[]
    for i,(f,v,piece) in enumerate(zip(fixture['deliveries'],inputs,page['pieces'])):
        report=f['delivery'];path=P(f['path']);need(inspect_delivery(path,validate_metadata=True)==report,'original_metadata')
        need(report['epoch']==7 and report['sequence']==i+2 and report['frame_start']==32000+i*16000 and report['frames']==16000 and report['close_reason']==('INTERRUPTION' if i==8 else 'ROTATION'),'original_geometry')
        need(v['id']==piece['input_id']==report['source_id'] and v['frames']==16000 and v['normalized_start']==piece['capture_start']==piece['normalized_start']==i*16000 and v['clean_eof'],'input_mapping')
        snap=P(v['identity']['path']).parent;need(snap==job/'admissions'/str(i)/'snapshot','snapshot_path');snapshots.append(snap)
        need(inspect_snapshot(snap)['delivery']==report,'snapshot_metadata')
        for name,h in f['hashes'].items():need(sha(path/name)==h and sha(snap/name)==h,'snapshot_bytes')
        need(v['identity']['sha256']==piece['digest']==report['wav_sha256'] and piece['pcm_sha256']==report['pcm_sha256'] and piece['frames']==16000 and piece['input_start']==0,'piece_hashes')
        combined.append(pcm(path/'audio.wav'))
    seq=reconcile_snapshots(snapshots);need(len(seq['captures'])==1 and all(x['relation']=='declared_adjacent' and not x['reasons'] for x in seq['captures'][0]['edges']),'sequence_edges')
    combined=b''.join(combined);need(len(combined)==288000 and any(combined) and pcm(run/'synthetic.wav')==combined,'combined_pcm');need(hashlib.sha256(combined).hexdigest()==fixture['selected_pcm_sha256']=='824962e469c1b5c3969804b2846b1428fe4a194a1744d5599aa595ae7f69af73','fixed_public_source_pcm')
    upload=r['upload'];need(upload==r['upload_reconciled_resume']==phases['finalize']['result']==phases['repeat']['result'],'completed_replay')
    need(upload['inputs']==upload['sources']==9 and upload['frames']==upload['published_frames']==144000 and upload['pages']==1 and upload['analysis_completion_verified'] is False,'upload_claim')
    frozen=dict(method='POST',path=f"/sessions/{r['session_id']}/recordings/synthetic-smoke/pages/1/seal",key=None,body=page['seal'],file_sha256=None)
    initial=phases['finalize']['requests'];repeat=[v for v in phases['repeat']['requests'] if v['method']!='GET'];seals=[v for v in initial if v['method']=='POST' and v['path'].endswith('/seal')]
    need(seals and all(v==frozen for v in seals+repeat),'frozen_seal_replay');puts=[v for v in initial if v['method']=='PUT'];need(len(puts)==len({v['key'] for v in puts})==9,'put_count')
    need(phases['finalize']['injected_lost_published_seal_responses']==r['injected_lost_published_seal_responses']==1,'seal_response_loss')
    need(r['incremental_repeat_check']==dict(no_new_sources=True,exact_frozen_seal_replays=len(repeat)),'replay_summary')
    status=phases['status']['result'];need(status==phases['status-repeat']['result']==r['status_observation'],'status_equal')
    need(r['status_get_only'] and r['status_repeat_identical'] and all(phases[n]['local_files_unchanged'] and all(v['method']=='GET' for v in phases[n]['requests']) for n in ['status','status-repeat']),'status_read_only')
    need(status['status']=='completed' and status['analysis_completion_observed'] and status['analysis_completion_verified'] and status['read_only'] and status['inputs']==9 and status['work_chunks']==1 and status['frames']==144000,'status_complete')
    mapping=[dict(ordinal=i,original_sequence=i+2,original_epoch=7,original_epoch_frame_start=32000+i*16000,original_epoch_frame_end=48000+i*16000,span_start_frame=i*16000,span_end_frame=(i+1)*16000) for i in range(9)]
    need(status['input_mapping']==mapping,'status_mapping')
    for value in [status,upload]:
        desc=value['span'];need(desc['original_epoch']==7 and desc['original_first_sequence']==2 and desc['original_last_sequence']==10 and desc['original_close_reason']=='INTERRUPTION' and desc['recorder_stop_observed'] is False and desc['eos_meaning']=='selected_span_only' and desc['before_span_gap_frames'] is None and desc['after_span_gap_frames'] is None and desc['hardware_frame_mapping']=='UNESTABLISHED','terminal_truth')
    need(all(status[k] is False for k in ['observation_atomic','remote_request_identity_verified','target_identity_authenticated','producer_identity_authenticated','accuracy_verified','physical_continuity_proven','capture_completeness_proven','concurrent_writer_safety_guaranteed']),'status_overclaim')
    original_db=run/'data/jobs.sqlite3'
    # A closed service may retain an empty WAL plus its shm index. Never ignore
    # a nonempty WAL or rollback journal and never recover original evidence.
    sidecars={suffix:P(str(original_db)+suffix) for suffix in ['-wal','-shm','-journal'] if P(str(original_db)+suffix).exists()}
    need(all(sidecars[k].stat().st_size==0 for k in ['-wal','-journal'] if k in sidecars),'nonempty_recovery_sidecar')
    o.mkdir(mode=0o700);copy=o/'jobs.sqlite3';shutil.copyfile(original_db,copy);need(sha(copy)==sha(original_db),'database_copy')
    for suffix,path in sidecars.items():
        target=P(str(copy)+suffix);shutil.copyfile(path,target);need(sha(target)==sha(path),'sidecar_copy')
    db=sqlite3.connect(copy.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    try:
        rows=lambda q:[dict(v) for v in db.execute(q)]
        need(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','database_integrity')
        sessions=rows('SELECT * FROM sessions');batches=rows('SELECT * FROM recording_batches');dbpages=rows('SELECT * FROM recording_pages');sources=rows('SELECT * FROM recording_sources ORDER BY source_start_frame');work=rows('SELECT w.*,c.path,c.digest,c.status,c.duration,c.next_offset,c.metadata FROM recording_work w JOIN chunks c ON c.id=w.chunk_id ORDER BY w.position');ranges=rows('SELECT * FROM recording_ranges ORDER BY capture_start_frame')
        need(len(sessions)==len(batches)==len(dbpages)==len(work)==1 and len(sources)==9,'db_counts')
        need(sessions[0]['id']==r['session_id']==header['target']['session_id']==batches[0]['session_id'] and sessions[0]['timeline_end']==9.0,'db_session')
        need((batches[0]['capture_origin_frame'],batches[0]['capture_end_frame'],batches[0]['eos'])==(0,144000,1) and batches[0]['capture_id']=='synthetic-smoke','db_capture')
        dp=dbpages[0];need((dp['state'],dp['released'],dp['eos'],dp['start_frame'],dp['end_frame'])==('published',1,1,0,144000),'db_page')
        need(dp['id']==page['server_id'] and dp['seal_key']==page['seal']['idempotency_key'],'db_seal_key');saved=json.loads(dp['seal_request']);need(all(saved[k]==page['seal'][k] for k in ['end_frame','eos','expected_revision']),'db_seal_body')
        for i,(v,piece,put) in enumerate(zip(sources,page['pieces'],puts)):
            need(v['id']==piece['server_id'] and v['idempotency_key']==piece['key']==put['key'] and v['digest']==piece['digest']==sha(v['path'])==put['file_sha256'] and v['source_start_frame']==i*16000 and v['frames']==16000 and v['batch_id']==batches[0]['id'] and v['page_id']==dp['id'],'db_source')
            need(pcm(v['path'])==combined[i*32000:(i+1)*32000],'db_source_pcm')
        v=work[0];need(v['status']=='completed' and v['receipt_state']=='durable' and v['native_receipt_status']=='receipt_retained' and v['frames']==144000 and v['capture_start_frame']==0 and v['next_offset']==v['duration']==9.0,'native_work')
        need(sha(v['path'])==v['digest'] and pcm(v['path'])==combined,'native_pcm')
        meta=json.loads(v['metadata']);need(meta['diarization_backend']=='nemotron3-native' and not meta.get('silence_fast_path',False) and meta['native_diarization']['finished'] and meta['native_diarization']['processed_until']==9.0,'native_execution')
        need(meta.get('asr_backend')=='sensevoice' and meta.get('model_execution') not in ('native_diarization_only_asr_skipped','not_run_silence'),'native_asr_path'); export_audit=audit_export(e,run,db,work,meta)
        need(len(ranges)==9 and all(v['relation']=='owner' and v['capture_start_frame']==i*16000 and v['source_start_frame']==0 and v['work_start_frame']==i*16000 and v['frames']==16000 and v['source_id']==sources[i]['id'] and v['chunk_id']==work[0]['chunk_id'] for i,v in enumerate(ranges)),'owner_ranges')
        streams=rows('SELECT * FROM stream_chunks');need(len(streams)==1 and streams[0]['stream_final']==streams[0]['upload_stream_final']==1,'stream_final')
        policies=rows('SELECT * FROM strict_recording_policy');need(len(policies)==1 and policies[0]['policy']=='strict-primary-payload-v2','strict_policy')
        control=rows('SELECT state,nonce FROM strict_recording_control');need(len(control)==1 and control[0]['state']=='completed' and control[0]['nonce'] is None,'native_control')
    finally:db.close()
    need(inv(run,True)==before,'original_evidence_mutated');need({k:v for f in fixture['deliveries'] for k,v in inv(f['path'],True).items()}==originals,'original_input_mutated');need({n:sha(e/n) for n in envelope_hashes}==envelope_hashes,'envelope_mutated')
    return dict(schema='public-speech-native-independent-saved-byte-audit-v1',passed=True,inputs=9,pages=1,sources=9,frames=144000,work_chunks=1,original_epoch=7,original_sequences=list(range(2,11)),original_start_frame=32000,original_terminal_close='INTERRUPTION',selected_span_only=True,frozen_seal_calls_in_first_finalize=len(seals),completed_repeat_frozen_seal_replays=len(repeat),intake_status_clean_child_processes=len(pids),distinct_clean_child_processes=export_audit['distinct_clean_child_processes'],status_processes_get_only=2,source_and_work_pcm_exact=True,owner_range_coverage_exact=True,native_execution=meta,export=export_audit,originals_unchanged_since_prelaunch=True,original_evidence_unchanged=True,database_audited_on_copy=True,closed_database_sidecar_bytes={k:v.stat().st_size for k,v in sidecars.items()},asset_files_verified=len(assets),candidate_files_verified=len(pins),runtime_verified=observed,terminal_cleanup_passed=True,elapsed_seconds=wall,cpu_seconds=user+system,peak_sampled_rss=r['peak_sampled_rss'],envelope_hashes=envelope_hashes,result_sha256=sha(run/'RESULT.json'),terminal_sha256=sha(run/'terminal.json'),audit_source_sha256=sha(__file__),limitations=['Saved original wait receipts are not independent re-observation of historical waits.','Clean intake process exit/reopen, not abrupt crash or native service restart.','Host-Java replay of fixed public AMI speech, not Android/device/microphone or real network delivery.','Real native primary ASR output and export linkage, not acoustic accuracy, speaker identity, continuity, capture completeness, multi-page, endurance or production rollout qualification.','Status is GET-only but not an atomic remote observation. Resource limits are sampled/cooperative, not instantaneous kernel quotas.'])

if __name__=='__main__':
    os.umask(0o077)
    a=argparse.ArgumentParser();a.add_argument('--envelope',required=True);a.add_argument('--output',required=True);a.add_argument('--preflight',required=True);x=a.parse_args();r=audit(x.envelope,x.output,x.preflight);(P(x.output)/'INDEPENDENT_AUDIT.json').write_text(json.dumps(r,indent=2,sort_keys=True)+'\n');print(json.dumps({k:v for k,v in r.items() if k not in ['native_execution','runtime_verified','envelope_hashes']},sort_keys=True))
