"""Fixed, model-free contracts for a current unified transcript projection."""
import base64
import hashlib
import json
import math
import re

from .native_owned_support_cache import ReadError

RATE, BLOCK_FRAMES = 16000, 960000
MAX_FRAME = (1 << 53) - 1
MAX_BINDINGS, MAX_BINDING_BYTES = 1024, 4 * 1024**2
AUDIT_BINDINGS = 64
MAX_SEQUENCE = (1 << 63) - 1
MAX_PRIMARY, MAX_PRIMARY_BYTES = 128, 256 * 1024
MAX_RESULTS, MAX_RESULT_BYTES, MAX_RESULTS_BYTES = 16, 256 * 1024, 2 * 1024**2
MAX_RESPONSE_BYTES, MAX_METADATA_BYTES = 256 * 1024, 16384
PRIMARY_FIELDS = ('id', 'chunk_id', 'start', 'end', 'text', 'raw_text',
                  'asr_backend', 'timestamp_kind', 'confidence',
                  'speaker_local_id', 'speaker_window_start')
HASH = re.compile(r'[a-f0-9]{64}\Z')
POLICY = {'schema': 'current-unified-transcript-v3', 'rate': RATE,
    'block_frames': BLOCK_FRAMES, 'selection': 'exact-adjacent-path-whole-primary-v1',
    'rank': ['maximum-support-frames', 'fewer-jobs', 'seam-ordinary-start-archive-hash'],
    'moss_scope': 'durable-automatic-timestamped-recording-only',
    'moss_timestamps': 'legacy-strict-or-validated-support-end-intersection-10ms-v1',
    'discovery': 'indexed-both-aliases-exact-component-frontier-v1',
    'decision_scope': 'closed-eligible-candidate-whole-primary-components-v1',
    'optional_closure': 'complete-baseline-only-on-explicit-closure-capacity-v1',
    'source_roots': 'ordered-unique-commit-roots-v1',
    'revision': 'transactional-session-counter-and-primary-change-v1',
    'extent': 'session-timeline-and-last-disjoint-chunk',
    'placement_audit': 'separate-last-examined-keyset-v1',
    'block_crossing': 'canonical-whole-source-runs-home-page-references-v1', 'word_times': None,
    'primary_frames': 'outward-envelope-snap-within-1e-6-frame',
    'primary_context_seconds': 5,
    'identity': 'anonymous-source-local', 'completeness': 'not-asserted'}


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('ascii')


POLICY_HASH = hashlib.sha256(encoded(POLICY)).hexdigest()
# A separate immutable identity: legacy POLICY and its cursor bytes stay intact.
LEXICAL_POLICY = {**POLICY,
    'selection': 'whole-lexical-span-estimated-boundaries-v1',
    'grouping': 'ordinal-whole-units-decoration-left-leading-first-equal-anchor-merge-v1',
    'timing': 'stored-native-anchors-estimated-ends-original-arithmetic-outward-envelope-v1',
    'admission': {'units': 512, 'groups': 128, 'atoms': 128, 'staging_bytes': 65536,
                  'shared_primary_bytes': MAX_PRIMARY_BYTES, 'shared_chunks': 128},
    'span_homes': 'maximal-global-consecutive-spans-envelope-start-v1',
    'optional_lexical': 'whole-overlay-primary-only-on-admission-or-serialization-capacity-v1',
    'acoustic_coverage': 'unproven'}
LEXICAL_POLICY_HASH = hashlib.sha256(encoded(LEXICAL_POLICY)).hexdigest()



def fail(reason, status=503):
    raise ReadError(status, reason)


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate key')
        result[key] = value
    return result


