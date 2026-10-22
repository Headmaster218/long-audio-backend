"""Explicit exact-crop acoustic references and optional primary suggestions.

Manual identity labels are not enrollment. References need an additional clean-
voice assertion and a currently reviewable, verified crop. Suggestions never
become references or relabel transcript words. All durable state is bounded.
"""
import base64
import hashlib
import json
import math
import sqlite3
import struct
import time
import uuid
from pathlib import Path

from . import reviewed_primary_persons as bridge, verified_identity as bank
from . import recording_speaker_history as history, speaker_support as support
from . import support_vectors, identity_canonical
from .primary_crop_support import POLICY as CROP_POLICY, LEGACY_POLICY

TABLE = 'identity_primary_voice_references_v1'
SUGGESTIONS = 'identity_primary_voice_suggestions_v1'
MAX_SUGGESTIONS = 4096
MAX_QUERY_REFERENCES = 64
ACTIONS = ('enroll_primary_voice', 'retract_primary_voice', 'revoke_primary_voice')
ATTESTATION = {'version':'reviewed_exact_primary_voice_v1', 'reviewed_exact_crop':True,
               'single_identified_voice':True, 'no_other_overlapping_voice':True}
DDL = {
TABLE: f'''CREATE TABLE {TABLE} (
 event_id TEXT PRIMARY KEY REFERENCES identity_review_events(id) ON DELETE CASCADE,
 association_event_id TEXT NOT NULL REFERENCES identity_primary_associations_v1(event_id),
 identity_group TEXT NOT NULL, generation TEXT NOT NULL, person_id TEXT NOT NULL,
 source_session_id TEXT NOT NULL, source_recording_id TEXT NOT NULL,
 observation_id TEXT NOT NULL, certificate_id TEXT NOT NULL, vector_sha256 TEXT NOT NULL,
 binding_sha256 TEXT NOT NULL, space_id TEXT NOT NULL, physical_crop_key TEXT NOT NULL, source_version TEXT NOT NULL, evidence_key TEXT NOT NULL,
 UNIQUE(identity_group,generation,observation_id))''',
'primary_voice_group': f'CREATE INDEX primary_voice_group ON {TABLE}(identity_group,generation,person_id,event_id)',
'primary_voice_evidence': f'CREATE INDEX primary_voice_evidence ON {TABLE}(identity_group,evidence_key)',
'primary_voice_source': f'CREATE INDEX primary_voice_source ON {TABLE}(source_session_id,source_recording_id,observation_id)',
SUGGESTIONS: f'''CREATE TABLE {SUGGESTIONS} (
 id INTEGER PRIMARY KEY AUTOINCREMENT, identity_group TEXT NOT NULL, generation TEXT NOT NULL,
 observation_id TEXT NOT NULL, source_session_id TEXT NOT NULL, source_recording_id TEXT NOT NULL,
 profile_revision INTEGER NOT NULL, availability INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','complete')),
 outcome TEXT CHECK(outcome IS NULL OR length(CAST(outcome AS BLOB))<=8192),
 UNIQUE(identity_group,generation,observation_id,profile_revision,availability))''',
'primary_voice_pending': f'CREATE INDEX primary_voice_pending ON {SUGGESTIONS}(state,id)',
'primary_voice_suggestion_source': f'CREATE INDEX primary_voice_suggestion_source ON {SUGGESTIONS}(source_session_id,source_recording_id,observation_id)',
'primary_voice_outcome_immutable': f'''CREATE TRIGGER primary_voice_outcome_immutable BEFORE UPDATE ON {SUGGESTIONS}
 WHEN OLD.state='complete' OR NEW.identity_group IS NOT OLD.identity_group OR NEW.generation IS NOT OLD.generation
 OR NEW.id IS NOT OLD.id OR NEW.state!='complete' OR NEW.outcome IS NULL
 OR NEW.observation_id IS NOT OLD.observation_id OR NEW.profile_revision IS NOT OLD.profile_revision
 OR NEW.availability IS NOT OLD.availability OR NEW.source_session_id IS NOT OLD.source_session_id
 OR NEW.source_recording_id IS NOT OLD.source_recording_id
 BEGIN SELECT RAISE(ABORT,'Primary identity suggestion is immutable'); END''',
}

