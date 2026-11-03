"""Real ASGI GET routes, disposable DB revision races, synthetic runner stage.

Not a historical-run resume, HTTP listener, native replay or accuracy test.
Requires explicit retained-evidence root; original files are read-only and pinned.
"""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

SOURCE=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(SOURCE))
sys.path.insert(0,str(SOURCE/'qualification/snapshot_export_recovery_20261103'))
import snapshot_http as host
from audio_backend import recorder_inbox_runner as runner
from audio_backend import recording_uploader as u


def inventory(root):
    return {str(p.relative_to(root)):{'bytes':p.stat().st_size,'mtime':p.stat().st_mtime_ns,
        'ctime':p.stat().st_ctime_ns,'sha256':host.digest(p)} for p in root.rglob('*') if p.is_file()}


def save(path,value):
    runner.single.manifest_write(path,value)


def setup_case(tmp_path):
    evidence=os.environ.get('AUDIO_RECOVERY_EVIDENCE_ROOT')
    if not evidence:pytest.skip('Set AUDIO_RECOVERY_EVIDENCE_ROOT for retained-evidence route tests')
    evidence=Path(evidence).resolve(strict=True);base=evidence/'chain-validation-20261103'
    original=base/'combined-run02/run'
    protected_roots=[original,base/'single-speech-verified']
    before={str(root):inventory(root) for root in protected_roots}
    snapshot=tmp_path/'jobs.sqlite3'
    snapshot.write_bytes((base/'failed-run02-audit/jobs.sqlite3').read_bytes())
    manifest=tmp_path/'source.json'
    rows=[dict(path=str(p.relative_to(SOURCE)),bytes=p.stat().st_size,sha256=host.digest(p))
        for p in sorted(SOURCE.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')]
    manifest.write_text(json.dumps({'files':rows}))
    cfg=original/'config.json';job=original/'intake';target=json.loads((job/'capture.json').read_bytes())['target']
    spec=dict(source_root=str(SOURCE),source_manifest=str(manifest),source_manifest_sha256=host.digest(manifest),
        snapshot=str(snapshot),snapshot_sha256=host.digest(snapshot),original_database=str(original/'data/jobs.sqlite3'),
        original_config=json.loads(cfg.read_bytes()),original_config_path=str(cfg),original_config_sha256=host.digest(cfg),
        api=target['origin'],session_id=target['session_id'],capture_id=target['capture_id'],job_dir=str(job),
        job_header_sha256=host.digest(job/'capture.json'),job_journal_sha256=host.digest(job/'uploader/uploader.json'))
    # Explicit synthetic already-completed stage, not adoption of old export state.
    state=tmp_path/'runner';state.mkdir(mode=0o700)
    config=json.loads((original/'runner/config.json').read_bytes());config['output_dir']=str(tmp_path/'export')
    intent=json.loads((original/'runner/input-000.intent.json').read_bytes());intent['config_sha256']=runner._hash(config)
    final=dict(schema=runner.SCHEMA,config_sha256=runner._hash(config),input_intents_sha256=runner._hash([intent]),kind='finalize')
    for name,value in [('config',config),('input-000.intent',intent),('input-000.done',runner._done(intent)),
                       ('finalize.intent',final),('finalize.done',runner._done(final))]:save(state/(name+'.json'),value)
    (state/'runner.lock').touch(mode=0o600)
    assert runner._ledger(state,config)==([intent],1)
    runner._job_prefix(config,[intent],1)
    return spec,state,original,before


class RouteTransport:
    """Only convert real ASGI responses to the product transport contract."""
    def __init__(self,client,spec,writer,changes,output,attempts):
        self.client,self.spec,self.writer,self.changes=client,spec,writer,changes
        self.rows=[];self.mutations=0;self.first_revisions=[]
        self.output,self.attempts,self.failed_partials=output,attempts,{}
    def request(self,method,path,**kwargs):
        assert method=='GET' and not kwargs
        if self.attempts and self.mutations:
            folder=self.output/('attempt-%02d' % self.mutations)
            if str(folder) not in self.failed_partials:
                self.failed_partials[str(folder)]=inventory(folder)
        # All previous route connections have closed before this bounded write.
        # This deliberately bypasses the read-only connection wrapper only for
        # the disposable test copy, never for the original database or source.
        if '/transcript?cursor=' in path and self.mutations<self.changes:
            db=self.writer(self.spec['snapshot'])
            try:
                assert db.execute('UPDATE sessions SET transcript_revision=transcript_revision+1 WHERE id=?',
                    (self.spec['session_id'],)).rowcount==1
                db.commit()
            finally:db.close()
            self.mutations+=1
        response=self.client.request(method,path)
        self.rows.append((method,path,response.status_code,response.json()))
        if '/transcript?block=' in path and response.status_code==200:self.first_revisions.append(response.json()['revision'])
        if not 200<=response.status_code<300:raise u.HTTPFailure(response.status_code,response.text)
        return response.json()


def invoke(state,transport,attempts):
    return runner.watch(state_dir=state,exclusive_writer=True,acknowledge_span_local_closure=True,
        export=True,export_recovery_attempts=attempts,transport=transport,
        max_actions=2,max_polls=1,poll_seconds=.1,
        limits=u.Limits(disk_floor=0,deadline_seconds=60),deadline_seconds=60)


@pytest.mark.parametrize('attempts,changes,outcome',[(0,1,'default_refusal'),(2,1,'recovered'),(2,2,'exhausted')])
def test_real_revision_race(tmp_path,attempts,changes,outcome):
    from fastapi.testclient import TestClient
    spec,state,original,before=setup_case(tmp_path)
    writer=sqlite3.connect
    guard=host.ProtectedPaths([*(Path(root) for root in before),SOURCE]).install()
    app=None
    try:
        app=host.build_app(spec)
        with TestClient(app) as client:
            transport=RouteTransport(client,spec,writer,changes,tmp_path/'export',attempts)
            if outcome=='recovered':
                result=invoke(state,transport,attempts)
                assert result['status']=='exported' and result['export_completed'] is True
                first=tmp_path/'export/attempt-01';second=tmp_path/'export/attempt-02'
                assert (first/'export.json.partial').is_file() and not (first/'complete.json').exists()
                preserved=inventory(first)
                complete=json.loads((second/'complete.json').read_bytes())
                assert complete['frames']==144000 and complete['units']==complete['nonempty_text_units']==5
                calls=len(transport.rows)
                assert invoke(state,transport,attempts)['status']=='exported'
                assert len(transport.rows)==calls and inventory(first)==preserved
                assert len(transport.first_revisions)==2 and len(set(transport.first_revisions))==2
                assert (state/'export.done.json').is_file()
            else:
                result=invoke(state,transport,attempts)
                assert result['status']=='unresolved' and result['preserve_inbox_state_job_and_output'] is True
                if outcome=='default_refusal':assert 'transport_stopped_no_retry' in result['reason']
                else:assert 'attempts_exhausted' in str(result)
                folder=tmp_path/'export' if attempts==0 else tmp_path/'export/attempt-01'
                assert (folder/'export.json.partial').is_file() and not (folder/'complete.json').exists()
                assert not (state/'export.done.json').exists()
                if attempts:
                    assert (tmp_path/'export/attempt-02/export.json.partial').is_file()
                    assert not (tmp_path/'export/attempt-03').exists()
            conflicts=[row for row in transport.rows if row[2]==409]
            assert len(conflicts)==changes
            assert all(row[3]=={'detail':'transcript_revision_changed'} for row in conflicts)
            assert all(row[0]=='GET' and row[2] in (200,409) for row in transport.rows)
            assert transport.mutations==changes
            assert len(transport.failed_partials)==(1 if attempts else 0)
            assert all(inventory(Path(folder))==saved for folder,saved in transport.failed_partials.items())
        assert app.state.response_budget._lease_count==0
        assert guard.denied==0
    finally:
        if app is not None:app.state.sqlite_guard.close()
        guard.close()
        assert {root:inventory(Path(root)) for root in before}==before
        host.verify_source(SOURCE,spec['source_manifest'],spec['source_manifest_sha256'])
