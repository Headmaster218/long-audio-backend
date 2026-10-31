"""Original analyzer vectors, privately and immutably bound to exact support.

Old observations are deliberately not a migration source. Canonical JSON keeps
all original Python float values; matching normalization is separately versioned.
"""
import json
import math
import uuid
from . import speaker_support as support

NORMALIZATION = 'python-f64-l2-fsum-v1'
MAX_VECTOR_BYTES = 64 * 1024


def normalized_vector(raw):
    if not isinstance(raw, list) or not 8 <= len(raw) <= 2048 or any(not support.finite(v) for v in raw):
        raise ValueError('Invalid original vector')
    scale = max(abs(v) for v in raw)
    if scale <= 1e-30:
        raise ValueError('Zero vector')
    norm = math.sqrt(math.fsum((v / scale) ** 2 for v in raw))
    normalized = [(v / scale) / norm for v in raw]
    return normalized, support.digest(support.canonical(normalized))


def query_proof(raw, metadata, prepared):
    """Validate transient proof without any dependency on artifact admission."""
    from .embedding_space import validate_space
    if not isinstance(prepared, dict) or prepared.get('status') not in ('available', 'window_pcm_quota', 'support_pcm_quota'):
        raise ValueError('Exact query support proof unavailable')
    manifest = prepared.get('manifest')
    support.validate_manifest(manifest)
    encoded = support.canonical(raw)
    if len(encoded) > MAX_VECTOR_BYTES or support.digest(encoded) != manifest['embedding_sha256']:
        raise ValueError('Original vector fingerprint mismatch')
    descriptor = metadata.get('speaker_embedding_space')
    space = validate_space(descriptor)
    if metadata.get('speaker_embedding_space_id') != space:
        raise ValueError('Embedding space fingerprint mismatch')
    # The space validator is authoritative for model/runtime proof. These are
    # separate cross-bindings to the actual captured support and analyzer output.
    model = descriptor['model']
    if (model['sha256'] != manifest['encoder']['model_sha256'] or
            model['dimension'] != manifest['encoder']['dimension'] or
            model['version'] != manifest['encoder']['version'] or len(raw) != model['dimension']):
        raise ValueError('Embedding space/model mismatch')
    vector, normalized_hash = normalized_vector(raw)
    selected = manifest['support']
    pcm=prepared.get('query_pcm',prepared.get('pcm'))
    if (not support.finite_pcm(pcm) or len(pcm)!=selected['pooled_sample_count']*4
            or support.digest(pcm)!=selected['pooled_pcm_sha256']):
        raise ValueError('Exact transient query PCM integrity mismatch')
    key = evidence_key(manifest,space)
    return {'raw': encoded, 'vector': vector, 'vector_sha256': support.digest(encoded),
            'normalized_sha256': normalized_hash, 'space_id': space, 'space': descriptor,
            'evidence_key': key, 'seconds': selected['pooled_sample_count'] / support.RATE,
            'manifest_sha256': support.digest(support.canonical(manifest))}


def evidence_key(manifest,space):
    selected=manifest['support']
    return support.digest(support.canonical({'pcm_sha256':selected['pooled_pcm_sha256'],
        'encoding':selected['hash_encoding'],'sample_count':selected['pooled_sample_count'],'space_id':space}))


def schema_statements(*, policy='strict-primary-manual-v1'):
    """Exact original schema unless the explicit fresh live payload policy is selected."""
    statements = [
        'CREATE TABLE IF NOT EXISTS speaker_support_vectors (\n        id TEXT PRIMARY KEY, support_id TEXT NOT NULL UNIQUE REFERENCES speaker_support(id) ON DELETE CASCADE,\n        identity_group TEXT NOT NULL, generation TEXT NOT NULL, raw_vector BLOB NOT NULL,\n        vector_sha256 TEXT NOT NULL, dimension INTEGER NOT NULL, space_id TEXT NOT NULL,\n        space TEXT NOT NULL, normalization TEXT NOT NULL, normalized_sha256 TEXT NOT NULL,\n        evidence_key TEXT NOT NULL, retained_bytes INTEGER NOT NULL CHECK(retained_bytes>0 AND retained_bytes<=65536), CHECK(length(raw_vector)=retained_bytes))',
        'CREATE INDEX IF NOT EXISTS support_vectors_group ON speaker_support_vectors(identity_group)',
        "CREATE TRIGGER IF NOT EXISTS support_vectors_immutable BEFORE UPDATE ON speaker_support_vectors\n        BEGIN SELECT RAISE(ABORT,'Original support vectors are immutable'); END",
    ]
    if policy == 'strict-primary-payload-v2':
        statements[0] = statements[0].replace('raw_vector BLOB NOT NULL','raw_vector BLOB').replace(
            'CHECK(length(raw_vector)=retained_bytes)',
            'CHECK(raw_vector IS NULL OR length(raw_vector)=retained_bytes)')
        statements[-1] = """CREATE TRIGGER IF NOT EXISTS support_vectors_hot_insert
        BEFORE INSERT ON speaker_support_vectors WHEN NEW.raw_vector IS NULL
        BEGIN SELECT RAISE(ABORT,'Fresh original vector requires hot bytes'); END"""
    return tuple(statements)


