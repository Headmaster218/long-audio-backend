"""Plan wiring uses synthetic primary receipts; never native/model execution."""
import json
import sys
from pathlib import Path
import pytest
from audio_backend import recorder_analysis_plan as plan
from audio_backend import recorder_candidate_postphase as post
from test_recorder_analysis_plan import setup
from test_recorder_ftps_receiver import configured
from test_recorder_generation_transition import NoHTTP, files
from test_recorder_native_plan_exports import native_stub
from test_recorder_inbox_runner import runner


def config(tmp_path):
    path=tmp_path/'candidate-config.json'
    path.write_text(json.dumps(dict(schema=post.CONFIG_SCHEMA,python=sys.executable,
        model=str(tmp_path/'absent-model.gguf'),ledger_dir=str(tmp_path/'candidate-ledger'),
        max_calls=8,max_wall_seconds=4000,max_output_bytes=256*1024**2)))
    path.chmod(0o600)
    return path


@pytest.mark.parametrize('terminal',[0,1,2])
def test_only_safe_completed_generations_and_primary_status_preserved(configured,tmp_path,monkeypatch,terminal):
    args,peer,*_=setup(configured,tmp_path,monkeypatch,terminal=terminal)
    monkeypatch.setattr(runner.exporter,'run',native_stub(runner.exporter.run,[]))
    calls=[]
    def drain(**kw):calls.append(kw);return post.status('candidate_resource_blocked')
    monkeypatch.setattr(post,'drain',drain)
    answer=plan.run(**args,transport=peer,native_turns=True,candidate_postphase=True,candidate_config=config(tmp_path))
    assert answer['status']==answer['primary_status']=='plan_complete'
    assert answer['enhancement']['status']=='candidate_resource_blocked'
    assert len(calls)==1 and len(calls[0]['generations'])==terminal+1
    assert set(p.name for p in args['control_dir'].iterdir())=={'chain.lock',plan.INTENT}
    assert set(p.name for p in (tmp_path/'candidate-ledger').iterdir())=={'ledger.lock','owner.json','reservations.json'}


def test_paused_has_only_binding_no_candidate_attempt_or_probe(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch)
    monkeypatch.setattr(post,'drain',lambda **kw:(_ for _ in ()).throw(AssertionError('deferred')))
    answer=plan.run(**args,transport=NoHTTP(),native_turns=True,candidate_postphase=True,
        candidate_config=config(tmp_path),max_total_actions=1)
    assert answer['status']==answer['primary_status']=='plan_paused'
    assert answer['enhancement']['status']=='enhancement_deferred'
    assert answer['enhancement']['candidate_attempts_started']==0
    assert set(p.name for p in (tmp_path/'candidate-ledger').iterdir())=={'ledger.lock','owner.json','reservations.json'}


def test_opt_in_requires_native_and_config_before_effects(tmp_path):
    for kw in ({'candidate_postphase':True},{'candidate_config':'/unread'}):
        with pytest.raises(Exception,match='candidate_'):
            plan.run(plan_file='/absent',control_dir='/absent2',**kw)
    assert not list(tmp_path.iterdir())


def test_legacy_intent_cannot_gain_opt_in(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch)
    assert plan.run(**args,transport=NoHTTP(),native_turns=True,max_total_actions=1)['status']=='plan_paused'
    cfg=config(tmp_path);before=files(tmp_path)
    with pytest.raises(Exception,match='saved_intent_changed'):
        plan.run(**args,transport=NoHTTP(),native_turns=True,candidate_postphase=True,candidate_config=cfg,max_total_actions=1)
    assert files(tmp_path)==before


def test_orphan_initialization_crash_does_not_adopt_on_repeat(configured,tmp_path,monkeypatch):
    args,peer,*_=setup(configured,tmp_path,monkeypatch);cfg=config(tmp_path)
    original=plan.single.manifest_write
    def crash(path,value):
        if path.name==plan.INTENT:raise RuntimeError('fixture crash after binding')
        return original(path,value)
    monkeypatch.setattr(plan.single,'manifest_write',crash)
    with pytest.raises(RuntimeError):
        plan.run(**args,transport=NoHTTP(),native_turns=True,candidate_postphase=True,candidate_config=cfg,max_total_actions=1)
    monkeypatch.setattr(plan.single,'manifest_write',original)
    assert not (args['control_dir']/plan.INTENT).exists()
    with pytest.raises(Exception,match='existing_no_adoption'):
        plan.run(**args,transport=NoHTTP(),native_turns=True,candidate_postphase=True,candidate_config=cfg,max_total_actions=1)


def test_main_reports_primary_success_optional_block_with_partial_exit(monkeypatch,capsys):
    monkeypatch.setattr(plan,'run',lambda **kw:dict(status='plan_complete',primary_status='plan_complete',
        enhancement=dict(status='candidate_resource_blocked')))
    assert plan.main(['--plan-file','/a','--control-dir','/b','--acknowledge-finite-plan','--exclusive-writer',
        '--acknowledge-span-local-closure'])==3
    assert json.loads(capsys.readouterr().out)['status']=='plan_complete'


def test_broken_main_stdout_still_retains_pending_cleanup(monkeypatch):
    import builtins
    monkeypatch.setattr(plan,'run',lambda **kw:dict(status='plan_complete',enhancement=dict(status='candidate_unknown')))
    monkeypatch.setattr(post,'pending_cleanup',lambda:True)
    retained=[]
    monkeypatch.setattr(post,'retain_cleanup',lambda:retained.append(True) or True)
    monkeypatch.setattr(post,'cleanup_notice',lambda value:None)
    def broken(*a,**k):raise BrokenPipeError('closed fixture stdout')
    monkeypatch.setattr(builtins,'print',broken)
    with pytest.raises(BrokenPipeError):
        plan.main(['--plan-file','/a','--control-dir','/b','--acknowledge-finite-plan','--exclusive-writer',
            '--acknowledge-span-local-closure'])
    assert retained==[True]
