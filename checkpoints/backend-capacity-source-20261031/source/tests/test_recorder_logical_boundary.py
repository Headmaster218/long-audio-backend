"""Logical closure controls with real tiny WAV inputs and uploader journals."""
import json
from pathlib import Path
import pytest
from audio_backend import recorder_logical_boundary as logical
from audio_backend import recorder_incremental_span_status as status
from audio_backend import recorder_incremental_span_intake as span
from audio_backend import recording_uploader as u
from test_recorder_inbox_runner import example, invoke, Peer, runner


def setup(tmp_path):
    paths,c,root=example(tmp_path,3)
    runner.configure(state_dir=root,config=c,first_delivery=paths[0]);peer=Peer()
    assert invoke(root,peer,max_actions=2)['inputs']==2
    args=dict(state_dir=root,successor_attempt=paths[2].name,next_state_dir=tmp_path/'next-runner',
        next_job_dir=tmp_path/'next-job',next_output_dir=tmp_path/'next-export',next_capture_id='next-span',
        next_session_name='next bounded span',acknowledge_logical_analysis_boundary=True)
    return paths,c,root,peer,args


def files(root):return {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_logical_tail_finalizes_without_mutating_rotations(tmp_path):
    paths,c,root,peer,args=setup(tmp_path)
    before=files(Path(c['inbox_dir']))
    assert logical.prepare(**args)['network_requests']==0
    assert logical.prepare(**args)['status']=='logical_boundary_prepared'
    assert runner.preflight(state_dir=root)['status']=='awaiting_finalize'
    assert invoke(root,peer)['status']=='awaiting_finalize'
    result=invoke(root,peer,finalize=True)
    assert peer.eos and result['inputs']==2
    assert before==files(Path(c['inbox_dir']))
    job=Path(c['job_dir']);guard=u.Guard(u.Limits(disk_floor=0))
    destination=runner.single.target(c['api'],'operator-selected-unverified',c['session_id'],c['capture_id'],0)
    header,receipts,records,_=status._local(job,destination,guard)
    policy=span.POLICY.for_job(job,header,receipts,records,lambda:120)
    journal,_=status._journal(job,destination,header,receipts,guard,policy=policy)
    assert journal.data['emitted_frames']==journal.data['published_frames']==8
    assert receipts[-1]['delivery']['close_reason']=='ROTATION'
    summary=policy.result_fields(records,header)
    assert summary['recording_complete'] is False
    assert summary['eos_meaning']=='selected_logical_analysis_span_only'
    assert summary['logical_boundary']['successor']['delivery']['frame_start']==1008
    assert not (tmp_path/'next-runner').exists()


def test_no_consent_no_receipt_no_eos(tmp_path):
    paths,c,root,peer,args=setup(tmp_path);before=files(tmp_path)
    with pytest.raises(runner.Refused,match='ack_required'):
        logical.prepare(**dict(args,acknowledge_logical_analysis_boundary=False))
    assert files(tmp_path)==before and not peer.eos
    assert not span.POLICY.finalizable(runner.inspect_delivery(paths[1],validate_metadata=True))


@pytest.mark.parametrize('field,value',[('frames',7),('recording_complete',True),('consent','implicit'),
    ('runner_config_sha256','0'*64),('admissions_sha256','0'*64)])
def test_changed_receipt_rejected_before_network(tmp_path,field,value):
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args)
    p=Path(c['job_dir'])/logical.NAME;d=json.loads(p.read_bytes());d[field]=value;p.write_bytes(runner.single._json(d))
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('HTTP before logical receipt validation')
    with pytest.raises(runner.Refused):invoke(root,NoHTTP(),finalize=True)
    assert not peer.eos


def test_partial_receipt_preserved(tmp_path,monkeypatch):
    paths,c,root,peer,args=setup(tmp_path)
    original=logical.single.manifest_write
    def crash(path,value):
        if path.name==logical.NAME:
            path.write_bytes(b'{');path.chmod(0o600);raise KeyboardInterrupt
        return original(path,value)
    monkeypatch.setattr(logical.single,'manifest_write',crash)
    with pytest.raises(KeyboardInterrupt):logical.prepare(**args)
    monkeypatch.setattr(logical.single,'manifest_write',original)
    before=files(tmp_path)
    with pytest.raises(Exception):logical.prepare(**args)
    assert files(tmp_path)==before


