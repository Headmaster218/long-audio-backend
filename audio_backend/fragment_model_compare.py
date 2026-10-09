"""Offline staged comparison driver. No HTTP, legacy Store, or identity writes."""
from contextlib import contextmanager, closing
from dataclasses import asdict
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

from .fragment_evidence import EvidenceStore
from .fragment_model_bridges import AnalyzerBridge, CallBudget, NativeBridge, NativeFactory
from .fragment_model_guard import Identity, ResourceGuard, ResourceLimit
from .fragment_model_protocol import (
    FIXTURES, FrozenFiles, freeze, materialize_arm, proposal, read_manifest, sha256, validate_approval,
)
from .fragment_runtime import LogicalRunner
from .fragment_runtime_contracts import RATE, RuntimePolicy, bounded_json, canonical, digest
from .fragment_runtime_store import RuntimeStore


def require_private_output(path):
    path = Path(path).resolve()
    root = Path(__file__).resolve().parents[1]
    relative = path.relative_to(root) if path.is_relative_to(root) else None
    if relative is None or len(relative.parts) < 2 or relative.parts[0] != 'reports' or not relative.parts[1].startswith('private_'):
        raise ValueError('Real experiment/freeze output requires an excluded reports/private_* subtree inside this checkout')
    return path


def atomic_json(path, value, guard):
    encoded = bounded_json(value, 2*1024**2).encode()
    guard.check(len(encoded))
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.pending')
    with temporary.open('wb') as stream:
        stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class StageStore:
    """Experiment-only immutable candidate/failure records, separate from cores."""
    def __init__(self, path, evidence):
        self.path, self.evidence = str(path), evidence
        self.evidence.require_external_roots()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS stages (id TEXT PRIMARY KEY, payload TEXT NOT NULL, hash TEXT NOT NULL)')

    def put(self, stage_id, payload):
        if not isinstance(stage_id, str) or not 0 < len(stage_id) <= 256:
            raise ValueError('Bounded candidate stage ID required')
        text = bounded_json(payload)
        if 'evidence_ref' in payload:
            self.evidence.read(payload['evidence_ref'])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            if 'evidence_ref' in payload:
                self.evidence.read(payload['evidence_ref'])
            db.execute('INSERT INTO stages VALUES (?,?,?)', (stage_id, text, digest(payload)))

    def rows(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            result = []
            for stage_id, text, sha in db.execute('SELECT id,payload,hash FROM stages ORDER BY id'):
                value = json.loads(text)
                if digest(value) != sha:
                    raise ValueError('Candidate stage record hash changed')
                if 'evidence_ref' in value:
                    self.evidence.read(value['evidence_ref'])
                result.append({'id': stage_id, **value})
            return result

    def begin_moss_attempt(self, freeze_sha256, arms, limits):
        # Durable pre-call admission fence; interrupted/failed attempts consume
        # their scope and cannot silently reset the budget on another invocation.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            existing = [row[0] for row in db.execute('SELECT id FROM stages')]
            if any(name.startswith('moss:') or name.endswith(':moss') for name in existing):
                raise ValueError('MOSS stage already attempted; a separately approved new attempt is required')
            payload = {'status': 'attempt_started', 'freeze_sha256': freeze_sha256,
                       'arms': list(arms), 'reserved_maxima': limits}
            db.execute('INSERT INTO stages VALUES (?,?,?)', ('moss:attempt', canonical(payload), digest(payload)))

    def references(self):
        rows = self.rows()
        return [row[key] for row in rows for key in ('evidence_ref', 'core_evidence_ref') if key in row]


class Watchdog:
    """Own-process watchdog for the standalone experiment only, <=50ms polls."""
    def __init__(self, guard, report_path):
        self.guard, self.report_path = guard, Path(report_path)
        self.operation_deadline = None
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        own = Identity.bind(os.getpid())
        if own not in self.guard.own_roots:
            raise ValueError('Watchdog may run only in its explicitly owned experiment process')
        def watch():
            while not self.stop.wait(.05):
                try:
                    self.guard.check()
                    if self.operation_deadline is not None and time.monotonic() > self.operation_deadline:
                        raise TimeoutError('Approved operation deadline exceeded')
                except BaseException as exc:
                    # A tiny bounded diagnostic may still be written when the
                    # ordinary artifact quota is breached. No shared path.
                    try:
                        try:
                            self.report_path.write_text(canonical({'error': type(exc).__name__, 'message': str(exc)[:2000]}))
                        except BaseException:
                            pass  # Disk/permission failure must not disable the watchdog.
                    finally:
                        try:
                            self.guard.terminate_owned()
                        finally:
                            os._exit(75)  # Guaranteed even if cleanup itself fails.
        self.thread = threading.Thread(target=watch, daemon=True)
        self.thread.start()

    @contextmanager
    def operation(self, seconds):
        self.operation_deadline = time.monotonic()+seconds
        try:
            yield
        finally:
            self.operation_deadline = None

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)


