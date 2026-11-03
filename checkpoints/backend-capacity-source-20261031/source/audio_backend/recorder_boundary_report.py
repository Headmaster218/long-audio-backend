"""Bounded read-only partition of verified recorder snapshot declarations.

Spans are observations, not import plans or proof of physical continuity. No
networking, session targets, normalization, gap repair or source mutation occurs.
"""
import argparse
import hashlib
import json
from pathlib import Path

from .recorder_delivery import Refused
from .recorder_sequence import _report, _verified_records, require
from .recorder_snapshot import _deadline

SCHEMA = 'recorder-boundary-observation-v1'
MAX_REPORT_BYTES = 256 * 1024


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _source(capture, sequence):
    return 'segment-' + capture + '-' + str(sequence) + '.ready'


def _span_id(capture, sequence):
    # Identity depends on the declared source at this boundary, never on a
    # selected content hash, input order, paths, or the current end of the span.
    return 'span-' + _digest(SCHEMA + '\n' + _source(capture, sequence))


def _segment(capture, rows):
    """An allowlist: never serialize the internal metadata/delivery dictionaries."""
    row = rows[0]
    m = row['metadata']
    return dict(source_id=_source(capture, m['sequence']),
                input_indices=sorted(r['input_index'] for r in rows),
                sequence=m['sequence'], epoch=m['epoch'],
                run_identity_sha256=_digest(m['run_id']),
                sample_rate_hz=m['sample_rate_hz'], channels=m['channels'],
                frame_start=m['frame_start'], frame_end_exclusive=m['frame_start'] + m['frames'],
                frames=m['frames'], target_frames=m['target_frames'],
                boundary=m['boundary'], close_reason=m['close_reason'],
                unsupported_close_reasons=(['interruption_close_not_supported_by_current_intake']
                                           if m['close_reason'] == 'INTERRUPTION' else
                                           ['recovered_tail_close_not_supported_by_current_intake']
                                           if m['close_reason'] == 'RECOVERED_TAIL' else []),
                trusted_previous_seam=m['trusted_previous_seam'],
                wav_bytes=row['delivery']['wav_bytes'], wav_sha256=row['delivery']['wav_sha256'],
                local_metadata_sha256=row['local_metadata_sha256'])


def _span(capture, segments):
    first, last = segments[0], segments[-1]
    return dict(span_id=_span_id(capture, first['sequence']),
                relation='declaration_contiguous_observed_audio',
                first_sequence=first['sequence'], last_sequence=last['sequence'],
                epoch=first['epoch'], run_identity_sha256=first['run_identity_sha256'],
                sample_rate_hz=first['sample_rate_hz'], channels=first['channels'],
                frame_start=first['frame_start'], frame_end_exclusive=last['frame_end_exclusive'],
                coordinate_basis='producer_epoch_local_recorded_pcm_declaration',
                segment_count=len(segments), observed_frames=sum(s['frames'] for s in segments),
                observed_wav_bytes=sum(s['wav_bytes'] for s in segments),
                first_boundary=first['boundary'], last_close_reason=last['close_reason'],
                unsupported_close_reasons=sorted({reason for s in segments for reason in s['unsupported_close_reasons']}),
                segments=segments, physical_continuity_proven=False, strict_import_ready=False)


