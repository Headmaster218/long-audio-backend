"""Source-authored, finite, model-free bounded-support contracts.

All PCM and native responses below are synthetic. No historical database,
recording, model, server or download is an input. Saved cases contain only
integer geometry from the frozen review; they do not assert new acoustics or
proper partial-parent output. Execution requires the separately approved gate.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json

import pytest

from audio_backend import lexical_evidence as lexical
from audio_backend import moss_automatic as automatic
from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import transcript_contract as contract
from audio_backend import transcript_projection as projection
from test_native_owned_support import synthetic_provenance


SID = 'bounded-synthetic-session'
EPOCH = 'a' * 32
RUN = '1' * 32


def synthetic_case(boundaries, components, *, origin_index=0, origin_support=None,
                   origin_seconds=0., run=RUN, epoch=EPOCH,
                   source_frames=None, separate_sources=False):
    """Small immutable synthetic observations; never a live authority claim."""
    total = boundaries[-1]
    source_frames = total if source_frames is None else source_frames
    finished = source_frames == total
    provenance = synthetic_provenance()
    observed = {'epoch_id': epoch, 'finished': finished,
        'fingerprint': provenance['diarization_fingerprint'], 'local_capacity': 8}
    for name in ('input', 'processed', 'stable'):
        raw = total / cells.RATE
        observed[name] = {'raw': raw, 'hex': raw.hex(), 'frame': total}
    native = {'epoch_id': epoch, 'epoch_index': 0, 'replay_start': 0,
        'state': observed, 'provenance': provenance, 'observed_action_count': 9, 'true_eof': finished}
    primary_native = {'epoch_id': epoch, 'epoch_index': 0,
        'owned_start': origin_seconds, 'owned_end': origin_seconds + 300.,
        'replay_start': origin_seconds, 'input_until': origin_seconds + total / cells.RATE,
        'processed_until': origin_seconds + total / cells.RATE,
        'stable_until': origin_seconds + total / cells.RATE,
        'finished': finished, 'provenance': provenance}
    scope = {'session_id': SID, 'kind': 'stream', 'key': 'synthetic-stream',
        'origin': float(origin_seconds).hex(), 'run': run, 'reset': 1, 'epoch': epoch, 'index': 0}
    turns = [{'label': epoch + ':speaker_1', 'original': [components[0][0], components[-1][1]]}]
    turns += [{'label': epoch + ':speaker_2', 'original': list(pair)} for pair in components]
    commits, evidences, sources = [], [], {}
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        cid = 'source-' + str(index) if separate_sources else 'source'
        local_start, local_end = (0, end - start) if separate_sources else (start, end)
        frames = end - start if separate_sources else source_frames
        source_start = start if separate_sources else 0
        checksum = hashlib.sha256(b'\0\0' * frames).hexdigest()
        part = cells.SourceSpan(SID, cid, cells.sha({'receipt': cid}), checksum,
            frames, local_start, local_end, start, end)
        evidence = {'chunk_id': cid, 'start': local_start, 'end': local_end,
            'native': deepcopy(primary_native), 'core_cut': {'nominal': local_end / cells.RATE,
                'actual': local_end / cells.RATE, 'reason': 'fixed_or_final'}}
        commits.append({'scope': deepcopy(scope), 'part': asdict(part),
            'native_sha256': cells.sha(evidence['native']), 'cut_sha256': cells.sha(evidence['core_cut']),
            'evidence_sha256': cells.sha(evidence), 'runtime_sha256': cells.sha(provenance)})
        evidences.append(evidence)
        sources[cid] = {'id': cid, 'session_id': SID, 'digest': checksum,
            'timeline_start': origin_seconds + source_start / cells.RATE, 'duration': frames / cells.RATE}
    query = {'query_start_frame': 0, 'query_end_frame': total,
        'query_pcm16le_sha256': hashlib.sha256(b'\0\0' * total).hexdigest(),
        'support_pcm16le_sha256': hashlib.sha256(b'\0\0' * total).hexdigest(),
        'native': deepcopy(native), 'turns': deepcopy(turns)}
    case = {'commits': commits, 'evidences': evidences, 'sources': sources,
        'native': native, 'turns': turns, 'query': query, 'origin': origin_seconds,
        'components': [list(pair) for pair in components]}
    case['target'] = core.original_target(ordinary_origin(case, origin_index, origin_support))
    return case


def ordinary_origin(case, index=0, support=None):
    commit = case['commits'][index]; p = commit['part']
    start, end = (p['logical_start'], p['logical_end']) if support is None else support
    parts = core.bounded_mapping([commit], start, end)
    pcm = b'\0\0' * (end - start); contents = [part.content() for part in parts]
    evidence = case['evidences'][index]; native = case['native']
    support = cells.SourceSupport(cells.sha({'schema': 'immutable-source-cell-generation-v1', 'sources': contents}),
        cells.sha({'schema': 'bounded-cell-mapping-v1', 'parts': contents}), commit['runtime_sha256'],
        cells.sha({'start': p['logical_start'], 'end': p['logical_end'],
            'commit': commit['evidence_sha256'], 'native': native}), max(p['source_frames'], p['logical_end']),
        p['logical_start'], p['logical_end'], start, end,
        hashlib.sha256(pcm).hexdigest(), cells.float32_digest(pcm))
    selected, turns = cells.selection(tuple(cells.Turn(*turn['original'], turn['label'])
        for turn in case['turns']), start, end)
    assert selected
    doc = {'policy': cells.POLICY_SHA256, 'support': support.to_dict(),
        'dependencies': [asdict(part) for part in parts], 'native': native, 'turns': turns,
        'selected': True, 'commit_evidence': evidence, 'commit': core.anchor(commit)}
    proof = cells.canonical(doc)
    cells.validate_document(proof)
    binding = automatic.source_binding('ordinary', doc, support, parts, [commit])
    return core.origin_certificate(proof, [commit], binding)


def seam_origin(case, left_index):
    commits = case['commits'][left_index:left_index + 2]
    shape = core.geometry(commits)
    pcm = b'\0\0' * (shape['end_frame'] - shape['start_frame'])
    query = dict(case['query'], support_pcm16le_sha256=hashlib.sha256(pcm).hexdigest())
    cell = core.make_cell(commits, query, case['evidences'][left_index + 1], pcm)
    doc = json.loads(cell.proof)
    binding = automatic.source_binding('core_seam', doc, cell.support, cell.parts, commits)
    return core.origin_certificate(cell.proof, commits, binding)


def primary_row(ident, start, end, *, cid='source', origin=0., text='synthetic', raw=None):
    return {'id': ident, 'chunk_id': cid, 'start': origin + start / cells.RATE,
        'end': origin + end / cells.RATE, 'text': text, 'raw_text': text if raw is None else raw,
        'asr_backend': 'sensevoice', 'timestamp_kind': lexical.PRIMARY_TIMESTAMP,
        'confidence': .8, 'speaker_local_id': 'source-local', 'speaker_window_start': 0.,
        'lexical_evidence': None}


def snapshot_for(case, rows, *, commits=None):
    commits = case['commits'] if commits is None else commits
    return core.lexical_snapshot(SID, rows, case['sources'], case['origin'],
        scan_frames=core.bounded_chain(commits))


def final_cell(case, rows, *, query=None, target=None):
    target = case['target'] if target is None else target
    snapshot = snapshot_for(case, rows)
    query = deepcopy(case['query'] if query is None else query)
    shape = core.select_support(target, case['commits'], snapshot,
        query['query_start_frame'], query['query_end_frame'])
    pcm = b'\0\0' * (shape['end_frame'] - shape['start_frame'])
    query['support_pcm16le_sha256'] = hashlib.sha256(pcm).hexdigest()
    query['capture_run'] = case['commits'][-1]['scope']['run']
    query['turns'] = [{**turn, 'label': query['capture_run'] + ':' + turn['label']} for turn in query['turns']]
    return core.make_bounded_cell(case['commits'], query, case['evidences'][-1], pcm, target, snapshot)


def lexical_row(case, ident, units):
    """Canonical synthetic retained inventory, with exact raw/display spans."""
    units = deepcopy(units)
    text = ''.join(item['text'] for item in units)
    raw = ''.join(item.get('raw_text', item['text']) for item in units)
    row = primary_row(ident, round(units[0]['start'] * cells.RATE),
        round(max(item['end'] for item in units) * cells.RATE), origin=case['origin'], text=text, raw=raw)
    display_at = raw_at = 0
    for item in units:
        item.setdefault('raw_text', item['text'])
        item.update(ownership_anchor=item['start'], anchor_kind='ctc_emission',
            display_span=[display_at, display_at + len(item['text'])],
            raw_span=[raw_at, raw_at + len(item['raw_text'])],
            granular_eligible=lexical._group_supported(item['text']) and lexical._group_supported(item['raw_text']))
        display_at += len(item['text']); raw_at += len(item['raw_text'])
    source = case['sources']['source']
    part = {**source, 'seek_seconds': 0., 'requested_seconds': source['duration'],
        'analyzer_frames': [0, round(source['duration'] * cells.RATE)]}
    value = {'schema': lexical.SCHEMA, 'primary_sha256': lexical.primary_sha(SID, row),
        'source_provenance': lexical.SOURCE_NOTE, 'source_parts': [part],
        'timestamp_semantics': lexical.TIME_NOTE,
        'coordinates': {'axis': 'chunk_relative_seconds', 'core_start': 0.,
            'core_end': source['duration'], 'analyzer_start': 0.}, 'units': units}
    row['lexical_evidence'] = lexical.encoded(value).decode('ascii')
    return row


@pytest.mark.parametrize('count', [2, 3, 4])
def test_complete_bounded_family_accepts_two_three_four_original_slots(count):
    case = synthetic_case([10 * i for i in range(count + 1)], [(2, 8)])
    cell = final_cell(case, [primary_row(1, 0, count * 10)])
    doc, support, parts = cells.validate_document(cell.proof)
    assert doc['schema'] == core.BOUNDED_SCHEMA
    assert (support.start_frame, support.end_frame) == (0, count * 10)
    assert len(parts) == 1  # Same-upload PCM coalesces; all slots remain roots.
    assert len({root['slot'] for root in doc['dependency_roots'] if root['slot']}) == count
    assert doc['publication_anchor'] == core.anchor(case['commits'][-1])
    assert doc['target']['identity']['components'] == [[2, 8]]
    assert cell.pcm == b'\0\0' * (count * 10)
    if count != 2:
        with pytest.raises(ValueError):core.geometry(case['commits'], core.SCHEMA)
    else:
        assert core.geometry(case['commits'], core.SCHEMA)['seam_frame'] == 10


def test_fifth_one_frame_commit_rejected_even_under_twenty_seconds():
    case = synthetic_case([0, 10, 20, 30, 40, 41], [(2, 8)])
    with pytest.raises(cells.Unavailable, match='commitment_capacity'):
        core.bounded_chain(case['commits'])


def test_origin_only_slot_counts_toward_the_same_four_root_ceiling():
    case = synthetic_case([0, 10, 20, 30, 40, 50, 60], [(22, 28)], origin_index=2)
    target = core.original_target(seam_origin(case, 1))
    final_commits = case['commits'][2:]
    snapshot = snapshot_for(case, [primary_row(1, 22, 60)], commits=final_commits)
    assert len(final_commits) == 4
    with pytest.raises(cells.Unavailable, match='commitment_capacity'):
        core.select_support(target, final_commits, snapshot, 20, 60)


def test_full_disconnected_overlap_sweep_and_label_rules():
    turns = [cells.Turn(1, 20, 'a'), cells.Turn(3, 5, 'b'), cells.Turn(9, 12, 'b'),
             cells.Turn(11, 15, 'c'), cells.Turn(15, 19, 'c'), cells.Turn(1, 20, 'a')]
    assert core.overlap_components(turns, 0, 30) == [[3, 5], [9, 19]]
    assert core.overlap_components(tuple(reversed(turns)), 4, 16) == [[4, 5], [9, 16]]
    for absent in ([cells.Turn(0, 8, 'a'), cells.Turn(8, 16, 'b')],
                   [cells.Turn(0, 16, 'a'), cells.Turn(0, 16, 'a')]):
        with pytest.raises(cells.Unavailable, match='no_same_response'):core.overlap_components(absent, 0, 16)


def test_target_key_excludes_support_run_epoch_and_local_names_but_not_mapping():
    first = synthetic_case([0, 40], [(12, 18)], origin_support=(0, 25))
    second = synthetic_case([0, 40], [(12, 18)], origin_support=(10, 30), run='2' * 32, epoch='b' * 32)
    assert first['target']['key'] == second['target']['key']
    assert first['target']['origins'][0]['proof'] != second['target']['origins'][0]['proof']
    different = synthetic_case([0, 40], [(12, 19)], origin_support=(0, 25))
    assert different['target']['key'] != first['target']['key']
    original = deepcopy(first['target'])
    with pytest.raises(cells.Unavailable, match='^core_slot_evidence_conflict$'):
        core.append_target_origin(first['target'], second['target']['origins'][0])
    assert first['target'] == original
    # A different original compact slot preserves the same overlap mapping,
    # so its individually valid origin reaches the separate scope check.
    transition = synthetic_case([0, 10, 40], [(12, 18)], origin_index=1,
        origin_support=(10, 30), run='2' * 32, epoch='b' * 32)
    assert transition['target']['key'] == first['target']['key']
    assert core.slot(transition['commits'][1]) != core.slot(first['commits'][0])
    with pytest.raises(cells.Unavailable, match='^unresolved_scope_transition$'):
        core.append_target_origin(first['target'], transition['target']['origins'][0])
    assert first['target'] == original
    changed = deepcopy(second['target']); changed['identity']['mapping'][0][0]['source_start'] += 1
    with pytest.raises(cells.Unavailable, match='target_identity'):core.validate_target(changed)


def test_eight_origin_certificate_ceiling_does_not_drop_first_evidence():
    case = synthetic_case([0, 50], [(12, 18)], origin_support=(0, 30))
    target = case['target']; first = deepcopy(target['origins'][0])
    for start in range(1, 8):
        target = core.append_target_origin(target, ordinary_origin(case, support=(start, start + 30)))
    assert len(target['origins']) == 8 and target['origins'][0] == first
    with pytest.raises(cells.Unavailable, match='origin_capacity'):
        core.append_target_origin(target, ordinary_origin(case, support=(8, 38)))


@pytest.mark.parametrize('name,boundaries,components,ordinary_index,seam_index', [
    ('A', [0, 168000, 305920], [(138256, 165744)], 0, 0),
    ('B', [168000, 305920, 336000], [(332336, 336000)], 1, 0),
])
def test_saved_a_b_aliases_share_exact_original_target(name, boundaries, components, ordinary_index, seam_index):
    case = synthetic_case(boundaries, components, origin_index=ordinary_index, source_frames=672000)
    alias = core.original_target(seam_origin(case, seam_index))
    assert alias['key'] == case['target']['key'], name
    merged = core.append_target_origin(case['target'], alias['origins'][0])
    assert len(merged['origins']) == 2
    assert merged['origins'][0] == case['target']['origins'][0]
    assert core.append_target_origin(merged, alias['origins'][0]) == merged


# Geometry only, copied as integers from the accepted saved-case table. These
# are the explicit blocking atoms, not a reconstruction of all saved parents.
SAVED = [
    ((0, 168000), (0, 168000), [(138256, 165744)], [(158576, 172976)], False),
    ((8000, 305920), (8000, 305920), [(138256, 165744)], [(158576, 172976)], True),
    ((168000, 336000), (168000, 336000), [(332336, 336000)], [(332480, 344960), (333760, 347200)], False),
    ((305920, 336000), (305920, 336000), [(332336, 336000)], [(332480, 344960), (333760, 347200)], False),
    ((305920, 496000), (305920, 504000), [(332336, 344784), (366096, 391824), (487696, 494704)], [(488320, 505600)], False),
    ((336000, 504000), (336000, 504000), [(336000, 344784), (366096, 391824), (487696, 494704)], [(332480, 344960), (488320, 505600)], False),
    ((344000, 664000), (344000, 664000), [(344000, 344784), (366096, 391824), (487696, 494704)], [(332480, 344960), (333760, 347200)], False),
]


@pytest.mark.parametrize('original,domain,components,atoms,success', SAVED)
def test_seven_saved_original_domains_keep_six_refusals(original, domain, components, atoms, success):
    case = synthetic_case(list(domain), components, origin_support=original, source_frames=672000)
    snapshot = snapshot_for(case, [primary_row(i + 1, *pair) for i, pair in enumerate(atoms)])
    if success:
        shape = core.select_support(case['target'], case['commits'], snapshot, *domain)
        assert shape == {'start_frame': original[0], 'end_frame': original[1]}
    else:
        with pytest.raises(cells.Unavailable, match='authority_unavailable|witness_unavailable'):
            core.select_support(case['target'], case['commits'], snapshot, *domain)


def test_seven_saved_geometries_have_five_exact_target_identities():
    keys = {synthetic_case(list(domain), components, origin_support=original,
        source_frames=672000)['target']['key'] for original, domain, components, _, _ in SAVED}
    assert len(keys) == 5


def test_c_full_target_needs_third_core_and_b_only_crop_is_not_success():
    components = [(332336, 344784), (366096, 391824), (487696, 494704)]
    case = synthetic_case([305920, 336000, 504000, 672000], components,
        origin_index=1, origin_support=(336000, 504000))
    # C's origin is the actual two-core seam, preserving its left component.
    case['target'] = core.original_target(seam_origin(case, 0))
    # Synthetic parents reproduce the reviewed safe enclosure. No saved
    # parent content or unobserved current native response is replayed.
    rows = [primary_row(1, 310400, 344960), primary_row(2, 333760, 347200),
            primary_row(3, 366096, 391824), primary_row(4, 488320, 505600)]
    cell = final_cell(case, rows)
    assert (cell.support.start_frame, cell.support.end_frame) == (310400, 505600)
    assert json.loads(cell.proof)['target']['identity']['components'] == [list(pair) for pair in components]
    assert any(end > 347200 for _, end in components)  # The B-only crop loses C.
    query = deepcopy(case['query']); query['turns'] = query['turns'][:2]
    with pytest.raises(cells.Unavailable, match='current_target_disagreement'):
        final_cell(case, rows, query=query)


@pytest.mark.parametrize('end,accepted', [(320000, True), (320032, False)])
def test_exact_twenty_second_cap_preserves_whole_atom(end, accepted):
    case = synthetic_case([0, 160000, end], [(100, 200)])
    snapshot = snapshot_for(case, [primary_row(1, 0, end)])
    if accepted:
        assert core.select_support(case['target'], case['commits'], snapshot, 0, end) == {
            'start_frame': 0, 'end_frame': end}
    else:
        with pytest.raises(cells.Unavailable, match='support_duration'):
            core.select_support(case['target'], case['commits'], snapshot, 0, end)


def test_nested_and_overlapping_opaque_atoms_reach_one_fixed_point():
    case = synthetic_case([0, 20, 40], [(10, 11)])
    snapshot = snapshot_for(case, [primary_row(1, 8, 12), primary_row(2, 10, 25),
        primary_row(3, 23, 35), primary_row(4, 0, 4)])
    assert core.select_support(case['target'], case['commits'], snapshot, 0, 40) == {
        'start_frame': 8, 'end_frame': 35}


def test_fixed_point_reuses_ordinal_grouping_and_nonempty_gap_refusal(monkeypatch):
    case = synthetic_case([0, 64000], [(17600, 19200)], origin_support=(16000, 24000))
    row = lexical_row(case, 1, [{'text': '甲', 'start': 0., 'end': .5},
        {'text': '，', 'start': .2, 'end': .4}, {'text': '乙', 'start': 2., 'end': 3.}])
    original_current, original_groups = lexical.current_inventory, projection.lexical_groups
    calls = []
    def current(*args, **kwargs):calls.append('current');return original_current(*args, **kwargs)
    def groups(*args, **kwargs):calls.append('groups');return original_groups(*args, **kwargs)
    monkeypatch.setattr(lexical, 'current_inventory', current)
    monkeypatch.setattr(projection, 'lexical_groups', groups)
    snapshot = snapshot_for(case, [row])
    assert calls == ['current', 'groups']
    assert [atom['unit_range'] for atom in snapshot['atoms']] == [[0, 2], [2, 3]]
    with pytest.raises(cells.Unavailable, match='unresolved_lexical_gap'):
        core.select_support(case['target'], case['commits'], snapshot, 0, 64000)
    # A larger legal interval exists; the bounded fixed-point policy still refuses.
    assert projection._legal(0, 48000, snapshot['atoms'])


def test_equal_anchors_punctuation_raw_display_and_separate_tracks_remain_exact():
    case = synthetic_case([0, 64000], [(8000, 16000)])
    row = lexical_row(case, 1, [{'text': '臺', 'raw_text': '台', 'start': .5, 'end': 1.},
        {'text': '，', 'start': .5, 'end': .8}, {'text': '灣', 'raw_text': '湾', 'start': .5, 'end': 1.5},
        {'text': '好', 'start': 2., 'end': 3.}])
    snapshot = snapshot_for(case, [row])
    assert [atom['unit_range'] for atom in snapshot['atoms']] == [[0, 3], [3, 4]]
    assert snapshot['parents'][0]['row']['text'] == '臺，灣好'
    assert snapshot['parents'][0]['row']['raw_text'] == '台，湾好'
    other = deepcopy(row); other['id'] = 2; value = json.loads(other['lexical_evidence'])
    value['primary_sha256'] = lexical.primary_sha(SID, other)
    other['lexical_evidence'] = lexical.encoded(value).decode('ascii')
    both = snapshot_for(case, [row, other])
    assert [atom['segment_id'] for atom in both['atoms']] == [1, 1, 2, 2]
    assert len(both['parents']) == 2  # Equal text never merges simultaneous parents.


@pytest.mark.parametrize('mode', ['null', 'malformed', 'stale', 'punctuation', 'order', 'tracks'])
def test_unsuitable_or_missing_inventory_is_whole_known_opaque_parent(mode):
    case = synthetic_case([0, 64000], [(8000, 16000)])
    row = lexical_row(case, 1, [{'text': '甲', 'start': .5, 'end': 1.}, {'text': '乙', 'start': 2., 'end': 3.}])
    if mode == 'null':row['lexical_evidence'] = None
    elif mode == 'malformed':row['lexical_evidence'] = '{broken'
    else:
        value = json.loads(row['lexical_evidence'])
        if mode == 'stale':value['source_parts'][0]['digest'] = 'f' * 64
        elif mode == 'punctuation':
            row = lexical_row(case, 1, [{'text': '！', 'start': .5, 'end': 1.}])
            value = json.loads(row['lexical_evidence'])
        elif mode == 'order':value['units'][1]['start'] = value['units'][1]['ownership_anchor'] = .25
        elif mode == 'tracks':value['units'][0]['source_track_id'] = 'one';value['units'][1]['source_track_id'] = 'two'
        row['lexical_evidence'] = lexical.encoded(value).decode('ascii')
    snapshot = snapshot_for(case, [row])
    assert len(snapshot['atoms']) == 1
    assert snapshot['atoms'][0]['source'] == 'primary'
    assert snapshot['atoms'][0]['start_frame'] == round(row['start'] * cells.RATE)
    assert snapshot['atoms'][0]['end_frame'] == round(row['end'] * cells.RATE)


def test_missing_scan_unknown_source_gap_and_nonadjacent_alias_are_unresolved():
    case = synthetic_case([0, 10, 20, 30], [(2, 8)], separate_sources=True)
    with pytest.raises(cells.Unavailable, match='incomplete_primary'):
        core.lexical_snapshot(SID, [], case['sources'], 0., complete=False, scan_frames=[0, 30])
    with pytest.raises(cells.Unavailable, match='source_unavailable'):
        snapshot_for(case, [primary_row(1, 0, 8, cid='missing')])
    broken = deepcopy(case['commits']); broken[1]['part']['logical_start'] += 1;broken[1]['part']['source_start'] += 1
    with pytest.raises(cells.Unavailable, match='source_gap'):core.bounded_chain(broken)
    original = deepcopy(case)
    duplicate = deepcopy(case['commits']); duplicate[-1]['part']['chunk_id'] = duplicate[0]['part']['chunk_id']
    with pytest.raises(cells.Unavailable, match='^bounded_duplicate_commitment$'):
        core.bounded_chain(duplicate)
    repeated = synthetic_case([0, 10, 20, 30], [(2, 8)])['commits']
    repeated[1] = deepcopy(case['commits'][1])
    assert len({core.slot(commit) for commit in repeated}) == 3
    for commit in repeated:core.validate_commit(commit)
    with pytest.raises(cells.Unavailable, match='^source_mapping_repeated_alias$'):
        core.bounded_chain(repeated)
    assert case == original
    fractional = deepcopy(case['commits']); fractional[0]['part']['source_end'] = 10.0
    with pytest.raises(cells.Unavailable, match='invalid_integer'):core.bounded_chain(fractional)
    with pytest.raises(cells.Unavailable, match='invalid_integer'):
        core.overlap_components([cells.Turn(0, 10, 'a'), cells.Turn(0, 10, 'b')], False, 10)


def test_aligned_nonzero_origin_and_fractional_origin_refusal():
    case = synthetic_case([0, 10, 20], [(2, 8)], origin_seconds=64.)
    cell = final_cell(case, [primary_row(1, 0, 20, origin=64.)])
    assert cell.support.start_frame == 0 and cell.support.end_frame == 20
    other = synthetic_case([0, 10, 20], [(2, 8)], origin_seconds=.00001)
    with pytest.raises(cells.Unavailable, match='fractional_frame_coordinate'):
        snapshot_for(other, [primary_row(1, 0, 20, origin=.00001)])


def test_actual_retained_ids_can_bind_but_inventory_or_atom_changes_cannot():
    case = synthetic_case([0, 64000], [(8000, 16000)])
    planned_row = lexical_row(case, (1 << 63) - 2,
        [{'text': '甲', 'start': .5, 'end': 1.}, {'text': '乙', 'start': 2., 'end': 3.}])
    actual_row = deepcopy(planned_row); actual_row['id'] = 9
    value = json.loads(actual_row['lexical_evidence']); value['primary_sha256'] = lexical.primary_sha(SID, actual_row)
    actual_row['lexical_evidence'] = lexical.encoded(value).decode('ascii')
    planned, actual = snapshot_for(case, [planned_row]), snapshot_for(case, [actual_row])
    assert core.same_numeric_snapshot(planned, actual, {planned_row['id']: 9})
    refused = snapshot_for(case, [dict(actual_row, lexical_evidence=None)])
    assert not core.same_numeric_snapshot(planned, refused, {planned_row['id']: 9})
    changed = deepcopy(actual); changed['atoms'][0]['end_frame'] += 1
    with pytest.raises(cells.Unavailable, match='inventory_mismatch'):core.validate_inventory(changed)


def test_pcm_source_mapping_query_and_frozen_target_tampering_fail_closed():
    case = synthetic_case([0, 10, 20, 30], [(2, 8)])
    cell = final_cell(case, [primary_row(1, 0, 30)])
    with pytest.raises(cells.Unavailable, match='cell_pcm_mismatch'):
        replace(cell, pcm=b'\1\0' + cell.pcm[2:])
    with pytest.raises(cells.Unavailable, match='cell_pcm_mismatch'):replace(cell, pcm=cell.pcm[:-2])
    for field in ('capture_run', 'query_pcm16le_sha256'):
        doc = json.loads(cell.proof)
        if field == 'capture_run':doc['query'][field] = '2' * 32
        else:doc['query'][field] = 'f' * 64
        with pytest.raises(cells.Unavailable):cells.validate_document(cells.canonical(doc))
    doc = json.loads(cell.proof); doc['dependencies'][0]['source_start'] += 1
    with pytest.raises(cells.Unavailable):cells.validate_document(cells.canonical(doc))
    doc = json.loads(cell.proof); doc['target']['identity']['components'] = [[2, 7]]
    with pytest.raises(cells.Unavailable):cells.validate_document(cells.canonical(doc))


# Frozen source52 pure oracle; no historical database or runtime is loaded.
_SOURCE52_CORE_SHA256 = '9f52933218006ba7ea5630dd8d4916b60038276fa52cbcd0d18022313cb45114'
_SOURCE52_INVENTORY_ORACLE = 'def lexical_snapshot(sid, rows, sources, scope_origin, *, complete=True, scan_frames=None):\n    from .native_owned_support_cache import ReadError\n    try:\n        return _lexical_snapshot(sid, rows, sources, scope_origin,\n            complete=complete, scan_frames=scan_frames)\n    except ReadError as exc:\n        # Shared projection validators use their HTTP/read-domain exception.\n        # Numeric planning keeps this an optional unresolved placement result.\n        raise Unavailable(exc.reason) from exc\n\ndef _lexical_snapshot(sid, rows, sources, scope_origin, *, complete=True, scan_frames=None):\n    """Pure adapter over the *existing* inventory and ordinal-group validators.\n\n    The caller supplies a complete bounded indexed scan and source snapshots.\n    No database or source I/O is hidden here, including for a stale inventory.\n    Retaining exact rows allows proof decoding to repeat identical semantics.\n    """\n    from .lexical_evidence import current_inventory, SOURCE_FIELDS, MAX_SEGMENT_BYTES\n    from .transcript_contract import PRIMARY_FIELDS, primary_evidence, primary_frame\n    from .transcript_projection import lexical_groups\n    from .transcript_store import CONTEXT_SECONDS\n    text(sid)\n    if complete is not True:raise Unavailable(\'incomplete_primary\')\n    if type(rows) not in (list, tuple) or len(rows) > MAX_PLAN_PARENTS:\n        raise Unavailable(\'primary_capacity\')\n    if type(sources) is not dict or not 1 <= len(sources) <= MAX_PARTS:\n        raise Unavailable(\'source_part_capacity\')\n    origin = exact_frame(scope_origin)\n    if type(scan_frames) not in (list, tuple) or len(scan_frames) != 2:\n        raise Unavailable(\'incomplete_primary\')\n    scan_start, scan_end = scan_frames; integer(scan_start); integer(scan_end)\n    if scan_start >= scan_end:raise Unavailable(\'incomplete_primary\')\n    retained_sources = []\n    for cid in sorted(sources):\n        source = sources[cid]\n        if any(k not in source for k in SOURCE_FIELDS):raise Unavailable(\'source_unavailable\')\n        item = {k: source[k] for k in SOURCE_FIELDS}\n        text(cid); digest(item[\'digest\'])\n        if item[\'id\'] != cid or item[\'session_id\'] != sid:raise Unavailable(\'source_unavailable\')\n        exact_frame(item[\'timeline_start\']); integer(exact_frame(item[\'duration\']), minimum=1)\n        retained_sources.append(item)\n    parents = []; atoms = []; units = 0; seen = set()\n    for supplied in rows:\n        row = {k: supplied[k] for k in (*PRIMARY_FIELDS, \'lexical_evidence\')}\n        if row[\'id\'] in seen:raise Unavailable(\'bounded_primary_duplicate\')\n        seen.add(row[\'id\'])\n        if row[\'chunk_id\'] not in sources:raise Unavailable(\'source_unavailable\')\n        raw = row[\'lexical_evidence\']\n        if raw is not None and (type(raw) is not str or len(raw) > MAX_SEGMENT_BYTES):\n            raise Unavailable(\'inventory_capacity\')\n        parent = primary_evidence(sid, row)\n        own = sources[row[\'chunk_id\']]\n        if (parent[\'start_frame\'] < max(0, primary_frame(own[\'timeline_start\']) - CONTEXT_SECONDS * RATE) or\n                parent[\'end_frame\'] > primary_frame(own[\'timeline_start\'] + own[\'duration\'], end=True) + CONTEXT_SECONDS * RATE):\n            raise Unavailable(\'primary_source_placement\')\n        if not (parent[\'start_frame\'] < origin + scan_end and origin + scan_start < parent[\'end_frame\']):\n            raise Unavailable(\'bounded_primary_scan_mismatch\')\n        current = current_inventory(None, sid, row, source_lookup=lambda cid: sources.get(cid))\n        groups = lexical_groups(parent, current) if current is not None else None\n        if current is not None:\n            units += len(current[\'inventory\'][\'units\'])\n            if units > MAX_PLAN_UNITS:raise Unavailable(\'lexical_unit_capacity\')\n        parent_atoms = groups if groups is not None else [parent]\n        descriptors = []\n        for item in parent_atoms:\n            atom = {\'source\': item[\'source\'], \'segment_id\': row[\'id\'],\n                \'start_frame\': item[\'start_frame\'] - origin, \'end_frame\': item[\'end_frame\'] - origin}\n            # ASR context may precede a nonzero recording scope origin. Keep\n            # that known placement; only source/support frames must be >= 0.\n            integer(atom[\'start_frame\'], minimum=-((1 << 53) - 1))\n            integer(atom[\'end_frame\'], minimum=-((1 << 53) - 1))\n            if groups is not None:\n                atom.update(unit_range=item[\'unit_range\'], display_span=item[\'display_span\'], raw_span=item[\'raw_span\'],\n                    parent_start_frame=item[\'parent_start_frame\'] - origin,\n                    parent_end_frame=item[\'parent_end_frame\'] - origin,\n                    inventory_sha256=current[\'inventory_sha256\'], policy_sha256=item[\'policy_sha256\'])\n            descriptors.append(atom); atoms.append(atom)\n            if len(atoms) > MAX_PLAN_ATOMS:raise Unavailable(\'lexical_atom_capacity\')\n        parents.append({\'row\': row, \'primary_sha256\': parent[\'content_sha256\'],\n            \'inventory_sha256\': current[\'inventory_sha256\'] if current is not None else None,\n            \'atoms\': descriptors})\n    snapshot = {\'schema\': INVENTORY_SCHEMA, \'session_id\': sid, \'origin\': float(scope_origin).hex(),\n        \'scan_frames\': [scan_start, scan_end], \'complete\': True,\n        \'parents\': parents, \'sources\': retained_sources, \'atoms\': atoms, \'unit_count\': units}\n    canonical(snapshot)\n    return snapshot\n\ndef validate_inventory(snapshot):\n    exact(snapshot, \'schema session_id origin scan_frames complete parents sources atoms unit_count\')\n    if snapshot[\'schema\'] != INVENTORY_SCHEMA:raise Unavailable(\'bounded_inventory_policy\')\n    if type(snapshot[\'parents\']) is not list or len(snapshot[\'parents\']) > MAX_PLAN_PARENTS:\n        raise Unavailable(\'primary_capacity\')\n    if type(snapshot[\'sources\']) is not list or not 1 <= len(snapshot[\'sources\']) <= MAX_PARTS:\n        raise Unavailable(\'source_part_capacity\')\n    sources = {}\n    for source in snapshot[\'sources\']:\n        if type(source) is not dict or source.get(\'id\') in sources:\n            raise Unavailable(\'bounded_source_duplicate\')\n        sources[source.get(\'id\')] = source\n    try:origin = float.fromhex(snapshot[\'origin\'])\n    except (TypeError, ValueError) as exc:raise Unavailable(\'core_seam_origin\') from exc\n    if not math.isfinite(origin) or origin.hex() != snapshot[\'origin\']:\n        raise Unavailable(\'core_seam_origin\')\n    expected = lexical_snapshot(snapshot[\'session_id\'], [p[\'row\'] for p in snapshot[\'parents\']],\n        sources, origin, complete=snapshot[\'complete\'], scan_frames=snapshot[\'scan_frames\'])\n    if canonical(expected) != canonical(snapshot):raise Unavailable(\'bounded_inventory_mismatch\')\n    return snapshot\n\ndef select_support(target, commits, snapshot, query_start, query_end):\n    """One deterministic fixed point, never a search over acoustic attempts."""\n    from .transcript_projection import _legal\n    identity, originals = validate_target(target); validate_inventory(snapshot)\n    ca, cb = bounded_chain(commits); integer(query_start); integer(query_end)\n    all_roots = {slot(c): c for c in originals}\n    for c in commits:\n        if slot(c) in all_roots and all_roots[slot(c)] != c:\n            raise Unavailable(\'core_slot_evidence_conflict\')\n        all_roots[slot(c)] = c\n    if len(all_roots) > MAX_BOUND_COMMITS:raise Unavailable(\'commitment_capacity\')\n    bounded_chain(sorted(all_roots.values(), key=lambda c: c[\'part\'][\'logical_start\']))\n    if (identity[\'scope\'] != {k: commits[0][\'scope\'][k] for k in identity[\'scope\']}\n            or snapshot[\'session_id\'] != identity[\'scope\'][\'session_id\']\n            or snapshot[\'origin\'] != identity[\'scope\'][\'origin\']\n            or not snapshot[\'scan_frames\'][0] <= ca < cb <= snapshot[\'scan_frames\'][1]):\n        raise Unavailable(\'bounded_plan_scope\')\n    if not 0 < query_end - query_start <= CORE_FRAMES:raise Unavailable(\'core_seam_query_coverage\')\n    atoms = snapshot[\'atoms\']; components = identity[\'components\']\n    # Exact source placement must remain equal, even for an alias-origin O.\n    for (a, b), expected in zip(components, identity[\'mapping\']):\n        if [asdict(p) for p in bounded_mapping(commits, a, b)] != expected:\n            raise Unavailable(\'bounded_target_mapping_changed\')\n    def legal(a, b):\n        return (0 < b - a <= SUPPORT_FRAMES and ca <= a < b <= cb\n            and query_start <= a < b <= query_end\n            and all(a <= lo < hi <= b for lo, hi in components)\n            and any(a < atom[\'end_frame\'] and atom[\'start_frame\'] < b for atom in atoms)\n            and _legal(a, b, atoms))\n    original = json.loads(target[\'origins\'][0][\'proof\'])[\'support\']\n    a, b = original[\'start_frame\'], original[\'end_frame\']\n    if legal(a, b):return {\'start_frame\': a, \'end_frame\': b}\n    a, b = components[0][0], components[-1][1]; included = set()\n    ordered = sorted(enumerate(atoms), key=lambda pair: (pair[1][\'start_frame\'], pair[1][\'end_frame\'], pair[0]))\n    changed = True\n    while changed:\n        changed = False\n        for index, atom in ordered:\n            if index not in included and a < atom[\'end_frame\'] and atom[\'start_frame\'] < b:\n                included.add(index)\n                lo, hi = min(a, atom[\'start_frame\']), max(b, atom[\'end_frame\'])\n                if (lo, hi) != (a, b):changed = True\n                a, b = lo, hi\n        if b - a > SUPPORT_FRAMES:raise Unavailable(\'support_duration\')\n    if not included:raise Unavailable(\'unresolved_lexical_gap\')\n    if not ca <= a < b <= cb:raise Unavailable(\'authority_unavailable\')\n    if not query_start <= a < b <= query_end:raise Unavailable(\'witness_unavailable\')\n    if not legal(a, b):raise Unavailable(\'no_legal_fixed_point\')\n    return {\'start_frame\': a, \'end_frame\': b}\n\ndef dependency_roots(target, commits, inventories):\n    """One canonical role mask per (source, compact); slots never coalesce."""\n    _, originals = validate_target(target); bounded_chain(commits)\n    validate_inventory(inventories)\n    roots = {}; unique_slots = set()\n    def add(cid, compact, role):\n        text(cid)\n        if compact is not None:digest(compact); unique_slots.add(compact)\n        key = (cid, compact)\n        roots[key] = roots.get(key, 0) | role\n    for c in originals:add(c[\'part\'][\'chunk_id\'], slot(c), 1)\n    for c in commits:add(c[\'part\'][\'chunk_id\'], slot(c), 2)\n    for source in inventories[\'sources\']:add(source[\'id\'], None, 4)\n    for parent in inventories[\'parents\']:add(parent[\'row\'][\'chunk_id\'], None, 8)\n    if len(unique_slots) > MAX_BOUND_COMMITS:raise Unavailable(\'commitment_capacity\')\n    if len({cid for cid, _ in roots}) > MAX_PARTS:raise Unavailable(\'source_part_capacity\')\n    result = [{\'kind\': roots[key], \'cid\': key[0], \'slot\': key[1]}\n              for key in sorted(roots, key=lambda key: (key[0], key[1] or \'\'))]\n    canonical(result, 4096)\n    return result\n\ndef validate_bounded_document(proof, doc=None):\n    if type(proof) is not bytes or not 1 <= len(proof) <= PROOF_BYTES:raise Unavailable(\'proof_capacity\')\n    if doc is None:doc = json.loads(proof)\n    exact(doc, \'schema policy commits geometry support dependencies query right_evidence selection publication_anchor selected coverage target inventories dependency_roots target_sha256\')\n    if doc[\'schema\'] != BOUNDED_SCHEMA or doc[\'policy\'] != BOUNDED_POLICY_SHA256 or doc[\'selected\'] is not True:\n        raise Unavailable(\'core_seam_policy\')\n    identity, originals = validate_target(doc[\'target\']); commits = doc[\'commits\']\n    ca, cb = bounded_chain(commits); validate_inventory(doc[\'inventories\'])\n    shape = exact(doc[\'geometry\'], \'start_frame end_frame\')\n    _bounded_query(doc[\'query\'], commits[-1], shape, doc[\'right_evidence\'])\n    if doc[\'target_sha256\'] != sha(doc[\'target\']):raise Unavailable(\'bounded_target_hash\')\n    expected_shape = select_support(doc[\'target\'], commits, doc[\'inventories\'],\n        doc[\'query\'][\'query_start_frame\'], doc[\'query\'][\'query_end_frame\'])\n    if shape != expected_shape:raise Unavailable(\'bounded_nondeterministic_support\')\n    current_overlap = overlap_components(doc[\'query\'][\'turns\'], shape[\'start_frame\'], shape[\'end_frame\'])\n    if any(not any(a <= lo < hi <= b for a, b in current_overlap) for lo, hi in identity[\'components\']):\n        raise Unavailable(\'bounded_current_target_disagreement\')\n    # Current query scope is identical to every accepted origin\'s acoustic scope.\n    if any(c[\'scope\'] != commits[-1][\'scope\'] for c in originals):\n        raise Unavailable(\'unresolved_scope_transition\')\n    parts = bounded_mapping(commits, shape[\'start_frame\'], shape[\'end_frame\'])\n    if doc[\'dependencies\'] != [asdict(p) for p in parts]:raise Unavailable(\'core_seam_mapping\')\n    if doc[\'dependency_roots\'] != dependency_roots(doc[\'target\'], commits, doc[\'inventories\']):\n        raise Unavailable(\'bounded_dependency_mismatch\')\n    from .native_owned_seam_cells import selected_pair\n    if doc[\'selection\'] != selected_pair([doc[\'query\']], shape[\'start_frame\'], shape[\'end_frame\']):\n        raise Unavailable(\'core_seam_selection\')\n    support = SourceSupport.from_dict(doc[\'support\']); contents = [p.content() for p in parts]\n    expected_core = sha({\'commits\': commits, \'geometry\': shape, \'query\': doc[\'query\'],\n        \'target_sha256\': doc[\'target_sha256\'], \'inventories_sha256\': sha(doc[\'inventories\']),\n        \'dependency_roots\': doc[\'dependency_roots\']})\n    if (support.source_generation != sha({\'schema\': \'immutable-source-cell-generation-v1\', \'sources\': contents})\n            or support.plan_sha256 != sha({\'schema\': \'bounded-cell-mapping-v1\', \'parts\': contents})\n            or support.native_runtime_sha256 != commits[-1][\'runtime_sha256\']\n            or support.core_sha256 != expected_core\n            or (support.source_frames, support.core_start_frame, support.core_end_frame,\n                support.start_frame, support.end_frame) != (cb, ca, cb, shape[\'start_frame\'], shape[\'end_frame\'])\n            or support.pcm16le_sha256 != doc[\'query\'][\'support_pcm16le_sha256\']):\n        raise Unavailable(\'core_seam_support\')\n    if doc[\'publication_anchor\'] != anchor(commits[-1]) or doc[\'coverage\'] != coverage(doc):\n        raise Unavailable(\'core_seam_publication\')\n    if canonical(doc) != proof:raise Unavailable(\'noncanonical_proof\')\n    return doc, support, parts\n\ndef make_bounded_cell(commits, query, evidence, pcm, target, snapshot):\n    from .native_owned_seam_cells import selected_pair\n    shape = select_support(target, commits, snapshot, query[\'query_start_frame\'], query[\'query_end_frame\'])\n    ca, cb = bounded_chain(commits); parts = bounded_mapping(commits, shape[\'start_frame\'], shape[\'end_frame\'])\n    contents = [p.content() for p in parts]; target_hash = sha(target)\n    roots = dependency_roots(target, commits, snapshot)\n    support = SourceSupport(sha({\'schema\': \'immutable-source-cell-generation-v1\', \'sources\': contents}),\n        sha({\'schema\': \'bounded-cell-mapping-v1\', \'parts\': contents}), commits[-1][\'runtime_sha256\'],\n        sha({\'commits\': commits, \'geometry\': shape, \'query\': query, \'target_sha256\': target_hash,\n             \'inventories_sha256\': sha(snapshot), \'dependency_roots\': roots}),\n        cb, ca, cb, shape[\'start_frame\'], shape[\'end_frame\'], hashlib.sha256(pcm).hexdigest(), float32_digest(pcm))\n    doc = {\'schema\': BOUNDED_SCHEMA, \'policy\': BOUNDED_POLICY_SHA256, \'commits\': list(commits),\n        \'geometry\': shape, \'support\': support.to_dict(), \'dependencies\': [asdict(p) for p in parts],\n        \'query\': query, \'right_evidence\': evidence,\n        \'selection\': selected_pair([query], shape[\'start_frame\'], shape[\'end_frame\']),\n        \'publication_anchor\': anchor(commits[-1]), \'selected\': True, \'target\': target,\n        \'target_sha256\': target_hash, \'inventories\': snapshot, \'dependency_roots\': roots}\n    doc[\'coverage\'] = coverage(doc); p = doc[\'publication_anchor\']\n    return Cell(support, parts, canonical(doc), pcm, commits[-1][\'scope\'][\'session_id\'],\n                p[\'chunk_id\'], p[\'start\'], p[\'end\'], p[\'sha256\'])\n'
_SOURCE52_ORACLE_SHA256 = 'eee113a98da3ef725841a8fb26dad6aea07a02b112706957d8b3a68011d164f4'
_SOURCE52_UNCHANGED_FUNCTIONS = {'store_policy': '4ae3a8bc27e762d29a25fc89fcba4d128efbb17215004f0edde765da957c0cff', 'is_core': 'b24075a23a798641a7811fc1b06c4a7a0f4a37f0893ba4ed466eb88d64f54deb', 'policy': '4d7f2bab073ec320f2b5ddd98e3520663a0fa558bc6700f2d43583f35d4515ca', 'exact': '403c63a238fa0774c61921af1f36805aa0614d40999c63f729757cf2ca4bd94a', 'scope_key': 'a90c417a53eb5b49315c982c158b0ca65c035aca7f4ca9a679eef917e3bed921', 'slot': '4b030de9b9d923a9cf3a2f41bb7b6cd7f4710f29403a43b533ab45e74a0f7573', 'anchor': '252d887e828406cda2d6457d699522422e35cbe81edb7442b61e3c1e09ec5220', 'validate_commit': 'fba2339655e09f7e430e32be9909f69df2f4c1850fdc1cf424ded1db3e24168e', 'geometry': 'd1765c734ae9e25dc432541ad551a7cb3716ba53c664f324b7f353dc91d2381d', 'mapping': '69b0aae7be6193892f878296ceeca3424e4bc171f3f879e739eae5d5797fd63d', 'ownership': '3bf8843afb3adaedb21eaea9675ab8ebe8c611c8efc8354154585c3dc144fa4b', 'coverage': '8a85b4fffc1b7b4f9158ad176bbba102f6b4d1a97896a24fd982a551010ac08f', 'validate_query': '8182eb46f4793e0e99ef87d101e540755d45c34bf9cb415c7404ae99806ad31f', 'validate_document': '55e5444292e6b7f9227d18fb9867dc889451ed8ef9572737f16a7d8c36430e44', 'make_cell': 'eab84814d3e36d966cd214b7fde13a161d75f98b66a81fbb2bc91dad3df3192b', 'overlap_components': '3bc049b9be52b9b8f0856ef2467e7a0022e74902e0d8add3d8f929d7bf23f3d5', 'bounded_chain': '337636142e762bb89c73483aa3673cad82051e18066dd08c93c1d71ccd1bb7de', 'bounded_mapping': '2cb6c9452b7b3a53458986264f9ebf4f99b25a42a2f7b7855b1f4bd749ce467d', 'origin_certificate': 'fa5dc1be41eaaa519dd21d7d3f750ff81ab06e5469c1c5600ce9a2bf7f554521', '_origin_identity': 'b93d3e073c83df615f00cc707c3bbc3286fec9148af2d6d24ebda14c4445f5d9', 'original_target': 'dd01994f5e061c8acf2cf816786658c681f1ecd9ed5db8c6e05ba026731d77b4', 'validate_target': 'c642e88ac8e9c841064b1b51adaee50c91ea0919b135896d3ee738e2b74c5605', 'append_target_origin': 'c6f898e3218d1c46da788634f5a2796e8512706fcaeb0d1803d85b0c49c97941', '_bounded_query': 'd94c01db700d96bf416e3327d2cb83aa540208bed28a4aa6189c898765ddeec9', 'init_schema': '780abd1ac0bcc1ca80be7e85e839e186514df54b4fd3038ef6546adc01f3a554', 'retention_counts': '6ea007c321c5a9faeb7f5750b9ae92f82285470b87f6f4f259af59e4ce0710e0', 'read_commit': '014859aff28b21035a97f76266b232c67420a5835a006a31c4a48d152d28f555', '_gc_guard': '4a4f1cdba5281259a8964ecc2c49210d97ccf39603e78e3443d6459d79e19200', '_gc_outbox_root': 'c52b72ee35f09594a42fe9458d4e42c205f6475a66a31417183547a5fc882f57', '_gc_stream_source': '3e779a4d54e555267bb4f4e087e1fa68cf0be0bad02bea86e41c0b89bb1d3055', '_gc_exact_row': '3af6b2f377222c80652154e842a46bbf81dbe140af783fca53a87422c8d7beb2', '_gc_receipt': 'fd1ca96cf2bd6b6de2dadd6aa2a221ea5075d6b83204c23252353b275523fd2b', '_gc_store_eligible': '9358a46c7d99ebeb5def22893c149900d3c308b3a2446856ea7f6bc5ca11137f', 'collect_compacts': '64689c698fa522a1fa3eb39aa10c3b18101f4c4c972c44bed86db1c0aecba107', 'transaction': 'a34d1291d5c64515def230f65cb59dedcc47066d1145197e4c10a96565251d0d', 'current': '5b9183d5f692363ed0f533f39dcf933ccfc90254aa8955ee07f71357c719023e', 'collect_receipts': '2d8a54a90060adf62c237dcb01673644872f200be5ea5365ad1426b1ab84b3c5'}
_SOURCE52_CONSTANTS = {'SCHEMA': 'a93dcab985400372ccef24d4bdce13aa04230b051c18c36c05e46f442aa2d009', 'RESET_SCHEMA': '14cfd4eb7a9c083aa34ddde9dfd881558179b807d4c92e88d537dcdab652ea44', 'RESTART_SCHEMA': '838f31b41245566bb11977dd77bbc800852d827042218441d00aae8bacc0ce1b', 'BOUNDED_SCHEMA': '5c78800447c1a146440a631c5421c6ff512925c474dfdae5ca1c943066032d10', 'TARGET_SCHEMA': 'a6bf1b768bdf6417b2c6e6da1cb64b20650c02d9d709e85f66e1da28d15d79c4', 'INVENTORY_SCHEMA': 'efe8f1b1dc76024a3a3e648a8e940c1e778b561bee8c617b0adebfa5154cb946', 'MAX_BOUND_COMMITS': '18c2c5afd1b5352fddedd29726c667397ec0d468e164b5a550cef8372d495f06', 'MAX_ORIGINS': 'f8140be7096d4007b5b1a091f537b6e63569716bdc66b4a2c01a3a9f6b18a20f', 'MAX_PLAN_PARENTS': 'bc79b9d282d78921129d5a6ad90157d850edfc38875da816a473e23fd517e7cc', 'MAX_PLAN_UNITS': '94591b39e8fcef4ff98a6183932bdca4801ab625bccf13a56ad5e4d2a1fd0fbe', 'MAX_PLAN_ATOMS': '5ab8cacd6cd2936ecfd84055fde979630caea4ccbce084ffad2eae7d4f6a8a00', 'SUPPORT_FRAMES': 'fd9e101a684c03dc7912971702301614fb87d1f921dbe54d0fd3cc6adc667991', 'COMPACT_BYTES': '1d95c1eed6915ec006562dc60c2d4c997cf006b3074c25ccb166dc58e1732e31', 'MAX_COMPACTS': '624f835ec62fb8f526aa40c36b50353fa2790603e4e387ecce2d1c0bda194dfa', 'PENDING_BYTES': 'eb1b0b48f2721fe41b0aff594a265c9bb75763e504a0b30af62aeadb7cec81ef', 'MAX_PENDING': '21498602f2af03c1807fad9810f27c228d91b6f367ebffdd91174d0eadfaf18a', 'COMPACT_GC_BATCH': 'bab15b66096e121f0e7cd5e94ce6d9209a4d5c9df4033b85536137a27b96d3c4', 'COMPACT_GC_BODY_BYTES': '9852fb6bde80207357a8297549c5b38157c83ab5c4d5cbebf331f643d86917fb', 'SIDE_FRAMES': 'a047df843132dec0a641f4be9e7aa368c288f75954d1bed3c5efe8b6da9937d4', 'POLICY': '8354c88ddf610952af79dcafed33a2c9dfb7fad430d5d32bde2eca7cd61eb129', 'POLICY_SHA256': '429ccd29ca057a9edd22e67540cb6226129af84645ab75fc673cce20106e4d0a', 'RESET_POLICY': '9e6001efa4f6eeb50278961a2f6f7a56acb2e6f122604a29bbf506bcd6b5de83', 'RESET_POLICY_SHA256': '778d78644fd7ead29c090325aeacc3a6be5121c3a9b80d45515c38d778b15d12', 'RESTART_POLICY': 'bdbe64dc7357167f0e166febb0fef4b20900967b1b3cbb723800a115d99db5e6', 'RESTART_POLICY_SHA256': '3dbc6fc609589aba922e1f9f89fa7e8384b49d8084a554541f288ffac104568c', 'BOUNDED_POLICY': '5b94f1b4c4a143f5091b7dd95fefb36683096a8ee2d91dfc243ea854fab15eb9', 'BOUNDED_POLICY_SHA256': '89433154003b78c7d5c9067996356813d61d70a0c9b0061ab9dd88a6022cf025'}
_LITERAL_V1_INVENTORY = b'{"atoms":[],"complete":true,"origin":"0x0.0p+0","parents":[],"scan_frames":[0,1],"schema":"native-owned-bounded-inventory-v1","session_id":"bounded-synthetic-session","sources":[{"digest":"0000000000000000000000000000000000000000000000000000000000000000","duration":1.0,"id":"source","session_id":"bounded-synthetic-session","timeline_start":0.0}],"unit_count":0}'
_LITERAL_V1_INVENTORY_SHA256 = 'aefc36f1f9d1b521e12b725943d5c0389da948423c02fe131c45b63453d952e5'


# Explicit source60 serializer and v2 closure. The old oracle never borrows
# the annotated candidate serializer or silently adopts a changed helper pin.
_SOURCE60_CELLS_SHA256 = 'ab282afa1877052f4c1da1566bb07f7c6b82d28749b98068cd45b578edcb4eb6'
_SOURCE60_CORE_SHA256 = '0222a5836dbb175e3bb4766debd85989293bbf1fa8f5f794d33807a6cac6a64e'
_SOURCE60_CANONICAL_ORACLE = 'def canonical(value, maximum=PROOF_BYTES):\n    """Only bounded, validated internal structures may reach this serializer."""\n    data = json.dumps(value, sort_keys=True, separators=(\',\', \':\'),\n                      ensure_ascii=True, allow_nan=False).encode(\'ascii\')\n    if len(data) > maximum:\n        raise Unavailable(\'serialization_capacity\')\n    return data\n\ndef sha(value):\n    return hashlib.sha256(canonical(value)).hexdigest()\n'
_SOURCE60_CANONICAL_SHA256 = '0b68449a68aee4d2adb9d23d8e10ef84c7a7af816ed3e17268853baaf305f398'
_SOURCE60_INVENTORY_ORACLE = 'def lexical_snapshot(sid, rows, sources, scope_origin, *, complete=True, scan_frames=None,\n                     schema=INVENTORY_SCHEMA):\n    from .native_owned_support_cache import ReadError\n    try:\n        if schema not in (INVENTORY_SCHEMA, COMPACT_INVENTORY_SCHEMA):\n            raise Unavailable(\'bounded_inventory_policy\')\n        view = _lexical_snapshot(sid, rows, sources, scope_origin,\n            complete=complete, scan_frames=scan_frames)\n        return _encode_inventory(view, schema)\n    except ReadError as exc:\n        # Shared projection validators use their HTTP/read-domain exception.\n        # Numeric planning keeps this an optional unresolved placement result.\n        raise Unavailable(exc.reason) from exc\n\ndef _lexical_snapshot(sid, rows, sources, scope_origin, *, complete=True, scan_frames=None):\n    """Pure adapter over the *existing* inventory and ordinal-group validators.\n\n    The caller supplies a complete bounded indexed scan and source snapshots.\n    No database or source I/O is hidden here, including for a stale inventory.\n    Retaining exact rows allows proof decoding to repeat identical semantics.\n    """\n    from .lexical_evidence import current_inventory, SOURCE_FIELDS, MAX_SEGMENT_BYTES\n    from .transcript_contract import PRIMARY_FIELDS, primary_evidence, primary_frame\n    from .transcript_projection import lexical_groups\n    from .transcript_store import CONTEXT_SECONDS\n    text(sid)\n    if complete is not True:raise Unavailable(\'incomplete_primary\')\n    if type(rows) not in (list, tuple) or len(rows) > MAX_PLAN_PARENTS:\n        raise Unavailable(\'primary_capacity\')\n    if type(sources) is not dict or not 1 <= len(sources) <= MAX_PARTS:\n        raise Unavailable(\'source_part_capacity\')\n    origin = exact_frame(scope_origin)\n    if type(scan_frames) not in (list, tuple) or len(scan_frames) != 2:\n        raise Unavailable(\'incomplete_primary\')\n    scan_start, scan_end = scan_frames; integer(scan_start); integer(scan_end)\n    if scan_start >= scan_end:raise Unavailable(\'incomplete_primary\')\n    retained_sources = []\n    for cid in sorted(sources):\n        source = sources[cid]\n        if any(k not in source for k in SOURCE_FIELDS):raise Unavailable(\'source_unavailable\')\n        item = {k: source[k] for k in SOURCE_FIELDS}\n        text(cid); digest(item[\'digest\'])\n        if item[\'id\'] != cid or item[\'session_id\'] != sid:raise Unavailable(\'source_unavailable\')\n        exact_frame(item[\'timeline_start\']); integer(exact_frame(item[\'duration\']), minimum=1)\n        retained_sources.append(item)\n    parents = []; atoms = []; units = 0; seen = set()\n    for supplied in rows:\n        row = {k: supplied[k] for k in (*PRIMARY_FIELDS, \'lexical_evidence\')}\n        if row[\'id\'] in seen:raise Unavailable(\'bounded_primary_duplicate\')\n        seen.add(row[\'id\'])\n        if row[\'chunk_id\'] not in sources:raise Unavailable(\'source_unavailable\')\n        raw = row[\'lexical_evidence\']\n        if raw is not None and (type(raw) is not str or len(raw) > MAX_SEGMENT_BYTES):\n            raise Unavailable(\'inventory_capacity\')\n        parent = primary_evidence(sid, row)\n        own = sources[row[\'chunk_id\']]\n        if (parent[\'start_frame\'] < max(0, primary_frame(own[\'timeline_start\']) - CONTEXT_SECONDS * RATE) or\n                parent[\'end_frame\'] > primary_frame(own[\'timeline_start\'] + own[\'duration\'], end=True) + CONTEXT_SECONDS * RATE):\n            raise Unavailable(\'primary_source_placement\')\n        if not (parent[\'start_frame\'] < origin + scan_end and origin + scan_start < parent[\'end_frame\']):\n            raise Unavailable(\'bounded_primary_scan_mismatch\')\n        current = current_inventory(None, sid, row, source_lookup=lambda cid: sources.get(cid))\n        groups = lexical_groups(parent, current) if current is not None else None\n        if current is not None:\n            units += len(current[\'inventory\'][\'units\'])\n            if units > MAX_PLAN_UNITS:raise Unavailable(\'lexical_unit_capacity\')\n        parent_atoms = groups if groups is not None else [parent]\n        descriptors = []\n        for item in parent_atoms:\n            atom = {\'source\': item[\'source\'], \'segment_id\': row[\'id\'],\n                \'start_frame\': item[\'start_frame\'] - origin, \'end_frame\': item[\'end_frame\'] - origin}\n            # ASR context may precede a nonzero recording scope origin. Keep\n            # that known placement; only source/support frames must be >= 0.\n            integer(atom[\'start_frame\'], minimum=-((1 << 53) - 1))\n            integer(atom[\'end_frame\'], minimum=-((1 << 53) - 1))\n            if groups is not None:\n                atom.update(unit_range=item[\'unit_range\'], display_span=item[\'display_span\'], raw_span=item[\'raw_span\'],\n                    parent_start_frame=item[\'parent_start_frame\'] - origin,\n                    parent_end_frame=item[\'parent_end_frame\'] - origin,\n                    inventory_sha256=current[\'inventory_sha256\'], policy_sha256=item[\'policy_sha256\'])\n            descriptors.append(atom); atoms.append(atom)\n            if len(atoms) > MAX_PLAN_ATOMS:raise Unavailable(\'lexical_atom_capacity\')\n        parents.append({\'row\': row, \'primary_sha256\': parent[\'content_sha256\'],\n            \'inventory_sha256\': current[\'inventory_sha256\'] if current is not None else None,\n            \'atoms\': descriptors})\n    snapshot = {\'schema\': INVENTORY_SCHEMA, \'session_id\': sid, \'origin\': float(scope_origin).hex(),\n        \'scan_frames\': [scan_start, scan_end], \'complete\': True,\n        \'parents\': parents, \'sources\': retained_sources, \'atoms\': atoms, \'unit_count\': units}\n    return snapshot\n\ndef _encode_inventory(view, schema):\n    """Persist one lossless row/inventory copy, never the derived atom view."""\n    if schema == INVENTORY_SCHEMA:\n        canonical(view)\n        return view\n    if schema != COMPACT_INVENTORY_SCHEMA:raise Unavailable(\'bounded_inventory_policy\')\n    from .lexical_evidence import encoded\n    parents = []\n    for parent in view[\'parents\']:\n        row = dict(parent[\'row\'])\n        if parent[\'inventory_sha256\'] is not None:\n            raw = row[\'lexical_evidence\']; value = json.loads(raw)\n            if encoded(value).decode(\'ascii\') != raw:\n                raise Unavailable(\'bounded_inventory_roundtrip\')\n            row[\'lexical_evidence\'] = {\'encoding\': \'canonical-json-v1\', \'value\': value}\n        parents.append({\'row\': row})\n    result = {key: view[key] for key in\n        (\'session_id\', \'origin\', \'scan_frames\', \'complete\', \'sources\', \'unit_count\')}\n    result.update(schema=COMPACT_INVENTORY_SCHEMA, parents=parents)\n    canonical(result)\n    return result\n\ndef _legacy_inventory_view(snapshot):\n    """Exact v1 wire validation; historical bytes and hashes remain unchanged."""\n    exact(snapshot, \'schema session_id origin scan_frames complete parents sources atoms unit_count\')\n    if snapshot[\'schema\'] != INVENTORY_SCHEMA:raise Unavailable(\'bounded_inventory_policy\')\n    if type(snapshot[\'parents\']) is not list or len(snapshot[\'parents\']) > MAX_PLAN_PARENTS:\n        raise Unavailable(\'primary_capacity\')\n    if type(snapshot[\'sources\']) is not list or not 1 <= len(snapshot[\'sources\']) <= MAX_PARTS:\n        raise Unavailable(\'source_part_capacity\')\n    sources = {}\n    for source in snapshot[\'sources\']:\n        if type(source) is not dict or source.get(\'id\') in sources:\n            raise Unavailable(\'bounded_source_duplicate\')\n        sources[source.get(\'id\')] = source\n    try:origin = float.fromhex(snapshot[\'origin\'])\n    except (TypeError, ValueError) as exc:raise Unavailable(\'core_seam_origin\') from exc\n    if not math.isfinite(origin) or origin.hex() != snapshot[\'origin\']:\n        raise Unavailable(\'core_seam_origin\')\n    expected = lexical_snapshot(snapshot[\'session_id\'], [p[\'row\'] for p in snapshot[\'parents\']],\n        sources, origin, complete=snapshot[\'complete\'], scan_frames=snapshot[\'scan_frames\'])\n    if canonical(expected) != canonical(snapshot):raise Unavailable(\'bounded_inventory_mismatch\')\n    return snapshot\n\ndef inventory_view(snapshot):\n    """Validate versioned wire bytes and regenerate a bounded, pure semantic view.\n\n    Only the original wire payload may enter proof/core hashes or byte charges.\n    The derived v1-shaped view is temporary and is never persisted or serialized.\n    """\n    if type(snapshot) is not dict:raise Unavailable(\'core_seam_shape\')\n    if snapshot.get(\'schema\') == INVENTORY_SCHEMA:return _legacy_inventory_view(snapshot)\n    if snapshot.get(\'schema\') != COMPACT_INVENTORY_SCHEMA:\n        raise Unavailable(\'bounded_inventory_policy\')\n    canonical(snapshot)  # Screen the complete compact input before expansion.\n    exact(snapshot, \'schema session_id origin scan_frames complete parents sources unit_count\')\n    if type(snapshot[\'parents\']) is not list or len(snapshot[\'parents\']) > MAX_PLAN_PARENTS:\n        raise Unavailable(\'primary_capacity\')\n    if type(snapshot[\'sources\']) is not list or not 1 <= len(snapshot[\'sources\']) <= MAX_PARTS:\n        raise Unavailable(\'source_part_capacity\')\n    integer(snapshot[\'unit_count\'], maximum=MAX_PLAN_UNITS)\n    sources = {}\n    for source in snapshot[\'sources\']:\n        if type(source) is not dict or source.get(\'id\') in sources:\n            raise Unavailable(\'bounded_source_duplicate\')\n        sources[source.get(\'id\')] = source\n    try:origin = float.fromhex(snapshot[\'origin\'])\n    except (TypeError, ValueError) as exc:raise Unavailable(\'core_seam_origin\') from exc\n    if not math.isfinite(origin) or origin.hex() != snapshot[\'origin\']:\n        raise Unavailable(\'core_seam_origin\')\n    from .lexical_evidence import encoded, MAX_SEGMENT_BYTES\n    from .transcript_contract import PRIMARY_FIELDS\n    rows = []; tagged = []\n    for index, parent in enumerate(snapshot[\'parents\']):\n        exact(parent, \'row\')\n        row = dict(exact(parent[\'row\'], \' \'.join((*PRIMARY_FIELDS, \'lexical_evidence\'))))\n        raw = row[\'lexical_evidence\']\n        if type(raw) is dict:\n            exact(raw, \'encoding value\')\n            if raw[\'encoding\'] != \'canonical-json-v1\' or type(raw[\'value\']) is not dict:\n                raise Unavailable(\'bounded_inventory_encoding\')\n            units = raw[\'value\'].get(\'units\')\n            if type(units) is not list or len(units) > MAX_PLAN_UNITS:\n                raise Unavailable(\'lexical_unit_capacity\')\n            raw = encoded(raw[\'value\']).decode(\'ascii\'); tagged.append(index)\n            row[\'lexical_evidence\'] = raw\n        if raw is not None and (type(raw) is not str or len(raw) > MAX_SEGMENT_BYTES):\n            raise Unavailable(\'inventory_capacity\')\n        rows.append(row)\n    from .native_owned_support_cache import ReadError\n    try:\n        view = _lexical_snapshot(snapshot[\'session_id\'], rows, sources, origin,\n            complete=snapshot[\'complete\'], scan_frames=snapshot[\'scan_frames\'])\n    except ReadError as exc:raise Unavailable(exc.reason) from exc\n    if any(view[\'parents\'][index][\'inventory_sha256\'] is None for index in tagged):\n        raise Unavailable(\'bounded_inventory_encoding\')\n    if canonical(_encode_inventory(view, COMPACT_INVENTORY_SCHEMA)) != canonical(snapshot):\n        raise Unavailable(\'bounded_inventory_mismatch\')\n    return view\n\ndef validate_inventory(snapshot):\n    inventory_view(snapshot)\n    return snapshot\n\ndef select_support(target, commits, snapshot, query_start, query_end):\n    """One deterministic fixed point, never a search over acoustic attempts."""\n    from .transcript_projection import _legal\n    identity, originals = validate_target(target); snapshot = inventory_view(snapshot)\n    ca, cb = bounded_chain(commits); integer(query_start); integer(query_end)\n    all_roots = {slot(c): c for c in originals}\n    for c in commits:\n        if slot(c) in all_roots and all_roots[slot(c)] != c:\n            raise Unavailable(\'core_slot_evidence_conflict\')\n        all_roots[slot(c)] = c\n    if len(all_roots) > MAX_BOUND_COMMITS:raise Unavailable(\'commitment_capacity\')\n    bounded_chain(sorted(all_roots.values(), key=lambda c: c[\'part\'][\'logical_start\']))\n    if (identity[\'scope\'] != {k: commits[0][\'scope\'][k] for k in identity[\'scope\']}\n            or snapshot[\'session_id\'] != identity[\'scope\'][\'session_id\']\n            or snapshot[\'origin\'] != identity[\'scope\'][\'origin\']\n            or not snapshot[\'scan_frames\'][0] <= ca < cb <= snapshot[\'scan_frames\'][1]):\n        raise Unavailable(\'bounded_plan_scope\')\n    if not 0 < query_end - query_start <= CORE_FRAMES:raise Unavailable(\'core_seam_query_coverage\')\n    atoms = snapshot[\'atoms\']; components = identity[\'components\']\n    # Exact source placement must remain equal, even for an alias-origin O.\n    for (a, b), expected in zip(components, identity[\'mapping\']):\n        if [asdict(p) for p in bounded_mapping(commits, a, b)] != expected:\n            raise Unavailable(\'bounded_target_mapping_changed\')\n    def legal(a, b):\n        return (0 < b - a <= SUPPORT_FRAMES and ca <= a < b <= cb\n            and query_start <= a < b <= query_end\n            and all(a <= lo < hi <= b for lo, hi in components)\n            and any(a < atom[\'end_frame\'] and atom[\'start_frame\'] < b for atom in atoms)\n            and _legal(a, b, atoms))\n    original = json.loads(target[\'origins\'][0][\'proof\'])[\'support\']\n    a, b = original[\'start_frame\'], original[\'end_frame\']\n    if legal(a, b):return {\'start_frame\': a, \'end_frame\': b}\n    a, b = components[0][0], components[-1][1]; included = set()\n    ordered = sorted(enumerate(atoms), key=lambda pair: (pair[1][\'start_frame\'], pair[1][\'end_frame\'], pair[0]))\n    changed = True\n    while changed:\n        changed = False\n        for index, atom in ordered:\n            if index not in included and a < atom[\'end_frame\'] and atom[\'start_frame\'] < b:\n                included.add(index)\n                lo, hi = min(a, atom[\'start_frame\']), max(b, atom[\'end_frame\'])\n                if (lo, hi) != (a, b):changed = True\n                a, b = lo, hi\n        if b - a > SUPPORT_FRAMES:raise Unavailable(\'support_duration\')\n    if not included:raise Unavailable(\'unresolved_lexical_gap\')\n    if not ca <= a < b <= cb:raise Unavailable(\'authority_unavailable\')\n    if not query_start <= a < b <= query_end:raise Unavailable(\'witness_unavailable\')\n    if not legal(a, b):raise Unavailable(\'no_legal_fixed_point\')\n    return {\'start_frame\': a, \'end_frame\': b}\n\ndef dependency_roots(target, commits, inventories):\n    """One canonical role mask per (source, compact); slots never coalesce."""\n    _, originals = validate_target(target); bounded_chain(commits)\n    inventories = inventory_view(inventories)\n    roots = {}; unique_slots = set()\n    def add(cid, compact, role):\n        text(cid)\n        if compact is not None:digest(compact); unique_slots.add(compact)\n        key = (cid, compact)\n        roots[key] = roots.get(key, 0) | role\n    for c in originals:add(c[\'part\'][\'chunk_id\'], slot(c), 1)\n    for c in commits:add(c[\'part\'][\'chunk_id\'], slot(c), 2)\n    for source in inventories[\'sources\']:add(source[\'id\'], None, 4)\n    for parent in inventories[\'parents\']:add(parent[\'row\'][\'chunk_id\'], None, 8)\n    if len(unique_slots) > MAX_BOUND_COMMITS:raise Unavailable(\'commitment_capacity\')\n    if len({cid for cid, _ in roots}) > MAX_PARTS:raise Unavailable(\'source_part_capacity\')\n    result = [{\'kind\': roots[key], \'cid\': key[0], \'slot\': key[1]}\n              for key in sorted(roots, key=lambda key: (key[0], key[1] or \'\'))]\n    canonical(result, 4096)\n    return result\n\ndef validate_bounded_document(proof, doc=None):\n    if type(proof) is not bytes or not 1 <= len(proof) <= PROOF_BYTES:raise Unavailable(\'proof_capacity\')\n    if doc is None:doc = json.loads(proof)\n    exact(doc, \'schema policy commits geometry support dependencies query right_evidence selection publication_anchor selected coverage target inventories dependency_roots target_sha256\')\n    if doc[\'schema\'] != BOUNDED_SCHEMA or doc[\'policy\'] != BOUNDED_POLICY_SHA256 or doc[\'selected\'] is not True:\n        raise Unavailable(\'core_seam_policy\')\n    identity, originals = validate_target(doc[\'target\']); commits = doc[\'commits\']\n    ca, cb = bounded_chain(commits); validate_inventory(doc[\'inventories\'])\n    shape = exact(doc[\'geometry\'], \'start_frame end_frame\')\n    _bounded_query(doc[\'query\'], commits[-1], shape, doc[\'right_evidence\'])\n    if doc[\'target_sha256\'] != sha(doc[\'target\']):raise Unavailable(\'bounded_target_hash\')\n    expected_shape = select_support(doc[\'target\'], commits, doc[\'inventories\'],\n        doc[\'query\'][\'query_start_frame\'], doc[\'query\'][\'query_end_frame\'])\n    if shape != expected_shape:raise Unavailable(\'bounded_nondeterministic_support\')\n    current_overlap = overlap_components(doc[\'query\'][\'turns\'], shape[\'start_frame\'], shape[\'end_frame\'])\n    if any(not any(a <= lo < hi <= b for a, b in current_overlap) for lo, hi in identity[\'components\']):\n        raise Unavailable(\'bounded_current_target_disagreement\')\n    # Current query scope is identical to every accepted origin\'s acoustic scope.\n    if any(c[\'scope\'] != commits[-1][\'scope\'] for c in originals):\n        raise Unavailable(\'unresolved_scope_transition\')\n    parts = bounded_mapping(commits, shape[\'start_frame\'], shape[\'end_frame\'])\n    if doc[\'dependencies\'] != [asdict(p) for p in parts]:raise Unavailable(\'core_seam_mapping\')\n    if doc[\'dependency_roots\'] != dependency_roots(doc[\'target\'], commits, doc[\'inventories\']):\n        raise Unavailable(\'bounded_dependency_mismatch\')\n    from .native_owned_seam_cells import selected_pair\n    if doc[\'selection\'] != selected_pair([doc[\'query\']], shape[\'start_frame\'], shape[\'end_frame\']):\n        raise Unavailable(\'core_seam_selection\')\n    support = SourceSupport.from_dict(doc[\'support\']); contents = [p.content() for p in parts]\n    expected_core = sha({\'commits\': commits, \'geometry\': shape, \'query\': doc[\'query\'],\n        \'target_sha256\': doc[\'target_sha256\'], \'inventories_sha256\': sha(doc[\'inventories\']),\n        \'dependency_roots\': doc[\'dependency_roots\']})\n    if (support.source_generation != sha({\'schema\': \'immutable-source-cell-generation-v1\', \'sources\': contents})\n            or support.plan_sha256 != sha({\'schema\': \'bounded-cell-mapping-v1\', \'parts\': contents})\n            or support.native_runtime_sha256 != commits[-1][\'runtime_sha256\']\n            or support.core_sha256 != expected_core\n            or (support.source_frames, support.core_start_frame, support.core_end_frame,\n                support.start_frame, support.end_frame) != (cb, ca, cb, shape[\'start_frame\'], shape[\'end_frame\'])\n            or support.pcm16le_sha256 != doc[\'query\'][\'support_pcm16le_sha256\']):\n        raise Unavailable(\'core_seam_support\')\n    if doc[\'publication_anchor\'] != anchor(commits[-1]) or doc[\'coverage\'] != coverage(doc):\n        raise Unavailable(\'core_seam_publication\')\n    if canonical(doc) != proof:raise Unavailable(\'noncanonical_proof\')\n    return doc, support, parts\n\ndef make_bounded_cell(commits, query, evidence, pcm, target, snapshot):\n    from .native_owned_seam_cells import selected_pair\n    shape = select_support(target, commits, snapshot, query[\'query_start_frame\'], query[\'query_end_frame\'])\n    ca, cb = bounded_chain(commits); parts = bounded_mapping(commits, shape[\'start_frame\'], shape[\'end_frame\'])\n    contents = [p.content() for p in parts]; target_hash = sha(target)\n    roots = dependency_roots(target, commits, snapshot)\n    support = SourceSupport(sha({\'schema\': \'immutable-source-cell-generation-v1\', \'sources\': contents}),\n        sha({\'schema\': \'bounded-cell-mapping-v1\', \'parts\': contents}), commits[-1][\'runtime_sha256\'],\n        sha({\'commits\': commits, \'geometry\': shape, \'query\': query, \'target_sha256\': target_hash,\n             \'inventories_sha256\': sha(snapshot), \'dependency_roots\': roots}),\n        cb, ca, cb, shape[\'start_frame\'], shape[\'end_frame\'], hashlib.sha256(pcm).hexdigest(), float32_digest(pcm))\n    doc = {\'schema\': BOUNDED_SCHEMA, \'policy\': BOUNDED_POLICY_SHA256, \'commits\': list(commits),\n        \'geometry\': shape, \'support\': support.to_dict(), \'dependencies\': [asdict(p) for p in parts],\n        \'query\': query, \'right_evidence\': evidence,\n        \'selection\': selected_pair([query], shape[\'start_frame\'], shape[\'end_frame\']),\n        \'publication_anchor\': anchor(commits[-1]), \'selected\': True, \'target\': target,\n        \'target_sha256\': target_hash, \'inventories\': snapshot, \'dependency_roots\': roots}\n    doc[\'coverage\'] = coverage(doc); p = doc[\'publication_anchor\']\n    return Cell(support, parts, canonical(doc), pcm, commits[-1][\'scope\'][\'session_id\'],\n                p[\'chunk_id\'], p[\'start\'], p[\'end\'], p[\'sha256\'])\n'
_SOURCE60_INVENTORY_SHA256 = '7d61bbe02f30004a62784a683bc67774e6fa5c37f323ff46b34beff19a5d1be7'

def source52_inventory_oracle():
    """Frozen encoder/validator closure; unchanged helpers are source-pinned.

    Cell is replaced only by a result carrier, so the old proof producer never
    indirectly invokes a new inventory reader. The frozen validator is then
    called explicitly. This is old-code equivalence, not a saved old full proof.
    """
    import ast
    import inspect
    from types import FunctionType, SimpleNamespace
    tree = ast.parse(inspect.getsource(core))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    constants = {n.targets[0].id: n for n in tree.body if isinstance(n, ast.Assign)
        and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
    for name, expected in _SOURCE52_UNCHANGED_FUNCTIONS.items():
        assert hashlib.sha256(ast.dump(functions[name], include_attributes=False).encode()).hexdigest() == expected
    for name, expected in _SOURCE52_CONSTANTS.items():
        assert hashlib.sha256(ast.dump(constants[name], include_attributes=False).encode()).hexdigest() == expected
    assert hashlib.sha256(_SOURCE52_INVENTORY_ORACLE.encode()).hexdigest() == _SOURCE52_ORACLE_SHA256
    namespace = dict(vars(core))
    # Rebind source-pinned helpers to the frozen namespace, so their global
    # canonical/sha lookups cannot borrow the candidate's annotated functions.
    for name in _SOURCE52_UNCHANGED_FUNCTIONS:
        function = getattr(core, name)
        frozen = FunctionType(function.__code__, namespace, name, function.__defaults__, function.__closure__)
        frozen.__kwdefaults__ = function.__kwdefaults__
        namespace[name] = frozen
    assert hashlib.sha256(_SOURCE60_CANONICAL_ORACLE.encode()).hexdigest() == _SOURCE60_CANONICAL_SHA256
    exec(compile(_SOURCE60_CANONICAL_ORACLE, '<frozen-source60-canonical>', 'exec'), namespace)
    exec(compile(_SOURCE52_INVENTORY_ORACLE, '<frozen-source52-inventory>', 'exec'), namespace)
    namespace['Cell'] = lambda support, parts, proof, *args: SimpleNamespace(
        support=support, parts=parts, proof=proof)
    return namespace


def source60_inventory_oracle():
    namespace = source52_inventory_oracle()
    assert hashlib.sha256(_SOURCE60_INVENTORY_ORACLE.encode()).hexdigest() == _SOURCE60_INVENTORY_SHA256
    exec(compile(_SOURCE60_INVENTORY_ORACLE, '<frozen-source60-inventory>', 'exec'), namespace)
    return namespace


def compact_snapshot_for(case, rows):
    return core.lexical_snapshot(SID, rows, case['sources'], case['origin'],
        scan_frames=core.bounded_chain(case['commits']), schema=core.COMPACT_INVENTORY_SCHEMA)


def query_for_snapshot(case, snapshot, *, selector=core.select_support):
    shape = selector(case['target'], case['commits'], snapshot,
        case['query']['query_start_frame'], case['query']['query_end_frame'])
    pcm = b'\0\0' * (shape['end_frame'] - shape['start_frame'])
    query = deepcopy(case['query'])
    query['support_pcm16le_sha256'] = hashlib.sha256(pcm).hexdigest()
    query['capture_run'] = case['commits'][-1]['scope']['run']
    query['turns'] = [{**turn, 'label': query['capture_run'] + ':' + turn['label']} for turn in query['turns']]
    return query, pcm


def compact_final_cell(case, rows):
    snapshot = compact_snapshot_for(case, rows)
    query, pcm = query_for_snapshot(case, snapshot)
    return core.make_bounded_cell(case['commits'], query, case['evidences'][-1],
        pcm, case['target'], snapshot)


def test_compact_inventory_frozen_v1_bytes_and_full_proof_equivalence(capsys):
    oracle = source52_inventory_oracle()
    assert hashlib.sha256(_LITERAL_V1_INVENTORY).hexdigest() == _LITERAL_V1_INVENTORY_SHA256
    literal = json.loads(_LITERAL_V1_INVENTORY)
    assert cells.canonical(core.validate_inventory(literal)) == _LITERAL_V1_INVENTORY
    assert cells.canonical(oracle['validate_inventory'](literal)) == _LITERAL_V1_INVENTORY
    case = synthetic_case([0, 10, 20, 30], [(2, 8)])
    rows = [primary_row(1, 0, 30)]
    old_snapshot = oracle['lexical_snapshot'](SID, rows, case['sources'], case['origin'],
        scan_frames=oracle['bounded_chain'](case['commits']))
    query, pcm = query_for_snapshot(case, old_snapshot, selector=oracle['select_support'])
    old = oracle['make_bounded_cell'](case['commits'], query, case['evidences'][-1],
        pcm, case['target'], old_snapshot)
    oracle['validate_bounded_document'](old.proof)
    current = final_cell(case, rows)
    assert current.proof == old.proof
    assert hashlib.sha256(current.proof).digest() == hashlib.sha256(old.proof).digest()
    assert cells.validate_document(old.proof)[1:] == (current.support, current.parts)
    with capsys.disabled():print('compact_v1_equivalence', json.dumps({'inventory_bytes': len(cells.canonical(old_snapshot)),
        'proof_bytes': len(old.proof), 'proof_sha256': hashlib.sha256(old.proof).hexdigest()}, sort_keys=True))


@pytest.mark.parametrize('mode', ['lexical', 'null', 'opaque', 'noncanonical', 'stale', 'equal_parents', 'escaped'])
def test_compact_inventory_lossless_numeric_and_atom_equivalence(mode):
    case = synthetic_case([0, 64000], [(8000, 16000)])
    row = lexical_row(case, 1, [{'text': '臺', 'raw_text': '台', 'start': .5, 'end': 1.},
        {'text': '，', 'start': .5, 'end': .8}, {'text': '灣', 'raw_text': '湾', 'start': .5, 'end': 1.5},
        {'text': '好', 'start': 2., 'end': 3.}])
    if mode == 'escaped':
        row = lexical_row(case, 1, [{'text': '甲"\\', 'raw_text': '乙\\"', 'start': .5, 'end': 1.},
            {'text': '好', 'start': 2., 'end': 3.}])
    if mode == 'null':row['lexical_evidence'] = None
    elif mode == 'opaque':row['lexical_evidence'] = '{broken'
    elif mode == 'noncanonical':row['lexical_evidence'] = json.dumps(json.loads(row['lexical_evidence']), indent=1)
    elif mode == 'stale':
        value = json.loads(row['lexical_evidence']); value['source_parts'][0]['digest'] = 'f' * 64
        row['lexical_evidence'] = lexical.encoded(value).decode('ascii')
    rows = [row]
    if mode == 'equal_parents':
        other = deepcopy(row); other['id'] = 2; value = json.loads(other['lexical_evidence'])
        value['primary_sha256'] = lexical.primary_sha(SID, other)
        other['lexical_evidence'] = lexical.encoded(value).decode('ascii'); rows.append(other)
    old = snapshot_for(case, rows); compact = compact_snapshot_for(case, rows)
    assert set(compact) == {'schema', 'session_id', 'origin', 'scan_frames', 'complete', 'parents', 'sources', 'unit_count'}
    assert all(set(parent) == {'row'} for parent in compact['parents'])
    view = core.inventory_view(compact)
    assert view == old
    assert [parent['row'] for parent in view['parents']] == rows
    assert core.inventory_view(deepcopy(compact)) == view
    assert compact_snapshot_for(case, rows) == compact
    assert core.select_support(case['target'], case['commits'], compact, 0, 64000) == core.select_support(
        case['target'], case['commits'], old, 0, 64000)
    assert core.dependency_roots(case['target'], case['commits'], compact) == core.dependency_roots(
        case['target'], case['commits'], old)
    if mode in ('lexical', 'equal_parents', 'escaped'):
        assert compact['parents'][0]['row']['lexical_evidence']['encoding'] == 'canonical-json-v1'
        assert lexical.encoded(compact['parents'][0]['row']['lexical_evidence']['value']).decode('ascii') == row['lexical_evidence']
    else:assert compact['parents'][0]['row']['lexical_evidence'] == row['lexical_evidence']
    assert not core.same_numeric_snapshot(old, compact)


def retained_size_rows(case, parent_count, unit_count, atom_count):
    """Only retained scalar counts; every text/timing value here is synthetic."""
    rows = []; remaining = unit_count; reductions = unit_count - atom_count
    for index in range(parent_count):
        count = -(-remaining // (parent_count - index)); remaining -= count
        base = index * 2.0
        units = []
        for ordinal in range(count):
            position = 0 if index == 0 and ordinal <= reductions else ordinal
            start = base + position * .125
            units.append({'text': '甲', 'raw_text': '甲', 'start': start, 'end': start + .0625})
        rows.append(lexical_row(case, index + 1, units))
    return rows


@pytest.mark.parametrize('counts', [(8, 68, 65), (13, 106, 101), (12, 85, 80)])
def test_compact_inventory_retained_size_geometry_and_complete_proof(counts, monkeypatch, capsys):
    case = synthetic_case([0, 168000, 336000, 504000], [(8000, 16000)])
    rows = retained_size_rows(case, *counts)
    compact = compact_snapshot_for(case, rows); view = core.inventory_view(compact)
    assert (len(view['parents']), view['unit_count'], len(view['atoms'])) == counts
    # Observe the unchanged source52 semantic value immediately before its
    # original persisted byte check; retain any actual serialization refusal.
    oracle = source52_inventory_oracle()
    original = oracle['canonical']
    observed_old = []
    def observe_old(value, maximum=cells.PROOF_BYTES):
        if type(value) is dict and value.get('schema') == core.INVENTORY_SCHEMA:
            observed_old.append(deepcopy(value))
        return original(value, maximum)
    oracle['canonical'] = observe_old
    try:oracle['lexical_snapshot'](SID, rows, case['sources'], case['origin'], scan_frames=[0, 504000])
    except cells.Unavailable as exc:assert str(exc) == 'serialization_capacity'
    assert observed_old and observed_old[-1] == view
    assert core.inventory_view(deepcopy(compact)) == view
    measured = {'parents': counts[0], 'units': counts[1], 'atoms': counts[2],
        'compact_bytes': len(cells.canonical(compact)),
        'expanded_v1_bytes': len(lexical.encoded(view)), 'proof_bytes': None,
        'target_bytes': len(cells.canonical(case['target'])), 'proof_accepted': False}
    real = core.canonical
    def observe(value, maximum=cells.PROOF_BYTES):
        if type(value) is dict and value.get('schema') == core.BOUNDED_SCHEMA:
            measured['proof_bytes'] = len(lexical.encoded(value))
        return real(value, maximum)
    monkeypatch.setattr(core, 'canonical', observe)
    query, pcm = query_for_snapshot(case, compact)
    try:
        cell = core.make_bounded_cell(case['commits'], query, case['evidences'][-1], pcm, case['target'], compact)
    except cells.Unavailable as exc:
        assert str(exc) == 'serialization_capacity'
        assert measured['proof_bytes'] is not None and measured['proof_bytes'] > cells.PROOF_BYTES
    else:
        assert len(cell.proof) == measured['proof_bytes'] <= cells.PROOF_BYTES
        measured['proof_accepted'] = True
    measured['proof_plus_target_bytes'] = measured['proof_bytes'] + measured['target_bytes']
    measured['combined_capacity_fits'] = measured['proof_plus_target_bytes'] <= cells.PROOF_BYTES
    with capsys.disabled():print('compact_retained_size', json.dumps(measured, sort_keys=True), flush=True)


@pytest.mark.parametrize('mutation', ['unknown_schema', 'mixed_v1', 'extra_atoms', 'missing_sources',
    'unknown_tag', 'tag_extra_key', 'tag_nonobject', 'raw_alias', 'row_text', 'source_digest',
    'unit_anchor', 'unit_count'])
def test_compact_inventory_wire_mutations_refuse(mutation):
    case = synthetic_case([0, 64000], [(8000, 16000)])
    row = lexical_row(case, 1, [{'text': '甲', 'start': .5, 'end': 1.}, {'text': '乙', 'start': 2., 'end': 3.}])
    compact = compact_snapshot_for(case, [row]); changed = deepcopy(compact)
    field = changed['parents'][0]['row']['lexical_evidence']
    if mutation == 'unknown_schema':changed['schema'] = 'native-owned-bounded-inventory-v999'
    elif mutation == 'mixed_v1':changed['schema'] = core.INVENTORY_SCHEMA
    elif mutation == 'extra_atoms':changed['atoms'] = []
    elif mutation == 'missing_sources':del changed['sources']
    elif mutation == 'unknown_tag':field['encoding'] = 'canonical-json-v999'
    elif mutation == 'tag_extra_key':field['raw'] = row['lexical_evidence']
    elif mutation == 'tag_nonobject':field['value'] = []
    elif mutation == 'raw_alias':changed['parents'][0]['row']['lexical_evidence'] = row['lexical_evidence']
    elif mutation == 'row_text':changed['parents'][0]['row']['text'] += '丙'
    elif mutation == 'source_digest':changed['sources'][0]['digest'] = 'f' * 64
    elif mutation == 'unit_anchor':field['value']['units'][0]['ownership_anchor'] += .1
    else:changed['unit_count'] += 1
    with pytest.raises(cells.Unavailable):core.validate_inventory(changed)
    assert compact_snapshot_for(case, [row]) == compact


def test_compact_inventory_wire_and_count_caps_remain_hard():
    case = synthetic_case([0, 64000], [(8000, 16000)])
    row = primary_row(1, 0, 64000)
    compact = compact_snapshot_for(case, [row])
    mutations = []
    more = deepcopy(compact); more['parents'] *= 129; mutations.append(more)
    more = deepcopy(compact); more['sources'] *= 129; mutations.append(more)
    more = deepcopy(compact); more['unit_count'] = 513; mutations.append(more)
    more = deepcopy(compact); more['parents'][0]['row']['text'] = 'x' * 65536; mutations.append(more)
    for changed in mutations:
        with pytest.raises(cells.Unavailable):core.validate_inventory(changed)
    units = [{'text': '甲', 'start': i * .02, 'end': i * .02 + .01} for i in range(129)]
    with pytest.raises(cells.Unavailable, match='lexical_atom_capacity'):
        compact_snapshot_for(case, [lexical_row(case, 1, units)])


def test_compact_inventory_fit_does_not_relax_full_proof_capacity(monkeypatch, capsys):
    case = synthetic_case([0, 10, 20, 30], [(2, 8)])
    rows = [primary_row(1, 0, 30, text='x' * 30000)]
    compact = compact_snapshot_for(case, rows)
    assert len(cells.canonical(compact)) <= cells.PROOF_BYTES
    sizes = []; original = core.canonical
    def observe(value, maximum=cells.PROOF_BYTES):
        if type(value) is dict and value.get('schema') == core.BOUNDED_SCHEMA:
            sizes.append(len(lexical.encoded(value)))
        return original(value, maximum)
    monkeypatch.setattr(core, 'canonical', observe)
    query, pcm = query_for_snapshot(case, compact)
    with pytest.raises(cells.Unavailable, match='serialization_capacity'):
        core.make_bounded_cell(case['commits'], query, case['evidences'][-1], pcm, case['target'], compact)
    assert sizes and sizes[-1] > cells.PROOF_BYTES
    # The independent comparison-input cap rejects before any SQL handle use.
    with pytest.raises(cells.Unavailable, match='^bounded_certificate_capacity$'):
        automatic._read_v2(None, key='0' * 64, expected=(b'x' * cells.PROOF_BYTES, b'y'))
    with capsys.disabled():print('compact_full_proof_refusal', json.dumps({'compact_bytes': len(cells.canonical(compact)),
        'proof_bytes': sizes[-1], 'limit_bytes': cells.PROOF_BYTES}, sort_keys=True))


def test_compact_inventory_retained_id_remap_and_mixed_version_refusal():
    case = synthetic_case([0, 64000], [(8000, 16000)])
    row = lexical_row(case, (1 << 63) - 2,
        [{'text': '甲', 'start': .5, 'end': 1.}, {'text': '乙', 'start': 2., 'end': 3.}])
    actual_row = deepcopy(row); actual_row['id'] = 9
    value = json.loads(actual_row['lexical_evidence']); value['primary_sha256'] = lexical.primary_sha(SID, actual_row)
    actual_row['lexical_evidence'] = lexical.encoded(value).decode('ascii')
    planned = compact_snapshot_for(case, [row]); actual = compact_snapshot_for(case, [actual_row])
    assert core.same_numeric_snapshot(planned, actual, {row['id']: 9})
    assert not core.same_numeric_snapshot(planned, snapshot_for(case, [actual_row]), {row['id']: 9})
    refused = compact_snapshot_for(case, [dict(actual_row, lexical_evidence=None)])
    assert not core.same_numeric_snapshot(planned, refused, {row['id']: 9})


_LITERAL_V2_INVENTORY = b'{"complete":true,"origin":"0x0.0p+0","parents":[],"scan_frames":[0,1],"schema":"native-owned-bounded-inventory-v2","session_id":"bounded-synthetic-session","sources":[{"digest":"0000000000000000000000000000000000000000000000000000000000000000","duration":1.0,"id":"source","session_id":"bounded-synthetic-session","timeline_start":0.0}],"unit_count":0}'


def test_support_local_frozen_v2_bytes_and_full_proof_equivalence():
    oracle = source60_inventory_oracle()
    assert oracle['validate_commit'].__globals__['canonical'] is oracle['canonical']
    assert oracle['validate_target'].__globals__['sha'] is oracle['sha']
    literal = json.loads(_LITERAL_V2_INVENTORY)
    assert cells.canonical(core.validate_inventory(literal)) == _LITERAL_V2_INVENTORY
    assert oracle['canonical'](oracle['validate_inventory'](literal)) == _LITERAL_V2_INVENTORY
    case = synthetic_case([0, 10, 20, 30], [(2, 8)])
    rows = [primary_row(1, 0, 30)]
    old_snapshot = oracle['lexical_snapshot'](SID, rows, case['sources'], case['origin'],
        scan_frames=[0, 30], schema=core.COMPACT_INVENTORY_SCHEMA)
    assert cells.canonical(compact_snapshot_for(case, rows)) == oracle['canonical'](old_snapshot)
    query, pcm = query_for_snapshot(case, old_snapshot, selector=oracle['select_support'])
    old = oracle['make_bounded_cell'](case['commits'], query, case['evidences'][-1],
        pcm, case['target'], old_snapshot)
    oracle['validate_bounded_document'](old.proof)
    current = compact_final_cell(case, rows)
    assert current.proof == old.proof
    assert current.support.core_sha256 == old.support.core_sha256
    assert cells.validate_document(old.proof)[1:] == (current.support, current.parts)


def local_snapshot_for(case, rows):
    complete = compact_snapshot_for(case, rows)
    shape = core.select_support(case['target'], case['commits'], complete,
        case['query']['query_start_frame'], case['query']['query_end_frame'])
    return core.localize_inventory(case['target'], case['commits'], complete,
        case['query']['query_start_frame'], case['query']['query_end_frame'], shape)


@pytest.mark.parametrize('kind', ['version', 'scope', 'geometry', 'target'])
def test_support_local_strict_version_scope_geometry_and_target(kind):
    case = synthetic_case([0, 40], [(9, 12)], origin_support=(8, 16))
    local = local_snapshot_for(case, [primary_row(1, 8, 16)])
    assert local['scan_frames'] == [8, 16]
    mutations = []
    if kind == 'version':
        for schema in ('native-owned-bounded-inventory-v999', core.INVENTORY_SCHEMA,
                       core.COMPACT_INVENTORY_SCHEMA):
            mutations.append(dict(deepcopy(local), schema=schema))
        mutations.append(dict(deepcopy(local), atoms=[]))
    elif kind == 'scope':
        mutations.append(dict(deepcopy(local), scope_policy='whole-band-v1'))
        changed = deepcopy(local); del changed['scope_policy']; mutations.append(changed)
        mutations.append(dict(deepcopy(local), complete=False))
        changed = deepcopy(local); changed['sources'].append(dict(case['sources']['source'], id='extra'))
        mutations.append(changed)
        changed = deepcopy(local); changed['sources'] = []; mutations.append(changed)
    elif kind == 'geometry':
        mutations.extend(dict(deepcopy(local), scan_frames=scan) for scan in ([8, 17], [0, 41], [8, 8]))
    else:
        mutations.append(dict(deepcopy(local), scan_frames=[10, 16]))
        mutations.append(dict(deepcopy(local), scan_frames=[8, 11]))
    for changed in mutations:
        with pytest.raises(cells.Unavailable):
            core.select_support(case['target'], case['commits'], changed, 0, 40)
    query, pcm = query_for_snapshot(case, local)
    cell = core.make_bounded_cell(case['commits'], query, case['evidences'][-1], pcm, case['target'], local)
    assert json.loads(cell.proof)['inventories'] == local
    assert cell.support.start_frame == 8 and cell.support.end_frame == 16
    if kind == 'geometry':
        doc = json.loads(cell.proof); doc['geometry']['start_frame'] = 7
        with pytest.raises(cells.Unavailable, match='bounded_nondeterministic_support'):
            cells.validate_document(cells.canonical(doc))


@pytest.mark.parametrize('kind', ['exact', 'discarded_original_preference', 'empty'])
def test_support_local_exact_selector_replay_and_empty_closure(kind):
    if kind == 'discarded_original_preference':
        case = synthetic_case([0, 40], [(12, 18)], origin_support=(0, 25))
        rows = [primary_row(1, 10, 20), primary_row(2, 24, 30)]
        complete = compact_snapshot_for(case, rows)
        frozen = core.select_support(case['target'], case['commits'], complete, 0, 40)
        assert frozen == {'start_frame': 10, 'end_frame': 20}
        with pytest.raises(cells.Unavailable, match='^bounded_local_selection_changed$'):
            core.localize_inventory(case['target'], case['commits'], complete, 0, 40, frozen)
        assert core.select_support(case['target'], case['commits'], complete, 0, 40) == frozen
        return
    if kind == 'empty':
        case = synthetic_case([0, 40], [(12, 18)])
        complete = compact_snapshot_for(case, [])
        local = core.lexical_snapshot(SID, [], case['sources'], case['origin'],
            scan_frames=[0, 40], schema=core.SUPPORT_LOCAL_INVENTORY_SCHEMA)
        assert local['parents'] == [] and local['sources'] == []
        for snapshot in (complete, local):
            with pytest.raises(cells.Unavailable, match='^unresolved_lexical_gap$'):
                core.select_support(case['target'], case['commits'], snapshot, 0, 40)
        with pytest.raises(cells.Unavailable, match='^unresolved_lexical_gap$'):
            core.localize_inventory(case['target'], case['commits'], complete, 0, 40,
                {'start_frame': 0, 'end_frame': 40})
        return
    case = synthetic_case([0, 64000], [(9000, 12000)], origin_support=(8000, 16000))
    row = lexical_row(case, 1, [{'text': '甲', 'start': .5, 'end': 1.},
        {'text': '乙', 'raw_text': '乙外', 'start': 2., 'end': 3.}])
    outside = {'id': 'outside-lexical', 'session_id': SID, 'digest': 'e' * 64,
        'timeline_start': 4., 'duration': 1.}
    context = dict(outside, id='context-only', timeline_start=6.)
    case['sources'].update({outside['id']: outside, context['id']: context})
    value = json.loads(row['lexical_evidence'])
    value['source_parts'].append({**outside, 'seek_seconds': 0., 'requested_seconds': 1.,
        'analyzer_frames': [64000, 80000]})
    row['lexical_evidence'] = lexical.encoded(value).decode('ascii')
    rows = [row, primary_row(2, 52000, 56000)]
    opaque = local_snapshot_for(case, rows)
    # Two Han characters are an unsuitable granular raw unit. The original
    # support cuts its whole opaque parent, so old selection and v3 replay
    # correctly retain the entire parent rather than the intended first atom.
    assert value['units'][1]['granular_eligible'] is False
    assert opaque['scan_frames'] == [8000, 48000]
    assert len(core.inventory_view(opaque)['atoms']) == 1
    row['raw_text'] = '甲丙'
    value['units'][1].update(raw_text='丙', raw_span=[1, 2], granular_eligible=True)
    value['primary_sha256'] = lexical.primary_sha(SID, row)
    row['lexical_evidence'] = lexical.encoded(value).decode('ascii')
    assert lexical.current_inventory(None, SID, row, source_lookup=case['sources'].get) is not None
    local = local_snapshot_for(case, rows)
    assert local['schema'] == core.SUPPORT_LOCAL_INVENTORY_SCHEMA
    assert local['scope_policy'] == core.SUPPORT_LOCAL_SCOPE_POLICY
    assert set(local) == {'schema', 'scope_policy', 'session_id', 'origin', 'scan_frames',
        'complete', 'parents', 'sources', 'unit_count'}
    assert local['scan_frames'] == [8000, 16000]
    assert [source['id'] for source in local['sources']] == ['outside-lexical', 'source']
    view = core.inventory_view(local)
    assert view['parents'][0]['row'] == row and len(view['parents']) == 1
    assert view['unit_count'] == 2 and len(view['atoms']) == 2
    assert view['atoms'][1]['end_frame'] == 48000
    assert local['parents'][0]['row']['lexical_evidence']['encoding'] == 'canonical-json-v1'
    query, pcm = query_for_snapshot(case, local)
    cell = core.make_bounded_cell(case['commits'], query, case['evidences'][-1], pcm, case['target'], local)
    doc, support, _ = cells.validate_document(cell.proof)
    assert [support.start_frame, support.end_frame] == local['scan_frames']
    assert doc['inventories'] == local
    roots = core.dependency_roots(case['target'], case['commits'], local)
    assert any(root['cid'] == 'outside-lexical' and root['kind'] & 4 for root in roots)
    # A missing independent lexical source must not silently turn the whole
    # retained parent opaque, even if someone substitutes its exact raw bytes.
    missing = deepcopy(local)
    missing['sources'] = [source for source in missing['sources'] if source['id'] != 'outside-lexical']
    missing['parents'][0]['row']['lexical_evidence'] = row['lexical_evidence']
    with pytest.raises(cells.Unavailable, match='bounded_inventory_source_changed'):
        core.validate_inventory(missing)


@pytest.mark.parametrize('kind', ['inventory', 'full_proof', 'combined_certificate'])
def test_support_local_caps_remain_hard(kind):
    case = synthetic_case([0, 10, 20, 30], [(2, 8)])
    if kind == 'inventory':
        local = local_snapshot_for(case, [primary_row(1, 0, 30)])
        more = deepcopy(local); more['parents'][0]['row']['text'] = 'x' * cells.PROOF_BYTES
        expected_bytes = len(lexical.encoded(more))
        with pytest.raises(cells.Unavailable, match='^serialization_capacity$') as caught:
            core.validate_inventory(more)
        assert caught.value.encoded_bytes == expected_bytes > cells.PROOF_BYTES
        assert caught.value.maximum_bytes == cells.PROOF_BYTES
        assert caught.value.capacity_phase == 'retained_inventory'
        for changed in (dict(local, parents=local['parents'] * 129),
                dict(local, sources=local['sources'] * 129), dict(local, unit_count=513)):
            with pytest.raises(cells.Unavailable):core.validate_inventory(changed)
        return
    if kind == 'full_proof':
        local = local_snapshot_for(case, [primary_row(1, 0, 30, text='x' * 30000)])
        assert len(cells.canonical(local)) <= cells.PROOF_BYTES
        query, pcm = query_for_snapshot(case, local)
        with pytest.raises(cells.Unavailable, match='^serialization_capacity$') as caught:
            core.make_bounded_cell(case['commits'], query, case['evidences'][-1], pcm, case['target'], local)
        assert caught.value.encoded_bytes > caught.value.maximum_bytes == cells.PROOF_BYTES
        assert caught.value.capacity_phase == 'full_proof'
        return
    # The comparison-input certificate guard is independent of inventory and
    # complete-proof limits and runs before any SQL handle is accessed.
    with pytest.raises(cells.Unavailable, match='^bounded_certificate_capacity$'):
        automatic._read_v2(None, key='0' * 64, expected=(b'x' * cells.PROOF_BYTES, b'y'))
    assert (cells.PROOF_BYTES, core.COMPACT_BYTES, core.MAX_BOUND_COMMITS,
        core.MAX_ORIGINS, core.MAX_PLAN_PARENTS, cells.MAX_PARTS,
        core.MAX_PLAN_UNITS, core.MAX_PLAN_ATOMS, cells.MAX_TURNS,
        core.SUPPORT_FRAMES) == (65536, 2048, 4, 8, 128, 128, 512, 128, 256, 320000)


def test_support_local_canonical_actual_bytes_and_innermost_phases(monkeypatch):
    oracle = source60_inventory_oracle()
    value = {'text': '甲', 'literal': '"\\'}
    before = oracle['canonical'](value)
    assert cells.canonical(value) == before
    with pytest.raises(cells.Unavailable) as old:
        oracle['canonical'](value, len(before) - 1)
    with pytest.raises(cells.Unavailable) as current:
        cells.canonical(value, len(before) - 1)
    assert current.value.args == old.value.args == ('serialization_capacity',)
    assert str(current.value) == str(old.value)
    assert current.value.encoded_bytes == len(before)
    assert current.value.maximum_bytes == len(before) - 1
    assert not hasattr(old.value, 'encoded_bytes') and not hasattr(current.value, 'capacity_phase')
    case = synthetic_case([0, 40], [(9, 12)], origin_support=(8, 16))
    local = local_snapshot_for(case, [primary_row(1, 8, 16)])
    query, pcm = query_for_snapshot(case, local)
    real_sha, real_canonical = core.sha, core.canonical
    for phase in ('retained_inventory', 'core_preimage', 'dependency_roots', 'full_proof'):
        refusal = cells.Unavailable('serialization_capacity')
        refusal.encoded_bytes = 65537; refusal.maximum_bytes = 65536
        observed = []
        def fail_sha(value):
            if (phase == 'retained_inventory' and value is local
                    or phase == 'core_preimage' and type(value) is dict and 'inventories_sha256' in value):
                observed.append(value)
                raise refusal
            return real_sha(value)
        def fail_canonical(value, maximum=cells.PROOF_BYTES):
            if (phase == 'dependency_roots' and maximum == 4096
                    or phase == 'full_proof' and type(value) is dict and value.get('schema') == core.BOUNDED_SCHEMA):
                observed.append(value)
                raise refusal
            return real_canonical(value, maximum)
        with monkeypatch.context() as patched:
            patched.setattr(core, 'sha', fail_sha); patched.setattr(core, 'canonical', fail_canonical)
            with pytest.raises(cells.Unavailable) as caught:
                core.make_bounded_cell(case['commits'], query, case['evidences'][-1], pcm, case['target'], local)
        assert caught.value is refusal and len(observed) == 1
        assert refusal.capacity_phase == phase
        assert refusal.args == ('serialization_capacity',)
    # An already known innermost phase survives a wider catch unchanged.
    refusal = cells.Unavailable('serialization_capacity'); refusal.capacity_phase = 'retained_inventory'
    def fail_preimage(value):
        if type(value) is dict and 'inventories_sha256' in value:raise refusal
        return real_sha(value)
    with monkeypatch.context() as patched:
        patched.setattr(core, 'sha', fail_preimage)
        with pytest.raises(cells.Unavailable) as caught:
            core.make_bounded_cell(case['commits'], query, case['evidences'][-1], pcm, case['target'], local)
    assert caught.value is refusal and refusal.capacity_phase == 'retained_inventory'