def native_provenance(config):
    """Reproduce the frozen wrapper's model-free provenance calculation."""
    from .inference.native_diarization import GEOMETRY, POSTPROCESSING, MORPHOLOGY_GUARD_SECONDS, RUNTIME_VERSION
    library, model = Path(config['native_diarization_library']), Path(config['native_diarization_model'])
    value = {
        'diarization_backend': 'nemotron3-native', 'diarization_runtime': 'NeMo-Speech.cpp',
        'diarization_runtime_version': RUNTIME_VERSION, 'diarization_library_sha256': sha256(library),
        'diarization_model_sha256': sha256(model), 'diarization_model_license': 'OpenMDW-1.1',
        'diarization_model_license_url': 'https://openmdw.ai/license/1-1/',
        'diarization_preset': 'v3-offline', 'diarization_geometry': dict(GEOMETRY),
        'diarization_postprocessing': dict(POSTPROCESSING), 'diarization_local_capacity': 8,
        'diarization_epoch_input_cap_seconds': float(config.get('native_diarization_epoch_max_seconds', 360)),
        'diarization_morphology_guard_seconds': MORPHOLOGY_GUARD_SECONDS,
        'diarization_global_identity': 'separate ERes2Net evidence and identity registry',
        'diarization_capacity_warning': 'At most 8 distinct speakers in one epoch, including replay/context; not unlimited speakers.',
        'diarization_native_requested_threads': 4,
        'diarization_runtime_files_sha256': {p.name: sha256(p) for p in sorted(library.parent.iterdir())
            if p.is_file() and not p.is_symlink() and any(n in p.name.lower() for n in ('nemo_speech_asr', 'ggml', 'llama'))},
    }
    value['diarization_fingerprint'] = digest(value)
    value['diarization_wrapper_cpu_limit'] = config.get('native_diarization_threads', 2)
    value['diarization_cpu_affinity_enforced'] = True
    return value


