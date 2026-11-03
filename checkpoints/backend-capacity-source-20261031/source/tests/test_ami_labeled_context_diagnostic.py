"""Model-free scoring checks using invented words, not corpus references."""
import importlib.util
from pathlib import Path

import pytest

SPEC=importlib.util.spec_from_file_location('ami_diagnostic',Path(__file__).resolve().parents[1]/'qualification/ami_labeled_context_diagnostic_20261103.py')
module=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(module)


@pytest.mark.parametrize('reference,hypothesis,counts,labels',[
    ([],[],{'I':0},[]),
    (['alpha'],[],{'D':1,'I':0},['D']),
    ([],['beta'],{'I':1},[]),
    (['alpha'],['alpha'],{'C':1,'I':0},['C']),
    (['alpha'],['beta'],{'S':1,'I':0},['S']),
    (['alpha','beta'],['alpha','gamma','beta'],{'C':2,'I':1},['C','C']),
    (['alpha','alpha'],['alpha'],{'D':1,'C':1,'I':0},['D','C']),
])
def test_exact_scoring_operations(reference,hypothesis,counts,labels):
    assert module.align(reference,hypothesis)==(counts,labels)


def test_strata_do_not_assign_insertions_to_speakers():
    rows=[{'speaker':'A'},{'speaker':'B'},{'speaker':'B'}]
    value=module.stratum(rows,['C','D','S'],lambda r:r['speaker']=='B')
    assert value=={'words':2,'correct':0,'substitutions':1,'deletions':1,'correct_fraction':0.}
    assert 'insertions' not in value


def test_reference_parser_excludes_nonword_and_punctuation_preserves_fillers():
    raw=b'''<root><w starttime="1" endtime="2">Um</w>
    <w starttime="2" endtime="2" punc="true">.</w>
    <vocalsound starttime="2" endtime="3"/>
    <transformerror starttime="3" endtime="3"/>
    <w starttime="4" endtime="5">beta-gamma</w></root>'''
    rows,excluded=module.parse_words(raw,'A')
    assert [r['text'] for r in rows]==['um','beta','gamma']
    assert all(r['speaker']=='A' for r in rows)
    assert excluded=={'punctuation':1,'vocalsound':1,'transformerror':1}


def test_invalid_reference_times_refuse():
    with pytest.raises(ValueError,match='invalid_reference_interval'):
        module.parse_words(b'<root><w starttime="2" endtime="1">alpha</w></root>','A')


def test_normalization_fixed_number_and_fillers_policy():
    assert module.tokenize("Ｕｍ, IT’S 42 beta-gamma.")==['um',"it's",'42','beta','gamma']
    assert module.tokenize('forty two')!=module.tokenize('42')
