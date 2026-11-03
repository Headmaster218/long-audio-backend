"""Independent saved-byte audit; originals are never opened by SQLite."""
import argparse, hashlib, importlib.util, json, pathlib, re, shutil, sqlite3, sys, wave
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

def audit(envelope,output,preflight):
    e=P(envelope).resolve();o=P(output).resolve();pre=load(preflight);plan=load(e/'LAUNCH_PLAN.json');run=e/'run'
    need(not o.exists() and run not in o.parents,'fresh_audit_directory')
    need(sha(e/'LAUNCH_PLAN.json')==pre['launch_plan_sha256'],'launch_plan_changed')
    source=P(plan['source']);candidate=P(plan['candidate']);fixture=load(plan['fixture_path'])
    need(sha(plan['fixture_path'])==plan['fixture_sha256']=='dbe7d41f903b0634e471e1ab7a1e0e2b54764f8468f426abed841e2662b2eabe','fixture_changed')
    before=inv(run,True);originals={k:v for r in fixture['deliveries'] for k,v in inv(r['path'],True).items()}
    need(originals==pre['original_delivery_files'],'originals_changed_since_prelaunch')
    envelope_hashes={n:sha(e/n) for n in ['LAUNCH_PLAN.json','launch.sh','ASSETS.json','CANDIDATE_SOURCE_PINS.json','CANDIDATE_ALL_PINS.json','OUTER_WAIT.json','timing.txt']}
    for n,k in [('ASSETS.json','asset_manifest_sha256'),('CANDIDATE_SOURCE_PINS.json','candidate_pins_sha256'),('CANDIDATE_ALL_PINS.json','candidate_all_pins_sha256')]:need(envelope_hashes[n]==plan[k],'envelope_pin')
    for n,h in pre['reviewed_source_files'].items():need(sha(candidate/n)==h and sha(source/n)==h,'reviewed_source_changed')
    pins=load(e/'CANDIDATE_ALL_PINS.json');deltas=[]
    need({str(p.relative_to(candidate)) for p in candidate.rglob('*') if p.is_file() and '__pycache__' not in p.parts}=={r['path'] for r in pins},'candidate_file_inventory')
    for r in pins:
        p=candidate/r['path'];need(p.stat().st_size==r['bytes'] and sha(p)==r['sha256'],'candidate_pin')
        if sha(source/r['path'])!=r['sha256']:deltas.append(r['path'])
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
    combined=b''.join(combined);need(len(combined)==288000 and not any(combined) and pcm(run/'synthetic.wav')==combined,'combined_pcm')
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
        meta=json.loads(v['metadata']);need(meta['diarization_backend']=='nemotron3-native' and meta['silence_fast_path'] and meta['models_executed']==['Nemotron-3'] and meta['native_diarization']['finished'] and meta['native_diarization']['processed_until']==9.0,'native_execution')
        need(meta['model_execution']=='native_diarization_only_asr_skipped','native_model_execution')
        need(len(ranges)==9 and all(v['relation']=='owner' and v['capture_start_frame']==i*16000 and v['source_start_frame']==0 and v['work_start_frame']==i*16000 and v['frames']==16000 and v['source_id']==sources[i]['id'] and v['chunk_id']==work[0]['chunk_id'] for i,v in enumerate(ranges)),'owner_ranges')
        streams=rows('SELECT * FROM stream_chunks');need(len(streams)==1 and streams[0]['stream_final']==streams[0]['upload_stream_final']==1,'stream_final')
        policies=rows('SELECT * FROM strict_recording_policy');need(len(policies)==1 and policies[0]['policy']=='strict-primary-payload-v2','strict_policy')
        control=rows('SELECT state,nonce FROM strict_recording_control');need(len(control)==1 and control[0]['state']=='completed' and control[0]['nonce'] is None,'native_control')
    finally:db.close()
    need(inv(run,True)==before,'original_evidence_mutated');need({k:v for f in fixture['deliveries'] for k,v in inv(f['path'],True).items()}==originals,'original_input_mutated');need({n:sha(e/n) for n in envelope_hashes}==envelope_hashes,'envelope_mutated')
    return dict(schema='nine-native-independent-saved-byte-audit-v1',passed=True,inputs=9,pages=1,sources=9,frames=144000,work_chunks=1,original_epoch=7,original_sequences=list(range(2,11)),original_start_frame=32000,original_terminal_close='INTERRUPTION',selected_span_only=True,frozen_seal_calls_in_first_finalize=len(seals),completed_repeat_frozen_seal_replays=len(repeat),distinct_clean_child_processes=len(pids),status_processes_get_only=2,source_and_work_pcm_exact=True,owner_range_coverage_exact=True,native_execution=meta,originals_unchanged_since_prelaunch=True,original_evidence_unchanged=True,database_audited_on_copy=True,closed_database_sidecar_bytes={k:v.stat().st_size for k,v in sidecars.items()},asset_files_verified=len(assets),candidate_files_verified=len(pins),runtime_verified=observed,terminal_cleanup_passed=True,elapsed_seconds=wall,cpu_seconds=user+system,peak_sampled_rss=r['peak_sampled_rss'],envelope_hashes=envelope_hashes,result_sha256=sha(run/'RESULT.json'),terminal_sha256=sha(run/'terminal.json'),audit_source_sha256=sha(__file__),limitations=['Saved original wait receipts are not independent re-observation of historical waits.','Clean intake process exit/reopen, not abrupt crash or native service restart.','Generated host-Java digital silence, not Android/device/microphone or real network delivery.','Native diarization ran; ASR was explicitly skipped. No acoustic accuracy, speaker identity, continuity, capture completeness, multi-page, endurance or production rollout claim.','Status is GET-only but not an atomic remote observation. Resource limits are sampled/cooperative, not instantaneous kernel quotas.'])

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--envelope',required=True);a.add_argument('--output',required=True);a.add_argument('--preflight',required=True);x=a.parse_args();r=audit(x.envelope,x.output,x.preflight);(P(x.output)/'INDEPENDENT_AUDIT.json').write_text(json.dumps(r,indent=2,sort_keys=True)+'\n');print(json.dumps({k:v for k,v in r.items() if k not in ['native_execution','runtime_verified','envelope_hashes']},sort_keys=True))
