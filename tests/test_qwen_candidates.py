"""Synthetic, model-free candidate contracts; these tests prove no ASR quality."""
from dataclasses import replace
import copy
import hashlib
import json
import struct
import subprocess
import sys
import wave

import pytest

from audio_backend.fragment_core import (
    Alias, Seam, Source, SourceChanged, SourcePlan, Span, inspect_pcm16_source,
)
from audio_backend.fragment_matcher import BoundaryEvidence, Continuity
from audio_backend.fragment_runtime import LogicalPCMReader
from audio_backend.fragment_runtime_contracts import digest
from audio_backend.qwen_candidates import (
    CORE_FRAMES, DEFAULT_CONFIG, MAX_TEXT_BYTES, RATE, UnsupportedSourceContract,
    admit_source_plan, candidate_windows, make_candidate_request, normalize_native,
    pcm16_to_float32le, prepare_candidate_requests, public_candidate, strict_loads,
    validate_candidate, validate_candidate_input, validate_candidate_request,
    validate_native_result,
)


def wav(path, pcm):
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm)
    return path


def plan_for(sources, *, final=True, relation='continuous', trim=0):
    sources = tuple(sources)
    spans = [Span(sources[0].fragment_id, 0, sources[0].frames, 0, sources[0].frames, 0)]
    aliases, seams = [], []
    cursor, island = sources[0].frames, 0
    for left, right in zip(sources, sources[1:]):
        declaration = Continuity('continuous', same_capture=True, replay_possible=False)
        evidence = BoundaryEvidence('abstain', 'synthetic_continuity_no_matcher_claim')
        status = 'declared_continuity_only'
        if trim:
            evidence = BoundaryEvidence('trim', 'unique_informative_exact_overlap',
                trim_frames=trim, candidate_frames=(trim,))
            status = 'verified_overlap'
        elif relation != 'continuous':
            declaration = Continuity('gap', gap_frames=7) if relation == 'gap' else Continuity('unknown')
            status = 'declared_gap' if relation == 'gap' else 'unknown_continuity'
        seams.append(Seam(left.fragment_id, right.fragment_id, declaration, evidence, cursor, status))
        if relation != 'continuous':
            island += 1
            cursor += declaration.gap_frames or 0
        if trim:
            aliases.append(Alias(right.fragment_id, 0, trim, cursor-trim, cursor, island))
        spans.append(Span(right.fragment_id, trim, right.frames, cursor, cursor+right.frames-trim, island))
        cursor += right.frames-trim
    return SourcePlan(sources, tuple(spans), tuple(aliases), tuple(seams), final).validate()


