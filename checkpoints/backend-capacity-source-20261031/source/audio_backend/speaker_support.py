"""Private, bounded, immutable acoustic review evidence. Never identity training.

Legacy/offline PCM artifacts live in SQLite. The explicit fresh live payload
policy may relocate exact bodies behind committed archive locators while keeping
logical evidence and erasure intact. The quota counts hot bytes, not SQLite/WAL
filesystem high-water marks. No re-decoding or silent replacement is permitted.
"""
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import math
import re
from pathlib import Path
import struct
import uuid
import wave

from .speaker_evidence import project_review_support, POOLED_HASH_ENCODING

RATE = 16000
MAX_FRAMES = RATE * 60 + 160
MAX_PCM_BYTES = RATE * 20 * 4
MAX_MANIFEST_BYTES = 128 * 1024
MAX_PARTS = 128
MAX_WINDOW_SUPPORTS = 64
DECODE_CONTRACT = 'ffmpeg input seek seconds; map 0:a:0; mono 16000Hz pcm_s16le; concatenate actual decoded frames; v1'
FLOAT_CONTRACT = 'mono PCM16 little-endian to IEEE754 float32 via signed-int16 / 32768; v1'
UNVERIFIED = 'predicted_from_diarization_unverified'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def is_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def integer(value, minimum, maximum):
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def producer_provenance(samples, turns, model_sha256, threads):
    """Record the actual frontend/selection implementation and analyzed samples.

    Called by the analyzer that used these samples, not inferred from whatever
    configuration happens to be current when a historical review is requested.
    """
    import numpy as np
    from . import speaker_evidence
    runtimes = {}
    for package in ('numpy', 'soundfile', 'sherpa-onnx'):
        runtimes[package] = importlib.metadata.version(package)
    frontend = {'schema_version': 1, 'input_contract': FLOAT_CONTRACT,
                'analyzer_sha256': digest((Path(__file__).parent / 'inference' / 'analyzer.py').read_bytes()),
                'runtimes': runtimes, 'provider': 'cpu', 'threads': threads,
                'normalization': 'extractor float32 vector / numpy.linalg.norm; cosine L2',
                'model_sha256': model_sha256}
    selection = {'schema_version': 1,
                 'implementation_sha256': digest(Path(speaker_evidence.__file__).read_bytes()),
                 'turns_sha256': digest(canonical(turns)),
                 'policy': 'other-speaker overlap subtraction; inward rounding; first 20 seconds'}
    return {'frontend': frontend, 'frontend_sha256': digest(canonical(frontend)),
            'selection': selection, 'selection_sha256': digest(canonical(selection)),
            'analyzer_input_pcm_sha256': digest(np.ascontiguousarray(samples, dtype='<f4').tobytes())}


