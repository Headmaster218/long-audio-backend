"""Independent saved-byte local-inbox loopback audit; originals never opened by SQLite.

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

def source_check(envelope,pre=None):
    e=P(envelope).resolve();plan=load(e/'LAUNCH_PLAN.json');candidate=P(plan['candidate']);source=P(plan['source'])
    if pre is not None:
        need(sha(e/'LAUNCH_PLAN.json')==pre['launch_plan_sha256'] and sha(e/'launch.sh')==pre['launch_script_sha256'],'reviewed_plan_changed')
    pins=load(e/'CANDIDATE_SOURCE_PINS.json');baseline=load(e/'SOURCE_BASELINE_PINS.json')
    for name,key in [('CANDIDATE_SOURCE_PINS.json','candidate_all_pins_sha256'),('SOURCE_BASELINE_PINS.json','source_baseline_pins_sha256'),('ASSETS.json','asset_manifest_sha256')]:
        need(sha(e/name)==plan[key],'source_envelope_pin')
    repo=P(subprocess.check_output(['git','-C',str(source),'rev-parse','--show-toplevel']).decode().strip());prefix=str(source.relative_to(repo));commit=plan['published_source_commit']
    git=lambda *a:subprocess.check_output(['git','-C',str(repo),*a])
    need(git('rev-parse',commit+':'+prefix).decode().strip()==plan['published_source_tree'],'published_tree')
    blobs={}
    for raw in git('ls-tree','-r','-z',commit+':'+prefix).split(b'\0'):
        if not raw:continue
        info,name=raw.split(b'\t');mode,kind,oid=info.decode().split();need(kind=='blob' and mode in ('100644','100755'),'committed_regular');blobs[name.decode()]=oid
    baseline={r['path']:r for r in baseline};candidate_pins={r['path']:r for r in pins}
    need(set(blobs)==set(baseline)==set(candidate_pins),'exact_candidate_inventory')
    need(not any(p.is_symlink() for p in candidate.rglob('*')),'candidate_symlink')
    actual={str(p.relative_to(candidate)) for p in candidate.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    need(actual==set(blobs),'candidate_extra_files');deltas=[];original_store=None
    for path,row in baseline.items():
        cp=candidate/path;need(not cp.is_symlink() and cp.stat().st_size==candidate_pins[path]['bytes'] and sha(cp)==candidate_pins[path]['sha256'],'candidate_bytes')
        raw=git('show',commit+':'+prefix+'/'+path)
        need(len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256'],'committed_baseline_bytes')
        if row['sha256']!=candidate_pins[path]['sha256']:deltas.append(path)
        if path=='audio_backend/native_owned_cell_store.py':original_store=raw
    need(deltas==['audio_backend/native_owned_cell_store.py'],'sole_accepted_delta')
    need(hashlib.sha256(original_store).hexdigest()==plan['production_native_store_sha256']=='efbd83e985482d47d3337e7290cddfacc680ca77c49452c904c18457211393d8','production_pin')
    expected=original_store.replace(b"evidence_sha256='d1eafbcf6bde0071d301f488ba82e69fa3bbadc446c62775276e4998efbdbe67'",b"evidence_sha256='27e9f806302f01da31d2f3bd2be0de327f195ccc63d0fd93c6b18ccb760a5589'").replace(b"sqlite3_package_sha256='096bb1a3902a75a2c3723f2d7863a8d3e9ed627bd862e6538eca2bc517d6e29b'",b"sqlite3_package_sha256='6ac9a15ee2c393bd994e6b3784ed3f5fb97c9b4f986cbce03dccbad6226778df'")
    need((candidate/deltas[0]).read_bytes()==expected and sha(candidate/'qualification/accepted_candidate_20261103.patch')==plan['accepted_patch_sha256']=='3a97845bf13b00e402ce73cb9990ce72f2696b9d82254cf442fbfde18a46afe8','exact_accepted_patch')
    need(sha(source/deltas[0])==plan['production_native_store_sha256'],'production_source_unchanged')
    assets=[r for r in load(e/'ASSETS.json')['files'] if r.get('role')!='sample']
    for row in assets:need(P(row['path']).stat().st_size==row['bytes'] and sha(row['path'])==row['sha256'],'asset_bytes')
    need(sha(plan['fixture_path'])==plan['fixture_sha256'],'fixture_pin')
    return plan,candidate,assets,len(pins)



def audit_producer(fixture_path, candidate=None):
    """Check saved Java provenance and exact PCM without running the producer."""
    f=load(fixture_path);root=P(fixture_path).parent
    need(f['passed'] is True and f['producer_commit']=='2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9','producer_commit')
    source=P(f['recorder_source']);checkout=P(f['producer_checkout'])
    git=lambda *args:subprocess.check_output(['git','-C',str(checkout),*args])
    need(git('rev-parse','HEAD').decode().strip()==f['producer_commit'] and not git('status','--porcelain=v1'),'producer_checkout')
    receipt=load(source/'ASSEMBLED_SOURCE.json')
    need(sha(source/'ASSEMBLED_SOURCE.json')==f['assembly_receipt_sha256']=='6434fa619850c576503ea442b3c679662b6fcd5720d4501cabd6d8c939d73a21','producer_assembly_receipt')
    for row in receipt['files']:
        path=source/row['path'];need(not path.is_symlink(),'producer_assembly_symlink');raw=path.read_bytes()
        need(len(raw)==row['bytes'] and sha(path)==row['sha256'] and raw==git('show',f['producer_commit']+':'+row['source_path']),'producer_assembly_bytes')
        need(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob'],'producer_assembly_git_blob')
    need(len(receipt['files'])==f['verified_assembly_files']==256,'producer_assembly_count')
    core=source/'app/src/main/java/io/github/headmaster218/recorder/core'
    actual_java={str(p.relative_to(source)) for p in core.glob('*.java')}
    need(actual_java=={r['path'] for r in f['production_source_pins']},'producer_core_inventory')
    for row in f['production_source_pins']:
        path=source/row['path'];need(path.stat().st_size==row['bytes'] and sha(path)==row['sha256'],'producer_core_pins')
    classes=root/'classes'
    need({str(p.relative_to(classes)) for p in classes.rglob('*.class')}=={r['path'] for r in f['class_pins']},'producer_class_inventory')
    for row in f['class_pins']:
        path=classes/row['path'];need(not path.is_symlink() and path.stat().st_size==row['bytes'] and sha(path)==row['sha256'],'producer_class_pins')
    need(f['terminal_reason']=='STOP' and f['terminal_api']=='PcmSegmentWriter.stop()','producer_stop_api')
    if candidate is not None:
        for name,key in [('PublicSpeechProducerDeliveries.java','harness_sha256'),('generate_public_speech_java_deliveries.py','generator_sha256'),('generate_nine_java_deliveries.py','generator_helper_sha256')]:
            need(sha(P(candidate)/'qualification'/name)==f[key],'producer_candidate_source_pin')
        java=(P(candidate)/'qualification/PublicSpeechProducerDeliveries.java').read_text()
        need('if (terminal.equals("STOP")) writer.stop();' in java and 'else writer.interrupt();' in java and 'args.length == 4 ? args[3] : "INTERRUPTION"' in java,'producer_real_terminal_api')
    wav=P(f['source_wav']);need(not wav.is_symlink() and wav.stat().st_size==f['source_wav_bytes']==68566744 and sha(wav)==f['source_wav_sha256']=='a15017d0d3c871866971ee178052a0ce3a7e44d6fbf89fc23dcab326511b65f5','public_source_wav')
    with wave.open(str(wav),'rb') as w:
        need((w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getcomptype())==(1,2,16000,'NONE'),'public_source_format')
        w.setpos(26640000);generated=w.readframes(176000)
    need(len(generated)==352000 and hashlib.sha256(generated).hexdigest()==f['generated_pcm_sha256']=='08b85a47d436e3ce6b7224c984db055fe61573618e241ae0dbac20215ed50de9','generated_pcm_pin')
    need((root/'public-source-crop.pcm').read_bytes()==generated,'generated_pcm_file')
    need(f['generated_source_start_frame']==26640000 and f['selected_source_start_frame']==26672000 and f['selected_source_end_frame']==26816000,'public_source_coordinates')
    need(f['selected_pcm_sha256']==hashlib.sha256(generated[64000:]).hexdigest()=='824962e469c1b5c3969804b2846b1428fe4a194a1744d5599aa595ae7f69af73','selected_pcm_pin')
    need(f['source_url']=='https://groups.inf.ed.ac.uk/ami/corpus/' and f['license_url']=='https://creativecommons.org/licenses/by/4.0/','public_source_attribution')
    need(f['generated_segments']==11 and f['selected_deliveries']==len(f['deliveries'])==9 and f['total_selected_frames']==144000,'producer_geometry')
    need((f['original_epoch'],f['original_first_sequence'],f['original_last_sequence'],f['original_start_frame'],f['original_end_frame'])==(7,2,10,32000,176000),'producer_origin')
    need(all(f[k] is False for k in ['device_test','network_test','native_analysis_test']) and f['hardware_frame_mapping']=='UNESTABLISHED','producer_limitations')
    from audio_backend.recorder_delivery import inspect_delivery,parse_marker,parse_envelope
    from audio_backend.recorder_metadata import decode
    combined=[]
    for i,entry in enumerate(f['deliveries']):
        path=P(entry['path']);need(path==root/'producer/deliveries'/f'{i:03d}','producer_delivery_path')
        report=inspect_delivery(path,validate_metadata=True);need(report==entry['delivery'],'producer_report')
        need((report['epoch'],report['sequence'],report['frame_start'],report['frames'],report['close_reason'])==(7,i+2,32000+i*16000,16000,'STOP' if i==8 else 'ROTATION'),'genuine_stop_geometry')
        need(set(entry['hashes'])=={'audio.wav','segment.v1.bin','delivery.v1.txt'} and all(sha(path/name)==digest for name,digest in entry['hashes'].items()),'producer_delivery_bytes')
        marker=parse_marker((path/'delivery.v1.txt').read_bytes());local=parse_envelope((path/'segment.v1.bin').read_bytes(),marker,return_local=True)
        need(local==(root/'producer/spool'/report['source_id']/'manifest.bin').read_bytes() and decode(local)==entry['original_metadata'],'producer_original_manifest')
        payload=pcm(path/'audio.wav');need(payload==generated[(i+2)*32000:(i+3)*32000],'producer_exact_public_pcm');combined.append(payload)
        need(entry['source_frame_start']==26672000+i*16000 and entry['source_frame_end']==26688000+i*16000,'producer_source_mapping')
    need(b''.join(combined)==generated[64000:],'producer_combined_pcm')
    return dict(passed=True,producer_commit=f['producer_commit'],assembly_files_verified=256,
        generated_segments=11,selected_deliveries=9,frames=144000,original_terminal='STOP',
        selected_pcm_sha256=f['selected_pcm_sha256'],source_wav_sha256=f['source_wav_sha256'],
        original_metadata_equals_envelope=True,public_source_exact=True,hardware_frame_mapping='UNESTABLISHED')


WATCH_BOUNDS=dict(max_actions=256,max_requests=1024,max_network_writes=512,
    max_network_bytes=256*1024**2,deadline_seconds=120,poll_seconds=.1,max_polls=512)


def events(path):
    raw=P(path).read_bytes();need(len(raw)<=4*1024**2 and raw.endswith(b'\n'),'event_file_bound_or_torn')
    rows=[json.loads(line) for line in raw.splitlines()];need(0<len(rows)<=4096,'event_count')
    need([r['index'] for r in rows]==list(range(1,len(rows)+1)),'event_contiguous')
    need(all(type(r['monotonic']) in (int,float) and r['monotonic']>0 for r in rows),'event_timestamp')
    need(all(a['monotonic']<=b['monotonic'] for a,b in zip(rows,rows[1:])),'event_time_reversed')
    need(len({r['pid'] for r in rows})==1,'event_process_changed');return rows


def one(rows,name):
    selected=[r for r in rows if r['event']==name];need(len(selected)==1,'event_once:'+name);return selected[0]


def audit_events(run,result):
    run=P(run);spec=load(run/'watch-spec.json');watch=load(run/'watch-result.json');rows=events(run/'watch-events.jsonl');arrivals=events(run/'arrival-events.jsonl')
    sid=result['session_id'];pid=watch['pid'];out=watch['result'];requests=watch['requests']
    need(watch['schema']=='native-inbox-watch-child-v1' and watch['phase']=='finite-watch' and watch['returned'] is True and 'error' not in watch,'watch_result')
    need(spec['api']=='http://127.0.0.1:'+str(result['startup']['port']) and spec['session_id']==sid and spec['capture_id']=='inbox-watch','watch_target')
    need(spec['state_dir']==str(run/'runner') and spec['job_dir']==str(run/'intake') and spec['watch_events']==str(run/'watch-events.jsonl'),'watch_local_binding')
    need(spec['watch_bounds']==WATCH_BOUNDS and all(spec[k] is True for k in ['finalize','export','wait_for_marker_last']),'watch_authorized_bounds')
    need(all(r['pid']==pid for r in rows) and pid==result['watch_pid'],'watch_pid')
    need(load(run/'watch-wait.json')==dict(pid=pid,original_popen_wait_completed=True,exit_code=0),'watch_original_wait')
    enter=one(rows,'watch_enter');returned=one(rows,'watch_return')
    need(rows[0]==enter and rows[-1]==returned and enter['bounds']==WATCH_BOUNDS and all(enter[k] is True for k in ['finalize','export','wait_for_marker_last']),'watch_event_boundaries')
    need(returned['result']==out==result['watch'],'watch_result_binding')
    need(out['status']=='exported' and out['export_completed'] is True and out['inputs']==9 and 9<out['polls']<=512 and out['actions']<=256 and out['helper_actions']<=out['actions'],'watch_bounded_completion')
    need(all(out[k] is True for k in ['continuous_polling_enabled','automatic_intake_enabled','invocation_bounded']) and all(out[k] is False for k in ['automatic_resume_enabled','daemon_installed','capture_completeness_proven','physical_continuity_proven']),'watch_scope')
    need(0<returned['monotonic']-enter['monotonic']<120 and out['network_requests']==len(requests)<=1024 and out['network_writes']==sum(r['method']!='GET' for r in requests)<=512 and 0<out['network_response_bytes']<=256*1024**2,'watch_global_budgets')
    starts=[r for r in rows if r['event']=='request_start'];ends=[r for r in rows if r['event'] in ('request_return','request_error')]
    need(len(starts)==len(ends)==len(requests),'request_event_coverage')
    for n,(start,end,request) in enumerate(zip(starts,ends,requests),1):
        need(start['request_count']==end['request_count']==n and start['method']==end['method']==request['method'] and start['path']==end['path']==request['path'],'request_event_binding')
        need(start['index']<end['index'] and (n==len(starts) or end['index']<starts[n]['index']),'request_event_order')
        if end['event']=='request_error':need(request['method']=='GET' and end.get('status')==404,'unexpected_request_error')
        need(request['method'] in ('GET','PUT','POST') and (request['path']=='/sessions/'+sid or request['path'].startswith('/sessions/'+sid+'/')),'watch_request_target')
        if request['method']=='GET':need(request['key'] is None and request['body'] is None and request['file_sha256'] is None,'get_request_payload')
        else:need(request['path'].startswith('/sessions/'+sid+'/recordings/inbox-watch/'),'watch_mutation_target')
    finalize=one(rows,'finalize_enter');finalized=one(rows,'finalize_return');export=one(rows,'export_enter');exported=one(rows,'export_return')
    need(finalize['inputs']==9 and finalized['capture_finalized'] is True and finalize['index']<finalized['index']<export['index']<exported['index']<returned['index'],'explicit_finalize_export_order')
    need(0<=finalize['request_count']<finalized['request_count']<=export['request_count']<exported['request_count']<=len(requests),'operation_request_bounds')
    need(all(r['method']=='GET' for r in requests[:finalize['request_count']]+requests[finalized['request_count']:]),'mutation_outside_finalize')
    terminal=load(run/'runner/input-008.done.json');intent=load(run/'runner/input-008.intent.json')
    need(finalize['last_admitted_delivery']==intent['delivery'] and intent['delivery']['close_reason']=='STOP' and intent['delivery']['sequence']==10 and terminal['intent_sha256']==hashlib.sha256((json.dumps(intent,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode('ascii')).hexdigest(),'genuine_stop_admission')
    hold=load(run/'MARKER_LAST_HOLD.json');pending=[r for r in rows if r['event']=='marker_pending_validated']
    need(2<=len(pending)==watch['pending_observations']<=128 and hold['first']==pending[0] and hold['second']==pending[1],'pending_observation_binding')
    need(all(r['path']==hold['path'] and r['writes']==0 and r['wav_bytes']==32044 for r in pending),'pending_original_shape')
    need(pending[0]['request_count']==pending[1]['request_count'] and pending[1]['monotonic']>pending[0]['monotonic'] and not any(pending[0]['index']<s['index']<pending[1]['index'] for s in starts),'pending_no_http')
    need(hold['runner_intake_inventory_unchanged'] is True and hold['inputs']==1 and hold['before_inventory']==hold['after_inventory'] and hold['before_inventory'],'pending_saved_inventory')
    saved=hold['before_inventory'];need(all(P(p).is_relative_to(run/'runner') or P(p).is_relative_to(run/'intake') for p in saved),'pending_inventory_target')
    need(set(str(run/'runner'/n) for n in ['config.json','runner.lock','input-000.intent.json','input-000.done.json'])<=set(saved) and not any('input-001.' in p or 'finalize.' in p or 'export.' in p for p in saved),'pending_ledger_one_input')
    for p,pin in saved.items():need(set(pin)=={'bytes','sha256','mtime_ns','ctime_ns'} and type(pin['bytes']) is int and len(pin['sha256'])==64,'pending_inventory_pin')
    names=[r['event'] for r in arrivals];need(names==['configured','first_admitted','partial_arrival','pending_hold_verified','marker_published']+sum((['complete_arrival','admitted'] for _ in range(7)),[]),'arrival_order')
    need(arrivals[0]['session_id']==sid and arrivals[0]['explicit_session_posts']==1 and arrivals[1]['watch_pid']==pid and arrivals[1]['inputs']==1,'initial_arrival_binding')
    need(all(r['monotonic']>enter['monotonic'] for r in arrivals[1:]),'later_arrivals_after_watch_start')
    need(arrivals[2]['path']==arrivals[4]['path']==hold['path'] and arrivals[2]['inputs']==arrivals[3]['inputs']==1 and arrivals[4]['ordinal']==1 and arrivals[3]['writes']==0 and arrivals[3]['request_count']==pending[1]['request_count'],'partial_arrival_binding')
    need(arrivals[2]['monotonic']<=pending[1]['monotonic']<arrivals[4]['monotonic'],'partial_marker_window')
    for index in range(2,9):
        arrival,admission=arrivals[5+(index-2)*2:7+(index-2)*2]
        need(arrival['ordinal']==admission['ordinal']==index and admission['inputs']==index+1 and arrival['close_reason']==('STOP' if index==8 else 'ROTATION'),'incremental_arrivals')
        need(arrival['path']==load(run/'runner'/f'input-{index:03d}.intent.json')['path'],'arrival_selected_path')
    return dict(passed=True,watch_pid=pid,watch_elapsed_seconds=returned['monotonic']-enter['monotonic'],requests=len(requests),network_writes=out['network_writes'],polls=out['polls'],pending_observations=len(pending),pending_http_requests=0,pending_intake_unchanged=True,post_start_arrivals=8,finalize_request_range=[finalize['request_count'],finalized['request_count']],export_request_range=[export['request_count'],exported['request_count']])


def audit_export(run,db,work,meta,status,sid):
    from audio_backend import transcript_contract as c, recording_primary_provenance as pp
    from audio_backend import native_owned_core_seams as core
    import unicodedata
    phases=[]
    rows=events(run/'watch-events.jsonl'); first=one(rows,'export_enter'); last=one(rows,'export_return')
    for directory, phase_name in [('export','runner-export'),('export-repeat','independent-export-repeat')]:
        if directory=='export':
            watched=load(run/'watch-result.json')
            phase=dict(returned=watched['returned'],result=watched['result'],requests=watched['requests'][first['request_count']:last['request_count']])
            need(0<=first['request_count']<last['request_count']<=len(watched['requests']),'export_slice_bounds')
        else:phase=load(run/(phase_name+'.json'))
        phases.append(phase)
        need(phase['returned'] and 'error' not in phase,'export_phase')
        need(0<len(phase['requests'])<=128 and all(r['method']=='GET' and r['key'] is None and r['body'] is None and r['file_sha256'] is None and r['path'].startswith('/sessions/'+sid+'/') for r in phase['requests']),'export_get_only_bound')
        transcript=[r for r in phase['requests'] if '/transcript?' in r['path']]
        need(len(transcript)==2 and all(r['path'].endswith('&projection=whole-primary-v1') for r in transcript),'explicit_primary_transcript')
        root=run/directory;need(not root.is_symlink() and stat.S_IMODE(root.stat().st_mode)==0o700,'private_export_directory')
        need({p.name for p in root.iterdir()}=={'export.json','transcript.txt','complete.json'},'export_exact_inventory')
        for path in root.iterdir():
            info=path.lstat();need(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1 and info.st_uid==os.geteuid(),'private_export_file')
        manifest=load(root/'complete.json')
        if directory=='export-repeat':need(manifest==phase['result'] and phase['local_files_unchanged'],'export_manifest_binding')
        else:need(phase['result']['export_completed'] and phase['result']['status']=='exported','runner_export_binding')
        need(manifest['schema']=='recorder-incremental-span-export-v1' and manifest['status']=='exported' and set(manifest['artifacts'])=={'export.json','transcript.txt'},'export_manifest')
        for name,pin in manifest['artifacts'].items():need(pin==dict(bytes=(root/name).stat().st_size,sha256=sha(root/name)),'export_artifact_hash')
    for name in ['export.json','transcript.txt','complete.json']:need((run/'export'/name).read_bytes()==(run/'export-repeat'/name).read_bytes(),'exact_export_repeat')
    document=load(run/'export/export.json');manifest=load(run/'export/complete.json');units=document['units']
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
    return dict(native_source_receipt_id=receipt.receipt_id,native_source_receipt_exact=True,passed=True,export_operations_get_only=2,export_request_counts=[len(p['requests']) for p in phases],export_transcript_pages=1,primary_units=len(units),nonempty_primary_units=sum(bool(u['text'].strip()) for u in units),exact_repeat_artifacts=True,private_directory_mode='0700',private_file_mode='0600',canonical_asr_db_identity_exact=True,all_exported_refs_native_receipt_bound=True,retained_primary_packets=len(packets),native_core_outcomes=len(outcomes),native_core_pcm_coverage_exact=True,asr_runtime=meta['asr_runtime'],asr_stage_seconds=meta['stage_seconds']['asr'],asr_turn_batches=meta['asr_turn_batches'],asr_backend='sensevoice',speaker_scope_count=len({tuple(u['speaker_scope']) for u in units if u['speaker_scope'] is not None}),accuracy_verified=False,speaker_identity_verified=False,capture_completeness_proven=False,artifacts=manifest['artifacts'],complete_manifest_sha256=sha(run/'export/complete.json'))



def audit_ledger(run,fixture,sid):
    def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')+b'\n'
    def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
    root=run/'runner';config=load(root/'config.json');config_hash=digest(config)
    need(config['schema']=='recorder-inbox-runner-config-v1' and config['session_id']==sid and config['capture_id']=='inbox-watch' and config['inbox_dir']==str(run/'inbox') and config['job_dir']==str(run/'intake') and config['output_dir']==str(run/'export') and config['max_hours']==1 and config['max_inputs']==9,'runner_config')
    expected_files={'config.json','runner.lock','finalize.intent.json','finalize.done.json','export.intent.json','export.done.json'};intents=[]
    for i,f in enumerate(fixture['deliveries']):
        path=run/'inbox'/f['delivery']['attempt'];name='input-%03d'%i;expected_files|={name+'.intent.json',name+'.done.json'}
        intent=load(root/(name+'.intent.json'));done=load(root/(name+'.done.json'))
        need(intent==dict(schema='recorder-inbox-runner-v1',config_sha256=config_hash,ordinal=i,path=str(path),delivery=f['delivery']),'runner_frozen_selected_original')
        need(done==dict(schema='recorder-inbox-runner-v1',config_sha256=config_hash,intent_sha256=digest(intent)),'runner_receipt');intents.append(intent)
    need({p.name for p in root.iterdir()}==expected_files,'runner_ledger_inventory')
    for kind in ['finalize','export']:
        intent=load(root/(kind+'.intent.json'));done=load(root/(kind+'.done.json'))
        need(intent==dict(schema='recorder-inbox-runner-v1',config_sha256=config_hash,kind=kind,input_intents_sha256=digest(intents)),'runner_terminal_intent')
        expected=dict(schema='recorder-inbox-runner-v1',config_sha256=config_hash,intent_sha256=digest(intent))
        if kind=='export':expected['complete_sha256']=sha(run/'export/complete.json')
        need(done==expected,'runner_terminal_receipt')
    return config



def audit(envelope,output,preflight):
    e=P(envelope).resolve();o=P(output).resolve();run=e/'run';pre=load(preflight)
    need(not o.exists() and not any((p/'.git').is_file() or os.path.lexists(p/'.git/HEAD') for p in (o,*o.parents)),'fresh_private_audit')
    plan,candidate,assets,pin_count=source_check(e,pre)
    need(all(o!=root and root not in o.parents for root in (e,candidate,P(plan['source']),P(plan['fixture_path']).parent)),'audit_in_protected_tree')
    before=inv(run,True);fixture=load(plan['fixture_path']);originals={k:v for f in fixture['deliveries'] for k,v in inv(f['path'],True).items()}
    need(originals==pre['original_delivery_files'],'originals_changed_since_review')
    envelope_names=['LAUNCH_PLAN.json','launch.sh','ASSETS.json','CANDIDATE_SOURCE_PINS.json','SOURCE_BASELINE_PINS.json','OUTER_WAIT.json','timing.txt']
    envelope_hashes={name:sha(e/name) for name in envelope_names}
    r=load(run/'RESULT.json');terminal=load(run/'terminal.json');sid=r['session_id']
    need(r['schema']=='public-speech-native-inbox-watch-v1' and r['passed'] and r['scenario_complete'],'run_failed')
    need(not any(k.endswith('error') for k in r) and r['source_unchanged'] and r['originals_unchanged'],'run_error_or_changed')
    need(all(r[k] is False for k in ['ftps_receiver_test','deployed','device_test','native_service_restart_test','abrupt_runner_crash_test','product_session_autocreation']),'run_overclaim')
    need(all(r['startup'][k] is True for k in ['strict','inherited_lock','worker_alive']),'native_startup')
    need(r['shutdown']['exit_code']==0 and r['shutdown']['terminal']==terminal,'terminal_binding')
    need(terminal['error'] is None and terminal['service_cleanup_error'] is None and terminal['shutdown_errors']=={},'cleanup_errors')
    need(all(terminal[k] for k in ['lock_closed','store_safe','permit_clear']) and not terminal['worker_alive'] and all(terminal['shutdown']['predicates'].values()) and terminal['committed_claim_late_count']==0,'cleanup_predicates')
    need(load(e/'OUTER_WAIT.json')==dict(observed_exit_code=0,original_shell_wait_completed=True),'outer_wait')
    match=re.fullmatch(r'real_seconds=([0-9.]+) user_seconds=([0-9.]+) sys_seconds=([0-9.]+)\n?',(e/'timing.txt').read_text());need(match is not None,'timing');wall,user,system=map(float,match.groups())
    need(0<wall<480 and 0<r['peak_sampled_rss']<=6*1024**3,'native_resource_bounds')
    need(shutil.disk_usage(e).free>=11*1024**3//2,'post_audit_disk_floor')
    need(load(run/'ASSET_VERIFIED.json')==assets,'asset_verification_receipt')
    runtime_pins=[v for v in load(e/'CANDIDATE_SOURCE_PINS.json') if v['path'].startswith(('audio_backend/','tools/')) and v['path'].endswith('.py')]
    need(load(run/'SOURCE_PINS.json')==runtime_pins,'source_run_receipt')
    sys.path.insert(0,str(candidate));sys.dont_write_bytecode=True
    from audio_backend.native_owned_cell_store import qualified_runtime,runtime_fingerprints,Budget
    from audio_backend.recorder_delivery import inspect_delivery
    from audio_backend.recorder_snapshot import inspect_snapshot
    from audio_backend.recorder_sequence import reconcile_snapshots
    q=qualified_runtime(run/'data');need(q is not None and (run/'data').stat().st_dev==27,'runtime_device');observed=runtime_fingerprints(Budget())
    need(all(getattr(q,k)==v for k,v in observed.items()),'runtime_fingerprints')
    event_audit=audit_events(run,r);phases,requests,pids=audit_phases(run,r);config=audit_ledger(run,fixture,sid)
    producer_audit=audit_producer(plan['fixture_path'],candidate)
    status=phases['independent-status']['result']
    need(status['status']=='completed' and status['analysis_completion_observed'] and status['analysis_completion_verified'] and status['read_only'] and status['inputs']==9 and status['work_chunks']==1 and status['frames']==144000,'status_complete')
    mapping=[dict(ordinal=i,original_sequence=i+2,original_epoch=7,original_epoch_frame_start=32000+i*16000,original_epoch_frame_end=48000+i*16000,span_start_frame=i*16000,span_end_frame=(i+1)*16000) for i in range(9)]
    need(status['input_mapping']==mapping,'status_mapping')
    desc=status['span'];need(desc['original_epoch']==7 and desc['original_first_sequence']==2 and desc['original_last_sequence']==10 and desc['original_close_reason']=='STOP' and desc['recorder_stop_observed'] is True and desc['eos_meaning']=='selected_span_only' and desc['before_span_gap_frames'] is None and desc['after_span_gap_frames'] is None and desc['hardware_frame_mapping']=='UNESTABLISHED','terminal_truth')
    need(all(status[k] is False for k in ['observation_atomic','remote_request_identity_verified','target_identity_authenticated','producer_identity_authenticated','accuracy_verified','physical_continuity_proven','capture_completeness_proven','concurrent_writer_safety_guaranteed']),'status_overclaim')
    original_paths=[]
    for f in fixture['deliveries']:
        arrived=run/'inbox'/f['delivery']['attempt'];original_paths.append(arrived)
        need(inspect_delivery(arrived,validate_metadata=True)==f['delivery'],'arrived_original_report')
        for name,h in f['hashes'].items():need(sha(arrived/name)==h,'arrived_original_bytes')
    need(set((run/'inbox').iterdir())==set(original_paths),'exact_arrived_inventory')
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
        need(report['epoch']==7 and report['sequence']==i+2 and report['frame_start']==32000+i*16000 and report['frames']==16000 and report['close_reason']==('STOP' if i==8 else 'ROTATION'),'original_geometry')
        need(v['id']==piece['input_id']==report['source_id'] and v['frames']==16000 and v['normalized_start']==piece['capture_start']==piece['normalized_start']==i*16000 and v['clean_eof'],'input_mapping')
        snap=P(v['identity']['path']).parent;need(snap==job/'admissions'/str(i)/'snapshot','snapshot_path');snapshots.append(snap)
        need(inspect_snapshot(snap)['delivery']==report,'snapshot_metadata')
        for name,h in f['hashes'].items():need(sha(path/name)==h and sha(snap/name)==h,'snapshot_bytes')
        need(v['identity']['sha256']==piece['digest']==report['wav_sha256'] and piece['pcm_sha256']==report['pcm_sha256'] and piece['frames']==16000 and piece['input_start']==0,'piece_hashes')
        combined.append(pcm(path/'audio.wav'))
    seq=reconcile_snapshots(snapshots);need(len(seq['captures'])==1 and all(x['relation']=='declared_adjacent' and not x['reasons'] for x in seq['captures'][0]['edges']),'sequence_edges')
    combined=b''.join(combined);need(len(combined)==288000 and any(combined),'combined_pcm');need(hashlib.sha256(combined).hexdigest()==fixture['selected_pcm_sha256']=='824962e469c1b5c3969804b2846b1428fe4a194a1744d5599aa595ae7f69af73','fixed_public_source_pcm')

    initial=load(run/'watch-result.json')['requests'][event_audit['finalize_request_range'][0]:event_audit['finalize_request_range'][1]];puts=[v for v in initial if v['method']=='PUT'];seals=[v for v in initial if v['method']=='POST' and v['path'].endswith('/seal')]
    need(len(puts)==len({v['key'] for v in puts})==9,'nine_unique_source_puts')
    frozen=dict(method='POST',path=f"/sessions/{sid}/recordings/inbox-watch/pages/1/seal",key=None,body=page['seal'],file_sha256=None)
    need(seals and all(v==frozen for v in seals),'exact_frozen_seal')
    mutation_paths=[v for v in requests if v['method']!='GET']
    need(len(mutation_paths)==10+len(seals) and sum(v['method']=='POST' and v['path'].endswith('/pages') for v in mutation_paths)==1,'exact_mutation_inventory')
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
        need((batches[0]['capture_origin_frame'],batches[0]['capture_end_frame'],batches[0]['eos'])==(0,144000,1) and batches[0]['capture_id']=='inbox-watch','db_capture')
        dp=dbpages[0];need((dp['state'],dp['released'],dp['eos'],dp['start_frame'],dp['end_frame'])==('published',1,1,0,144000),'db_page')
        need(dp['id']==page['server_id'] and dp['seal_key']==page['seal']['idempotency_key'],'db_seal_key');saved=json.loads(dp['seal_request']);need(all(saved[k]==page['seal'][k] for k in ['end_frame','eos','expected_revision']),'db_seal_body')
        for i,(v,piece,put) in enumerate(zip(sources,page['pieces'],puts)):
            need(v['id']==piece['server_id'] and v['idempotency_key']==piece['key']==put['key'] and v['digest']==piece['digest']==sha(v['path'])==put['file_sha256'] and v['source_start_frame']==i*16000 and v['frames']==16000 and v['batch_id']==batches[0]['id'] and v['page_id']==dp['id'],'db_source')
            need(pcm(v['path'])==combined[i*32000:(i+1)*32000],'db_source_pcm')
        v=work[0];need(v['status']=='completed' and v['receipt_state']=='durable' and v['native_receipt_status']=='receipt_retained' and v['frames']==144000 and v['capture_start_frame']==0 and v['next_offset']==v['duration']==9.0,'native_work')
        need(sha(v['path'])==v['digest'] and pcm(v['path'])==combined,'native_pcm')
        meta=json.loads(v['metadata']);need(meta['diarization_backend']=='nemotron3-native' and not meta.get('silence_fast_path',False) and meta['native_diarization']['finished'] and meta['native_diarization']['processed_until']==9.0,'native_execution')
        need(meta.get('asr_backend')=='sensevoice' and meta.get('model_execution') not in ('native_diarization_only_asr_skipped','not_run_silence'),'native_asr_path'); export_audit=audit_export(run,db,work,meta,status,sid)
        need(len(ranges)==9 and all(v['relation']=='owner' and v['capture_start_frame']==i*16000 and v['source_start_frame']==0 and v['work_start_frame']==i*16000 and v['frames']==16000 and v['source_id']==sources[i]['id'] and v['chunk_id']==work[0]['chunk_id'] for i,v in enumerate(ranges)),'owner_ranges')
        streams=rows('SELECT * FROM stream_chunks');need(len(streams)==1 and streams[0]['stream_final']==streams[0]['upload_stream_final']==1,'stream_final')
        policies=rows('SELECT * FROM strict_recording_policy');need(len(policies)==1 and policies[0]['policy']=='strict-primary-payload-v2','strict_policy')
        control=rows('SELECT state,nonce FROM strict_recording_control');need(len(control)==1 and control[0]['state']=='completed' and control[0]['nonce'] is None,'native_control')
    finally:db.close()
    need(inv(run,True)==before,'original_evidence_mutated');need({k:v for f in fixture['deliveries'] for k,v in inv(f['path'],True).items()}==originals,'original_input_mutated');need({n:sha(e/n) for n in envelope_hashes}==envelope_hashes,'envelope_mutated')
    return dict(schema='finite-inbox-watch-native-independent-saved-byte-audit-v1',passed=True,
        inputs=9,pages=1,sources=9,frames=144000,work_chunks=1,original_epoch=7,
        original_sequences=list(range(2,11)),original_start_frame=32000,original_terminal_close='STOP',
        selected_span_only=True,distinct_clean_child_processes=len(pids),requests=len(requests),
        explicit_harness_session_creations=1,runner_session_creations=0,
        exact_mutations_only_at_explicit_finalize=True,frozen_seal_calls=len(seals),exact_selected_path_ledger=True,
        one_finite_watch_process=True,post_start_arrivals=8,watch_event_audit=event_audit,producer_audit=producer_audit,source_and_work_pcm_exact=True,
        owner_range_coverage_exact=True,native_execution=meta,export=export_audit,
        original_evidence_unchanged=True,originals_unchanged_since_prelaunch=True,
        database_audited_on_copy=True,closed_database_sidecar_bytes={k:v.stat().st_size for k,v in sidecars.items()},
        assets_verified=len(assets),candidate_files_verified=pin_count,runtime_verified=observed,
        terminal_cleanup_passed=True,elapsed_seconds=wall,cpu_seconds=user+system,peak_sampled_rss=r['peak_sampled_rss'],
        envelope_hashes=envelope_hashes,result_sha256=sha(run/'RESULT.json'),terminal_sha256=sha(run/'terminal.json'),
        audit_source_sha256=sha(__file__),limitations=[
            'Saved original wait receipts are not independent re-observation of historical waits.',
            'One finite foreground watch, not abrupt crash or native service restart.',
            'Controlled local arrivals and real loopback HTTP, not an FTPS receiver or physical device.',
            'Real native primary ASR output and export linkage, not recognition accuracy or speaker identity.',
            'Selected-span completion, not full-capture continuity, multi-page, endurance or production rollout.',
            'GET-only status is not an atomic remote observation; limits are sampled/cooperative.'])


def audit_phases(run,result):
    sid=result['session_id'];watch=load(run/'watch-result.json');pids=[watch['pid']];requests=list(watch['requests']);phases={}
    for name in ['independent-status','independent-export-repeat']:
        value=load(run/(name+'.json'));wait=load(run/(name+'-wait.json'));spec=load(run/(name+'-spec.json'))
        need(value['phase']==spec['phase']==name and value['returned'] is True and 'error' not in value and value['local_files_unchanged'] is True,'independent_phase')
        need(wait==dict(pid=value['pid'],original_popen_wait_completed=True,exit_code=0),'independent_original_wait')
        need(spec['api']=='http://127.0.0.1:'+str(result['startup']['port']) and spec['session_id']==sid and spec['capture_id']=='inbox-watch','independent_target')
        need(spec['state_dir']==str(run/'runner') and spec['job_dir']==str(run/'intake'),'independent_local_binding')
        need(0<len(value['requests'])<=128 and all(r['method']=='GET' and (r['path']=='/sessions/'+sid or r['path'].startswith('/sessions/'+sid+'/')) and r['key'] is None and r['body'] is None and r['file_sha256'] is None for r in value['requests']),'independent_get_only')
        phases[name]=value;pids.append(value['pid']);requests.extend(value['requests'])
    need(len(set(pids))==3,'three_distinct_children')
    journal=[json.loads(line) for line in (run/'http.jsonl').read_text().splitlines() if line.strip()]
    posts=[v['intent'] for v in journal if 'intent' in v and v['intent']['method']=='POST']
    need(len(posts)==1 and posts[0]['path']=='/sessions','single_harness_session')
    need(not any(v['method']=='POST' and v['path']=='/sessions' for v in requests),'child_session_creation')
    return phases,requests,pids


if __name__=='__main__':
    os.umask(0o077)
    a=argparse.ArgumentParser();a.add_argument('--envelope',required=True);a.add_argument('--output',required=True);a.add_argument('--preflight',required=True);x=a.parse_args()
    r=audit(x.envelope,x.output,x.preflight);(P(x.output)/'INDEPENDENT_AUDIT.json').write_text(json.dumps(r,indent=2,sort_keys=True)+'\n');print(json.dumps(r,sort_keys=True))
