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
SAMPLE_RATE=16000
MAX_PROJECTION_RECORDS=64
MAX_PROJECTION_BYTES=64*1024
PROJECTION_WRAPPER_RESERVE=128
FAMILIES=frozenset({'rain','driving','subway','door','music','silence'})
PRIVATE_VOICE_LABELS=frozenset({'Male speech, man speaking','Female speech, woman speaking','Child speech, kid speaking','Baby cry, infant cry','Children shouting','Children playing'})
STATUS='unverified_model_candidate'
TEMPORAL_KIND='framemn_temporal_v1'
TEMPORAL_PROFILE='knock-001-median9-experimental-v1'
TEMPORAL_COMMIT='1aa47e482f7e89904cba2338999345025d8b4e36'
TEMPORAL_CHECKPOINT='4a0fe320d5369987b772394c51881fb20a602967e6842f72f3e5c8181065ece7'
TEMPORAL_LABELMAP='89c223c4b1d5b5fefbccc10e5a652221a8c4fd70ab46a8a1885670e889719348'
TEMPORAL_CONTEXT='independent_160000_sample_tiles;median9_across_tile_seams;reflect_only_analyzer_endpoints'
TEMPORAL_MAX_RUNS=125


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
    kind=candidate.get('payload_kind')
    if kind is not None:
        if kind!=TEMPORAL_KIND:raise ValueError('Unknown candidate payload kind')
        return _temporal_payload(candidate)
    if 'temporal' in candidate or 'candidate_provenance' in candidate:
        raise ValueError('Temporal evidence requires an explicit payload kind')
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

def _integer(value,name,low,high):
    if isinstance(value,bool) or not isinstance(value,int) or not low<=value<=high:
        raise ValueError('Invalid temporal '+name)
    return value


def _pairs(value,name,upper):
    if not isinstance(value,list) or len(value)>TEMPORAL_MAX_RUNS:
        raise ValueError('Invalid temporal '+name+' count')
    result=[];previous=-1
    for pair in value:
        if not isinstance(pair,list) or len(pair)!=2:raise ValueError('Invalid temporal '+name)
        first=_integer(pair[0],name,0,upper);last=_integer(pair[1],name,0,upper)
        # Adjacent runs are not canonical and must have been merged by decoding.
        if last<=first or first<=previous:raise ValueError('Invalid temporal '+name+' order')
        result.append([first,last]);previous=last
    return result


def _temporal_provenance(candidate):
    raw=candidate.get('candidate_provenance')
    constants={'model':'FrameMN10','checkpoint_sha256':TEMPORAL_CHECKPOINT,
        'checkpoint_integrity':'local_first_seen_sha256_not_publisher_digest',
        'labelmap_identity':'audioset_strong_447_at_'+TEMPORAL_COMMIT,
        'upstream_commit':TEMPORAL_COMMIT,'context_semantics':TEMPORAL_CONTEXT,
        'profile':TEMPORAL_PROFILE}
    if not isinstance(raw,dict) or set(raw)!=(set(constants)|{'labelmap_sha256'}):
        raise ValueError('Invalid temporal provenance fields')
    if any(type(raw[key]) is not str or raw[key]!=value for key,value in constants.items()):
        raise ValueError('Invalid temporal provenance identity')
    digest=raw['labelmap_sha256']
    if digest!=TEMPORAL_LABELMAP:
        raise ValueError('Invalid temporal labelmap identity')
    result={**constants,'labelmap_sha256':digest,'candidate_source':'framemn_temporal_candidate',
        'scores_calibrated':False,'provenance_truncated':False}
    result['fingerprint']=hashlib.sha256(_json(result,MAX_PROVENANCE_BYTES).encode()).hexdigest()
    return _json(result,MAX_PROVENANCE_BYTES)


