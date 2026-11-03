"""Synthetic activity-scoring controls; no actual speaker qualification."""
import importlib.util
from pathlib import Path

SPEC=importlib.util.spec_from_file_location('ami_native',Path(__file__).resolve().parents[1]/'qualification/ami_native_midpoint_diagnostic_20261103.py')
module=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(module)


def rows():
    return [{'speaker':'A','start':0.,'end':1.},{'speaker':'B','start':1.,'end':2.}]


def test_no_native_activity_remains_unmatched():
    report=module.midpoint_coverage(rows(),[],['B'])
    assert report['native_cluster_count']==0
    assert report['low_share_reference_words']['oracle_one_to_one_covered']==0
    assert report['low_share_reference_words']['any_activity']==0


def test_permuted_anonymous_labels_map_without_claiming_identity():
    turns=[{'start':0.,'end':1.,'speaker_local_id':'z'},{'start':1.,'end':2.,'speaker_local_id':'x'}]
    report=module.midpoint_coverage(rows(),turns,['B'])
    assert report['reference_speakers']['A']['mapped_cluster']=='z'
    assert report['reference_speakers']['B']['mapped_cluster']=='x'
    assert report['low_share_reference_words']['oracle_one_to_one_covered']==1


def test_continuous_single_cluster_is_not_two_correct_speakers():
    turns=[{'start':0.,'end':2.,'speaker_local_id':'x'}]
    report=module.midpoint_coverage(rows(),turns,['A','B'])
    pooled=report['low_share_reference_words']
    assert pooled['any_activity']==2
    assert pooled['oracle_one_to_one_covered']==1
    assert pooled['best_single_cluster_covered']==2


def test_midpoint_interval_is_half_open():
    turns=[{'start':0.,'end':.5,'speaker_local_id':'x'}]
    report=module.midpoint_coverage(rows(),turns,['A'])
    assert report['low_share_reference_words']['any_activity']==0


def test_multiple_active_clusters_are_reported_not_hidden():
    turns=[{'start':0.,'end':2.,'speaker_local_id':name} for name in ['x','y']]
    report=module.midpoint_coverage(rows(),turns,['A','B'])
    assert report['low_share_reference_words']['multiple_active_clusters']==2
    assert report['low_share_reference_words']['oracle_one_to_one_covered']==2