def parse_json(raw, maximum):
    if type(raw) not in (bytes, str) or not 0 < len(raw) <= maximum:
        fail('unresolved_capacity')
    try:
        return json.loads(raw, object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        fail('transcript_evidence_invalid')


def frame(seconds):
    if (type(seconds) not in (int, float) or not math.isfinite(seconds)
            or not 0 <= seconds <= MAX_FRAME / RATE):
        fail('transcript_coordinate_invalid')
    value = round(seconds * RATE)
    if not 0 <= value <= MAX_FRAME or seconds != value / RATE:
        fail('transcript_coordinate_unplaced')
    return value


def primary_frame(seconds, *, end=False):
    """Conservative display geometry; the original ASR float stays immutable."""
    if (type(seconds) not in (int, float) or not math.isfinite(seconds)
            or not 0 <= seconds <= MAX_FRAME / RATE):
        fail('transcript_primary_invalid')
    scaled = seconds * RATE
    nearest = round(scaled)
    if abs(scaled - nearest) <= 1e-6:
        value = nearest
    else:
        value = math.ceil(scaled) if end else math.floor(scaled)
    if not 0 <= value <= MAX_FRAME: fail('transcript_primary_invalid')
    return value


def primary_evidence(sid, row):
    """Current exact ASR content identity, deliberately excluding person assignment."""
    data = {'session_id': sid, **{key: row[key] for key in PRIMARY_FIELDS}}
    if (type(data['id']) is not int or data['id'] < 1
            or type(data['text']) is not str
            or any(data[key] is not None and type(data[key]) is not str
                   for key in ('raw_text', 'asr_backend', 'timestamp_kind', 'speaker_local_id'))):
        fail('transcript_primary_invalid')
    for name in ('confidence', 'speaker_window_start'):
        value = data[name]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            fail('transcript_primary_invalid')
    start, end = primary_frame(data['start']), primary_frame(data['end'], end=True)
    if data['start'] >= data['end'] or start >= end: fail('transcript_primary_invalid')
    sha = digest(data)
    return {'source_ref': f"p.{data['id']}.{sha}", 'source': 'primary',
        'content_sha256': sha, 'home_block': start // BLOCK_FRAMES,
        'start_frame': start, 'end_frame': end, 'text': data['text'],
        'raw_text': data['raw_text'], 'timestamp_kind': data['timestamp_kind'],
        'speaker_scope': [data['chunk_id'], data['speaker_window_start'], data['speaker_local_id']]
            if data['speaker_local_id'] is not None and data['speaker_window_start'] is not None else None,
        'word_times': None, 'current_evidence': 'exact_current_asr_content',
        'person_identity': 'not_projected', 'canonical_asr': data}


def cursor(sid, block, revision, *, policy_hash=POLICY_HASH):
    return base64.urlsafe_b64encode(encoded({'policy': policy_hash, 'session': sid,
        'block': block, 'revision': revision})).decode('ascii').rstrip('=')


def parse_cursor(token, sid, *, policy_hash=POLICY_HASH):
    if type(token) is not str or not 1 <= len(token) <= 512 or re.fullmatch(r'[A-Za-z0-9_-]+', token) is None:
        fail('invalid_transcript_cursor', 409)
    try:
        raw = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
        value = parse_json(raw, 384)
        if (type(value) is not dict or set(value) != {'policy', 'session', 'block', 'revision'}
                or policy_hash not in (POLICY_HASH, LEXICAL_POLICY_HASH)
                or value['policy'] != policy_hash or value['session'] != sid
                or type(value['block']) is not int or not 0 <= value['block'] <= MAX_FRAME // BLOCK_FRAMES
                or type(value['revision']) is not str or HASH.fullmatch(value['revision']) is None
                or cursor(sid, value['block'], value['revision'], policy_hash=policy_hash) != token):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, ReadError):
        fail('invalid_transcript_cursor', 409)


def audit_cursor(sid, after, revision):
    return base64.urlsafe_b64encode(encoded({'policy': POLICY_HASH, 'session': sid,
        'view': 'binding-placement-audit-v1', 'after': after, 'revision': revision})).decode('ascii').rstrip('=')