def _temporal(candidate):
    raw=candidate.get('temporal')
    fixed={'profile':TEMPORAL_PROFILE,'target_label':'Knock','target_class_index':228,
        'threshold':.01,'median_frames':9,'grid_samples':640,'sample_rate':SAMPLE_RATE}
    fields=set(fixed)|{'tile_start_sample','tile_sample_count','analyzer_sample_count',
        'frame_runs','raw_target_peak','filtered_target_peak'}
    owned={'owned_sample_runs','owned_tile_sample_range'}
    if not isinstance(raw,dict) or set(raw) not in (fields,fields|owned):
        raise ValueError('Invalid temporal payload fields')
    for key,value in fixed.items():
        if type(raw[key]) is not type(value) or raw[key]!=value:raise ValueError('Invalid temporal '+key)
    total=_integer(raw['analyzer_sample_count'],'analyzer samples',1,960160)
    first=_integer(raw['tile_start_sample'],'tile start',0,total-1)
    count=_integer(raw['tile_sample_count'],'tile samples',1,160000)
    if first%160000 or count!=min(160000,total-first):raise ValueError('Invalid temporal tile geometry')
    frames=(count+639)//640
    runs=_pairs(raw['frame_runs'],'frame runs',frames)
    result={**fixed,'tile_start_sample':first,'tile_sample_count':count,'analyzer_sample_count':total,
        'frame_runs':runs,'raw_target_peak':_number(raw['raw_target_peak'],'raw target peak',0,1),
        'filtered_target_peak':_number(raw['filtered_target_peak'],'filtered target peak',0,1)}
    if 'owned_sample_runs' in raw:
        bounds=raw['owned_tile_sample_range']
        if not isinstance(bounds,list) or len(bounds)!=2:raise ValueError('Invalid temporal owned bounds')
        lo=_integer(bounds[0],'owned first sample',0,count);hi=_integer(bounds[1],'owned last sample',0,count)
        if hi<=lo:raise ValueError('Invalid temporal owned bounds')
        actual=_pairs(raw['owned_sample_runs'],'owned sample runs',count)
        expected=[[max(lo,a*640),min(hi,b*640,count)] for a,b in runs
                  if min(hi,b*640,count)>max(lo,a*640)]
        if actual!=expected:raise ValueError('Temporal owned support is not exact core intersection')
        result.update(owned_sample_runs=actual,owned_tile_sample_range=[lo,hi])
    return result


def _temporal_payload(candidate):
    temporal=_temporal(candidate)
    _temporal_provenance(candidate)
    if (candidate.get('method')!='framemn10_knock_temporal_experimental' or
            candidate.get('model_inference_executed') is not True or
            candidate.get('onset_precision_seconds') is not None or
            candidate.get('time_precision')!='model_score_grid' or
            candidate.get('category_scores',{})!={} or 'speech_score' in candidate):
        raise ValueError('Invalid temporal candidate semantics')
    # Reuse the legacy bounded label sanitizer, without interpreting those raw
    # maxima as decoded temporal evidence or category scores.
    raw_labels=candidate.get('top_labels',[])
    # Validate even suppressed entries, then filter before the diagnostic top-k.
    _payload({'top_labels':raw_labels})
    private=PRIVATE_VOICE_LABELS|{'Baby laughter','Child singing','Female singing','Male singing'}
    labels=json.loads(_payload({'top_labels':[item for item in raw_labels if item['label'] not in private]}))
    result={'payload_kind':TEMPORAL_KIND,'method':candidate['method'],
        'model_inference_executed':True,'top_labels':labels['top_labels'],
        'top_labels_truncated':labels['top_labels_truncated'],'category_scores':{},
        'top_labels_semantics':'raw_per_class_tile_maxima_diagnostic_only',
        'ownership':'core_intersection','time_axis':'session_timeline',
        'support_semantics':'Original model tile; frame_runs are unverified original grid support',
        'scores_calibrated':False,'temporal':temporal,
        'temporal_time_axis':'tile_relative','temporal_interval_convention':'half_open',
        'temporal_owned_support_units':'samples','temporal_original_support_units':'score_frames',
        'timing_note':'40 ms grid is neither onset accuracy nor end-to-end latency'}
    return _json(result,MAX_PAYLOAD_BYTES)


