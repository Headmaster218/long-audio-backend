"""Model-free event coverage contract; historical raw scores remain unchanged."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    'event_coverage_metrics', Path(__file__).parents[1] / 'scripts/benchmarks/score_predictions.py')
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def event(label, start=0., end=1.):
    return {'event': label, 'start': start, 'end': end}


def coverage(label='rain', intervals=None):
    return {'schema_version': 1, 'exhaustive_intervals': {
        label: intervals if intervals is not None else [{'start': 0., 'end': 10.}]}}


def test_disjoint_vocal_taxonomy_is_unavailable_not_zero_accuracy():
    ref = [event('laugh'), event('cough', 2., 3.)]
    hyp = [event('music'), event('door', 2., 3.)]
    got = metrics.environmental_event_score(ref, hyp)
    assert got['available'] is False
    assert got['reason'] == 'no_target_reference_coverage'
    assert got['covered_targets'] == []
    assert got['unsupported_targets'] == list(metrics.ENVIRONMENTAL_EVENT_TARGETS)
    assert got['precision'] is got['recall'] is got['f1'] is None
    assert got['per_target'] == {}
    assert got['out_of_taxonomy_reference_events'] == 2
    assert got['unsupported_target_hypotheses'] == {'door': 1, 'music': 1}
    assert metrics.event_score(ref, hyp) == {
        'precision': 0., 'recall': 0., 'f1': 0, 'matched': 0,
        'reference_events': 2, 'hypothesis_events': 2, 'iou_threshold': .3,
        'matching': 'greedy descending IoU; same source event type'}


def test_score_preserves_legacy_events_and_adds_separate_coverage_result():
    gold = {'segments': [], 'events': [event('laugh'), event('cough', 2., 2.)]}
    prediction = {'events': [event('music')]}
    got = metrics.score(gold, prediction)
    assert got['events'] == metrics.event_score([event('laugh')], prediction['events'])
    assert got['environmental_events']['reason'] == 'no_target_reference_coverage'
    assert 'events' not in metrics.score({'segments': []}, prediction)
    assert metrics.score({'segments': []}, prediction)['environmental_events']['available'] is False


def test_partial_targets_positive_recall_does_not_create_negative_truth():
    ref = [event('rain'), event('laugh', 3., 4.)]
    hyp = [event('rain'), event('rain', 5., 6.), event('door')]
    got = metrics.environmental_event_score(ref, hyp)
    assert got['available'] and not got['all_targets_covered']
    assert got['covered_targets'] == ['rain']
    assert got['unsupported_targets'] == ['driving', 'subway', 'door', 'music', 'silence']
    rain = got['per_target']['rain']
    assert rain['recall'] == 1.
    assert rain['precision'] is rain['f1'] is rain['false_positive_events'] is None
    assert rain['hypotheses_outside_exhaustive_coverage'] == 2
    assert got['precision'] is got['recall'] is got['f1'] is None


@pytest.mark.parametrize('label', ['Train', 'train', 'Rail transport', 'Creak', 'Humming', 'Rain', 'other'])
def test_no_inferred_label_or_source_context_mapping(label):
    got = metrics.environmental_event_score([event(label)], [event('driving'), event('subway'), event('door'), event('music'), event('rain')])
    assert not got['available']
    assert got['covered_targets'] == []


def test_exact_target_label_matches_and_false_negatives_are_real():
    got = metrics.environmental_event_score([event('subway')], [event('subway')])
    assert got['per_target']['subway']['recall'] == 1.
    assert got['per_target']['subway']['precision'] is None
    got = metrics.environmental_event_score([event('rain')], [])
    assert got['per_target']['rain']['recall'] == 0.
    assert got['per_target']['rain']['precision'] is None


def test_empty_references_are_unknown_without_explicit_coverage():
    for hyp in ([], [event('rain')]):
        got = metrics.environmental_event_score([], hyp)
        assert not got['available']
        assert got['reason'] == 'no_target_reference_coverage'
        assert got['precision'] is got['recall'] is None
    assert not metrics.environmental_event_score([], [], coverage(intervals=[]))['available']


def test_explicit_exhaustive_negative_support_allows_false_positive_count():
    got = metrics.environmental_event_score([], [event('rain')], coverage())
    rain = got['per_target']['rain']
    assert got['covered_targets'] == ['rain']
    assert rain['false_positive_events'] == 1 and rain['precision'] == 0.
    assert rain['recall'] is rain['f1'] is None
    empty = metrics.environmental_event_score([], [], coverage())['per_target']['rain']
    assert empty['false_positive_events'] == 0
    assert empty['precision'] is empty['recall'] is empty['f1'] is None


def test_coverage_boundaries_exclude_unknown_support_without_clipping():
    support = coverage(intervals=[{'start': 1., 'end': 3.}])
    predictions = [event('rain', 1., 3.), event('rain', .99, 2.), event('rain', 2., 3.01), event('rain', 4., 5.)]
    got = metrics.environmental_event_score([event('rain', 1., 3.)], predictions, support)['per_target']['rain']
    assert got['precision_scored_hypotheses'] == 1
    assert got['hypotheses_outside_exhaustive_coverage'] == 3
    assert got['precision'] == 1. and got['recall'] == 1.
    assert got['false_positive_events'] == 0
    assert got['f1'] is None and not got['f1_support_aligned']
    assert predictions[1]['start'] == .99 and predictions[2]['end'] == 3.01


def test_touching_coverage_can_merge_but_real_gap_cannot():
    contiguous = coverage(intervals=[{'start': 2., 'end': 3.}, {'start': 1., 'end': 2.}])
    got = metrics.environmental_event_score([], [event('rain', 1., 3.)], contiguous)['per_target']['rain']
    assert got['precision_scored_hypotheses'] == 1
    gap = coverage(intervals=[{'start': 1., 'end': 2.}, {'start': 2.01, 'end': 3.}])
    got = metrics.environmental_event_score([], [event('rain', 1., 3.)], gap)['per_target']['rain']
    assert got['precision_scored_hypotheses'] == 0
    assert got['false_positive_events'] == 0 and got['precision'] is None


def test_iou_exact_boundary_and_zero_duration_reference():
    # Exactly .3 is accepted; a nonoverlapping touch is not.
    gold = [event('rain', 0., 10.), event('rain', 20., 20.)]
    yes = metrics.environmental_event_score(gold, [event('rain', 0., 3.)], coverage())
    assert yes['per_target']['rain']['matched_reference_events'] == 1
    assert yes['per_target']['rain']['f1'] == 1.
    no = metrics.environmental_event_score(gold, [event('rain', 0., 2.999999)])
    assert no['per_target']['rain']['recall'] == 0.
    touch = metrics.environmental_event_score([event('rain', 0., 1.)], [event('rain', 1., 2.)])
    assert touch['per_target']['rain']['recall'] == 0.
    assert not metrics.environmental_event_score([event('rain', 0., 0.)], [])['available']


def test_precision_matching_does_not_consume_predictions_outside_coverage():
    ref = [event('rain', 1., 3.)]
    hyp = [event('rain', 1., 3.), event('rain', .5, 3.)]
    got = metrics.environmental_event_score(ref, hyp, coverage(intervals=[{'start': 1., 'end': 3.}]))['per_target']['rain']
    assert got['precision_matched_hypotheses'] == 1
    assert got['precision_scored_hypotheses'] == 1
    assert got['recall'] == got['precision'] == 1.


@pytest.mark.parametrize('bad', [
    {}, {'schema_version': 2, 'exhaustive_intervals': {}},
    {'schema_version': True, 'exhaustive_intervals': {}},
    {'schema_version': 1.0, 'exhaustive_intervals': {}},
    coverage('Train'), coverage(intervals=[{'start': 0., 'end': float('inf')}]),
    coverage(intervals=[{'start': -1., 'end': 1.}]),
    coverage(intervals=[{'start': 1., 'end': 1.}]),
    coverage(intervals=[{'start': False, 'end': 1.}]),
    coverage(intervals='all'),
])
def test_malformed_or_implicit_coverage_is_rejected(bad):
    with pytest.raises(ValueError):
        metrics.environmental_event_score([], [], bad)


def test_exhaustive_coverage_is_label_specific():
    got = metrics.environmental_event_score([event('rain'), event('door')], [event('rain'), event('door')], coverage('rain'))
    assert got['per_target']['rain']['precision'] == 1.
    assert got['per_target']['door']['precision'] is None
    assert got['per_target']['door']['false_positive_events'] is None


@pytest.mark.parametrize('iou', [0., 1.01, float('nan'), True])
def test_invalid_iou_is_rejected_only_by_new_coverage_scorer(iou):
    with pytest.raises(ValueError):
        metrics.environmental_event_score([], [], iou=iou)


def test_new_result_does_not_mutate_input_annotations():
    import copy
    ref = [event('rain', 1., 3.)]
    hyp = [event('rain', .5, 3.), event('Train', 4., 5.)]
    declared = coverage(intervals=[{'start': 2., 'end': 3.}, {'start': 1., 'end': 2.}])
    before = copy.deepcopy((ref, hyp, declared))
    metrics.environmental_event_score(ref, hyp, declared)
    assert (ref, hyp, declared) == before
