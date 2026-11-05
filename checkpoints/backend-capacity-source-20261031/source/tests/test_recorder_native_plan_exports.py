"""Native evidence opt-in plumbing; synthetic transport does not qualify models."""
import hashlib
import json
from pathlib import Path
import pytest

from audio_backend import recorder_analysis_plan as plan
from audio_backend import recorder_analysis_handoff as handoff
from audio_backend import recorder_generation_transition as transition
from audio_backend import recorder_deployment_bundle as bundle
from audio_backend import recorder_export_recovery as recovery
from test_recorder_analysis_plan import setup as plan_setup
from test_recorder_generation_transition import setup as transition_setup, NoHTTP, files
from test_recorder_analysis_handoff import options as handoff_options
from test_recorder_ftps_receiver import configured
from test_recorder_inbox_runner import runner, example, invoke, Peer, fake_export


def native_stub(original, calls):
    """Add third publication fixture while preserving logical-boundary metadata."""
    def export(**kw):
        calls.append(dict(kw))
        manifest = original(**kw)
        if kw.get('native_turns'):
            root = Path(kw['output_dir']); raw = b'{"synthetic_native_evidence":true}\n'
            path = root/'native-turns.json'; path.write_bytes(raw); path.chmod(0o600)
            manifest['artifacts'][path.name] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            manifest['local_publication'] = 'manifest_commits_three_artifacts'
            (root/'complete.json').write_bytes(runner.single._json(manifest))
        return manifest
    return export


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('terminal', [0, 1, 2])
def test_plan_exact_artifacts_policy_and_zero_http_repeat(configured, tmp_path, monkeypatch, native, terminal):
    args, peer, data, roots = plan_setup(configured, tmp_path, monkeypatch, terminal=terminal)
    calls = []; monkeypatch.setattr(runner.exporter, 'run', native_stub(runner.exporter.run, calls))
    before = {str(p):files(p) for group in roots for p in group[:2]}
    result = plan.run(**args, transport=peer, native_turns=native)
    assert result['status'] == 'plan_complete', result
    assert len(calls) == terminal+1
    assert all((kw.get('native_turns') is True if native else 'native_turns' not in kw) for kw in calls)
    chosen = {'export.json','transcript.txt'} | ({'native-turns.json'} if native else set())
    saved_plan = json.loads((args['control_dir']/plan.INTENT).read_bytes())
    assert saved_plan.get('export_policy') == (runner.NATIVE_EXPORT_POLICY if native else None)
    for index in range(terminal+1):
        state = tmp_path/f'runner-{index}'; output = tmp_path/f'output-{index}'
        intent = json.loads((state/'export.intent.json').read_bytes())
        assert intent.get('export_policy') == (runner.NATIVE_EXPORT_POLICY if native else None)
        assert set(intent) == {'schema','config_sha256','kind','input_intents_sha256'} | ({'export_policy'} if native else set())
        manifest = json.loads((output/'complete.json').read_bytes())
        assert set(manifest['artifacts']) == chosen
        assert {p.name for p in output.iterdir()} == chosen | {'complete.json'}
        for name, bound in manifest['artifacts'].items():
            raw = (output/name).read_bytes()
            assert len(raw) == bound['bytes'] and hashlib.sha256(raw).hexdigest() == bound['sha256']
        assert 'native_turns' not in json.loads((state/'config.json').read_bytes())
    saved = files(tmp_path)
    repeated = plan.run(**args, transport=NoHTTP(), native_turns=native)
    assert repeated['status'] == 'plan_complete' and repeated['network_requests'] == repeated['network_writes'] == 0
    assert files(tmp_path) == saved
    with pytest.raises(runner.Refused, match='policy_changed|saved_intent_changed'):
        plan.run(**args, transport=NoHTTP(), native_turns=not native)
    assert files(tmp_path) == saved
    assert {str(p):files(p) for group in roots for p in group[:2]} == before


@pytest.mark.parametrize('native', [False, True])
def test_plan_choice_pinned_before_export_or_network(configured, tmp_path, monkeypatch, native):
    args, _, _, _ = plan_setup(configured, tmp_path, monkeypatch)
    result = plan.run(**args, transport=NoHTTP(), native_turns=native, max_total_actions=1)
    assert result['status'] == 'plan_paused' and result['network_requests'] == 0
    assert not list(tmp_path.glob('runner-*/export.intent.json'))
    before = files(tmp_path)
    with pytest.raises(runner.Refused, match='saved_intent_changed'):
        plan.run(**args, transport=NoHTTP(), native_turns=not native)
    assert files(tmp_path) == before


