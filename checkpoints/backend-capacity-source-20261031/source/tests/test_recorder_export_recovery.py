"""Model-free races through real ASGI transcript/status projections."""
from dataclasses import replace
import json
import pytest
from audio_backend import recorder_export_recovery as recovery
from audio_backend import recorder_incremental_span_export as export
from audio_backend import recording_uploader as u
from test_recorder_incremental_span_status import fixture
from test_recorder_incremental_span_export import seed, verify_files
from test_recorder_span_status import local_files


def test_revision_race_restarts_all_checks_preserves_old_and_partial(tmp_path):
    with fixture(tmp_path, count=1, frames=10*u.RATE) as (args, run, routes, app, state):
        seed(app); old = tmp_path/'old'; old.mkdir(); (old/'partial').write_bytes(b'old evidence')
        before = local_files(args); seen = 0
        def change(path, value):
            nonlocal seen
            if '/transcript?' in path:
                seen += 1
                if seen == 1:
                    with app.state.store.db() as db:
                        db.execute("UPDATE segments SET text='new primary',raw_text='new primary'")
        routes.edit = change
        result = recovery.run(**run, output_dir=tmp_path/'recovery')
        assert result['attempts'] == 2 and result['status'] == 'exported'
        assert not (tmp_path/'recovery/attempt-01/complete.json').exists()
        assert (tmp_path/'recovery/attempt-01/export.json.partial').exists()
        data = verify_files(tmp_path/'recovery/attempt-02', result['export'])
        assert data['units'][0]['text'] == 'new primary'
        assert before == local_files(args) and (old/'partial').read_bytes() == b'old evidence'
        assert all(method == 'GET' for method, _ in routes.calls)
        audit = [json.loads(line) for line in (tmp_path/'recovery/recovery.jsonl').read_text().splitlines()]
        assert [a['status'] for a in audit[1:]] == ['started','revision_changed','started','exported']


@pytest.mark.parametrize('detail', ['', 'transcript_revision_changed', '{"detail":"other"}',
    '{"detail":"transcript_revision_changed","extra":true}',
    '{"detail":"other","detail":"transcript_revision_changed"}',
    '{"detail":"transcript_revision_changed"} trailing'])
def test_unknown_conflict_never_retries(tmp_path, detail):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        real = routes.request
        def request(method, path, **kwargs):
            if '/transcript?cursor=' in path: raise u.HTTPFailure(409, detail)
            return real(method, path, **kwargs)
        routes.request = request
        with pytest.raises(u.HTTPFailure): recovery.run(**run, output_dir=tmp_path/'recovery')
        assert not (tmp_path/'recovery/attempt-02').exists()
        assert not list((tmp_path/'recovery').rglob('complete.json'))


def test_repeated_revision_races_stop_at_bound(tmp_path):
    with fixture(tmp_path, count=1, frames=10*u.RATE) as (args, run, routes, app, state):
        seed(app)
        def change(path, value):
            if '/transcript?block=' in path:
                with app.state.store.db() as db: db.execute("UPDATE segments SET text=text || 'x'")
        routes.edit = change
        with pytest.raises(export.status.Refused, match='attempts_exhausted'):
            recovery.run(**run, output_dir=tmp_path/'recovery')
        assert len(list((tmp_path/'recovery').glob('attempt-*'))) == 2
        assert not list((tmp_path/'recovery').rglob('complete.json'))


@pytest.mark.parametrize('kind', ['requests','network_bytes'])
def test_aggregate_budget_refuses_before_next_request(tmp_path, kind):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        bounds = replace(recovery.RecoveryLimits(), **{kind:1})
        with pytest.raises(export.status.Refused, match='bound'):
            recovery.run(**run, output_dir=tmp_path/'recovery', recovery_limits=bounds)
        assert len(routes.calls) <= 1
        assert not (tmp_path/'recovery/attempt-02').exists()


def test_existing_output_is_never_changed(tmp_path):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        path=tmp_path/'old'; path.mkdir(); (path/'evidence').write_bytes(b'keep')
        with pytest.raises(export.status.Refused, match='output_exists'):
            recovery.run(**run, output_dir=path)
        assert (path/'evidence').read_bytes() == b'keep' and not routes.calls