# Revocation is not ordinary PCM retirement. Reserved review slots allow a
# lifecycle tombstone without blocking deletion at an already-full gallery.
DDL['primary_voice_source_revocation'] = f"""CREATE TRIGGER primary_voice_source_revocation
 AFTER UPDATE OF state ON {history.SOURCES} WHEN NEW.state='revoked' AND OLD.state!='revoked'
 BEGIN
 INSERT INTO identity_review_events
 (id,identity_group,generation,sequence,action,person_id,operator_context,previous_event_id,
 response,created,source_session_id,source_recording_id,primary_observation_id)
 SELECT lower(hex(randomblob(16))),v.identity_group,v.generation,p.revision+1,'revoke_primary_voice',
 v.person_id,'source_lifecycle',v.event_id,'{{}}',(julianday('now')-2440587.5)*86400,
 v.source_session_id,v.source_recording_id,v.observation_id
 FROM {TABLE} v JOIN identity_group_policy p ON p.identity_group=v.identity_group WHERE v.source_version=NEW.version;
 UPDATE identity_group_policy SET
 revision=CASE WHEN typeof(revision)='integer' AND revision BETWEEN 0 AND 9223372036854775806
 THEN revision+1 ELSE RAISE(ABORT,'Reviewed revision exhausted') END,
 availability=CASE WHEN typeof(availability)='integer' AND availability BETWEEN 0 AND 9223372036854775806
 THEN availability+1 ELSE RAISE(ABORT,'Reviewed availability exhausted') END,change_reason='primary_voice_source_revoked'
 WHERE identity_group IN (SELECT identity_group FROM {TABLE} WHERE source_version=NEW.version);
 DELETE FROM {TABLE} WHERE source_version=NEW.version;
 END"""


def present(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)).fetchone())


def init_schema(db, *, enable=False):
    if not db.in_transaction:raise ValueError('Primary voice migration requires transaction')
    actual = history._schema_sql(db,DDL)
    if actual:
        if not history._same_schema(actual,DDL):raise ValueError('Incompatible primary voice schema')
        return
    if not enable:
        current=history._schema_sql(db,{history.BRIDGE_ERASE_GUARD})
        if history._same_schema(current,{history.BRIDGE_ERASE_GUARD:history.REFERENCE_BRIDGE_GUARD}):
            raise ValueError('Incomplete primary voice schema')
        return
    if not bridge.validate_schema(db):raise ValueError('Reviewed person bridge required')
    for sql in DDL.values():db.execute(sql)
    # The changed known history guard makes an old dev61 writer refuse startup.
    db.execute('DROP TRIGGER '+history.BRIDGE_ERASE_GUARD)
    db.execute(history.REFERENCE_BRIDGE_GUARD)


def _require(db):
    if not present(db):bridge.fail('primary_voice_references_disabled',404)


def _certificate(db, bound):
    """Metadata binding alone cannot certify the original vector bytes."""
    b=bound.get('binding')
    if (not b or not bound['review_eligible'] or not bound['vector_available']
            or b['evidence_kind']!='vector'):bridge.fail('primary_vector_unavailable')
    sizes=db.execute(f'''SELECT typeof(vector),length(vector),certificate_id,vector_sha256
        FROM {history.CERTIFICATES} WHERE observation_id=?''',(b['observation_id'],)).fetchone()
    if (sizes is None or tuple(sizes[:2])!=('blob',2048) or sizes['certificate_id']!=b['certificate_id']
            or sizes['vector_sha256']!=b['vector_sha256']):bridge.fail('primary_vector_integrity',503)
    raw=db.execute(f'SELECT vector FROM {history.CERTIFICATES} WHERE observation_id=?',(b['observation_id'],)).fetchone()[0]
    if len(raw)!=2048 or hashlib.sha256(raw).hexdigest()!=b['vector_sha256']:bridge.fail('primary_vector_integrity',503)
    values=list(struct.unpack('<512f',raw))
    try:vector,_=support_vectors.normalized_vector(values)
    except ValueError:bridge.fail('primary_vector_integrity',503)
    # binding() validated the exact descriptor and its digest, not merely a model name.
    return vector


def _float_digest(pcm):
    # Geometry is taken from the validated certificate, within this endpoint's
    # fixed 2-second transport ceiling, never padded/truncated to that ceiling.
    digest=hashlib.sha256();block=bytearray(4096)
    for start in range(0,len(pcm),2048):
        count=min(2048,len(pcm)-start)//2
        for index in range(count):
            struct.pack_into('<f',block,index*4,struct.unpack_from('<h',pcm,start+index*2)[0]/32768.)
        digest.update(memoryview(block)[:count*4])
    return digest.hexdigest()