def _partition_records(records):
    """Pure analysis of already verified rows; callers must use report_snapshots."""
    analysis = _report(records)
    captures = []
    for summary in analysis['captures']:
        capture = summary['capture_identity_sha256']
        grouped = {}
        for row in records:
            if row['delivery']['capture_identity_sha256'] != capture:
                continue
            key = (row['local_metadata_sha256'], row['delivery']['wav_sha256'])
            grouped.setdefault(row['metadata']['sequence'], {}).setdefault(key, []).append(row)
        sequences = sorted(grouped)
        conflicted = set(summary['conflicting_ordinals'])
        edges = {(e['previous_sequence'], e['current_sequence']): e for e in summary['edges']}
        segments = {}
        quarantines = []
        repeat_count = 0
        for sequence in sequences:
            variants = [_segment(capture, rows) for _, rows in sorted(grouped[sequence].items())]
            repeat_count += sum(len(v['input_indices']) - 1 for v in variants)
            if sequence in conflicted:
                quarantines.append(dict(source_id=_source(capture, sequence), sequence=sequence,
                                        reason='conflicting_ordinal_content', selected_content=None,
                                        distinct_content_count=len(variants), variants=variants,
                                        physical_continuity_proven=False, strict_import_ready=False))
            else:
                segments[sequence] = variants[0]

        spans = []
        current = []
        boundaries = []
        previous = None
        for sequence in sequences:
            unresolved = False
            if previous is not None:
                edge = edges[(previous, sequence)]
                touches_conflict = previous in conflicted or sequence in conflicted
                # _report has a representative for diagnostics at a conflicting
                # ordinal. None of its variant-dependent declarations may win.
                if touches_conflict:
                    reasons = ['conflicting_ordinal_content']
                    if sequence - previous > 1:
                        reasons.append('missing_ordinals')
                else:
                    reasons = list(edge['reasons'])
                unresolved = bool(reasons)
                if unresolved:
                    delta = None
                    if not touches_conflict:
                        a, b = segments[previous], segments[sequence]
                        if a['epoch'] == b['epoch'] and 'same_epoch_declaration_conflict' not in reasons:
                            delta = dict(frames=b['frame_start'] - a['frame_end_exclusive'],
                                         basis='declaration_only', physical_duration_proven=False)
                    boundaries.append(dict(previous_source_id=_source(capture, previous),
                                           current_source_id=_source(capture, sequence),
                                           previous_sequence=previous, current_sequence=sequence,
                                           relation='unresolved_boundary', reasons=reasons,
                                           missing_ordinal_count=sequence - previous - 1,
                                           declared_frame_delta=delta, physical_gap_duration_seconds=None,
                                           physical_continuity_proven=False, strict_import_ready=False))
            if current and (unresolved or sequence in conflicted):
                spans.append(_span(capture, current))
                current = []
            if sequence not in conflicted:
                current.append(segments[sequence])
            previous = sequence
        if current:
            spans.append(_span(capture, current))

        first_sequence = sequences[0]
        leading_reasons = []
        if first_sequence:
            leading_reasons.append('prefix_not_supplied')
        if first_sequence in conflicted:
            leading_reasons.append('conflicting_ordinal_content')
        else:
            first = segments[first_sequence]
            for condition, reason in (
                (first['frame_start'] != 0, 'first_supplied_frame_nonzero'),
                (first['trusted_previous_seam'], 'predecessor_not_supplied'),
                (first['epoch'] != 0, 'initial_epoch_not_zero'),
                (first['boundary'] != 'START', 'initial_boundary_not_start'),
                (first['close_reason'] == 'RECOVERED_TAIL', 'recovered_tail'),
            ):
                if condition:
                    leading_reasons.append(reason)
        entry_reasons = {boundary['current_sequence']: boundary['reasons'] for boundary in boundaries}
        for span in spans:
            span['entry_reasons'] = list(leading_reasons if span['first_sequence'] == first_sequence
                                         else entry_reasons[span['first_sequence']])
        final_variants = grouped[sequences[-1]]
        captures.append(dict(capture_identity_sha256=capture, snapshot_input_count=summary['input_count'],
                             unique_ordinal_count=len(sequences), same_content_repeat_count=repeat_count,
                             unambiguous_segment_count=len(segments), conflicting_ordinals=sorted(conflicted),
                             first_supplied_sequence=first_sequence, prefix_not_supplied=first_sequence != 0,
                             leading_observation=dict(reasons=leading_reasons, physical_gap_duration_seconds=None),
                             last_supplied_close_reasons=sorted({rows[0]['metadata']['close_reason'] for rows in final_variants.values()}),
                             spans=spans, boundaries=boundaries, quarantines=quarantines,
                             unambiguous_observed_wav_bytes=sum(s['wav_bytes'] for s in segments.values()),
                             quarantined_distinct_content_wav_bytes=sum(v['wav_bytes'] for q in quarantines for v in q['variants']),
                             warning_codes=summary['warning_codes'], capture_completeness_proven=False,
                             physical_continuity_proven=False, strict_import_ready=False))
    return dict(schema=SCHEMA, snapshot_input_count=len(records), captures=captures,
                observations_only=True, physical_continuity_proven=False,
                hardware_frame_mapping='UNESTABLISHED', strict_import_ready=False,
                gap_repair_performed=False, epoch_ingestion_supported=False)


def _json(report):
    raw = json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    require(len(raw.encode('ascii')) + 1 <= MAX_REPORT_BYTES, 'boundary_report_output_bytes')
    return raw


def report_snapshots(paths, *, deadline_seconds=120):
    """Verify every explicit snapshot, then report; never return partial success."""
    remaining = _deadline(deadline_seconds)
    records, total = _verified_records(paths, remaining)
    report = _partition_records(records)
    # This is validation workload, including repeat arguments. Span audio totals
    # count each unambiguous source once; quarantined variants are separate.
    report['verified_input_wav_bytes'] = total
    _json(report)
    remaining()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshots', nargs='+', type=Path)
    args = parser.parse_args(argv)
    try:
        result = report_snapshots(args.snapshots)
    except (OSError, Refused) as exc:
        print(json.dumps(dict(error=str(exc) if isinstance(exc, Refused) else 'boundary_report_file_access_failed',
                              observations_only=True, physical_continuity_proven=False,
                              strict_import_ready=False, gap_repair_performed=False)))
        return 2
    print(_json(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