def test_exact_successor_and_fresh_next_required(tmp_path):
    paths,c,root,peer,args=setup(tmp_path)
    with pytest.raises(runner.Refused,match='exact_successor'):
        logical.prepare(**dict(args,successor_attempt=paths[0].name))
    args['next_state_dir'].mkdir(mode=0o700)
    with pytest.raises(runner.Refused,match='fresh_next'):logical.prepare(**args)
    assert not (Path(c['job_dir'])/logical.NAME).exists()


def test_pending_selection_refuses(tmp_path):
    paths,c,root=example(tmp_path,2)
    runner.configure(state_dir=root,config=c,first_delivery=paths[0])
    with pytest.raises(runner.Refused,match='pending_input'):
        logical.prepare(state_dir=root,successor_attempt=paths[1].name,next_state_dir=tmp_path/'next',
            next_job_dir=tmp_path/'job2',next_output_dir=tmp_path/'out2',next_capture_id='new',
            acknowledge_logical_analysis_boundary=True)


@pytest.mark.parametrize('consumer',['intake','status','export'])
def test_consumers_reject_changed_authority_before_http(tmp_path,consumer):
    from audio_backend import recorder_incremental_span_export as export
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args)
    if consumer != 'intake':invoke(root,peer,finalize=True)
    p=Path(c['job_dir'])/logical.NAME;d=json.loads(p.read_bytes());d['consent']='not-granted'
    p.write_bytes(runner.single._json(d))
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('HTTP before authority validation')
    opts=dict(job_dir=c['job_dir'],api=c['api'],session_id=c['session_id'],capture_id=c['capture_id'],
        exclusive_writer=True,transport=NoHTTP(),limits=u.Limits(disk_floor=0))
    with pytest.raises(runner.Refused):
        if consumer=='intake':span.run(**opts,finalize=True,acknowledge_span_local_closure=True)
        elif consumer=='status':status.run(**opts)
        else:export.run(**opts,output_dir=tmp_path/'output-test')
    assert not (tmp_path/'output-test').exists()


def test_changed_successor_blocks_cut_consumption(tmp_path):
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args)
    (paths[2]/'audio.wav').write_bytes(b'changed')
    before=files(tmp_path)
    with pytest.raises(Exception):invoke(root,peer,finalize=True)
    assert files(tmp_path)==before and not peer.eos


def test_complete_receipt_interruption_exact_resume(tmp_path,monkeypatch):
    paths,c,root,peer,args=setup(tmp_path)
    original=logical.single.manifest_write
    def crash(path,value):
        original(path,value)
        if path.name==logical.NAME:raise KeyboardInterrupt
    monkeypatch.setattr(logical.single,'manifest_write',crash)
    with pytest.raises(KeyboardInterrupt):logical.prepare(**args)
    monkeypatch.setattr(logical.single,'manifest_write',original)
    assert logical.prepare(**args)['status']=='logical_boundary_prepared'
    invoke(root,peer,finalize=True)
    assert peer.eos


def test_finalize_interruption_retains_same_bound_cut(tmp_path,monkeypatch):
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args)
    receipt=(Path(c['job_dir'])/logical.NAME).read_bytes()
    original=runner.single.manifest_write
    def crash(path,value):
        original(path,value)
        if path.name=='finalize.intent.json':raise KeyboardInterrupt
    monkeypatch.setattr(runner.single,'manifest_write',crash)
    with pytest.raises(KeyboardInterrupt):invoke(root,peer,finalize=True)
    monkeypatch.setattr(runner.single,'manifest_write',original)
    invoke(root,peer,finalize=True)
    assert peer.eos and (Path(c['job_dir'])/logical.NAME).read_bytes()==receipt


def test_physical_stop_cannot_be_relabelled(tmp_path):
    paths,c,root,peer,args=setup(tmp_path)
    invoke(root,peer,max_actions=1)
    before=files(tmp_path)
    with pytest.raises(runner.Refused,match='rotation_required'):logical.prepare(**args)
    assert before==files(tmp_path) and not peer.eos


def test_tampered_receipt_cannot_authorize_interrupted_finalize(tmp_path,monkeypatch):
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args)
    original=runner.single.manifest_write
    def crash(path,value):
        original(path,value)
        if path.name=='finalize.intent.json':raise KeyboardInterrupt
    monkeypatch.setattr(runner.single,'manifest_write',crash)
    with pytest.raises(KeyboardInterrupt):invoke(root,peer,finalize=True)
    monkeypatch.setattr(runner.single,'manifest_write',original)
    p=Path(c['job_dir'])/logical.NAME;d=json.loads(p.read_bytes());d['frames']+=1
    p.write_bytes(runner.single._json(d))
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('HTTP with invalid recovered authority')
    with pytest.raises(runner.Refused):invoke(root,NoHTTP(),finalize=True)
    assert not peer.eos