def provenance_details(producer, metadata):
    """Strict safe, self-describing historical runtime/selection snapshots."""
    frontend_keys=('schema_version','input_contract','analyzer_sha256','runtimes','provider','threads','normalization','model_sha256')
    selection_keys=('schema_version','implementation_sha256','turns_sha256','policy')
    frontend={key:producer['frontend'][key] for key in frontend_keys}
    selection={key:producer['selection'][key] for key in selection_keys}
    runtime=frontend['runtimes']
    def token(value,maximum=100):
        return isinstance(value,str) and 0<len(value)<=maximum and re.fullmatch(r'[A-Za-z0-9_.+ -]+',value) is not None
    if (not integer(frontend['schema_version'],1,1) or frontend['provider']!='cpu'
            or not is_hash(frontend['analyzer_sha256']) or not is_hash(frontend['model_sha256'])
            or not is_hash(selection['implementation_sha256']) or not is_hash(selection['turns_sha256'])
            or not integer(frontend['threads'],1,4) or not isinstance(runtime,dict)
            or set(runtime)!={'numpy','soundfile','sherpa-onnx'} or any(not token(v) for v in runtime.values())
            or frontend['normalization']!='extractor float32 vector / numpy.linalg.norm; cosine L2'
            or not integer(selection['schema_version'],1,1)
            or selection['policy']!='other-speaker overlap subtraction; inward rounding; first 20 seconds'
            or digest(canonical(frontend))!=producer['frontend_sha256']
            or digest(canonical(selection))!=producer['selection_sha256']):
        raise ValueError('Unsupported frontend or selection provenance')
    backend=metadata.get('diarization_backend')
    model=metadata.get('diarization_model_sha256')
    if backend not in ('pyannote','nemotron3-native') or not is_hash(model):
        raise ValueError('Missing actual diarization model provenance')
    diarization={'backend':backend,'model_sha256':model}
    for key in ('diarization_runtime','diarization_runtime_version'):
        value=metadata.get(key)
        if not token(value):raise ValueError('Missing diarization runtime provenance')
        diarization[key.removeprefix('diarization_')]=value
    if backend=='pyannote':
        config=metadata.get('effective_config',{})
        threshold=config.get('diarization_threshold')
        threads=config.get('cpu_threads')
        if not finite(threshold) or not 0<=threshold<=1 or not integer(threads,1,4):
            raise ValueError('Missing diarization frontend configuration')
        diarization['config']={'clustering_threshold':threshold,'threads':threads,'provider':'cpu',
                               'min_duration_on':.3,'min_duration_off':.3}
    else:
        for key in ('diarization_library_sha256','diarization_fingerprint'):
            value=metadata.get(key)
            if not is_hash(value):raise ValueError('Missing native runtime fingerprint')
            diarization[key.removeprefix('diarization_')]=value
        files=metadata.get('diarization_runtime_files_sha256')
        if not isinstance(files,dict) or len(files)>128 or any(not token(k,160) or not is_hash(v) for k,v in files.items()):
            raise ValueError('Invalid native runtime inventory')
        diarization['runtime_files_sha256']=dict(files)
        for key,allowed in (
                ('diarization_geometry',('spkcache','fifo','chunk','update_period','left_context','right_context','frame_seconds')),
                ('diarization_postprocessing',('onset','offset','pad_onset','pad_offset','min_duration_on','min_duration_off'))):
            raw=metadata.get(key,{})
            if not isinstance(raw,dict) or any(not finite(raw.get(k)) for k in allowed):
                raise ValueError('Missing native geometry/postprocessing')
            diarization[key.removeprefix('diarization_')]={k:raw[k] for k in allowed}
        state=metadata.get('native_diarization',{})
        if (not isinstance(state,dict) or not isinstance(state.get('epoch_id'),str)
                or not re.fullmatch('[a-f0-9]{32}',state['epoch_id'])
                or not integer(state.get('epoch_index'),0,10**12) or not isinstance(state.get('finished'),bool)):
            raise ValueError('Missing native epoch scope')
        epoch={k:state[k] for k in ('epoch_id','epoch_index','finished')}
        for key in ('owned_start','owned_end','replay_start','input_until','processed_until','stable_until'):
            if not finite(state.get(key)):raise ValueError('Invalid native epoch coordinates')
            epoch[key]=state[key]
        diarization['epoch']=epoch
    if len(canonical(diarization))>32768:raise ValueError('Oversized diarization provenance')
    return frontend,selection,diarization


class SourceFingerprints:
    """A bounded stat-keyed cache; one cold streaming read per unchanged file.

    dev/ino/size/mtime/ctime are checked before and after hashing and decoding.
    This is local-file change detection, not adversarial filesystem attestation.
    """
    def __init__(self, capacity=128):
        self.capacity = capacity
        self.cache = OrderedDict()

    @staticmethod
    def signature(path):
        stat = Path(path).stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def get(self, path, stop=None):
        before = self.signature(path)
        key = (str(Path(path).resolve()), before)
        if key in self.cache:
            value = self.cache.pop(key)
            self.cache[key] = value
            return value, before
        hashed = hashlib.sha256()
        with Path(path).open('rb') as source:
            while block := source.read(1024 * 1024):
                if stop and stop():
                    raise InterruptedError()
                hashed.update(block)
        if self.signature(path) != before:
            raise ValueError('Source changed during hashing')
        value = hashed.hexdigest()
        self.cache[key] = value
        while len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
        return value, before


def schema_statements(*, policy='strict-primary-manual-v1'):
    """Exact original schema unless the explicit fresh live payload policy is selected."""
    statements = [
        'CREATE TABLE IF NOT EXISTS speaker_identity_generations (\n        identity_group TEXT PRIMARY KEY, generation TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS speaker_support (\n        id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),\n        chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,\n        identity_group TEXT NOT NULL, generation TEXT NOT NULL,\n        window_start REAL NOT NULL, local_speaker_id TEXT NOT NULL,\n        observation_id TEXT, manifest TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,\n        status TEXT NOT NULL, pcm BLOB,\n        pcm_bytes INTEGER NOT NULL CHECK(pcm_bytes>=0 AND pcm_bytes<=1280000),\n        CHECK((pcm IS NULL AND pcm_bytes=0) OR (pcm IS NOT NULL AND length(pcm)=pcm_bytes)),\n        UNIQUE(chunk_id,window_start,local_speaker_id))',
        'CREATE INDEX IF NOT EXISTS support_session ON speaker_support(session_id,id)',
        'CREATE INDEX IF NOT EXISTS support_group ON speaker_support(identity_group)',
        'CREATE TABLE IF NOT EXISTS speaker_support_sources (\n        support_id TEXT NOT NULL REFERENCES speaker_support(id) ON DELETE CASCADE,\n        chunk_id TEXT NOT NULL REFERENCES chunks(id), PRIMARY KEY(support_id,chunk_id))',
        'CREATE INDEX IF NOT EXISTS support_source_chunk ON speaker_support_sources(chunk_id)',
        'CREATE TRIGGER IF NOT EXISTS speaker_support_source_erasure\n        BEFORE DELETE ON chunks BEGIN DELETE FROM speaker_support\n        WHERE id IN (SELECT support_id FROM speaker_support_sources WHERE chunk_id=OLD.id); END',
        "CREATE TRIGGER IF NOT EXISTS speaker_support_immutable\n        BEFORE UPDATE ON speaker_support BEGIN SELECT RAISE(ABORT,'Acoustic support is immutable'); END",
    ]
    if policy == 'strict-primary-payload-v2':
        statements[1] = statements[1].replace(
            'CHECK((pcm IS NULL AND pcm_bytes=0) OR (pcm IS NOT NULL AND length(pcm)=pcm_bytes))',
            'CHECK(pcm IS NULL OR length(pcm)=pcm_bytes)')
        statements[-1] = """CREATE TRIGGER IF NOT EXISTS speaker_support_hot_insert
        BEFORE INSERT ON speaker_support WHEN NEW.pcm IS NULL AND NEW.pcm_bytes>0
        BEGIN SELECT RAISE(ABORT,'Fresh acoustic support requires hot PCM'); END"""
    return tuple(statements)