def parse_audit_cursor(token, sid):
    if type(token) is not str or not 1 <= len(token) <= 512 or re.fullmatch(r'[A-Za-z0-9_-]+', token) is None:
        fail('invalid_transcript_cursor', 409)
    try:
        raw = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
        value = parse_json(raw, 384)
        if (type(value) is not dict or set(value) != {'policy', 'session', 'view', 'after', 'revision'}
                or value['policy'] != POLICY_HASH or value['session'] != sid
                or value['view'] != 'binding-placement-audit-v1'
                or type(value['after']) is not int or not 0 <= value['after'] <= MAX_SEQUENCE
                or type(value['revision']) is not str or HASH.fullmatch(value['revision']) is None
                or audit_cursor(sid, value['after'], value['revision']) != token):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, ReadError):
        fail('invalid_transcript_cursor', 409)


def parse_reference(ref):
    if type(ref) is not str or len(ref) > 160: fail('invalid_source_ref', 409)
    match = re.fullmatch(r'p\.([1-9][0-9]{0,18})\.([a-f0-9]{64})', ref)
    if match:
        ident = int(match[1])
        if ident >= 1 << 63: fail('invalid_source_ref', 409)
        return 'primary', ident, match[2], None
    match = re.fullmatch(r'l\.([1-9][0-9]{0,18})\.([a-f0-9]{64})\.([a-f0-9]{64})\.(0|[1-9][0-9]{0,2})-(0|[1-9][0-9]{0,2})', ref)
    if match:
        ident, first, end = int(match[1]), int(match[4]), int(match[5])
        if ident >= 1 << 63 or match[3] != LEXICAL_POLICY_HASH or not 0 <= first < end <= 512:
            fail('invalid_source_ref', 409)
        return 'lexical', ident, match[2], (first, end)
    match = re.fullmatch(r'm\.([a-f0-9]{32})\.([a-f0-9]{64})\.(all|0|[1-9][0-9]{0,2})', ref)
    if match:
        index = match[3] if match[3] == 'all' else int(match[3])
        if index != 'all' and index >= 256: fail('invalid_source_ref', 409)
        return 'moss', match[1], match[2], index
    fail('invalid_source_ref', 409)


def response(value):
    raw = encoded(value)
    if len(raw) > MAX_RESPONSE_BYTES: fail('unresolved_capacity')
    return raw


def lexical_reference(segment_id, inventory_sha256, first, end):
    ref = f'l.{segment_id}.{inventory_sha256}.{LEXICAL_POLICY_HASH}.{first}-{end}'
    parse_reference(ref)
    return ref


def lexical_fallback(value, reason):
    """Discard the entire optional overlay, never retry whole-primary MOSS."""
    observed_notices = len(value.get('notices', []))
    value.update(reading=value['baseline_reading'], selected_runs=[], alternatives=[],
        reading_status='primary_only_optional_lexical_unresolved',
        notices=[{'status':'lexical_overlay_incomplete','reason':reason,
                  'observed_notice_count':observed_notices,'evidence_complete':False}])
    value['lexical_projection'] = {'complete': False, 'selection_performed': False,
        'alternatives_complete': False, 'reason': reason, 'acoustic_coverage': 'unproven'}
    return value


def transcript_response(value):
    """Optional view fields never displace a legal durable-text response."""
    if len(encoded(value)) <= MAX_RESPONSE_BYTES:return response(value)
    if 'recording_speaker_links' in value:
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                item.pop('recording_speaker_link', None)
                stack.extend(item.values())
            elif isinstance(item, list):stack.extend(item)
        value.pop('recording_speaker_links')
        # Preserve either kind of unresolved optional overlay when stripping.
        value['reading_status'] = ('primary_only_optional_closure_unresolved'
            if value.get('optional_closure', {}).get('complete') is False else
            'primary_only_optional_lexical_unresolved'
            if value.get('lexical_projection', {}).get('complete') is False else
            'provisional_links_omitted_cap')
    if len(encoded(value)) > MAX_RESPONSE_BYTES and 'lexical_projection' in value:
        lexical_fallback(value, 'response_bytes')
    return response(value)


