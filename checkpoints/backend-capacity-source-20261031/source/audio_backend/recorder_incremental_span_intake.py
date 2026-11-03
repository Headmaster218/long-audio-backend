"""Explicit bounded incremental intake for one selected recorder span.

Producer sequence, epoch, frame origin and metadata remain unchanged. Backend
frames start at zero for this selected span only. Every invocation acknowledges
that EOS cannot establish full capture completeness or physical continuity.
"""
import argparse
from dataclasses import dataclass
import json
import os
import re
import sys

from . import recorder_capture_intake as engine
from . import recorder_intake as single
from . import recording_uploader as u
from .recorder_batch_intake import BoundaryRefused
from .recorder_delivery import Refused
from .recorder_metadata import GAPS, LONG_MAX
from .recorder_sequence import _edge
from .recorder_snapshot import _open_directory, _same_directory, _receipt
from .recorder_span_intake import span_description

SCHEMA = 'explicit-incremental-recorder-span-intake-v1'
TERMINAL_CLOSES = frozenset(('STOP', 'INTERRUPTION', 'RECOVERED_TAIL'))
ORIGIN_KEYS = frozenset(('capture_identity_sha256', 'run_identity_sha256', 'epoch',
    'first_sequence', 'epoch_frame_start', 'sample_rate_hz', 'channels',
    'requested_input', 'actual_input', 'boundary', 'target_frames'))
require = single.require


@dataclass(frozen=True)
class SpanPolicy(engine.IntakePolicy):
    schema: str = SCHEMA

    @property
    def header_keys(self):
        return frozenset(('span_origin', 'acknowledgments'))

    def same(self, actual, expected):
        # JSON declarations must retain exact types: False is not frame zero
        # and True is not ordinal one, even though Python equality aliases them.
        return single._json(actual) == single._json(expected)

    def header_fields(self, first):
        report, metadata = first['delivery'], first['metadata']
        origin = {key: report[key] for key in ('capture_identity_sha256',
                  'run_identity_sha256', 'epoch', 'sample_rate_hz', 'channels')}
        origin.update(first_sequence=report['sequence'], epoch_frame_start=report['frame_start'])
        origin.update({key: metadata[key] for key in
                       ('requested_input', 'actual_input', 'boundary', 'target_frames')})
        return dict(span_origin=origin,
                    acknowledgments=dict(exclusive_writer=True, span_local_closure=True))

    def validate_header(self, header):
        require(header.get('initial_intent') is True, 'span_initial_intent_required')
        require(single._json(header['acknowledgments']) == single._json(
            dict(exclusive_writer=True, span_local_closure=True)), 'span_acknowledgments_changed')
        origin = header['span_origin']
        require(type(origin) is dict and set(origin) == ORIGIN_KEYS, 'span_origin_fields')
        require(all(type(origin[key]) is int and 0 <= origin[key] <= LONG_MAX
                    for key in ('epoch', 'first_sequence', 'epoch_frame_start')),
                'span_origin_coordinates')
        require(type(origin['sample_rate_hz']) is int and origin['sample_rate_hz'] == u.RATE
                and type(origin['channels']) is int and origin['channels'] == 1,
                'span_requires_mono16k')
        require(type(origin['target_frames']) is int and 0 < origin['target_frames'] <= LONG_MAX,
                'span_origin_target_frames')
        require(all(type(origin[key]) is str and re.fullmatch(r'[0-9a-f]{64}', origin[key])
                    for key in ('capture_identity_sha256', 'run_identity_sha256')),
                'span_origin_identity')
        require(all(type(origin[key]) is str and 0 < len(origin[key]) <= 512
                    for key in ('requested_input', 'actual_input'))
                and type(origin['boundary']) is str and origin['boundary'] in GAPS,
                'span_origin_configuration')

    def validate_snapshot(self, snapshot, report, remaining):
        # The legacy inspector returns the observed report after comparing it
        # with Python equality. Check the raw receipt too, so a changed boolean
        # or float cannot hide behind that normalized result in this schema.
        descriptor = _open_directory(snapshot)
        try:
            value, _ = _receipt(descriptor, remaining)
            require(self.same(value['body']['delivery'], report),
                    'span_snapshot_report_types_changed')
            require(all(type(entry) is dict and set(entry) == {'bytes', 'sha256'}
                        and type(entry['bytes']) is int and entry['bytes'] > 0
                        and type(entry['sha256']) is str
                        and re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])
                        for entry in value['body']['files'].values()),
                    'span_snapshot_file_types_changed')
            _same_directory(snapshot, descriptor)
        finally:
            os.close(descriptor)

    def validate_report(self, report):
        require(report['sample_rate_hz'] == u.RATE and report['channels'] == 1,
                'span_requires_mono16k')
        require(report['close_reason'] in TERMINAL_CLOSES | {'ROTATION'}, 'span_close_reason')

    def validate_edge(self, previous, current, header=None):
        report = current['delivery']; self.validate_report(report)
        if previous is None:
            # No missing predecessor is synthesized and no producer START is
            # invented. Bind the actual first declaration to immutable intent.
            if header is not None:
                expected = self.header_fields(current)
                require(all(single._json(header[key]) == single._json(value)
                            for key, value in expected.items()), 'span_origin_binding_changed')
            return
        require(previous['delivery']['capture_identity_sha256'] == report['capture_identity_sha256'],
                'capture_producer_identity_changed')
        # _edge includes terminal-predecessor and recovered-tail adjacency
        # refusals. A recovered uncertain tail is therefore singleton-only.
        edge = _edge(previous, current, set())
        if edge['reasons']:
            raise BoundaryRefused(edge['reasons'])

    def ordinal(self, report, header):
        return report['sequence'] - header['span_origin']['first_sequence']

    def input_start(self, receipts, index):
        # Never subtract the original absolute frame origin: only the proven
        # selected prefix determines a backend-local input position.
        return sum(receipt['delivery']['frames'] for receipt in receipts[:index])

    def finalizable(self, report):
        return report['close_reason'] in TERMINAL_CLOSES

    @property
    def terminal_required_reason(self):
        return 'span_terminal_closure_required_before_finalize'

    def result_fields(self, records, header):
        description = span_description([record['delivery'] for record in records])
        description['original_run_identity_sha256'] = header['span_origin']['run_identity_sha256']
        return dict(span=description, capture_completeness_proven=False,
                    eos_meaning='selected_span_only')