def execute_base(frozen, approval, output, guard, watchdog):
    from .native_worker import NativeProcess
    from .fragment_model_meter import make_metered_analyzer
    arms, limits = validate_approval(approval, frozen, 'base')
    if limits['moss_support']['calls'] or limits['moss_load']['calls']:
        raise ValueError('Base approval must leave the separate MOSS stage disabled')
    output = require_private_output(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output/'fragment-compare-ledger.sqlite').exists():
        raise ValueError('Base execution requires a fresh output; replay needs a separately approved attempt')
    temp = output/'tmp'; temp.mkdir(exist_ok=True)
    manifest = read_manifest(frozen['manifest_path'])
    files = FrozenFiles(frozen['files']); files.verify(full=True)
    budget = CallBudget(limits)
    config = copy.deepcopy(frozen['config'])
    # This path is private to this execution; no running service settings change.
    config['data_dir'] = str(output)
    config['_fragment_expected_imports'] = frozen['files']
    config['_fragment_native_max_response_bytes'] = 2*1024**2
    config_bytes = len(bounded_json(config, 2*1024**2).encode())
    native_fp = digest({'freeze': digest(frozen), 'role': 'native'})
    analyzer_fp = digest({'freeze': digest(frozen), 'role': 'analyzer'})
    policy = RuntimePolicy(native_fingerprint=native_fp, analyzer_fingerprint=analyzer_fp)
    expected_native = native_provenance(config)
    results = []
    with EvidenceStore(output/'evidence', reserve=guard.check) as evidence:
        store = RuntimeStore(output/'fragment-compare-ledger.sqlite', evidence_store=evidence)
        stages = StageStore(output/'private-stages.sqlite', evidence)
        for arm in arms:
            # Fresh state even for equal PCM arms; never reuse neural outputs.
            with materialize_arm(arm, manifest, frozen['source_root'], temp, guard) as prepared:
                result = {k: prepared[k] for k in ('arm', 'status', 'trims', 'source_bindings')}
                if prepared['status'] != 'admitted_exact_pcm':
                    results.append(result)
                    continue
                run_id = arm
                store.create(run_id, prepared['plan'], policy)
                if store.snapshot(run_id).next_frame:
                    raise ValueError('Automatic resume/replay is not budget-approved; use a fresh approved attempt')
                snapshot = store.snapshot(run_id)
                settings = SimpleNamespace(data_dir=output, disk_reserve_bytes=int(5.5*1024**3),
                    process_memory_limit_bytes=int(3.5*1024**3), cpu_threads=2,
                    inference_config=lambda: config)
                def failure_sink(value):
                    ref = evidence.put(value)
                    stages.put(run_id+':native-failure:'+str(len(stages.rows())), {'evidence_ref': ref, 'status': 'failed'})
                def create_process():
                    guard.check(config_bytes)
                    return NativeProcess(settings)
                factory = NativeFactory(lambda: NativeBridge(create_process,
                    fingerprint=native_fp, expected_provenance=expected_native, temp_root=temp, verify=files.verify,
                    budget=budget, guard=guard, eos_control=snapshot.control['control_id'],
                    eos_epoch=snapshot.control['epoch'], eos_final_frame=snapshot.control['final_frame'], failure_sink=failure_sink, expected_imports=frozen['files']))
                analyzer = None
                try:
                    guard.check()
                    actual = make_metered_analyzer(config, budget, guard)
                    analyzer = AnalyzerBridge(actual, fingerprint=analyzer_fp, evidence_store=evidence,
                        temp_root=temp, verify=files.verify, budget=budget, guard=guard,
                        provenance={'freeze_sha256': digest(frozen), 'implementation_commit': frozen['implementation_commit'],
                                    'native_fingerprint': native_fp, 'analyzer_fingerprint': analyzer_fp},
                        source_bindings=prepared['source_bindings'])
                    runner = LogicalRunner(store, run_id, prepared['paths'], analyzer, factory)
                    while True:
                        with watchdog.operation(300):
                            state = runner.step()
                        if state == 'completed':
                            break
                        if state != 'committed':
                            raise ValueError(f'Final offline arm cannot progress: {state}')
                    commits = store.commits(run_id)
                    result.update(status='completed', read_bindings=prepared['read_bindings'],
                                  whole_pcm_sha256=prepared['whole_pcm_sha256'],
                                  committed_windows=[p['window_id'] for p in commits])
                except BaseException as exc:
                    result.update(status='failed', error=f'{type(exc).__name__}: {exc}')
                    if analyzer and analyzer.failure_ref:
                        stages.put(run_id+':analyzer-failure', {'status': 'failed', 'evidence_ref': analyzer.failure_ref})
                    results.append(result)
                    atomic_json(output/'run-manifest.json', {'freeze_sha256': digest(frozen), 'arms': results,
                        'partial': True, 'model_calls': budget.records, 'error': result['error']}, guard)
                    raise  # No hidden retries or proceeding with a silently failed arm.
                finally:
                    factory.close()
                    if analyzer:
                        analyzer.close()
                results.append(result)
                atomic_json(output/'run-manifest.json', {'freeze_sha256': digest(frozen), 'arms': results,
                    'partial': len(results) != len(arms), 'model_calls': budget.records}, guard)
        files.verify(full=True)
        comparison = compare_commits(store, results, evidence, manifest)
        atomic_json(output/'comparison.json', comparison, guard)
        atomic_json(output/'model-calls.json', {'used': budget.used, 'records': budget.records}, guard)
        # Cleanup is lease-exclusive and only after both core and stage roots
        # have been read and verified. Interrupted runs retain raw failure blobs.
        refs = [p['evidence_ref'] for arm in results if arm['status'] == 'completed' for p in store.commits(arm['arm'])]
        evidence.cleanup(refs, external_reference_provider=stages.references)
    return comparison