def _audio(store,db,bound):
    """Read only the exact retained PCM crop through existing owned-file checks."""
    from .native_owned_cell_store import Budget,Receipt
    from .native_owned_cell_capture import _owned_path,_open,_read_frames,_continuity,riff_geometry
    from .native_owned_cells import Unavailable
    b=bound.get('binding')
    if not b or not bound['review_eligible'] or bound['source_state']!='human_associated':bridge.fail('primary_audio_unavailable')
    row=db.execute(f'''SELECT s.chunk_id,s.state,o.body AS receipt,c.path,c.session_id,c.digest,c.status
       FROM {history.SOURCES} s JOIN chunks c ON c.id=s.chunk_id
       JOIN {history.SCOPES} h ON h.scope_seq=s.scope_seq
       JOIN {history.OBJECTS} o ON o.scope_seq=s.scope_seq AND o.kind='receipt' AND o.digest=?
       WHERE s.version=? AND c.session_id=? AND h.scope_id=?''',
       (b['receipt_sha256'],b['source_version'],b['source_session_id'],b['scope_id'])).fetchone()
    # Source receipt column is selected only after a byte/type screen.
    if row is None or row['state']!='active' or row['status'] in ('cancelled','failed','staging'):
        bridge.fail('primary_audio_unavailable')
    try:
        receipt=Receipt.decode(row['receipt'])
        physical=b['physical_crop']; start=physical['source_start_frame']; end=physical['source_end_frame']
        if (not 0<end-start<=32000 or receipt.receipt_id!=b['receipt_sha256']
                or receipt.digest!=physical['source_sha256'] or receipt.frames!=physical['source_frames']
                or receipt.session_id!=b['source_session_id'] or receipt.chunk_id!=row['chunk_id']
                or row['digest']!=receipt.digest or not 0<=start<end<=receipt.frames):
            bridge.fail('primary_audio_binding_changed')
        path=_owned_path(row['path'],Path(store.settings.data_dir).resolve()/'uploads','.audio')
        budget=Budget()
        with budget.phase():
            fd,signature=_open(path,budget)
            try:
                if signature!=receipt.signature:bridge.fail('primary_audio_binding_changed')
                offset,frames=riff_geometry(fd,signature[2],budget)
                if (offset,frames)!=(receipt.data_offset,receipt.frames):bridge.fail('primary_audio_binding_changed')
                pcm=_read_frames(fd,offset+start*2,end-start,budget)
                _continuity(fd,path,signature,budget)
            finally:fd.close()
        if (hashlib.sha256(pcm).hexdigest()!=physical['pcm16le_sha256'] or
                _float_digest(pcm)!=physical['float32le_sha256']):bridge.fail('primary_audio_binding_changed')
        return pcm
    except (OSError,ValueError,TypeError,Unavailable):bridge.fail('primary_audio_unavailable')


@bridge._operation
def review_audio(store,sid,rid,observation_id):
    with bridge._db(store) as (db,check):
        _require(db);bound=bridge._binding(db,sid,rid,observation_id)
        pcm=_audio(store,db,bound);check()
        return {'observation_id':observation_id,'binding_sha256':bound['binding_sha256'],
            'pcm16le_sha256':bound['binding']['physical_crop']['pcm16le_sha256'],
            'sample_rate':16000,'channels':1,'frames':len(pcm)//2,'encoding':'pcm_s16le_base64',
            'audio':base64.b64encode(pcm).decode('ascii'),'authority':'exact_crop_review_only'}


def acoustic_key(bound):
    b=bound['binding'];p=b['physical_crop']
    return support.digest(support.canonical({'pcm_sha256':p['float32le_sha256'],
        'encoding':support.POOLED_HASH_ENCODING,'sample_count':p['source_end_frame']-p['source_start_frame'],
        'space_id':b['space_id']}))


def _reference_row(db,event,group,generation):
    row=db.execute(f'SELECT * FROM {TABLE} WHERE event_id=? AND identity_group=? AND generation=?',(event,group,generation)).fetchone()
    if row is None:bridge.fail('active_primary_reference_required')
    return row


