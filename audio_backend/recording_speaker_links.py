"""Pure bounded, provisional recording-local links. No inference, I/O or writes.

The caller must validate live CellLease/recording membership before capture and
revalidate authority before publication. Content hashes are evidence, not that
live authority. Native labels and MOSS tags are local observations, never people.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
import re
import struct

from .embedding_space import validate_space
from .native_lexical_alternatives import SourceSupport
from .moss_handy_adapter import TIMESTAMP_POLICY, validate_timestamp_policy, validate_adjustment
from .native_owned_cells import (Unavailable, RATE, CELL_PCM_BYTES, MAX_TURNS,
    canonical, digest, integer, text, sha, validate_document, float32_digest,
    intersect_mapping)
from .native_owned_seam_cells import is_composite, validate_cell_pcm

SCHEMA = 'recording-speaker-links-v1'
CROP_FRAMES = 32000
EROSION_FRAMES = 1600
MAX_CROPS = 4
MAX_NODES = 32
MAX_PAIRS = 496
MAX_BATCHES = 32
MAX_BATCH_BYTES = 262144
MAX_VECTOR_BATCH_BYTES = 524288
DIMENSION = 512
TAU = .55
MARGIN = .10
POLICY = {'schema': SCHEMA, 'sample_rate': RATE, 'crop_frames': CROP_FRAMES,
    'erosion_frames': EROSION_FRAMES, 'max_crops': MAX_CROPS,
    'max_nodes': MAX_NODES, 'max_pairs': MAX_PAIRS, 'dimension': DIMENSION,
    'phrase_rounding': 'inward', 'competitor_rounding': 'outward',
    'selection': 'one-eligible-native-label-longest-clean-component-centered',
    'score': 'original-normalized-float32-fsum-float64-products',
    'linkage': 'complete-link-all-original-crosspairs', 'tau': TAU, 'margin': MARGIN,
    'decision': 'reciprocal-unique-best-both-margins-synchronous-disjoint',
    'cannot_link': 'rank-all-competitors-then-veto-winner-never-promote',
    'duplicates': 'same-source-crop-or-exact-pcm-is-one-independent-observation',
    'missing_runner_up': 'abstain', 'persistent_person_identity': False}
POLICY_SHA256 = sha(POLICY)
SCHEMA_V2 = 'recording-speaker-links-v2'
POLICY_V2 = {**deepcopy(POLICY), 'schema': SCHEMA_V2,
    'selection': 'typed-primary-first-alternation-four-node-admissions',
    'duplicates': 'typed-aliases-share-explicit-physical-probe-index',
    'native_attribution': 'all-original-and-independent-query-namespaces',
    'source_dependency': 'rank-all-then-veto-overlap-in-current-best-runner-up-trio'}
POLICY_SHA256_V2 = sha(POLICY_V2)
SUPPORTED_POLICIES = (POLICY_SHA256, POLICY_SHA256_V2)
MAX_OUTCOMES_V2 = 768
_PRIMARY_NODE_FIELDS = frozenset(('primary_source_ref', 'primary_packet_sha256',
    'primary_commit', 'primary_row_index', 'original_parent_ordinal',
    'original_unit_ordinals', 'original_turn_ordinals', 'capture_namespace'))
_TYPED_NODE_FIELDS = _PRIMARY_NODE_FIELDS | frozenset(('source_kind',
    'native_attributions', 'probe_index', 'float32le_sha256'))
_TYPED_BATCH_FIELDS = frozenset(('primary_packets', 'phrase_evidence'))


def expected_space(num_threads=2):
    """Audited descriptor only; imports/loads no embedding runtime or model."""
    from .embedding_space import _AUDITED_BINDINGS, SCHEMA_VERSION
    binding = '02763d940b59563aeeb7e70cc1b0b12542b6d09fa1f0c7be76f33faedb293be0'
    result = {'schema_version': SCHEMA_VERSION, 'status': 'proven',
        'binding_id': binding, **deepcopy(_AUDITED_BINDINGS[binding]),
        'extractor': {'provider': 'cpu', 'num_threads': num_threads}}
    validate_space(result)
    return result


def _hash_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _bounded(value, maximum=MAX_BATCH_BYTES):
    return canonical(value, maximum)


def _native_responses(doc, support, session_id, *, typed=False):
    """Never offset query.turns by support origin or by reset replay twice."""
    if 'query' in doc:
        responses = [(doc['query'], support.start_frame, support.end_frame)]
    elif 'native_witnesses' in doc:
        # v2 responses are separate observations. Crop coverage stays inside
        # the piece actually contributed by that response, never their union.
        seam = doc['geometry']['seam_frame']
        responses = [(doc['native_witnesses'][0], support.start_frame, seam),
                     (doc['native_witnesses'][1], seam, support.end_frame)]
    else:
        responses = [(doc, support.start_frame, support.end_frame)]
    answer = []
    for response, lo, hi in responses:
        native = response['native']
        domain = {'schema': 'recording-native-coordinate-scope-v1',
            'session_id': session_id, 'epoch_id': native['epoch_id'],
            'epoch_index': native['epoch_index'],
            'native_runtime_sha256': support.native_runtime_sha256}
        from .native_owned_core_seams import RESTART_SCHEMA
        if typed and 'capture_run' in response:
            domain.update(schema='recording-native-capture-query-scope-v2',
                          capture_run=response['capture_run'])
        elif doc.get('schema') == RESTART_SCHEMA:
            # Native epoch IDs can repeat after physical restart. This proof
            # explicitly owns its query labels in the freshly observed run.
            domain.update(schema='recording-native-restart-query-scope-v1',
                          capture_run=response['capture_run'])
        scope = sha(domain)
        answer.append((scope, response['turns'], lo, hi))
    return answer


def _witnesses(responses, proof_sha256):
    # One positive overlap proves each hard label-pair constraint. Keep the
    # canonical earliest witness, irrespective of whether either crop survives.
    found = {}
    for scope, turns, _, _ in responses:
        for i, one in enumerate(turns):
            for two in turns[i + 1:]:
                labels = tuple(sorted((one['label'], two['label'])))
                a = max(one['original'][0], two['original'][0])
                b = min(one['original'][1], two['original'][1])
                if labels[0] == labels[1] or a >= b:
                    continue
                key = (scope, labels)
                if key not in found or (a, b) < found[key]:
                    found[key] = (a, b)
    return [{'native_scope_id': scope, 'labels': list(labels),
             'overlap_frames': list(found[(scope, labels)]), 'proof_sha256': proof_sha256}
            for scope, labels in sorted(found)]


def _subtract(interval, blockers):
    a, b = interval
    result = []
    cursor = a
    for lo, hi in sorted(blockers, key=lambda interval: (interval[0], interval[1])):
        if hi <= cursor or lo >= b:
            continue
        if cursor < lo:
            result.append((cursor, min(lo, b)))
        cursor = max(cursor, hi)
        if cursor >= b:
            break
    if cursor < b:
        result.append((cursor, b))
    return result


def _phrases(projection, support):
    if type(projection) is not dict:
        raise Unavailable('speaker_link_projection_shape')
    _bounded(projection, 262144)
    if (projection.get('schema') != 'moss-handy-diagnostic-v1'
            or projection.get('support_id') != support.support_id
            or projection.get('schema_errors') != []
            or projection.get('truncated') is not False
            or projection.get('aborted') is not False
            or projection.get('speaker_identity_valid') is not False):
        raise Unavailable('speaker_link_projection_unusable')
    try:
        validate_timestamp_policy(projection)
    except ValueError as exc:
        raise Unavailable('speaker_link_projection_unusable') from exc
    alternative = projection.get('alternative')
    if (type(alternative) is not dict or alternative.get('provider') != 'moss'
            or alternative.get('outcome') != 'returned'
            or alternative.get('support_id') != support.support_id
            or alternative.get('pcm16le_sha256') != support.pcm16le_sha256
            or alternative.get('float32le_sha256') != support.float32le_sha256):
        raise Unavailable('speaker_link_projection_binding')
    digest(projection.get('raw_sha256'))
    for key in ('raw_sha256', 'raw_bytes'):
        if alternative.get(key) != projection.get(key):
            raise Unavailable('speaker_link_raw_binding')
    phrases = projection.get('diagnostic_phrases')
    if type(phrases) is not list or len(phrases) > 256:
        raise Unavailable('speaker_link_phrase_capacity')
    duration = (support.end_frame - support.start_frame) / RATE
    result = []
    tags = {}
    for index, phrase in enumerate(phrases):
        if type(phrase) is not dict:
            raise Unavailable('speaker_link_phrase_shape')
        a, b = phrase.get('start'), phrase.get('end')
        tag = phrase.get('speaker_local_id')
        if (type(a) not in (int, float) or type(b) not in (int, float)
                or not math.isfinite(a) or not math.isfinite(b)
                or not 0 <= a < b <= duration
                or type(tag) is not str or re.fullmatch(r'S[0-9]{1,15}', tag) is None
                or phrase.get('timing_scope') != 'support-relative-model-unverified'
                or phrase.get('speaker_identity_valid') is not False):
            raise Unavailable('speaker_link_phrase_geometry')
        slot = phrase.get('source_slot')
        integer(slot, 7)
        if tag in tags and tags[tag] != slot:
            raise Unavailable('speaker_link_phrase_slot')
        tags[tag] = slot
        if len(tags) > 8 or len(set(tags.values())) != len(tags):
            raise Unavailable('speaker_link_phrase_slot')
        try:
            adjusted = validate_adjustment(projection, phrase, support)
        except (ValueError, TypeError) as exc:
            raise Unavailable('speaker_link_phrase_geometry') from exc
        result.append({'phrase_index': index, 'moss_tag': tag,
            'probe_source_eligible': adjusted is None,
            'inward': (support.start_frame + (math.ceil(a * RATE) if adjusted is None else adjusted[0]),
                       support.start_frame + (math.floor(b * RATE) if adjusted is None else adjusted[2])),
            'outward': (support.start_frame + (math.floor(a * RATE) if adjusted is None else adjusted[1]),
                        support.start_frame + (math.ceil(b * RATE) if adjusted is None else adjusted[2]))})
    return result


def _batch_digest(batch):
    return _hash_bytes(_bounded({k: ([{n: v for n, v in node.items() if n != 'vector'}
                    for node in value] if k == 'nodes' else value)
             for k, value in batch.items() if k != 'batch_sha256'}))


def capture_candidates(support, pcm, proof, projection, job_id, result_sha256, space):
    """Return (serializable batch, owned PCM tuple aligned with batch.nodes).

    `result_sha256` binds the already serialized MOSS result, which can include
    its controller envelope. `projection` is that result's parsed diagnostic.
    No claim of ground-truth cleanliness or complete MOSS content is made.
    """
    if type(support) is not SourceSupport:
        raise Unavailable('speaker_link_support_required')
    text(job_id); digest(result_sha256)
    space_id = validate_space(space)
    if space['model']['dimension'] != DIMENSION:
        raise Unavailable('speaker_link_dimension')
    doc, witnessed_support, parts = validate_document(proof)
    if witnessed_support != support:
        raise Unavailable('speaker_link_support_binding')
    if (type(pcm) is not bytes or len(pcm) > CELL_PCM_BYTES
            or len(pcm) != 2 * (support.end_frame - support.start_frame)
            or _hash_bytes(pcm) != support.pcm16le_sha256
            or float32_digest(pcm) != support.float32le_sha256):
        raise Unavailable('speaker_link_pcm_binding')
    if is_composite(doc):
        validate_cell_pcm(doc, pcm)
    session_id = parts[0].session_id
    proof_sha256 = _hash_bytes(proof)
    binding = projection.get('cell_binding') if type(projection) is dict else None
    if binding is not None and (type(binding) is not dict
            or binding.get('origin') != 'durable_cell'
            or binding.get('proof_sha256') != proof_sha256
            or binding.get('support') != support.to_dict()
            or binding.get('cell_id') != sha({'policy': doc['policy'], 'support': support.to_dict()})):
        raise Unavailable('speaker_link_result_cell_binding')
    responses = _native_responses(doc, support, session_id)
    phrases = _phrases(projection, support)
    batch = {'schema': SCHEMA, 'policy_sha256': POLICY_SHA256, 'job_id': job_id,
        'result_sha256': result_sha256, 'support_id': support.support_id,
        'proof_sha256': proof_sha256, 'projection_sha256': _hash_bytes(_bounded(projection)),
        'space_id': space_id, 'session_id': session_id,
        'native_scope_id': responses[0][0], 'proof': doc, 'nodes': [], 'outcomes': [],
        'witnesses': _witnesses(responses, proof_sha256)}
    if 'timestamp_policy' in projection:
        # Input-policy attribution is included in the batch/projection hashes;
        # existing scoring policy and historical batches are not rewritten.
        batch['timestamp_policy'] = projection['timestamp_policy']
    crops = []
    for phrase in phrases:
        if not phrase['probe_source_eligible']:
            batch['outcomes'].append({'phrase_index': phrase['phrase_index'],
                'status': 'timestamp_adjusted_unverified'})
            continue
        eligible = {}
        for scope, turns, lo, hi in responses:
            for turn in turns:
                a = max(lo, phrase['inward'][0], turn['original'][0])
                b = min(hi, phrase['inward'][1], turn['original'][1])
                if a >= b:
                    continue
                blockers = [other['original'] for _, observations, _, _ in responses
                    for other in observations if other['label'] != turn['label']]
                blockers += [other['outward'] for other in phrases
                    if other['moss_tag'] != phrase['moss_tag'] or not other['probe_source_eligible']]
                for left, right in _subtract((a, b), blockers):
                    left += EROSION_FRAMES; right -= EROSION_FRAMES
                    if right - left >= CROP_FRAMES:
                        eligible.setdefault((scope, turn['label']), set()).add((left, right))
        outcome = {'phrase_index': phrase['phrase_index'], 'status': 'insufficient_clean_support'}
        if len(eligible) > 1:
            outcome['status'] = 'ambiguous_native_label'
        elif eligible:
            if len(crops) == MAX_CROPS:
                outcome['status'] = 'crop_capacity'
            else:
                (scope, label), intervals = next(iter(eligible.items()))
                a, b = min(intervals, key=lambda x: (-(x[1] - x[0]), x[0], x[1]))
                start = a + (b - a - CROP_FRAMES) // 2
                end = start + CROP_FRAMES
                crop = pcm[2 * (start - support.start_frame):2 * (end - support.start_frame)]
                if len(crop) != 2 * CROP_FRAMES:
                    raise Unavailable('speaker_link_exact_crop_required')
                source_spans = [asdict(p) for p in intersect_mapping(parts, start, end)]
                content = [{k: p[k] for k in ('source_sha256', 'source_frames',
                           'source_start', 'source_end')} for p in source_spans]
                node = {'job_id': job_id, 'result_sha256': result_sha256,
                    'phrase_index': phrase['phrase_index'], 'moss_tag': phrase['moss_tag'],
                    'native_label': label, 'native_scope_id': scope,
                    'start_frame': start, 'end_frame': end,
                    'pcm16le_sha256': _hash_bytes(crop), 'source_spans': source_spans,
                    'source_key': sha({'schema': 'exact-source-crop-v1', 'parts': content}),
                    'space_id': space_id, 'support_id': support.support_id,
                    'proof_sha256': proof_sha256, 'projection_sha256': batch['projection_sha256']}
                node['node_id'] = sha({'schema': SCHEMA, 'policy_sha256': POLICY_SHA256, **node})
                batch['nodes'].append(node); crops.append(crop)
                outcome.update(status='candidate', node_id=node['node_id'])
        batch['outcomes'].append(outcome)
    batch['batch_sha256'] = _batch_digest(batch)
    if len(_bounded(batch)) + sum(map(len, crops)) + 65536 > 524288:
        raise Unavailable('speaker_link_staging_capacity')
    return batch, tuple(crops)


def valid_vector(vector):
    """Preserve original normalized float32 components; never renormalize."""
    if type(vector) is not list or len(vector) != DIMENSION:
        raise Unavailable('speaker_link_vector_shape')
    for value in vector:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise Unavailable('speaker_link_vector_component')
        try:
            exact = struct.unpack('<f', struct.pack('<f', value))[0]
        except (OverflowError, struct.error) as exc:
            raise Unavailable('speaker_link_vector_component') from exc
        if exact != value:
            raise Unavailable('speaker_link_vector_not_original_float32')
    if abs(math.fsum(value * value for value in vector) - 1.) >= 1e-5:
        raise Unavailable('speaker_link_vector_norm')
    return tuple(vector)


def _combined_witnesses(*collections):
    unique = {canonical(w): w for values in collections for w in values}
    return [deepcopy(unique[key]) for key in sorted(unique)]


def _moss_selection(phrase, phrases, responses):
    """The v1 numeric crop rule, independently replayable without lexical text."""
    if not phrase['probe_source_eligible']:
        return 'timestamp_adjusted_unverified', None
    eligible = {}
    for scope, turns, lo, hi in responses:
        for turn in turns:
            a = max(lo, phrase['inward'][0], turn['original'][0])
            b = min(hi, phrase['inward'][1], turn['original'][1])
            if a >= b:
                continue
            blockers = [other['original'] for _, observations, _, _ in responses
                for other in observations if other['label'] != turn['label']]
            blockers += [other['outward'] for other in phrases
                if other['moss_tag'] != phrase['moss_tag'] or not other['probe_source_eligible']]
            for left, right in _subtract((a, b), blockers):
                left += EROSION_FRAMES; right -= EROSION_FRAMES
                if right - left >= CROP_FRAMES:
                    eligible.setdefault((scope, turn['label']), set()).add((left, right))
    if len(eligible) > 1:
        return 'ambiguous_native_label', None
    if not eligible:
        return 'insufficient_clean_support', None
    (scope, label), intervals = next(iter(eligible.items()))
    a, b = min(intervals, key=lambda x: (-(x[1] - x[0]), x[0], x[1]))
    start = a + (b - a - CROP_FRAMES) // 2
    return 'candidate', (scope, label, start, start + CROP_FRAMES)


def _owned_crop(pcm, support, start, end):
    """The single v2 PCM allocation edge, after metadata capacity admission."""
    crop = pcm[2 * (start - support.start_frame):2 * (end - support.start_frame)]
    if len(crop) != 2 * CROP_FRAMES:
        raise Unavailable('speaker_link_exact_crop_required')
    return crop


def capture_candidates_v2(support, pcm, proof, projection, job_id, result_sha256,
                          space, *, primary_packets=()):
    """Capture at most four typed aliases, sharing exactly duplicate PCM probes.

    The returned PCM tuple is indexed by node.probe_index, not node order.
    Native primary rows and MOSS phrases remain distinct bounded admissions.
    """
    from .recording_primary_probe import primary_metadata, primary_witnesses
    if type(support) is not SourceSupport:
        raise Unavailable('speaker_link_support_required')
    text(job_id); digest(result_sha256)
    space_id = validate_space(space)
    if space['model']['dimension'] != DIMENSION:
        raise Unavailable('speaker_link_dimension')
    doc, witnessed_support, parts = validate_document(proof)
    if witnessed_support != support:
        raise Unavailable('speaker_link_support_binding')
    if (type(pcm) is not bytes or len(pcm) > CELL_PCM_BYTES
            or len(pcm) != 2 * (support.end_frame - support.start_frame)
            or _hash_bytes(pcm) != support.pcm16le_sha256
            or float32_digest(pcm) != support.float32le_sha256):
        raise Unavailable('speaker_link_pcm_binding')
    if is_composite(doc):
        validate_cell_pcm(doc, pcm)
    session_id = parts[0].session_id
    proof_sha256 = _hash_bytes(proof)
    binding = projection.get('cell_binding') if type(projection) is dict else None
    if binding is not None and (type(binding) is not dict
            or binding.get('origin') != 'durable_cell'
            or binding.get('proof_sha256') != proof_sha256
            or binding.get('support') != support.to_dict()
            or binding.get('cell_id') != sha({'policy': doc['policy'], 'support': support.to_dict()})):
        raise Unavailable('speaker_link_result_cell_binding')
    responses = _native_responses(doc, support, session_id, typed=True)
    phrases = _phrases(projection, support)
    primary, primary_outcomes = primary_metadata(support, proof, primary_packets, space_id)
    # Validate packet witnesses even when there is no eligible primary row.
    primary_hard = primary_witnesses(proof, primary_packets)
    batch = {'schema': SCHEMA_V2, 'policy_sha256': POLICY_SHA256_V2,
        'job_id': job_id, 'result_sha256': result_sha256, 'support_id': support.support_id,
        'proof_sha256': proof_sha256, 'projection_sha256': _hash_bytes(_bounded(projection)),
        'space_id': space_id, 'session_id': session_id,
        'native_scope_id': responses[0][0], 'proof': doc, 'nodes': [], 'outcomes': [],
        'primary_packets': deepcopy(list(primary_packets)),
        'phrase_evidence': [{**p, 'inward': list(p['inward']), 'outward': list(p['outward'])}
                            for p in phrases],
        'witnesses': _combined_witnesses(_witnesses(responses, proof_sha256), primary_hard)}
    if 'timestamp_policy' in projection:
        batch['timestamp_policy'] = projection['timestamp_policy']
    moss, moss_outcomes = [], []
    for phrase in phrases:
        status, selected = _moss_selection(phrase, phrases, responses)
        outcome = {'phrase_index': phrase['phrase_index'], 'status': status}
        if selected:
            if len(moss) == MAX_CROPS:
                outcome['status'] = 'crop_capacity'
            else:
                scope, label, start, end = selected
                source_spans = [asdict(p) for p in intersect_mapping(parts, start, end)]
                content = [{k: p[k] for k in ('source_sha256', 'source_frames',
                            'source_start', 'source_end')} for p in source_spans]
                moss.append({'source_kind': 'moss', 'phrase_index': phrase['phrase_index'],
                    'moss_tag': phrase['moss_tag'], 'native_label': label,
                    'native_scope_id': scope, 'start_frame': start, 'end_frame': end,
                    'source_spans': source_spans,
                    'source_key': sha({'schema': 'exact-source-crop-v1', 'parts': content}),
                    'native_attributions': [{'native_scope_id': scope, 'native_label': label}]})
        moss_outcomes.append(outcome)
    candidates = []
    for index in range(max(len(primary), len(moss))):
        for kind in (primary, moss):
            if index < len(kind):
                candidates.append(kind[index])
    # Reserve the worst case before the first crop allocation. Exact duplicate
    # sharing can reduce this charge, never expand the four-admission ceiling.
    reserved = deepcopy(batch)
    bindings = {k: batch[k] for k in ('job_id', 'result_sha256', 'support_id',
                'proof_sha256', 'projection_sha256', 'space_id')}
    reserved['nodes'] = [{**deepcopy(n), **bindings, 'pcm16le_sha256': '0' * 64,
        'float32le_sha256': '0' * 64, 'node_id': '0' * 64, 'probe_index': index}
        for index, n in enumerate(candidates[:MAX_CROPS])]
    reserved['outcomes'] = [dict(o, node_id='0' * 64) if o['status'] == 'candidate'
                            else deepcopy(o) for o in moss_outcomes + list(primary_outcomes)]
    reserved['batch_sha256'] = '0' * 64
    if len(_bounded(reserved)) + len(reserved['nodes']) * 2 * CROP_FRAMES + 65536 > 524288:
        raise Unavailable('speaker_link_staging_capacity')
    del reserved
    physical = []
    for original in candidates[:MAX_CROPS]:
        node = deepcopy(original)
        for key in ('job_id', 'result_sha256', 'support_id', 'proof_sha256',
                    'projection_sha256', 'space_id'):
            node[key] = batch[key]
        # Allocation happens after the final four typed admissions. Metadata
        # from either candidate pool never owns an extra speculative PCM crop.
        source_aliases = [n for n in batch['nodes'] if n['source_key'] == node['source_key']]
        if source_aliases:
            alias = source_aliases[0]
            crop = physical[alias['probe_index']]
            observed = memoryview(pcm)[2 * (node['start_frame'] - support.start_frame):
                                       2 * (node['end_frame'] - support.start_frame)]
            if observed != crop:
                raise Unavailable('speaker_link_duplicate_pcm_disagreement')
            node.update(pcm16le_sha256=alias['pcm16le_sha256'],
                        float32le_sha256=alias['float32le_sha256'])
        else:
            crop = _owned_crop(pcm, support, node['start_frame'], node['end_frame'])
            node.update(pcm16le_sha256=_hash_bytes(crop), float32le_sha256=float32_digest(crop))
        matches = [n for n in batch['nodes'] if n['source_key'] == node['source_key']
                   or n['pcm16le_sha256'] == node['pcm16le_sha256']]
        if matches:
            indexes = {n['probe_index'] for n in matches}
            if len(indexes) != 1:
                raise Unavailable('speaker_link_probe_alias_conflict')
            node['probe_index'] = next(iter(indexes))
            if physical[node['probe_index']] != crop:
                raise Unavailable('speaker_link_duplicate_pcm_disagreement')
        else:
            node['probe_index'] = len(physical)
            physical.append(crop)
        node['node_id'] = sha({'schema': SCHEMA_V2, 'policy_sha256': POLICY_SHA256_V2, **node})
        batch['nodes'].append(node)
    for original in moss_outcomes + list(primary_outcomes):
        outcome = {k: deepcopy(v) for k, v in original.items() if k != 'node_id'}
        key = 'primary_source_ref' if 'primary_source_ref' in outcome else 'phrase_index'
        kind = 'native_primary' if key == 'primary_source_ref' else 'moss'
        matching = [n for n in batch['nodes'] if n['source_kind'] == kind
                    and n.get(key) == outcome[key]]
        if matching:
            if len(matching) != 1:
                raise Unavailable('speaker_link_duplicate_source_ref')
            outcome.update(status='candidate', node_id=matching[0]['node_id'])
        elif outcome.get('status') == 'candidate':
            outcome['status'] = 'crop_capacity'
        batch['outcomes'].append(outcome)
    batch['batch_sha256'] = _batch_digest(batch)
    if len(_bounded(batch)) + sum(map(len, physical)) + 65536 > 524288:
        raise Unavailable('speaker_link_staging_capacity')
    validate_batch(batch, require_vectors=False)
    return batch, tuple(physical)


def validate_batch(batch, *, require_vectors=True):
    """Validate trusted internal capture metadata after storage/IPC transport."""
    if type(batch) is dict and batch.get('schema') == SCHEMA_V2:
        return _validate_batch_v2(batch, require_vectors=require_vectors)
    if type(batch) is not dict or batch.get('schema') != SCHEMA or batch.get('policy_sha256') != POLICY_SHA256:
        raise Unavailable('speaker_link_batch_policy')
    if (_TYPED_BATCH_FIELDS | _TYPED_NODE_FIELDS).intersection(batch):
        raise Unavailable('speaker_link_mixed_policy_metadata')
    _bounded(batch, MAX_VECTOR_BATCH_BYTES)
    if 'timestamp_policy' in batch and batch['timestamp_policy'] != TIMESTAMP_POLICY:
        raise Unavailable('speaker_link_batch_policy')
    for key in ('result_sha256', 'proof_sha256', 'projection_sha256', 'space_id', 'support_id',
                'batch_sha256', 'native_scope_id'):
        digest(batch.get(key))
    text(batch.get('session_id')); text(batch.get('job_id'))
    if _batch_digest(batch) != batch['batch_sha256']:
        raise Unavailable('speaker_link_batch_binding')
    doc, support, parts = validate_document(canonical(batch['proof']))
    if (support.support_id != batch['support_id'] or _hash_bytes(canonical(doc)) != batch['proof_sha256']
            or parts[0].session_id != batch['session_id']):
        raise Unavailable('speaker_link_batch_proof')
    responses = _native_responses(doc, support, batch['session_id'])
    if (batch['native_scope_id'] != responses[0][0]
            or batch.get('witnesses') != _witnesses(responses, batch['proof_sha256'])):
        raise Unavailable('speaker_link_witness_binding')
    nodes, outcomes = batch.get('nodes'), batch.get('outcomes')
    if type(nodes) is not list or len(nodes) > MAX_CROPS or type(outcomes) is not list or len(outcomes) > 256:
        raise Unavailable('speaker_link_batch_capacity')
    indexes = set()
    for node in nodes:
        if type(node) is not dict:
            raise Unavailable('speaker_link_node_shape')
        if _TYPED_NODE_FIELDS.intersection(node):
            raise Unavailable('speaker_link_mixed_policy_metadata')
        integer(node.get('phrase_index'), 255)
        if node['phrase_index'] in indexes:
            raise Unavailable('speaker_link_duplicate_phrase')
        indexes.add(node['phrase_index'])
        for key in ('job_id', 'result_sha256', 'support_id', 'proof_sha256', 'projection_sha256', 'space_id'):
            if node.get(key) != batch[key]:
                raise Unavailable('speaker_link_node_binding')
        original = {k: value for k, value in node.items() if k not in ('node_id', 'vector')}
        if node.get('node_id') != sha({'schema': SCHEMA, 'policy_sha256': POLICY_SHA256, **original}):
            raise Unavailable('speaker_link_node_digest')
        integer(node.get('start_frame')); integer(node.get('end_frame'))
        if (node['end_frame'] - node['start_frame'] != CROP_FRAMES
                or not support.start_frame <= node['start_frame'] < node['end_frame'] <= support.end_frame):
            raise Unavailable('speaker_link_node_geometry')
        expected_parts = [asdict(p) for p in intersect_mapping(parts, node['start_frame'], node['end_frame'])]
        if node.get('source_spans') != expected_parts:
            raise Unavailable('speaker_link_node_sources')
        content = [{k: p[k] for k in ('source_sha256', 'source_frames', 'source_start', 'source_end')}
                   for p in expected_parts]
        if node.get('source_key') != sha({'schema': 'exact-source-crop-v1', 'parts': content}):
            raise Unavailable('speaker_link_source_key')
        digest(node.get('pcm16le_sha256'))
        if not any(scope == node.get('native_scope_id') and any(t['label'] == node.get('native_label')
                and max(lo, t['original'][0]) <= node['start_frame'] < node['end_frame'] <= min(hi, t['original'][1])
                for t in turns) for scope, turns, lo, hi in responses):
            raise Unavailable('speaker_link_native_binding')
        if require_vectors:
            valid_vector(node.get('vector'))
    for index, outcome in enumerate(outcomes):
        if type(outcome) is not dict or outcome.get('phrase_index') != index:
            raise Unavailable('speaker_link_outcome_shape')
        if (_TYPED_NODE_FIELDS | _TYPED_BATCH_FIELDS).intersection(outcome):
            raise Unavailable('speaker_link_mixed_policy_metadata')
        if outcome.get('status') == 'candidate':
            matching = [n for n in nodes if n['phrase_index'] == index]
            if len(matching) != 1 or matching[0]['node_id'] != outcome.get('node_id'):
                raise Unavailable('speaker_link_outcome_binding')
        elif outcome.get('status') == 'timestamp_adjusted_unverified':
            if batch.get('timestamp_policy') != TIMESTAMP_POLICY or index in indexes:
                raise Unavailable('speaker_link_outcome_binding')
        elif outcome.get('status') not in ('insufficient_clean_support', 'ambiguous_native_label', 'crop_capacity'):
            raise Unavailable('speaker_link_outcome_status')
        elif index in indexes:
            raise Unavailable('speaker_link_outcome_binding')
    if any(index >= len(outcomes) for index in indexes):
        raise Unavailable('speaker_link_missing_outcome')
    return batch


def _validate_phrase_evidence(batch, support):
    phrases = batch.get('phrase_evidence')
    if type(phrases) is not list or len(phrases) > 256:
        raise Unavailable('speaker_link_phrase_capacity')
    tags = set()
    for index, phrase in enumerate(phrases):
        if (type(phrase) is not dict
                or set(phrase) != {'phrase_index', 'moss_tag', 'probe_source_eligible', 'inward', 'outward'}
                or type(phrase.get('phrase_index')) is not int or phrase['phrase_index'] != index
                or type(phrase.get('probe_source_eligible')) is not bool
                or type(phrase.get('moss_tag')) is not str
                or re.fullmatch(r'S[0-9]{1,15}', phrase['moss_tag']) is None):
            raise Unavailable('speaker_link_phrase_evidence')
        tags.add(phrase['moss_tag'])
        if len(tags) > 8:
            raise Unavailable('speaker_link_phrase_evidence')
        for key in ('inward', 'outward'):
            interval = phrase[key]
            if (type(interval) is not list or len(interval) != 2
                    or any(type(x) is not int for x in interval)
                    or any(not support.start_frame <= x <= support.end_frame for x in interval)
                    or interval[0] > interval[1] + (1 if key == 'inward' else 0)):
                raise Unavailable('speaker_link_phrase_evidence')
        if phrase['probe_source_eligible']:
            if not (0 <= phrase['inward'][0] - phrase['outward'][0] <= 1
                    and 0 <= phrase['outward'][1] - phrase['inward'][1] <= 1):
                raise Unavailable('speaker_link_phrase_evidence')
        elif batch.get('timestamp_policy') != TIMESTAMP_POLICY:
            raise Unavailable('speaker_link_phrase_evidence')
    return phrases


def _node_attributions(node):
    if node.get('source_kind') is None:
        return ({'native_scope_id': node['native_scope_id'],
                 'native_label': node['native_label']},)
    attributions = node.get('native_attributions')
    if type(attributions) is not list or not 1 <= len(attributions) <= 4:
        raise Unavailable('speaker_link_native_attributions')
    seen = set()
    for attribution in attributions:
        if (type(attribution) is not dict
                or set(attribution) != {'native_scope_id', 'native_label'}):
            raise Unavailable('speaker_link_native_attributions')
        digest(attribution['native_scope_id']); text(attribution['native_label'])
        pair = (attribution['native_scope_id'], attribution['native_label'])
        if pair in seen:
            raise Unavailable('speaker_link_native_attributions')
        seen.add(pair)
    if (node.get('native_scope_id'), node.get('native_label')) not in seen:
        raise Unavailable('speaker_link_native_attributions')
    return attributions


def _validate_probe_indexes(nodes):
    seen = []
    for node in nodes:
        integer(node.get('probe_index'), MAX_CROPS - 1)
        matches = [n for n in seen if n['source_key'] == node['source_key']
                   or n['pcm16le_sha256'] == node['pcm16le_sha256']]
        same_index = [n for n in seen if n['probe_index'] == node['probe_index']]
        if matches:
            if any(n['probe_index'] != node['probe_index'] for n in matches):
                raise Unavailable('speaker_link_probe_alias_binding')
        elif same_index:
            raise Unavailable('speaker_link_probe_alias_binding')
        elif node['probe_index'] != len({n['probe_index'] for n in seen}):
            raise Unavailable('speaker_link_probe_index_order')
        if any(n['pcm16le_sha256'] != node['pcm16le_sha256']
               or n['float32le_sha256'] != node['float32le_sha256'] for n in same_index):
            raise Unavailable('speaker_link_duplicate_pcm_disagreement')
        seen.append(node)


def _validate_batch_v2(batch, *, require_vectors):
    from .recording_primary_probe import primary_witnesses, primary_metadata, validate_primary_node
    if batch.get('policy_sha256') != POLICY_SHA256_V2:
        raise Unavailable('speaker_link_batch_policy')
    _bounded(batch, MAX_VECTOR_BATCH_BYTES)
    if 'timestamp_policy' in batch and batch['timestamp_policy'] != TIMESTAMP_POLICY:
        raise Unavailable('speaker_link_batch_policy')
    for key in ('result_sha256', 'proof_sha256', 'projection_sha256', 'space_id', 'support_id',
                'batch_sha256', 'native_scope_id'):
        digest(batch.get(key))
    text(batch.get('session_id')); text(batch.get('job_id'))
    if _batch_digest(batch) != batch['batch_sha256']:
        raise Unavailable('speaker_link_batch_binding')
    doc, support, parts = validate_document(canonical(batch.get('proof')))
    if (support.support_id != batch['support_id'] or _hash_bytes(canonical(doc)) != batch['proof_sha256']
            or parts[0].session_id != batch['session_id']):
        raise Unavailable('speaker_link_batch_proof')
    responses = _native_responses(doc, support, batch['session_id'], typed=True)
    packets = batch.get('primary_packets')
    if type(packets) is not list or len(packets) > 4:
        raise Unavailable('speaker_link_primary_packet_capacity')
    expected_witnesses = _combined_witnesses(_witnesses(responses, batch['proof_sha256']),
        primary_witnesses(canonical(doc), packets))
    if batch['native_scope_id'] != responses[0][0] or batch.get('witnesses') != expected_witnesses:
        raise Unavailable('speaker_link_witness_binding')
    phrases = _validate_phrase_evidence(batch, support)
    primary_candidates, primary_outcomes = primary_metadata(support, canonical(doc), packets, batch['space_id'])
    primary_statuses = {canonical(o['primary_source_ref']): o['status'] for o in primary_outcomes}
    if len(primary_statuses) != len(primary_outcomes):
        raise Unavailable('speaker_link_duplicate_source_ref')
    moss_candidates = [('moss', p['phrase_index']) for p in phrases
                       if _moss_selection(p, phrases, responses)[0] == 'candidate'][:MAX_CROPS]
    primary_refs = [('native_primary', canonical(n['primary_source_ref'])) for n in primary_candidates]
    ordered_refs = []
    for index in range(max(len(primary_refs), len(moss_candidates))):
        for values in (primary_refs, moss_candidates):
            if index < len(values):
                ordered_refs.append(values[index])
    expected_refs = ordered_refs[:MAX_CROPS]
    nodes, outcomes = batch.get('nodes'), batch.get('outcomes')
    if (type(nodes) is not list or len(nodes) > MAX_CROPS
            or type(outcomes) is not list or len(outcomes) > MAX_OUTCOMES_V2):
        raise Unavailable('speaker_link_batch_capacity')
    refs = set()
    actual_refs = []
    for node in nodes:
        if type(node) is not dict or node.get('source_kind') not in ('moss', 'native_primary'):
            raise Unavailable('speaker_link_node_shape')
        for key in ('job_id', 'result_sha256', 'support_id', 'proof_sha256', 'projection_sha256', 'space_id'):
            if node.get(key) != batch[key]:
                raise Unavailable('speaker_link_node_binding')
        original = {k: v for k, v in node.items() if k not in ('node_id', 'vector')}
        if node.get('node_id') != sha({'schema': SCHEMA_V2, 'policy_sha256': POLICY_SHA256_V2, **original}):
            raise Unavailable('speaker_link_node_digest')
        integer(node.get('start_frame')); integer(node.get('end_frame'))
        if (node['end_frame'] - node['start_frame'] != CROP_FRAMES
                or not support.start_frame <= node['start_frame'] < node['end_frame'] <= support.end_frame):
            raise Unavailable('speaker_link_node_geometry')
        expected_parts = [asdict(p) for p in intersect_mapping(parts, node['start_frame'], node['end_frame'])]
        if node.get('source_spans') != expected_parts:
            raise Unavailable('speaker_link_node_sources')
        content = [{k: p[k] for k in ('source_sha256', 'source_frames', 'source_start', 'source_end')}
                   for p in expected_parts]
        if node.get('source_key') != sha({'schema': 'exact-source-crop-v1', 'parts': content}):
            raise Unavailable('speaker_link_source_key')
        digest(node.get('pcm16le_sha256')); digest(node.get('float32le_sha256'))
        attributions = _node_attributions(node)
        if node['source_kind'] == 'moss':
            integer(node.get('phrase_index'), 255)
            if _PRIMARY_NODE_FIELDS.intersection(node) or node['phrase_index'] >= len(phrases):
                raise Unavailable('speaker_link_phrase_binding')
            ref = ('moss', node['phrase_index'])
            phrase = phrases[node['phrase_index']]
            status, selected = _moss_selection(phrase, phrases, responses)
            actual = (node.get('native_scope_id'), node.get('native_label'),
                      node['start_frame'], node['end_frame'])
            if (status != 'candidate' or selected != actual or node.get('moss_tag') != phrase['moss_tag']
                    or attributions != [{'native_scope_id': actual[0], 'native_label': actual[1]}]):
                raise Unavailable('speaker_link_phrase_binding')
        else:
            if 'phrase_index' in node or 'moss_tag' in node:
                raise Unavailable('speaker_link_primary_source_ref')
            validate_primary_node(node, canonical(doc), packets)
            ref = ('native_primary', canonical(node.get('primary_source_ref')))
        if ref in refs:
            raise Unavailable('speaker_link_duplicate_source_ref')
        refs.add(ref)
        actual_refs.append(ref)
        if require_vectors:
            valid_vector(node.get('vector'))
    _validate_probe_indexes(nodes)
    if actual_refs != expected_refs:
        raise Unavailable('speaker_link_candidate_selection')
    seen_outcomes = set()
    for outcome in outcomes:
        if type(outcome) is not dict:
            raise Unavailable('speaker_link_outcome_shape')
        primary = 'primary_source_ref' in outcome
        if primary:
            if 'phrase_index' in outcome:
                raise Unavailable('speaker_link_outcome_shape')
            ref = ('native_primary', canonical(outcome['primary_source_ref']))
            if ref[1] not in primary_statuses:
                raise Unavailable('speaker_link_primary_outcome_binding')
            matching = [n for n in nodes if n['source_kind'] == 'native_primary'
                        and n['primary_source_ref'] == outcome['primary_source_ref']]
            expected_status = primary_statuses[ref[1]]
            if outcome.get('status') != ('candidate' if matching else
                    'crop_capacity' if expected_status == 'candidate' else expected_status):
                raise Unavailable('speaker_link_primary_outcome_binding')
        else:
            integer(outcome.get('phrase_index'), 255)
            index = outcome['phrase_index']
            if index >= len(phrases):
                raise Unavailable('speaker_link_outcome_shape')
            ref = ('moss', index)
            matching = [n for n in nodes if n['source_kind'] == 'moss' and n['phrase_index'] == index]
            expected_status, _ = _moss_selection(phrases[index], phrases, responses)
            if outcome.get('status') != ('candidate' if matching else
                    'crop_capacity' if expected_status == 'candidate' else expected_status):
                raise Unavailable('speaker_link_outcome_binding')
        if ref in seen_outcomes:
            raise Unavailable('speaker_link_outcome_shape')
        seen_outcomes.add(ref)
        if outcome.get('status') == 'candidate':
            if len(matching) != 1 or matching[0]['node_id'] != outcome.get('node_id'):
                raise Unavailable('speaker_link_outcome_binding')
        elif matching or 'node_id' in outcome:
            raise Unavailable('speaker_link_outcome_binding')
        elif outcome.get('status') not in ('insufficient_clean_support', 'ambiguous_native_label',
                'crop_capacity', 'timestamp_adjusted_unverified'):
            raise Unavailable('speaker_link_outcome_status')
    expected_outcomes = {('moss', i) for i in range(len(phrases))}
    expected_outcomes.update(('native_primary', pref) for pref in primary_statuses)
    if not refs <= seen_outcomes or seen_outcomes != expected_outcomes:
        raise Unavailable('speaker_link_missing_outcome')
    return batch


def cluster_scores(scores, n, cannot=frozenset(), *, source_overlap=None):
    """Fixed-policy complete-link, including blocked/below-tau competitors.

    Ranking happens BEFORE a cannot-link winner veto. Removing that competitor
    and promoting another would create unsupported links, so is forbidden.
    """
    integer(n, MAX_NODES)
    expected = {(a, b) for a in range(n) for b in range(a + 1, n)}
    if type(scores) is not dict or set(scores) != expected or len(scores) > MAX_PAIRS:
        raise Unavailable('speaker_link_score_matrix')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in scores.values()):
        raise Unavailable('speaker_link_score_nonfinite')
    cannot = frozenset(cannot)
    if not cannot <= expected:
        raise Unavailable('speaker_link_cannot_pair')
    typed = source_overlap is not None
    source_overlap = frozenset() if source_overlap is None else frozenset(source_overlap)
    if not source_overlap <= expected:
        raise Unavailable('speaker_link_source_overlap_pair')
    current = [(i,) for i in range(n)]
    traces = []
    for round_number in range(MAX_NODES):
        proposals = {}
        reasons = {'missing_runner_up': 0, 'exact_tie': 0, 'below_tau': 0,
                   'low_margin': 0, 'cannot_link_winner': 0, 'not_reciprocal': 0}
        if typed:
            reasons['source_overlap_dependency'] = 0
        for i, group in enumerate(current):
            candidates = []
            for j, other in enumerate(current):
                if i == j:
                    continue
                cross = [(min(a, b), max(a, b)) for a in group for b in other]
                candidates.append((min(scores[p] for p in cross), j, any(p in cannot for p in cross)))
            candidates.sort(key=lambda item: (-item[0], current[item[1]]))
            if len(candidates) < 2:
                reasons['missing_runner_up'] += 1
                continue
            best, second = candidates[:2]
            # All competitors remain in the original ranking. Dependency is a
            # veto on the selected evidence trio, never a filter or fallback.
            trio = (group, current[best[1]], current[second[1]])
            dependent = any((min(a, b), max(a, b)) in source_overlap
                for at, one in enumerate(trio) for other in trio[at + 1:]
                for a in one for b in other)
            if best[0] == second[0]:
                reasons['exact_tie'] += 1
            elif best[0] < TAU:
                reasons['below_tau'] += 1
            elif best[0] - second[0] < MARGIN:
                reasons['low_margin'] += 1
            elif best[2]:
                reasons['cannot_link_winner'] += 1
            elif dependent:
                reasons['source_overlap_dependency'] += 1
            else:
                proposals[i] = best[1]
        merges = [(i, j) for i, j in sorted(proposals.items()) if i < j and proposals.get(j) == i]
        used = {i for ij in merges for i in ij}
        reasons['not_reciprocal'] = sum(i not in used for i in proposals)
        traces.append({'round': round_number, 'cluster_count': len(current), 'reasons': reasons,
                       'merges': [[list(current[i]), list(current[j])] for i, j in merges]})
        if not merges:
            return current, traces
        if len(used) != 2 * len(merges):
            raise Unavailable('speaker_link_nondisjoint_merges')
        next_groups = [current[i] for i in range(len(current)) if i not in used]
        for i, j in merges:
            merged = tuple(sorted(current[i] + current[j]))
            for at, a in enumerate(merged):
                for b in merged[at + 1:]:
                    if (a, b) in cannot or scores[(a, b)] < TAU:
                        raise Unavailable('speaker_link_complete_link_violation')
            next_groups.append(merged)
        current = sorted(next_groups)
    raise Unavailable('speaker_link_round_capacity')


def reconcile_batches(batches, *, terminal_states=None):
    """Recompute current annotations for ONE caller-validated recording.

    Labels are anonymous, provisional and revision-sensitive. Exact duplicate
    source crops/PCM do not supply extra competitors or independent evidence.
    Duplicate aliases retain their own result/phrase bindings and all hard
    witnesses; disagreement between duplicate vectors fails closed.
    """
    if type(batches) not in (list, tuple) or len(batches) > MAX_BATCHES:
        raise Unavailable('speaker_link_batch_count')
    batches = [validate_batch(batch, require_vectors=False) for batch in batches]
    if len({b['policy_sha256'] for b in batches}) > 1:
        raise Unavailable('speaker_link_mixed_policy')
    policy_sha256 = batches[0]['policy_sha256'] if batches else POLICY_SHA256
    schema = SCHEMA_V2 if policy_sha256 == POLICY_SHA256_V2 else SCHEMA
    typed = schema == SCHEMA_V2
    terminal_states = {} if terminal_states is None else terminal_states
    if type(terminal_states) is not dict or len(terminal_states) > MAX_BATCHES:
        raise Unavailable('speaker_link_terminal_states')
    for key, value in terminal_states.items():
        digest(key)
        if value not in ('probe_unavailable', 'probe_interrupted'):
            raise Unavailable('speaker_link_terminal_state')
    unique = {}
    for batch in batches:
        key = (batch['job_id'], batch['result_sha256'])
        if key in unique and _bounded(unique[key], MAX_VECTOR_BATCH_BYTES) != _bounded(batch, MAX_VECTOR_BATCH_BYTES):
            raise Unavailable('speaker_link_conflicting_batch')
        unique[key] = batch
    batches = [unique[key] for key in sorted(unique)]
    if len({b['session_id'] for b in batches}) > 1 or len({b['space_id'] for b in batches}) > 1:
        raise Unavailable('speaker_link_incomparable_scope')
    all_nodes = sorted((node for batch in batches for node in batch['nodes']), key=lambda n: n['node_id'])
    if len(all_nodes) > MAX_NODES:
        raise Unavailable('speaker_link_node_capacity')
    if len({n['node_id'] for n in all_nodes}) != len(all_nodes):
        raise Unavailable('speaker_link_duplicate_node')
    nodes = [node for node in all_nodes if 'vector' in node]
    for node in nodes:
        valid_vector(node['vector'])
    # Connected duplicate classes account for both alias source provenance and
    # identical observed PCM. No duplicate earns a second runner-up vote.
    groups = []
    for node in (all_nodes if typed else nodes):
        matches = [g for g in groups if any(n['source_key'] == node['source_key']
                   or n['pcm16le_sha256'] == node['pcm16le_sha256'] for n in g)]
        if matches:
            combined = [node] + [n for g in matches for n in g]
            for g in matches:
                groups.remove(g)
            groups.append(sorted(combined, key=lambda n: n['node_id']))
        else:
            groups.append([node])
    groups.sort(key=lambda g: g[0]['node_id'])
    witnesses = {(w['native_scope_id'], tuple(w['labels'])) for b in batches for w in b['witnesses']}
    if typed:
        # Metadata-only aliases cannot cast a competitor vote, but their
        # original observations and source intervals cannot be erased either.
        # Validate even classes without a vector before dropping them from the
        # acoustic matrix. A contradictory exact alias always fails closed.
        for group in groups:
            if (len({n['pcm16le_sha256'] for n in group}) != 1
                    or len({n['float32le_sha256'] for n in group}) != 1):
                raise Unavailable('speaker_link_duplicate_pcm_disagreement')
            aliases = [a for node in group for a in _node_attributions(node)]
            if any(a['native_scope_id'] == b['native_scope_id'] and
                    (a['native_scope_id'], tuple(sorted((a['native_label'], b['native_label'])))) in witnesses
                    for a in aliases for b in aliases):
                raise Unavailable('speaker_link_duplicate_native_conflict')
        groups = [group for group in groups if any('vector' in node for node in group)]
    vectors = []
    for group in groups:
        if len({n['pcm16le_sha256'] for n in group}) != 1:
            raise Unavailable('speaker_link_duplicate_pcm_disagreement')
        if typed and len({n['float32le_sha256'] for n in group}) != 1:
            raise Unavailable('speaker_link_duplicate_pcm_disagreement')
        available = [n for n in group if 'vector' in n]
        vector = valid_vector(available[0]['vector'])
        if any(valid_vector(n['vector']) != vector for n in available[1:]):
            raise Unavailable('speaker_link_duplicate_vector_disagreement')
        vectors.append(vector)
    cannot = set()
    attributions = [[a for node in group for a in (_node_attributions(node) if typed else
        ({'native_scope_id': node['native_scope_id'], 'native_label': node['native_label']},))]
        for group in groups]
    for i, group in enumerate(groups):
        for j in range(i, len(groups)):
            conflict = any(a['native_scope_id'] == b['native_scope_id'] and
                (a['native_scope_id'], tuple(sorted((a['native_label'], b['native_label'])))) in witnesses
                for a in attributions[i] for b in attributions[j])
            if conflict:
                if i == j:
                    raise Unavailable('speaker_link_duplicate_native_conflict')
                cannot.add((i, j))
    scores = {(a, b): math.fsum(x * y for x, y in zip(vectors[a], vectors[b]))
              for a in range(len(groups)) for b in range(a + 1, len(groups))}
    source_overlap = {(a, b) for a in range(len(groups)) for b in range(a + 1, len(groups))
        if any(_source_intersects(one, two) for one in groups[a] for two in groups[b])} if typed else None
    clusters, traces = cluster_scores(scores, len(groups), cannot, source_overlap=source_overlap)
    annotations = []
    memberships = {}
    for cluster in clusters:
        if len(cluster) > 1:
            identity = 'recording-speaker-' + sha({'policy_sha256': policy_sha256,
                'session_id': batches[0]['session_id'],
                'members': sorted(n['node_id'] for i in cluster for n in groups[i])})[:20]
        else:
            identity = None
        for i in cluster:
            for node in groups[i]:
                if 'vector' in node:
                    memberships[node['node_id']] = (identity, len(groups[i]) > 1)
    jobs = []
    for batch in batches:
        terminal = terminal_states.get(batch['batch_sha256'], 'probe_unavailable')
        counts = {}
        for outcome in batch['outcomes']:
            counts[outcome['status']] = counts.get(outcome['status'], 0) + 1
        default = 'capacity_exhausted' if counts.get('crop_capacity') else 'insufficient_clean_support'
        if batch['nodes'] and not any(n['node_id'] in memberships for n in batch['nodes']):
            default = terminal
        jobs.append({'job_id': batch['job_id'], 'result_sha256': batch['result_sha256'],
            'default_status': default, 'outcome_counts': counts,
            'support_id': batch['support_id'], 'proof_sha256': batch['proof_sha256'],
            'native_scope_id': batch['native_scope_id'], 'space_id': batch['space_id']})
        for node in batch['nodes']:
            identity, duplicate = memberships.get(node['node_id'], (None, False))
            available = node['node_id'] in memberships
            annotation = {k: node[k] for k in ('job_id', 'result_sha256',
                'node_id', 'native_label', 'native_scope_id', 'start_frame',
                'end_frame', 'pcm16le_sha256', 'source_key', 'space_id', 'support_id',
                'proof_sha256', 'projection_sha256')}
            if node.get('source_kind') == 'native_primary':
                annotation.update({k: deepcopy(node[k]) for k in (
                    'source_kind', 'primary_source_ref', 'primary_packet_sha256',
                    'primary_row_index', 'original_parent_ordinal', 'original_unit_ordinals',
                    'original_turn_ordinals', 'capture_namespace', 'native_attributions',
                    'probe_index', 'float32le_sha256')})
            else:
                annotation.update(phrase_index=node['phrase_index'], moss_tag=node['moss_tag'])
                if typed:
                    annotation.update({k: deepcopy(node[k]) for k in (
                        'source_kind', 'native_attributions', 'probe_index', 'float32le_sha256')})
            annotation.update(status=('provisional_linked' if identity else 'source_scoped')
                if available else terminal, provisional_speaker_id=identity,
                duplicate_evidence=duplicate, speaker_identity_valid=False)
            if available and not identity:
                annotation['reason'] = 'reciprocal_complete_link_abstention'
            annotations.append(annotation)
    linked_count = sum(len(c) > 1 for c in clusters)
    if linked_count:
        state = 'provisional_linked'
    elif nodes:
        state = 'source_scoped'
    elif all_nodes:
        state = 'probe_interrupted' if any(a['status'] == 'probe_interrupted' for a in annotations) else 'probe_unavailable'
    else:
        state = 'capacity_exhausted' if any(j['default_status'] == 'capacity_exhausted' for j in jobs) else 'insufficient_clean_support'
    result = {'annotations': annotations, 'jobs': jobs, 'state': state,
        'diagnostics': {'schema': schema, 'policy_sha256': policy_sha256,
            'node_count': len(all_nodes), 'available_node_count': len(nodes),
            'independent_node_count': len(groups), 'pair_count': len(scores),
            'duplicate_count': len(nodes) - len(groups), 'cannot_link_count': len(cannot),
            'linked_cluster_count': linked_count, 'round_count': len(traces),
            'round_reasons': {reason: sum(t['reasons'][reason] for t in traces)
                             for reason in traces[0]['reasons']},
            'merge_counts': [len(t['merges']) for t in traces],
            'speaker_identity_valid': False}}
    if typed:
        result['diagnostics']['source_overlap_pair_count'] = len(source_overlap)
        result['diagnostics']['unembedded_alias_count'] = sum(
            'vector' not in node for group in groups for node in group)
    _bounded(result, 65536)
    return result


def _source_intersects(one, two):
    """Recording-source coordinates, including every typed duplicate alias."""
    return any(a['source_sha256'] == b['source_sha256']
        and a['source_frames'] == b['source_frames']
        and max(a['source_start'], b['source_start']) < min(a['source_end'], b['source_end'])
        for a in one['source_spans'] for b in two['source_spans'])
