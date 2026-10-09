"""Durable, paginated evidence ledger. Candidates are never promoted to events."""
from __future__ import annotations
import hashlib
import json
import math

MAX_WINDOWS_PER_COMMIT=64
MAX_PAGE_SIZE=500
MAX_LABELS=10
MAX_FAMILIES=6
MAX_TEXT=256
MAX_PAYLOAD_BYTES=8192
MAX_PROVENANCE_BYTES=4096
FAMILIES=frozenset({'rain','driving','subway','door','music','silence'})
PRIVATE_VOICE_LABELS=frozenset({'Male speech, man speaking','Female speech, woman speaking','Child speech, kid speaking','Baby cry, infant cry','Children shouting','Children playing'})
STATUS='unverified_model_candidate'

def init_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS event_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
        analysis_offset REAL NOT NULL, candidate_index INTEGER NOT NULL,
        start REAL NOT NULL, end REAL NOT NULL,
        support_start REAL NOT NULL, support_end REAL NOT NULL,
        status TEXT NOT NULL CHECK(status='unverified_model_candidate'),
        time_precision TEXT NOT NULL, onset_precision_seconds REAL,
        payload TEXT NOT NULL, provenance TEXT NOT NULL,
        UNIQUE(chunk_id,analysis_offset,candidate_index),
        CHECK(end>start), CHECK(support_end>support_start))''')
    db.execute('CREATE INDEX IF NOT EXISTS event_candidates_chunk ON event_candidates(chunk_id,id)')

def _number(value,name,low=None,high=None):
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):raise ValueError('Invalid candidate '+name)
    value=float(value)
    if (low is not None and value<low) or (high is not None and value>high):raise ValueError('Invalid candidate '+name)
    return value

def _text(value,name):
    if not isinstance(value,str) or not value or len(value)>MAX_TEXT:raise ValueError('Invalid candidate '+name)
    return value

def _json(value,limit):
    encoded=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    if len(encoded.encode('utf-8'))>limit:raise ValueError('Candidate evidence payload exceeds limit')
    return encoded

def _provenance(metadata):
    """Preserve event-relevant stable evidence, never arbitrary dynamic metadata."""
    if metadata is None:metadata={}
    if not isinstance(metadata,dict):raise ValueError('Invalid candidate provenance')
    out={'candidate_source':'audio_event_classifier','scores_calibrated':False}
    truncated=False
    def text(key,value):
        nonlocal truncated
        if not isinstance(value,str):return
        if len(value)>256:truncated=True
        out[key]=value[:256]
    for key in ('inference_revision','event_model','event_model_license','event_policy_revision','event_artifact_identity_status'):
        if key in metadata:text(key,metadata[key])
    for key in ('event_model_sha256','event_labelmap_sha256'):
        value=metadata.get(key)
        if isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdefABCDEF' for c in value):
            out[key]=value.lower()
    models=metadata.get('models',{})
    if isinstance(models,dict) and isinstance(models.get('events'),str):text('event_model',models['events'])
    for key in ('event_window_seconds','event_hop_seconds','event_max_seconds'):
        if key in metadata:out[key]=_number(metadata[key],key,0,120)
    config=metadata.get('effective_config',{})
    if isinstance(config,dict):
        selected={}
        for key in ('event_threshold','event_release_ratio','event_silence_peak','silence_rms_threshold'):
            if key in config:selected[key]=_number(config[key],key,0,1)
        if isinstance(config.get('event_thresholds'),dict):
            selected['event_thresholds']={k:_number(v,'threshold',0,1) for k,v in config['event_thresholds'].items() if k in FAMILIES}
        if selected:out['effective_config']=selected
    out['provenance_truncated']=truncated
    encoded=_json(out,MAX_PROVENANCE_BYTES-100)
    out['fingerprint']=hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    return _json(out,MAX_PROVENANCE_BYTES)

def _payload(candidate):
    labels=candidate.get('top_labels',[])
    if not isinstance(labels,list) or len(labels)>100:raise ValueError('Invalid candidate top-label list')
    unique={}
    for item in labels:
        if not isinstance(item,dict):raise ValueError('Invalid candidate label entry')
        label=_text(item.get('label'),'label');score=_number(item.get('score'),'score',0,1)
        if label in PRIVATE_VOICE_LABELS:continue
        unique[label]=max(score,unique.get(label,0.))
    top=[{'label':label,'score':score} for label,score in sorted(unique.items(),key=lambda item:(-item[1],item[0]))[:MAX_LABELS]]
    families=candidate.get('category_scores',{})
    if not isinstance(families,dict) or len(families)>MAX_FAMILIES or any(key not in FAMILIES for key in families):raise ValueError('Invalid candidate families')
    scores={}
    for family,item in families.items():
        if not isinstance(item,dict):raise ValueError('Invalid candidate family entry')
        source=_text(item.get('source_label'),'source label')
        if source in PRIVATE_VOICE_LABELS:continue
        scores[family]={'score':_number(item.get('score'),'family score',0,1),'source_label':source}
    default_method='signal_threshold_silence_rule' if set(scores)=={'silence'} and not top else 'audioset_window_classifier'
    result={'top_labels':top,'category_scores':scores,'top_labels_truncated':len(unique)>MAX_LABELS,
            'method':_text(candidate.get('method',default_method),'method'),
            'ownership':'core_intersection','time_axis':'session_timeline',
            'support_semantics':'Original classifier support, not verified event onset/end',
            'scores_calibrated':False}
    if 'model_inference_executed' in candidate:
        if not isinstance(candidate['model_inference_executed'],bool):raise ValueError('Invalid candidate model execution state')
        result['model_inference_executed']=candidate['model_inference_executed']
    for key in ('speech_score','rms','peak'):
        if key in candidate:result[key]=_number(candidate[key],key,0,1 if key=='speech_score' else None)
    return _json(result,MAX_PAYLOAD_BYTES)

def commit_candidates(db,chunk,offset,candidates,provenance):
    """Insert within the caller's transaction; repeat offsets preserve first evidence.

    Inputs must already be core-owned by worker.own_core. Original support can
    extend outside the core. No threshold or semantic relabeling is performed.
    """
    if not isinstance(candidates,list) or len(candidates)>MAX_WINDOWS_PER_COMMIT:raise ValueError('Candidate window limit exceeded')
    offset=_number(offset,'analysis offset',0)
    duration=_number(chunk['duration'],'chunk duration',0)
    origin=_number(chunk['timeline_start'],'timeline start')+offset
    if offset>duration:raise ValueError('Candidate offset outside chunk')
    record_provenance=_provenance(provenance)
    prepared=[]
    for index,item in enumerate(candidates):
        if not isinstance(item,dict) or item.get('time_axis')!='core_relative' or item.get('ownership')!='core_intersection':raise ValueError('Candidates must be rebased to owned core')
        start=_number(item.get('start'),'start',0,60);end=_number(item.get('end'),'end',0,60)
        support_start=_number(item.get('support_start',start),'support start',-60,120)
        support_end=_number(item.get('support_end',end),'support end',-60,120)
        if end<=start or offset+end>duration+1e-6:raise ValueError('Candidate owned interval outside chunk')
        if support_start>start+1e-6 or support_end<end-1e-6 or not 0<support_end-support_start<=60+1e-6:raise ValueError('Candidate support does not contain owned interval')
        precision=item.get('onset_precision_seconds')
        if precision is not None:precision=_number(precision,'onset precision',0,60)
        prepared.append((chunk['id'],offset,index,origin+start,origin+end,origin+support_start,origin+support_end,STATUS,'window_support',precision,_payload(item),record_provenance))
    inserted=0
    for values in prepared:
        cursor=db.execute('''INSERT INTO event_candidates(chunk_id,analysis_offset,candidate_index,start,end,support_start,support_end,status,time_precision,onset_precision_seconds,payload,provenance)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(chunk_id,analysis_offset,candidate_index) DO NOTHING''',values)
        inserted+=cursor.rowcount
    return inserted

def candidates(store,sid,after=0,limit=500):
    if isinstance(after,bool) or not isinstance(after,int) or after<0:raise ValueError('Invalid candidate cursor')
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=MAX_PAGE_SIZE:raise ValueError('Candidate page limit must be1..500')
    store.session(sid)
    with store.db() as db:
        fetched=db.execute('''SELECT e.* FROM event_candidates e JOIN chunks c ON c.id=e.chunk_id
            WHERE c.session_id=? AND e.id>? ORDER BY e.id LIMIT ?''',(sid,after,limit+1)).fetchall()
    more=len(fetched)>limit;rows=[]
    for raw in fetched[:limit]:
        row=dict(raw);payload=json.loads(row.pop('payload'));row['provenance']=json.loads(row['provenance']);row.update(payload);rows.append(row)
    return {'event_candidates':rows,'next_cursor':rows[-1]['id'] if rows else after,'has_more':more}
