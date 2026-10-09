"""MOSS-TD engine contracts with fake heavyweight dependencies; no model run."""
import ast
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from audio_backend import moss_td_assets as assets
from audio_backend import moss_td_runner as runner
from audio_backend.inference import moss_td_asr as engine


def fake_parser(text, **kwargs):
    return [SimpleNamespace(start=float(m[1]), end=float(m[4]), speaker=m[2], text=m[3])
            for m in engine._CANONICAL.finditer(text)]


def parse(text, duration=10, ids=None):
    return engine.parse_generation(text, [1, 2] if ids is None else ids, duration, fake_parser)


def test_original_labels_first_appearance_and_concurrent_nonmonotonic_times():
    result = parse('[4.25][S99] Later [5.25][0.1][S01]中文[2][1][S99]overlap[3]')
    assert result['status'] == 'ok'
    assert [p['speaker_local_id'] for p in result['phrases']] == ['S99', 'S01', 'S99']
    assert [p['source_slot'] for p in result['phrases']] == [0, 1, 0]
    assert [p['start'] for p in result['phrases']] == [4.25, .1, 1]
    assert result['phrases'][0]['raw_text'] == ' Later '
    assert result['phrases'][0]['text'] == 'Later'
    assert result['phrases'][0]['ownership_anchor'] == 4.25
    assert result['phrases'][0]['timestamp_quality'] == 'model_generated_unverified'
    assert result['phrases'][0]['confidence'] is None
    assert result['diagnostic_phrases'] == []


@pytest.mark.parametrize('text,reason', [
    ('Refusal', 'noncanonical_or_unparsed_text'),
    ('[0][S01]hi[1] trailing', 'noncanonical_or_unparsed_text'),
    ('[0][S01]hi[1][bad]', 'noncanonical_or_unparsed_text'),
    ('[0][S01]hi[11]', 'out_of_range_or_invalid_timestamps'),
    ('[2][S01]hi[1]', 'out_of_range_or_invalid_timestamps'),
    ('[2][S01]hi[2]', 'out_of_range_or_invalid_timestamps'),
    ('[-1][S01]hi[2]', 'noncanonical_or_unparsed_text'),
    ('[0][speaker1]hi[2]', 'noncanonical_or_unparsed_text'),
    ('[0][S01] [2]', 'empty_phrase_text'),
    ('', 'no_text'),
])
def test_fail_closed(text, reason):
    result = parse(text)
    assert result['status'] == 'unresolved'
    assert reason in result['schema_errors']
    assert result['phrases'] == []
    assert result['raw_text'] == text
    assert result['raw_generated_ids'] == [1, 2]


def test_valid_prefix_survives_as_diagnostic_only():
    result = parse('[0][S01]valid[1][2][S02]bad[12]')
    assert result['phrases'] == []
    assert len(result['diagnostic_phrases']) == 1
    assert result['diagnostic_phrases'][0]['text'] == 'valid'
    assert result['diagnostic_phrases'][0]['end'] == 1


def test_times_never_clamped_or_tolerated():
    assert parse('[0][S01]hi[10]')['phrases'][0]['end'] == 10
    result = parse('[0][S01]hi[10.000001]')
    assert result['status'] == 'unresolved' and result['diagnostic_phrases'] == []


def test_eight_unique_labels_allowed_ninth_fails_and_diagnostics_keep_originals():
    text = ''.join(f'[0][S{i:02}]speaker{i}[1]' for i in range(1, 10))
    assert parse(text.rsplit('[0]', 1)[0])['status'] == 'ok'
    result = parse(text)
    assert 'speaker_capacity_exceeded' in result['schema_errors']
    assert result['phrases'] == []
    assert len(result['diagnostic_phrases']) == 9
    assert result['diagnostic_phrases'][-1]['speaker_local_id'] == 'S09'
    assert result['diagnostic_phrases'][-1]['source_slot'] is None