def init_schema(db, *, policy='strict-primary-manual-v1'):
    for statement in schema_statements(policy=policy):
        db.execute(statement)


def retained_bytes(db, *, live=False):
    if live:
        from .strict_payload_v2 import hot_bytes
        return hot_bytes(db)
    return db.execute('''SELECT (SELECT COALESCE(SUM(pcm_bytes),0) FROM speaker_support) +
        (SELECT COALESCE(SUM(retained_bytes),0) FROM speaker_support_vectors)''').fetchone()[0]


def commit(store, db, support_id, group, proof):
    if not support_id or proof is None:
        return None
    row = db.execute("SELECT generation,manifest_sha256,status FROM speaker_support WHERE id=?", (support_id,)).fetchone()
    if not row or row['status'] != 'available' or row['manifest_sha256'] != proof['manifest_sha256']:
        return None
    raw = proof['raw']
    limit = store.settings.speaker_support_total_pcm_bytes
    if support._payload_live(store):
        from .strict_payload_v2 import hot_limit
        limit = hot_limit(store)
    if retained_bytes(db, live=support._payload_live(store)) + len(raw) > limit:
        if support._payload_live(store):
            from .strict_payload_v2 import PayloadCapacityError
            raise PayloadCapacityError('total_vector_quota')
        return None
    from .resources import check_resources
    try:
        check_resources(store.settings, len(raw) * 3)
    except RuntimeError as exc:
        if support._payload_live(store):
            from .strict_payload_v2 import PayloadCapacityError
            raise PayloadCapacityError('vector_resource_unavailable') from exc
        return None
    evidence_id = uuid.uuid4().hex
    db.execute('INSERT INTO speaker_support_vectors VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (evidence_id, support_id, group, row['generation'], raw, proof['vector_sha256'], len(proof['vector']),
         proof['space_id'], support.canonical(proof['space']).decode(), NORMALIZATION,
         proof['normalized_sha256'], proof['evidence_key'], len(raw)))
    return evidence_id


def read(db, support_id, *, payload=None):
    size=db.execute('SELECT length(raw_vector),length(space) FROM speaker_support_vectors WHERE support_id=?',(support_id,)).fetchone()
    if size and ((size[0] is not None and not 0<size[0]<=MAX_VECTOR_BYTES) or not 0<size[1]<=16384):
        raise ValueError('Original vector proof exceeds read limits')
    row = db.execute('SELECT * FROM speaker_support_vectors WHERE support_id=?', (support_id,)).fetchone()
    if row is None:
        raise ValueError('vector_proof_unavailable: old support cannot be enrolled from legacy observations')
    from .embedding_space import validate_space
    if row['raw_vector'] is None:
        from .strict_payload_v2 import PayloadIntegrityError
        if payload is None:
            raise PayloadIntegrityError('cold_vector_context')
        row = dict(row)
        row['raw_vector'] = payload.resolve('vector',row['id'],row['retained_bytes'],row['vector_sha256'])
    elif payload is not None:
        payload.hot('vector',row['id'],row['retained_bytes'],row['vector_sha256'])
    raw = bytes(row['raw_vector'])
    if not 0 < len(raw) <= MAX_VECTOR_BYTES or len(raw) != row['retained_bytes']:
        raise ValueError('Original vector size mismatch')
    values = json.loads(raw)
    normalized, hashed = normalized_vector(values)
    descriptor = json.loads(row['space'])
    owner=db.execute('''SELECT s.identity_group,s.generation,s.manifest,s.manifest_sha256 FROM speaker_support s
        JOIN sessions sess ON sess.id=s.session_id AND sess.deleted=0 AND sess.identity_group=s.identity_group
        JOIN chunks c ON c.id=s.chunk_id AND c.session_id=s.session_id WHERE s.id=?''',(support_id,)).fetchone()
    if not owner:raise ValueError('Original vector source missing')
    manifest=json.loads(owner['manifest'])
    support.validate_manifest(manifest)
    if (row['identity_group']!=owner['identity_group'] or row['generation']!=owner['generation']
            or support.digest(support.canonical(manifest))!=owner['manifest_sha256']
            or row['vector_sha256']!=manifest['embedding_sha256']
            or row['evidence_key']!=evidence_key(manifest,row['space_id'])
            or descriptor['model']['sha256']!=manifest['encoder']['model_sha256']
            or descriptor['model']['version']!=manifest['encoder']['version']
            or descriptor['model']['dimension']!=manifest['encoder']['dimension']
            or row['dimension']!=manifest['encoder']['dimension']):
        raise ValueError('Original vector source binding mismatch')
    if (support.canonical(values) != raw or support.digest(raw) != row['vector_sha256']
            or row['normalization'] != NORMALIZATION or hashed != row['normalized_sha256']
            or len(values) != row['dimension'] or validate_space(descriptor) != row['space_id']):
        raise ValueError('Original vector proof integrity mismatch')
    return dict(row), normalized