@pytest.fixture(scope='module')
def media(tmp_path_factory):
    root = tmp_path_factory.mktemp('qwen-synthetic')
    size = CORE_FRAMES + 1
    pattern = struct.pack('<6h', -32768, -12345, -1, 0, 1, 32767)
    pcm = (pattern * ((size + 5) // 6))[:size*2]
    cut = 160001
    paths = {'whole': wav(root/'whole.wav', pcm),
             'left': wav(root/'left.wav', pcm[:2*cut]),
             'right': wav(root/'right.wav', pcm[2*cut:])}
    sources = {key: inspect_pcm16_source(key, path) for key, path in paths.items()}
    return pcm, paths, sources


ARGS = dict(source_scope='synthetic-session', source_generation=3,
            runtime_manifest_sha256='a'*64, model_sha256='b'*64)


def one_request(media):
    _, paths, sources = media
    plan = plan_for([sources['whole']])
    window = next(candidate_windows(plan))
    pcm = LogicalPCMReader(plan, paths).read(window.read_start, window.read_end)
    return plan, window, pcm, make_candidate_request(plan, window, pcm, **ARGS)


def rebind(request):
    result = copy.deepcopy(request)
    result['source_map_sha256'] = digest(result['source_mapping'])
    result['profile_sha256'] = digest(result['config'])
    value = digest({key: value for key, value in result.items()
                    if key not in ('binding_sha256', 'request_id', 'candidate_id')})
    result.update(binding_sha256=value, request_id=value, candidate_id=value)
    return result


def raw(**updates):
    value = dict(native_status=0, native_status_name='OK', result_available=True,
        text='  native 文本  ', raw_text='language Chinese<asr_text>  native 文本  ',
        language='Chinese', was_truncated=False, truncation_available=True,
        exception_type=None, exception_status=None, partial_result_available=False,
        native_token_count=0, native_timestamp_kind='none')
    value.update(updates)
    return value


def no_result(**updates):
    return raw(result_available=False, text=None, raw_text=None, language=None,
               native_token_count=None, native_timestamp_kind=None, **updates)


def test_integer_global_grid_and_one_sample_tail():
    source = Source('synthetic', 'a'*64, RATE, 1, 15*CORE_FRAMES+1)
    windows = tuple(candidate_windows(plan_for([source])))
    assert len(windows) == 16
    assert [(w.core_start, w.core_end) for w in windows] == [
        (a, min(a+CORE_FRAMES, source.frames)) for a in range(0, source.frames, CORE_FRAMES)]
    assert all(w.read_start == w.core_start and w.read_end == w.core_end for w in windows)
    assert windows[-1].core_end-windows[-1].core_start == 1
    assert windows[-1].finish_native is True


def test_split_whole_equal_pcm_and_float_hashes_source_receipts_differ(media):
    pcm, paths, sources = media
    whole = prepare_candidate_requests(plan_for([sources['whole']]), paths, **ARGS)
    split = prepare_candidate_requests(plan_for([sources['left'], sources['right']]), paths, **ARGS)
    assert len(whole) == len(split) == 2
    for a, b in zip(whole, split):
        assert a['ownership'] == a['read'] == b['read']
        assert a['pcm16_sha256'] == b['pcm16_sha256']
        assert a['float32_sha256'] == b['float32_sha256']
        assert a['binding_sha256'] != b['binding_sha256']
        assert a['source_map_sha256'] != b['source_map_sha256']
        assert a['float32_count'] <= CORE_FRAMES
    seam = split[0]['source_mapping']
    assert [(x['span']['source_start'], x['span']['source_end']) for x in seam] == [(0, 160001), (0, 159999)]
    assert [(x['span']['logical_start'], x['span']['logical_end']) for x in seam] == [(0, 160001), (160001, CORE_FRAMES)]
    tail = split[1]
    assert tail['float32_count'] == 1
    assert tail['source_mapping'][0]['span']['source_start'] == 159999
    assert tail['context'] == {'left_frames': 0, 'right_frames': 0,
                              'left_reason': 'ownership_boundary', 'right_reason': 'true_eos'}
    assert whole[0]['pcm16_sha256'] == hashlib.sha256(pcm[:2*CORE_FRAMES]).hexdigest()
    assert all('path' not in json.dumps(value) for value in whole+split)


def test_trimmed_source_mapping_retains_original_offsets(tmp_path):
    # Synthetic accepted seam, not a matcher quality claim.
    data = struct.pack('<8h', *range(8)) * 1001
    paths = {'a': wav(tmp_path/'a.wav', data), 'b': wav(tmp_path/'b.wav', data[-16000:]+data)}
    sources = [inspect_pcm16_source(key, path) for key, path in paths.items()]
    plan = plan_for(sources, trim=8000)
    request, = prepare_candidate_requests(plan, paths, **ARGS)
    assert request['source_mapping'][1]['span'] == {
        'fragment_id': 'b', 'island': 0, 'source_start': 8000, 'source_end': 16008,
        'logical_start': 8008, 'logical_end': 16016}
    assert request['pcm16_sha256'] == hashlib.sha256(data+data).hexdigest()


@pytest.mark.parametrize('relation', ['gap', 'unknown'])
def test_unproven_seams_rejected(relation):
    sources = [Source(key, key*64, RATE, 1, 8) for key in ('a', 'b')]
    with pytest.raises(UnsupportedSourceContract):
        candidate_windows(plan_for(sources, relation=relation))


def test_only_final_original_exact_pcm_admitted():
    source = Source('a', 'a'*64, RATE, 1, 8)
    for plan in (plan_for([source], final=False),
                 plan_for([replace(source, sample_rate=8000)]),
                 plan_for([replace(source, channels=2)]),
                 plan_for([replace(source, sample_width=2.0)]), object()):
        with pytest.raises(UnsupportedSourceContract):
            admit_source_plan(plan)


def test_float32_receipt_is_exact_little_endian():
    values = [-32768, -1, 0, 1, 32767]
    pcm = struct.pack('<5h', *values)
    assert pcm16_to_float32le(pcm) == struct.pack('<5f', *(v/32768 for v in values))
    for invalid in (b'', b'x', bytearray(b'\x00\x00'), b'\x00'*(2*CORE_FRAMES+2)):
        with pytest.raises(ValueError):
            pcm16_to_float32le(invalid)


def test_requests_deterministic_and_identity_binds_provenance(media):
    plan, window, pcm, request = one_request(media)
    assert make_candidate_request(plan, window, pcm, **ARGS) == request
    for field, value in [('source_scope', 'another'), ('source_generation', 4),
                         ('runtime_manifest_sha256', 'c'*64), ('model_sha256', 'd'*64)]:
        changed = make_candidate_request(plan, window, pcm, **{**ARGS, field: value})
        assert changed['candidate_id'] != request['candidate_id']
    assert validate_candidate_input(request, pcm) == pcm16_to_float32le(pcm.pcm)
    assert request['source_mapping'][0]['source_sha256'] == plan.sources[0].sha256
    assert request['source_mapping'][0]['source_sha256'] != request['pcm16_sha256']


@pytest.mark.parametrize('field,value', [('source_generation', True), ('sample_rate', 16000.0),
    ('float32_count', True), ('float32_transform', 'native-endian'), ('source_scope', ''),
    ('pcm16_sha256', 'A'*64)])
def test_request_types_and_input_transform_are_strict(media, field, value):
    request = copy.deepcopy(one_request(media)[3])
    request[field] = value
    with pytest.raises(ValueError):
        validate_candidate_request(rebind(request))


@pytest.mark.parametrize('mutation', ['context', 'ownership_bool', 'mapping_bool', 'source_hole', 'config_bool', 'unknown'])
def test_rehashed_request_cannot_hide_invalid_contract(media, mutation):
    request = copy.deepcopy(one_request(media)[3])
    if mutation == 'context':
        request['context']['left_frames'] = 1
    elif mutation == 'ownership_bool':
        request['ownership']['start_frame'] = False
    elif mutation == 'mapping_bool':
        request['source_mapping'][0]['span']['island'] = False
    elif mutation == 'source_hole':
        request['source_mapping'][0]['span']['logical_start'] = 1
    elif mutation == 'config_bool':
        request['config']['context_frames'] = False
    else:
        request['word_timestamps'] = []
    with pytest.raises(ValueError):
        validate_candidate_request(rebind(request))


def test_tampered_pcm_map_and_float_hash_fail_before_decoder(media):
    _, _, pcm, request = one_request(media)
    bad_float = rebind({**request, 'float32_sha256': 'd'*64})
    with pytest.raises(ValueError):
        validate_candidate_input(bad_float, pcm)
    with pytest.raises(ValueError):
        validate_candidate_input(request, replace(pcm, pcm=b'\x00\x00'+pcm.pcm[2:]))
    with pytest.raises(ValueError):
        validate_candidate_input(request, replace(pcm, map_sha256='d'*64))
    forged = copy.deepcopy(request)
    forged['source_mapping'][0]['span']['source_start'] += 1
    forged['source_mapping'][0]['span']['source_end'] += 1
    with pytest.raises(ValueError):
        validate_candidate_input(rebind(forged), pcm)


def test_source_changed_rejected_on_new_binding(tmp_path):
    path = wav(tmp_path/'changed.wav', b'\x01\x00'*8)
    source = inspect_pcm16_source('a', path)
    wav(path, b'\x02\x00'*8)
    with pytest.raises(SourceChanged):
        prepare_candidate_requests(plan_for([source]), {'a': path}, **ARGS)


@pytest.mark.parametrize('text,status', [('  native 文本  ', 'returned_text'), ('', 'returned_empty'), ('   ', 'returned_text')])
def test_native_success_keeps_exact_text_and_no_silence_claim(text, status):
    result = normalize_native(raw(text=text))
    assert result['text'] == text
    assert result['raw_text'] == raw()['raw_text']
    assert result['status'] == status
    assert result['complete'] is True
    assert result['issues'] == []
    assert validate_native_result(result) == result
    assert result['word_timestamps'] is None
    assert result['speaker_id'] is None and result['speaker_local_id'] is None
    assert result['confidence'] is None
    assert result['generated_token_count'] is None and result['generated_token_count_known'] is False
    assert result['timestamp_semantics'] == 'input_window_only'
    assert result['verification_status'] == 'unverified_model_output'


@pytest.mark.parametrize('code,name,flag,outcome', [(18, 'OUTPUT_TRUNCATED', True, 'output_truncated'),
    (13, 'ABORTED', False, 'aborted'), (17, 'INPUT_TOO_LONG', False, 'native_error'),
    (91, 'UNKNOWN_NATIVE_ERROR', None, 'native_error'), (0, 'OK', False, 'native_error')])
def test_native_numeric_errors_and_partials_preserved(code, name, flag, outcome):
    result = normalize_native(raw(native_status=code, native_status_name=name,
        exception_type='TranscribeError', exception_status=code, partial_result_available=True,
        was_truncated=flag, truncation_available=flag is not None))
    assert result['status'] == outcome
    assert result['native_status'] == result['exception_status'] == code
    assert result['native_status_name'] == name
    assert result['text'] == raw()['text'] and result['raw_text'] == raw()['raw_text']
    assert result['partial_result_available'] is True
    assert result['complete'] is False


def test_generic_exception_with_no_result_keeps_unknown_status():
    result = normalize_native(no_result(native_status=None, native_status_name=None,
        exception_type='RuntimeError', was_truncated=None, truncation_available=False))
    assert result['status'] == 'native_error'
    assert result['native_status'] is None and result['exception_status'] is None
    assert result['text'] is None and result['partial_result_available'] is False
    assert result['truncation_state'] == 'unavailable'


@pytest.mark.parametrize('updates,outcome,issue', [
    ({'was_truncated': True}, 'output_truncated', None),
    ({'was_truncated': None, 'truncation_available': False}, 'schema_error', 'truncation_unavailable'),
    ({'was_truncated': None}, 'schema_error', 'inconsistent_truncation_receipt'),
    ({'native_status': 18, 'native_status_name': 'OUTPUT_TRUNCATED'}, 'output_truncated', 'truncation_status_flag_disagree'),
    ({'execution_returned': False}, 'schema_error', 'execution_return_unconfirmed'),
    ({'native_timestamp_kind': 'word'}, 'schema_error', 'unexpected_native_timestamp_kind'),
    ({'result_available': False}, 'schema_error', 'result_availability_mismatch'),
])
def test_truncation_unknown_and_inconsistent_receipts_never_complete(updates, outcome, issue):
    result = normalize_native(raw(**updates))
    assert result['status'] == outcome and result['complete'] is False
    assert result['text'] == raw()['text']
    if issue:
        assert issue in result['issues']


@pytest.mark.parametrize('updates', [
    {'native_status': False}, {'exception_status': 17.0}, {'native_token_count': True},
    {'was_truncated': 0}, {'truncation_available': 1}, {'execution_returned': 1},
    {'text': '文'*(MAX_TEXT_BYTES//3+1)}, {'raw_text': 'a'*MAX_TEXT_BYTES},
    {'exception_type': 'Traceback /private/path'}, {'word_timestamps': []},
    {'speaker_id': 'speaker_a'}, {'confidence': 0.9}, {'generated_token_count': 12},
    {'native_timings': {'decode_ms': float('nan')}},
])
def test_raw_adapter_strict_types_bounds_and_no_invented_metadata(updates):
    with pytest.raises(ValueError):
        normalize_native(raw(**updates))


def test_native_inventory_is_not_generated_token_count_or_confidence(media):
    native = normalize_native(raw(native_token_count=1,
        native_tokens=[{'id': 123, 'text': 'native', 'p': 0.99}],
        native_timings={'decode_ms': 1.5}))
    assert native['native_token_count'] == 1
    assert native['generated_token_count'] is None and native['confidence'] is None
    request = one_request(media)[3]
    candidate = public_candidate(request, native)
    assert 'native_tokens' not in candidate['native']
    assert candidate['request']['read'] == request['ownership']
    assert validate_candidate(candidate, request) == candidate
    changed = copy.deepcopy(candidate)
    changed['native']['speaker_id'] = 'speaker_a'
    with pytest.raises(ValueError):
        validate_candidate(changed, request)
    changed = copy.deepcopy(candidate)
    changed['request']['source_scope'] = 'elsewhere'
    with pytest.raises(ValueError):
        validate_candidate(changed, request)


@pytest.mark.parametrize('text', ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}'])
def test_strict_json_rejects_ambiguous_nonfinite_input(text):
    with pytest.raises(ValueError):
        strict_loads(text)


def test_no_model_imports_or_primary_writes():
    check = subprocess.run([sys.executable, '-B', '-c',
        "import sys; import audio_backend.qwen_candidates; "
        "assert not {'transcribe_cpp', 'torch', 'numpy'} & set(sys.modules)"],
        capture_output=True, text=True, timeout=5)
    assert check.returncode == 0, check.stderr
    assert DEFAULT_CONFIG == {'backend': 'cpu', 'n_threads': 2, 'kv_type': 'f16', 'n_ctx': 2048,
        'language': None, 'task': 'transcribe', 'timestamps': 'none',
        'context_frames': 0, 'generation_cap': 256, 'no_history': True}


def test_excessive_inventory_rejected_before_source_access():
    source = Source('synthetic', 'a'*64, RATE, 1, 1024*CORE_FRAMES+1)
    with pytest.raises(ValueError, match='1024-window'):
        prepare_candidate_requests(plan_for([source]), {'synthetic': '/not-read'}, **ARGS)


def test_ambiguous_seam_is_not_a_supported_continuity_assertion():
    sources = [Source(key, key*64, RATE, 1, 8) for key in ('a', 'b')]
    plan = plan_for(sources)
    evidence = BoundaryEvidence('abstain', 'ambiguous', candidate_frames=(1, 2))
    seam = replace(plan.seams[0], evidence=evidence, status='ambiguous_overlap')
    plan = replace(plan, seams=(seam,), spans=(plan.spans[0], replace(plan.spans[1], island=1)))
    plan.validate()
    with pytest.raises(UnsupportedSourceContract):
        admit_source_plan(plan)