def reference_vector(db,row):
    claim=db.execute(f'SELECT * FROM {bridge.TABLE} WHERE event_id=?',(row['association_event_id'],)).fetchone()
    event=db.execute('SELECT action,person_id,fingerprints FROM identity_review_events WHERE id=?',(row['event_id'],)).fetchone()
    if (claim is None or event is None or event['action']!='enroll_primary_voice' or
            event['person_id']!=row['person_id'] or claim['person_id']!=row['person_id']):
        bridge.fail('primary_reference_integrity',503)
    bound=bridge._binding(db,row['source_session_id'],row['source_recording_id'],row['observation_id'])
    b=bound['binding']
    expected={k:row[k] for k in ('association_event_id','certificate_id','vector_sha256','binding_sha256','space_id','physical_crop_key','evidence_key')}
    if (bridge._decode(event['fingerprints'],bridge.MAX_BINDING)!=expected or not b or
            bound['binding_sha256']!=row['binding_sha256'] or b['certificate_id']!=row['certificate_id']
            or b['vector_sha256']!=row['vector_sha256'] or b['space_id']!=row['space_id']
            or bound['physical_crop_key']!=row['physical_crop_key'] or acoustic_key(bound)!=row['evidence_key']):bridge.fail('primary_reference_integrity',503)
    return _certificate(db,bound)


@bridge._operation
def enroll(store,group,request):
    bridge._shape(request,bridge.CAS_FIELDS|{'association_event_id','binding_sha256','certificate_id',
        'vector_sha256','space_id','attestation'})
    attestation=request['attestation']
    if (type(attestation) is not dict or attestation!=ATTESTATION or
            any(type(attestation.get(key)) is not bool for key in ATTESTATION if key!='version')):
        bridge.fail('explicit_clean_crop_attestation_required')
    bridge._identifier(request['association_event_id'],event=True)
    with bridge._db(store,write=True) as (db,check):
        _require(db);group,generation,policy=bridge._context(db,group)
        hashed,retry=bridge._request(db,group,generation,policy,request,'enroll_primary_voice',request['association_event_id'])
        if retry is not None:
            active=bool(db.execute(f'SELECT 1 FROM {TABLE} WHERE event_id=?',(retry['event_id'],)).fetchone())
            return {**retry,'active':active,'idempotent_replay':True}
        bridge._require_projection(db,group,generation)
        if not projection_intact(db,group,generation):bridge.fail('primary_reference_integrity',503)
        claim=db.execute(f'''SELECT * FROM {bridge.TABLE} WHERE event_id=? AND identity_group=? AND generation=?''',
            (request['association_event_id'],group,generation)).fetchone()
        if claim is None:bridge.fail('active_primary_association_required')
        bound=bridge._binding(db,claim['source_session_id'],claim['source_recording_id'],claim['observation_id'])
        b=bound['binding']
        if not b:bridge.fail('primary_evidence_unavailable')
        expected={'binding_sha256':bound['binding_sha256'],**{k:b[k] for k in ('certificate_id','vector_sha256','space_id')}}
        if any(request[k]!=v for k,v in expected.items()):bridge.fail('primary_evidence_binding_changed')
        _certificate(db,bound);_audio(store,db,bound)
        params=json.loads(policy['policy']);physical=b['physical_crop']
        if (physical['source_end_frame']-physical['source_start_frame'])/16000<params['minimum_enrollment_seconds']:
            bridge.fail('insufficient_primary_enrollment_duration')
        if policy['space_id'] is not None and policy['space_id']!=b['space_id']:bridge.fail('incompatible_embedding_space')
        if db.execute(f'SELECT 1 FROM {TABLE} WHERE identity_group=? AND physical_crop_key=?',(group,bound['physical_crop_key'])).fetchone():
            bridge.fail('duplicate_primary_voice_reference')
        canonical=bridge._canonical(db,group,generation,policy)
        key=acoustic_key(bound)
        bank.check_evidence_conflicts(db,group,'positive',claim['person_id'],key,canonical)
        bank._capacity(db,'positive',claim['person_id'],canonical=canonical)
        event_id=uuid.uuid4().hex
        fingerprints={'association_event_id':claim['event_id'],**expected,'physical_crop_key':bound['physical_crop_key'],'evidence_key':key}
        response={'event_id':event_id,'action':'enroll_primary_voice','person_id':claim['person_id'],
            'association_event_id':claim['event_id'],'observation_id':claim['observation_id'],
            'generation':generation,'profile_revision':policy['revision']+1,'active':True,
            'operator_assertion_is_claim':True,'automatic_enrollment':False}
        bridge._insert_event(db,group,generation,policy['revision']+1,'enroll_primary_voice',request,hashed,response,
            event_id=event_id,person=claim['person_id'],previous=claim['event_id'],binding=fingerprints,
            source=(claim['source_session_id'],claim['source_recording_id'],claim['observation_id']),attestation=ATTESTATION)
        db.execute(f'INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (event_id,claim['event_id'],group,generation,claim['person_id'],claim['source_session_id'],
             claim['source_recording_id'],claim['observation_id'],b['certificate_id'],b['vector_sha256'],
             bound['binding_sha256'],b['space_id'],bound['physical_crop_key'],b['source_version'],key))
        db.execute('UPDATE identity_group_policy SET space_id=COALESCE(space_id,?) WHERE identity_group=?',(b['space_id'],group))
        bank._invalidate(db,group,'primary_voice_reference_changed');check()
        return response


