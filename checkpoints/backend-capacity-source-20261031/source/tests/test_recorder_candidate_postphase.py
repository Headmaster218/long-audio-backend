"""Finite postphase receipts/accounting fixtures; no model or native inference."""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit, parse_qs
import pytest

from audio_backend import recorder_candidate_postphase as post
from audio_backend import recorder_overlap_candidates as candidate
from audio_backend import recorder_incremental_span_export as export
from audio_backend import moss_handy_controller as controller
from audio_backend.moss_handy_runner import write_owned
from audio_backend.moss_handy_profile import canonical
from test_moss_handy_controller import FakeDriver, clean
from test_recorder_incremental_span_status import fixture
from test_recorder_native_overlap import native_shaped_fixture_ids, evidence


def policy(tmp_path, **limits):
    return dict(schema=post.SCHEMA,enabled=True,settings=dict(python=sys.executable,model='/fixed.fixture',
        ledger_dir=str(tmp_path/'ledger'),max_calls=64,max_wall_seconds=86400,max_output_bytes=1024*post.MiB,**limits),
        profile_name=post.handy.NAME,profile_sha256=post.handy.profile(post.handy.NAME).sha256,
        runtime_manifest_sha256=post.handy.RUNTIME_SHA256,model_sha256=post.handy.MODEL_SHA256,
        model_bytes=post.handy.MODEL_BYTES)


@contextmanager
def source(tmp_path, empty=False):
    with fixture(tmp_path,count=1,frames=32000) as (args,run,routes,app,*_):
        native_shaped_fixture_ids(app)
        class Native:
            def request(self,method,path,**kw):
                if '/native-turn-evidence?' not in path:return routes.request(method,path,**kw)
                q=parse_qs(urlsplit(path).query);parts=urlsplit(path).path.split('/')
                return evidence(parts[2],parts[4],int(q['frames'][0]),sha=q['source_sha256'][0],
                    turns=[] if empty else [(0,24000,'e:speaker_1'),(8000,32000,'e:speaker_2')])
        export.run(**dict(run,transport=Native()),output_dir=tmp_path/'export',native_turns=True)
        yield args['job_dir'],tmp_path/'export'


class DiskDriver(FakeDriver):
    def wait(self,cancel):
        result,raw=super().wait(cancel)
        if raw is not None:write_owned(self.directory,'raw.bin',raw)
        write_owned(self.directory,'result.json',canonical(result))
        return result,raw


def fake_supervisor(calls, mutate=lambda entry:None):
    def run(argv,*,output_dir,**kw):
        calls.append(list(argv))
        get=lambda name:argv[argv.index('--'+name)+1]
        def factory(directory,**opts):return controller.MossController(directory,**opts,_driver_factory=DiskDriver)
        candidate.run(job_dir=get('job-dir'),export_dir=get('export-dir'),model=get('model'),
            output_dir=get('output-dir'),selected_ranges=None,auto_native_overlap=True,
            exclusive_inputs=True,aggregate_budget_managed=True,_controller=factory,
            expected_plan_sha256=get('expected-plan-sha256'))
        for name in ('supervisor.stdout.txt','supervisor.stderr.txt'):
            path=output_dir/name;path.write_bytes(b'');path.chmod(0o600)
        mutate(output_dir)
        return dict(status='completed',exit_code=0,cleanup=clean(),resource_proof={'synthetic':True})
    return run


def setup(tmp_path, monkeypatch):
    chosen=policy(tmp_path);intent=dict(schema='fixture',candidate_policy=chosen)
    post.initialize_ledger(chosen,intent,resumed=False)
    monkeypatch.setattr(post,'verify_policy',lambda chosen:None)
    return chosen,intent


def drain(chosen,intent,generations,calls,**kwargs):
    return post.drain(policy=chosen,plan_intent=intent,generations=generations,api_origin='http://127.0.0.1:1234',
        _preflight=lambda *a,**k:object(),_supervise=fake_supervisor(calls),**kwargs)