SPEAKER_LINK_STATES = frozenset(('provisional_linked','source_scoped','insufficient_clean_support',
    'probe_unavailable','probe_interrupted','links_stale','capacity_exhausted'))


def speaker_annotation(saved, job_id, result_sha256, phrase_index):
    """Whitelisted public annotation: no vectors or full native proof payloads."""
    from .recording_speaker_links import TYPED_POLICIES
    if type(saved) is dict and saved.get('history_policy'):
        return history_speaker_annotation(saved,job_id,result_sha256,phrase_index)
    if type(saved) is not dict:saved = {}
    state = saved.get('state', 'probe_unavailable')
    if saved.get('scope_id') is None:state = 'probe_unavailable'
    if type(state) is not str or state not in SPEAKER_LINK_STATES:state = 'probe_unavailable'
    value = {'status':state, 'provisional_speaker_id':None,
        'scope':'recording', 'speaker_identity_valid':False, 'profile_update_allowed':False,
        'policy_sha256':saved.get('policy_sha256'), 'space_id':saved.get('space_id'),
        'revision':saved.get('revision'), 'decision_revision':saved.get('decision_revision')}
    if saved.get('policy_sha256') in TYPED_POLICIES:
        value['validation_semantics'] = 'validated_at_decision_revision'
    if state == 'links_stale':return value
    jobs = saved.get('jobs', [])
    for item in jobs if type(jobs) is list else ():
        if type(item) is not dict:continue
        if (item.get('job_id'),item.get('result_sha256')) == (job_id,result_sha256):
            status=item.get('default_status',item.get('status'))
            if type(status) is str and status in SPEAKER_LINK_STATES:value['status']=status
            break
    annotations = saved.get('annotations', [])
    for item in annotations if type(annotations) is list else ():
        if type(item) is not dict or item.get('source_kind') == 'native_primary':continue
        if (item.get('job_id'),item.get('result_sha256'),item.get('phrase_index')) == (job_id,result_sha256,phrase_index):
            status = item.get('status')
            if type(status) is not str or status not in SPEAKER_LINK_STATES:break
            value['status'] = status
            if status == 'provisional_linked':value['provisional_speaker_id']=item.get('provisional_speaker_id')
            for key in ('node_id','proof_sha256','source_key','reason','duplicate_evidence'):
                if key in item:value[key]=item[key]
            return value
    # A linked scope is not evidence that an unprobed phrase shares a speaker.
    if value['status'] == 'provisional_linked':value['status']='insufficient_clean_support'
    return value