@pytest.mark.parametrize('missing,consumer', [(m,c) for m in ('receipt','job_anchor','runner_anchor')
    for c in ('runner','intake','status')] + [('job_both','runner')])
def test_individual_authority_loss_never_unfences_or_calls_http(tmp_path,missing,consumer):
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args)
    job=Path(c['job_dir'])
    if consumer=='status':invoke(root,peer,finalize=True)
    if missing in ('receipt','job_both'):(job/logical.NAME).unlink()
    if missing in ('job_anchor','job_both'):(job/logical.ANCHOR).unlink()
    if missing=='runner_anchor':(root/logical.RUNNER_ANCHOR).unlink()
    before=files(tmp_path)
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('HTTP with missing authority')
    opts=dict(job_dir=c['job_dir'],api=c['api'],session_id=c['session_id'],capture_id=c['capture_id'],
        exclusive_writer=True,transport=NoHTTP(),limits=u.Limits(disk_floor=0))
    with pytest.raises(Exception):
        if consumer=='runner':invoke(root,NoHTTP())
        elif consumer=='intake':span.run(**opts,finalize=True,acknowledge_span_local_closure=True)
        else:status.run(**opts)
    assert files(tmp_path)==before and not (root/'input-002.intent.json').exists()


def test_semantically_valid_receipt_edit_is_hash_fenced(tmp_path):
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args)
    p=Path(c['job_dir'])/logical.NAME;d=json.loads(p.read_bytes());d['next_session_intent']['session_name']='different valid name'
    p.write_bytes(runner.single._json(d))
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('HTTP with edited receipt')
    with pytest.raises(runner.Refused,match='hash_changed'):invoke(root,NoHTTP(),finalize=True)


def test_failed_receipt_sync_cannot_be_bypassed_by_retry_or_finalize(tmp_path,monkeypatch):
    import os
    paths,c,root,peer,args=setup(tmp_path)
    real=os.fsync;calls=[]
    def fail(fd):
        if Path(os.readlink('/proc/self/fd/'+str(fd))).name==logical.NAME:
            calls.append(1);raise OSError('injected durability failure')
        return real(fd)
    monkeypatch.setattr(os,'fsync',fail)
    with pytest.raises(OSError):logical.prepare(**args)
    with pytest.raises(OSError):logical.prepare(**args)
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('HTTP before durable authority')
    with pytest.raises(OSError):invoke(root,NoHTTP(),finalize=True)
    assert len(calls)>=3 and not peer.eos
    monkeypatch.setattr(os,'fsync',real)
    invoke(root,peer,finalize=True)
    assert peer.eos


@pytest.mark.parametrize('boundary',[logical.ANCHOR,logical.RUNNER_ANCHOR])
def test_anchor_crash_before_receipt_refuses_consumers_then_exact_prepare_resumes(tmp_path,monkeypatch,boundary):
    paths,c,root,peer,args=setup(tmp_path)
    original=logical.single.manifest_write
    def crash(path,value):
        original(path,value)
        if path.name==boundary:raise KeyboardInterrupt
    monkeypatch.setattr(logical.single,'manifest_write',crash)
    with pytest.raises(KeyboardInterrupt):logical.prepare(**args)
    monkeypatch.setattr(logical.single,'manifest_write',original)
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('HTTP during incomplete preparation')
    with pytest.raises(Exception):invoke(root,NoHTTP())
    assert logical.prepare(**args)['status']=='logical_boundary_prepared'
    invoke(root,peer,finalize=True);assert peer.eos


def test_effectful_http_rechecks_and_syncs_bound_authority(tmp_path,monkeypatch):
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args)
    real=logical.durable;checks=[];writes=[]
    def observe(job,expected=None):
        real(job,expected);checks.append(expected is not None)
    monkeypatch.setattr(logical,'durable',observe)
    request=peer.request
    def network(method,path,**kw):
        if method!='GET':
            assert len(checks)>len(writes) and checks[-1] is True
            writes.append(path)
        return request(method,path,**kw)
    peer.request=network
    invoke(root,peer,finalize=True)
    assert writes and len(checks)>=len(writes)+1