def test_token_limit_fails_closed_even_if_last_token_eos_and_text_valid():
    result = parse('[0][S01]hello[1]', ids=list(range(512)))
    assert result['reason_code'] == 'generation_token_limit_reached'
    assert result['metadata']['token_limit_reached'] is True
    assert len(result['diagnostic_phrases']) == 1
    assert result['phrases'] == []


def test_official_parser_omission_and_failure_detected():
    for parser in (lambda *a, **kw: [], lambda *a, **kw: (_ for _ in ()).throw(ValueError('bad'))):
        result = engine.parse_generation('[0][S01]hello[1]', [1], 2, parser)
        assert result['status'] == 'unresolved' and not result['phrases']
        assert 'parser_envelope_mismatch' in result['schema_errors']


def test_nonfinite_parser_timestamp_fails_closed():
    bad = lambda *a, **kw: [SimpleNamespace(start=0, end=math.inf, speaker='S01', text='hello')]
    result = engine.parse_generation('[0][S01]hello[1]', [1], 2, bad)
    assert 'out_of_range_or_invalid_timestamps' in result['schema_errors']
    assert not result['phrases']


def test_prompt_ast_does_not_execute_helper(tmp_path):
    path = tmp_path / 'helper.py'
    path.write_text("raise RuntimeError('do not execute')\nDEFAULT_PROMPT = ('a' 'b')\n")
    assert engine._read_prompt(path) == 'ab'
    path.write_text("DEFAULT_PROMPT = __import__('no_such_module')\n")
    with pytest.raises(ValueError):
        engine._read_prompt(path)


def test_source_loader_ignores_bytecode_and_checks_source_hash(tmp_path):
    path = tmp_path / 'source.py'
    path.write_text('VALUE = 42\n')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    module = engine._import_verified('_moss_test_verified', path, digest)
    assert module.VALUE == 42
    path.write_text('VALUE = 43\n')
    with pytest.raises(ValueError, match='SHA256'):
        engine._import_verified('_moss_test_corrupt', path, digest)
    assert '_moss_test_corrupt' not in sys.modules


