"""Finite, fresh, synthetic lexical-projection contracts.

These tests are model-free read/projection tests, not actual ASR evidence. They
never call persistence, open audio or historical databases, or load a model.
Only this scoped fixture adds the dev35 inventory schema to the HTTP fixture.
"""
from copy import deepcopy
import json
import sqlite3

import pytest

from audio_backend import lexical_evidence as le
from audio_backend import transcript_contract as c
from audio_backend import transcript_http as http
from audio_backend import transcript_projection as projection
from audio_backend import transcript_store as ts
from audio_backend.native_owned_support_cache import ReadError
from test_transcript_http import Fixture, SID
from test_transcript_projection import candidate, selected, texts


MODE = 'lexical-estimated-v1'


def unit(text, start, end, *, raw_text=None, track=None):
    value = dict(text=text, raw_text=text if raw_text is None else raw_text,
                 start=start, end=end, ownership_anchor=start,
                 anchor_kind='ctc_emission')
    if track is not None:
        value['source_track_id'] = track
    return value


def four_units():
    return [unit(char, index, index + 1) for index, char in enumerate('甲乙丙丁')]


def assembled(units, key):
    """Independent fixture assembly for complete non-phrase stored units."""
    original = ''.join(item[key] for item in units)
    left = len(original) - len(original.lstrip())
    final, cursor, spans = original.strip(), 0, []
    for item in units:
        stop = cursor + len(item[key])
        spans.append([max(0, min(len(final), cursor - left)),
                      max(0, min(len(final), stop - left))])
        cursor = stop
    return final, spans