@bridge._operation
def retract(store,group,event_id,request):
    bridge._shape(request,bridge.CAS_FIELDS);bridge._identifier(event_id,event=True)
    with bridge._db(store,write=True) as (db,check):
        _require(db);group,generation,policy=bridge._context(db,group)
        hashed,retry=bridge._request(db,group,generation,policy,request,'retract_primary_voice',event_id)
        if retry is not None:return retry
        row=_reference_row(db,event_id,group,generation);bank.event_capacity(db,removed_active=1)
        new=uuid.uuid4().hex
        response={'event_id':new,'previous_event_id':event_id,'generation':generation,
            'profile_revision':policy['revision']+1,'active':False,'action':'retract_primary_voice'}
        bridge._insert_event(db,group,generation,policy['revision']+1,'retract_primary_voice',request,hashed,response,
            event_id=new,person=row['person_id'],previous=event_id,
            source=(row['source_session_id'],row['source_recording_id'],row['observation_id']))
        db.execute(f'DELETE FROM {TABLE} WHERE event_id=?',(event_id,))
        bank._invalidate(db,group,'primary_voice_reference_changed');return response


def revoke_association(db,event_id):
    if not present(db):return
    for row in db.execute(f'SELECT * FROM {TABLE} WHERE association_event_id=?',(event_id,)).fetchall():
        policy=bank.group_policy(db,row['identity_group'])
        bank.event_capacity(db,removed_active=1)
        new=uuid.uuid4().hex
        # Lifecycle revocations are ledger events, never replayable enrollment.
        request={'idempotency_key':None}
        response={'event_id':new,'previous_event_id':row['event_id'],'active':False,'action':'revoke_primary_voice'}
        bridge._insert_event(db,row['identity_group'],row['generation'],policy['revision']+1,
            'revoke_primary_voice',request,None,response,event_id=new,person=row['person_id'],previous=row['event_id'],
            source=(row['source_session_id'],row['source_recording_id'],row['observation_id']))
        db.execute(f'DELETE FROM {TABLE} WHERE event_id=?',(row['event_id'],))


def erase_observation(db,group,generation,observation_id):
    if not present(db):return
    db.execute(f'DELETE FROM {TABLE} WHERE identity_group=? AND generation=? AND observation_id=?',(group,generation,observation_id))
    db.execute(f'DELETE FROM {SUGGESTIONS} WHERE identity_group=? AND generation=? AND observation_id=?',(group,generation,observation_id))
    db.execute("DELETE FROM identity_review_events WHERE identity_group=? AND generation=? AND primary_observation_id=? AND action IN (?,?,?)",
        (group,generation,observation_id,*ACTIONS))


def reset(db,group):
    if not present(db):return
    db.execute(f'DELETE FROM {TABLE} WHERE identity_group=?',(group,))
    db.execute(f'DELETE FROM {SUGGESTIONS} WHERE identity_group=?',(group,))


def erase_source(db,sid,rid=None):
    if not present(db):return
    predicate='source_session_id=?'+(' AND source_recording_id=?' if rid else '')
    args=(sid,rid) if rid else (sid,)
    db.execute(f'DELETE FROM {SUGGESTIONS} WHERE {predicate}',args)
    # Association erasure owns reference-event removal; pure query sources also
    # disappear without affecting other recordings or their manual claims.