def test_exact_full_page_uses_finish_without_empty_tail(tmp_path,monkeypatch):
    monkeypatch.setattr(u,'PAGE_FRAMES',8);monkeypatch.setattr(u,'SOURCE_FRAMES',4)
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args)
    invoke(root,peer,finalize=True)
    job=Path(c['job_dir']);guard=u.Guard(u.Limits(disk_floor=0))
    destination=runner.single.target(c['api'],'operator-selected-unverified',c['session_id'],c['capture_id'],0)
    header,receipts,records,_=status._local(job,destination,guard)
    policy=span.POLICY.for_job(job,header,receipts,records,lambda:120)
    journal,_=status._journal(job,destination,header,receipts,guard,policy=policy)
    assert journal.data['emitted_frames']==journal.data['published_frames']==8
    assert peer.eos and peer.finish is not None and all(p['eos'] is False for p in peer.pages.values())
    assert journal.data['inputs_done']==2 and journal.data['pages_done']==1


def test_export_recovery_local_pin_uses_logical_authority_and_pins_it(tmp_path):
    from audio_backend import recorder_export_recovery as recovery
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args);invoke(root,peer,finalize=True)
    destination=runner.single.target(c['api'],'operator-selected-unverified',c['session_id'],c['capture_id'],0)
    pin=recovery._LocalPin(c['job_dir'],destination,u.Guard(u.Limits(disk_floor=0)))
    pin.check_full()
    assert Path(c['job_dir'])/logical.NAME in pin.identities
    assert root/logical.RUNNER_ANCHOR in pin.identities
    assert paths[2]/'audio.wav' in pin.identities
    p=Path(c['job_dir'])/logical.NAME;d=json.loads(p.read_bytes());d['frames']+=1
    p.write_bytes(runner.single._json(d))
    with pytest.raises(Exception):pin.check()
    with pytest.raises(Exception):pin.check_full()


def test_real_status_and_recovery_export_preserve_logical_boundary(tmp_path):
    """Real GET/export validators; ASGI work completion is explicitly synthetic."""
    from test_recording_session_contract import handler_app,session
    from test_recorder_span_status import Routes
    from audio_backend import recorder_export_recovery as recovery
    with handler_app(tmp_path/'server') as (client,app):
        sid=session(client)
        paths,c,root=example(tmp_path,3);c['session_id']=sid
        runner.configure(state_dir=root,config=c,first_delivery=paths[0])
        class PublicationPeer(Peer):
            def request(self,method,path,**kw):
                value=super().request(method,path.replace('/sessions/'+sid,'/sessions/known'),**kw)
                if path.endswith('/recording-contract'):value['session_id']=sid
                return value
        peer=PublicationPeer();invoke(root,peer,max_actions=2)
        logical.prepare(state_dir=root,successor_attempt=paths[2].name,next_state_dir=tmp_path/'next',
            next_job_dir=tmp_path/'next-job',next_output_dir=tmp_path/'next-out',next_capture_id='next',
            acknowledge_logical_analysis_boundary=True)
        invoke(root,peer,finalize=True)
        job=Path(c['job_dir']);state=json.loads((job/'uploader/uploader.json').read_bytes())
        pages=[json.loads(p.read_bytes())['payload'] for p in sorted((job/'uploader/uploader.history').glob('page-*.json'))]
        with app.state.store.db() as db:
            bid='logical-synthetic-batch'
            db.execute("INSERT INTO recording_batches(id,session_id,capture_id,mode,state,revision,capture_origin_frame,capture_end_frame,eos,created) VALUES(?,?,?,'sealed_pages','queued',1,0,?,1,0)",
                       (bid,sid,'extended',state['emitted_frames']))
            position=0
            for page in pages:
                start=(page['number']-1)*u.PAGE_FRAMES;end=page['seal']['end_frame'];pid=page['server_id']
                db.execute("INSERT INTO recording_pages(id,batch_id,page_number,revision,state,released,start_frame,end_frame,eos,created) VALUES(?,?,?,?,'published',1,?,?,?,0)",
                    (pid,bid,page['number'],page['seal']['expected_revision'],start,end,int(page['seal']['eos'])))
                for piece in page['pieces']:
                    db.execute('INSERT INTO recording_sources(id,batch_id,page_id,idempotency_key,digest,path,frames,bytes,source_start_frame,format,created) VALUES(?,?,?,?,?,?,?,?,?,?,0)',
                        (piece['server_id'],bid,pid,piece['key'],piece['digest'],'/synthetic/'+piece['server_id'],piece['frames'],piece['bytes'],piece['capture_start'],'wav-pcm16le-16000-mono'))
                for frame in range(start,end,168000):
                    length=min(168000,end-frame);cid='logical-work-'+str(position)
                    db.execute("INSERT INTO chunks(rowid,id,session_id,idempotency_key,digest,path,duration,timeline_start,status,next_offset,created,error) VALUES(?,?,?,?,?,?,?,?,'completed',?,0,NULL)",
                        (10000+position*7,cid,sid,cid,'c'*64,'/synthetic/'+cid,length/u.RATE,frame/u.RATE,length/u.RATE))
                    db.execute("INSERT INTO recording_work(chunk_id,batch_id,page_id,position,frames,bytes,capture_start_frame,native_receipt_status,receipt_state) VALUES(?,?,?,?,?,?,?,'disabled','gap')",
                        (cid,bid,pid,position,length,length*2+44,frame));position+=1
        routes=Routes(client)
        # Ordinary export must refuse protected logical inputs before writing.
        from audio_backend import recorder_incremental_span_export as exporter
        before=files(tmp_path)
        for forbidden in (paths[2]/'bad-export',root/'bad-export',Path(c['inbox_dir'])/'bad-export',tmp_path/'next-out'):
            with pytest.raises(runner.Refused):
                exporter.run(job_dir=job,api=c['api'],session_id=sid,capture_id='extended',
                    exclusive_writer=True,limits=u.Limits(disk_floor=0),transport=routes,output_dir=forbidden)
            assert not forbidden.exists()
        assert files(tmp_path)==before
        output=tmp_path/'real-export'
        result=recovery.run(job_dir=job,api=c['api'],session_id=sid,capture_id='extended',
            exclusive_writer=True,limits=u.Limits(disk_floor=0),transport=routes,output_dir=output)
        manifest=result['export']
        assert manifest['frames']==8 and manifest['closure_kind']=='logical_analysis_boundary'
        assert manifest['recording_complete'] is False and manifest['recognition_continuity_verified'] is False
        data=json.loads((output/'attempt-01/export.json').read_bytes())
        assert data['completion']['span']['original_close_reason']=='ROTATION'
        assert data['completion']['logical_boundary']['successor']['delivery']['frame_start']==1008
        assert 'LOGICAL ANALYSIS BOUNDARY' in (output/'attempt-01/transcript.txt').read_text()
        assert all(method=='GET' for method,_ in routes.calls)


