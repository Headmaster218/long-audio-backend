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
