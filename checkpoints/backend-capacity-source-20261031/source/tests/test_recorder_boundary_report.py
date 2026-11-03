"""Partition observations without selecting conflicts or authorizing ingestion."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from audio_backend import recorder_boundary_report as b
from audio_backend import recorder_sequence as s
from test_recorder_sequence import row as sequence_row, fixture, anchored_rows


def row(seq=0, **changes):
    result = sequence_row(seq, **changes)
    m = result['metadata']
    result['delivery']['wav_bytes'] = 44 + m['frames'] * m['channels'] * 2
    return result


def report(*rows):
    return b._partition_records(list(rows))


def capture(*rows):
    return report(*rows)['captures'][0]


def span_ids(result):
    return [span['span_id'] for c in result['captures'] for span in c['spans']]


def test_shuffled_input_retains_original_indices_and_stable_span_identity():
    ordered = report(row(0), row(1), row(2, close='STOP'))
    rows = [row(2, close='STOP'), row(0), row(1)]
    for index, item in enumerate(rows):
        item['input_index'] = index
    shuffled = report(*rows)
    assert span_ids(ordered) == span_ids(shuffled)
    span = shuffled['captures'][0]['spans'][0]
    assert span['first_sequence'] == 0 and span['last_sequence'] == 2
    assert [r['input_indices'] for r in span['segments']] == [[1], [2], [0]]
    assert span['observed_frames'] == 12 and span['observed_wav_bytes'] == 156
    assert span['last_close_reason'] == 'STOP'


def test_span_identity_is_source_boundary_based_not_content_or_end_based():
    baseline = span_ids(report(row(0)))
    assert baseline == span_ids(report(row(0, pcm='c' * 64), row(1)))
    assert baseline != span_ids(report(row(1)))
    assert baseline != span_ids(report(row(0, capture='different')))
    # Observing an earlier adjoining prefix changes the actual start boundary.
    suffix = report(row(1), row(2))
    assert span_ids(suffix) != span_ids(report(row(0), row(1), row(2)))


def test_exact_duplicates_are_counted_once_as_audio_and_retain_provenance():
    first = row(0)
    repeated = deepcopy(first)
    repeated['input_index'] = 4
    c = capture(first, repeated, row(1))
    assert c['snapshot_input_count'] == 3
    assert c['same_content_repeat_count'] == 1 and c['unique_ordinal_count'] == 2
    assert c['unambiguous_segment_count'] == 2 and c['unambiguous_observed_wav_bytes'] == 104
    assert c['spans'][0]['observed_frames'] == 8
    assert c['spans'][0]['segments'][0]['input_indices'] == [0, 4]


def test_all_conflicting_variants_quarantined_even_with_exact_repeats():
    a = row(1)
    repeat = deepcopy(a)
    repeat['input_index'] = 5
    conflict = row(1, epoch=3, start=100, pcm='c' * 64, close='INTERRUPTION')
    conflict['input_index'] = 6
    c = capture(row(0), a, repeat, conflict, row(2))
    assert [(x['first_sequence'], x['last_sequence']) for x in c['spans']] == [(0, 0), (2, 2)]
    assert c['unambiguous_observed_wav_bytes'] == 104
    assert c['quarantined_distinct_content_wav_bytes'] == 104
    assert c['same_content_repeat_count'] == 1 and c['conflicting_ordinals'] == [1]
    quarantine = c['quarantines'][0]
    assert quarantine['selected_content'] is None and quarantine['distinct_content_count'] == 2
    assert {v['close_reason'] for v in quarantine['variants']} == {'ROTATION', 'INTERRUPTION'}
    for boundary in c['boundaries']:
        assert boundary['reasons'] == ['conflicting_ordinal_content']
        assert boundary['declared_frame_delta'] is None
    reversed_c = capture(row(2), conflict, repeat, a, row(0))
    assert c == reversed_c


def test_only_conflicted_ordinal_has_no_selected_audio_or_span():
    c = capture(row(0), row(0, pcm='c' * 64, close='STOP'))
    assert c['spans'] == [] and c['unambiguous_segment_count'] == 0
    assert c['unambiguous_observed_wav_bytes'] == 0
    assert c['last_supplied_close_reasons'] == ['ROTATION', 'STOP']
    assert c['leading_observation']['reasons'] == ['conflicting_ordinal_content']


@pytest.mark.parametrize('close', ['STOP', 'INTERRUPTION', 'RECOVERED_TAIL'])
def test_nonrotation_close_is_preserved_and_splits_next_segment(close):
    c = capture(row(0, close=close), row(1))
    assert len(c['spans']) == 2
    assert c['spans'][0]['last_close_reason'] == close
    assert 'previous_segment_not_rotation' in c['boundaries'][0]['reasons']
    assert c['spans'][1]['entry_reasons'] == c['boundaries'][0]['reasons']


@pytest.mark.parametrize('close,reason', [
    ('INTERRUPTION', 'interruption_close_not_supported_by_current_intake'),
    ('RECOVERED_TAIL', 'recovered_tail_close_not_supported_by_current_intake'),
])
def test_singleton_unsupported_close_has_explicit_intake_blocker(close, reason):
    span = capture(row(0, close=close))['spans'][0]
    assert span['unsupported_close_reasons'] == [reason]
    assert span['segments'][0]['unsupported_close_reasons'] == [reason]
    assert not span['strict_import_ready']


def test_interruption_can_end_span_but_recovered_tail_is_separate():
    c = capture(row(0), row(1, close='INTERRUPTION'), row(2, close='RECOVERED_TAIL', seam=False))
    assert [(x['first_sequence'], x['last_sequence']) for x in c['spans']] == [(0, 1), (2, 2)]
    assert c['spans'][0]['last_close_reason'] == 'INTERRUPTION'
    assert c['spans'][1]['last_close_reason'] == 'RECOVERED_TAIL'
    assert 'recovered_tail' in c['boundaries'][0]['reasons']


@pytest.mark.parametrize('changes,reason', [
    ({'epoch': 1, 'boundary': 'ROUTE_CHANGE_UNCERTAIN', 'start': 0, 'seam': False}, 'epoch_boundary_unmapped'),
    ({'epoch': 1, 'boundary': 'RESTART', 'run': 'new', 'start': 0, 'seam': False}, 'epoch_boundary_unmapped'),
    ({'epoch': 1, 'boundary': 'RESTART', 'start': 0, 'seam': False}, 'restart_without_new_run'),
    ({'epoch': 1, 'boundary': 'UNKNOWN', 'run': 'new', 'start': 0, 'seam': False}, 'new_run_without_restart'),
    ({'epoch': 2, 'boundary': 'UNKNOWN', 'start': 0, 'seam': False}, 'missing_epoch_declarations'),
    ({'epoch': 1, 'boundary': 'START', 'start': 0, 'seam': False}, 'new_epoch_start_boundary'),
    ({'epoch': 1, 'boundary': 'UNKNOWN', 'seam': False}, 'new_epoch_nonzero_start'),
    ({'epoch': 1, 'boundary': 'UNKNOWN', 'start': 0}, 'cross_epoch_seam_conflict'),
])
def test_epoch_boundaries_never_get_a_duration_or_global_coordinate(changes, reason):
    c = capture(row(0), row(1, **changes))
    assert len(c['spans']) == 2
    boundary = c['boundaries'][0]
    assert reason in boundary['reasons']
    assert boundary['declared_frame_delta'] is None and boundary['physical_gap_duration_seconds'] is None
    assert c['spans'][1]['frame_start'] == changes.get('start', 4)


def test_epoch_regression_remains_unmapped():
    c = capture(row(0, epoch=2), row(1, epoch=1))
    assert 'epoch_regression' in c['boundaries'][0]['reasons']
    assert c['boundaries'][0]['declared_frame_delta'] is None


@pytest.mark.parametrize('start,reason,delta', [(7, 'declared_frame_gap', 3), (2, 'declared_frame_overlap', -2)])
def test_same_epoch_delta_is_declaration_only_not_physical_duration(start, reason, delta):
    c = capture(row(0), row(1, start=start))
    boundary = c['boundaries'][0]
    assert len(c['spans']) == 2 and reason in boundary['reasons']
    assert boundary['declared_frame_delta'] == dict(frames=delta, basis='declaration_only', physical_duration_proven=False)
    assert boundary['physical_gap_duration_seconds'] is None


@pytest.mark.parametrize('changes,reason', [
    ({'run': 'new'}, 'same_epoch_declaration_conflict'),
    ({'rate': 8000}, 'same_epoch_declaration_conflict'),
    ({'channels': 2}, 'same_epoch_declaration_conflict'),
    ({'requested': 'other'}, 'same_epoch_declaration_conflict'),
    ({'actual': 'other'}, 'same_epoch_declaration_conflict'),
    ({'boundary': 'UNKNOWN'}, 'same_epoch_declaration_conflict'),
    ({'target': 8, 'close': 'STOP'}, 'same_epoch_declaration_conflict'),
    ({'seam': False}, 'previous_seam_not_trusted'),
    ({'close': 'RECOVERED_TAIL', 'seam': False}, 'recovered_tail'),
])
def test_all_same_epoch_edge_refusals_split_spans(changes, reason):
    c = capture(row(0), row(1, **changes))
    assert len(c['spans']) == 2 and reason in c['boundaries'][0]['reasons']
    assert c['spans'][1]['entry_reasons'] == c['boundaries'][0]['reasons']
    if reason == 'same_epoch_declaration_conflict':
        assert c['boundaries'][0]['declared_frame_delta'] is None


def test_anchor_contradictions_keep_existing_split_semantics():
    a, z = anchored_rows()
    for record in (a, z):
        record['delivery']['wav_bytes'] = 52
    z['metadata']['anchors']['observations'][0]['domain'] = '00000000-0000-0000-0000-000000000002'
    c = capture(a, z)
    assert len(c['spans']) == 2
    assert 'anchor_clock_domain_changed' in c['boundaries'][0]['reasons']


def test_missing_prefix_and_ordinals_remain_visible():
    c = capture(row(2), row(5, close='STOP'))
    assert c['prefix_not_supplied'] and c['first_supplied_sequence'] == 2
    assert 'prefix_not_supplied' in c['leading_observation']['reasons']
    assert c['leading_observation']['physical_gap_duration_seconds'] is None
    assert c['boundaries'][0]['missing_ordinal_count'] == 2
    assert 'missing_ordinals' in c['boundaries'][0]['reasons']
    assert not c['capture_completeness_proven']


def test_single_odd_initial_declaration_and_final_rotation_not_hidden():
    c = capture(row(0, epoch=4, start=9, boundary='RESTART', seam=True))
    assert set(c['leading_observation']['reasons']) == {
        'first_supplied_frame_nonzero', 'predecessor_not_supplied', 'initial_epoch_not_zero', 'initial_boundary_not_start'}
    assert c['last_supplied_close_reasons'] == ['ROTATION']
    assert not c['capture_completeness_proven'] and not c['strict_import_ready']


def test_distinct_captures_are_never_joined_and_raw_metadata_not_exposed():
    rows = [row(0, capture='private-capture-one', run='private-run', requested='private-request', actual='private-device'),
            row(0, capture='private-capture-two')]
    result = report(*rows)
    assert len(result['captures']) == 2 and all(len(c['spans']) == 1 for c in result['captures'])
    raw = b._json(result)
    assert all(secret not in raw for secret in ['private-capture', 'private-run', 'private-request', 'private-device'])
    assert '"capture_id":' not in raw and '"run_id":' not in raw


def test_every_report_and_partition_is_non_authoritative():
    result = report(row(0), row(1), row(1, pcm='c' * 64), row(3, close='STOP'))
    objects = [result]
    for c in result['captures']:
        objects += [c] + c['spans'] + c['quarantines'] + c['boundaries']
    assert all(o['physical_continuity_proven'] is False and o['strict_import_ready'] is False for o in objects)
    assert result['hardware_frame_mapping'] == 'UNESTABLISHED'
    assert result['gap_repair_performed'] is False and result['epoch_ingestion_supported'] is False


def files(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
            for p in root.rglob('*') if p.is_file()}


def test_snapshot_integration_is_read_only_for_sources_and_snapshots(tmp_path):
    a = fixture(tmp_path / 'a', close='INTERRUPTION')
    z = fixture(tmp_path / 'z', 1, epoch=1, boundary='RESTART', run='new', start=0, seam=False, close='STOP')
    before = files(tmp_path)
    result = b.report_snapshots([z, a, a])
    assert result['verified_input_wav_bytes'] == 156
    c = result['captures'][0]
    assert c['same_content_repeat_count'] == 1 and c['unambiguous_observed_wav_bytes'] == 104
    assert len(c['spans']) == 2 and c['spans'][0]['last_close_reason'] == 'INTERRUPTION'
    assert files(tmp_path) == before
    assert str(tmp_path) not in b._json(result)


@pytest.mark.parametrize('file_name', ['audio.wav', 'segment.v1.bin', 'snapshot.json'])
def test_malformed_or_changed_snapshot_refuses_whole_report(tmp_path, file_name):
    a = fixture(tmp_path / 'a')
    z = fixture(tmp_path / 'z', 1)
    (z / file_name).write_bytes(b'changed')
    before = files(tmp_path)
    with pytest.raises(s.Refused):
        b.report_snapshots([a, z])
    assert files(tmp_path) == before


@pytest.mark.parametrize('paths', [[], [None] * 33, 'not-a-list'])
def test_snapshot_count_bounds(paths):
    with pytest.raises(s.Refused, match='snapshot_count'):
        b.report_snapshots(paths)


def test_max_snapshot_count_and_json_bound(tmp_path):
    a = fixture(tmp_path / 'a')
    result = b.report_snapshots([a] * 32)
    assert result['snapshot_input_count'] == 32
    assert result['captures'][0]['same_content_repeat_count'] == 31
    assert result['captures'][0]['unambiguous_observed_wav_bytes'] == 52
    assert len(b._json(result).encode('ascii')) < b.MAX_REPORT_BYTES


def test_shared_aggregate_byte_precheck(tmp_path, monkeypatch):
    a = fixture(tmp_path / 'a')
    monkeypatch.setattr(s, 'MAX_INPUT_BYTES', 103)
    with pytest.raises(s.Refused, match='input_bytes'):
        b.report_snapshots([a, a])


@pytest.mark.parametrize('seconds', [0, -1, 121, True, float('nan'), float('inf')])
def test_deadline_cannot_be_disabled_or_extended(seconds):
    with pytest.raises(s.Refused, match='deadline_bounds'):
        b.report_snapshots([], deadline_seconds=seconds)


def test_deadline_is_checked_after_partition(tmp_path, monkeypatch):
    a = fixture(tmp_path / 'a')
    expired = False
    original = b._partition_records
    def remaining():
        if expired:
            raise s.Refused('snapshot_deadline_exceeded')
        return 120
    def partition(rows):
        nonlocal expired
        result = original(rows)
        expired = True
        return result
    monkeypatch.setattr(b, '_deadline', lambda seconds: remaining)
    monkeypatch.setattr(b, '_partition_records', partition)
    with pytest.raises(s.Refused, match='deadline_exceeded'):
        b.report_snapshots([a])


def test_output_bound_fails_closed(tmp_path, monkeypatch):
    a = fixture(tmp_path / 'a')
    monkeypatch.setattr(b, 'MAX_REPORT_BYTES', 1)
    with pytest.raises(s.Refused, match='output_bytes'):
        b.report_snapshots([a])


def test_cli_json_and_path_free_failure(tmp_path, capsys):
    a = fixture(tmp_path / 'a')
    assert b.main([str(a)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['schema'] == b.SCHEMA and not output['strict_import_ready']
    assert str(tmp_path) not in json.dumps(output)
    assert b.main([str(tmp_path / 'private-missing-snapshot')]) == 2
    failed = capsys.readouterr().out
    assert 'private-missing-snapshot' not in failed and str(tmp_path) not in failed
    assert json.loads(failed)['error'] == 'boundary_report_file_access_failed'


def test_report_module_does_not_import_intake_or_network_clients():
    text = Path(b.__file__).read_text()
    assert 'from .recorder_capture_intake' not in text
    assert 'import requests' not in text and 'import httpx' not in text
