"""Bounded runner unit/protocol tests. No native inference qualification."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from audio_backend import recorder_inbox_runner as runner
from audio_backend import recorder_intake as single
from audio_backend import recording_uploader as u
from test_recorder_inbox import delivery
from test_extended_recorder_intake import Peer


def example(tmp_path, count=3, terminal='STOP'):
    inbox = tmp_path/'inbox'; inbox.mkdir(mode=0o700)
    paths = [delivery(inbox, 23+i, epoch=7, start=1000+4*i, boundary='RESTART',
        run='runner-process', seam=i>0, close=terminal if i == count-1 else 'ROTATION') for i in range(count)]
    config = runner.config_from_delivery(first_delivery=paths[0],inbox_dir=inbox,
        job_dir=tmp_path/'job',output_dir=tmp_path/'export',api='http://127.0.0.1:8765',
        session_id='known',capture_id='extended')
    root=tmp_path/'runner'
    return paths,config,root


def invoke(root,peer,**kwargs):
    options=dict(state_dir=root,exclusive_writer=True,acknowledge_span_local_closure=True,
        limits=u.Limits(disk_floor=0,deadline_seconds=120,poll_seconds=.001),transport=peer)
    options.update(kwargs);return runner.run(**options)


def test_local_setup_freezes_selected_first_and_performs_no_network(tmp_path,monkeypatch):
    paths,config,root=example(tmp_path)
    monkeypatch.setattr(u.HTTPTransport,'request',lambda *a,**kw:pytest.fail('configuration HTTP'))
    result=runner.configure(state_dir=root,config=config,first_delivery=paths[0])
    assert result['network_requests']==0 and not Path(config['job_dir']).exists()
    saved=json.loads((root/'input-000.intent.json').read_bytes())
    assert saved['path']==str(paths[0]) and saved['delivery']['sequence']==23
    assert (root.stat().st_mode&0o777)==0o700
    assert all((p.stat().st_mode&0o777)==0o600 for p in root.iterdir())
    with pytest.raises(single.Refused):runner.configure(state_dir=root,config=config)


def test_nine_deliveries_bounded_actions_originals_preserved_and_explicit_eos(tmp_path):
    paths,config,root=example(tmp_path,9)
    before={str(p):p.read_bytes() for path in paths for p in path.iterdir()}
    runner.configure(state_dir=root,config=config,first_delivery=paths[0]);peer=Peer()
    result=invoke(root,peer,max_actions=4)
    assert result['inputs']==4 and result['status']=='action_limit' and not peer.eos
    assert invoke(root,peer,max_actions=5)['inputs']==9
    assert invoke(root,peer)['status']=='awaiting_finalize' and not peer.eos
    assert invoke(root,peer,finalize=True)['status']=='action_limit' and peer.eos
    assert before=={str(p):p.read_bytes() for path in paths for p in path.iterdir()}
    assert (root/'finalize.done.json').is_file()


def test_transport_nonce_duplicate_requires_explicit_first_choice_then_stays_frozen(tmp_path):
    paths,config,root=example(tmp_path,1)
    other=paths[0].with_name(paths[0].name[:-32]+'1'*32)
    shutil.copytree(paths[0],other)
    marker=other/'delivery.v1.txt';marker.write_text(marker.read_text().replace(paths[0].name,other.name))
    runner.configure(state_dir=root,config=config);peer=Peer()
    with pytest.raises(single.Refused,match='duplicate_selection'):invoke(root,peer)
    assert not Path(config['job_dir']).exists()
    assert invoke(root,peer,select_attempt=other.name)['inputs']==1
    assert invoke(root,peer)['status']=='awaiting_finalize'
    saved=json.loads((root/'input-000.intent.json').read_bytes());assert saved['path']==str(other)
    other.rename(tmp_path/'held-original')
    with pytest.raises(single.Refused,match='frozen_original_missing'):invoke(root,peer)


def test_fresh_process_crash_after_accept_before_receipt_resumes_exact_path(tmp_path):
    paths,config,root=example(tmp_path,2)
    runner.configure(state_dir=root,config=config)
    script='''
import os
from audio_backend import recorder_inbox_runner as r, recording_uploader as u
from test_extended_recorder_intake import Peer
real=r.intake.run
def crash(**kwargs):
    result=real(**kwargs)
    os._exit(73)
r.intake.run=crash
r.run(state_dir=os.environ['RUNNER_ROOT'],exclusive_writer=True,acknowledge_span_local_closure=True,
      limits=u.Limits(disk_floor=0),transport=Peer())
'''
    env=dict(os.environ,RUNNER_ROOT=str(root));env['PYTHONPATH']=str(Path(__file__).parent)+os.pathsep+env.get('PYTHONPATH','')
    child=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True,timeout=30)
    assert child.returncode==73,child.stderr
    assert (root/'input-000.intent.json').exists() and not (root/'input-000.done.json').exists()
    assert invoke(root,Peer())['inputs']==1
    assert invoke(root,Peer())['inputs']==2
    assert len(list((Path(config['job_dir'])/'admissions').iterdir()))==2


def test_pending_or_blocked_observation_never_creates_export_intent(tmp_path,monkeypatch):
    paths,config,root=example(tmp_path,1);runner.configure(state_dir=root,config=config);peer=Peer()
    invoke(root,peer,max_actions=2,finalize=True)
    for state in ('pending','blocked'):
        monkeypatch.setattr(runner.status,'run',lambda **kw:dict(status=state,analysis_completion_verified=False))
        monkeypatch.setattr(runner.exporter,'run',lambda **kw:pytest.fail('must not export pending work'))
        assert invoke(root,peer,export=True)['status']==state
        assert not (root/'export.intent.json').exists()


def fake_export(output_dir,**kwargs):
    """Publication stub, not evidence of transcript or primary completion."""
    path=Path(output_dir);path.mkdir(mode=0o700)
    artifacts={}
    for name,raw in (('export.json',b'{}\n'),('transcript.txt',b'Synthetic fixture\n')):
        target=path/name;target.write_bytes(raw);target.chmod(0o600)
        artifacts[name]=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    manifest=dict(schema=runner.exporter.SCHEMA,status='exported',artifacts=artifacts)
    marker=path/'complete.json';marker.write_text(json.dumps(manifest)+'\n');marker.chmod(0o600)
    return manifest


@pytest.mark.parametrize('crash_stage',['before_output','partial_output','after_publication'])
def test_export_crash_never_overwrites_or_adopts_uncertain_output(tmp_path,monkeypatch,crash_stage):
    paths,config,root=example(tmp_path,1);runner.configure(state_dir=root,config=config);peer=Peer()
    invoke(root,peer,max_actions=2,finalize=True)
    monkeypatch.setattr(runner.status,'run',lambda **kw:dict(status='completed',analysis_completion_verified=True))
    class Crash(BaseException):pass
    def stopped(**kwargs):
        if crash_stage=='after_publication':fake_export(**kwargs)
        elif crash_stage=='partial_output':
            out=Path(kwargs['output_dir']);out.mkdir(mode=0o700);(out/'export.json.partial').write_bytes(b'partial')
        raise Crash()
    monkeypatch.setattr(runner.exporter,'run',stopped)
    with pytest.raises(Crash):invoke(root,peer,export=True)
    assert (root/'export.intent.json').exists() and not (root/'export.done.json').exists()
    monkeypatch.setattr(runner.exporter,'run',fake_export)
    if crash_stage=='before_output':
        assert invoke(root,peer,export=True)['status']=='exported'
    else:
        before={p.name:p.read_bytes() for p in Path(config['output_dir']).iterdir()}
        with pytest.raises(single.Refused,match='publication_uncertain'):invoke(root,peer,export=True)
        assert before=={p.name:p.read_bytes() for p in Path(config['output_dir']).iterdir()}


def test_completed_export_receipt_rechecks_artifact_hashes_without_reexport(tmp_path,monkeypatch):
    paths,config,root=example(tmp_path,1);runner.configure(state_dir=root,config=config);peer=Peer()
    invoke(root,peer,max_actions=2,finalize=True)
    monkeypatch.setattr(runner.status,'run',lambda **kw:dict(status='completed',analysis_completion_verified=True))
    monkeypatch.setattr(runner.exporter,'run',fake_export)
    assert invoke(root,peer,export=True)['status']=='exported'
    monkeypatch.setattr(runner.exporter,'run',lambda **kw:pytest.fail('must not reexport'))
    assert invoke(root,peer,export=True)['status']=='exported'
    (Path(config['output_dir'])/'transcript.txt').write_bytes(b'changed')
    with pytest.raises(single.Refused,match='artifact'):invoke(root,peer,export=True)


def test_request_limit_is_shared_across_actions_and_resumable(tmp_path):
    paths,config,root=example(tmp_path,2);runner.configure(state_dir=root,config=config);peer=Peer()
    with pytest.raises(single.Refused,match='request_bound'):invoke(root,peer,max_actions=2,max_requests=1)
    assert (root/'input-000.intent.json').exists()
    assert invoke(root,peer,max_actions=2)['inputs']==2


def test_cli_configuration_is_local_only_and_errors_are_redacted(tmp_path,capsys):
    paths,config,root=example(tmp_path,1)
    args=['configure','--state-dir',str(root),'--first-delivery',str(paths[0])]
    for name in ('inbox_dir','job_dir','output_dir','api','session_id','capture_id'):
        args.extend(['--'+name.replace('_','-'),config[name]])
    assert runner.main(args)==0
    assert json.loads(capsys.readouterr().out)['status']=='configured'
    assert runner.main(args)==2
    output=capsys.readouterr().err
    assert str(root) not in output and 'traceback' not in output.lower()


def test_inventory_accepts_192_input_arrivals_and_counts_all_256_entries(tmp_path):
    # Tiny real delivery files establish enumeration capacity, not 12-hour runtime.
    paths,config,root=example(tmp_path,192)
    runner.configure(state_dir=root,config=config);peer=Peer()
    assert invoke(root,peer)['inputs']==1
    inbox=Path(config['inbox_dir'])
    for index in range(64):(inbox/('ignored-'+str(index))).touch()
    assert invoke(root,peer)['inputs']==2
    (inbox/'one-too-many').touch()
    before=(root/'input-001.done.json').read_bytes()
    with pytest.raises(single.Refused,match='entry_limit'):invoke(root,peer)
    assert (root/'input-001.done.json').read_bytes()==before
    assert not (root/'input-002.intent.json').exists()