@pytest.mark.parametrize('where',['successor','runner','inbox','next_state','next_job','next_output'])
def test_recovery_refuses_protected_logical_trees_before_mutation_or_http(tmp_path,where):
    from audio_backend import recorder_export_recovery as recovery
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args);invoke(root,peer,finalize=True)
    target={'successor':paths[2]/'bad-export','runner':root/'bad-export',
        'inbox':Path(c['inbox_dir'])/'bad-export','next_state':args['next_state_dir'],
        'next_job':args['next_job_dir'],'next_output':args['next_output_dir']}[where]
    before=files(tmp_path)
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('protected recovery output must fail before HTTP')
    with pytest.raises(runner.Refused):
        recovery.run(job_dir=c['job_dir'],api=c['api'],session_id=c['session_id'],capture_id=c['capture_id'],
            exclusive_writer=True,limits=u.Limits(disk_floor=0),transport=NoHTTP(),output_dir=target)
    assert not target.exists() and files(tmp_path)==before


@pytest.mark.parametrize('consumer',['ordinary','recovery'])
@pytest.mark.parametrize('where',['successor','runner','inbox','next_state','next_job','next_output'])
@pytest.mark.parametrize('relation',['root','descendant','ancestor'])
def test_all_protected_destinations_refuse_before_http_and_no_output(tmp_path,consumer,where,relation):
    from audio_backend import recorder_incremental_span_export as exporter
    from audio_backend import recorder_export_recovery as recovery
    paths,c,root,peer,args=setup(tmp_path);logical.prepare(**args);invoke(root,peer,finalize=True)
    base={'successor':paths[2],'runner':root,'inbox':Path(c['inbox_dir']),
        'next_state':args['next_state_dir'],'next_job':args['next_job_dir'],
        'next_output':args['next_output_dir']}[where]
    target=base if relation=='root' else base/'bad-export' if relation=='descendant' else base.parent
    before=files(tmp_path);names={str(p) for p in tmp_path.rglob('*')}
    class NoHTTP:
        def request(self,*a,**kw):pytest.fail('protected output must fail before HTTP')
    with pytest.raises(Exception):
        (exporter if consumer=='ordinary' else recovery).run(job_dir=c['job_dir'],api=c['api'],
            session_id=c['session_id'],capture_id=c['capture_id'],exclusive_writer=True,
            limits=u.Limits(disk_floor=0),transport=NoHTTP(),output_dir=target)
    assert names=={str(p) for p in tmp_path.rglob('*')} and files(tmp_path)==before
