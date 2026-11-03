"""Read-only recorder profile capacity arithmetic; no media/job-state access, HTTP or ingestion.

Assumes mono16k and one unchanged epoch. Terminal-aware producers may declare
a full final segment STOP; legacy rotation-only producers need a shorter STOP tail. Does not qualify actual duration, quality or devices.
"""
import argparse
import json

from .recording_uploader import (RATE, PAGE_FRAMES, SOURCE_FRAMES, PAGE_SOURCES,
                                 APPEND_DEFAULT_INPUTS, APPEND_MAX_INPUTS)

PRODUCER_MODES = ("terminal-aware", "legacy-rotation-only")


def plan(*, segment_seconds=300, max_hours=12, max_inputs=APPEND_DEFAULT_INPUTS,
         expected_seconds=None, producer_mode="terminal-aware"):
    if producer_mode not in PRODUCER_MODES:
        raise ValueError("producer_mode_invalid")
    for value, low, high, name in ((segment_seconds,1,3600,'segment_seconds'),
            (max_hours,1,48,'max_hours'),(max_inputs,1,APPEND_MAX_INPUTS,'max_inputs')):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(name + '_bounds')
    if expected_seconds is not None and (type(expected_seconds) is not int or not 1 <= expected_seconds <= 48*3600):
        raise ValueError('expected_seconds_bounds')
    target=segment_seconds*RATE; frame_cap=max_hours*3600*RATE
    emitted=page_frames=slots=peak=full_inputs=terminal_max=stop_max=0
    pages=1; reason='input_count'; refused=None
    for index in range(max_inputs):
        available=frame_cap-emitted
        if not available:
            reason='duration';break
        reservation=(min(PAGE_FRAMES-page_frames,available)+SOURCE_FRAMES-1)//SOURCE_FRAMES
        if slots+reservation>PAGE_SOURCES:
            reason='page_source_slots'
            refused=dict(input_ordinal=index, page_number=pages, pending_sources=slots,
                         pending_frames=page_frames, reserved_sources=reservation)
            break
        # Keep strict shorter-tail capacity separate from terminal-aware STOP.
        # Neither mode creates or infers a terminal declaration.
        terminal_max=max(terminal_max,emitted+min(target-1,available))
        stop_max=max(stop_max,emitted+min(
            target if producer_mode == "terminal-aware" else target-1, available))
        if target>available:
            reason='duration';break
        left=target
        while left:
            if page_frames==PAGE_FRAMES:
                page_frames=slots=0;pages+=1
            piece=min(left,SOURCE_FRAMES,PAGE_FRAMES-page_frames)
            left-=piece;emitted+=piece;page_frames+=piece;slots+=1;peak=max(peak,slots)
            if slots>PAGE_SOURCES:
                raise AssertionError('Planner exceeded admission reservation')
        full_inputs+=1
    if emitted==frame_cap:reason='duration'
    warnings=(['exact_rotation_boundary_requires_additional_stop_tail']
              if producer_mode == 'legacy-rotation-only' else [])
    if reason=='page_source_slots':warnings.append('short_segments_exhaust_page_source_slots')
    if max_inputs*target<frame_cap:warnings.append('input_count_limits_requested_duration')
    result=dict(schema='recorder-capture-profile-plan-v2',producer_mode=producer_mode,
        explicit_stop_declaration_required=True,
        exact_full_segment_stop_supported=producer_mode=='terminal-aware',read_only=True,software_arithmetic_only=True,
        runtime_qualified=False,physical_continuity_proven=False,
        segment_seconds=segment_seconds,max_hours=max_hours,max_inputs=max_inputs,
        page_seconds=PAGE_FRAMES//RATE,source_piece_seconds=SOURCE_FRAMES//RATE,max_sources_per_page=PAGE_SOURCES,
        full_rotations_before_limit=full_inputs,full_rotation_frames_before_limit=emitted,
        maximum_frames_with_stop=stop_max,maximum_seconds_with_stop=stop_max/RATE,
        maximum_frames_with_nonempty_stop_tail=terminal_max,
        maximum_seconds_with_nonempty_stop_tail=terminal_max/RATE,
        limiting_factor=reason,first_source_slot_refusal=refused,
        peak_sources_per_page_in_planned_prefix=peak,warning_codes=warnings,
        no_default_changes=True,no_automatic_eos=True)
    if expected_seconds is not None:
        expected=expected_seconds*RATE
        exact=expected%target==0
        # Legacy needs a new nonempty tail after an exact full ROTATION.
        # Terminal-aware uses the final full delivery, explicitly marked STOP.
        extra=1 if exact and producer_mode == "legacy-rotation-only" else 0
        needed_frames=expected+extra
        result.update(expected_seconds=expected_seconds,
            required_inputs_with_stop=(expected+target-1)//target+extra,
            required_inputs_with_stop_tail=expected//target+1,
            expected_ends_exactly_at_rotation_boundary=exact,
            minimum_additional_tail_frames=extra,
            minimum_additional_frames_for_shorter_stop_tail=1 if exact else 0,
            expected_capture_with_stop_fits=needed_frames<=stop_max,
            expected_capture_with_tail_fits=expected+(1 if exact else 0)<=terminal_max)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--segment-seconds',type=int,default=300)
    parser.add_argument('--max-hours',type=int,default=12)
    parser.add_argument('--max-inputs',type=int,default=APPEND_DEFAULT_INPUTS)
    parser.add_argument('--producer-mode',choices=PRODUCER_MODES,default='terminal-aware')
    parser.add_argument('--expected-seconds',type=int)
    args=vars(parser.parse_args(argv))
    try:result=plan(**args)
    except ValueError as exc:
        print(json.dumps(dict(status='invalid_profile',reason=str(exc))))
        return 2
    print(json.dumps(result,sort_keys=True))
    return 2 if result.get('expected_capture_with_stop_fits') is False else 0


if __name__=='__main__':raise SystemExit(main())
