# Unexecuted portable template; generate every configuration placeholder before use.
"""External finite-smoke producer gate; leaves accepted product results unchanged.

This is a qualification precondition for one frozen SenseVoice/native producer,
not a production schema or a guarantee that Store validation/commit succeeds.
"""
import hashlib
import json
import math
MAX_ENVELOPE=8*2**20
MAX_METADATA=2**20
MAX_TURNS=32
MAX_LABELS=8
BACKEND='sensevoice'
TIMING='Native CTC token starts; interval ends are estimated, not forced alignment'
class Rejected(RuntimeError):pass

def require(ok,reason):
    if not ok:raise Rejected(reason)

def encoded(value):
    try:return json.dumps(value,allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,OverflowError) as exc:raise Rejected('nonfinite or unsupported JSON') from exc

def admit_native(turns, *, frames=307040):
    require(type(frames) is int and frames==307040,'fixed native frame count')
    require(type(turns) is list and len(turns)<=MAX_TURNS,'native turn limit')
    require(len(encoded(turns))<=2**20,'native turn bytes')
    labels=set()
    for turn in turns:
        require(type(turn) is dict,'native turn object required')
        label=turn.get('speaker_local_id')
        require(type(label) is str and 0<len(label)<=128,'native label type or length')
        labels.add(label)
        start,end=turn.get('start'),turn.get('end')
        require(type(start) in (int,float) and type(end) in (int,float) and
                math.isfinite(start) and math.isfinite(end) and
                0<=start<end<=frames/16000,'native turn interval')
    require(len(labels)<=MAX_LABELS,'native local label limit')
    return frozenset(labels)

def admit_raw(raw, *, native_labels, expected_speaker_sha256):
    require(type(raw) is bytes and len(raw)<=MAX_ENVELOPE,'raw analyzer envelope bytes')
    def nonfinite(value):raise Rejected('nonfinite JSON constant')
    try:result=json.loads(raw.decode('utf-8'),parse_constant=nonfinite)
    except (UnicodeDecodeError,ValueError) as exc:raise Rejected('invalid raw analyzer JSON') from exc
    evidence=admit(result,native_labels=native_labels,expected_speaker_sha256=expected_speaker_sha256)
    return dict(evidence,raw_wire_bytes=len(raw),raw_wire_sha256=hashlib.sha256(raw).hexdigest())

def admit(result, *, native_labels, expected_speaker_sha256):
    """Return evidence only; callers retain/pass the exact original result object."""
    require(type(native_labels) in (set,frozenset),'native label set must come from observed bounded native turns')
    require(len(native_labels)<=MAX_LABELS and all(type(x) is str and 0<len(x)<=128 for x in native_labels),'native label limit')
    require(type(expected_speaker_sha256) is str and len(expected_speaker_sha256)==64 and all(x in '0123456789abcdef' for x in expected_speaker_sha256),'pinned speaker SHA required')
    require(type(result) is dict and '_error' not in result,'non-result or producer error')
    raw=encoded(result);require(len(raw)<=MAX_ENVELOPE,'result exceeds8MiB')
    metadata=result.get('metadata');require(type(metadata) is dict,'metadata required')
    metadata_raw=encoded(metadata);require(len(metadata_raw)<=MAX_METADATA,'metadata exceeds1MiB')
    require(metadata.get('asr_backend')==BACKEND,'unexpected ASR backend')
    require(metadata.get('timestamp_semantics')==TIMING,'unexpected timestamp semantics')
    require(metadata.get('speaker_embedding_sha256')==expected_speaker_sha256,'speaker model identity mismatch')
    require(result.get('overlap_candidates') is None,'lexical candidates outside this smoke')
    require(not result.get('_speaker_support') and not result.get('_speaker_embedding_vectors'),'speaker support outside this smoke')
    turns=result.get('speaker_turns',[])
    require(type(turns) is list and len(turns)<=MAX_TURNS,'turn limit')
    observed=set()
    for turn in turns:
        require(type(turn) is dict,'turn object required')
        label=turn.get('speaker_local_id')
        require(type(label) is str and label in native_labels,'turn label not native admitted')
        observed.add(label)
    require(observed==native_labels,'analyzer/native label set differs')
    segments=result.get('segments',[]);events=result.get('events',[])
    require(type(segments) is list and len(segments)<=1000 and type(events) is list and len(events)<=1000,'product row ceilings')
    for row in segments:
        require(type(row) is dict,'segment object required')
        require(row.get('asr_backend')==BACKEND and row.get('timestamp_kind')==TIMING,'segment provenance mismatch')
        label=row.get('speaker_local_id')
        require(label is None or type(label) is str and label in native_labels,'segment label not native admitted')
    for row in turns+segments:
        vector=row.get('speaker_embedding')
        if vector is not None:
            require(type(vector) is list and 8<=len(vector)<=2048 and all(type(x) in (int,float) and math.isfinite(x) for x in vector),'embedding bound')
    return {'envelope_bytes':len(raw),'envelope_sha256':hashlib.sha256(raw).hexdigest(),
            'metadata_bytes':len(metadata_raw),'turns':len(turns),'native_labels':len(native_labels),
            'segments':len(segments),'events':len(events),'scope':'fixed producer precondition; Store may still reject'}
