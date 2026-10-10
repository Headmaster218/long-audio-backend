"""Durable unverified overlap candidates, isolated from transcript/voice banks."""
import json
import math
from . import overlap_diagnostics as diagnostics

REGION_PUBLIC=('id','chunk_id','overlap_group_id','window_start','start','end','support_start','support_end',
    'status','reason_code','detected_source_count','capacity','original_mixture_retained','continuity_status','shared_support_id','schema_error_count','raw_output_sha256','region_kind','model_label_count')
TRACK_PUBLIC=('id','chunk_id','region_id','source_track_id','overlap_group_id','window_start','start','end',
    'support_start','support_end','text','raw_text','scope_id','source_slot','identity_status','quality_status','reason_code')
PROVENANCE_PUBLIC=('model_id','model_revision','source_revision','weights_sha256','license','sample_rate',
    'capacity','device','window_mode','conditioning_local_id','conditioning_source','language_gate','source_identity_semantics','output_normalization','asr_backend','timestamp_semantics','enrollment_seconds','enrollment_source','enrollment_start','enrollment_end','enrollment_identity_verified','weights','runtime','asr_language','shared_support_id','related_overlap_group_ids','native_unit_kind','native_phrase_intervals','native_time_axis','boundary_uncertain','evidence_routing_note','association_basis','support_owner_anchor','model_local_speaker_label','multi_speaker_quality_unverified','possible_missing_minority','capacity_semantics','emitted_label_count','native_detected_label_count','count_mismatch_uncertain','model_sha256','dtype','cpu_threads','attention_implementation','prompt_sha256','audio_sha256','inference_run','native_peak_simultaneous_count','runtime_provider','runtime_version','runtime_source_revision','runtime_manifest_sha256','runtime_library_sha256','source_weight_sha256','requested_device','actual_backend','actual_device_kind','actual_device_name','generation_budget','generated_tokens','generation_token_count_known','native_output_truncated','precision','generation_budget_policy','fixed_512_token_parity','session_n_ctx','audio_token_count','prompt_token_count','finish_reason')