def test_exact_completed_receipt_reused_no_child_no_budget_reset(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch);calls=[]
    with source(tmp_path) as pair:
        result=drain(chosen,intent,[pair],calls)
        assert result['status']=='candidate_drain_complete',result
        assert len(calls)==1 and result['reserved']['calls']==1
        saved={str(p):p.read_bytes() for p in (tmp_path/'ledger').rglob('*') if p.is_file()}
        repeated=drain(chosen,intent,[pair],calls)
        assert repeated['status']=='candidate_drain_complete',repeated
        assert repeated['reserved']==result['reserved'] and repeated['generations'][0]['reused']
        assert len(calls)==1
        assert saved=={str(p):p.read_bytes() for p in (tmp_path/'ledger').rglob('*') if p.is_file()}
        assert calls[0][1:5]==['-I','-B','-S','-c']


@pytest.mark.parametrize('damage',['raw','status','native','selection','manifest','extra','cleanup'])
def test_completed_corruption_stops_without_replay(tmp_path,monkeypatch,damage):
    chosen,intent=setup(tmp_path,monkeypatch);calls=[]
    with source(tmp_path) as pair:
        assert drain(chosen,intent,[pair],calls)['status']=='candidate_drain_complete'
        root=tmp_path/'ledger';entry=next(p for p in root.iterdir() if p.is_dir())
        if damage=='raw':(entry/'result/call-00.raw.txt').write_bytes(b'changed')
        elif damage=='status':(entry/'result/call-00.status.json').unlink()
        elif damage=='native':(entry/'result/owned-00/result.json').write_bytes(b'{}')
        elif damage=='selection':(entry/'result/selection.json').write_bytes(b'{}')
        elif damage=='manifest':(root/'generation-00.complete.json').write_bytes(b'{}')
        elif damage=='cleanup':(entry/'supervisor.json').write_bytes(b'{}')
        else:
            p=entry/'unknown';p.write_bytes(b'');p.chmod(0o600)
        result=drain(chosen,intent,[pair],calls)
        assert result['status']=='candidate_review_required',result
        assert len(calls)==1


def test_missing_terminal_manifest_is_unknown_even_complete_cli_output(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch);calls=[]
    with source(tmp_path) as pair:
        assert drain(chosen,intent,[pair],calls)['status']=='candidate_drain_complete'
        (tmp_path/'ledger/generation-00.complete.json').unlink()
        result=drain(chosen,intent,[pair],calls)
        assert result['status']=='candidate_unknown' and result['reserved']['calls']==1 and len(calls)==1


def test_partial_raw_missing_is_unknown_not_known_failure(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch);calls=[]
    with source(tmp_path) as pair:
        result=post.drain(policy=chosen,plan_intent=intent,generations=[pair],api_origin='http://127.0.0.1:1234',
            _preflight=lambda *a,**k:object(),_supervise=fake_supervisor(calls,lambda e:(e/'result/call-00.raw.txt').unlink()))
        assert result['status']=='candidate_unknown',result
        assert not (tmp_path/'ledger/generation-00.failure.json').exists()
        assert drain(chosen,intent,[pair],calls)['status']=='candidate_unknown' and len(calls)==1


def test_no_native_overlap_explicit_no_call_and_no_resource_launch(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch)
    def forbidden(*a,**k):raise AssertionError('no launch expected')
    with source(tmp_path,empty=True) as pair:
        result=post.drain(policy=chosen,plan_intent=intent,generations=[pair],api_origin='remote-unidentifiable',
            _preflight=forbidden,_supervise=forbidden)
        assert result['status']=='candidate_drain_complete' and result['model_call_count']==0,result
        completion=json.loads((tmp_path/'ledger/generation-00.complete.json').read_bytes())
        assert completion['status']=='no_call' and completion['no_call_reason']=='no_detected_native_overlap'
        assert result['complete_content'] is False and result['accuracy_verified'] is False


