"""Synthetic audit ledgers only: no real database, models, audio or inference."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.benchmarks.audit_moss_endurance_candidates import (
    audit, classify_reason, intersect, main, reference_overlap, seconds, sha256, union,
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return sha256(path)


@pytest.fixture
def fixture(tmp_path):
    run = tmp_path / 'run'
    sources = []
    mapped = []
    hashes = {}
    for dataset, recording, language, a, segments in [
        ('AliMeeting-Eval', 'ali-one', 'zh', 0., [
            {'start': 1., 'end': 9., 'speaker': 'same-name'},
            {'start': 3., 'end': 12., 'speaker': 'second'},
            {'start': 4., 'end': 6., 'speaker': 'same-name'},
            {'start': 0., 'end': 20., 'speaker': None},
        ]),
        ('AMI', 'ami-one', 'en', 20., [
            {'start': 0., 'end': 15., 'speaker': 'same-name'},
            {'start': 5., 'end': 10., 'speaker': 'second'},
        ]),
    ]:
        gold = f'gold/{recording}.json'
        digest = write_json(run / 'snapshot' / gold, {
            'dataset': dataset, 'recording_id': recording, 'duration_seconds': 20.,
            'segments': segments,
        })
        hashes[gold] = digest
        source = {'dataset': dataset, 'recording_id': recording, 'language': language,
                  'duration_seconds': 20., 'gold': gold, 'gold_sha256': digest,
                  'audio_sha256': hashlib.sha256(recording.encode()).hexdigest(),
                  'identity_group': 'local-' + recording}
        sources.append(source)
        mapped.append({**source, 'source_start_sample': 0,
                       'concatenated_start_seconds': a, 'concatenated_end_seconds': a + 20.})
    write_json(run / 'run.json', {
        'run_id': 'synthetic-run', 'source_records': sources, 'source_sha256': hashes,
        'concatenation': {'sources': mapped, 'duration_seconds': 40., 'audio_sha256': 'input-audio-hash'},
    })
    database = tmp_path / 'fixture.sqlite3'
    with sqlite3.connect(database) as db:
        db.executescript('''
            CREATE TABLE chunks (id TEXT, digest TEXT, timeline_start REAL, duration REAL,
                                 next_offset REAL, status TEXT, error TEXT);
            INSERT INTO chunks VALUES('chunk','input-audio-hash',0,40,40,'completed',NULL);
            CREATE TABLE overlap_regions (
                id INTEGER,chunk_id TEXT,window_start REAL,shared_support_id TEXT,
                support_start REAL,support_end REAL,start REAL,end REAL,region_kind TEXT,
                status TEXT,reason_code TEXT,support_activity_json TEXT,model_label_count INTEGER);
            CREATE TABLE overlap_tracks (
                id INTEGER,chunk_id TEXT,window_start REAL,region_id INTEGER,source_track_id TEXT,
                scope_id TEXT,support_start REAL,support_end REAL,start REAL,end REAL,text TEXT,
                identity_status TEXT,quality_status TEXT,reason_code TEXT,provenance_json TEXT);
            CREATE TABLE overlap_track_regions(track_id INTEGER,region_id INTEGER);
        ''')
    return run, database


def region(path, number, a, b, *, sid=None, interval=None, reason='support_not_model_timing',
           window=0., native=2, model=1, kind='detected_overlap'):
    sid = sid or f'chunk@{window:.6f}/support-{number}'
    interval = interval or (a, b)
    status = 'unverified_hypothesis' if reason == 'support_not_model_timing' else 'unresolved'
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO overlap_regions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            number, 'chunk', window, sid, a, b, *interval, kind, status, reason,
            json.dumps({'distinct_local_speakers': native, 'max_simultaneous_speakers': native}), model))
    return sid


def phrase(path, number, region_id, a, b, *, sid=None, native_interval=None, label='SPEAKER_0',
           text='candidate phrase', source_id=None, window=0., native=None):
    with sqlite3.connect(path) as db:
        sid = sid or db.execute('SELECT shared_support_id FROM overlap_regions WHERE id=?', (region_id,)).fetchone()[0]
        native_interval = native_interval or (a, min(b, a + 1.))
        p = {'association_basis': 'support_not_model_timing', 'shared_support_id': sid,
             'native_time_axis': 'session_absolute', 'native_unit_kind': 'native_phrase',
             'model_local_speaker_label': label,
             'native_phrase_intervals': native if native is not None else [{
                 'start': native_interval[0], 'end': native_interval[1], 'text': text,
                 'unit_kind': 'native_phrase', 'boundary_uncertain': True,
             }]}
        db.execute('INSERT INTO overlap_tracks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            number, 'chunk', window, region_id, source_id or f'{sid}-phrase-{number}',
            'support-local', a, b, *native_interval, text, 'unresolved', 'unverified',
            'support_associated_hypothesis', json.dumps(p)))
        db.execute('INSERT INTO overlap_track_regions VALUES(?,?)', (number, region_id))


def run_audit(fixture, **kwargs):
    return audit(*fixture, consistent_snapshot=True, **kwargs)


def test_interval_union_intersection_and_distinct_speaker_sweep():
    assert union([(0, 4), (3, 5), (5, 6), (12, 12)]) == [(0, 6)]
    assert intersect([(0, 10), (15, 20)], [(2, 5), (7, 18)]) == [(2, 5), (7, 10), (15, 18)]
    overlap, stats = reference_overlap([
        {'start': 0, 'end': 8, 'speaker': 'a'},
        {'start': 1, 'end': 6, 'speaker': 'a'},
        {'start': 3, 'end': 5, 'speaker': 'b'},
        {'start': 5, 'end': 9, 'speaker': 'c'},
        {'start': 0, 'end': 10, 'speaker': 'unknown'},
    ], 10)
    assert overlap == [(3, 8)]
    assert seconds(overlap) == 5
    assert stats['peak_distinct_annotation_speakers'] == 2
    assert stats['unknown_speaker_segments'] == 1


def test_shared_support_counted_once_links_are_not_duplicate_phrases(fixture):
    run, db = fixture
    sid = region(db, 1, 0., 10., interval=(2., 3.))
    region(db, 2, 0., 10., sid=sid, interval=(7., 8.))
    # Phrase lies outside either linked region, yet legitimately belongs to input.
    phrase(db, 1, 1, 0., 10., native_interval=(0., 1.))
    with sqlite3.connect(db) as connection:
        connection.execute('INSERT INTO overlap_track_regions VALUES(1,2)')
    report = run_audit(fixture, expected_database_sha256=sha256(db), batch_size=1)
    assert report['overall']['counts'] == {
        'overlap_regions': 2, 'overlap_tracks': 1, 'overlap_track_regions': 2}
    assert report['overall']['supports'] == 1
    assert report['overall']['distinct_stored_support_ids'] == 1
    assert report['overall']['input_union_seconds']['valid_format'] == 10
    assert report['duplicates']['duplicate_exact_same_scope_native_phrase_keys']['groups'] == 0
    assert report['diagnostics'] == []
    exposure = report['per_original_source'][0]['reference_overlap_exposure']
    assert exposure['reference_overlap_whole_original_seconds'] == 6
    assert exposure['reference_overlap_in_observed_support_input']['valid_format_source_contained_seconds'] == 6
    assert exposure['final_reference_overlap_input_fraction']['valid_format'] == 1
    assert report['overall']['null_source_identity_rows'] == 1
    assert report['database']['unchanged_during_audit']
    assert report['database']['expected_hash_verified']
    assert report['evaluator']['sha256'] == sha256(Path(__file__).parents[1] / 'scripts/benchmarks/audit_moss_endurance_candidates.py')
    assert json.loads(json.dumps(report, allow_nan=False)) == report


def test_failure_categories_planning_budget_and_generation_budget_are_separate(fixture):
    _, db = fixture
    for n, reason in enumerate(['candidate_timeout', 'analysis_budget_exhausted',
                                'native_support_capacity_exceeded', 'native_generation_budget_exhausted',
                                'candidate_dependency_failure', 'candidate_silent_input',
                                'new_unrecognized_reason', 'not_yet_attempted'], 1):
        region(db, n, (n - 1) * 5., n * 5., reason=reason, model=None)
    report = run_audit(fixture)
    overall = report['overall']
    assert overall['planned_support_count'] == 5
    assert overall['not_planned_support_count'] == 3
    assert overall['unresolved_support_count'] == 8
    assert overall['valid_format_support_count'] == 0
    assert all(row['emitted_distinct_labels'] is None for row in overall['model_vs_native_label_counts'])
    reasons = {r['reason']: r for r in overall['region_reasons']}
    assert len(reasons) == 8
    assert reasons['analysis_budget_exhausted']['category'] == 'not_planned_analysis_budget'
    assert reasons['native_generation_budget_exhausted']['category'] == 'unresolved_generation_budget'
    assert reasons['new_unrecognized_reason']['category'] == 'unresolved_other'
    assert any('does not prove the model ran' in s for s in report['semantics'])
    assert classify_reason('candidate_import_failure') == 'unresolved_dependency'


def test_crossing_supports_are_separate_but_input_exposure_intersects_each_original(fixture):
    _, db = fixture
    region(db, 1, 18., 28.)
    phrase(db, 1, 1, 18., 28., native_interval=(21., 22.))
    report = run_audit(fixture)
    assert report['artificial_source_boundary_crossing']['supports'] == 1
    assert report['artificial_source_boundary_crossing']['input_union_seconds']['valid_format'] == 10
    assert report['per_language_source_contained']['en']['counts']['overlap_tracks'] == 0
    assert report['per_language_source_contained']['zh']['counts']['overlap_tracks'] == 0
    ami = report['per_original_source'][1]['reference_overlap_exposure']
    assert ami['reference_overlap_in_observed_support_input']['valid_format_including_boundary_crossing_seconds'] == 3
    assert ami['reference_overlap_in_observed_support_input']['valid_format_boundary_crossing_only_seconds'] == 3
    assert ami['reference_overlap_in_observed_support_input']['valid_format_source_contained_seconds'] == 0
    assert report['within_32_seconds_of_artificial_boundary']['supports'] == 1
    assert report['per_language_reference_overlap_exposure']['en']['final_reference_overlap_input_fraction']['valid_format'] == .6
    assert report['reference_overlap_exposure']['reference_overlap_whole_original_seconds'] == 11


def test_partial_source_has_no_final_coverage_fraction_or_truncated_gold(fixture):
    _, db = fixture
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE chunks SET next_offset=23,status='running'")
    region(db, 1, 0., 10.)
    phrase(db, 1, 1, 0., 10.)
    report = run_audit(fixture)
    assert report['completion']['status'] == 'partial'
    assert report['completion']['complete_original_recordings'] == 1
    first, second = [s['reference_overlap_exposure'] for s in report['per_original_source']]
    assert first['complete_original_source']
    assert not second['complete_original_source']
    assert second['observed_original_seconds'] == 3
    assert second['final_reference_overlap_input_fraction'] is None
    assert second['reference_overlap_whole_original_seconds'] == 5
    assert 'partial' in second['assertion_status']
    assert 'segments' not in second['gold']
    assert report['reference_overlap_exposure']['final_reference_overlap_input_fraction'] is None
    assert report['reference_overlap_exposure']['complete_originals_only']['reference_overlap_seconds'] == 6


def test_duplicates_and_model_repetition_are_not_conflated(fixture):
    _, db = fixture
    region(db, 1, 0., 10.)
    phrase(db, 1, 1, 0., 10., text='same', source_id='duplicated')
    phrase(db, 2, 1, 0., 10., text='same', source_id='duplicated')
    phrase(db, 3, 1, 0., 10., text='same', native_interval=(2., 3.))
    report = run_audit(fixture)
    dup = report['duplicates']
    assert dup['duplicate_source_track_ids']['groups'] == 1
    assert dup['duplicate_exact_same_scope_native_phrase_keys']['groups'] == 1
    assert dup['duplicate_exact_same_scope_native_phrase_keys']['extra_rows_beyond_first'] == 1
    assert dup['repeated_same_scope_label_text_at_distinct_intervals']['groups'] == 1
    assert 'text' not in dup['duplicate_exact_same_scope_native_phrase_keys']['examples_first_20'][0]
    assert report['overall']['counts']['overlap_tracks'] == 3  # No repair/dedup of ledger.


def test_local_labels_and_annotations_in_different_groups_never_create_global_identity(fixture):
    _, db = fixture
    region(db, 1, 0., 10., sid='same-local-id', window=0.)
    region(db, 2, 20., 30., sid='same-local-id', window=20.)
    phrase(db, 1, 1, 0., 10., label='same-name', text='same')
    phrase(db, 2, 2, 20., 30., label='same-name', text='same', window=20.)
    report = run_audit(fixture)
    assert report['overall']['supports'] == 2
    assert report['overall']['null_source_identity_rows'] == 2
    assert report['overall']['model_vs_native_label_counts'][0]['emitted_distinct_labels'] == 1
    assert report['duplicates']['stored_support_id_reused_across_scopes']['groups'] == 1
    assert report['duplicates']['duplicate_exact_same_scope_native_phrase_keys']['groups'] == 0
    golds = [s['reference_overlap_exposure']['gold'] for s in report['per_original_source']]
    assert golds[0]['identity_scope'] != golds[1]['identity_scope']
    assert golds[0]['annotation_granularity'] != golds[1]['annotation_granularity']
    assert all('speaker_mapping' not in s for s in report['per_original_source'])


def test_bad_native_format_cannot_be_success(fixture):
    _, db = fixture
    region(db, 1, 0., 10.)
    phrase(db, 1, 1, 0., 10., native=[])
    report = run_audit(fixture)
    assert report['overall']['valid_format_support_count'] == 0
    kinds = {row['kind'] for row in report['diagnostics']}
    assert 'success_status_without_consistent_valid_phrases' in kinds
    assert 'missing_or_invalid_native_phrase_evidence' in kinds


def test_valid_and_unresolved_support_unions_are_not_summed_or_deduplicated_by_links(fixture):
    _, db = fixture
    region(db, 1, 0., 10.)
    phrase(db, 1, 1, 0., 10.)
    region(db, 2, 5., 15., reason='candidate_timeout')
    report = run_audit(fixture)
    unions = report['overall']['input_union_seconds']
    assert unions['valid_format'] == 10
    assert unions['unresolved'] == 10
    assert unions['planned'] == 15
    assert unions['all_observed'] == 15


def test_native_times_outside_support_are_diagnostic_not_exposure_evidence(fixture):
    _, db = fixture
    region(db, 1, 0., 10.)
    phrase(db, 1, 1, 0., 10., native_interval=(11., 12.))
    report = run_audit(fixture)
    assert report['overall']['valid_format_support_count'] == 0
    assert any(row['kind'] == 'native_phrase_outside_support' for row in report['diagnostics'])


def test_null_and_nonnull_identity_are_reported_never_permuted(fixture):
    _, db = fixture
    region(db, 1, 0., 10.)
    phrase(db, 1, 1, 0., 10., label='same-name')
    with sqlite3.connect(db) as connection:
        connection.execute('ALTER TABLE overlap_tracks ADD COLUMN speaker_id TEXT')
        connection.execute("UPDATE overlap_tracks SET speaker_id='unverified-persistent-uuid'")
    report = run_audit(fixture)
    assert report['overall']['null_source_identity_rows'] == 0
    assert report['source_identity_storage']['speaker_id_column_present']
    assert any(row['kind'] == 'nonnull_candidate_source_identity' for row in report['diagnostics'])
    assert 'unverified-persistent-uuid' not in json.dumps(report)


def test_online_backup_is_read_only_and_consistent_including_wal(fixture):
    _, path = fixture
    with sqlite3.connect(path) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("UPDATE chunks SET status='running',next_offset=20")
        writer.commit()
        report = audit(*fixture)
        assert report['completion']['complete_original_recordings'] == 1
        assert report['database']['consistency_method'] == 'sqlite_online_backup_from_read_only_connection'
        assert report['database']['unchanged_during_audit']
        assert writer.execute('SELECT status FROM chunks').fetchone()[0] == 'running'
        with pytest.raises(ValueError, match='WAL'):
            run_audit(fixture)


def test_gold_and_database_hash_mismatches_fail_closed(fixture):
    run, db = fixture
    with pytest.raises(ValueError, match='database SHA-256 mismatch'):
        run_audit(fixture, expected_database_sha256='0' * 64)
    gold = run / 'snapshot/gold/ali-one.json'
    gold.write_text(gold.read_text() + '\n')
    with pytest.raises(ValueError, match='gold SHA-256 mismatch'):
        run_audit(fixture)


def test_snapshot_and_manifest_never_written_and_cli_refuses_overwrite(fixture, tmp_path):
    run, db = fixture
    files = {p: sha256(p) for p in run.rglob('*') if p.is_file()}
    db_hash = sha256(db)
    report_path = tmp_path / 'audit.json'
    main([str(run), str(db), '--consistent-snapshot', '--output', str(report_path)])
    assert json.loads(report_path.read_text())['completion']['status'] == 'final'
    assert sha256(db) == db_hash
    assert files == {p: sha256(p) for p in run.rglob('*') if p.is_file()}
    with pytest.raises(FileExistsError):
        main([str(run), str(db), '--consistent-snapshot', '--output', str(report_path)])


def test_wrong_database_or_incomplete_source_mapping_is_rejected(fixture):
    run, db = fixture
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE chunks SET digest='unrelated'")
    with pytest.raises(ValueError, match='matching frozen'):
        run_audit(fixture)
    config = json.loads((run / 'run.json').read_text())
    config['concatenation']['sources'][1]['source_start_sample'] = 1
    write_json(run / 'run.json', config)
    with pytest.raises(ValueError, match='complete original'):
        run_audit(fixture)