def init_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS overlap_regions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
      window_start REAL NOT NULL, local_group_id TEXT NOT NULL, overlap_group_id TEXT NOT NULL UNIQUE,
      start REAL NOT NULL,end REAL NOT NULL,support_start REAL NOT NULL,support_end REAL NOT NULL,
      status TEXT NOT NULL,reason_code TEXT NOT NULL DEFAULT 'unresolved',detected_source_count INTEGER NOT NULL,detected_speaker_local_ids_json TEXT NOT NULL,
      capacity INTEGER NOT NULL,original_mixture_retained INTEGER NOT NULL,continuity_status TEXT,shared_support_id TEXT,support_activity_json TEXT NOT NULL DEFAULT '{}',conditioning_evidence_json TEXT NOT NULL DEFAULT '[]',
      UNIQUE(chunk_id,window_start,local_group_id));
    CREATE INDEX IF NOT EXISTS overlap_regions_chunk ON overlap_regions(chunk_id,id);
    CREATE TABLE IF NOT EXISTS overlap_tracks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,region_id INTEGER NOT NULL REFERENCES overlap_regions(id) ON DELETE CASCADE,
      chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,window_start REAL NOT NULL,
      local_source_track_id TEXT NOT NULL,source_track_id TEXT NOT NULL UNIQUE,overlap_group_id TEXT NOT NULL,
      start REAL NOT NULL,end REAL NOT NULL,support_start REAL NOT NULL,support_end REAL NOT NULL,
      text TEXT NOT NULL,raw_text TEXT NOT NULL,scope_id TEXT NOT NULL,source_slot INTEGER NOT NULL,
      identity_status TEXT NOT NULL,quality_status TEXT NOT NULL,reason_code TEXT NOT NULL DEFAULT 'unverified_candidate_text',provenance_json TEXT NOT NULL,conditioning_evidence_json TEXT NOT NULL DEFAULT '{}',
      UNIQUE(chunk_id,window_start,local_source_track_id));
    CREATE INDEX IF NOT EXISTS overlap_tracks_region ON overlap_tracks(region_id,id);
    CREATE TABLE IF NOT EXISTS overlap_track_regions (
      track_id INTEGER NOT NULL REFERENCES overlap_tracks(id) ON DELETE CASCADE,
      region_id INTEGER NOT NULL REFERENCES overlap_regions(id) ON DELETE CASCADE,
      PRIMARY KEY(track_id,region_id));
    CREATE INDEX IF NOT EXISTS overlap_track_regions_lookup ON overlap_track_regions(region_id,track_id);
    CREATE TABLE IF NOT EXISTS overlap_support_diagnostics (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
      window_start REAL NOT NULL, diagnostic_key TEXT NOT NULL,
      shared_support_id TEXT, support_start REAL NOT NULL, support_end REAL NOT NULL,
      retention TEXT NOT NULL, envelope_json TEXT NOT NULL,
      retained_bytes INTEGER NOT NULL,
      UNIQUE(chunk_id,window_start,diagnostic_key));
    CREATE INDEX IF NOT EXISTS overlap_diagnostics_support ON overlap_support_diagnostics(chunk_id,window_start,shared_support_id);
    CREATE TABLE IF NOT EXISTS overlap_candidate_migrations (name TEXT PRIMARY KEY);
    ''')
    db.execute("INSERT OR IGNORE INTO overlap_candidate_migrations VALUES('private_support_diagnostics_v1')")
    for table in ('overlap_regions','overlap_tracks'):
        if 'reason_code' not in {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN reason_code TEXT NOT NULL DEFAULT 'unresolved'")
    if 'conditioning_evidence_json' not in {row['name'] for row in db.execute('PRAGMA table_info(overlap_tracks)')}:
        db.execute("ALTER TABLE overlap_tracks ADD COLUMN conditioning_evidence_json TEXT NOT NULL DEFAULT '{}'")
    for column,kind in [('schema_error_count','INTEGER'),('raw_output_sha256','TEXT'),('region_kind',"TEXT NOT NULL DEFAULT 'detected_overlap'"),('model_label_count','INTEGER')]:
        if column not in {row['name'] for row in db.execute('PRAGMA table_info(overlap_regions)')}:
            db.execute(f'ALTER TABLE overlap_regions ADD COLUMN {column} {kind}')
    if 'support_activity_json' not in {row['name'] for row in db.execute('PRAGMA table_info(overlap_regions)')}:
        db.execute("ALTER TABLE overlap_regions ADD COLUMN support_activity_json TEXT NOT NULL DEFAULT '{}'")
    if 'shared_support_id' not in {row['name'] for row in db.execute('PRAGMA table_info(overlap_regions)')}:
        db.execute('ALTER TABLE overlap_regions ADD COLUMN shared_support_id TEXT')
    if not db.execute("SELECT 1 FROM overlap_candidate_migrations WHERE name='initial_track_links_v1'").fetchone():
        db.execute('INSERT OR IGNORE INTO overlap_track_regions(track_id,region_id) SELECT id,region_id FROM overlap_tracks')
        db.execute("INSERT INTO overlap_candidate_migrations VALUES('initial_track_links_v1')")
    if 'conditioning_evidence_json' not in {row['name'] for row in db.execute('PRAGMA table_info(overlap_regions)')}:
        db.execute("ALTER TABLE overlap_regions ADD COLUMN conditioning_evidence_json TEXT NOT NULL DEFAULT '[]'")


def _small_text(value,maximum=512):
    if not isinstance(value,str) or len(value)>maximum:raise ValueError('Invalid overlap candidate string')
    return value


def _native_intervals(rows):
    if not isinstance(rows,list) or len(rows)>128:raise ValueError('Invalid native phrase interval count')
    result=[]
    for row in rows:
        unit={}
        for key in ('start','end','ownership_anchor'):
            value=row.get(key)
            if not isinstance(value,(float,int)) or not math.isfinite(value):raise ValueError('Invalid native phrase timing')
            unit[key]=value
        if not 0<=unit['end']-unit['start']<=60.01 or not unit['start']-.001<=unit['ownership_anchor']<=unit['end']+.001:
            raise ValueError('Invalid native phrase extent/anchor')
        if row.get('unit_kind')!='native_phrase':raise ValueError('Only explicit native phrase spans allowed')
        unit.update(text=_small_text(row.get('text',''),10000),unit_kind='native_phrase',
            boundary_uncertain=bool(row.get('boundary_uncertain',False)))
        if row.get('ownership_anchor_kind') is not None:unit['ownership_anchor_kind']=_small_text(row['ownership_anchor_kind'],100)
        result.append(unit)
    return result


def _provenance(value):
    if value.get('generation_token_count_known') is False and value.get('generated_tokens') is not None:
        raise ValueError('Unknown generation token count must remain null/unavailable')
    result={}
    for key in PROVENANCE_PUBLIC:
        v=value.get(key)
        if v is None:
            if key=='generated_tokens' and key in value:result[key]=None
            continue
        if key in ('generation_token_count_known','native_output_truncated','fixed_512_token_parity') and type(v) is not bool:raise ValueError('Invalid runtime boolean provenance')
        if key in ('session_n_ctx','audio_token_count','prompt_token_count') and (type(v) is not int or not 0<=v<=2048):raise ValueError('Invalid bounded native token metadata')
        if key=='generation_budget' and (type(v) is not int or not 1<=v<=512):raise ValueError('Invalid generation budget provenance')
        if key=='generated_tokens' and (type(v) is not int or not 0<=v<=512):raise ValueError('Invalid known generation count provenance')
        if key=='native_phrase_intervals':
            result[key]=_native_intervals(v)
            continue
        if key=='related_overlap_group_ids':
            if not isinstance(v,list) or len(v)>32:raise ValueError('Invalid related overlap groups')
            result[key]=list(dict.fromkeys(_small_text(item,512) for item in v))
            continue
        if isinstance(v,str):
            if len(v)>1024 or v.lstrip().startswith(('/', '\\')) or ':\\' in v or (len(v)>2 and v[0].isalpha() and v[1]==':' and v[2]=='/'):continue
        elif isinstance(v,(int,float,bool)):
            if isinstance(v,float) and not math.isfinite(v):raise ValueError('Nonfinite overlap provenance')
        else:continue
        result[key]=v
    return result


def _support_activity(value):
    if not value:return {}
    if not isinstance(value,dict):raise ValueError('Invalid recognition support activity')
    ids=value.get('speaker_local_ids',[])
    if not isinstance(ids,list) or len(ids)>4096:raise ValueError('Invalid support voice count')
    ids=list(dict.fromkeys(_small_text(sid,200) for sid in ids))
    count=value.get('max_simultaneous_speakers',0)
    if type(count) is not int or not 0<=count<=len(ids):raise ValueError('Invalid concurrent support voice count')
    return {'max_simultaneous_speakers':count,'distinct_local_speakers':len(ids),
        'speaker_local_ids':ids,'identity_scope':'analysis_window_local'}


def _conditioning_evidence(rows,chunk_id=None,window_start=None,resolved=None):
    if not isinstance(rows,list) or len(rows)>8:raise ValueError('Invalid conditioning evidence')
    result=[]
    for row in rows:
        item={}
        for key in ('conditioning_local_id','reason_code'):
            if row.get(key) is not None:item[key]=_small_text(row[key],200)
        seconds=row.get('enrollment_seconds')
        if seconds is not None:
            if not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or not 0<=seconds<=60:raise ValueError('Invalid clean enrollment duration')
            item['enrollment_seconds']=seconds
        if chunk_id is not None and item.get('conditioning_local_id'):
            item.update(_conditioning_scope(item['conditioning_local_id'],chunk_id,window_start,(resolved or {}).get(item['conditioning_local_id'])))
        elif 'speaker_window_start' in row:
            item.update(_public_conditioning(row))
        result.append(item)
    return result


def _conditioning_scope(local_id,chunk_id,window_start,resolution=None):
    local_id=_small_text(local_id,200)
    speaker=None;status='unknown';turn_ids=[]
    if isinstance(resolution,dict):
        speaker=resolution.get('speaker_id');status=resolution.get('speaker_status',resolution.get('status','unknown'))
        turn_ids=resolution.get('speaker_turn_ids',[])
        if not isinstance(turn_ids,list) or len(turn_ids)>4096 or any(type(x) is not int or x<=0 for x in turn_ids):raise ValueError('Invalid conditioning speaker-turn links')
        turn_ids=list(dict.fromkeys(turn_ids))
    elif isinstance(resolution,(tuple,list)) and len(resolution)>=3:
        speaker,status=resolution[0],resolution[2]
    if speaker is not None:speaker=_small_text(speaker,200)
    return {'conditioning_local_id':local_id,'chunk_id':_small_text(chunk_id,200),
        'speaker_window_start':float(window_start),'speaker_turn_ids':turn_ids,'conditioned_on_speaker_id':speaker,
        'raw_diarization_speaker_status':_small_text(status,100),
        'source':'raw_diarization_target_in_same_analysis_window',
        'independently_verified_source_identity':False}


def _public_conditioning(value):
    if not value:return {}
    if not isinstance(value,dict):raise ValueError('Invalid conditioning scope')
    seconds=value.get('speaker_window_start')
    if not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<0:raise ValueError('Invalid conditioning window')
    return _conditioning_scope(value.get('conditioning_local_id'),value.get('chunk_id'),seconds,
        {'speaker_id':value.get('conditioned_on_speaker_id'),'speaker_status':value.get('raw_diarization_speaker_status','unknown'),'speaker_turn_ids':value.get('speaker_turn_ids',[])})


def commit_candidates(db,chunk,offset,payload,metadata,resolved_local_identities=None):
    """Called inside Store's window transaction; retries cannot duplicate tracks."""
    if not payload:return
    from .overlap_candidates import validate_candidates
    duration=max(0,float(chunk['duration'])-offset)
    validate_candidates(payload,duration,owned=True)
    base=float(chunk['timeline_start'])+offset
    if any(base+item['start']<0 or base+item['support_start']<-.001 for item in payload.get('regions',[])+payload.get('tracks',[])):raise ValueError('Negative absolute overlap support')
    prefix=f"{chunk['id']}@{offset:.6f}/";region_ids={}
    # Same caller-owned transaction as candidate rows and chunk cursor. Never
    # commit here or swallow a write failure; a retry must not skip lost evidence.
    for item in payload.get('diagnostics',[]):
        value=dict(item, key=prefix+item['key'], time_axis='session_absolute',
            shared_support_id=prefix+item['shared_support_id'] if item['shared_support_id'] else None,
            support_start=base+item['support_start'],support_end=base+item['support_end'])
        if value['support_start'] < -.001:raise ValueError('Negative absolute diagnostic support')
        serialized=diagnostics.encoded(value)
        if len(serialized)>diagnostics.MAX_RECORD_BYTES:raise ValueError('Stored diagnostic exceeds byte budget')
        prior=db.execute('SELECT envelope_json FROM overlap_support_diagnostics WHERE chunk_id=? AND window_start=? AND diagnostic_key=?',
            (chunk['id'],offset,item['key'])).fetchone()
        if prior and prior[0]!=serialized.decode('utf-8'):
            raise ValueError('Conflicting diagnostic retry; original evidence retained')
        db.execute('''INSERT OR IGNORE INTO overlap_support_diagnostics(chunk_id,window_start,diagnostic_key,
            shared_support_id,support_start,support_end,retention,envelope_json,retained_bytes)
            VALUES(?,?,?,?,?,?,?,?,?)''',(chunk['id'],offset,item['key'],value['shared_support_id'],
            value['support_start'],value['support_end'],value['retention'],serialized.decode('utf-8'),len(serialized)))
    resolved_local_identities=resolved_local_identities or {}
    for region in payload.get('regions',[]):
        local=region['overlap_group_id'];gid=prefix+local
        status=_small_text(region.get('status','unresolved'),100)
        local_ids=region.get('detected_speaker_local_ids',[])
        if not isinstance(local_ids,list) or len(local_ids)>4096:raise ValueError('Invalid detected speaker list')
        local_ids=[_small_text(s,200) for s in local_ids]
        values=(chunk['id'],offset,local,gid,*[base+region[k] for k in ('start','end','support_start','support_end')],status,_small_text(region.get('reason_code','unresolved'),100),
            region['detected_source_count'],json.dumps(local_ids),int(region.get('capacity',2)),1,
            _small_text(region.get('continuity_status','unresolved'),100),
            prefix+_small_text(region['shared_support_id'],200) if region.get('shared_support_id') else None,
            json.dumps(_support_activity(region.get('support_activity',{}))),json.dumps(_conditioning_evidence(region.get('conditioning_evidence',[]),chunk['id'],offset,resolved_local_identities)),region.get('region_kind','detected_overlap'),region.get('model_label_count'))
        db.execute('''INSERT OR IGNORE INTO overlap_regions(chunk_id,window_start,local_group_id,overlap_group_id,
          start,end,support_start,support_end,status,reason_code,detected_source_count,detected_speaker_local_ids_json,
          capacity,original_mixture_retained,continuity_status,shared_support_id,support_activity_json,conditioning_evidence_json,region_kind,model_label_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',values)
        row=db.execute('SELECT id FROM overlap_regions WHERE chunk_id=? AND window_start=? AND local_group_id=?',(chunk['id'],offset,local)).fetchone()
        count=region.get('schema_error_count');digest=region.get('raw_output_sha256')
        if count is not None:
            if type(count) is not int or not 0<=count<=128:raise ValueError('Invalid bounded candidate schema count')
            db.execute('UPDATE overlap_regions SET schema_error_count=? WHERE id=? AND schema_error_count IS NULL',(count,row[0]))
        if digest is not None:
            if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('Invalid bounded candidate raw hash')
            db.execute('UPDATE overlap_regions SET raw_output_sha256=? WHERE id=? AND raw_output_sha256 IS NULL',(digest,row[0]))
        region_ids[local]=row[0]
    for track in payload.get('tracks',[]):
        group=track['overlap_group_id'];local=track['source_track_id']
        slot=track.get('source_slot')
        if type(slot) is not int or not 0<=slot<8:raise ValueError('Invalid bounded source slot')
        provenance=_provenance(track.get('provenance',{}))
        if 'native_phrase_intervals' in provenance:
            if provenance.get('native_time_axis') not in (None,'core_relative'):raise ValueError('Expected core-relative native phrase evidence at commit')
            for unit in provenance['native_phrase_intervals']:
                for key in ('start','end','ownership_anchor'):unit[key]+=base
                if unit['start']<0:raise ValueError('Negative absolute native phrase timestamp')
            provenance['native_time_axis']='session_absolute'
        related=[x for x in provenance.get('related_overlap_group_ids',[group]) if x in region_ids]
        related=list(dict.fromkeys([group,*related]))
        if 'related_overlap_group_ids' in provenance:provenance['related_overlap_group_ids']=[prefix+x for x in related]
        if provenance.get('association_basis')=='support_not_model_timing' and 'support_owner_anchor' in provenance:
            provenance['support_owner_anchor']+=base
        if provenance.get('shared_support_id'):provenance['shared_support_id']=prefix+provenance['shared_support_id']
        target=track.get('conditioning_local_id') or provenance.get('conditioning_local_id')
        conditioning=_conditioning_scope(target,chunk['id'],offset,resolved_local_identities.get(target)) if target else {}
        values=(region_ids[group],chunk['id'],offset,local,prefix+local,prefix+group,
            *[base+track[k] for k in ('start','end','support_start','support_end')],
            track.get('text',''),track.get('raw_text',track.get('text','')),
            _small_text(track.get('scope_id','unresolved'),200),slot,'unresolved',
            _small_text(track.get('quality_status','unverified'),100),_small_text(track.get('reason_code','unverified_candidate_text'),100),json.dumps(provenance),json.dumps(conditioning))
        db.execute('''INSERT OR IGNORE INTO overlap_tracks(region_id,chunk_id,window_start,local_source_track_id,
          source_track_id,overlap_group_id,start,end,support_start,support_end,text,raw_text,scope_id,source_slot,
          identity_status,quality_status,reason_code,provenance_json,conditioning_evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',values)
        saved=db.execute('SELECT id FROM overlap_tracks WHERE chunk_id=? AND window_start=? AND local_source_track_id=?',(chunk['id'],offset,local)).fetchone()
        for related_group in related:
            db.execute('INSERT OR IGNORE INTO overlap_track_regions(track_id,region_id) VALUES(?,?)',(saved[0],region_ids[related_group]))


def public_region(row):
    row=dict(row);result={k:row[k] for k in REGION_PUBLIC if k in row}
    result['original_mixture_retained']=True
    result['detected_speaker_local_ids']=json.loads(row.get('detected_speaker_local_ids_json','[]'))
    result['speaker_local_id_scope']='this analysis window only'
    result['support_activity']=_support_activity(json.loads(row.get('support_activity_json','{}')))
    result['conditioning_evidence']=_conditioning_evidence(json.loads(row.get('conditioning_evidence_json','[]')))
    return result


def public_track(row):
    row=dict(row);result={k:row[k] for k in TRACK_PUBLIC if k in row}
    conditioning=_public_conditioning(json.loads(row.get('conditioning_evidence_json','{}')))
    result.update(conditioning_evidence=conditioning,
        conditioned_on_speaker_id=conditioning.get('conditioned_on_speaker_id'),
        provenance=_provenance(json.loads(row.get('provenance_json','{}'))),
        speaker_id=None,profile_update_eligible=False,enrollment_available=False,
        identity_status='unresolved',candidate_only=True)
    return result


def candidates(store,sid,after=0,limit=100):
    """Region cursor; associated tracks stay with their region on each page."""
    store.session(sid);limit=max(1,min(500,int(limit)))
    regions=[];tracks=[];size=0;seen_tracks=set()
    with store.db() as db:
        rows=db.execute('''SELECT r.* FROM overlap_regions r JOIN chunks c ON c.id=r.chunk_id
          WHERE c.session_id=? AND r.id>? ORDER BY r.id LIMIT ?''',(sid,after,limit)).fetchall()
        for row in rows:
            region=public_region(row)
            associated=[public_track(t) for t in db.execute('SELECT t.* FROM overlap_tracks t JOIN overlap_track_regions l ON l.track_id=t.id WHERE l.region_id=? ORDER BY t.id',(row['id'],)) if t['id'] not in seen_tracks]
            increment=len(json.dumps([region,associated],allow_nan=False))
            if regions and size+increment>2*1024**2:break
            regions.append(region);tracks.extend(associated);seen_tracks.update(t['id'] for t in associated);size+=increment
    return {'regions':regions,'tracks':tracks,'next_cursor':regions[-1]['id'] if regions else after,
            'cursor_semantics':'overlap region ID; shared tracks may recur across pages with the same immutable track ID',
            'candidate_only':True,'speaker_identity_claim':False}


def private_diagnostics(store,sid,after=0,limit=10):
    """Explicit local/private export. Not mounted on the ordinary HTTP API.

    Immutable diagnostic-ID cursor; one shared support record regardless of the
    number of linked regions. Bounded materialization and UTF-8 response bytes.
    """
    store.session(sid);after=max(0,int(after));limit=max(1,min(50,int(limit)))
    result={'diagnostics':[],'next_cursor':after,'private':True,'candidate_only':True,
            'cursor_semantics':'immutable diagnostic ID; shared supports emitted once'}
    with store.db() as db:
        rows=db.execute("""SELECT d.* FROM overlap_support_diagnostics d
            JOIN chunks c ON c.id=d.chunk_id WHERE c.session_id=? AND d.id>?
            ORDER BY d.id LIMIT ?""",(sid,after,limit))
        for row in rows:
            item={'id':row['id'],'chunk_id':row['chunk_id'],'window_start':row['window_start'],
                  'retained_bytes':row['retained_bytes'],'envelope':json.loads(row['envelope_json'])}
            previous_cursor=result['next_cursor']
            result['diagnostics'].append(item);result['next_cursor']=row['id']
            if len(diagnostics.encoded(result))>diagnostics.MAX_PAGE_BYTES:
                result['diagnostics'].pop();result['next_cursor']=previous_cursor;break
    return result


def private_diagnostic_status(store,sid,after=0,limit=100):
    """Bounded region references plus truthful historical absence labels.

    Historical hashes are not recoverable text. Valid phrase raw_text is not a
    full generation envelope. This private projection changes no public fields.
    """
    store.session(sid);after=max(0,int(after));limit=max(1,min(500,int(limit)))
    result={'regions':[],'next_cursor':after,'private':True}
    with store.db() as db:
        rows=db.execute("""SELECT r.* FROM overlap_regions r JOIN chunks c ON c.id=r.chunk_id
            WHERE c.session_id=? AND r.id>? ORDER BY r.id LIMIT ?""",(sid,after,limit))
        for row in rows:
            item={'region_id':row['id'],'shared_support_id':row['shared_support_id'],
                  'diagnostic_id':None,'batch_diagnostic_id':None,
                  'full_raw_output_available':False,'schema_error_details_available':False,
                  'diagnostic_phrases_available':False}
            saved=db.execute("""SELECT id,envelope_json FROM overlap_support_diagnostics
                WHERE chunk_id=? AND window_start=? AND shared_support_id=?""",
                (row['chunk_id'],row['window_start'],row['shared_support_id'])).fetchone()
            batch=db.execute("""SELECT id FROM overlap_support_diagnostics
                WHERE chunk_id=? AND window_start=? AND diagnostic_key='batch-failure'""",
                (row['chunk_id'],row['window_start'])).fetchone()
            if batch:item['batch_diagnostic_id']=batch['id']
            if saved:
                evidence=json.loads(saved['envelope_json']);captures=evidence['captures']
                item.update(diagnostic_id=saved['id'],diagnostic_retention=evidence['retention'],
                    unavailability_reason=None if evidence['retention']=='complete' else 'see_private_capture_status',
                    capture_retention={key:value['retention'] for key,value in captures.items()})
                for field,capture in [('full_raw_output_available','raw_text'),('schema_error_details_available','schema_errors'),('diagnostic_phrases_available','diagnostic_phrases')]:
                    item[field]=captures[capture]['retention']=='complete'
            elif batch:
                item.update(diagnostic_retention='batch_failure_only',unavailability_reason='support_envelope_unavailable')
            else:
                phrase=db.execute('SELECT 1 FROM overlap_track_regions WHERE region_id=? LIMIT 1',(row['id'],)).fetchone()
                kind=('hash_and_counts_only' if row['raw_output_sha256'] is not None or row['schema_error_count'] is not None else
                      'phrase_raw_text_only' if phrase else 'status_and_reason_only')
                item.update(diagnostic_retention=kind,unavailability_reason='not_persisted_by_this_backend_version')
            previous_cursor=result['next_cursor']
            result['regions'].append(item);result['next_cursor']=row['id']
            if len(diagnostics.encoded(result))>diagnostics.MAX_PAGE_BYTES:
                result['regions'].pop();result['next_cursor']=previous_cursor;break
    return result