def test_budget_insufficient_no_attempt_and_no_resource_probe(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch);chosen['settings']['max_calls']=0
    # A new pinned fixture ledger for this policy, not a mutation of persisted settings.
    intent['candidate_policy']=chosen
    owner=tmp_path/'ledger/owner.json';owner.write_bytes(post.u._json(dict(schema=post.SCHEMA,
        plan_intent_sha256=post.fingerprint(intent),policy_sha256=post.fingerprint(chosen),replay_allowed=False)))
    calls=[]
    with source(tmp_path) as pair:
        result=drain(chosen,intent,[pair],calls)
        assert result['status']=='candidate_budget_exhausted' and not calls
        assert not list((tmp_path/'ledger').glob('generation-*'))


def test_resource_block_has_no_reservation(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch)
    def blocked(*a,**k):raise RuntimeError('synthetic unavailable proof')
    with source(tmp_path) as pair:
        result=post.drain(policy=chosen,plan_intent=intent,generations=[pair],api_origin='http://remote:1',_preflight=blocked)
        assert result['status']=='candidate_resource_blocked' and result['reserved']['calls']==0
        assert not list((tmp_path/'ledger').glob('generation-*'))


def test_orphan_or_missing_ledger_never_adopt_or_recreate(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch)
    with pytest.raises(Exception,match='existing_no_adoption'):post.initialize_ledger(chosen,intent,resumed=False)
    (tmp_path/'ledger').rename(tmp_path/'preserved-ledger')
    with pytest.raises(Exception,match='missing_no_recreate'):post.initialize_ledger(chosen,intent,resumed=True)
    assert not (tmp_path/'ledger').exists()


def test_unknown_reservation_survives_rerun_and_prevents_new_directory(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch);calls=[]
    with source(tmp_path) as pair:
        def crash(*a,**k):calls.append(1);raise RuntimeError('lost receipt')
        result=post.drain(policy=chosen,plan_intent=intent,generations=[pair],api_origin='http://127.0.0.1:1',
            _preflight=lambda *a,**k:object(),_supervise=crash)
        assert result['status']=='candidate_unknown' and result['reserved']['calls']==1
        assert drain(chosen,intent,[pair],calls)['status']=='candidate_unknown' and len(calls)==1


def test_exact_zero_selection_reserves_zero_calls_and_never_launches(tmp_path,monkeypatch):
    import test_recorder_incremental_span_status as fixtures
    from test_recorder_overlap_candidates import zero_guard_delivery
    monkeypatch.setattr(fixtures,'large_delivery',lambda path,frames:zero_guard_delivery(path,frames,b'\0\0'*frames))
    chosen,intent=setup(tmp_path,monkeypatch);calls=[]
    with source(tmp_path) as pair:
        result=drain(chosen,intent,[pair],calls)
        assert result['status']=='candidate_drain_complete' and result['model_call_count']==0 and calls==[]
        complete=json.loads((tmp_path/'ledger/generation-00.complete.json').read_bytes())
        assert complete['no_call_reason']=='all_selected_pcm_exact_zero'
        assert complete['status']=='no_call' and result['reserved']['calls']==result['reserved']['wall_seconds']==0


def test_arbitrary_preexisting_hashed_output_is_not_adopted_or_escaped(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch);calls=[]
    (tmp_path/'ledger'/('generation-00-'+'a'*64)).mkdir(mode=0o700)
    with source(tmp_path) as pair:
        result=drain(chosen,intent,[pair],calls)
        assert result['status']=='candidate_review_required' and not calls
        assert not list((tmp_path/'ledger').glob('*.intent.json'))