def init_schema(db, *, policy='strict-primary-manual-v1'):
    for statement in schema_statements(policy=policy):
        db.execute(statement)


def _payload_live(store):
    return (getattr(store, '_strict_enabled', False) is True
            and getattr(store, '_strict_policy', None) == 'strict-primary-payload-v2')


@contextmanager
def _payload_context(store, db, *, budget=None, check=None):
    """One explicit snapshot/cumulative resolver per outer API, never per object."""
    if not _payload_live(store):
        yield None
        return
    if not db.in_transaction:
        db.execute('BEGIN')
    with store.payload_resolver(db, budget=budget, check=check) as payload:
        yield payload


def generation(db, group):
    db.execute('INSERT OR IGNORE INTO speaker_identity_generations VALUES(?,?)', (group, uuid.uuid4().hex))
    return db.execute('SELECT generation FROM speaker_identity_generations WHERE identity_group=?', (group,)).fetchone()[0]


def capture_generation(store, chunk):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        group = db.execute('SELECT identity_group FROM sessions WHERE id=? AND deleted=0', (chunk['session_id'],)).fetchone()
        return generation(db, group[0]) if group else None


def decoded_floats(wav):
    import numpy as np
    with wave.open(str(wav), 'rb') as source:
        frames = source.getnframes()
        if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, RATE, 'NONE'):
            raise ValueError('Unsupported decoded PCM format')
        if not integer(frames, 1, MAX_FRAMES):
            raise ValueError('Unbounded or empty decoded PCM')
        raw = source.readframes(frames)
    if len(raw) != frames * 2:
        raise ValueError('Truncated decoded PCM')
    return np.frombuffer(raw, dtype='<i2').astype('<f4') / np.float32(32768)