def compare_commits(store, results, evidence, manifest=None):
    """Exact observed comparisons, without inferring accuracy from PCM equality."""
    rows, normalized = [], {}
    for result in results:
        arm = result['arm']
        if result['status'] != 'completed':
            rows.append({'arm': arm, 'status': result['status']})
            continue
        commits = store.commits(arm)
        data = {'pcm_hashes': [p['pcm_hash'] for p in commits],
                'lexical': [{k: s[k] for k in ('start', 'end', 'text', 'ownership_anchor', 'routing_anchor')}
                            for p in commits for s in p['segments']],
                'events': [{k: e[k] for k in ('event', 'start_frame', 'end_frame', 'native_start_frame', 'native_end_frame')}
                           for p in commits for e in p['events']]}
        normalized[arm] = data
        from .fragment_model_score import score_base
        fixture = next((f for f in (manifest or {}).get('fixtures', []) if f['id'] == FIXTURES[arm[0]]), None)
        quality = score_base(fixture, commits) if fixture else {'status': 'no_reference_provided'}
        rows.append({'arm': arm, 'status': 'completed', 'owned_frames': sum(p['window']['core_end']-p['window']['core_start'] for p in commits),
                     'core_count': len(commits), 'raw_evidence': [p['evidence_ref'] for p in commits],
                     'laughter_quality': 'unsupported_label' if arm.startswith('F_') else 'not_applicable',
                     'event_semantics': 'supported categories / window support, not event count',
                     'speaker_identity': 'epoch local; no slot-number cross-run shortcut',
                     'model_quality_diagnostics': quality})
    pairs = []
    for arm, data in normalized.items():
        baseline = arm[0]+'_whole'
        if arm == baseline or baseline not in normalized:
            continue
        reference = normalized[baseline]
        pairs.append({'baseline': baseline, 'arm': arm,
                      'exact_logical_read_pcm_equal': data['pcm_hashes'] == reference['pcm_hashes'],
                      'observed_lexical_projection_equal': data['lexical'] == reference['lexical'],
                      'observed_supported_event_intervals_equal': data['events'] == reference['events'],
                      'speaker_comparison': 'requires anonymous-label optimal assignment; raw evidence retained',
                      'model_equality_is_not_accuracy': True})
    return {'arms': rows, 'pairs': pairs, 'claim_scope': 'actual-call outputs and exact PCM/source ownership; raw timing retained'}