def _own_temporal(candidate,start,end):
    temporal=_temporal(candidate)
    if 'owned_sample_runs' in temporal:raise ValueError('Temporal candidate already owned')
    first=temporal['tile_start_sample'];count=temporal['tile_sample_count']
    if candidate['start']!=first/SAMPLE_RATE or candidate['end']!=(first+count)/SAMPLE_RATE:
        raise ValueError('Temporal model tile timestamps do not match sample geometry')
    lo=max(first,_sample_boundary(start));hi=min(first+count,_sample_boundary(end))
    if hi<=lo:return None
    lo-=first;hi-=first
    runs=[[max(lo,a*640),min(hi,b*640,count)] for a,b in temporal['frame_runs']
          if min(hi,b*640,count)>max(lo,a*640)]
    return dict(temporal,owned_sample_runs=runs,owned_tile_sample_range=[lo,hi])


def _diagnostic_evidence(candidate):
    """Retain validated public evidence, raw timing and a hash, never private labels."""
    evidence=json.loads(_payload(candidate))
    for key in ('ownership','time_axis','support_semantics'):evidence.pop(key)
    raw=json.dumps(candidate,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    return {'candidate_evidence':evidence,'candidate_evidence_sanitized':True,
            'raw_support_seconds':[candidate['start'],candidate['end']],
            'raw_candidate_sha256':hashlib.sha256(raw.encode('utf-8')).hexdigest()}


def _sample_boundary(seconds):
    """Map a boundary with an uncertainty-aware half-up tie convention.

    A half-frame time divided by SAMPLE_RATE need not multiply back to the
    exact half frame. Allow three input roundings (two nonnegative rounded
    terms and their rounded sum), plus multiplication rounding, in frame units.
    This is an uncertainty-aware tie convention, not exact rounding of arbitrary
    float histories. It changes only integer ownership, never time or duration,
    and applies identically to either side of a shared boundary.
    """
    scaled=seconds*SAMPLE_RATE
    lower=math.floor(scaled);midpoint=lower+.5
    uncertainty=(3*math.ulp(seconds)*SAMPLE_RATE+math.ulp(scaled))/2
    return lower+int(scaled>=midpoint or midpoint-scaled<=uncertainty)


def own_candidates(metadata,start,end):
    """Intersect on the detector's PCM grid; keep surviving float times unchanged.

    Detector supports are integer 16 kHz sample ranges. A floating-point sliver
    at a touching boundary cannot own a sample. This is not a duration cutoff:
    one-frame grid supports and bounded-roundoff half-frame ties are retained.
    Original classifier candidates remain untouched in bounded window metadata.
    """
    raw=metadata.get('event_candidates',[])
    if not isinstance(raw,list) or len(raw)>MAX_WINDOWS_PER_COMMIT:raise ValueError('Candidate window limit exceeded')
    candidates=[]
    for index,candidate in enumerate(raw):
        if not isinstance(candidate,dict):raise ValueError('Invalid candidate window')
        support_start=_number(candidate.get('start'),'support start',0,120)
        support_end=_number(candidate.get('end'),'support end',0,120)
        if not 0<support_end-support_start<=60+1e-6:raise ValueError('Invalid candidate support interval')
        precision=candidate.get('onset_precision_seconds')
        if precision is not None:_number(precision,'onset precision',0,60)
        _payload(candidate)  # Exclusion must not bypass the existing evidence validation.
        temporal=(_own_temporal(candidate,start,end) if candidate.get('payload_kind')==TEMPORAL_KIND else None)
        lo=max(start,support_start);hi=min(end,support_end)
        if hi<=lo:continue
        # Use explicit half-up ties: round-to-even can turn an
        # exact one-sample [1.5,2.5] frame interval into the empty [2,2].
        first=_sample_boundary(lo);last=_sample_boundary(hi)
        if last<=first:
            metadata.setdefault('event_candidate_projection_diagnostics',[]).append({
                'reason':'numeric_degenerate_sample_grid_intersection',
                'source_candidate_index':index,**_diagnostic_evidence(candidate),
                'raw_time_axis':'analyzer_input',
                'core_input_seconds':[start,end],'intersection_input_seconds':[lo,hi],
                'intersection_frames':[first,last],'sample_rate':SAMPLE_RATE})
            continue
        if candidate.get('payload_kind')==TEMPORAL_KIND and temporal is None:continue
        owned=dict(candidate,start=lo-start,end=hi-start,
            support_start=candidate['start']-start,support_end=candidate['end']-start,
            time_axis='core_relative',ownership='core_intersection',
            _source_candidate_index=index,
            support_semantics='original model window; not verified event onset/end')
        if temporal is not None:
            owned['temporal']=temporal
            _payload(owned)  # Refuse overflow; never truncate decoded support.
        candidates.append(owned)
    return candidates


def retain_projection_diagnostics(provenance,metadata,offset):
    """Keep bounded first evidence across later windows in existing chunk JSON.

    This runs inside the core transaction, independent of stable model variants.
    Overflow is explicit; last-window raw metadata is never rewritten or filtered.
    """
    diagnostics=metadata.get('event_candidate_projection_diagnostics',[])
    if not diagnostics:return
    audit=provenance.setdefault('event_candidate_projection_diagnostics',
        {'count':0,'records':[],'truncated':False})
    for diagnostic in diagnostics:
        audit['count']+=1
        record=dict(diagnostic,analysis_offset=offset)
        records=audit['records']+[record]
        if (len(records)>MAX_PROJECTION_RECORDS or
                # Match chunks.provenance serialization, including escaped Unicode.
                # Reserve room for count/records/truncated and growing count digits.
                len(json.dumps(records,allow_nan=False).encode('utf-8'))>MAX_PROJECTION_BYTES-PROJECTION_WRAPPER_RESERVE):
            audit['truncated']=True
        else:audit['records'].append(record)


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
    if provenance is None:provenance={}
    prepared=[]
    for index,item in enumerate(candidates):
        if not isinstance(item,dict) or item.get('time_axis')!='core_relative' or item.get('ownership')!='core_intersection':raise ValueError('Candidates must be rebased to owned core')
        owned_limit=60.01 if item.get('payload_kind')==TEMPORAL_KIND else 60
        start=_number(item.get('start'),'start',0,owned_limit);end=_number(item.get('end'),'end',0,owned_limit)
        support_start=_number(item.get('support_start',start),'support start',-60,120)
        support_end=_number(item.get('support_end',end),'support end',-60,120)
        if end<=start or offset+end>duration+1e-6:raise ValueError('Candidate owned interval outside chunk')
        if support_start>start+1e-6 or support_end<end-1e-6 or not 0<support_end-support_start<=60+1e-6:raise ValueError('Candidate support does not contain owned interval')
        precision=item.get('onset_precision_seconds')
        if precision is not None:precision=_number(precision,'onset precision',0,60)
        payload=_payload(item)
        row_provenance=record_provenance
        time_precision='window_support'
        if item.get('payload_kind')==TEMPORAL_KIND:
            temporal=_temporal(item)
            if 'owned_sample_runs' not in temporal:raise ValueError('Temporal candidate lacks core ownership')
            bounds=[_sample_boundary(start-support_start),_sample_boundary(end-support_start)]
            if temporal['owned_tile_sample_range']!=bounds:
                raise ValueError('Temporal row and nested core ownership disagree')
            if _sample_boundary(support_end-support_start)!=temporal['tile_sample_count']:
                raise ValueError('Temporal original support geometry mismatch')
            row_provenance=_temporal_provenance(item)
            time_precision='model_score_grid'
        absolute=[origin+start,origin+end,origin+support_start,origin+support_end]
        if not all(math.isfinite(value) for value in absolute):raise ValueError('Nonfinite candidate projection')
        if absolute[1]<=absolute[0] or absolute[3]<=absolute[2]:
            source_index=item.get('_source_candidate_index',index)
            raw=provenance.get('event_candidates',[])
            # Replace the list so a rolled-back core cannot mutate retry input.
            provenance['event_candidate_projection_diagnostics']=[
                *provenance.get('event_candidate_projection_diagnostics',[]),{
                'reason':'numeric_degenerate_absolute_projection',
                'source_candidate_index':source_index,
                **_diagnostic_evidence(raw[source_index] if 0<=source_index<len(raw) else item),
                'raw_time_axis':'analyzer_input' if 0<=source_index<len(raw) else 'core_relative',
                'owned_core_seconds':[start,end],'support_core_seconds':[support_start,support_end],
                'absolute_seconds':absolute,'timeline_core_origin':origin}]
            continue
        prepared.append((chunk['id'],offset,index,*absolute,STATUS,time_precision,precision,payload,row_provenance))
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
