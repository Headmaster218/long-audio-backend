"""Pure model-free primary-probe metadata contracts, not live authority tests.

All packets in this file are explicitly constructed numeric fixtures. The real
producer/receipt/lease path is tested separately in the pipeline test module.
No fixture here is allowed to stand in for current source authority.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib

import pytest

from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import recording_primary_probe as probe
from audio_backend import recording_primary_provenance as provenance
from test_bounded_core_support import (
    EPOCH, RUN, synthetic_case, ordinary_origin, primary_row, final_cell,
)

RATE = cells.RATE
SPACE = '7' * 64


def packet_header(packet, *, sequence=1):
    """Numeric serialization fixture; never inserts or blesses live evidence."""
    raw = provenance.bounded(packet)
    part = packet['commit']['part']
    digest = hashlib.sha256(raw).hexdigest()
    slot = core.slot(packet['commit'])
    header = {'sequence': sequence, 'sid': part['session_id'], 'rid': 'synthetic-recording',
        'cid': part['chunk_id'], 'core_start': part['source_start'],
        'core_end': part['source_end'], 'core_slot': slot, 'digest': digest}
    header['charge'] = provenance._charge(raw, header['sid'], header['rid'], header['cid'], slot, digest)
    provenance.validate_packet(packet, header)
    return {'header': header, 'packet': packet}


def synthetic_primary_case(*, clean_seconds=3., unknown=True):
    """Return (case, bounded_cell, [saved_packet]) with three disjoint regions.

    The first core owns the primary crop. Two later 4-second regions permit
    independent MOSS observations. Repeated label text across the primary and
    live query belongs to different explicit capture namespaces.
    """
    clean = round(clean_seconds * RATE)
    overlap_end = clean + RATE // 4
    case = synthetic_case([0, 4 * RATE, 8 * RATE, 12 * RATE], [(clean, overlap_end)])
    labels = [EPOCH + ':speaker_' + str(i) for i in range(1, 5)]
    geometry = [(2, 0, 0, overlap_end), (6, 0, RATE // 8, overlap_end),
                (9, 1, clean, overlap_end), (11, 2, 4 * RATE, 8 * RATE),
                (12, 3, 8 * RATE, 12 * RATE)]
    turns = [{'label': labels[label], 'original': [a, b]} for _, label, a, b in geometry]
    case['turns'] = deepcopy(turns)
    case['query']['turns'] = deepcopy(turns)
    case['target'] = core.original_target(ordinary_origin(case))
    cell = final_cell(case, [primary_row(1, 0, 12 * RATE)])
    units = [[5, 7, .5, .625, .5, 0, [2, 6]]]
    rows = [[5, [0]]]
    refs = [['p.1.' + 'a' * 64, None]]
    if unknown:
        anchor = clean_seconds + .125
        units.append([9, 13, anchor, anchor + .0625, anchor, None, [2, 6, 9]])
        rows.append([9, [1]])
        refs.append(['p.2.' + 'b' * 64, None])
    packet = {'schema': provenance.SCHEMA, 'commit': deepcopy(case['commits'][0]),
        'query': [0, 12 * RATE, 0, 12 * RATE, 12 * RATE, 9, True],
        'owned_pcm16le_sha256': hashlib.sha256(b'\0\0' * (4 * RATE)).hexdigest(),
        'labels': labels,
        'turns': [[ordinal, label, a, b, a / RATE, b / RATE]
                  for ordinal, label, a, b in geometry],
        'units': units, 'rows': rows, 'refs': refs}
    saved = packet_header(packet)
    probe.validate_packets(cell.proof, [saved])
    return case, cell, [saved]


def test_original_ordinals_clean_crop_and_independent_capture_namespaces():
    case, cell, packets = synthetic_primary_case()
    candidates, witnesses, outcomes = probe.primary_candidates(
        cell.support, cell.pcm, cell.proof, packets, SPACE)
    assert len(candidates) == 1
    node, pcm = candidates[0]
    assert (node['start_frame'], node['end_frame']) == (8000, 40000)
    assert len(pcm) == 64000 and pcm == b'\0\0' * 32000
    assert node['source_kind'] == 'native_primary'
    assert node['primary_source_ref'] == packets[0]['packet']['refs'][0][0]
    assert node['original_parent_ordinal'] == 5
    assert node['original_unit_ordinals'] == [[5, 7]]
    assert node['original_turn_ordinals'] == [2, 6]
    assert 'phrase_index' not in node and 'moss_tag' not in node
    native, query = node['native_attributions']
    assert native['native_scope_id'] != query['native_scope_id']
    assert native['native_label'] == EPOCH + ':speaker_1'
    assert query['native_label'] == RUN + ':' + native['native_label']
    assert node['source_spans'] == [dict(case['commits'][0]['part'],
        source_start=8000, source_end=40000, logical_start=8000, logical_end=40000)]
    assert outcomes == [{'primary_source_ref': packets[0]['packet']['refs'][0][0], 'status': 'candidate'},
        {'primary_source_ref': packets[0]['packet']['refs'][1][0], 'status': 'ambiguous_native_label'}]
    assert witnesses == [{'native_scope_id': native['native_scope_id'],
        'labels': [EPOCH + ':speaker_1', EPOCH + ':speaker_2'],
        'overlap_frames': [3 * RATE, 3 * RATE + RATE // 4],
        'proof_sha256': packets[0]['header']['digest']}]
    assert probe.validate_primary_node(node, cell.proof, packets) == node


def test_no_two_second_clean_crop_retains_unknown_outcome_and_overlap_witness():
    _, cell, packets = synthetic_primary_case(clean_seconds=2.)
    candidates, witnesses, outcomes = probe.primary_candidates(
        cell.support, cell.pcm, cell.proof, packets, SPACE)
    assert candidates == []
    assert [item['status'] for item in outcomes] == [
        'insufficient_clean_support', 'ambiguous_native_label']
    assert len(witnesses) == 1 and witnesses[0]['overlap_frames'] == [32000, 36000]


def test_exact_erosion_threshold_without_padding_or_concatenation():
    for seconds, count in ((2.1999375, 0), (2.2, 1)):
        _, cell, packets = synthetic_primary_case(clean_seconds=seconds, unknown=False)
        candidates, _, _ = probe.primary_candidates(cell.support, cell.pcm, cell.proof, packets, SPACE)
        assert len(candidates) == count
        if count:
            assert (candidates[0][0]['start_frame'], candidates[0][0]['end_frame']) == (1600, 33600)


@pytest.mark.parametrize('field', ['original_parent_ordinal', 'original_unit_ordinals',
    'original_turn_ordinals', 'source_spans', 'start_frame', 'primary_packet_sha256'])
def test_original_primary_node_fields_cannot_be_rewritten(field):
    _, cell, packets = synthetic_primary_case()
    candidates, _, _ = probe.primary_candidates(cell.support, cell.pcm, cell.proof, packets, SPACE)
    node = deepcopy(candidates[0][0])
    node[field] = None
    with pytest.raises(cells.Unavailable, match='primary_probe_node_binding'):
        probe.validate_primary_node(node, cell.proof, packets)


@pytest.mark.parametrize('field,value', [('phrase_index', 0), ('moss_tag', 'speaker_1')])
def test_native_primary_cannot_fabricate_a_moss_phrase(field, value):
    _, cell, packets = synthetic_primary_case()
    candidates, _, _ = probe.primary_candidates(cell.support, cell.pcm, cell.proof, packets, SPACE)
    node = dict(candidates[0][0], **{field: value})
    with pytest.raises(cells.Unavailable, match='primary_probe_fabricated_phrase'):
        probe.validate_primary_node(node, cell.proof, packets)


def test_exact_packet_source_mapping_and_digest_required():
    _, cell, packets = synthetic_primary_case()
    tampered = deepcopy(packets)
    tampered[0]['packet']['owned_pcm16le_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='header binding'):
        probe.validate_packets(cell.proof, tampered)
    packet = deepcopy(packets[0]['packet'])
    packet['commit']['part']['source_start'] += 1
    packet['commit']['part']['source_end'] += 1
    remapped = packet_header(packet)
    with pytest.raises(cells.Unavailable, match='primary_probe_packet_binding'):
        probe.validate_packets(cell.proof, [remapped])
    with pytest.raises(cells.Unavailable, match='primary_probe_support_binding'):
        probe.primary_metadata(replace(cell.support, core_sha256='f' * 64), cell.proof, packets, SPACE)


def test_packet_and_turn_capacity_reject_without_partial_acceptance():
    _, cell, packets = synthetic_primary_case()
    with pytest.raises(cells.Unavailable, match='primary_probe_packet_binding'):
        probe.validate_packets(cell.proof, packets * 2)
    with pytest.raises(cells.Unavailable, match='primary_probe_packet_capacity'):
        probe.validate_packets(cell.proof, packets * 5)
    packet = deepcopy(packets[0]['packet'])
    # Additional raw turns are same-label duplicates, not synthetic identities.
    packet['turns'] += [[ordinal, 0, 0, 4 * RATE, 0., 4.] for ordinal in range(13, 265)]
    for unit in packet['units']:
        unit[6] = [t[0] for t in packet['turns'] if t[4] - .03 <= unit[2] <= t[5] + .03]
    saved = packet_header(packet)
    assert len(packet['turns']) == probe.MAX_PACKET_TURNS + 1
    with pytest.raises(cells.Unavailable, match='primary_probe_packet_binding'):
        probe.validate_packets(cell.proof, [saved])


def test_named_slots_are_proof_bounded_and_plain_ordinary_proof_is_ineligible():
    case, cell, _ = synthetic_primary_case()
    assert probe.named_commits(cell.proof) == tuple(case['commits'])
    ordinary = ordinary_origin(case)['proof']
    assert probe.named_commits(ordinary.encode('ascii')) == ()