def pending_hint(store):
    """Indexed metadata-only scheduling hint, no proof, scoring or state write."""
    try:
        with bridge._db(store) as (db,check):
            return present(db) and db.execute(f"SELECT 1 FROM {SUGGESTIONS} INDEXED BY primary_voice_pending WHERE state='pending' LIMIT 1").fetchone() is not None
    except (bridge.BridgeError,sqlite3.Error):return False


def enqueue(db,sid,rid,observation_id):
    """Cheap durable optional intent in the certificate's transaction, no scoring."""
    if not present(db):return
    row=db.execute('''SELECT p.*,s.identity_group FROM sessions s JOIN identity_group_policy p
        ON p.identity_group=s.identity_group WHERE s.id=? AND s.deleted=0 AND p.mode=?''',(sid,bank.MODE)).fetchone()
    if row is None or not row['primary_associations_enabled']:return
    generation=support.generation(db,row['identity_group'])
    if db.execute(f'SELECT COUNT(*) FROM {SUGGESTIONS}').fetchone()[0]>=MAX_SUGGESTIONS:
        db.execute(f"DELETE FROM {SUGGESTIONS} WHERE id IN (SELECT id FROM {SUGGESTIONS} WHERE state='complete' ORDER BY id LIMIT 16)")
    if db.execute(f'SELECT COUNT(*) FROM {SUGGESTIONS}').fetchone()[0]>=MAX_SUGGESTIONS:return
    db.execute(f'''INSERT OR IGNORE INTO {SUGGESTIONS}
        (identity_group,generation,observation_id,source_session_id,source_recording_id,profile_revision,availability,state)
        VALUES(?,?,?,?,?,?,?,'pending')''',(row['identity_group'],generation,observation_id,sid,rid,row['revision'],row['availability']))


def projection_intact(db,group,generation):
    if not present(db):return True
    expected="""SELECT e.id,e.person_id,e.fingerprints,e.source_session_id,e.source_recording_id,e.primary_observation_id
        FROM identity_review_events e WHERE e.identity_group=? AND e.generation=? AND e.action='enroll_primary_voice'
        AND NOT EXISTS(SELECT 1 FROM identity_review_events x WHERE x.previous_event_id=e.id
          AND x.identity_group=e.identity_group AND x.generation=e.generation
          AND x.action IN ('retract_primary_voice','revoke_primary_voice'))"""
    if db.execute(f"""SELECT 1 FROM ({expected}) e LEFT JOIN {TABLE} v ON v.event_id=e.id
        WHERE v.event_id IS NULL OR v.person_id IS NOT e.person_id OR v.identity_group IS NOT ? OR v.generation IS NOT ?
        OR v.source_session_id IS NOT e.source_session_id OR v.source_recording_id IS NOT e.source_recording_id
        OR v.observation_id IS NOT e.primary_observation_id OR NOT json_valid(e.fingerprints)
        OR CASE WHEN json_valid(e.fingerprints) THEN json_extract(e.fingerprints,'$.evidence_key') END IS NOT v.evidence_key LIMIT 1""",(group,generation,group,generation)).fetchone():return False
    return not db.execute(f"""SELECT 1 FROM {TABLE} v LEFT JOIN identity_review_events e ON e.id=v.event_id
        WHERE v.identity_group=? AND (e.id IS NULL OR e.action!='enroll_primary_voice' OR e.generation IS NOT ?
        OR e.identity_group IS NOT ? OR EXISTS(SELECT 1 FROM identity_review_events x WHERE x.previous_event_id=e.id
          AND x.identity_group=e.identity_group AND x.generation=e.generation
          AND x.action IN ('retract_primary_voice','revoke_primary_voice'))) LIMIT 1""",(group,generation,group)).fetchone()


def _gallery_count(db,group):
    count=db.execute("SELECT COUNT(*) FROM identity_active_reviews WHERE identity_group=? AND kind='positive'",(group,)).fetchone()[0]
    count+=db.execute(f'SELECT COUNT(*) FROM {TABLE} WHERE identity_group=?',(group,)).fetchone()[0]
    return count