def test_global_call_budget_survives_completed_first_generation_and_rerun(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch);chosen['settings']['max_calls']=1
    root=tmp_path/'ledger';(root/'owner.json').write_bytes(post.u._json(dict(schema=post.SCHEMA,
        plan_intent_sha256=post.fingerprint(intent),policy_sha256=post.fingerprint(chosen),replay_allowed=False)))
    other=tmp_path/'other';other.mkdir(mode=0o700);calls=[]
    with source(tmp_path) as first:pass
    with source(other) as second:
        for _ in range(2):
            result=drain(chosen,intent,[first,second],calls)
            assert result['status']=='candidate_budget_exhausted' and result['reserved']['calls']==1,result
            assert len(calls)==1 and not (root/'generation-01.intent.json').exists()


def test_truncated_native_receipt_is_known_failure_never_replayed(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch);calls=[]
    original=DiskDriver.__init__
    def setup_driver(self,*a,**k):
        original(self,*a,**k);self.modify=lambda value:value.update(truncated=True)
    monkeypatch.setattr(DiskDriver,'__init__',setup_driver)
    with source(tmp_path) as pair:
        result=drain(chosen,intent,[pair],calls)
        assert result['status']=='candidate_failed',result
        assert (tmp_path/'ledger/generation-00.failure.json').exists()
        assert drain(chosen,intent,[pair],calls)['status']=='candidate_failed' and len(calls)==1


def test_missing_completed_tail_keeps_monotonic_reservation_no_replay(tmp_path,monkeypatch):
    import shutil
    chosen,intent=setup(tmp_path,monkeypatch);calls=[]
    with source(tmp_path) as pair:
        assert drain(chosen,intent,[pair],calls)['status']=='candidate_drain_complete'
        root=tmp_path/'ledger'
        for path in root.glob('generation-*'):
            if path.is_dir():shutil.rmtree(path)
            else:path.unlink()
        result=drain(chosen,intent,[pair],calls)
        assert result['status']=='candidate_unknown' and result['reserved']['calls']==1 and len(calls)==1


def test_parse_failure_is_terminal_failed_diagnostic_not_completed(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch);calls=[]
    original=DiskDriver.__init__
    def setup_driver(self,*a,**k):original(self,*a,**k);self.raw=b'[bad][S1]unfinished'
    monkeypatch.setattr(DiskDriver,'__init__',setup_driver)
    with source(tmp_path) as pair:
        result=drain(chosen,intent,[pair],calls)
        assert result['status']=='candidate_failed',result
        assert not (tmp_path/'ledger/generation-00.complete.json').exists()
        assert drain(chosen,intent,[pair],calls)['status']=='candidate_failed' and len(calls)==1


def test_cleanup_retains_original_owner_without_new_jobs_and_restores_signals():
    import signal
    before={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM)}
    class Pending:
        attempts=0
        def pending_cleanup(self):return self.attempts<3
        def retry_pending_cleanup(self):
            self.attempts+=1
            if self.attempts==1:raise PermissionError('temporary fixture visibility denial')
            return self.attempts==3
    pending=Pending();sleeps=[]
    assert post.retain_cleanup(_module=pending,_sleep=lambda value:sleeps.append(value))
    assert pending.attempts==3 and sleeps==[5,5]
    assert before=={s:signal.getsignal(s) for s in before}


def test_installed_layout_code_binding_uses_packaged_manifest(tmp_path,monkeypatch):
    import shutil
    from audio_backend import moss_td_cpp_assets as assets
    installed=tmp_path/'site-packages';installed.mkdir()
    shutil.copytree(post.ROOT/'audio_backend',installed/'audio_backend',ignore=shutil.ignore_patterns('__pycache__'))
    monkeypatch.setattr(post,'ROOT',installed)
    monkeypatch.setattr(assets,'MANIFEST_PATH',installed/'audio_backend/profiles/moss-td-cpp-model-manifest.json')
    assert not (installed/'moss-td-cpp-model-manifest.json').exists()
    first=post.code_binding()
    assert first==post.code_binding() and len(first)==64