def prepare_moss(arm, commit, evidence, config):
    from .moss_td_candidates import plan_supports
    if arm not in ('B_whole', 'B_split') or commit['window']['core_start'] != 0 or commit['window']['core_end'] != 179680:
        raise ValueError('Separate MOSS comparison is limited to the exact B owned core')
    raw = evidence.verify_core(commit['evidence_ref'], commit, arm)
    turns = raw['analyzer_request']['diarization']['turns']
    frozen_config = {**config, 'moss_td_trigger': 'native_overlap', 'moss_td_max_support_seconds': 20,
                     'moss_td_max_seconds': 11.23, 'candidate_core_start': 0., 'candidate_core_end': 11.23}
    regions, supports = plan_supports(turns, 11.23, frozen_config)
    if len(supports) > 1:
        raise ValueError('One B core may have at most one MOSS support')
    for plan in supports:
        old = plan['shared_support_id']
        plan['shared_support_id'] = arm+':'+commit['window_id']+':'+old
        for region in plan['regions']:
            region['shared_support_id'] = plan['shared_support_id']
            region['overlap_group_id'] = arm+':'+commit['window_id']+':'+region['overlap_group_id']
    return {'arm': arm, 'core_evidence_ref': commit['evidence_ref'], 'regions': regions, 'supports': supports,
            'status': 'eligible_native_overlap' if supports else 'not_attempted_native_gate'}