def test_status_regression_does_not_restart(tmp_path):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        def change(path, value):
            if '/transcript?' in path:
                with app.state.store.db() as db: db.execute("UPDATE chunks SET status='paused'")
        routes.edit=change
        with pytest.raises(export.status.Refused): recovery.run(**run, output_dir=tmp_path/'recovery')
        assert not (tmp_path/'recovery/attempt-02').exists()
        assert not list((tmp_path/'recovery').rglob('complete.json'))


def test_non_transcript_typed_conflict_is_not_retryable(tmp_path):
    with fixture(tmp_path, count=1) as (args, run, routes, app, state):
        def request(method,path,**kwargs): raise u.HTTPFailure(409,'{"detail":"transcript_revision_changed"}')
        routes.request=request
        with pytest.raises(u.HTTPFailure): recovery.run(**run,output_dir=tmp_path/'recovery')
        assert not (tmp_path/'recovery/attempt-02').exists()


def test_changed_text_under_reused_revision_is_not_retried(tmp_path):
    from audio_backend import transcript_contract as c
    with fixture(tmp_path, count=1, frames=10*u.RATE) as (args, run, routes, app, state):
        seed(app); seen=0
        def change(path,page):
            nonlocal seen
            if '/transcript?' not in path: return
            seen += 1
            if seen == 2:
                row=dict(page['baseline_reading'][0]['canonical_asr'],text='corrupt',raw_text='corrupt')
                page['baseline_reading']=[dict(c.primary_evidence(run['session_id'],row),reference_only=False)]
        routes.edit=change
        with pytest.raises(export.status.Refused,match='transcript_changed'):
            recovery.run(**run,output_dir=tmp_path/'recovery')
        assert not (tmp_path/'recovery/attempt-02').exists()
        assert not list((tmp_path/'recovery').rglob('complete.json'))


def test_mid_pagination_revision_race_starts_at_zero(tmp_path):
    with fixture(tmp_path,count=1,frames=65*u.RATE) as (args,run,routes,app,state):
        seed(app); changed=False
        def change(path,value):
            nonlocal changed
            if '/transcript?block=0' in path and not changed:
                changed=True
                with app.state.store.db() as db: db.execute("UPDATE segments SET text='fresh'")
        routes.edit=change
        result=recovery.run(**run,output_dir=tmp_path/'recovery')
        assert result['attempts']==2 and result['export']['transcript_pages']==2
        assert sum('/transcript?block=0' in path for _,path in routes.calls)==2
        assert not (tmp_path/'recovery/attempt-01/complete.json').exists()


def test_request_budget_does_not_reset_on_restart(tmp_path):
    with fixture(tmp_path,count=1,frames=10*u.RATE) as (args,run,routes,app,state):
        seed(app)
        def change(path,value):
            if '/transcript?block=' in path:
                with app.state.store.db() as db: db.execute("UPDATE segments SET text=text || 'x'")
        routes.edit=change
        real=routes.request; count=0
        def request(method,path,**kwargs):
            nonlocal count
            count+=1
            return real(method,path,**kwargs)
        routes.request=request
        # First determine the number of GETs before a real first-pass conflict.
        with pytest.raises(export.status.Refused,match='attempts_exhausted'):
            recovery.run(**run,output_dir=tmp_path/'single',recovery_limits=recovery.RecoveryLimits(attempts=1))
        cap=count; count=0
        with pytest.raises(export.status.Refused,match='request_bound'):
            recovery.run(**run,output_dir=tmp_path/'bounded',recovery_limits=recovery.RecoveryLimits(requests=cap))
        assert count==cap
        assert not list((tmp_path/'bounded').rglob('complete.json'))


def test_cancel_between_attempts_never_resets_guard(tmp_path):
    with fixture(tmp_path,count=1,frames=10*u.RATE) as (args,run,routes,app,state):
        seed(app); cancelled=False; real=routes.request
        def request(method,path,**kwargs):
            nonlocal cancelled
            if '/transcript?cursor=' in path:
                cancelled=True
                raise u.HTTPFailure(409,'{"detail":"transcript_revision_changed"}')
            return real(method,path,**kwargs)
        routes.request=request
        with pytest.raises(InterruptedError):
            recovery.run(**run,output_dir=tmp_path/'recovery',cancel=lambda:cancelled)
        assert not (tmp_path/'recovery/attempt-02').exists()