def test_pending_cleanup_notice_and_retry_keep_ledger_lock(tmp_path,monkeypatch,capsys):
    lock=tmp_path/'held.lock';lock.write_bytes(b'');lock.chmod(0o600)
    monkeypatch.setattr(post,'pending_cleanup',lambda:True)
    seen=[]
    def retained():
        with pytest.raises(Exception):post.runner.engine.held_lock(lock)
        seen.append('lock-retained');return True
    monkeypatch.setattr(post,'retain_cleanup',retained)
    with post._held(lock):pass
    assert seen==['lock-retained']
    assert 'candidate_cleanup_incomplete' in capsys.readouterr().err
    fd=post.runner.engine.held_lock(lock);post.os.close(fd)


def test_nested_owned_runner_ignores_poisoned_product_bytecode(tmp_path):
    import shutil,importlib.util,marshal,struct,subprocess
    package=tmp_path/'installed/audio_backend';package.parent.mkdir()
    shutil.copytree(post.ROOT/'audio_backend',package,ignore=shutil.ignore_patterns('__pycache__'))
    source=package/'moss_handy_adapter.py';cache=Path(importlib.util.cache_from_source(str(source)))
    cache.parent.mkdir();marker=tmp_path/'poisoned-import-ran'
    payload=compile('open('+repr(str(marker))+',"w").write("executed")\nraise SystemExit(93)',str(source),'exec')
    info=source.stat();cache.write_bytes(importlib.util.MAGIC_NUMBER+struct.pack('<III',0,int(info.st_mtime),info.st_size)+marshal.dumps(payload))
    owned=tmp_path/'owned';owned.mkdir(mode=0o700)
    process=subprocess.run([sys.executable,'-I','-B','-S',str(package/'moss_handy_runner.py'),str(owned),'0'*64],
        input=b'1',capture_output=True,timeout=10)
    assert process.returncode!=0 and process.returncode!=93
    assert not marker.exists() and not (owned/'unused-bytecode').exists()


def test_child_rejects_changed_selection_before_output_or_any_model_call(tmp_path,monkeypatch):
    chosen,intent=setup(tmp_path,monkeypatch);calls=[];constructions=[]
    original=candidate.prepare;prepares=[];driver=DiskDriver.__init__
    def prepared(*args,**kwargs):
        result=list(original(*args,**kwargs));prepares.append(1)
        if len(prepares)==2:
            # Simulate a changed source/selection between the parent's released
            # read locks and child's independently locked preflight: one -> two.
            result[-1]=copy.deepcopy(result[-1]);result[-1]['ranges']=[
                dict(start_frame=0,end_frame=16000),dict(start_frame=16000,end_frame=32000)]
        return result
    def construct(*args,**kwargs):constructions.append(1);return driver(*args,**kwargs)
    monkeypatch.setattr(candidate,'prepare',prepared)
    monkeypatch.setattr(DiskDriver,'__init__',construct)
    with source(tmp_path) as pair:
        answer=drain(chosen,intent,[pair],calls)
        assert answer['status']=='candidate_unknown',answer
        assert answer['reserved']['calls']==1 and not constructions
        entry=next(p for p in (tmp_path/'ledger').iterdir() if p.is_dir())
        assert not (entry/'result').exists()
        assert drain(chosen,intent,[pair],calls)['status']=='candidate_unknown' and len(calls)==1


def test_broken_cleanup_notice_cannot_release_owned_ledger_lock(tmp_path,monkeypatch):
    lock=tmp_path/'held.lock';lock.write_bytes(b'');lock.chmod(0o600)
    class Broken:
        def write(self,*a):raise BrokenPipeError('closed fixture stderr')
        def flush(self):raise OSError('closed fixture stderr')
    monkeypatch.setattr(post.sys,'stderr',Broken())
    monkeypatch.setattr(post,'pending_cleanup',lambda:True)
    retained=[]
    def cleanup():
        with pytest.raises(Exception):post.runner.engine.held_lock(lock)
        retained.append(True);return True
    monkeypatch.setattr(post,'retain_cleanup',cleanup)
    with post._held(lock):pass
    assert retained==[True]
    fd=post.runner.engine.held_lock(lock);post.os.close(fd)