class LexicalFixture(Fixture):
    def __init__(self, path):
        super().__init__(path)
        with self.db() as db:
            db.execute('BEGIN')
            le.init_schema(db)
            db.execute('DELETE FROM segments')

    def segment(self, ident, start, end, text, *, cid='chunk', raw_text=None,
                offset=0.):
        # Named columns remain valid before and after this subclass's migration.
        with self.db() as db:
            db.execute('''INSERT INTO segments
                (id,chunk_id,start,end,text,raw_text,asr_backend,timestamp_kind,
                 confidence,speaker_local_id,speaker_window_start,speaker_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (ident, cid, start, end, text,
                 text if raw_text is None else raw_text, 'sensevoice',
                 le.PRIMARY_TIMESTAMP, .8, 'S1', offset, 'private-person'))

    def row(self, ident):
        with self.db() as db:
            return dict(db.execute('SELECT * FROM segments WHERE id=?',
                                   (ident,)).fetchone())

    def parent(self, ident):
        return c.primary_evidence(SID, self.row(ident))

    def install(self, ident, units, *, cid='chunk', core_start=0., core_end=21.,
                analyzer_start=0., requested_seconds=None):
        """Write an explicitly synthetic canonical inventory, never persist()."""
        units = deepcopy(units)
        display, display_spans = assembled(units, 'text')
        raw, raw_spans = assembled(units, 'raw_text')
        for item, ds, rs in zip(units, display_spans, raw_spans):
            item.update(display_span=ds, raw_span=rs,
                        granular_eligible=(le._group_supported(item['text'])
                                           and le._group_supported(item['raw_text'])))
        with self.db() as db:
            source = dict(db.execute('''SELECT id,session_id,digest,timeline_start,
                duration FROM chunks WHERE id=?''', (cid,)).fetchone())
        origin = source['timeline_start']
        start = origin + core_start + (units[0]['start'] - core_start)
        end = origin + core_start + (max(item['end'] for item in units) - core_start)
        self.segment(ident, start, end, display, cid=cid, raw_text=raw,
                     offset=core_start)
        requested = (source['duration'] - analyzer_start
                     if requested_seconds is None else requested_seconds)
        part = dict(source, seek_seconds=analyzer_start,
                    requested_seconds=requested,
                    analyzer_frames=[0, round(requested * c.RATE)])
        value = dict(schema=le.SCHEMA,
                     primary_sha256=c.primary_evidence(SID, self.row(ident))['content_sha256'],
                     source_provenance=le.SOURCE_NOTE, source_parts=[part],
                     timestamp_semantics=le.TIME_NOTE,
                     coordinates=dict(axis='chunk_relative_seconds',
                                      core_start=core_start, core_end=core_end,
                                      analyzer_start=analyzer_start),
                     units=units)
        self.replace_inventory(ident, value)
        return value

    def replace_inventory(self, ident, value):
        raw = c.encoded(value).decode('ascii') if isinstance(value, dict) else value
        with self.db() as db:
            db.execute('UPDATE segments SET lexical_evidence=? WHERE id=?', (raw, ident))
        return raw

    def current(self, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM segments WHERE id=?', (ident,)).fetchone()
            return le.current_inventory(db, SID, row)

    def groups(self, ident):
        saved = self.current(ident)
        assert saved is not None, 'the synthetic canonical inventory must validate'
        return projection.lexical_groups(self.parent(ident), saved)

    def lexical_page(self, **kwargs):
        return self.page(projection=MODE, **kwargs)

    def lexical_evidence(self, ident, first, end, *, sha=None):
        if sha is None:
            sha = self.current(ident)['inventory_sha256']
        ref = c.lexical_reference(ident, sha, first, end)
        return json.loads(self.reader.evidence(SID, ref))


@pytest.fixture
def data(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('projection fixtures must not call production persist')
    monkeypatch.setattr(le, 'persist', forbidden)
    return LexicalFixture(tmp_path / 'fresh-synthetic-lexical.sqlite3')


def pure(data, jobs, *, ident=1, block=0, extra=()):
    parent = data.parent(ident)
    groups = data.groups(ident)
    lexical = {} if groups is None else {ident: groups}
    return projection.project(block, [parent, *extra], jobs, lexical=lexical,
                              policy_hash=c.LEXICAL_POLICY_HASH)


def replaced_spans(value):
    return [span for alternative in value['alternatives']
            if alternative['reason'] == 'replaced_primary'
            for span in alternative['replaced_spans']]


def assert_primary_only(value):
    assert value['reading'] == value['baseline_reading']
    assert value['selected_runs'] == value['alternatives'] == []
    state = value['lexical_projection']
    assert state['complete'] is False
    assert state['selection_performed'] is False
    assert state['alternatives_complete'] is False
    assert len(c.encoded(state)) < 2048


def assert_conserved(data, ident, values):
    """Recover each original codepoint exactly once, independently per stream."""
    if isinstance(values, dict):
        values = [values]
    parent, spans = data.parent(ident), {}
    for value in values:
        for item in value['reading'] + replaced_spans(value):
            if item['source'] != 'primary_lexical':
                continue
            if item.get('reference_only'):
                item = json.loads(data.reader.evidence(SID, item['source_ref']))
            if item['parent_source_ref'] == parent['source_ref']:
                spans[item['source_ref']] = item
    assert spans, 'a granular partition must include kept and replaced evidence'
    ordered = sorted(spans.values(), key=lambda item: item['unit_range'][0])
    saved = data.current(ident)['inventory']
    cursor = 0
    for item in ordered:
        first, end = item['unit_range']
        assert first == cursor < end
        cursor = end
        assert 'canonical_asr' not in item and 'inventory' not in item
        assert item['inventory_sha256'] == data.current(ident)['inventory_sha256']
    assert cursor == len(saved['units'])
    for text_key, span_key in (('text', 'display_span'), ('raw_text', 'raw_span')):
        cursor, pieces = 0, []
        for item in ordered:
            first, end = item[span_key]
            assert first == cursor <= end
            assert item[text_key] == parent[text_key][first:end]
            cursor = end
            pieces.append(item[text_key])
        assert cursor == len(parent[text_key])
        assert ''.join(pieces).encode('utf-8') == parent[text_key].encode('utf-8')


def test_interior_chinese_span_partition(data):
    data.install(1, four_units())
    job = data.job(1, 1, 3, labels=('S1',), text='合成替代')
    legacy, value = data.page(), data.lexical_page()
    assert selected(legacy) == []
    assert selected(value) == [job['key']]
    assert value['baseline_reading'] == legacy['baseline_reading']
    assert texts(value['reading']) == ['甲', '合成替代', '丁']
    assert [item['unit_range'] for item in replaced_spans(value)] == [[1, 3]]
    assert value['selected_runs'][0]['replaced_span_refs'] == [
        replaced_spans(value)[0]['source_ref']]
    assert value['alternatives'][0]['primary_reading'] == legacy['baseline_reading']
    assert all(item['source'] != 'primary' for item in value['reading'])
    assert value['lexical_projection']['complete'] is True
    assert_conserved(data, 1, value)


@pytest.mark.parametrize('middle', [" can't", ' word-piece!', ' 123,'])
def test_ascii_joined_units_and_punctuation_partition(data, middle):
    original = [unit('(', 0, .1), unit('first', .1, 1), unit(',', 1, 1.1),
                unit('', 1.1, 1.1), unit(middle, 1.2, 2),
                unit('!', 2, 2.1), unit(' last', 2.2, 3), unit(')', 3, 3.1)]
    data.install(1, original)
    groups = data.groups(1)
    assert [item['unit_range'] for item in groups] == [[0, 4], [4, 6], [6, 8]]
    assert [item['text'] for item in groups] == ['(first,', middle + '!', ' last)']
    value = pure(data, [candidate('middle', groups[1]['start_frame'],
                                  groups[1]['end_frame'])])
    assert selected(value) == ['middle']
    assert [span['unit_range'] for span in replaced_spans(value)] == [[4, 6]]
    assert_conserved(data, 1, value)


@pytest.mark.parametrize('decor_raw,decor_display', [('', '，'), ('!', ''), ('  ', '\t')])
def test_raw_display_independent_empty_contributions(data, decor_raw, decor_display):
    data.install(1, [unit('  first', 0, 1, raw_text=' A'),
                     unit(decor_display, 1, 1.1, raw_text=decor_raw),
                     unit(' second', 1.2, 2, raw_text=' BBB'),
                     unit(' last  ', 2.2, 3, raw_text=' Z ')])
    groups = data.groups(1)
    assert [item['unit_range'] for item in groups] == [[0, 2], [2, 3], [3, 4]]
    assert groups[0]['display_span'] != groups[0]['raw_span']
    value = pure(data, [candidate('replacement', groups[1]['start_frame'],
                                  groups[1]['end_frame'])])
    assert selected(value) == ['replacement']
    assert replaced_spans(value)[0]['text'] == ' second'
    assert replaced_spans(value)[0]['raw_text'] == ' BBB'
    assert_conserved(data, 1, value)


@pytest.mark.parametrize('case', [
    'unicode_word', 'multiple_cjk', 'emoji', 'combining', 'internal_space',
    'symbol', 'stream_disagreement', 'mixed_tracks', 'missing_track',
    'decoration_only', 'zero_lexical_interval', 'invalid_partition',
])
def test_unsupported_or_ambiguous_segment_stays_opaque(data, case):
    units = four_units()
    unsupported = {'unicode_word': 'café', 'multiple_cjk': '中文',
                   'emoji': '👩\u200d💻', 'combining': 'e\u0301',
                   'internal_space': 'two words', 'symbol': '+'}
    if case in unsupported:
        units[1].update(text=unsupported[case], raw_text=unsupported[case])
    elif case == 'stream_disagreement':
        units[1].update(text='', raw_text='word')
    elif case in ('mixed_tracks', 'missing_track'):
        for item in units:
            item['source_track_id'] = 'track-one'
        if case == 'mixed_tracks':
            units[1]['source_track_id'] = 'track-two'
        else:
            units[1].pop('source_track_id')
    elif case == 'decoration_only':
        for item in units:
            item.update(text='!', raw_text='!')
    elif case == 'zero_lexical_interval':
        units[1]['end'] = units[1]['start']
    inventory = data.install(1, units)
    if case == 'invalid_partition':
        inventory['units'][1]['display_span'][0] += 1
        data.replace_inventory(1, inventory)
        assert data.current(1) is None
    else:
        assert data.groups(1) is None
    data.job(1, 1, 3, labels=('S1',))
    value = data.lexical_page()
    assert selected(value) == []
    assert value['reading'] == value['baseline_reading']
    assert value['lexical_projection']['complete'] is True
    assert value['alternatives'][0]['reason'] == 'partial_primary_unresolved'


@pytest.mark.parametrize('case', ['equal', 'decreasing'])
def test_equal_anchors_merge_and_decreasing_anchors_refuse(data, case):
    units = four_units()
    if case == 'equal':
        units[2].update(start=1, ownership_anchor=1)
    else:
        units[1].update(start=2, ownership_anchor=2, end=3)
        units[2].update(start=1, ownership_anchor=1, end=3)
    data.install(1, units)
    groups = data.groups(1)
    if case == 'equal':
        assert [item['unit_range'] for item in groups] == [[0, 1], [1, 3], [3, 4]]
        value = pure(data, [candidate('merged', c.RATE, 3 * c.RATE)])
        assert selected(value) == ['merged']
        assert_conserved(data, 1, value)
        assert selected(pure(data, [candidate('cannot-split', c.RATE, 2 * c.RATE)])) == []
    else:
        assert groups is None
        assert selected(pure(data, [candidate('no-repair', c.RATE, 3 * c.RATE)])) == []


@pytest.mark.parametrize('left_delta,right_delta', [
    (-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 0), (0, 1),
    (1, -1), (1, 0), (1, 1),
])
def test_group_half_open_boundary_matrix(data, left_delta, right_delta):
    data.install(1, four_units())
    value = pure(data, [candidate('boundary', c.RATE + left_delta,
                                  3 * c.RATE + right_delta)])
    assert bool(selected(value)) is (left_delta == right_delta == 0)
    if selected(value):
        assert [span['unit_range'] for span in replaced_spans(value)] == [[1, 3]]
        assert_conserved(data, 1, value)
    else:
        assert not replaced_spans(value)
    # Touching the half-open edge does not remove either adjacent group.
    exact = pure(data, [candidate('one', c.RATE, 2 * c.RATE)])
    assert [span['unit_range'] for span in replaced_spans(exact)] == [[1, 2]]


@pytest.mark.parametrize('case', ['adjacent_bisects_group', 'one_frame_gap', 'empty_internal_gap'])
def test_adjacent_union_and_gap_refusal(data, case):
    units = four_units()
    if case == 'empty_internal_gap':
        units = [unit('甲', 0, 1), unit('乙', 2, 3), unit('丙', 3, 4)]
    data.install(1, units)
    if case == 'empty_internal_gap':
        jobs = [candidate('gap-only', round(1.25 * c.RATE), round(1.75 * c.RATE))]
    else:
        join = round(2.5 * c.RATE)
        jobs = [candidate('left', c.RATE, join),
                candidate('right', join + (case == 'one_frame_gap'), 3 * c.RATE)]
    value = pure(data, jobs)
    if case == 'adjacent_bisects_group':
        assert selected(value) == ['left', 'right']
        assert len(value['selected_runs']) == 1
        assert [span['unit_range'] for span in replaced_spans(value)] == [[1, 3]]
        assert_conserved(data, 1, value)
    else:
        assert selected(value) == []
        assert not replaced_spans(value)


@pytest.mark.parametrize('opaque_end,allowed', [(3.5, False), (2.5, True)])
def test_simultaneous_opaque_parent_blocks_partial_run(data, opaque_end, allowed):
    data.install(1, four_units())
    data.segment(2, 1.5, opaque_end, 'simultaneous historical')
    value = pure(data, [candidate('interior', c.RATE, 3 * c.RATE)],
                 extra=[data.parent(2)])
    assert bool(selected(value)) is allowed
    if allowed:
        assert data.parent(2)['source_ref'] in value['selected_runs'][0]['primary_source_refs']
        assert_conserved(data, 1, value)
    else:
        assert not replaced_spans(value)


@pytest.mark.parametrize('historical', ['null', 'stale'])
def test_mixed_historical_and_current_inventory(data, historical):
    data.install(1, four_units())
    if historical == 'null':
        data.segment(2, 5, 9, 'historical whole')
    else:
        value = data.install(2, [unit('old', 5, 7), unit(' tail', 7, 9)])
        value['primary_sha256'] = '0' * 64
        data.replace_inventory(2, value)
    assert data.current(2) is None
    first = data.job(1, 1, 3, labels=('S1',))
    second = data.job(2, 5, 9, labels=('S1',))
    value = data.lexical_page()
    assert selected(value) == [first['key'], second['key']]
    assert len(value['baseline_reading']) == 2
    assert any(data.parent(2)['source_ref'] in run['primary_source_refs']
               for run in value['selected_runs'])
    assert_conserved(data, 1, value)


@pytest.mark.parametrize('origin', [123.456789, 1000000000.1234567])
def test_nonzero_origin_uses_original_arithmetic(data, origin):
    data.chunk('offset', origin, origin + 21)
    inventory = data.install(1, [unit('前', 10.8, 11.1), unit('中', 11.2, 11.7),
                                 unit('後', 11.8, 12.1)], cid='offset',
                             core_start=11., core_end=12., analyzer_start=10.,
                             requested_seconds=2.)
    parent, groups = data.parent(1), data.groups(1)
    assert parent['canonical_asr']['start'] == origin + 11. + (10.8 - 11.)
    for group, original in zip(groups, inventory['units']):
        assert group['start_frame'] == c.primary_frame(
            origin + 11. + (original['start'] - 11.))
        assert group['end_frame'] == c.primary_frame(
            origin + 11. + (original['end'] - 11.), end=True)
    evidence = data.lexical_evidence(1, 1, 2)
    assert evidence['units'] == inventory['units'][1:2]
    assert evidence['coordinates'] == inventory['coordinates']
    assert evidence['session_unit_times'] == [{
        key: origin + 11. + (inventory['units'][1][key] - 11.)
        for key in ('start', 'end', 'ownership_anchor')}]


@pytest.mark.parametrize('case', ['estimated_end', 'negative_core_overhang', 'one_frame_end'])
def test_overhang_is_not_clamped(data, case):
    if case == 'negative_core_overhang':
        units = [unit('前', .8, 1.4), unit('中', 1.5, 2), unit('後', 2, 3.1)]
        data.install(1, units, core_start=1., core_end=3.)
        support = (c.RATE, round(1.4 * c.RATE))
        assert data.groups(1)[0]['start_frame'] == round(.8 * c.RATE)
        assert data.parent(1)['canonical_asr']['speaker_window_start'] == 1.
    else:
        units = four_units()
        units[1]['end'] = 2.2 if case == 'estimated_end' else 2 + 1 / c.RATE
        data.install(1, units)
        support = (c.RATE, 2 * c.RATE)
        assert data.groups(1)[1]['end_frame'] > support[1]
    value = pure(data, [candidate('cannot-clamp', *support)])
    assert selected(value) == []
    assert data.current(1)['inventory']['units'][0]['start'] == units[0]['start']


def test_cross_page_fragment_home_and_numeric_order(data):
    data.chunk('edge', 58, 65)
    data.install(1, [unit(chr(0x4e00 + index), index / 4, (index + 1) / 4)
                     for index in range(14)], cid='edge', core_end=7.)
    groups = data.groups(1)
    jobs = [candidate('replace-two', groups[2]['start_frame'], groups[2]['end_frame']),
            candidate('replace-eleven', groups[11]['start_frame'], groups[11]['end_frame'])]
    first, second = pure(data, jobs), pure(data, jobs, block=1)
    kept = [item for page in (first, second) for item in page['reading']
            if item['source'] == 'primary_lexical' and not item['reference_only']]
    assert [item['unit_range'] for item in kept] == [[0, 2], [3, 11], [12, 14]]
    assert [item['home_block'] for item in kept] == [0, 0, 1]
    crossing = kept[1]
    reference = next(item for item in second['reading']
                     if item['source_ref'] == crossing['source_ref'])
    assert reference['reference_only'] is True
    assert 'text' not in reference and 'raw_text' not in reference
    assert second['baseline_reading'][0]['reference_only'] is True
    assert [item['unit_range'] for item in replaced_spans(first)] == [[2, 3]]
    assert [item['unit_range'] for item in replaced_spans(second)] == [[11, 12]]
    assert_conserved(data, 1, [first, second])
    permuted = projection.project(0, [data.parent(1)], list(reversed(jobs)),
                                  lexical={1: groups}, policy_hash=c.LEXICAL_POLICY_HASH)
    assert permuted == first
    saved_jobs = [data.job(number, groups[index]['start_frame'] / c.RATE,
                           groups[index]['end_frame'] / c.RATE,
                           labels=('S1',), cid='edge', text=f'synthetic-{index}')
                  for number, index in ((1, 2), (2, 11))]
    forward = [data.lexical_page(block=block) for block in (0, 1)]
    reverse = {block: data.lexical_page(block=block) for block in (1, 0)}
    for block, (actual, expected) in enumerate(zip(forward, (first, second))):
        assert actual == reverse[block]
        assert actual['optional_closure']['complete'] is True
        assert actual['lexical_projection']['complete'] is True
        assert actual['baseline_reading'] == expected['baseline_reading']
        assert [item for item in actual['reading'] if item['source'] == 'primary_lexical'] == [
            item for item in expected['reading'] if item['source'] == 'primary_lexical']
        assert selected(actual) == [saved_jobs[block]['key']]
    assert_conserved(data, 1, forward)


@pytest.mark.parametrize('case', ['query_accept', 'query_reject', 'cursor', 'legacy_bytes'])
def test_policy_mode_query_and_cursor_matrix(data, case):
    if case == 'query_accept':
        for raw, expected in (
            (b'', {}), (b'projection=whole-primary-v1', {'projection': 'whole-primary-v1'}),
            (b'projection=lexical-estimated-v1', {'projection': MODE}),
            (b'block=1&cursor=abc&projection=lexical-estimated-v1',
             {'block': 1, 'cursor': 'abc', 'projection': MODE}),
        ):
            assert http.parse_query(raw) == expected
    elif case == 'query_reject':
        for raw, flags in (
            (b'projection=lexical-estimated-v1&projection=lexical-estimated-v1', {}),
            (b'projection=unknown', {}), (b'projection=Lexical-estimated-v1', {}),
            (b'projection=lexical%2Destimated-v1', {}), (b'extra=1', {}),
            (b'projection=lexical-estimated-v1', {'detail': True}),
            (b'projection=lexical-estimated-v1', {'audit': True}),
            (b'block=0&cursor=x&projection=whole-primary-v1&extra=1', {}),
            (b'x' * 1025, {}),
        ):
            with pytest.raises(ReadError) as exc:
                http.parse_query(raw, **flags)
            assert exc.value.status_code == 409
    else:
        data.install(1, four_units())
        data.job(1, 1, 3, labels=('S1',))
        legacy = data.page()
        explicit = data.page(projection='whole-primary-v1')
        assert c.encoded(legacy) == c.encoded(explicit)
        lexical = data.lexical_page()
        assert legacy['policy_hash'] == legacy['policy_sha256'] == c.POLICY_HASH
        assert lexical['policy_hash'] == lexical['policy_sha256'] == c.LEXICAL_POLICY_HASH
        assert c.POLICY_HASH == c.digest(c.POLICY) != c.LEXICAL_POLICY_HASH
        assert c.LEXICAL_POLICY['selection'] == 'whole-lexical-span-estimated-boundaries-v1'
        if case == 'cursor':
            assert legacy['revision'] != lexical['revision']
            assert data.lexical_page(cursor=lexical['next_cursor'])['block'] == 1
            assert data.page(cursor=legacy['next_cursor'])['block'] == 1
            for token, kwargs in ((lexical['next_cursor'], {}),
                                  (lexical['next_cursor'], {'projection': 'whole-primary-v1'}),
                                  (legacy['next_cursor'], {'projection': MODE})):
                with pytest.raises(ReadError) as exc:
                    data.page(cursor=token, **kwargs)
                assert exc.value.status_code == 409
            audit = data.audit()
            assert audit['policy_sha256'] == c.POLICY_HASH
            assert audit['revision'] == legacy['revision']


@pytest.mark.parametrize('case', [
    'whole', 'decoration_only', 'unaligned', 'single', 'maximum_syntax',
    'policy', 'digest', 'empty_range', 'negative', 'reversed', 'over_512',
    'past_inventory', 'missing_parent', 'null', 'malformed', 'primary', 'source',
    'cross_session', 'noncanonical',
])
def test_lexical_reference_policy_and_currentness(data, case):
    inventory = data.install(1, [unit('甲', 0, 1), unit('，', 1, 1.2),
                                 unit('乙', 1.2, 2), unit('丙', 2, 3)])
    current = data.current(1)
    sha = current['inventory_sha256']
    ranges = {'whole': (0, 4), 'decoration_only': (1, 2), 'unaligned': (1, 3), 'single': (2, 3)}
    first, end = ranges.get(case, (1, 2))
    ref = c.lexical_reference(1, sha, first, end)
    if case == 'maximum_syntax':
        longest = c.lexical_reference((1 << 63) - 1, sha, 511, 512)
        assert len(longest) == 159
        assert c.parse_reference(longest)[0] == 'lexical'
        return
    expected_status = 409
    if case == 'policy':
        ref = ref.replace(c.LEXICAL_POLICY_HASH, '0' * 64)
    elif case == 'digest':
        ref = ref.replace(sha, '0' * 64)
    elif case in ('empty_range', 'negative', 'reversed', 'over_512', 'past_inventory'):
        suffix = {'empty_range': '1-1', 'negative': '-1-2', 'reversed': '2-1',
                  'over_512': '511-513', 'past_inventory': '4-5'}[case]
        ref = ref.rsplit('.', 1)[0] + '.' + suffix
    elif case == 'missing_parent':
        with data.db() as db:
            db.execute('DELETE FROM segments WHERE id=1')
        expected_status = 404
    elif case == 'null':
        data.replace_inventory(1, None)
        expected_status = 404
    elif case == 'malformed':
        data.replace_inventory(1, '{}')
    elif case == 'primary':
        raw = data.row(1)['lexical_evidence']
        with data.db() as db:
            db.execute("UPDATE segments SET text='edited' WHERE id=1")
        data.replace_inventory(1, raw)
    elif case == 'source':
        with data.db() as db:
            db.execute("UPDATE chunks SET digest=? WHERE id='chunk'", ('a' * 64,))
    elif case == 'noncanonical':
        data.replace_inventory(1, ' ' + data.row(1)['lexical_evidence'])
    if case in ranges:
        evidence = json.loads(data.reader.evidence(SID, ref))
        assert evidence['source_ref'] == ref and evidence['source'] == 'primary_lexical'
        assert evidence['range_semantics'] == 'stored_complete_unit_range'
        assert evidence['selection_eligibility'] == evidence['selection_status'] == 'not_asserted'
        assert evidence['units'] == inventory['units'][first:end]
        assert evidence['unit_range'] == [first, end]
        assert evidence['parent_source_ref'] == data.parent(1)['source_ref']
        assert evidence['inventory_sha256'] == sha
        for text_key, span_key in (('text', 'display_span'), ('raw_text', 'raw_span')):
            span = [inventory['units'][first][span_key][0], inventory['units'][end - 1][span_key][1]]
            assert evidence[span_key] == span
            assert evidence[text_key] == data.parent(1)[text_key][span[0]:span[1]]
    else:
        if case == 'cross_session':
            expected_status = 404
        with pytest.raises(ReadError) as exc:
            data.reader.evidence('other' if case == 'cross_session' else SID, ref)
        assert exc.value.status_code == expected_status


@pytest.mark.parametrize('mutation', ['person', 'inventory', 'primary', 'source'])
def test_mutation_restart_and_baseline_conservation(data, mutation):
    inventory = data.install(1, four_units())
    data.job(1, 1, 3, labels=('S1',))
    before = data.lexical_page()
    assert_conserved(data, 1, before)
    old_ref = replaced_spans(before)[0]['source_ref']
    old_parent = data.parent(1)
    if mutation == 'person':
        with data.db() as db:
            db.execute("UPDATE segments SET speaker_id='another-private-person' WHERE id=1")
    elif mutation == 'inventory':
        inventory['coordinates']['core_end'] = 20.
        data.replace_inventory(1, inventory)
    elif mutation == 'primary':
        with data.db() as db:
            db.execute("UPDATE segments SET text='edited primary' WHERE id=1")
        assert data.row(1)['lexical_evidence'] is None
    else:
        with data.db() as db:
            db.execute("UPDATE chunks SET digest=? WHERE id='chunk'", ('a' * 64,))
    data.reader = ts.TranscriptStore(data)
    after = data.lexical_page()
    assert data.lexical_page() == after
    with pytest.raises(ReadError) as exc:
        data.lexical_page(cursor=before['next_cursor'])
    assert exc.value.status_code == 409
    if mutation == 'person':
        assert after['reading'] == before['reading']
        assert json.loads(data.reader.evidence(SID, old_ref))['parent_source_ref'] == old_parent['source_ref']
    else:
        with pytest.raises(ReadError) as exc:
            data.reader.evidence(SID, old_ref)
        assert exc.value.status_code == (404 if mutation == 'primary' else 409)
    if mutation != 'primary':
        assert after['baseline_reading'] == before['baseline_reading']
        assert data.parent(1) == old_parent
    if mutation in ('person', 'inventory'):
        assert_conserved(data, 1, after)
    else:
        assert after['reading'] == after['baseline_reading']


@pytest.mark.parametrize('limit', [
    'units', 'groups', 'atoms', 'primary_bytes', 'cumulative_bytes', 'inventory_bytes',
    'bindings', 'response', 'malformed_staging', 'stable_staging_order',
])
def test_lexical_admission_caps_are_primary_only(data, monkeypatch, limit):
    data.install(1, four_units())
    data.job(1, 1, 3, labels=('S1',))
    statements = []
    assert ts.MAX_ADMITTED_LEXICAL_UNITS == 512
    assert ts.MAX_LEXICAL_GROUPS == ts.MAX_PROJECTED_ATOMS == 128
    if limit in ('units', 'groups', 'atoms'):
        name = {'units': 'MAX_ADMITTED_LEXICAL_UNITS', 'groups': 'MAX_LEXICAL_GROUPS',
                'atoms': 'MAX_PROJECTED_ATOMS'}[limit]
        if limit == 'atoms':
            data.segment(2, 5, 9, 'opaque parent also consumes one atom')
            maximum = 4
        else:
            data.install(2, [unit(char, index + 5, index + 6)
                             for index, char in enumerate('前中文後')])
            maximum = 7
        data.job(2, 6, 8, labels=('S1',))
        monkeypatch.setattr(ts, name, maximum)
    elif limit == 'primary_bytes':
        row = data.row(1)
        primary_bytes = sum(len((row[key] or '').encode('utf-8')) for key in
                            ('text', 'raw_text', 'asr_backend', 'timestamp_kind', 'speaker_local_id'))
        monkeypatch.setattr(c, 'MAX_PRIMARY_BYTES', primary_bytes + len(row['lexical_evidence']) - 1)
    elif limit == 'cumulative_bytes':
        data.install(2, [unit('前', 5, 6), unit('中', 6, 8), unit('後', 8, 9)])
        data.job(2, 6, 8, labels=('S1',))
        rows = [data.row(ident) for ident in (1, 2)]
        primary_bytes = sum(len((row[key] or '').encode('utf-8')) for row in rows
                            for key in ('text', 'raw_text', 'asr_backend',
                                        'timestamp_kind', 'speaker_local_id'))
        monkeypatch.setattr(c, 'MAX_PRIMARY_BYTES', primary_bytes +
                            sum(len(row['lexical_evidence']) for row in rows) - 1)
    elif limit == 'inventory_bytes':
        data.replace_inventory(1, 'x' * (le.MAX_SEGMENT_BYTES + 1))
    elif limit == 'bindings':
        monkeypatch.setattr(projection, 'MAX_BINDINGS', 1)
    elif limit == 'response':
        with monkeypatch.context() as patch:
            patch.setattr(ts, 'MAX_ADMITTED_LEXICAL_UNITS', 3)
            minimal = data.lexical_page()
        full = data.lexical_page()
        ceiling = len(c.encoded(minimal)) + 128
        assert ceiling < len(c.encoded(full))
        monkeypatch.setattr(c, 'MAX_RESPONSE_BYTES', ceiling)
    elif limit == 'malformed_staging':
        # Many entries can materialize within the 64-KiB byte staging bound.
        raw = c.encoded({'units': [{}] * 21800}).decode('ascii')
        assert le.MAX_SEGMENT_BYTES - 256 < len(raw) <= le.MAX_SEGMENT_BYTES
        data.replace_inventory(1, raw)
        value = data.lexical_page()
        assert value['lexical_projection']['complete'] is True
        assert value['reading'] == value['baseline_reading']
        assert selected(value) == []
        return
    else:
        data.install(10, [unit('十', 5, 6), unit('後', 6, 7)])
        data.install(2, [unit('二', 8, 9), unit('後', 9, 10)])
        data.job(2, 5.5, 6.5, labels=('S1',))
        data.job(3, 8.5, 9.5, labels=('S1',))
        original, events, active = le.current_inventory, [], []
        def staged(db, sid, row, **kwargs):
            assert active == []
            active.append(row['id'])
            events.append(row['id'])
            try:
                return original(db, sid, row, **kwargs)
            finally:
                active.pop()
        monkeypatch.setattr(le, 'current_inventory', staged)
        value = data.lexical_page()
        assert events == [1, 2, 10]
        assert value['lexical_projection']['complete'] is True
        return
    if limit in ('primary_bytes', 'cumulative_bytes', 'inventory_bytes'):
        connect = sqlite3.connect
        def traced(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db
        monkeypatch.setattr(ts.sqlite3, 'connect', traced)
    value = data.lexical_page()
    assert_primary_only(value)
    assert value['policy_hash'] == c.LEXICAL_POLICY_HASH
    assert value['baseline_reading'][0]['source_ref'] == data.parent(1)['source_ref']
    # No whole-primary reselection may run after any optional admission failure.
    assert value['optional_closure']['complete'] is True
    if statements:
        lexical_reads = [sql for sql in statements if sql.startswith('SELECT')
                         and 'lexical_evidence' in sql]
        headers = [sql for sql in lexical_reads if 'length(' in sql]
        bodies = [sql for sql in lexical_reads if 'length(' not in sql]
        assert headers
        assert len(bodies) == (1 if limit == 'cumulative_bytes' else 0)
        if bodies:
            assert lexical_reads.index(headers[0]) < lexical_reads.index(bodies[0])


@pytest.mark.parametrize('case', ['new', 'already_charged', 'descriptor_alias', 'overflow', 'database_error'])
def test_source_cache_charges_before_select(data, monkeypatch, case):
    events, selects = [], []
    charged = {'chunk'} if case in ('already_charged', 'descriptor_alias') else set()
    cid = 'chunk'
    if case == 'descriptor_alias':
        data.install(1, four_units())
        data.chunk('descriptor-alias', 100, 110)
        valid = data.job(1, 1, 3, labels=('S1',))
        job = data.job(2, .5, 2.5, labels=('S1',))
        with data.db() as db:
            db.execute(f'UPDATE {ts.AUTO} SET cid2=? WHERE candidate_key=?',
                       ('descriptor-alias', job['key']))
        close, source_lookup = data.reader._closure, data.reader._lexical_source_lookup
        captured, calls = {}, []
        def capture_closure(*args, **kwargs):
            closed = close(*args, **kwargs)
            captured['charged'] = closed[4]
            return closed
        def shared_lookup(db, check_snapshot, charged_chunks):
            assert charged_chunks is captured['charged']
            calls.append(charged_chunks)
            return source_lookup(db, check_snapshot, charged_chunks)
        with monkeypatch.context() as patch:
            patch.setattr(data.reader, '_closure', capture_closure)
            patch.setattr(data.reader, '_lexical_source_lookup', shared_lookup)
            page = data.lexical_page()
        assert selected(page) == [valid['key']]
        assert len(calls) == 1
        charged = captured['charged']
        assert {'chunk', 'descriptor-alias'} <= charged
        cid = 'descriptor-alias'
    if case == 'overflow':
        charged = {f'charged-{index}' for index in range(ts.MAX_CHUNKS)}
    initial = set(charged)
    def check():
        events.append('check')
    with data.db() as db:
        class Observed:
            def execute(self, sql, params=()):
                assert params[0] in charged, 'new contributor must be charged before SELECT'
                assert events and events[-1] == 'check'
                assert 'id,session_id,digest,timeline_start,duration' in sql.replace(' ', '').replace('\n', '')
                selects.append((sql, params))
                events.append('select')
                if case == 'database_error':
                    raise sqlite3.OperationalError('synthetic lexical source error')
                return db.execute(sql, params)
        lookup = data.reader._lexical_source_lookup(Observed(), check, charged)
        if case == 'overflow':
            with pytest.raises(ts._ClosureCapacity):
                lookup(cid)
            assert selects == [] and charged == initial
        elif case == 'database_error':
            with pytest.raises(sqlite3.OperationalError, match='synthetic lexical source error'):
                lookup(cid)
            assert len(selects) == 1
        else:
            first, second = lookup(cid), lookup(cid)
            assert dict(first) == dict(second)
            assert set(first.keys()) == set(le.SOURCE_FIELDS)
            assert len(selects) == 1
            assert charged == initial | {cid}
            assert events[0] == events[-1] == 'check'


@pytest.mark.parametrize('failure', ['database', 'deadline', 'vm', 'missing_dependency'])
def test_hard_read_failures_propagate(data, monkeypatch, failure):
    data.install(1, four_units())
    data.job(1, 1, 3, labels=('S1',))
    original = data.reader._lexical_inputs
    clock = [0.]
    data.reader.clock = lambda: clock[0]
    class MissingRow:
        def fetchone(self):
            return None
    class MissingBody:
        def __init__(self, wrapped):
            self.wrapped = wrapped
        def execute(self, sql, *args):
            if ('lexical_evidence' in sql and 'length(' not in sql.lower()
                    and 'select' in sql.lower()):
                return MissingRow()
            return self.wrapped.execute(sql, *args)
        def __getattr__(self, name):
            return getattr(self.wrapped, name)
    def broken(db, sid, check, *args, **kwargs):
        if failure == 'database':
            raise sqlite3.OperationalError('synthetic lexical database failure')
        if failure == 'deadline':
            clock[0] = 1.
            check()
        if failure == 'vm':
            monkeypatch.setattr(ts, 'MAX_VM_STEPS', 100)
            db.execute('''WITH RECURSIVE numbers(n) AS
                (SELECT 1 UNION ALL SELECT n+1 FROM numbers WHERE n<1000)
                SELECT sum(n) FROM numbers''').fetchone()
        return original(MissingBody(db) if failure == 'missing_dependency' else db,
                        sid, check, *args, **kwargs)
    monkeypatch.setattr(data.reader, '_lexical_inputs', broken)
    with pytest.raises(ReadError) as exc:
        data.lexical_page()
    assert exc.value.status_code == 503
    assert exc.value.reason == {
        'database': 'transcript_storage_unavailable', 'deadline': 'unresolved_capacity',
        'vm': 'unresolved_capacity', 'missing_dependency': 'transcript_dependency_missing',
    }[failure]


@pytest.mark.parametrize('state', ['complete', 'lexical_fallback', 'closure_fallback', 'dense_notices'])
def test_response_capacity_with_optional_speaker_annotations(data, monkeypatch, state):
    data.install(1, four_units())
    data.job(1, 1, 3, labels=('S1',))
    if state == 'lexical_fallback':
        monkeypatch.setattr(ts, 'MAX_ADMITTED_LEXICAL_UNITS', 3)
    elif state == 'closure_fallback':
        monkeypatch.setattr(c, 'MAX_RESULTS', 0)
    value = data.lexical_page()
    if state == 'dense_notices':
        value['notices'] = [dict(status='unsupported_unplaced',
                                 candidate_key=f'{index:064x}', reason='x' * 256)
                            for index in range(1024)]
        minimal = c.lexical_fallback(deepcopy(value), 'response_bytes')
        value['recording_speaker_links'] = {'padding': 'y' * 16000}
        monkeypatch.setattr(c, 'MAX_RESPONSE_BYTES', len(c.encoded(minimal)))
        actual = json.loads(c.transcript_response(value))
        assert actual == minimal
        assert_primary_only(actual)
        assert actual['notices'] == [dict(status='lexical_overlay_incomplete',
                                          reason='response_bytes',
                                          observed_notice_count=1024,
                                          evidence_complete=False)]
        assert len(c.encoded(actual['notices'])) < 256
        assert actual['reading_status'] == 'primary_only_optional_lexical_unresolved'
        assert 'recording_speaker_links' not in actual
        return
    original = deepcopy(value)
    value['recording_speaker_links'] = {'padding': 'x' * 16000}
    for item in value['reading']:
        item['recording_speaker_link'] = {'padding': 'y' * 16000}
    monkeypatch.setattr(c, 'MAX_RESPONSE_BYTES', len(c.encoded(original)) + 128)
    actual = json.loads(c.transcript_response(value))
    assert 'recording_speaker_links' not in actual
    assert all('recording_speaker_link' not in item for item in actual['reading'])
    assert actual['reading'] == original['reading']
    assert actual['baseline_reading'] == original['baseline_reading']
    if state == 'lexical_fallback':
        assert_primary_only(actual)
        assert actual['reading_status'] == original['reading_status']
    elif state == 'closure_fallback':
        assert actual['optional_closure']['complete'] is False
        assert actual['reading_status'] == 'primary_only_optional_closure_unresolved'
    else:
        assert actual['lexical_projection']['complete'] is True
        assert actual['selected_runs'] == original['selected_runs']


def test_estimated_containment_does_not_certify_acoustics(data):
    inventory = data.install(1, four_units())
    job = data.job(1, 1, 3, labels=('S1', 'S2'), text='重复重复。')
    value = data.lexical_page()
    assert selected(value) == [job['key']]
    assert value['complete_content'] is False and value['eos'] is None
    assert value['acoustic_coverage'] == 'unproven'
    moss = [item for item in value['reading'] if item['source'] == 'moss']
    assert [item['raw_text'] for item in moss] == ['重复重复。', '重复重复。']
    assert [item['speaker_scope'] for item in moss] == [
        [job['jid'], job['sha'], label] for label in ('S1', 'S2')]
    assert [item['start'] for item in moss] == [0., 0.]
    assert [item['end'] for item in moss] == [1., 1.]
    for item in value['reading']:
        assert item['word_times'] is None
    evidence = data.lexical_evidence(1, 1, 3)
    assert evidence['acoustic_coverage'] == 'unproven'
    assert evidence['complete_content'] is False and evidence['word_times'] is None
    assert evidence['timestamp_semantics'] == le.TIME_NOTE
    assert evidence['source_provenance'] == le.SOURCE_NOTE
    assert evidence['source_parts'] == inventory['source_parts']
    assert evidence['units'] == inventory['units'][1:3]
    assert evidence['selection_status'] == evidence['selection_eligibility'] == 'not_asserted'
    assert 'private-person' not in json.dumps(value) + json.dumps(evidence)
    assert_conserved(data, 1, value)