def _gallery(db,group,generation,space,canonical):
    if _gallery_count(db,group)>MAX_QUERY_REFERENCES:bridge.fail('primary_query_gallery_capacity',503)
    if not bank.projection_intact(db,group,generation) or not projection_intact(db,group,generation):bridge.fail('unknown_reference_integrity',503)
    rows=bank.active_reference_rows(db,'positive',group)
    rows=sorted(rows,key=lambda row:(canonical['mapping'].get(row['person_id'],''),row['event_id']))
    person=None;vectors=[];events=[]
    for row in rows:
        if row['generation']!=generation or row['space_id']!=space:continue
        root=canonical['mapping'].get(row['person_id'])
        if root is None:bridge.fail('unknown_reference_integrity',503)
        if root!=person and person is not None:
            yield person,vectors,events
            vectors=[];events=[]
        person=root
        if len(vectors)>=bank.MAX_PER_PERSON:bridge.fail('unknown_reference_integrity',503)
        vector=reference_vector(db,row) if row.get('reference_type')=='primary' else bank.reference_vector(db,row)[1]
        vectors.append(vector);events.append(row['event_id'])
    if person is not None:yield person,vectors,events


def _compute(db,job,policy):
    params=json.loads(policy['policy'])
    overlap_policy=bank.primary_query_policy(params)
    if support.digest(support.canonical(params))!=policy['policy_sha256']:
        bridge.fail('unknown_query_policy_integrity',503)
    bound=bridge._binding(db,job['source_session_id'],job['source_recording_id'],job['observation_id'],
        include_native_crop_support=overlap_policy==CROP_POLICY)
    vector=_certificate(db,bound);b=bound['binding']
    outcome={'authority':'automatic_suggestion','extent':'observed_crop_only','experimental':True,
        'observation_id':job['observation_id'],'binding_sha256':bound['binding_sha256'],
        'source_authority_revision':bound['source_authority_revision'],'source_revision':bound['source_revision'],
        'generation':job['generation'],'profile_revision':policy['revision'],'availability_sequence':policy['availability'],
        'space_id':b['space_id'],'person_id':None,'candidate_person_id':None,'score':None,'margin':None,
        'reference_event_ids':[],'status':'unknown_no_reviewed_references','automatic_enrollment':False}
    if overlap_policy==CROP_POLICY:
        outcome.update(primary_query_overlap_policy=overlap_policy,query_policy_sha256=policy['policy_sha256'])
    if policy['space_id'] is not None and b['space_id']!=policy['space_id']:
        outcome['status']='unknown_embedding_space';return outcome
    p=b['physical_crop']
    if (p['source_end_frame']-p['source_start_frame'])/16000<params['minimum_query_seconds']:
        outcome['status']='unknown_insufficient_speech';return outcome
    # binding() rederives the complete immutable packet witnesses and count.
    witness=db.execute(f'''SELECT a.witness_count FROM {history.ATTEMPTS} a
        JOIN {history.CERTIFICATES} c ON c.attempt=a.token WHERE c.observation_id=?''',
        (job['observation_id'],)).fetchone()
    if witness is None or type(witness[0]) is not int or witness[0]<0:
        outcome['status']='unknown_packet_overlap';return outcome
    if overlap_policy==LEGACY_POLICY and witness[0]!=0:
        outcome['status']='unknown_packet_overlap';return outcome
    if overlap_policy==CROP_POLICY:
        crop_support=bound.get('_native_crop_support')
        if not crop_support or crop_support.get('policy')!=CROP_POLICY:
            outcome['status']='unknown_crop_support';return outcome
        outcome['crop_support_extent']='native_metadata_only'
        outcome['acoustic_purity_certified']=False
        outcome['packet_overlap_witness_count']=witness[0]
    if _gallery_count(db,job['identity_group'])>MAX_QUERY_REFERENCES:
        outcome['status']='unknown_gallery_capacity';return outcome
    canonical=bridge._canonical(db,job['identity_group'],job['generation'],policy)
    try:
        scores=[(bank.centroid_score(vectors,vector),person,events)
                for person,vectors,events in _gallery(db,job['identity_group'],job['generation'],b['space_id'],canonical)]
    except (bridge.BridgeError,ValueError,TypeError,KeyError) as exc:
        outcome['status']='unknown_gallery_capacity' if getattr(exc,'reason',None)=='primary_query_gallery_capacity' else 'unknown_reference_integrity'
        return outcome
    selected=bank.select_reviewed_score(scores,params)
    outcome.update(selected)
    # Exact acoustic negative uses the existing PCM/space evidence-key contract,
    # without fabricating a legacy support or adding a negative from prediction.
    key=support.digest(support.canonical({'pcm_sha256':p['float32le_sha256'],
        'encoding':support.POOLED_HASH_ENCODING,'sample_count':p['source_end_frame']-p['source_start_frame'],
        'space_id':b['space_id']}))
    for negative in db.execute('''SELECT * FROM identity_active_reviews WHERE identity_group=?
            AND generation=? AND kind='negative' AND evidence_key=?''',
            (job['identity_group'],job['generation'],key)):
        if canonical['mapping'].get(negative['person_id'])==outcome['candidate_person_id']:
            bank.reference_vector(db,negative)
            outcome.update(person_id=None,status='unknown_exact_negative',negative_event_id=negative['event_id'])
    # A conflicting manual claim is an abstention, not a reason to rewrite it.
    if outcome['person_id'] is not None:
        for claim in db.execute(f'SELECT person_id FROM {bridge.TABLE} WHERE identity_group=? AND generation=? AND physical_crop_key=?',
                (job['identity_group'],job['generation'],bound['physical_crop_key'])):
            if canonical['mapping'].get(claim['person_id'])!=outcome['person_id']:
                outcome.update(person_id=None,status='unknown_conflicting_manual_claim')
    return outcome