def prepare_window(result, wav, lineage, expected_generation, *, core_start, analyzer_start, settings):
    """Return private artifacts separately; never insert PCM into public metadata.

    This optional path fails closed without changing normal analysis or matching.
    Only complete selected support can produce an artifact, never a prefix.
    """
    metadata = result.get('metadata', {})
    evidence_map = metadata.get('speaker_embedding_evidence', {})
    if not isinstance(evidence_map, dict):
        return {}
    candidates = list(evidence_map)[:MAX_WINDOW_SUPPORTS]
    prepared = {}
    samples = None
    total_bytes = 0
    for local_id in candidates:
        value = {'generation': expected_generation, 'status': 'invalid_support', 'manifest': {}, 'pcm': None}
        prepared[str(local_id)] = value
        try:
            evidence = evidence_map[local_id]
            if not isinstance(evidence, dict) or not isinstance(evidence.get('pooled_support'), dict):
                value['status'] = 'missing_support'
                continue
            if evidence.get('overlap_excluded') is not True or evidence.get('source') != 'overlap_subtracted_neural_turns':
                value['status'] = 'unsupported_selection'
                continue
            support = project_review_support(evidence['pooled_support'], core_start_seconds=core_start,
                                             analyzer_chunk_start_seconds=analyzer_start)
            support = {key:support[key] for key in ('schema_version','time_axis','sample_rate','analyzer_frame_count',
                'intervals_samples','interval_count','complete','pooled_sample_count','pooled_pcm_sha256','hash_encoding',
                'purity_status','human_verified','coordinate_projection')}
            if not support['complete']:
                value['status'] = 'incomplete_intervals'
                continue
            if support['sample_rate'] != RATE or not integer(support['pooled_sample_count'], 1, MAX_PCM_BYTES // 4):
                value['status'] = 'support_size_limit'
                continue
            if not isinstance(lineage, dict) or lineage.get('status') != 'complete':
                value['status'] = lineage.get('status', 'missing_lineage') if isinstance(lineage, dict) else 'missing_lineage'
                continue
            producer = metadata.get('speaker_support_producer')
            if not isinstance(producer, dict):
                value['status'] = 'missing_frontend'
                continue
            model = metadata.get('speaker_embedding_sha256')
            version = metadata.get('speaker_embedding_version')
            dimension = metadata.get('speaker_embedding_dimension')
            if not is_hash(model) or not isinstance(version, str) or not 1 <= len(version) <= 200 or not integer(dimension, 8, 2048):
                value['status'] = 'missing_model_binding'
                continue
            embeddings=[item.get('speaker_embedding') for item in result.get('speaker_turns',[])+result.get('segments',[])
                        if str(item.get('speaker_local_id'))==str(local_id) and item.get('speaker_embedding') is not None]
            if not embeddings or any(not isinstance(vector,list) or len(vector)!=dimension
                    or any(not finite(v) for v in vector) for vector in embeddings):
                value['status']='embedding_dimension_mismatch'
                continue
            if any(vector != embeddings[0] for vector in embeddings):
                value['status']='conflicting_embedding_binding'
                continue
            frontend, selection = producer.get('frontend'), producer.get('selection')
            if (not isinstance(frontend, dict) or not isinstance(selection, dict)
                    or frontend.get('model_sha256') != model or frontend.get('input_contract') != FLOAT_CONTRACT
                    or producer.get('frontend_sha256') != digest(canonical(frontend))
                    or producer.get('selection_sha256') != digest(canonical(selection))):
                value['status'] = 'frontend_mismatch'
                continue
            # Store only fingerprint allowlists, never arbitrary producer strings.
            if not is_hash(frontend.get('analyzer_sha256')) or not is_hash(selection.get('implementation_sha256')) or not is_hash(selection.get('turns_sha256')):
                value['status'] = 'missing_frontend'
                continue
            frontend,selection,diarization=provenance_details(producer,metadata)
            turns=metadata.get('diarization_turns')
            if not isinstance(turns,list) or digest(canonical(turns))!=selection['turns_sha256']:
                value['status']='selection_mismatch'
                continue
            if samples is None:
                samples = decoded_floats(wav)
            if (support['analyzer_frame_count'] != len(samples) or lineage.get('analyzer_frame_count') != len(samples)
                    or producer.get('analyzer_input_pcm_sha256') != digest(samples.astype('<f4').tobytes())):
                value['status'] = 'analyzer_input_mismatch'
                continue
            parts = lineage.get('parts')
            cursor = 0
            if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS:
                value['status'] = 'source_parts_limit'
                continue
            safe_parts = []
            for part in parts:
                interval = part.get('analyzer_frames')
                if (not isinstance(interval, list) or len(interval) != 2
                        or not integer(interval[0], cursor, cursor) or not integer(interval[1], cursor, len(samples))
                        or not is_hash(part.get('source_sha256'))
                        or not isinstance(part.get('chunk_id'), str) or len(part['chunk_id']) != 32
                        or not finite(part.get('seek_seconds')) or part['seek_seconds'] < 0
                        or not finite(part.get('requested_seconds')) or not 0 < part['requested_seconds'] <= 60.01):
                    raise ValueError('Invalid decoded source lineage')
                cursor = interval[1]
                selected = []
                for lo, hi in support['intervals_samples']:
                    a, b = max(lo, interval[0]), min(hi, interval[1])
                    if a < b:
                        selected.append([a - interval[0], b - interval[0]])
                safe_parts.append({k: part[k] for k in ('chunk_id', 'source_sha256', 'seek_seconds', 'requested_seconds', 'analyzer_frames')})
                safe_parts[-1]['selected_decoded_frames'] = selected
            if cursor != len(samples) or not is_hash(lineage.get('decoder_sha256')):
                raise ValueError('Incomplete decoded source lineage')
            pcm = b''.join(samples[a:b].astype('<f4').tobytes() for a, b in support['intervals_samples'])
            if len(pcm) != support['pooled_sample_count'] * 4 or digest(pcm) != support['pooled_pcm_sha256']:
                value['status'] = 'pooled_pcm_mismatch'
                continue
            manifest = {'schema_version': 1, 'support': support,
                'encoder': {'model_sha256': model, 'version': version, 'dimension': dimension,
                            'metric': 'cosine_l2_normalized'},
                'embedding_sha256':digest(canonical(embeddings[0])),
                'frontend':frontend,'selection':selection,'diarization':diarization,
                'diarization_sha256':digest(canonical(diarization)),
                'frontend_sha256': producer['frontend_sha256'],
                'selection_sha256': producer['selection_sha256'],
                'selection_implementation_sha256': selection['implementation_sha256'],
                'diarization_turns_sha256': selection['turns_sha256'],
                'decoder_sha256': lineage['decoder_sha256'], 'decoder_contract_sha256': digest(DECODE_CONTRACT.encode()),
                'float_conversion_sha256': digest(FLOAT_CONTRACT.encode()),
                'analyzer_input_pcm_sha256': producer['analyzer_input_pcm_sha256'],
                'source_parts': safe_parts,
                'source_frame_semantics': 'actual per-part decoded output frames; seeks are source-time requests, not original encoded-file sample alignment'}
            if len(canonical(manifest)) > MAX_MANIFEST_BYTES:
                value['status'] = 'manifest_size_limit'
                continue
            value['manifest'] = manifest
            value['raw_vector'] = list(embeddings[0])
            # Transient proof remains available even when artifact admission is
            # disabled/full. This bytes object is never placed in metadata.
            value['query_pcm'] = pcm
            total_bytes += len(pcm)
            if total_bytes > settings.speaker_support_window_pcm_bytes:
                value['status'] = 'window_pcm_quota'
                continue
            if len(pcm) > settings.speaker_support_max_pcm_bytes:
                value['status'] = 'support_pcm_quota'
                continue
            value.update(status='available', pcm=pcm)
        except (ValueError, TypeError, KeyError, OSError, RuntimeError, OverflowError):
            value['status'] = 'invalid_support'
    return prepared


def validate_manifest(manifest):
    """Fail closed at persistence AND read boundaries, including private extras."""
    expected={'schema_version','support','encoder','embedding_sha256','frontend','selection','diarization',
        'diarization_sha256','frontend_sha256','selection_sha256','selection_implementation_sha256',
        'diarization_turns_sha256','decoder_sha256','decoder_contract_sha256','float_conversion_sha256',
        'analyzer_input_pcm_sha256','source_parts','source_frame_semantics'}
    if not isinstance(manifest,dict) or set(manifest)!=expected or not integer(manifest['schema_version'],1,1):
        raise ValueError('Invalid support manifest schema')
    if len(canonical(manifest))>MAX_MANIFEST_BYTES:raise ValueError('Manifest size limit')
    hashes=expected-{'schema_version','support','encoder','frontend','selection','diarization','source_parts','source_frame_semantics'}
    if any(not is_hash(manifest[key]) for key in hashes):raise ValueError('Invalid manifest fingerprint')
    selected=manifest['support']
    if not isinstance(selected,dict) or set(selected)!={'schema_version','time_axis','sample_rate','analyzer_frame_count',
            'intervals_samples','interval_count','complete','pooled_sample_count','pooled_pcm_sha256','hash_encoding',
            'purity_status','human_verified','coordinate_projection'}:
        raise ValueError('Invalid selected support schema')
    projection=selected['coordinate_projection']
    projected=project_review_support(selected,core_start_seconds=projection['core_start_in_analyzer_seconds'],
                                    analyzer_chunk_start_seconds=projection['analyzer_start_in_chunk_seconds'])
    if (not integer(selected['schema_version'],1,1) or canonical(projected)!=canonical(selected)
            or selected['complete'] is not True or selected['sample_rate']!=RATE
            or not integer(selected['pooled_sample_count'],1,MAX_PCM_BYTES//4)
            or not integer(selected['analyzer_frame_count'],1,MAX_FRAMES)):
        raise ValueError('Incomplete or inconsistent selected support')
    encoder=manifest['encoder']
    if (not isinstance(encoder,dict) or set(encoder)!={'model_sha256','version','dimension','metric'}
            or not is_hash(encoder['model_sha256']) or not integer(encoder['dimension'],8,2048)
            or not isinstance(encoder['version'],str) or not 1<=len(encoder['version'])<=200
            or not re.fullmatch(r'[A-Za-z0-9_.+ -]+',encoder['version']) or encoder['metric']!='cosine_l2_normalized'):
        raise ValueError('Invalid encoder binding')
    diar=manifest['diarization']
    metadata={f'diarization_{key}':value for key,value in diar.items() if key not in ('config','epoch')}
    if 'config' in diar:
        config=diar['config']
        metadata['effective_config']={'diarization_threshold':config['clustering_threshold'],'cpu_threads':config['threads']}
    if 'epoch' in diar:metadata['native_diarization']=diar['epoch']
    producer={key:manifest[key] for key in ('frontend','selection','frontend_sha256','selection_sha256')}
    frontend,selection,diarization=provenance_details(producer,metadata)
    if (canonical(frontend)!=canonical(manifest['frontend']) or canonical(selection)!=canonical(manifest['selection'])
            or canonical(diarization)!=canonical(diar) or digest(canonical(diar))!=manifest['diarization_sha256']
            or frontend['model_sha256']!=encoder['model_sha256']
            or selection['implementation_sha256']!=manifest['selection_implementation_sha256']
            or selection['turns_sha256']!=manifest['diarization_turns_sha256']
            or manifest['decoder_contract_sha256']!=digest(DECODE_CONTRACT.encode())
            or manifest['float_conversion_sha256']!=digest(FLOAT_CONTRACT.encode())
            or frontend['input_contract']!=FLOAT_CONTRACT):
        raise ValueError('Inconsistent manifest provenance')
    parts=manifest['source_parts'];cursor=0
    if not isinstance(parts,list) or not 1<=len(parts)<=MAX_PARTS:raise ValueError('Source part limit')
    for part in parts:
        if not isinstance(part,dict) or set(part)!={'chunk_id','source_sha256','seek_seconds','requested_seconds','analyzer_frames','selected_decoded_frames'}:
            raise ValueError('Invalid source part schema')
        interval=part['analyzer_frames']
        if (not isinstance(interval,list) or len(interval)!=2 or not integer(interval[0],cursor,cursor)
                or not integer(interval[1],cursor,selected['analyzer_frame_count'])
                or not isinstance(part['chunk_id'],str) or not re.fullmatch('[a-f0-9]{32}',part['chunk_id'])
                or not is_hash(part['source_sha256']) or not finite(part['seek_seconds']) or part['seek_seconds']<0
                or not finite(part['requested_seconds']) or not 0<part['requested_seconds']<=60.01):
            raise ValueError('Invalid source frame lineage')
        expected_selected=[]
        for lo,hi in selected['intervals_samples']:
            a,b=max(lo,interval[0]),min(hi,interval[1])
            if a<b:expected_selected.append([a-interval[0],b-interval[0]])
        if canonical(part['selected_decoded_frames'])!=canonical(expected_selected):raise ValueError('Incorrect source selection')
        cursor=interval[1]
    if cursor!=selected['analyzer_frame_count']:raise ValueError('Incomplete source frames')
    if manifest['source_frame_semantics']!='actual per-part decoded output frames; seeks are source-time requests, not original encoded-file sample alignment':
        raise ValueError('Invalid source frame semantics')


def finite_pcm(pcm):
    # The exponent bits reject NaN/Inf without importing an inference dependency.
    return isinstance(pcm,bytes) and len(pcm)%4==0 and all(
        bits & 0x7f800000 != 0x7f800000 for (bits,) in struct.iter_unpack('<I',pcm))


def commit_support(store, db, chunk, offset, group, local_id, observation_id, prepared):
    """Independent lifetime from the legacy observation cache; no auto-eviction."""
    if not store.settings.speaker_support_enabled:
        return None,'capture_not_enabled'
    # Retained metadata survives PCM archival. Store freezes this finite policy
    # before initialization; changing the caller's Settings cannot grow it mid-run.
    record_limit = getattr(store, '_speaker_support_max_records', store.settings.speaker_support_max_records)
    if db.execute('SELECT COUNT(*) FROM speaker_support').fetchone()[0] >= record_limit:
        if _payload_live(store):
            from .strict_payload_v2 import PayloadCapacityError
            raise PayloadCapacityError('support_row_quota')
        return None, 'support_row_quota'
    value = prepared if isinstance(prepared, dict) else {}
    current_generation = generation(db, group)
    status = value.get('status', 'missing_lineage')
    if not isinstance(status,str) or not re.fullmatch('[a-z_]{1,80}',status):status='invalid_support'
    manifest = value.get('manifest', {})
    pcm = value.get('pcm')
    if value.get('generation') != current_generation:
        status, manifest, pcm = 'missing_or_stale_generation', {}, None
    try:
        encoded = canonical(manifest)
        if manifest:validate_manifest(manifest)
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise ValueError('Manifest limit')
        if status == 'available':
            support = manifest['support']
            projection=support['coordinate_projection']
            project_review_support(support,core_start_seconds=projection['core_start_in_analyzer_seconds'],
                                   analyzer_chunk_start_seconds=projection['analyzer_start_in_chunk_seconds'])
            if support['complete'] is not True or support['sample_rate']!=RATE:
                raise ValueError('Incomplete or unsupported support')
            if (not isinstance(pcm, bytes) or not 0 < len(pcm) <= min(MAX_PCM_BYTES, store.settings.speaker_support_max_pcm_bytes)
                    or len(pcm) != support['pooled_sample_count'] * 4 or digest(pcm) != support['pooled_pcm_sha256']
                    or not finite_pcm(pcm)):
                raise ValueError('Invalid PCM artifact')
            for part in manifest['source_parts']:
                source = db.execute('SELECT session_id,digest FROM chunks WHERE id=?', (part['chunk_id'],)).fetchone()
                if not source or source['session_id'] != chunk['session_id'] or source['digest'] != part['source_sha256']:
                    status, pcm = 'source_mismatch', None
                    break
            from .support_vectors import retained_bytes
            used = retained_bytes(db, live=_payload_live(store))
            limit = store.settings.speaker_support_total_pcm_bytes
            if _payload_live(store):
                from .strict_payload_v2 import hot_limit
                limit = hot_limit(store)
            if pcm and used + len(pcm) > limit:
                if _payload_live(store):
                    from .strict_payload_v2 import PayloadCapacityError
                    raise PayloadCapacityError('total_pcm_quota')
                status, pcm = 'total_pcm_quota', None
            if pcm:
                from .resources import check_resources
                try:
                    # SQLite main/WAL/journal can temporarily duplicate a payload.
                    check_resources(store.settings, 3 * len(pcm) + len(encoded) * 3)
                except RuntimeError as exc:
                    if _payload_live(store):
                        from .strict_payload_v2 import PayloadCapacityError
                        raise PayloadCapacityError('support_resource_unavailable') from exc
                    status, pcm = 'resource_unavailable', None
        else:
            pcm = None
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        status, manifest, pcm = 'invalid_support', {}, None
        encoded = canonical(manifest)
    support_id = uuid.uuid4().hex
    db.execute('''INSERT INTO speaker_support VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (support_id, chunk['session_id'], chunk['id'], group, current_generation, offset, str(local_id),
         observation_id, encoded.decode(), digest(encoded), status, pcm, len(pcm) if pcm else 0))
    for part in manifest.get('source_parts', []):
        # Even metadata-only records must be revoked if a contributing source is deleted.
        if db.execute('SELECT 1 FROM chunks WHERE id=? AND session_id=?', (part['chunk_id'], chunk['session_id'])).fetchone():
            db.execute('INSERT OR IGNORE INTO speaker_support_sources VALUES(?,?)', (support_id, part['chunk_id']))
    return support_id, status


def _row(db, sid, support_id, with_pcm=False, *, payload=None):
    fields = f's.id,s.generation,substr(s.manifest,1,{MAX_MANIFEST_BYTES+1}) AS manifest,s.manifest_sha256,s.status,s.pcm_bytes,s.window_start,s.local_speaker_id,length(s.pcm) AS actual_pcm_bytes'
    if with_pcm:fields += f',substr(s.pcm,1,{MAX_PCM_BYTES+1}) AS pcm'
    row = db.execute(f'''SELECT {fields} FROM speaker_support s INDEXED BY support_session
        JOIN sessions sess ON sess.id=s.session_id AND sess.deleted=0 AND sess.identity_group=s.identity_group
        JOIN speaker_identity_generations g ON g.identity_group=s.identity_group AND g.generation=s.generation
        WHERE s.session_id=? AND s.id=?''', (sid, support_id)).fetchone()
    if not row:
        raise KeyError('Acoustic support is unavailable in this session')
    if row['pcm_bytes'] > 0 and (payload is not None or row['actual_pcm_bytes'] is None):
        # Bare NULL is not a legacy unavailable row. A declared retained body
        # needs exactly one placement, under this same DB snapshot.
        from .strict_payload_v2 import PayloadIntegrityError
        try:
            expected = json.loads(row['manifest'])['support']['pooled_pcm_sha256']
            if not is_hash(expected):
                raise ValueError('PCM digest')
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            raise PayloadIntegrityError('payload_pcm_manifest') from exc
        if row['actual_pcm_bytes'] is not None:
            payload.hot('pcm',row['id'],row['pcm_bytes'],expected)
        else:
            if payload is None or not payload.available('pcm',row['id'],row['pcm_bytes'],expected):
                raise PayloadIntegrityError('cold_pcm_placement')
            row = dict(row)
            if with_pcm:
                row['pcm'] = payload.resolve('pcm',row['id'],row['pcm_bytes'],expected)
                row['actual_pcm_bytes'] = len(row['pcm'])
            else:
                row['actual_pcm_bytes'] = row['pcm_bytes']
    return row


def metadata(store, sid, support_id=None, *, segment_id=None, turn_id=None):
    store.session(sid)
    with store.db() as db, _payload_context(store, db) as payload:
        from . import verified_identity, support_vectors
        verified=verified_identity.is_verified(db,sid)
        def unavailable_for(reason,support_id=None):
            result=unavailable(reason,support_id)
            if verified:
                result.update(identity_learning_available=True,verified_review_eligible=False,verified_review_reason=reason)
            return result
        if segment_id is not None or turn_id is not None:
            table, row_id = ('segments', segment_id) if segment_id is not None else ('speaker_turns', turn_id)
            ref = db.execute(f'''SELECT r.speaker_support_id,r.speaker_support_status FROM {table} r
                JOIN chunks c ON c.id=r.chunk_id WHERE c.session_id=? AND r.id=?''', (sid, row_id)).fetchone()
            if not ref:
                raise KeyError('Source result not found in session')
            support_id = ref['speaker_support_id']
            if not support_id:
                return unavailable_for(ref['speaker_support_status'] or 'legacy_support_not_recorded')
        try:
            row = _row(db, sid, support_id, payload=payload)
        except KeyError:
            if segment_id is not None or turn_id is not None:
                return unavailable_for('support_revoked_or_removed',support_id)
            raise
        manifest = json.loads(row['manifest'])
        try:
            if manifest:validate_manifest(manifest)
            if digest(canonical(manifest)) != row['manifest_sha256']:raise ValueError('Manifest integrity')
        except (ValueError,TypeError,KeyError,AttributeError):
            return unavailable_for('manifest_integrity_mismatch',support_id)
        result = unavailable_for(row['status'], support_id)
        result.update(identity_generation=row['generation'], manifest_sha256=row['manifest_sha256'],
                      window_start=row['window_start'], local_speaker_id=row['local_speaker_id'],
                      manifest=manifest, support_complete=bool(manifest.get('support', {}).get('complete')))
        result['audio_available'] = (row['status'] == 'available' and 0 < row['pcm_bytes'] <= MAX_PCM_BYTES
                                     and row['actual_pcm_bytes']==row['pcm_bytes'])
        result['playback_integrity']='full_pcm_sha256_checked_before_audio_response'
        result['review_eligible'] = result['audio_available'] and result['support_complete']
        if result['audio_available']:
            result['audio_url'] = f'/sessions/{sid}/speaker-support/{support_id}/audio'
        from . import verified_identity, support_vectors
        if verified_identity.is_verified(db,sid):
            result['identity_learning_available']=True
            result['verified_review_eligible']=False
            result['verified_review_reason']='vector_proof_unavailable'
            try:
                vector,_=support_vectors.read(db,support_id,payload=payload)
                result.update(vector_id=vector['id'],vector_sha256=vector['vector_sha256'],space_id=vector['space_id'])
                result['verified_review_eligible']=result['review_eligible']
                result['verified_review_reason']='available' if result['review_eligible'] else row['status']
            except (ValueError,TypeError,KeyError):
                pass
        return result


def unavailable(reason, support_id=None):
    return {'support_id': support_id, 'audio_available': False, 'support_complete': False,
            'review_eligible': False, 'reason': reason, 'human_verified': False,
            'purity_status': UNVERIFIED, 'identity_learning_available': False,
            'source_bytes_semantics':'original captured PCM; source hashes were checked at capture, current source files are not revalidated for review',
            'warning': 'Review covers ALL pooled fragments, including context outside this line or turn. Byte integrity does not establish one speaker or human verification.'}


def validate_pcm_evidence(row, pcm):
    """Shared read-side trust gate for both playback and review admission."""
    try:
        manifest=json.loads(row['manifest'])
        validate_manifest(manifest)
        selected=manifest['support']
        if (row['status']!='available' or digest(canonical(manifest))!=row['manifest_sha256']
                or not isinstance(pcm,bytes) or not 0<len(pcm)<=MAX_PCM_BYTES
                or len(pcm)!=row['pcm_bytes'] or len(pcm)!=selected['pooled_sample_count']*4
                or selected['sample_rate']!=RATE or selected['complete'] is not True
                or selected['hash_encoding']!=POOLED_HASH_ENCODING
                or digest(pcm)!=selected['pooled_pcm_sha256'] or not finite_pcm(pcm)):
            raise ValueError('Exact acoustic support integrity check failed')
        return manifest
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        raise ValueError('Exact acoustic support integrity check failed') from exc


def audio(store, sid, support_id):
    from .resources import check_resources
    check_resources(store.settings)
    with store.db() as db, _payload_context(store, db) as payload:
        # Check stored length before requesting a BLOB into Python memory.
        row = _row(db, sid, support_id, payload=payload)
        if (row['status'] != 'available' or not 0 < row['pcm_bytes'] <= MAX_PCM_BYTES
                or row['actual_pcm_bytes'] != row['pcm_bytes']):
            raise ValueError('Exact full acoustic support is unavailable')
        full = _row(db, sid, support_id, with_pcm=True, payload=payload)
        pcm = full['pcm']
        validate_pcm_evidence(row,pcm)
    # IEEE float WAVE (format 3) preserves every f32 bit. No PCM16 requantization.
    fmt = struct.pack('<HHIIHH', 3, 1, RATE, RATE * 4, 4, 32)
    fact = struct.pack('<I', len(pcm) // 4)
    body = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt + b'fact' + struct.pack('<I', 4) + fact
    body += b'data' + struct.pack('<I', len(pcm)) + pcm
    return b'RIFF' + struct.pack('<I', len(body)) + body
