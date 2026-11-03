"""Model-free experiment guards; invented words only."""
import importlib.util
from pathlib import Path

import pytest

SPEC=importlib.util.spec_from_file_location('handy_diagnostic',Path(__file__).resolve().parents[1]/'qualification/ami_handy_candidate_diagnostic_20261103.py')
module=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(module)


def invented_rows():
    return [{'text':'alpha' if i<29 else 'beta','speaker':'A' if i<29 else 'B',
             'start':i*.25+.01,'end':i*.25+.2,'index':i} for i in range(40)]


def test_frozen_window_low_share_rule(monkeypatch):
    monkeypatch.setattr(module,'CLIPS',[('test',0)])
    clips,refs=module.make_plan({'test':invented_rows()})
    assert clips[0]['frames']==252000
    assert clips[0]['reference_words']==40
    assert clips[0]['low_share_speakers']==['B']
    assert len(refs['test@0'])==40


def test_cut_reference_word_refuses(monkeypatch):
    monkeypatch.setattr(module,'CLIPS',[('test',0)])
    rows=invented_rows()+[{'text':'gamma','speaker':'A','start':15.5,'end':16.,'index':100}]
    with pytest.raises(ValueError,match='outer_reference_cut'):
        module.make_plan({'test':rows})


def test_missing_low_share_denominator_refuses(monkeypatch):
    monkeypatch.setattr(module,'CLIPS',[('test',0)])
    rows=[dict(r,speaker='A') for r in invented_rows()]
    with pytest.raises(ValueError,match='low_share_denominator'):
        module.make_plan({'test':rows})


def test_empty_output_counts_deletions_not_success():
    rows=invented_rows();result=module.score(rows,'',['B'])
    assert result['errors']==40 and result['wer']==1.
    assert result['low_share_reference_words']['correct']==0
    assert result['low_share_reference_words']['deletions']==11
