"""Finite fresh-process intake/status phases for the real native smoke.

This helper does not start a service, seed database rows, or replace a model.
The caller owns the unchanged service/resource Gate and waits every child.
Local reports contain synthetic fixture provenance and must stay out of Git.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from capacity_run import REPO, digest_file, save
from audio_backend import recorder_incremental_span_intake as intake
from audio_backend import recorder_incremental_span_status as status
from audio_backend import recorder_incremental_span_export as export
from audio_backend import recording_uploader as u

MAX_INPUTS = 32
PHASES = ('first', 'second', 'finalize', 'repeat', 'status', 'status-repeat', 'export', 'export-repeat')


def need(value, reason):
    if not value:
        raise RuntimeError(reason)


def inventory(roots):
    """Small smoke inputs/journal only, never mutable live service data."""
    rows = {}; total = 0
    for root in roots:
        for path in sorted(Path(root).rglob('*')):
            need(not path.is_symlink(), 'probe_inventory_symlink')
            if path.is_file():
                info = path.stat(); total += info.st_size
                need(total <= 64 * 1024**2 and len(rows) < 2048, 'probe_inventory_bound')
                rows[str(path)] = dict(bytes=info.st_size, sha256=digest_file(path),
                    mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)
    return rows


class Trace(u.HTTPTransport):
    def __init__(self, *args, lose_seal=False, get_only=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []; self.lost = 0; self.lose_seal = lose_seal; self.get_only = get_only

    def request(self, method, path, **kwargs):
        need(not self.get_only or method == 'GET', 'status_attempted_mutation')
        file = kwargs.get('file')
        row = dict(method=method, path=path, key=kwargs.get('key'), body=kwargs.get('body'),
                   file_sha256=digest_file(file) if file else None)
        self.calls.append(row)
        value = super().request(method, path, **kwargs)
        if self.lose_seal and not self.lost and method == 'POST' and path.endswith('/seal') and value.get('released'):
            self.lost += 1
            raise u.Uncertain('Injected loss after actual successful published seal')
        return value


def verify_completed_repeat(initial, repeated):
    seals = [row for row in initial if row['method'] == 'POST' and row['path'].endswith('/seal')]
    need(seals, 'missing_initial_seal')
    mutations = [row for row in repeated if row['method'] != 'GET']
    need(all(row == seals[-1] for row in mutations), 'incremental_repeat_changed_mutation')
    return dict(no_new_sources=True, exact_frozen_seal_replays=len(mutations))


def verify_status(value, reports):
    need(value['status'] == 'completed' and value['analysis_completion_verified'] is True
         and value['read_only'] is True, 'native_status_not_complete')
    start = 0; mapping = []
    for ordinal, report in enumerate(reports):
        end = start + report['frames']
        mapping.append(dict(ordinal=ordinal, original_sequence=report['sequence'],
            original_epoch=report['epoch'], original_epoch_frame_start=report['frame_start'],
            original_epoch_frame_end=report['frame_start'] + report['frames'],
            span_start_frame=start, span_end_frame=end)); start = end
    need(value['input_mapping'] == mapping and value['frames'] == start
         and value['inputs'] == len(reports), 'native_status_mapping_changed')
    description = value['span']
    need(description['original_close_reason'] == reports[-1]['close_reason']
         and description['recorder_stop_observed'] is (reports[-1]['close_reason'] == 'STOP')
         and description['before_span_gap_frames'] is None and description['after_span_gap_frames'] is None
         and description['eos_meaning'] == 'selected_span_only'
         and value['capture_completeness_proven'] is False, 'native_status_scope_changed')


def verify_export_repeat(first, repeated, output, reports):
    """Retain exact private artifacts, return only a bounded aggregate result."""
    need(first['result'] == repeated['result'], 'native_export_repeat_changed')
    value = first['result']
    need(value.get('schema') == export.SCHEMA and value.get('status') == 'exported' and
         value.get('projection') == 'primary-baseline-v1' and value.get('complete_content') is False and
         type(value.get('nonempty_text_units')) is int and value['nonempty_text_units'] > 0 and
         value.get('text_status') == 'primary_text_observed', 'native_export_no_primary_speech_text')
    need(all(row['method'] == 'GET' and row['body'] is None and row['file_sha256'] is None
             for phase in (first, repeated) for row in phase['requests']), 'native_export_mutation')
    need(first['local_files_unchanged'] and repeated['local_files_unchanged'], 'native_export_changed_inputs')
    need(set(value['artifacts']) == {'export.json', 'transcript.txt'}, 'native_export_artifact_inventory')
    for name in ('export.json', 'transcript.txt', 'complete.json'):
        paths = [Path(output) / phase / name for phase in ('export', 'export-repeat')]
        need(all(path.is_file() and not path.is_symlink() and path.stat().st_mode & 0o077 == 0
                 for path in paths), 'native_export_unsafe_artifact')
        need(paths[0].read_bytes() == paths[1].read_bytes(), 'native_export_artifact_repeat_changed')
        if name in value['artifacts']:
            need(paths[0].stat().st_size == value['artifacts'][name]['bytes'] and
                 digest_file(paths[0]) == value['artifacts'][name]['sha256'], 'native_export_artifact_changed')
    document = json.loads((Path(output) / 'export/export.json').read_bytes())
    verify_status(document['completion'], reports)
    need(document['summary'] == {key: value[key] for key in document['summary']}, 'native_export_summary_changed')
    need(len(document['units']) == value['units'] and
         sum(bool(unit['text'].strip()) for unit in document['units']) == value['nonempty_text_units'],
         'native_export_text_count_changed')
    return dict(get_only=True, repeat_identical=True, local_files_unchanged=True,
                nonempty_primary_text_observed=True, units=value['units'],
                nonempty_text_units=value['nonempty_text_units'], frames=value['frames'],
                artifacts=value['artifacts'], accuracy_verified=False,
                speaker_identity_verified=False, capture_completeness_proven=False)


def run_phase(spec, phase, output):
    paths = [Path(path) for path in spec['deliveries']]
    need(2 <= len(paths) <= MAX_INPUTS and 1 <= spec['split'] < len(paths), 'probe_input_count')
    limits = u.Limits(deadline_seconds=120, poll_seconds=.1)
    transport = Trace(spec['api'], u.Guard(limits), lose_seal=phase == 'finalize',
                      get_only=phase.startswith(('status', 'export')))
    args = dict(job_dir=Path(spec['job_dir']), api=spec['api'], session_id=spec['session_id'],
                capture_id=spec['capture_id'], exclusive_writer=True, limits=limits, transport=transport)
    report = dict(schema='native-incremental-span-probe-v1', phase=phase, pid=os.getpid(),
                  passed=False, requests=transport.calls)
    try:
        if phase.startswith(('status', 'export')):
            before = inventory([*paths, args['job_dir']])
            report['result'] = (export.run(**args, output_dir=Path(output).parent / phase)
                                if phase.startswith('export') else status.run(**args))
            report['local_files_unchanged'] = inventory([*paths, args['job_dir']]) == before
            need(report['local_files_unchanged'], 'status_mutated_local_files')
        else:
            args['acknowledge_span_local_closure'] = True
            if phase in ('first', 'second'):
                stages = []; frames = sum(spec['frames'][:spec['split']]) if phase == 'second' else 0
                if phase == 'second':
                    report['midpoint_replay'] = intake.run(**args, delivery=paths[spec['split'] - 1])
                    need(report['midpoint_replay']['inputs'] == spec['split']
                         and report['midpoint_replay']['frames'] == frames, 'midpoint_replay_changed')
                indices = range(spec['split']) if phase == 'first' else range(spec['split'], len(paths))
                for index in indices:
                    value = intake.run(**args, delivery=paths[index], start=index == 0)
                    frames += spec['frames'][index]
                    need(value['inputs'] == index + 1 and value['frames'] == frames
                         and not value['capture_finalized'] and value['published_frames'] == 0
                         and value['status'] == 'delivery_staged_without_eos', 'stage_premature_publication')
                    need(intake.run(**args, delivery=paths[index]) == value, 'latest_replay_changed')
                    stages.append(value)
                need(all(row['method'] == 'GET' for row in transport.calls), 'staging_remote_mutation')
                report['stages'] = stages
            else:
                report['result'] = intake.run(**args, finalize=True)
                need(report['result']['capture_finalized'] and report['result']['published_frames'] == sum(spec['frames']),
                     'finalization_incomplete')
                if phase == 'finalize':
                    need(transport.lost == 1, 'published_seal_loss_not_exercised')
        report['passed'] = True
    except BaseException as exc:
        report['error'] = dict(type=type(exc).__name__, message=str(exc)[:2048])
    finally:
        report['injected_lost_published_seal_responses'] = transport.lost
        save(output, report)
    return 0 if report['passed'] else 1


def child_phase(gate, spec_path, phase):
    """Poll the same Gate during child work; reap original Popen on every path."""
    need(phase in PHASES, 'probe_phase')
    output = gate.out / ('incremental-' + phase + '.json')
    command = [sys.executable, '-I', '-B', str(REPO / 'tools/strict_incremental_span_probe.py'),
               str(spec_path), phase, str(output)]
    with (gate.out / ('incremental-' + phase + '.stdout')).open('xb') as stdout, \
            (gate.out / ('incremental-' + phase + '.stderr')).open('xb') as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, cwd=REPO)
        code = None
        try:
            while process.poll() is None:
                gate.check(); time.sleep(.05)
            code = process.wait(timeout=1)
        finally:
            if process.poll() is None:
                process.terminate()
                try: code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); code = process.wait(timeout=5)
            elif code is None:
                # Gate failure can race a clean child exit after the loop poll.
                code = process.wait(timeout=1)
            save(gate.out / ('incremental-' + phase + '-wait.json'),
                 dict(pid=process.pid, original_popen_wait_completed=True, exit_code=code))
    need(code == 0, 'incremental_child_failed:' + phase)
    value = json.loads(output.read_bytes()); need(value['passed'], 'incremental_phase_failed:' + phase)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec', type=Path); parser.add_argument('phase', choices=PHASES)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    need(args.spec.stat().st_size <= 65536 and not args.output.exists(), 'probe_spec_or_output')
    return run_phase(json.loads(args.spec.read_bytes()), args.phase, args.output)


if __name__ == '__main__':
    raise SystemExit(main())
