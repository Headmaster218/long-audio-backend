"""Pure arithmetic only. No recorded media, native duration or device evidence."""
import pytest
from audio_backend.recorder_capture_profile import plan,main
from audio_backend import recording_uploader as u


def test_legacy64_is_count_limited_not_source_slot_limited():
    result=plan(max_inputs=64,producer_mode="legacy-rotation-only",expected_seconds=7*3600)
    assert result['full_rotations_before_limit']==64
    assert result['peak_sources_per_page_in_planned_prefix']==4
    assert result['limiting_factor']=='input_count'
    assert result['maximum_frames_with_nonempty_stop_tail']==64*300*16000-1
    assert result['required_inputs_with_stop_tail']==85
    assert not result['expected_capture_with_tail_fits']


def test_explicit128_fits_seven_hours_but_not_twelve():
    result=plan(max_inputs=128,producer_mode="legacy-rotation-only",expected_seconds=7*3600)
    assert result['expected_capture_with_tail_fits']
    assert result['maximum_frames_with_nonempty_stop_tail']==128*300*16000-1
    assert not plan(max_inputs=128,expected_seconds=12*3600)['expected_capture_with_tail_fits']
    assert result['minimum_additional_tail_frames']==1


@pytest.mark.parametrize('seconds,inputs',[(1,14),(19,46)])
def test_selectable_tiny_segments_hit_existing_source_reservation(seconds,inputs):
    r=plan(segment_seconds=seconds,max_inputs=128)
    assert r['limiting_factor']=='page_source_slots' and r['full_rotations_before_limit']==inputs
    assert r['first_source_slot_refusal']['input_ordinal']==inputs
    assert r['maximum_frames_with_nonempty_stop_tail']==inputs*seconds*16000-1


@pytest.mark.parametrize('seconds',[20,21,30,60,120,294,300,600,3600])
def test_supported_sample_profiles_do_not_hit_source_slots(seconds):
    assert plan(segment_seconds=seconds,max_inputs=128,max_hours=48)['first_source_slot_refusal'] is None


def test_duration_bound_and_exact_rotation_warning():
    r=plan(segment_seconds=300,max_hours=7,max_inputs=128,expected_seconds=7*3600)
    assert r['limiting_factor']=='duration'
    assert r['maximum_frames_with_nonempty_stop_tail']==7*3600*16000-1
    assert not r['expected_capture_with_tail_fits']
    assert plan(segment_seconds=300,max_hours=8,max_inputs=128,expected_seconds=7*3600)['expected_capture_with_tail_fits']
    r=plan(segment_seconds=600,max_hours=7,max_inputs=128,expected_seconds=7*3600-1)
    assert r['expected_capture_with_tail_fits'] and r['minimum_additional_tail_frames']==0


@pytest.mark.parametrize('change',[dict(segment_seconds=0),dict(segment_seconds=3601),dict(segment_seconds=True),
    dict(max_hours=49),dict(max_inputs=193),dict(expected_seconds=0),dict(expected_seconds=172801)])
def test_invalid_profile(change):
    with pytest.raises(ValueError):plan(**change)


def test_cli_reports_expected_capacity_refusal(capsys):
    assert main(['--expected-seconds','25200','--max-inputs','64'])==2
    assert '"expected_capture_with_stop_fits": false' in capsys.readouterr().out
    assert main(['--expected-seconds','25200','--max-inputs','128'])==0


def test_current_defaults_fit_twelve_hours_with_explicit_full_stop():
    r=plan(expected_seconds=43200)
    assert r['max_inputs']==u.APPEND_DEFAULT_INPUTS==192
    assert r['producer_mode']=='terminal-aware'
    assert r['maximum_frames_with_stop']==43200*u.RATE
    assert r['maximum_frames_with_nonempty_stop_tail']==43200*u.RATE-1
    assert r['required_inputs_with_stop']==144
    assert r['minimum_additional_tail_frames']==0
    assert r['expected_capture_with_stop_fits']
    assert not r['expected_capture_with_tail_fits']
    assert r['explicit_stop_declaration_required'] and r['no_automatic_eos']
    assert r['warning_codes']==[]


@pytest.mark.parametrize('inputs,fits',[(143,False),(144,True),(145,True),(192,True)])
def test_twelve_hour_input_boundary(inputs,fits):
    r=plan(max_inputs=inputs,expected_seconds=43200)
    assert r['expected_capture_with_stop_fits'] is fits
    assert r['required_inputs_with_stop']==144


@pytest.mark.parametrize('mode', ['terminal-aware','legacy-rotation-only'])
def test_nonexact_stop_uses_same_deliveries(mode):
    r=plan(max_inputs=144,expected_seconds=43199,producer_mode=mode)
    assert r['required_inputs_with_stop']==144
    assert r['expected_capture_with_stop_fits']
    assert r['minimum_additional_tail_frames']==0


def test_legacy_exact_boundary_needs_both_one_more_delivery_and_frame():
    for inputs in (143,144,145,192):
        r=plan(max_inputs=inputs,expected_seconds=43200,producer_mode='legacy-rotation-only')
        assert r['required_inputs_with_stop']==145
        assert r['minimum_additional_tail_frames']==1
        assert not r['expected_capture_with_stop_fits']  # Even 192 cannot exceed 12h.
        assert not r['exact_full_segment_stop_supported']
    assert not plan(max_hours=13,max_inputs=144,expected_seconds=43200,
                    producer_mode='legacy-rotation-only')['expected_capture_with_stop_fits']
    assert plan(max_hours=13,max_inputs=145,expected_seconds=43200,
                producer_mode='legacy-rotation-only')['expected_capture_with_stop_fits']


def test_cli_terminal_mode_and_legacy_opt_in(capsys):
    assert main(['--expected-seconds','43200','--max-inputs','144'])==0
    assert '"required_inputs_with_stop": 144' in capsys.readouterr().out
    assert main(['--expected-seconds','43200','--producer-mode','legacy-rotation-only'])==2
    assert '"required_inputs_with_stop": 145' in capsys.readouterr().out
    assert main(['--max-inputs','193'])==2
    assert 'max_inputs_bounds' in capsys.readouterr().out


def test_unknown_producer_mode_rejected():
    with pytest.raises(ValueError,match='producer_mode_invalid'):
        plan(producer_mode='guess-stop')