@bridge._operation
def process_one(store):
    """At most one durable query, no models/PCM, failure cannot change transcription."""
    with bridge._db(store,write=True) as (db,check):
        _require(db)
        job=db.execute(f"SELECT * FROM {SUGGESTIONS} WHERE state='pending' ORDER BY id LIMIT 1").fetchone()
        if job is None:return {'processed':False}
        policy=bank.group_policy(db,job['identity_group'])
        current=support.generation(db,job['identity_group'])
        if (policy is None or policy['revision']!=job['profile_revision'] or policy['availability']!=job['availability']
                or current!=job['generation']):
            outcome={'status':'stale','authority':'automatic_suggestion','person_id':None}
        else:
            try:outcome=_compute(db,job,policy)
            except (bridge.BridgeError,ValueError,TypeError,KeyError) as exc:
                outcome={'status':getattr(exc,'reason','unknown_reference_integrity'),'authority':'automatic_suggestion','person_id':None}
        raw=bridge._encode(outcome,8192).decode();check()
        db.execute(f"UPDATE {SUGGESTIONS} SET state='complete',outcome=? WHERE id=? AND state='pending'",(raw,job['id']))
        return {'processed':True,'status':outcome['status']}


@bridge._operation
def suggestion(store,sid,rid,observation_id):
    with bridge._db(store) as (db,check):
        _require(db);bound=bridge._binding(db,sid,rid,observation_id)
        job=db.execute(f'''SELECT * FROM {SUGGESTIONS} WHERE source_session_id=? AND source_recording_id=?
            AND observation_id=? ORDER BY id DESC LIMIT 1''',(sid,rid,observation_id)).fetchone()
        if job is None:return {'status':'unavailable','authority':'automatic_suggestion','person_id':None}
        policy=bank.group_policy(db,job['identity_group'])
        # This endpoint is query-only. generation() is a creating writer even
        # when the bank already exists, so read the retained generation directly.
        generation=db.execute('SELECT generation FROM speaker_identity_generations WHERE identity_group=?',
            (job['identity_group'],)).fetchone()
        if (not bound['review_eligible'] or policy is None or policy['revision']!=job['profile_revision']
                or policy['availability']!=job['availability'] or generation is None or generation[0]!=job['generation']):
            return {'status':'stale','authority':'automatic_suggestion','person_id':None}
        if job['state']=='pending':return {'status':'pending','authority':'automatic_suggestion','person_id':None}
        outcome=bridge._decode(job['outcome'],8192)
        params=json.loads(policy['policy'])
        overlap_policy=bank.primary_query_policy(params)
        if (support.digest(support.canonical(params))!=policy['policy_sha256'] or
                (overlap_policy==CROP_POLICY and
                 (outcome.get('primary_query_overlap_policy')!=CROP_POLICY or
                  outcome.get('query_policy_sha256')!=policy['policy_sha256'])) or
                (overlap_policy==LEGACY_POLICY and outcome.get('primary_query_overlap_policy',LEGACY_POLICY)!=LEGACY_POLICY)):
            return {'status':'stale','authority':'automatic_suggestion','person_id':None}
        if (outcome.get('binding_sha256')!=bound['binding_sha256'] or
                outcome.get('source_authority_revision')!=bound['source_authority_revision'] or
                outcome.get('source_revision')!=bound['source_revision']):
            return {'status':'stale','authority':'automatic_suggestion','person_id':None}
        return outcome