POLICY = SpanPolicy()


def run(*, job_dir, api, session_id, capture_id, delivery=None, start=False, finalize=False,
        exclusive_writer=False, acknowledge_span_local_closure=False,
        max_hours=None, max_inputs=None, limits=None, transport=None, cancel=None,
        recover_initial=False):
    require(acknowledge_span_local_closure is True, 'span_local_closure_ack_required')
    return engine._run(policy=POLICY, job_dir=job_dir, api=api, session_id=session_id,
        capture_id=capture_id, delivery=delivery, start=start, finalize=finalize,
        exclusive_writer=exclusive_writer, max_hours=max_hours, max_inputs=max_inputs,
        limits=limits, transport=transport, cancel=cancel, recover_initial=recover_initial)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('job-dir', 'api', 'session-id', 'capture-id'):
        parser.add_argument('--' + name, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--delivery'); group.add_argument('--finalize', action='store_true')
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--recover-initial', action='store_true',
        help='Exact initial recovery requires durable span intent and a pristine uploader')
    parser.add_argument('--exclusive-writer', action='store_true', required=True)
    parser.add_argument('--acknowledge-span-local-closure', action='store_true', required=True)
    parser.add_argument('--max-hours', type=int); parser.add_argument('--max-inputs', type=int)
    parser.add_argument('--deadline-seconds', type=float, default=12*3600)
    args = vars(parser.parse_args(argv)); deadline = args.pop('deadline_seconds')
    try:
        result = run(**args, limits=u.Limits(deadline_seconds=deadline))
    except (Exception, KeyboardInterrupt) as exc:
        result = single.failure_report(exc); result['schema'] = SCHEMA
        if isinstance(exc, BoundaryRefused):
            result['boundary_reason_codes'] = exc.reasons
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