@pytest.mark.parametrize('detail', [
    '{"detail":{"reason":"transcript_revision_changed"}}',
    '[{"detail":"transcript_revision_changed"}]',
    '{"detail":"transcript_revision_changed","detail":"transcript_revision_changed"}',
    '{"detail":"transcript_revision_changed","x":{}}'])
def test_error_shape_is_exact(detail):
    assert u.HTTPFailure(409,detail).response_reason is None


@pytest.mark.parametrize('path', ['/sessions/other/transcript?cursor=x&projection=whole-primary-v1',
    '/sessions/s/transcript?cursor=x&projection=other',
    '/sessions/s/transcript?block=0&projection=whole-primary-v1'])
def test_wrong_cursor_route_refuses(path):
    class Peer:
        def request(self,*args): raise u.HTTPFailure(409,'{"detail":"transcript_revision_changed"}')
    guard=u.Guard(u.Limits(),None)
    http=recovery._GET(Peer(),guard,recovery.RecoveryLimits(),'s')
    with pytest.raises(u.HTTPFailure): http.request('GET',path)


def test_audit_failure_does_not_mask_original_refusal(tmp_path,monkeypatch):
    with fixture(tmp_path,count=1) as (args,run,routes,app,state):
        real=export._Output.write
        def write(self,name,raw):
            if b'"status":"refused"' in raw: raise OSError('audit disk failed')
            return real(self,name,raw)
        monkeypatch.setattr(export._Output,'write',write)
        def request(*args,**kwargs): raise u.HTTPFailure(503,'unavailable')
        routes.request=request
        with pytest.raises(u.HTTPFailure) as error:
            recovery.run(**run,output_dir=tmp_path/'recovery')
        assert error.value.status==503
        assert not list((tmp_path/'recovery').rglob('complete.json'))


def test_root_audit_fsync_failure_never_returns_success(tmp_path,monkeypatch):
    import os,stat
    with fixture(tmp_path,count=1) as (args,run,routes,app,state):
        path=tmp_path/'recovery'; real=os.fsync
        def fsync(fd):
            info=os.fstat(fd)
            if (path/'recovery.jsonl').exists() and stat.S_ISDIR(info.st_mode) and info.st_ino==path.stat().st_ino:
                raise OSError('audit directory fsync failed')
            return real(fd)
        monkeypatch.setattr(os,'fsync',fsync)
        with pytest.raises(OSError,match='audit directory'):
            recovery.run(**run,output_dir=path)
        # Child is independently committed, but root operation is uncertain.
        assert (path/'attempt-01/complete.json').exists()
        assert not (path/'attempt-02').exists()


@pytest.mark.parametrize('target', ['source','relative_job','snapshot'])
def test_protected_output_refused_before_any_write(tmp_path,target,monkeypatch):
    with fixture(tmp_path,count=1) as (args,run,routes,app,state):
        if target=='source':
            parent=args['deliveries'][0]; parent.chmod(0o700)
        elif target=='snapshot': parent=args['job_dir']/'admissions/0/snapshot'
        else:
            parent=args['job_dir']; monkeypatch.chdir(parent.parent)
            run['job_dir']=parent.name
        before=local_files(args); identity=u._signature(parent.stat())
        with pytest.raises(export.status.Refused,match='output_is_input'):
            recovery.run(**run,output_dir=parent/'forbidden')
        assert before==local_files(args) and identity==u._signature(parent.stat())
        assert not (parent/'forbidden').exists() and not routes.calls


@pytest.mark.parametrize('when', ['conflict','success'])
def test_coherent_same_path_local_replacement_is_never_published(tmp_path,when):
    import os
    with fixture(tmp_path,count=1,frames=10*u.RATE) as (args,run,routes,app,state):
        real=routes.request
        def request(method,path,**kwargs):
            if '/transcript?cursor=' in path:
                header=args['job_dir']/'capture.json'
                replacement=header.with_name('replacement')
                replacement.write_bytes(header.read_bytes()); replacement.chmod(0o600)
                os.replace(replacement,header)
                if when=='conflict':
                    raise u.HTTPFailure(409,'{"detail":"transcript_revision_changed"}')
            return real(method,path,**kwargs)
        routes.request=request
        with pytest.raises(export.status.Refused,match='local_identity_changed'):
            recovery.run(**run,output_dir=tmp_path/'recovery')
        assert not list((tmp_path/'recovery').rglob('complete.json'))
        assert not (tmp_path/'recovery/attempt-02').exists()
