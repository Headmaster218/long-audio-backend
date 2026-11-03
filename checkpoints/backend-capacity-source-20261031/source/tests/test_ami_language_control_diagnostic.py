"""Model-free study guards; all words invented."""
import importlib.util
from pathlib import Path
import pytest

SPEC=importlib.util.spec_from_file_location('language_diagnostic',Path(__file__).resolve().parents[1]/'qualification/ami_language_control_diagnostic_20261103.py')
module=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(module)

def rows():
    return [{'text':'alpha','speaker':'A','start':i*.3+.01,'end':i*.3+.25,'index':i} for i in range(30)]+[
        {'text':'beta','speaker':'B','start':i*.3+.02,'end':i*.3+.22,'index':30+i} for i in range(10)]

def test_plan_freezes_language_and_window(monkeypatch):
    monkeypatch.setattr(module,'CLIPS',[('test',0)])
    clips,refs=module.make_plan({'test':rows()})
    assert module.ARMS==('auto','en')
    assert clips[0]['frames']==336000 and clips[0]['reference_words']==40
    assert clips[0]['low_share_speakers']==['B']
    assert clips[0]['overlap_reference_words']==20
    assert len(refs['test@0'])==40

def test_cut_word_refused(monkeypatch):
    monkeypatch.setattr(module,'CLIPS',[('test',0)])
    with pytest.raises(ValueError,match='outer_reference_cut'):
        module.make_plan({'test':rows()+[{'text':'gamma','speaker':'A','start':20.9,'end':21.2,'index':50}]})

def test_missing_minority_refused(monkeypatch):
    monkeypatch.setattr(module,'CLIPS',[('test',0)])
    with pytest.raises(ValueError,match='low_share_denominator'):
        module.make_plan({'test':[dict(r,speaker='A') for r in rows()]})

def test_missing_overlap_refused(monkeypatch):
    monkeypatch.setattr(module,'CLIPS',[('test',0)])
    with pytest.raises(ValueError,match='overlap_denominator'):
        module.make_plan({'test':[dict(r,start=r['start']+10,end=r['end']+10) if r['speaker']=='B' else r for r in rows()]})

def test_empty_hypothesis_not_success():
    reference=[dict(r,overlap=True) for r in rows()]
    result=module.score(reference,'',['B'])
    assert result['errors']==40 and result['counts']['D']==40
    assert result['low_share_reference_words']['deletions']==10
    assert result['overlap_reference_words']['correct']==0
    assert result['nonoverlap_reference_words']['words']==0