def execute_moss(frozen, approval, output, guard, watchdog):
    from .moss_td_candidates import parse_support, reject_cpp_loader_overrides
    from .overlap_candidates import validate_candidates
    arms, limits = validate_approval(approval, frozen, 'moss')
    if any(limits[k]['calls'] for k in limits if k not in ('moss_load', 'moss_support')):
        raise ValueError('Separate MOSS approval cannot authorize base model calls')
    config = frozen.get('moss_config')
    if not config or config.get('moss_td_runtime') != 'cpp' or config.get('moss_td_cpp_device') != 'cpu':
        raise ValueError('Freeze must explicitly include the existing C++ CPU Q8 closure/config')
    output = require_private_output(output)
    manifest_file = output/'run-manifest.json'
    base_manifest = json.loads(manifest_file.read_text())
    if base_manifest.get('freeze_sha256') != frozen.get('base_freeze_sha256', digest(frozen)):
        raise ValueError('MOSS source arms do not belong to the explicitly bound base freeze')
    files = FrozenFiles(frozen['files']); files.verify(full=True)
    budget = CallBudget(limits)
    manifest = read_manifest(frozen['manifest_path'])
    alias = manifest['optional_existing_alias']
    wav = Path(frozen['source_root'])/alias['path']
    if sha256(wav) != alias['sha256']:
        raise ValueError('B original-mixture alias changed')
    with EvidenceStore(output/'evidence', reserve=guard.check) as evidence:
        store = RuntimeStore(output/'fragment-compare-ledger.sqlite', evidence_store=evidence)
        stages = StageStore(output/'private-stages.sqlite', evidence)
        preparations, jobs = [], []
        for arm in arms:
            snapshot = store.snapshot(arm)
            if snapshot.status != 'completed':
                raise ValueError('MOSS requires completed true-native/base-analyzer arm')
            commits = store.commits(arm)
            if len(commits) != 1:
                raise ValueError('B must have exactly one committed core')
            prepared = prepare_moss(arm, commits[0], evidence, config)
            preparations.append(prepared)
            for support in prepared['supports']:
                if support['start'] != 0 or support['end'] != 11.23:
                    raise ValueError('Unexpected support; no hidden source trimming')
                jobs.append({'support_id': support['shared_support_id'], 'path': str(wav)})
        if len(jobs) > 2:
            raise ValueError('MOSS support cap exceeded')
        stages.begin_moss_attempt(digest(frozen), arms, limits)
        raw = {'supports': [], 'metadata': {'model_loads': 0}}
        if jobs:
            load = budget.reserve('moss_load')
            tickets = [budget.reserve('moss_support', 179680, context=job['support_id']) for job in jobs]
            reject_cpp_loader_overrides(config)
            with tempfile.TemporaryDirectory(prefix='moss-stage-', dir=output/'tmp') as directory:
                directory = Path(directory)
                request, result = directory/'request.json', directory/'result.json'
                request_value = {'config': {**config, 'moss_td_trigger': 'native_overlap',
                    'moss_td_max_support_seconds': 20, 'moss_td_max_seconds': 22.46}, 'supports': jobs, 'output': str(result)}
                atomic_json(request, request_value, guard)
                command = [config['moss_td_python'], str(Path(__file__).parent/'moss_td_runner.py'), str(request)]
                # Existing native runner is isolated; base children are absent.
                process = subprocess.Popen(command, cwd=Path(__file__).parents[1],
                    env={**os.environ, 'AUDIO_PARENT_PID': str(os.getpid()),
                         'AUDIO_PARENT_CREATED': str(Identity.bind(os.getpid()).created)})
                try:
                    with watchdog.operation(600):
                        while process.poll() is None:
                            guard.check(); time.sleep(.05)
                    if result.exists() and result.stat().st_size <= 2*1024**2:
                        raw = json.loads(result.read_text())
                    else:
                        error_path = result.with_suffix('.error.json')
                        raw = {'supports': [], 'failure': {'returncode': process.returncode,
                            'error_report': json.loads(error_path.read_text()) if error_path.exists() and error_path.stat().st_size <= 8192 else None}}
                    raw['harness_request'] = request_value
                    raw['harness_returncode'] = process.returncode
                    if process.returncode == 0:
                        budget.complete(load)
                        for ticket in tickets:
                            budget.complete(ticket)
                finally:
                    if process.poll() is None:
                        process.kill(); process.wait(timeout=5)
        raw_ref = evidence.put({'kind': 'separate_moss_raw_batch', 'freeze_sha256': digest(frozen), 'raw': raw,
                                'preparations': preparations, 'model_calls': budget.records,
                                'actual_token_count': None, 'maximum_tokens_per_support': 512})
        groups = raw.get('supports', [])
        group_by_id = {g.get('support_id'): g for g in groups}
        if len(group_by_id) != len(groups) or any(sid not in {j['support_id'] for j in jobs} for sid in group_by_id):
            stages.put('moss:batch', {'status': 'failed_unexpected_supports', 'evidence_ref': raw_ref})
            raise ValueError('Unexpected/duplicate MOSS result support IDs; complete raw batch retained')
        for prepared in preparations:
            candidate = {'regions': prepared['regions'], 'tracks': [], 'metadata': {
                'trigger': 'native_overlap', 'profile_update_eligible': False,
                'source_continuity': 'not_established', 'actual_token_count': None}}
            for support in prepared['supports']:
                group = group_by_id.get(support['shared_support_id'])
                try:
                    if not group or group.get('status') != 'ok':
                        raise ValueError('Candidate failed, missing or unparseable; raw diagnostics retained')
                    tracks = parse_support(support, group.get('phrases'), int(config.get('moss_td_capacity', 8)), group.get('metadata'))
                    candidate['tracks'].extend(tracks)
                    for region in support['regions']:
                        region.update(status='unverified_hypothesis', reason_code='support_not_model_timing')
                except (ValueError, KeyError, TypeError) as exc:
                    for region in support['regions']:
                        region.update(status='unresolved', reason_code='candidate_schema_failure', failure_reason=str(exc))
            validate_candidates(candidate, 11.23)
            from .fragment_model_score import candidate_score
            fixture = next(f for f in manifest['fixtures'] if f['id'] == FIXTURES['B'])
            score = candidate_score(fixture['reference']['turns'], candidate['tracks'], fixture['reference']['repeat_text_cases'])
            stages.put(prepared['arm']+':moss', {'status': prepared['status'], 'evidence_ref': raw_ref,
                'projection': candidate, 'source_aware_cer': score, 'gold_denominator_retained_on_failure': True,
                'core_evidence_ref': prepared['core_evidence_ref']})
        files.verify(full=True)
        result = {'stages': stages.rows(), 'model_calls': budget.records, 'used': budget.used}
        atomic_json(output/'moss-stage.json', result, guard)
        return result
