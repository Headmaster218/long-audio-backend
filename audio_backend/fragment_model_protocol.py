"""Frozen offline experiment definition. This module never admits inference."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import wave

from .fragment_runtime_contracts import RATE, bounded_json, digest

FIXTURE_SHA256 = 'b1440539eadc526966440b14b1dee74d548fd35b4b359f6e4d271a9ab6afe702'
RUNTIME_COMMIT = '39f34e494952595c3882ca0b74a6c6a0bfcee9ab'
MATCHER_COMMIT = 'febde6fa9bba49d433bf1a238f717909001f3403'
MATCHER_EVIDENCE = '8dd40c04c17d8ea29e54fee81502d9fef9ff3011'
MATCHER_POLICY = 'exact-pcm16-v2q1p1'
ARMS = ('A_whole', 'A_split', 'A_duplicate_8000', 'A_duplicate_32000',
        'B_whole', 'B_split', 'F_whole', 'F_split')
FIXTURES = {'A': 'A_en_midword', 'B': 'B_zh_turn_repeat_overlap', 'F': 'F_event_interval'}
FULL_LIMITS = {
    'analyzer': {'calls': 12, 'frames': 4679360},
    'native_reset': {'calls': 8, 'frames': 0},
    'native_push': {'calls': 8, 'frames': 4423360},
    'native_finish': {'calls': 8, 'frames': 0},
    'native_turns': {'calls': 16, 'frames': 0},
    'sensevoice': {'calls': 384, 'frames': 720*RATE},
    'embedding': {'calls': 96, 'frames': 1920*RATE},
    'zipformer': {'calls': 108, 'frames': 540*RATE},
    'moss_support': {'calls': 2, 'frames': 359360},
    'moss_load': {'calls': 1, 'frames': 0},
}


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()



def imported_receipt(expected):
    """Actual imported Python/native-extension paths, checked against frozen bytes."""
    modules = {}
    prefixes = {'audio_backend', 'numpy', 'sherpa_onnx', 'soundfile', '_soundfile_data',
                'opencc', 'psutil', 'cffi', '_cffi_backend', 'transcribe_cpp'}
    for name, module in list(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if name.split('.')[0] not in prefixes or not path:
            continue
        resolved = str(Path(path).resolve())
        if resolved not in expected:
            raise ValueError(f'Unfrozen actual imported module: {name}: {resolved}')
        entry = expected[resolved]
        if sha256(resolved) != entry['sha256']:
            raise ValueError(f'Actual imported wrapper/extension changed: {name}')
        modules[name] = {'path': resolved, **entry}
    interpreter = str(Path(sys.executable).resolve())
    if interpreter not in expected or sha256(interpreter) != expected[interpreter]['sha256']:
        raise ValueError('Actual interpreter differs from frozen bytes')
    return {'interpreter': {'path': interpreter, **expected[interpreter]}, 'modules': modules}

def stat_identity(path):
    stat = Path(path).stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def read_manifest(path):
    if sha256(path) != FIXTURE_SHA256:
        raise ValueError('Frozen fixture manifest hash differs')
    return json.loads(Path(path).read_text())


def proposal():
    return {'status': 'implementation_only_not_execution_approval', 'fixture_sha256': FIXTURE_SHA256,
            'runtime_commit': RUNTIME_COMMIT, 'matcher_commit': MATCHER_COMMIT,
            'matcher_evidence_commit': MATCHER_EVIDENCE, 'matcher_policy': MATCHER_POLICY,
            'arms': list(ARMS), 'maximum_call_budgets': FULL_LIMITS,
            'core_frames': 480000, 'context_frames': 32000, 'epoch_frames': 4800000,
            'native_probes_included': False, 'automatic_retries': False,
            'laughter_quality': 'unsupported_label', 'ctc_timing': 'starts_native_ends_estimated',
            'candidate_stage': 'separate_B_native_overlap_only',
            'resource_limits': {'aggregate_project_rss_bytes': 6*1024**3,
                'experiment_model_rss_bytes': int(3.5*1024**3), 'implementation_rss_bytes': 512*1024**2, 'initial_base_stage_rss_bytes': 1024**3,
                'artifacts_bytes': 64*1024**2, 'transient_pcm_bytes': 8*1024**2,
                'host_available_bytes': 2*1024**3, 'disk_free_after_writes': int(5.5*1024**3)}}


class FrozenFiles:
    """Full hash at freeze/admission, immutable metadata on every call.

    Stat caching is a local change detector, not an adversarial filesystem
    integrity boundary. A final full rehash is mandatory before the report.
    """
    def __init__(self, entries):
        self.entries = entries
        self.identities = {}

    def verify(self, full=False):
        for name, entry in self.entries.items():
            path = Path(name)
            before = stat_identity(path)
            if path.resolve() != path:
                raise ValueError(f'Frozen canonical path substituted: {name}')
            if full or name not in self.identities:
                if before[2] != entry['bytes'] or sha256(path) != entry['sha256']:
                    raise ValueError(f'Frozen file hash differs: {name}')
                if stat_identity(path) != before:
                    raise ValueError(f'Frozen file changed during hashing: {name}')
                self.identities[name] = before
            elif before != self.identities[name]:
                raise ValueError(f'Frozen consumed file changed: {name}')
        # Catch wrong-checkout imports instead of trusting only environment vars.
        for name, module in list(sys.modules.items()):
            path = getattr(module, '__file__', None)
            if (name.split('.')[0] in {'audio_backend', 'numpy', 'sherpa_onnx', 'soundfile',
                    '_soundfile_data', 'opencc', 'psutil', 'cffi', '_cffi_backend', 'transcribe_cpp'}) and path:
                actual = str(Path(path).resolve())
                if actual not in self.entries:
                    raise ValueError(f'Unfrozen/wrong-checkout imported module: {name}: {actual}')
        return {'interpreter': str(Path(sys.executable).resolve()), 'modules': {name: str(Path(module.__file__).resolve())
            for name, module in list(sys.modules.items()) if name.startswith('audio_backend') and getattr(module, '__file__', None)}}


def freeze(source_root, manifest_path, config, *, extra_assets=(), moss_config=None):
    """Read/hash only. No WAV extraction, model construction or inference."""
    source_root = Path(source_root).resolve()
    manifest = read_manifest(manifest_path)
    checkout = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout, text=True).strip()
    if subprocess.check_output(['git', 'diff', '--name-only', 'HEAD', '--', 'audio_backend',
                                'scripts/benchmarks/compare_fragment_models.py'], cwd=checkout, text=True).strip():
        raise ValueError('Freeze requires committed implementation source')
    paths = {Path(sys.executable).resolve(), Path(manifest_path).resolve()}
    paths.update(checkout.glob('audio_backend/**/*.py'))
    paths.add(checkout/'scripts/benchmarks/compare_fragment_models.py')
    model = Path(config['model_dir']).resolve()
    paths.update(model/name for name in ('sensevoice/model.int8.onnx', 'sensevoice/tokens.txt', 'speaker.onnx',
        'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx',
        'sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv'))
    paths.add(Path(config['native_diarization_model']).resolve())
    library = Path(config['native_diarization_library']).resolve()
    paths.add(library)
    paths.update(p for p in library.parent.iterdir() if p.is_file())
    versions = {}
    from packaging.requirements import Requirement
    pending = ['sherpa-onnx', 'numpy', 'soundfile', 'opencc-python-reimplemented', 'psutil']
    while pending:
        package = pending.pop()
        if package in versions:
            continue
        dist = importlib.metadata.distribution(package)
        versions[package] = dist.version
        paths.update(Path(dist.locate_file(item)).resolve() for item in dist.files or ()
                     if not str(item).endswith(('.pyc', '.pyo')) and Path(dist.locate_file(item)).is_file())
        for declaration in dist.requires or ():
            requirement = Requirement(declaration)
            if requirement.marker is None or requirement.marker.evaluate({'extra': ''}):
                pending.append(requirement.name)
    pyvenv = Path(sys.prefix)/'pyvenv.cfg'
    if pyvenv.is_file():
        paths.add(pyvenv)
    if moss_config is not None:
        from .moss_td_cpp_assets import consumed_assets
        paths.update(Path(item['path']).resolve() for item in consumed_assets(moss_config))
    paths.update(Path(value).resolve() for value in extra_assets)
    bindings = []
    for asset in [*manifest['assets'], manifest['optional_existing_alias']]:
        path = (source_root/asset['path']).resolve()
        if sha256(path) != asset['sha256']:
            raise ValueError(f'Original/alias source changed: {asset["path"]}')
        paths.add(path)
        bindings.append({'path': str(path), 'sha256': asset['sha256']})
    entries = {}
    for path in sorted(paths):
        path = path.resolve()
        before = stat_identity(path)
        value = {'sha256': sha256(path), 'bytes': before[2]}
        if stat_identity(path) != before:
            raise ValueError(f'File changed while freezing {path}')
        entries[str(path)] = value
    result = {**proposal(), 'implementation_commit': commit, 'source_root': str(source_root),
              'manifest_path': str(Path(manifest_path).resolve()), 'config': config,
              'dependency_versions': versions, 'files': entries, 'source_bindings': bindings}
    if moss_config is not None:
        result['moss_config'] = moss_config
    bounded_json(result, 2*1024**2)
    return result


def validate_approval(approval, frozen, stage):
    """Explicit parent-supplied execution receipt, bound to exact content/caps.

    This is an operational admission file, not a cryptographic authorization
    system. Its presence never substitutes for the parent's actual permission.
    """
    if (approval.get('freeze_sha256') != digest(frozen) or approval.get('stage') != stage
        or not isinstance(approval.get('authorization_reference'), str)
        or not approval['authorization_reference'].strip()):
        raise ValueError('Missing matching explicit staged execution approval')
    arms = approval.get('arms')
    if not isinstance(arms, list) or not arms or len(arms) != len(set(arms)) or any(a not in ARMS for a in arms):
        raise ValueError('Approval must select unique frozen matrix arms')
    if stage == 'moss' and any(a not in ('B_whole', 'B_split') for a in arms):
        raise ValueError('MOSS stage supports only selected B arms')
    limits = approval.get('budgets')
    if not isinstance(limits, dict) or set(limits) != set(FULL_LIMITS):
        raise ValueError('Approval must state every call/audio budget, including zero/not-run categories')
    for kind, maximum in FULL_LIMITS.items():
        value = limits[kind]
        if (not isinstance(value, dict) or set(value) != {'calls', 'frames'}
            or any(type(value[k]) is not int or not 0 <= value[k] <= maximum[k] for k in maximum)):
            raise ValueError(f'Approval exceeds frozen call/audio maxima: {kind}')
    return arms, limits


@contextmanager
def materialize_arm(arm, manifest, source_root, temp_root, guard):
    """Later-authorized exact PCM16 transport, anonymous inputs to blind matcher."""
    import tempfile
    from .fragment_core import BoundedPCM16Reader, build_pcm16_plan, inspect_pcm16_source
    from .fragment_matcher import Continuity
    from .fragment_runtime import LogicalPCMReader
    from .fragment_runtime_contracts import RuntimePolicy, next_window
    fixture = next(f for f in manifest['fixtures'] if f['id'] == FIXTURES[arm[0]])
    asset = next(a for a in manifest['assets'] if a['id'] == fixture['asset_id'])
    original = Path(source_root)/asset['path']
    if sha256(original) != asset['sha256']:
        raise ValueError('Original source hash changed before transport')
    transport, offset = original, 0
    if asset['format'] == 'FLAC':
        alias = manifest['optional_existing_alias']
        transport, offset = Path(source_root)/alias['path'], fixture['source_frames'][0]
        if sha256(transport) != alias['sha256']:
            raise ValueError('Existing B WAV alias hash changed')
    source = inspect_pcm16_source('original', transport)
    reader = BoundedPCM16Reader((source,), {'original': transport})
    lo, hi = fixture['source_frames']
    reference = reader.read_source('original', lo-offset, hi-offset)
    if hashlib.sha256(reference).hexdigest() != fixture['canonical_pcm_sha256']:
        raise ValueError('Bounded PCM differs from frozen canonical fixture')
    if '_duplicate_' in arm:
        duplicate = int(arm.rsplit('_', 1)[1])
        variant = next(v for v in manifest['duplicate_head_variants'] if v['duplicate_frames'] == duplicate)
        views = variant['input_views']
    else:
        duplicate = 0
        views = [fixture['whole_view']] if arm.endswith('_whole') else fixture['split_views']
    with tempfile.TemporaryDirectory(prefix='arm-', dir=temp_root) as directory:
        paths, sources, bindings = {}, [], []
        for index, view in enumerate(views):
            a, b = view['source_frames']
            pcm = reader.read_source('original', a-offset, b-offset)
            guard.check(len(pcm)+44)
            path, fid = Path(directory)/f'{index}.wav', f'part-{index}'
            with wave.open(str(path), 'wb') as out:
                out.setnchannels(1); out.setsampwidth(2); out.setframerate(RATE); out.writeframes(pcm)
            descriptor = inspect_pcm16_source(fid, path)
            paths[fid] = path
            sources.append(descriptor)
            bindings.append({'original_file_sha256': asset['sha256'], 'original_source_frames': [a, b],
                             'transport_file_sha256': descriptor.sha256, 'transport': asdict(descriptor),
                             'pcm_sha256': hashlib.sha256(pcm).hexdigest(), 'fragment_id': fid})
        declarations = [Continuity('continuous', same_capture=True, replay_possible=False)]*(len(sources)-1)
        input_reader = BoundedPCM16Reader(sources, paths)
        plan = build_pcm16_plan(sources, declarations, input_reader, final=True, policy=MATCHER_POLICY)
        from .fragment_runtime_contracts import UnsupportedDiscontinuity
        try:
            logical = LogicalPCMReader(plan, paths)
        except UnsupportedDiscontinuity:
            yield {'arm': arm, 'status': 'unresolved_normalization_skip_models',
                   'trims': [seam.evidence.trim_frames for seam in plan.seams], 'plan': plan,
                   'source_bindings': bindings}
            return
        corrected = logical.read(0, plan.end_frame) if plan.end_frame <= 60*RATE else None
        trims = [seam.evidence.trim_frames for seam in plan.seams]
        accepted = (plan.end_frame == hi-lo and corrected is not None and corrected.pcm == reference
                    and (not duplicate or trims[0] == duplicate))
        if not accepted:
            yield {'arm': arm, 'status': 'unresolved_normalization_skip_models', 'trims': trims, 'plan': plan,
                   'source_bindings': bindings}
            return
        reads, cursor = [], 0
        while True:
            window = next_window(plan, cursor, RuntimePolicy())
            if window is None:
                break
            pcm = logical.read(window.read_start, window.read_end)
            expected = hashlib.sha256(reference[2*window.read_start:2*window.read_end]).hexdigest()
            if pcm.pcm_sha256 != expected:
                raise ValueError('Logical model read differs from whole reference')
            reads.append({'window': asdict(window), 'pcm_sha256': expected})
            cursor = window.core_end
        yield {'arm': arm, 'status': 'admitted_exact_pcm', 'plan': plan, 'paths': paths, 'trims': trims,
               'source_bindings': bindings, 'read_bindings': reads, 'whole_pcm_sha256': fixture['canonical_pcm_sha256']}