def primary_speaker_annotation(saved, primary_source_ref):
    """One reversible annotation for an exact current parent, never a label.

    Saved observations can contain several crops for the same parent. None may
    win by list order: all matching observations must agree on status and ID.
    The caller supplies a parent reference computed in its current DB snapshot.
    """
    if type(saved) is not dict or saved.get('scope_id') is None:return None
    from .recording_speaker_links import TYPED_POLICIES
    from .recording_speaker_history import POLICY_SHA256 as HISTORY_POLICY
    if saved.get('policy_sha256') not in (*TYPED_POLICIES,HISTORY_POLICY):return None
    try:
        if parse_reference(primary_source_ref)[0] != 'primary':return None
    except ReadError:return None
    annotations = saved.get('annotations', [])
    if type(annotations) is not list:return None
    matches = [item for item in annotations if type(item) is dict
        and item.get('source_kind') == 'native_primary'
        and item.get('primary_source_ref') == primary_source_ref]
    if not matches:return None
    if any(item.get('speaker_identity_valid') is not False for item in matches):return None
    value = {'status':'probe_unavailable', 'provisional_speaker_id':None,
        'source_kind':'native_primary', 'primary_source_ref':primary_source_ref,
        'scope':'recording', 'speaker_identity_valid':False, 'profile_update_allowed':False,
        'reversible':True, 'validation_semantics':'validated_at_decision_revision',
        'policy_sha256':saved.get('policy_sha256'), 'space_id':saved.get('space_id'),
        'revision':saved.get('revision'), 'decision_revision':saved.get('decision_revision')}
    if saved.get('history_policy'):
        value['history_status']=saved.get('history_status','pending')
        value['validation_semantics']=saved.get('validation_semantics','validated_at_admission')
    state = saved.get('state')
    if type(state) is not str or state not in SPEAKER_LINK_STATES:return value
    revision, decision_revision = saved.get('revision'), saved.get('decision_revision')
    if (state == 'links_stale' or type(revision) is not int or revision < 0
            or type(decision_revision) is not int or revision != decision_revision):
        value['status'] = 'links_stale'
        return value
    identities = []
    for item in matches:
        status, identity = item.get('status'), item.get('provisional_speaker_id')
        if (type(status) is not str or status not in SPEAKER_LINK_STATES
                or status == 'provisional_linked' and (type(identity) is not str
                    or re.fullmatch(r'recording-speaker-[a-f0-9]{20}', identity) is None)
                or status != 'provisional_linked' and identity is not None):
            return value
        identities.append((status, identity))
    if len(set(identities)) != 1:
        value.update(status='insufficient_clean_support', reason='primary_observations_disagree')
        return value
    value['status'], value['provisional_speaker_id'] = identities[0]
    # Keep only agreeing, bounded scalar provenance. Historical lexical refs,
    # private capture packets, vectors and native labels are never exported.
    for key in ('node_id','proof_sha256','source_key','primary_packet_sha256','reason','duplicate_evidence'):
        item = matches[0].get(key)
        if key == 'duplicate_evidence':valid = type(item) is bool
        elif key == 'reason':valid = type(item) is str and len(item) <= 128
        else:valid = type(item) is str and HASH.fullmatch(item) is not None
        if valid and all(match.get(key) == item for match in matches):value[key] = item
    return value


def annotate_primary_speaker_links(value, annotations):
    """Decorate projected rows only; exact content and lexical hashes stay put."""
    stack = [value]
    while stack:
        item = stack.pop()
        if type(item) is dict:
            source = item.get('source')
            ref = (item.get('source_ref') if source == 'primary' else
                   item.get('parent_source_ref') if source == 'primary_lexical' else None)
            if type(ref) is str and ref in annotations:
                item['recording_speaker_link'] = dict(annotations[ref])
            stack.extend(child for key, child in item.items() if key != 'recording_speaker_link')
        elif type(item) is list:stack.extend(item)
    return value


def history_speaker_annotation(saved,job_id,result_sha256,phrase_index):
    """Complete indexed history matches use unanimous status and identity."""
    base={'status':saved.get('state','probe_unavailable'),'provisional_speaker_id':None,
        'scope':'recording','speaker_identity_valid':False,'profile_update_allowed':False,'reversible':True,
        'policy_sha256':saved.get('policy_sha256'),'space_id':saved.get('space_id'),
        'revision':saved.get('revision'),'decision_revision':saved.get('decision_revision'),
        'history_status':saved.get('history_status','pending'),
        'validation_semantics':saved.get('validation_semantics','validated_at_admission')}
    if base['history_status']!='current' or saved.get('complete') is not True:return base
    found=[x for x in saved.get('annotations',[]) if x.get('source_kind')=='moss' and
        (x.get('job_id'),x.get('result_sha256'),x.get('phrase_index'))==(job_id,result_sha256,phrase_index)]
    if not found:return {**base,'status':'insufficient_clean_support'}
    identities={(x.get('status'),x.get('provisional_speaker_id')) for x in found}
    if len(identities)!=1:return {**base,'status':'insufficient_clean_support','reason':'observations_disagree'}
    status,identity=next(iter(identities))
    if (status not in SPEAKER_LINK_STATES or any(x.get('speaker_identity_valid') is not False for x in found)
            or status=='provisional_linked' and (type(identity) is not str or re.fullmatch(r'recording-speaker-[a-f0-9]{20}',identity) is None)
            or status!='provisional_linked' and identity is not None):return {**base,'status':'probe_unavailable'}
    return {**base,'status':status,'provisional_speaker_id':identity}
