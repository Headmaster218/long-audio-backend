"""Model-free experiment guards; no acoustic quality qualification."""
import importlib.util
from pathlib import Path

import pytest

SPEC=importlib.util.spec_from_file_location('context_diagnostic',Path(__file__).resolve().parents[1]/'qualification/sensevoice_context_diagnostic_20261103.py')
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_empty_window_does_not_create_text_or_invent_time():
    assert module.own_window([],84000,89472,52000)==([], {})


def test_untokenized_nonempty_output_refuses_instead_of_shifting_time():
    with pytest.raises(ValueError,match='missing_lexical_ownership_evidence'):
        module.own_window([{'text':'control','start':0.,'end':1.}],84000,89472,52000)


def test_context_word_preserves_source_timing_with_core_relative_output():
    # Analyzer window begins at 3.25s; a unit at its 2.1s maps to source 5.35s.
    unit={'start':2.1,'end':2.3,'ownership_anchor':2.1,'text':'control'}
    segment={'text':'control','start':2.1,'end':2.3,'_units':[unit]}
    owned,metadata=module.own_window([segment],84000,89472,52000)
    assert len(owned)==1
    assert owned[0]['start']+5.25==pytest.approx(5.35)
    assert owned[0]['end']+5.25==pytest.approx(5.55)
    assert metadata['boundary_tail'][0]['ownership_anchor']==pytest.approx(5.35)


@pytest.mark.parametrize('a,b,expected',[('', '',0),('abc','abc',0),('abc','ab',1),('abc','axc',1)])
def test_distance_proxy(a,b,expected):
    assert module.distance(a,b)==expected
    assert module.distance(b,a)==expected