@pytest.mark.parametrize('native', [False, True])
def test_bundle_generated_analyze_opt_in_only(configured, tmp_path, native):
    result = bundle.prepare(bundle_dir=tmp_path/'fresh', endpoint_config=configured['path'],
        api='http://127.0.0.1:8000', acknowledge_fresh_only=True, native_turns=native)
    assert ('--native-turns' in result['commands']['analyze']) is native
    assert result['settings'].get('native_turns') == (True if native else None)
    assert '--native-turns' not in result['commands']['initialize_prefix']
    finite = json.loads((tmp_path/'fresh/plan.json').read_bytes())
    assert set(finite) == {'schema','state_dir','edges'}
    assert result['network_requests'] == 0 and result['service_started'] is False


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('committed', [False, True])
def test_runner_saved_choice_refuses_switch_before_effects(tmp_path, monkeypatch, native, committed):
    paths, config, root = example(tmp_path, 1)
    runner.configure(state_dir=root, config=config)
    invoke(root, Peer(), max_actions=2, finalize=True)
    monkeypatch.setattr(runner.status, 'run', lambda **kw:dict(status='completed', analysis_completion_verified=True))
    if committed:
        monkeypatch.setattr(runner.exporter, 'run', native_stub(fake_export, []))
        assert invoke(root, NoHTTP(), export=True, native_turns=native)['status'] == 'exported'
    else:
        def fail(**kw):raise RuntimeError('before output')
        monkeypatch.setattr(runner.exporter, 'run', fail)
        with pytest.raises(RuntimeError):invoke(root, NoHTTP(), export=True, native_turns=native)
    before = files(tmp_path)
    with pytest.raises(runner.Refused, match='policy_changed'):
        invoke(root, NoHTTP(), export=True, native_turns=not native)
    assert files(tmp_path) == before


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('wrapper', ['handoff', 'transition'])
def test_wrapper_checks_saved_export_before_boundary_effect(configured, tmp_path, monkeypatch, native, wrapper):
    args, peer, *_ = transition_setup(configured, tmp_path, monkeypatch)
    monkeypatch.setattr(runner.exporter, 'run', native_stub(runner.exporter.run, []))
    done = transition.transition(**args, transport=peer, native_turns=native)
    assert done['status'] == 'transition_complete', done
    before = files(tmp_path)
    call = transition.transition if wrapper == 'transition' else handoff.handoff
    options = args if wrapper == 'transition' else handoff_options(args)
    with pytest.raises(runner.Refused, match='policy_changed'):
        call(**options, transport=NoHTTP(), native_turns=not native)
    assert files(tmp_path) == before


@pytest.mark.parametrize('damage', ['missing', 'unknown', 'hash', 'extra_manifest', 'policy', 'publication'])
def test_native_receipt_requires_exact_files_hash_and_policy(tmp_path, damage):
    output = tmp_path/'export'; native_stub(fake_export, [])(output_dir=output, native_turns=True)
    intent = dict(schema=runner.SCHEMA, config_sha256='0'*64, kind='export', input_intents_sha256='1'*64,
        export_policy=runner.NATIVE_EXPORT_POLICY)
    marker = output/'complete.json'; manifest = json.loads(marker.read_bytes())
    if damage == 'missing':(output/'native-turns.json').unlink()
    if damage == 'unknown':(output/'extra.json').write_bytes(b'{}')
    if damage == 'hash':
        path = output/'native-turns.json'; raw = path.read_bytes(); path.write_bytes(b'!' + raw[1:])
    if damage == 'extra_manifest':manifest['artifacts']['unexpected.json'] = dict(bytes=1,sha256='0'*64)
    if damage == 'policy':intent['export_policy'] = 'other'
    if damage == 'publication':manifest['local_publication'] = 'manifest_commits_artifact_pair'
    marker.write_bytes(runner.single._json(manifest))
    with pytest.raises(runner.Refused):
        runner._export_receipt(tmp_path, dict(output_dir=str(output)), intent, runner.u.Guard(runner.u.Limits()))


def test_legacy_receipt_never_accepts_third_artifact(tmp_path):
    native_stub(fake_export, [])(output_dir=tmp_path/'export', native_turns=True)
    with pytest.raises(runner.Refused):
        runner._export_receipt(tmp_path, dict(output_dir=str(tmp_path/'export')),
            dict(config_sha256='0'*64), runner.u.Guard(runner.u.Limits()))


@pytest.mark.parametrize('entry', ['plan', 'handoff', 'transition', 'run', 'watch', 'recovery'])
def test_native_recovery_is_refused_before_files_or_http(tmp_path, entry):
    state = str(tmp_path/'absent')
    common = dict(native_turns=True, transport=NoHTTP(), export_recovery_attempts=2)
    edge = dict(state_dir=state, route_config=state, next_state_dir=state, next_job_dir=state,
        next_output_dir=state, next_capture_id='next')
    if entry == 'plan':call = plan.run; kw = dict(plan_file=state, control_dir=state)
    elif entry == 'handoff':call = handoff.handoff; kw = edge
    elif entry == 'transition':call = transition.transition; kw = dict(edge, successor_attempt='absent')
    elif entry == 'recovery':
        call = recovery.run; common.pop('export_recovery_attempts')
        kw = dict(job_dir=state, api='invalid', session_id='known', capture_id='cap', output_dir=state)
    else:call = getattr(runner, entry); kw = dict(state_dir=state)
    with pytest.raises(runner.Refused, match='native_turn.*unsupported'):
        call(**kw, **common)
    assert not list(tmp_path.iterdir())


def test_standalone_handoff_forwards_native_to_successor(configured, tmp_path, monkeypatch):
    args, peer, config, *_ = transition_setup(configured, tmp_path, monkeypatch)
    original = runner.exporter.run
    def selected(output_dir, **kw):
        function = original if Path(output_dir) == Path(config['output_dir']) else fake_export
        return function(output_dir=output_dir, **kw)
    calls = []; monkeypatch.setattr(runner.exporter, 'run', native_stub(selected, calls))
    result = handoff.handoff(**handoff_options(args), transport=peer, native_turns=True)
    assert result['status'] == 'handoff_complete', result
    assert len(calls) == 2 and all(kw['native_turns'] is True for kw in calls)
    before = files(tmp_path)
    repeated = handoff.handoff(**handoff_options(args), transport=NoHTTP(), native_turns=True)
    assert repeated['status'] == 'handoff_complete' and repeated['network_requests'] == 0
    assert files(tmp_path) == before


@pytest.mark.parametrize('value', [None, 0, 1, 'true'])
def test_native_option_requires_real_boolean(value):
    with pytest.raises(runner.Refused, match='native_turn_option'):
        runner.native_export_options(value)