def toy_assets(monkeypatch, tmp_path):
    path = tmp_path / 'pinned.py'
    path.write_text('x = 1\n')
    spec = {'runtime_files': [{'name': path.name, 'bytes': path.stat().st_size,
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
    monkeypatch.setattr(assets, 'manifest', lambda: spec)
    return path, spec


def test_full_asset_hash_and_size_only_readiness(monkeypatch, tmp_path):
    path, spec = toy_assets(monkeypatch, tmp_path)
    config = {'moss_td_python': sys.executable, 'moss_td_model_dir': str(tmp_path)}
    assert assets.verify_model_directory(tmp_path) == spec
    path.write_text('x = 2\n')
    assert assets.moss_td_readiness(config)['ready'] is True
    with pytest.raises(ValueError, match='SHA256'):
        assets.verify_model_directory(tmp_path)
    path.write_text('bad')
    assert assets.moss_td_readiness(config)['ready'] is False


def test_readiness_missing_runtime_and_python(tmp_path):
    assert assets.moss_td_readiness({})['reason'] == 'moss_td_runtime_not_configured'
    assert assets.moss_td_readiness({'moss_td_python': str(tmp_path / 'missing'),
                                    'moss_td_model_dir': str(tmp_path)})['reason'] == 'moss_td_python_missing'


@pytest.mark.parametrize('name', ['../escape', 'a/b', 'a\\b', ''])
def test_manifest_rejects_nonflat_names(monkeypatch, tmp_path, name):
    monkeypatch.setattr(assets, 'manifest', lambda: {'runtime_files': [{'name': name}]})
    with pytest.raises(ValueError, match='filename'):
        assets.verify_model_directory(tmp_path)


def test_pins_and_single_directory_contract():
    spec = assets.manifest()
    assert spec['revision'] == '704aa4a9c304e8520be88901e0d1960158ef5b15'
    assert spec['source_revision'] == '61bc29cd4120be7b5d3b761b64cd5dff57263642'
    rows = {row['name']: row for row in spec['runtime_files']}
    weight = rows['model-00000-of-00001.safetensors']
    assert weight['bytes'] == 1817113576
    assert weight['sha256'] == '9a0ceb4ab7330357db3ff583dba8d83625d5b733b00e1d55d6970e11b07026c4'
    assert {'transcript_parser.py', 'inference_utils.py', 'model.safetensors.index.json'} <= rows.keys()
    assert all(row['revision'] in row['url'] for row in rows.values())
    assert not any(name.endswith(('.bin', '.pt', '.pkl')) for name in rows)


def test_assets_verified_before_heavy_or_custom_import(monkeypatch, tmp_path):
    called = []
    def deny(*args, **kw):
        called.append('verified')
        raise ValueError('corrupt asset')
    monkeypatch.setattr(engine, 'verify_model_directory', deny)
    monkeypatch.setitem(sys.modules, 'torch', None)
    with pytest.raises(ValueError, match='corrupt asset'):
        engine.MossTDRecognizer({'moss_td_model_dir': str(tmp_path)})
    assert called == ['verified']


def bare_recognizer():
    obj = engine.MossTDRecognizer.__new__(engine.MossTDRecognizer)
    obj.max_support_seconds = 20
    obj.metadata = {'model_revision': 'test-pinned'}
    return obj


def test_literal_zero_is_silent_without_inference(tmp_path):
    path = tmp_path / 'silent.wav'
    sf.write(path, np.zeros(16000, dtype='float32'), 16000, subtype='FLOAT')
    result = bare_recognizer().transcribe({'support_id': 's', 'path': str(path)})
    assert result['reason_code'] == 'silent_input' and not result['phrases']
    assert result['metadata']['inference_run'] is False
    assert result['metadata']['audio_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_quiet_nonzero_is_not_declared_silent(tmp_path):
    path = tmp_path / 'quiet.wav'
    sf.write(path, np.full(16000, 1e-30, dtype='float32'), 16000, subtype='FLOAT')
    obj = bare_recognizer()
    class Sentinel:
        def apply_chat_template(self, *args, **kw):
            raise RuntimeError('quiet nonzero reached inference')
    obj.processor = Sentinel()
    obj.prompt = 'prompt'
    with pytest.raises(RuntimeError, match='quiet nonzero reached inference'):
        obj.transcribe({'path': str(path)})


@pytest.mark.parametrize('samples,sr', [(np.ones(16001), 8000), (np.ones((100, 2)), 16000),
                                       (np.ones(320001), 16000), (np.array([np.nan]), 16000)])
def test_audio_read_rejects_invalid_input(tmp_path, samples, sr):
    path = tmp_path / 'invalid.wav'
    sf.write(path, samples, sr, subtype='FLOAT')
    with pytest.raises(ValueError):
        bare_recognizer()._read(path)


def request(tmp_path, **config):
    return {'config': config, 'supports': [{'support_id': 'one', 'path': str(tmp_path / 'in.wav')}],
            'output': str(tmp_path / 'result.json')}


@pytest.mark.parametrize('change', [
    {'supports': []}, {'supports': [{'support_id': 'a', 'path': 'https://bad'}]},
    {'supports': [{'support_id': 'a', 'path': 'x', 'target': 'S01'}]},
    {'supports': [{'support_id': 'a', 'path': 'x'}] * 2},
    {'supports': [{'support_id': str(i), 'path': 'x'} for i in range(5)]},
    {'oracle': 'forbidden'},
])
def test_request_shape_fail_closed(tmp_path, change):
    value = request(tmp_path)
    value.update(change)
    with pytest.raises(ValueError):
        runner.validate_request(value)


@pytest.mark.parametrize('key,value', [('moss_td_max_support_seconds', 20.01), ('moss_td_max_seconds', 81),
    ('moss_td_max_support_seconds', math.nan), ('moss_td_memory_limit_bytes', 5 * 1024**3),
    ('moss_td_timeout_seconds', 0), ('moss_td_max_seconds', True)])
def test_hard_limits(tmp_path, key, value):
    with pytest.raises(ValueError):
        runner.validate_request(request(tmp_path, **{key: value}))


def test_atomic_json_checks_utf8_bytes_and_nan(tmp_path):
    path = tmp_path / 'out.json'
    runner.atomic_json(path, {'text': '中文'})
    assert json.loads(path.read_text()) == {'text': '中文'}
    assert not path.with_name('out.json.pending').exists()
    with pytest.raises(ValueError):
        runner.atomic_json(path, {'text': '中' * (runner.MAX_JSON_BYTES // 2)})
    with pytest.raises(ValueError):
        runner.atomic_json(path, {'bad': math.nan})


def test_resource_guard_counts_descendants_and_reserves(monkeypatch, tmp_path):
    class Process:
        def __init__(self, rss, children=()): self.rss, self.descendants = rss, children
        def memory_info(self): return SimpleNamespace(rss=self.rss)
        def children(self, **kw): return self.descendants
    parent = Process(80, [Process(90)])
    psutil = SimpleNamespace(virtual_memory=lambda: SimpleNamespace(available=2 * 1024**3), NoSuchProcess=RuntimeError)
    monkeypatch.setattr(runner.shutil, 'disk_usage', lambda path: SimpleNamespace(free=6 * 1024**3))
    stats = {}
    result = runner.resource_failure(parent, {'moss_td_memory_limit_bytes': 160}, tmp_path/'out', stats,
                                     runner.time.monotonic(), psutil)
    assert result[0] == 'memory_guard' and stats['peak_rss_bytes'] == 170
    psutil.virtual_memory = lambda: SimpleNamespace(available=1)
    assert runner.resource_failure(parent, {}, tmp_path/'out', {}, runner.time.monotonic(), psutil)[0] == 'host_memory_reserve'
    psutil.virtual_memory = lambda: SimpleNamespace(available=2 * 1024**3)
    monkeypatch.setattr(runner.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1))
    assert runner.resource_failure(parent, {}, tmp_path/'out', {}, runner.time.monotonic(), psutil)[0] == 'disk_reserve'
    monkeypatch.setattr(runner.shutil, 'disk_usage', lambda path: SimpleNamespace(free=6 * 1024**3))
    assert runner.resource_failure(parent, {}, tmp_path/'out', {}, runner.time.monotonic()-301, psutil)[0] == 'timeout'


def test_runner_one_load_sequential_failures_continue(monkeypatch, tmp_path):
    calls = []
    class FakeRecognizer:
        metadata = {'test': True}
        def __init__(self, config): calls.append('load')
        def transcribe(self, support):
            calls.append(support['support_id'])
            if support['support_id'] == 'one': raise ValueError('per-support error')
            return parse('[0][S01]hello[1]', duration=2)
    monkeypatch.setattr(engine, 'MossTDRecognizer', FakeRecognizer)
    monkeypatch.setattr(runner, 'resource_failure', lambda *args: None)
    monkeypatch.setattr(runner.os, 'sched_setaffinity', lambda *args: None)
    path = tmp_path / 'in.wav'
    sf.write(path, np.ones(32000), 16000)
    value = request(tmp_path)
    value['supports'].append({'support_id': 'two', 'path': str(path)})
    path = tmp_path / 'request.json'
    path.write_text(json.dumps(value))
    runner.main([str(path)])
    result = json.loads((tmp_path/'result.json').read_text())
    assert calls == ['load', 'one', 'two']
    assert result['metadata']['model_loads'] == 1
    assert [r['status'] for r in result['supports']] == ['unresolved', 'ok']


def test_runner_total_budget_before_load(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, 'resource_failure', lambda *args: None)
    monkeypatch.setattr(runner.os, 'sched_setaffinity', lambda *args: None)
    monkeypatch.setattr(engine, 'MossTDRecognizer', lambda config: pytest.fail('must not load'))
    sf.write(tmp_path/'in.wav', np.ones(32000), 16000)
    value = request(tmp_path, moss_td_max_seconds=1)
    path = tmp_path/'request.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='total audio budget'):
        runner.main([str(path)])


def test_verified_official_parser_when_optional_assets_are_present():
    """Exercise the audited raw parser when local optional validation assets exist."""
    directory = assets.ROOT/'validation/moss-td-runtime'
    if not directory.exists():
        pytest.skip('Optional local MOSS-TD validation assets are not installed')
    spec = assets.verify_model_directory(directory)
    row = next(row for row in spec['runtime_files'] if row['name'] == 'transcript_parser.py')
    parser = engine._import_verified('_moss_test_official_parser', directory/row['name'], row['sha256'])
    raw = '[4][S03]later[5][0][S01]first[2][1][S03]overlap[3]'
    result = engine.parse_generation(raw, [1], 6, parser.parse_transcript)
    assert result['status'] == 'ok'
    assert [row['source_slot'] for row in result['phrases']] == [0, 1, 0]
    result = engine.parse_generation('[0][S01]valid[1] leftover', [1], 6, parser.parse_transcript)
    assert result['status'] == 'unresolved' and not result['phrases']


def installer_module():
    import importlib.util
    path = assets.ROOT/'scripts/setup_moss_td.py'
    spec = importlib.util.spec_from_file_location('_test_setup_moss_td', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_verified_flat_prepared_directory(monkeypatch, tmp_path):
    setup = installer_module()
    source = tmp_path/'source'
    source.mkdir()
    path, spec = toy_assets(monkeypatch, source)
    monkeypatch.setattr(setup, 'manifest', lambda: spec)
    monkeypatch.setattr(setup, 'require_space', lambda *a: None)
    destination = setup.prepare(tmp_path/'destination', prepared_dir=source)
    assert (destination/path.name).read_bytes() == path.read_bytes()
    assert path.read_text() == 'x = 1\n'
    before = (destination/path.name).stat()
    assert setup.prepare(destination, prepared_dir=source) == destination
    after = (destination/path.name).stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


def test_installer_bad_prepared_hash_before_creating_destination(monkeypatch, tmp_path):
    setup = installer_module()
    source = tmp_path/'source'
    source.mkdir()
    path, spec = toy_assets(monkeypatch, source)
    monkeypatch.setattr(setup, 'manifest', lambda: spec)
    path.write_text('x = 2\n')
    with pytest.raises(ValueError, match='SHA256'):
        setup.prepare(tmp_path/'destination', prepared_dir=source)
    assert not (tmp_path/'destination').exists()


def test_installer_download_hash_validation_and_partial_cleanup(monkeypatch, tmp_path):
    import io
    setup = installer_module()
    payload = b'x = 1\n'
    spec = {'runtime_files': [{'name': 'pinned.py', 'bytes': len(payload),
                              'sha256': hashlib.sha256(payload).hexdigest(), 'url': 'https://example.invalid/pinned'}]}
    monkeypatch.setattr(assets, 'manifest', lambda: spec)
    monkeypatch.setattr(setup, 'manifest', lambda: spec)
    monkeypatch.setattr(setup, 'require_space', lambda *a: None)
    monkeypatch.setattr(setup.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(payload))
    destination = setup.prepare(tmp_path/'good')
    assert (destination/'pinned.py').read_bytes() == payload
    monkeypatch.setattr(setup.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(b'x = 2\n'))
    with pytest.raises(ValueError, match='SHA256'):
        setup.prepare(tmp_path/'bad')
    assert not (tmp_path/'bad/pinned.py').exists()
    assert not (tmp_path/'bad/pinned.py.part').exists()
