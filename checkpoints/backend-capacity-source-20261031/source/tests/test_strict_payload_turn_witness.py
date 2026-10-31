"""Fresh turn-witness acceptance controls; no execution authorization.

Eight finite groups share four original Stores and five generated chunks. Native
numbers, embeddings and receipt lookup are synthetic; original Authority mapping,
PCM, Capture, Store transactions, collector and cold readers execute unchanged.
The standalone run() is the same fixed workload as the one pytest adapter.
No model, Worker, actor, thread, service, subprocess or historical DB is opened.
"""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import ast
import re
import sqlite3
import hashlib
import json
from pathlib import Path
import time
from types import MappingProxyType, SimpleNamespace
import uuid

from audio_backend import worker as wm, native_owned_cell_capture as capture_module
from audio_backend import native_owned_cells as cells, native_owned_core_seams as core
from audio_backend import recording_overlap as recording, recording_primary_provenance as pp
from audio_backend import strict_recording as strict, strict_payload_v2 as payload
from audio_backend import speaker_support as support, support_vectors
from audio_backend.native_owned_cell_store import Receipt
from audio_backend.storage import Store
from test_strict_payload_v2 import configuration, NumericalAnalyzer
from test_strict_archive_v2 import signature
import test_native_owned_support as native_fixture
from test_native_owned_support import synthetic_state, wav_bytes

RATE=16000
NAMES=('fractional','empty','unknown','rollback')
WITNESS_TABLE='strict_payload_core_witness'
OLD_SOURCE_SHA256={'strict_recording.py':'03a4c5c4d392cabaf55b54c6b373636fe167974428cbd5d04af087f1bfd57072',
    'strict_payload_v2.py':'535764ae7821349f4a67bcd9d1f5402b781413ea293a63f8ce41bd719377382d'}
# Exact candidate12 source snapshots; SHA256 origins are above. No runtime
# dependency on its old workspace. These literals are not a complete old Store.
OLD_STRICT_SQL=(
    "CREATE TABLE IF NOT EXISTS strict_recording_policy (\n        sid TEXT PRIMARY KEY, policy TEXT NOT NULL CHECK(policy='strict-primary-payload-v2'))",
    "CREATE TABLE IF NOT EXISTS strict_recording_control (\n        cid TEXT PRIMARY KEY, sid TEXT NOT NULL, source TEXT NOT NULL CHECK(length(source)=64),\n        frames INTEGER NOT NULL CHECK(frames>0), state TEXT NOT NULL\n        CHECK(state IN ('ready','active','waiting','manual','completed','cancelled')),\n        nonce TEXT CHECK(nonce IS NULL OR length(nonce)=32))",
    "CREATE TABLE IF NOT EXISTS strict_recording_outcome (\n        cid TEXT NOT NULL, start_frame INTEGER NOT NULL CHECK(typeof(start_frame)='integer' AND start_frame>=0),\n        end_frame INTEGER NOT NULL CHECK(typeof(end_frame)='integer'),\n        nonce TEXT NOT NULL CHECK(length(nonce)=32), source TEXT NOT NULL CHECK(length(source)=64),\n        slot TEXT NOT NULL CHECK(length(slot)=64), status TEXT NOT NULL\n        CHECK(status IN ('retained','existing','empty','unknown_only')),\n        packet TEXT, PRIMARY KEY(cid,start_frame), UNIQUE(cid,end_frame), CHECK(end_frame>start_frame))",
    "CREATE TRIGGER IF NOT EXISTS strict_policy_fresh BEFORE INSERT ON strict_recording_policy BEGIN\n        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM sessions WHERE id=NEW.sid AND deleted=0 AND timeline_end=0 AND processing_mode='legacy')\n          OR EXISTS(SELECT 1 FROM chunks WHERE session_id=NEW.sid) THEN RAISE(ABORT,'strict_fresh_only') END; END",
    "CREATE TRIGGER IF NOT EXISTS strict_policy_update BEFORE UPDATE ON strict_recording_policy BEGIN SELECT RAISE(ABORT,'strict_policy_immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_policy_delete BEFORE DELETE ON strict_recording_policy BEGIN SELECT RAISE(ABORT,'strict_policy_retained'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_control_source BEFORE UPDATE OF cid,sid,source,frames ON strict_recording_control BEGIN SELECT RAISE(ABORT,'strict_source_immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_control_delete BEFORE DELETE ON strict_recording_control BEGIN SELECT RAISE(ABORT,'strict_control_retained'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_outcome_update BEFORE UPDATE ON strict_recording_outcome BEGIN SELECT RAISE(ABORT,'strict_outcome_immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_outcome_delete BEFORE DELETE ON strict_recording_outcome BEGIN SELECT RAISE(ABORT,'strict_outcome_retained'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_outcome_admit BEFORE INSERT ON strict_recording_outcome BEGIN\n        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM chunks c JOIN strict_recording_control s ON s.cid=c.id\n          JOIN strict_recording_policy p ON p.sid=c.session_id WHERE c.id=NEW.cid AND c.status='running'\n          AND s.state='active' AND s.nonce=NEW.nonce AND s.source=NEW.source AND c.digest=NEW.source\n          AND c.next_offset=NEW.start_frame/16000.0 AND c.duration=s.frames/16000.0\n          AND NEW.end_frame<=s.frames AND EXISTS(SELECT 1 FROM native_core_commits n WHERE n.slot=NEW.slot AND n.cid=c.id))\n          THEN RAISE(ABORT,'strict_outcome_authorization') END;\n        SELECT CASE WHEN (SELECT COUNT(*) FROM strict_recording_outcome)>=2056 THEN RAISE(ABORT,'strict_outcome_capacity') END; END",
    "CREATE TRIGGER IF NOT EXISTS strict_source_fence BEFORE UPDATE OF session_id,digest,path,duration,timeline_start ON chunks\n        WHEN EXISTS(SELECT 1 FROM strict_recording_policy WHERE sid=OLD.session_id)\n        AND (NEW.session_id IS NOT OLD.session_id OR NEW.digest IS NOT OLD.digest OR NEW.path IS NOT OLD.path\n          OR NEW.duration IS NOT OLD.duration OR NEW.timeline_start IS NOT OLD.timeline_start)\n        BEGIN SELECT RAISE(ABORT,'strict_source_immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_advance_fence BEFORE UPDATE OF next_offset ON chunks\n        WHEN EXISTS(SELECT 1 FROM strict_recording_policy WHERE sid=OLD.session_id) AND NEW.next_offset IS NOT OLD.next_offset BEGIN\n        SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM strict_recording_control s JOIN strict_recording_outcome o ON o.cid=s.cid\n          WHERE s.cid=OLD.id AND s.state='active' AND s.nonce=o.nonce AND s.source=o.source AND o.source=OLD.digest\n          AND OLD.status='running' AND o.start_frame/16000.0=OLD.next_offset AND o.end_frame/16000.0=NEW.next_offset\n          AND NEW.next_offset>OLD.next_offset AND NEW.next_offset<=OLD.duration)\n          THEN RAISE(ABORT,'strict_advance_requires_outcome') END; END",
    "CREATE TRIGGER IF NOT EXISTS strict_state_fence BEFORE UPDATE OF status ON chunks\n        WHEN EXISTS(SELECT 1 FROM strict_recording_policy WHERE sid=OLD.session_id) BEGIN\n        SELECT CASE WHEN NEW.status='running' AND NOT EXISTS(SELECT 1 FROM strict_recording_control WHERE cid=OLD.id AND state='active' AND nonce IS NOT NULL)\n          THEN RAISE(ABORT,'strict_running_authorization') END;\n        SELECT CASE WHEN NEW.status='queued' AND NOT EXISTS(SELECT 1 FROM strict_recording_control WHERE cid=OLD.id AND state IN ('ready','waiting') AND nonce IS NULL)\n          THEN RAISE(ABORT,'strict_manual_retry_required') END;\n        SELECT CASE WHEN NEW.status='completed' AND NOT EXISTS(SELECT 1 FROM strict_recording_control s JOIN strict_recording_outcome o ON o.cid=s.cid\n          WHERE s.cid=OLD.id AND s.state='active' AND s.nonce=o.nonce AND s.source=OLD.digest\n          AND o.end_frame=s.frames AND NEW.next_offset=o.end_frame/16000.0 AND NEW.next_offset=OLD.duration)\n          THEN RAISE(ABORT,'strict_completion_requires_terminal_outcome') END; END",
    "CREATE TABLE IF NOT EXISTS strict_payload_policy (\n      singleton INTEGER PRIMARY KEY CHECK(singleton=1), policy TEXT NOT NULL CHECK(policy='strict-primary-payload-v2'))",
    "CREATE TABLE IF NOT EXISTS strict_payload_unit (\n      sequence INTEGER PRIMARY KEY CHECK(sequence>0 AND sequence<=2056), unit_id TEXT NOT NULL UNIQUE CHECK(length(unit_id)=64),\n      cid TEXT NOT NULL UNIQUE CHECK(length(cid)=32), store_key TEXT NOT NULL CHECK(length(store_key)=64),\n      evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),\n      manifest_bytes INTEGER NOT NULL CHECK(typeof(manifest_bytes)='integer' AND manifest_bytes BETWEEN 1 AND 4194304), bucket INTEGER NOT NULL CHECK(typeof(bucket)='integer' AND bucket=(sequence-1)/16),\n      state TEXT NOT NULL CHECK(state IN ('reserved','published','committed')), manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64),\n      bytes INTEGER NOT NULL CHECK(typeof(bytes)='integer' AND bytes>0 AND bytes<=37748746), file_sha256 TEXT, signature TEXT,\n      CHECK((state='reserved' AND file_sha256 IS NULL AND signature IS NULL) OR\n        (state IN ('published','committed') AND length(file_sha256)=64 AND length(signature) BETWEEN 1 AND 512)))",
    "CREATE TABLE IF NOT EXISTS strict_payload_object (\n      kind TEXT NOT NULL CHECK(kind IN ('pcm','vector')), owner_id TEXT NOT NULL CHECK(length(owner_id)=32),\n      unit_id TEXT NOT NULL REFERENCES strict_payload_unit(unit_id), object_id TEXT NOT NULL CHECK(length(object_id) BETWEEN 1 AND 80),\n      bytes INTEGER NOT NULL CHECK(typeof(bytes)='integer' AND bytes>0 AND bytes<=1280000), sha256 TEXT NOT NULL CHECK(length(sha256)=64),\n      offset INTEGER NOT NULL CHECK(typeof(offset)='integer' AND offset>=10), PRIMARY KEY(kind,owner_id))",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_policy_update BEFORE UPDATE ON strict_payload_policy\n      BEGIN SELECT RAISE(ABORT,'strict_payload_policy_immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_policy_delete BEFORE DELETE ON strict_payload_policy\n      BEGIN SELECT RAISE(ABORT,'strict_payload_policy_retained'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_unit_update BEFORE UPDATE ON strict_payload_unit BEGIN\n      SELECT CASE WHEN NEW.sequence IS NOT OLD.sequence OR NEW.unit_id IS NOT OLD.unit_id OR NEW.cid IS NOT OLD.cid\n        OR NEW.bucket IS NOT OLD.bucket OR NEW.manifest_sha256 IS NOT OLD.manifest_sha256 OR NEW.bytes IS NOT OLD.bytes\n        OR NEW.store_key IS NOT OLD.store_key OR NEW.evidence_sha256 IS NOT OLD.evidence_sha256\n        OR NEW.manifest_bytes IS NOT OLD.manifest_bytes OR strict_payload_transition('unit',OLD.unit_id)!=1\n        OR NOT ((OLD.state='reserved' AND NEW.state='published') OR (OLD.state='published' AND NEW.state='committed'\n          AND NEW.file_sha256 IS OLD.file_sha256 AND NEW.signature IS OLD.signature))\n        THEN RAISE(ABORT,'strict_payload_unit_transition') END; END",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_unit_delete BEFORE DELETE ON strict_payload_unit\n      BEGIN SELECT RAISE(ABORT,'strict_payload_unit_retained'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_object_insert BEFORE INSERT ON strict_payload_object BEGIN\n      SELECT CASE WHEN strict_payload_locator(NEW.kind,NEW.owner_id,NEW.unit_id,NEW.object_id,NEW.bytes,NEW.sha256,NEW.offset)!=1 OR NOT EXISTS(\n        SELECT 1 FROM strict_payload_unit WHERE unit_id=NEW.unit_id AND state='committed'\n          AND NEW.offset+NEW.bytes<=bytes) THEN RAISE(ABORT,'strict_payload_locator_unauthorized') END; END",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_object_update BEFORE UPDATE ON strict_payload_object\n      BEGIN SELECT RAISE(ABORT,'strict_payload_locator_immutable'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_object_delete BEFORE DELETE ON strict_payload_object\n      WHEN (OLD.kind='pcm' AND EXISTS(SELECT 1 FROM speaker_support WHERE id=OLD.owner_id))\n        OR (OLD.kind='vector' AND EXISTS(SELECT 1 FROM speaker_support_vectors WHERE id=OLD.owner_id))\n      BEGIN SELECT RAISE(ABORT,'strict_payload_locator_retained'); END",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_support_erasure AFTER DELETE ON speaker_support BEGIN\n      DELETE FROM strict_payload_object WHERE kind='pcm' AND owner_id=OLD.id; END",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_vector_erasure AFTER DELETE ON speaker_support_vectors BEGIN\n      DELETE FROM strict_payload_object WHERE kind='vector' AND owner_id=OLD.id; END",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_support_update BEFORE UPDATE ON speaker_support BEGIN\n      SELECT CASE WHEN NEW.id IS NOT OLD.id OR NEW.session_id IS NOT OLD.session_id OR NEW.chunk_id IS NOT OLD.chunk_id\n        OR NEW.identity_group IS NOT OLD.identity_group OR NEW.generation IS NOT OLD.generation\n        OR NEW.window_start IS NOT OLD.window_start OR NEW.local_speaker_id IS NOT OLD.local_speaker_id\n        OR NEW.observation_id IS NOT OLD.observation_id OR NEW.manifest IS NOT OLD.manifest\n        OR NEW.manifest_sha256 IS NOT OLD.manifest_sha256 OR NEW.status IS NOT OLD.status OR NEW.pcm_bytes IS NOT OLD.pcm_bytes\n        OR OLD.pcm IS NULL OR NEW.pcm IS NOT NULL OR strict_payload_transition('pcm',OLD.id)!=1\n        OR NOT EXISTS(SELECT 1 FROM strict_payload_object o JOIN strict_payload_unit u USING(unit_id)\n          WHERE o.kind='pcm' AND o.owner_id=OLD.id AND o.bytes=OLD.pcm_bytes\n          AND o.sha256=json_extract(OLD.manifest,'$.support.pooled_pcm_sha256') AND u.state='committed')\n        THEN RAISE(ABORT,'strict_payload_support_immutable') END; END",
    "CREATE TRIGGER IF NOT EXISTS strict_payload_vector_update BEFORE UPDATE ON speaker_support_vectors BEGIN\n      SELECT CASE WHEN NEW.id IS NOT OLD.id OR NEW.support_id IS NOT OLD.support_id OR NEW.identity_group IS NOT OLD.identity_group\n        OR NEW.generation IS NOT OLD.generation OR NEW.vector_sha256 IS NOT OLD.vector_sha256 OR NEW.dimension IS NOT OLD.dimension\n        OR NEW.space_id IS NOT OLD.space_id OR NEW.space IS NOT OLD.space OR NEW.normalization IS NOT OLD.normalization\n        OR NEW.normalized_sha256 IS NOT OLD.normalized_sha256 OR NEW.evidence_key IS NOT OLD.evidence_key\n        OR NEW.retained_bytes IS NOT OLD.retained_bytes OR OLD.raw_vector IS NULL OR NEW.raw_vector IS NOT NULL\n        OR strict_payload_transition('vector',OLD.id)!=1 OR NOT EXISTS(\n          SELECT 1 FROM strict_payload_object o JOIN strict_payload_unit u USING(unit_id)\n          WHERE o.kind='vector' AND o.owner_id=OLD.id AND o.bytes=OLD.retained_bytes\n          AND o.sha256=OLD.vector_sha256 AND u.state='committed')\n        THEN RAISE(ABORT,'strict_payload_vector_immutable') END; END",
)
OLD_ADMISSION_SOURCE='def _canonical_ddl(sql):\n    return \'\'.join(sql.replace(\' IF NOT EXISTS\',\'\').split())\n\ndef admit_schema(db, policy=POLICY):\n    expected={}\n    for sql in schema_statements(policy):\n        match=re.match(r\'CREATE (TABLE|TRIGGER) IF NOT EXISTS ([a-z_]+)\',sql)\n        expected[match[2]]=(match[1].lower(),_canonical_ddl(sql))\n    rows=db.execute("SELECT CASE WHEN length(name)<=64 THEN name ELSE NULL END,type,CASE WHEN length(sql)<=4096 THEN sql ELSE NULL END FROM sqlite_master WHERE lower(name) GLOB \'strict_*\' LIMIT 33").fetchall()\n    if len(rows)!=len(expected):raise ValueError(\'strict_schema_mismatch\')\n    for name,kind,sql in rows:\n        if type(sql) is not str or name not in expected or (kind,_canonical_ddl(sql))!=expected[name]:raise ValueError(\'strict_schema_mismatch\')\n    if policy==PAYLOAD_POLICY:\n        from .strict_payload_v2 import admit_payload_schema\n        admit_payload_schema(db)'
REPORT={'schema':'payload-turn-witness-control-v1','groups':8,'cases':[]}
PENDING={'stores':[None]*4,'authorities':[None]*4,'captures':[None]*5,
         'receipt_owners':[None]*4,'collectors':[None]*12,'collector_errors':[None]*12,
         'expected_errors':[None]*24,'raw_db':None,'initializer':None,
         'file':None,'first_error':None,'cleanup_error':None}
STATE={'deadline':None,'chunks':0,'commits':0,'collector_calls':0,'captures':0,'refusals':0,'builder_calls':0,'commit_attempts':0,'expected_errors':0,'source_reads':0,'source_bytes':0,'collector_witnesses':[None]*12}


def require(value,reason):
    if not value:raise AssertionError(reason)


def check():
    require(STATE['deadline'] is not None and time.monotonic()<STATE['deadline'],'control_deadline')


def remember(error,cleanup=False):
    if cleanup and PENDING['cleanup_error'] is None:PENDING['cleanup_error']=error
    if PENDING['first_error'] is None:PENDING['first_error']=error



def write_new(path,raw):
    """One generated output owner, retained on unknown close; no retry."""
    check();require(PENDING['file'] is None,'generated_output_owner_occupied')
    require(type(raw) is bytes and len(raw)<=128*1024,'generated_output_bound')
    original=None
    try:
        PENDING['file']=path.open('xb')
        require(PENDING['file'].write(raw)==len(raw),'generated_output_short_write')
    except BaseException as error:
        original=error;remember(error);raise
    finally:
        if PENDING['file'] is not None:
            try:PENDING['file'].close()
            except BaseException as error:
                remember(error,True)
                if original is None:raise
            else:PENDING['file']=None


def safe(value):
    if type(value) is bytes:return {'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()}
    if isinstance(value,dict):return {k:safe(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [safe(v) for v in value]
    return value


def digest(value):
    raw=json.dumps(safe(value),sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    require(len(raw)<=256*1024,'bounded_projection');return hashlib.sha256(raw).hexdigest()


def positive(store):
    if store.strict_cleanup_uncertain():store._strict_refuse()
    require(not store.strict_cleanup_uncertain() and store.strict_cleanup_confirmed()
            and store._strict_depth==0 and store._strict_connections==[None,None],
            'original_store_cleanup')


class ReceiptOwner:
    """No actor/liveness assertion: lookup only actual fresh Receipt objects."""
    def __init__(self):
        self.enabled=True;self._epoch=object();self.bounded_core_enabled=True
        self.retention_store_key='d'*64;self.receipts={}
    def health(self):return {'ready':True}
    def receipt(self,cid):return self.receipts.get(cid)


def new_store(root,index):
    check();path=root/NAMES[index];path.mkdir()
    store=Store.__new__(Store);PENDING['stores'][index]=store
    Store.__init__(store,path/'primary.sqlite',configuration(path))
    store.moss_automatic=SimpleNamespace(primary_only=True)
    owner=ReceiptOwner();PENDING['receipt_owners'][index]=owner
    authority=capture_module.PrimaryAuthority(store);PENDING['authorities'][index]=authority
    authority.owner=owner
    return store,owner,authority


def seed(store,owner,*,origin=0.,count=1):
    """Same canonical work/source/range boundary as seed_contiguous_recording.

    Original _add_chunk(offset=...) supplies the fractional-case absolute origin;
    no existing immutable chunk is changed and no earlier audio is fabricated.
    """
    root=store.settings.data_dir/'uploads';root.mkdir()
    sid=store.create_session('generated turn geometry')['id']
    bid=uuid.uuid4().hex;stream='recording.'+bid;origin_frame=round(origin*RATE)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''INSERT INTO recording_batches(id,session_id,capture_id,state,
            capture_origin_frame,capture_end_frame,created) VALUES(?,?,?,'queued',?,?,0.)''',
            (bid,sid,'generated-turn-geometry',origin_frame,origin_frame+count*32000))
    values=[]
    for index in range(count):
        check();pcm=(index+1).to_bytes(2,'little',signed=True)*32000;encoded=wav_bytes(pcm)
        original=root/f'{index}-original.audio';work=root/f'{index}-work.audio'
        write_new(original,encoded);write_new(work,encoded)
        sha=hashlib.sha256(encoded).hexdigest();source_id=uuid.uuid4().hex
        position=origin_frame+index*32000
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            cid,created=store._add_chunk(db,sid,str(index),sha,work,2.,offset=position/RATE,
                stream_id=stream,stream_final=index==count-1,_status='staging')
            require(created,'original_chunk_created')
            receipt=Receipt(sid,cid,sha,32000,44,len(encoded),signature(work),position/RATE,stream)
            db.execute('''INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,
                capture_start_frame,native_receipt_status,native_receipt,receipt_id,receipt_state,file_signature)
                VALUES(?,?,?,?,?,?,'durable',?,?,'durable',?)''',
                (cid,bid,index,32000,len(encoded),position,receipt.encoded,receipt.receipt_id,
                 json.dumps(list(receipt.signature),separators=(',',':'))))
            db.execute('''INSERT INTO recording_sources(id,batch_id,idempotency_key,digest,path,
                frames,bytes,source_start_frame,format,created) VALUES(?,?,?,?,?,?,?,?,?,0.)''',
                (source_id,bid,str(index),sha,str(original),32000,len(encoded),position,recording.FORMAT))
            db.execute('''INSERT INTO recording_ranges(batch_id,source_id,chunk_id,source_start_frame,
                capture_start_frame,work_start_frame,frames,relation) VALUES(?,?,?,0,?,0,32000,'owner')''',
                (bid,source_id,cid,position))
            db.execute("UPDATE chunks SET status='queued' WHERE id=?",(cid,))
        owner.receipts[cid]=receipt;STATE['chunks']+=1
        values.append(SimpleNamespace(store=store,sid=sid,cid=cid,bid=bid,stream=stream,
            original=original,work=work,pcm=pcm,receipt=receipt))
    positive(store);return values


def numerical_witness(value,chunk,turn):
    """Original witness actions ingest THIS source's actual generated bytes."""
    epoch='a'*32;w=cells.RollingWitness();provenance=native_fixture.synthetic_provenance()
    w.observe({'state':synthetic_state(epoch,0),'provenance':provenance})
    w.observe_pcm(0,32000,value.pcm)
    w.observe(synthetic_state(epoch,32000,stable_frames=32000))
    w.observe(synthetic_state(epoch,32000,finished=True))
    raw=[{'start':turn[0]/RATE,'end':turn[1]/RATE,'speaker_local_id':epoch+':speaker_1'}]
    w.observe({'turns':raw})
    origin=chunk['timeline_start']
    native={'end':2.,'extract_start':0.,'extract_end':2.,'turns':deepcopy(raw),
        'provenance':provenance,'state':{'epoch_id':epoch,'epoch_index':0,
            'owned_start':origin,'owned_end':origin+300.,'replay_start':origin,
            'input_until':origin+2.,'processed_until':origin+2.,'stable_until':origin+2.,
            'finished':True,'provenance':provenance}}
    return w,native


def prepare_one(value,chunk,capture,offset,end,*,turn=(0,32000),mode='retained',ordinal=0):
    """Original prepare chain; no injected status, packet or captured row hash."""
    check();store=value.store;capture.begin_prepare(chunk,None)
    witness,native=numerical_witness(value,chunk,turn);native['end']=end
    capture.witness=witness;capture.observed_reset_generation=1
    capture.analyzer=value.pcm;capture.analyzer_start=0;capture.end_prepare()
    analyzer=NumericalAnalyzer({});analyzer.owned_start=offset;original=None
    try:result=analyzer.analyze(value.work,diarization=native)
    except BaseException as error:original=error;remember(error);raise
    finally:
        try:analyzer.close()
        except BaseException as cleanup:
            remember(cleanup,True)
            if original is None:raise
    if mode=='empty':result['segments']=[]
    if mode=='unknown_only':
        # The actual observer finds no matching native turn at these anchors.
        start,stop=(.1,.3) if ordinal==0 else (1.7,1.9)
        row=result['segments'][0];row.update(start=start,end=stop,speaker_local_id=None)
        row['_units'][0].update(start=start,end=stop,ownership_anchor=start)
    observed=pp.observe(result,native);require(observed[1]=='pending','original_primary_observation')
    if mode=='unknown_only':require(all(u[5] is None and not u[6] for u in observed[0]['units']),'actual_unknown_assignment')
    wm.validate_result(result,2.);pp.validate_observation(result,observed)
    require(result.get('_primary_observation') is not None,'original_observation_retained')
    result['metadata'].update(native['provenance']);result['metadata']['native_diarization']=native['state']
    lineage={'status':'complete','analyzer_frame_count':32000,
        'decoder_sha256':cells.sha({'synthetic':'canonical-existing-bytes'}),
        'parts':[{'chunk_id':chunk['id'],'source_sha256':chunk['digest'],
            'seek_seconds':0.,'requested_seconds':2.,'analyzer_frames':[0,32000]}]}
    generation=support.capture_generation(store,chunk)
    prepared=support.prepare_window(result,value.work,lineage,generation,
        core_start=offset,analyzer_start=0.,settings=store.settings)
    require(any(v.get('status')=='available' for v in prepared.values()),'actual_nonempty_support')
    result=wm.own_core(result,offset,end,None,0.,retain_lexical=True)
    result['_speaker_support']=prepared
    result['_lexical_sources']=[{**{k:chunk[k] for k in ('id','session_id','digest','duration','timeline_start')},
        'seek_seconds':0.,'requested_seconds':2.,'analyzer_frames':[0,32000]}]
    result['metadata']['core_cut']={'nominal':end,'actual':end,'reason':'fixed_or_final'}
    capture.start_bounded(result);capture.prepare(chunk,native,offset,end,result,required=True)
    require(result['_strict_capture'].status==mode,'original_capture_status')
    require(('_primary_provenance' in result)==(mode=='retained'),'packetless_is_not_normal_packet')
    if mode in ('empty','unknown_only'):
        require(type(result.get('_strict_payload_capture')) is payload.PacketlessCapture,'original_typed_carrier')
    else:require('_strict_payload_capture' not in result,'retained_has_no_packetless_carrier')
    require('_strict_payload_draft' not in result,'private_draft_not_published')
    capture.prepare_core_seam(chunk,native,offset,end,result,None,lambda:False,required=True)
    with result['_native_bounded_support']['budget'].phase():strict.require_prepared(chunk,offset,end,result)
    return result


def start_capture(value,owner,authority):
    chunk=value.store.claim();require(chunk is not None and chunk['id']==value.cid,'original_claim')
    capture=capture_module.Capture(owner,authority)
    require(capture.primary_only is True,'actual_primary_only_profile');capture.core_seams=core.CoreSeams(capture)
    i=STATE['captures'];require(i<5,'capture_capacity');PENDING['captures'][i]=capture;STATE['captures']+=1
    value.claim=chunk;value.nonce=chunk['_strict_attempt_nonce'];value.capture=capture
    return chunk,capture


def commit_two(value,owner,authority,*,cut=1.,turn=(0,32000),modes=('retained','retained'),faults=False):
    check();chunk,capture=start_capture(value,owner,authority);claimed=deepcopy(chunk);offset=0.
    if faults:commit_failures(value,chunk,capture)
    for ordinal,end in enumerate((cut,2.)):
        result=prepare_one(value,chunk,capture,offset,end,turn=turn,mode=modes[ordinal],ordinal=ordinal)
        STATE['commit_attempts']+=1
        require(value.store.commit_window(chunk,offset,end,result) is True,'original_commit_ack')
        STATE['commits']+=1;offset=end
        require(chunk==claimed,'claim_unchanged_after_ack');positive(value.store)
    value.statuses=list(modes);return value


def snapshot(value):
    check();store=value.store
    with store.db() as db:
        db.execute('BEGIN')
        chunk=dict(db.execute('SELECT id,status,next_offset,duration FROM chunks WHERE id=?',(value.cid,)).fetchone())
        outcomes=[dict(r) for r in db.execute('SELECT * FROM strict_recording_outcome WHERE cid=? ORDER BY start_frame LIMIT 3',(value.cid,))]
        commits=[dict(r) for r in db.execute('SELECT * FROM native_core_commits WHERE cid=? ORDER BY slot LIMIT 3',(value.cid,))]
        turns=[dict(r) for r in db.execute('SELECT id,start,end,speaker_local_id,speaker_window_start FROM speaker_turns WHERE chunk_id=? ORDER BY id LIMIT 9',(value.cid,))]
        segments=[dict(r) for r in db.execute('SELECT id,start,end,text,raw_text,confidence,speaker_local_id,lexical_evidence FROM segments WHERE chunk_id=? ORDER BY id LIMIT 9',(value.cid,))]
        supports=[dict(r) for r in db.execute('SELECT id,pcm_bytes,manifest_sha256,length(pcm) AS hot_bytes FROM speaker_support WHERE chunk_id=? ORDER BY id LIMIT 9',(value.cid,))]
        vectors=[dict(r) for r in db.execute('''SELECT v.id,v.support_id,v.vector_sha256,v.retained_bytes,length(v.raw_vector) AS hot_bytes
            FROM speaker_support_vectors v JOIN speaker_support s ON s.id=v.support_id WHERE s.chunk_id=? ORDER BY v.id LIMIT 9''',(value.cid,))]
        require(chunk['status']=='completed' and chunk['next_offset']==2. and len(outcomes)==len(commits)==2
                and 0<len(turns)<=4 and len(segments)<=2 and supports and vectors,'finite_complete_source_prefix')
        require([o['nonce'] for o in outcomes]==[value.nonce]*2,'same_nonce_real_acks')
        packets=[];projected=[]
        for out in outcomes:
            saved=pp.read_exact(db,cid=value.cid,start=out['start_frame'],end=out['end_frame'])
            if out['status'] in ('empty','unknown_only'):require(saved is None and out['packet'] is None,'packetless_has_no_packet')
            else:
                require(saved is not None,'retained_original_packet');packets.append(saved)
                p=saved['packet'];part=p['commit']['part'];origin=float.fromhex(p['commit']['scope']['origin'])
                for _,label,a,b,_,_ in p['turns']:
                    lo,hi=max(a,part['logical_start']),min(b,part['logical_end'])
                    if lo<hi:projected.append((origin+lo/RATE,origin+hi/RATE,p['labels'][label]))
        witnesses=[dict(r) for r in db.execute(f'SELECT * FROM {WITNESS_TABLE} WHERE cid=? ORDER BY core_start LIMIT 5',(value.cid,))]
        require(len(witnesses)==sum(o['status'] in ('empty','unknown_only') for o in outcomes),'exact_witness_cardinality')
        stored=[(r['start'],r['end'],r['speaker_local_id']) for r in turns]
        units=[dict(r) for r in db.execute('SELECT cid,state,unit_id FROM strict_payload_unit WHERE cid=? LIMIT 2',(value.cid,))]
    positive(store)
    logical=[chunk,outcomes,commits,turns,segments,packets,witnesses,
        [{k:v for k,v in s.items() if k!='hot_bytes'} for s in supports],
        [{k:v for k,v in s.items() if k!='hot_bytes'} for s in vectors]]
    def geometry(rows):return [[float(a).hex(),float(b).hex(),label] for a,b,label in rows]
    return {'logical_sha256':digest(logical),'outcomes':[o['status'] for o in outcomes],
        'turns':turns,'projected':geometry(projected),'stored':geometry(stored),
        'counter_matches':Counter(projected)==Counter(stored),'support':supports,'vectors':vectors,
        'hot_pcm':sum(s['hot_bytes'] or 0 for s in supports),'hot_vectors':sum(s['hot_bytes'] or 0 for s in vectors),
        'units':units,'packet_count':len(packets),'witnesses':witnesses,'segments':segments}


def collect(value,*,refusal=None,expected_type=payload.PayloadIntegrityError):
    check();budget=payload.PayloadBudget();caught=None
    try:
        with payload._operation(value.store,budget) as owner:
            manifest,specs=payload._collect(owner,value.cid)
            require(manifest['metadata']['control']['cid']==value.cid
                and all(r['chunk_id']==value.cid for r in manifest['metadata']['turns']),'collector_cid_isolation')
            answer={'product_outcome':'accepted','manifest_sha256':digest(manifest),
                'objects':len(specs),'snapshot_turns':len(manifest['metadata']['turns']),
                'archived_witnesses':len(manifest['metadata']['core_witnesses']),
                'archived_witnesses_sha256':digest(manifest['metadata']['core_witnesses'])}
            for row in manifest['metadata']['core_witnesses']:
                encoded=row['body'];require(type(encoded) is dict and set(encoded)=={'$bytes'},'complete_archived_witness_body')
                raw=bytes.fromhex(encoded['$bytes']);require(hashlib.sha256(raw).hexdigest()==row['digest'],'archived_witness_digest')
    except expected_type as error:
        caught=error
        if refusal is None:raise
        require(type(error) is expected_type and str(error)==refusal,'exact_collector_refusal')
        retain_expected(error);STATE['refusals']+=1
        answer={'product_outcome':'refused','reason':str(error)}
    positive(value.store)
    require((caught is not None)==(refusal is not None),'expected_collector_outcome')
    return answer


def cold_baseline(value,name):
    before=snapshot(value);require(not before['counter_matches'],'fractional_old_formula_is_not_exact')
    sid=before['support'][0]['id']
    audio=support.audio(value.store,value.sid,sid)
    with value.store.db() as db:
        db.execute('BEGIN');vector,_=support_vectors.read(db,sid)
    value.cold_sample=(sid,audio,vector['raw_vector'],vector['space'])
    witness=object.__new__(capture_module.PayloadFrontier);witness.store=value.store
    original=capture_module.PayloadFrontier.guard
    @contextmanager
    def guard(self,db,budget):
        require(self is witness and db.in_transaction,'component_frontier_binding');budget.check()
        yield MappingProxyType({'sid':value.sid,'stream_id':value.stream,
            'frontier_frame':round((value.receipt.timeline_start+2.)*RATE),'roots':frozenset()})
        budget.check()
    capture_module.PayloadFrontier.guard=guard
    error=None;collect_start=STATE['collector_calls']
    try:answer=value.store.maintain_payloads(witness,payload.PayloadBudget())
    except BaseException as exc:error=exc;remember(exc);raise
    finally:
        if capture_module.PayloadFrontier.guard is not guard:
            cleanup=AssertionError('frontier_observer_changed');remember(cleanup,True)
            if error is None:raise cleanup
        else:capture_module.PayloadFrontier.guard=original
    require(answer['state']=='committed' and answer['cid']==value.cid,'original_full_maintenance')
    require(STATE['collector_calls']==collect_start+2,'original_collection_and_recollection')
    expected=(len(before['witnesses']),digest(payload._jsonable(before['witnesses'])))
    require(STATE['collector_witnesses'][collect_start:collect_start+2]==[expected,expected],
        'complete_original_witnesses_in_both_archive_passes')
    after=snapshot(value)
    require(after['logical_sha256']==before['logical_sha256'] and after['hot_pcm']==after['hot_vectors']==0
            and len(after['units'])==1 and after['units'][0]['state']=='committed','only_hot_bodies_retired')
    cold_read(value)
    value.cold_snapshot=after
    return {'case':name,'archived_witnesses':len(before['witnesses']),'product_outcome':'accepted','placement':'full_original_maintenance',
            'before':summarize(before),'after':summarize(after)}


def cold_read(value):
    sid,audio,raw,space=value.cold_sample
    require(support.audio(value.store,value.sid,sid)==audio,'actual_cold_pcm_exact')
    with value.store.db() as db:
        db.execute('BEGIN')
        with value.store.payload_resolver(db) as resolver:
            vector,normalized=support_vectors.read(db,sid,payload=resolver)
            require(vector['raw_vector']==raw and vector['space']==space and normalized[0]==1.,'actual_cold_vector_exact')
    positive(value.store)


def summarize(s):
    return {key:s[key] for key in ('logical_sha256','outcomes','projected','stored','counter_matches',
        'hot_pcm','hot_vectors','packet_count')}



def owner_summary():
    stores=[s for s in PENDING['stores'] if s is not None]
    authorities=[a for a in PENDING['authorities'] if a is not None]
    captures=[c for c in PENDING['captures'] if c is not None]
    collectors=[o for o in PENDING['collectors'] if o is not None]
    clean=all(getattr(s,'_strict_depth',None)==0 and getattr(s,'_strict_connections',None)==[None,None]
        and s.strict_cleanup_confirmed() and not s.strict_cleanup_uncertain() for s in stores)
    io=PENDING['file'] is None and PENDING['raw_db'] is None
    if PENDING['initializer'] is not None:
        item=PENDING['initializer'];io=io and getattr(item,'_strict_depth',None)==0
        io=io and getattr(item,'_strict_connections',None)==[None,None] and item.strict_cleanup_confirmed() and not item.strict_cleanup_uncertain()
    io=io and all(getattr(a._list_reads,'current',None) is None for a in authorities)
    io=io and all(c.analyzer is None for c in captures) and all(not o.unknown and not o.fds and o.scan is None for o in collectors)
    return {'stores_retained':len(stores),'captures_retained':len(captures),'file_pending':PENDING['file'] is not None,
        'first_error':None if PENDING['first_error'] is None else type(PENDING['first_error']).__name__,
        'cleanup_error':None if PENDING['cleanup_error'] is None else type(PENDING['cleanup_error']).__name__,
        'cleanup_confirmed':clean and io,'physical_io_clear':clean and io,
        'expected_collector_refusals':STATE['refusals']}



def retain_expected(error):
    index=STATE['expected_errors'];require(index<len(PENDING['expected_errors']),'expected_error_slots')
    PENDING['expected_errors'][index]=error;STATE['expected_errors']+=1


def logical_tip(value):
    """Small transaction-rollback oracle; no implicit transaction assumptions."""
    with value.store.db() as db:
        db.execute('BEGIN')
        chunk=tuple(db.execute('SELECT status,next_offset FROM chunks WHERE id=?',(value.cid,)).fetchone())
        control=tuple(db.execute('SELECT state,nonce FROM strict_recording_control WHERE cid=?',(value.cid,)).fetchone())
        counts=[]
        for table,column in (('segments','chunk_id'),('speaker_turns','chunk_id'),('speaker_support','chunk_id'),
            ('speaker_observations','chunk_id'),('native_core_commits','cid'),('strict_recording_outcome','cid'),
            (pp.TABLE,'cid'),(WITNESS_TABLE,'cid')):
            counts.append(db.execute(f'SELECT COUNT(*) FROM {table} WHERE {column}=?',(value.cid,)).fetchone()[0])
        vectors=db.execute('''SELECT COUNT(*) FROM speaker_support_vectors v JOIN speaker_support s
            ON s.id=v.support_id WHERE s.chunk_id=?''',(value.cid,)).fetchone()[0]
    positive(value.store);return chunk,control,tuple(counts),vectors


class CommitAfterWitnessFault(RuntimeError):pass


def commit_failures(value,chunk,capture):
    before=logical_tip(value);original_claim=deepcopy(chunk);rows=[]
    for fault in ('missing','changed_nonce','after_insert'):
        result=prepare_one(value,chunk,capture,0.,1.,mode='empty')
        carrier=result['_strict_payload_capture']
        if fault=='missing':result.pop('_strict_payload_capture')
        elif fault=='changed_nonce':result['_strict_payload_capture']=replace(carrier,binding=carrier.binding[:3]+('f'*32,)+carrier.binding[4:])
        original_hook=payload.commit_packetless_witness
        sentinel=CommitAfterWitnessFault('generated after original witness INSERT')
        forwarded=[0];caught=None;body=None
        def fail_after_insert(db,*args):
            forwarded[0]+=1;require(db.in_transaction,'same_original_transaction')
            answer=original_hook(db,*args)
            row=db.execute(f'SELECT COUNT(*) FROM {WITNESS_TABLE} WHERE cid=?',(value.cid,)).fetchone()
            require(row[0]==1,'original_witness_insert_reached')
            raise sentinel
        if fault=='after_insert':payload.commit_packetless_witness=fail_after_insert
        try:
            STATE['commit_attempts']+=1
            try:value.store.commit_window(chunk,0.,1.,result)
            except BaseException as error:
                caught=error;retain_expected(error)
                if fault=='after_insert':require(error is sentinel,'original_sentinel_identity')
                else:require(type(error) is payload.PayloadIntegrityError and str(error)=='payload_witness_capture_binding','typed_carrier_refusal')
            require(caught is not None,'commit_fault_must_refuse')
            positive(value.store)
        except BaseException as error:body=error;remember(error);raise
        finally:
            if fault=='after_insert':
                if payload.commit_packetless_witness is fail_after_insert:payload.commit_packetless_witness=original_hook
                else:
                    cleanup=AssertionError('commit_hook_changed');remember(cleanup,True)
                    if body is None:raise cleanup
        require(logical_tip(value)==before and chunk==original_claim,'atomic_failure_preserves_tip_and_proofs')
        rows.append({'fault':fault,'error':type(caught).__name__,'hook_calls':forwarded[0],
            'rollback_positive':True,'nonce_unchanged':True})
    REPORT['cases'].append({'case':'prepared_carrier_refusal','attempts':rows[:2]})
    REPORT['cases'].append({'case':'same_transaction_rollback','attempt':rows[2]})


def accepted_case(value,name):
    before=snapshot(value);answer=collect(value);after=snapshot(value)
    require(answer['archived_witnesses']==len(before['witnesses'])
        and answer['archived_witnesses_sha256']==digest(payload._jsonable(before['witnesses'])),
        'collector_includes_complete_original_witness_bodies')
    require(before==after and before['hot_pcm']>0 and before['hot_vectors']>0 and not before['units'],
        'collector_preserves_hot_logical_state')
    return {'case':name,**answer,'packet_count':before['packet_count'],
        'witness_count':len(before['witnesses']),'outcomes':before['outcomes'],
        'turn_count':len(before['turns']),'logical_sha256':before['logical_sha256']}


def expect_sql_refusal(value,sql,args,reason):
    caught=None
    try:
        with value.store.db() as db:
            db.execute('BEGIN IMMEDIATE');db.execute(sql,args)
    except sqlite3.IntegrityError as error:
        caught=error;retain_expected(error);require(reason in str(error),'exact_immutable_sql_reason')
    require(caught is not None,'sql_refusal_required');positive(value.store)


def witness_fault(value,mode,reason):
    """Actual helper + original SQL, with one bounded cursor response double.

    No DDL or persisted body changes. This is not real media corruption and does
    not replace the original connection, progress handler, transaction or owner.
    """
    original=payload._read_core_witnesses;hits=[0];body=None
    class Response:
        def __init__(self,cursor,kind):self.cursor=cursor;self.kind=kind
        def fetchall(self):
            rows=self.cursor.fetchall();require(self.kind=='heads' and rows,'real_witness_heads')
            hits[0]+=1;require(hits[0]==1,'one_head_response_fault');return []
        def fetchone(self):
            row=self.cursor.fetchone();require(self.kind=='body' and row is not None,'real_witness_body')
            raw=row[0];require(type(raw) is bytes and raw,'bounded_real_body')
            hits[0]+=1;require(hits[0]==1,'one_body_response_fault');return (b'!'+raw[1:],)
    class ReadEdge:
        def __init__(self,db):self.db=db
        def execute(self,sql,args=()):
            cursor=self.db.execute(sql,args)
            if mode=='missing' and 'FROM strict_payload_core_witness WHERE cid=? ORDER BY core_start LIMIT ?' in sql and 'length(body) AS size' in sql:
                return Response(cursor,'heads')
            if mode=='body' and sql.startswith('SELECT body FROM strict_payload_core_witness '):
                return Response(cursor,'body')
            return cursor
    def changed(owner,db,cid):
        require(cid==value.cid,'exact_fault_cid');return original(owner,ReadEdge(db),cid)
    payload._read_core_witnesses=changed
    try:answer=collect(value,refusal=reason)
    except BaseException as error:body=error;remember(error);raise
    finally:
        if payload._read_core_witnesses is changed:payload._read_core_witnesses=original
        else:
            cleanup=AssertionError('witness_reader_observer_changed');remember(cleanup,True)
            if body is None:raise cleanup
    require(hits[0]==1,'one_original_witness_response');return answer


def row_fault(value,table,row,column,new_value,reason):
    """One actual generated-row mutation, restored only after positive cleanup."""
    require((table,column) in (('speaker_turns','end'),('segments','confidence')),'fixed_row_mutation')
    with value.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        require(db.execute(f'UPDATE {table} SET {column}=? WHERE id=? AND chunk_id=?',
            (new_value,row['id'],value.cid)).rowcount==1,'one_generated_row_changed')
        if table=='segments':
            require(db.execute('SELECT lexical_evidence FROM segments WHERE id=?',(row['id'],)).fetchone()[0] is None,
                'original_primary_mutation_revokes_lexical')
    positive(value.store);answer=None;body=None
    try:answer=collect(value,refusal=reason)
    except BaseException as error:body=error;remember(error);raise
    finally:
        # Never touch the DB again if original rollback/close became uncertain.
        if not value.store.strict_cleanup_uncertain() and value.store.strict_cleanup_confirmed():
            try:
                with value.store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    require(db.execute(f'UPDATE {table} SET {column}=? WHERE id=? AND chunk_id=?',
                        (row[column],row['id'],value.cid)).rowcount==1,'one_original_row_restored')
                    if table=='segments':
                        # Original primary-change trigger revoked the inventory.
                        # Restore its exact saved body after restoring primary fields;
                        # do not rewind original revision/change-feed side effects.
                        db.execute('UPDATE segments SET lexical_evidence=? WHERE id=? AND chunk_id=?',
                            (row['lexical_evidence'],row['id'],value.cid))
                positive(value.store)
            except BaseException as error:
                remember(error,True)
                if body is None:raise
    return answer


def durable_witness_controls(empty,unknown):
    a=snapshot(empty);b=snapshot(unknown)
    require(len(a['witnesses'])==1 and len(b['witnesses'])==2,'packetless_witness_exact_counts')
    row=a['witnesses'][0]
    expect_sql_refusal(empty,f'UPDATE {WITNESS_TABLE} SET body=body WHERE cid=?',(empty.cid,),
        'strict_payload_witness_immutable')
    expect_sql_refusal(empty,f'DELETE FROM {WITNESS_TABLE} WHERE cid=?',(empty.cid,),
        'strict_payload_witness_retained')
    # Identical INSERT still reaches the real admission/unique-key defense.
    columns=list(row)
    caught=None
    try:
        with empty.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute(f'INSERT INTO {WITNESS_TABLE}('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row.values()))
    except sqlite3.IntegrityError as error:caught=error;retain_expected(error)
    require(caught is not None,'extra_witness_rejected');positive(empty.store)
    negative=[witness_fault(empty,'missing','payload_witness_missing'),
        witness_fault(empty,'body','payload_witness_body_digest'),
        row_fault(empty,'speaker_turns',a['turns'][0],'end',a['turns'][0]['end']+1/RATE,'payload_turn_geometry')]
    empty_turn=next(row for row in a['turns'] if row['speaker_window_start']==1.)
    negative.append(row_fault(empty,'speaker_turns',empty_turn,'end',empty_turn['end']+1/RATE,
        'payload_witness_turn_row_changed'))
    target=b['segments'][0];require(target['confidence']==0.,'known_generated_confidence')
    negative.append(row_fault(unknown,'segments',target,'confidence',.5,'payload_witness_unknown_row'))
    require(snapshot(empty)==a and snapshot(unknown)==b,'all_generated_changes_restored');cold_read(empty)
    return {'case':'durable_witness_integrity','immutable_sql_refusals':3,'collector_refusals':negative,
        'projection_faults_are_synthetic':True,'real_row_mutations':3,'placements_unchanged':True,'revision_side_effects_not_erased':True}


def erasure_case(value):
    before=snapshot(value);support_id=before['support'][0]['id']
    audit=[(r['cid'],r['core_start'],hashlib.sha256(r['body']).hexdigest()) for r in before['witnesses']]
    answer=value.store.delete_session(value.sid)
    positive(value.store);require(answer['cleanup_pending']==0,'original_source_cleanup_positive')
    require(not value.original.exists() and not value.work.exists(),'actual_generated_source_erasure')
    with value.store.db() as db:
        db.execute('BEGIN')
        saved=[(r['cid'],r['core_start'],hashlib.sha256(r['body']).hexdigest())
            for r in db.execute(f'SELECT cid,core_start,body FROM {WITNESS_TABLE} WHERE cid=? ORDER BY core_start LIMIT 5',(value.cid,))]
        outcome_count=db.execute('SELECT COUNT(*) FROM strict_recording_outcome WHERE cid=?',(value.cid,)).fetchone()[0]
        chunk_count=db.execute('SELECT COUNT(*) FROM chunks WHERE id=?',(value.cid,)).fetchone()[0]
    require(saved==audit and outcome_count==2 and chunk_count==0,'audit_retained_not_current')
    caught=None
    try:support.audio(value.store,value.sid,support_id)
    except KeyError as error:caught=error;retain_expected(error)
    require(caught is not None,'erased_source_cannot_read');positive(value.store)
    refusal=collect(value,refusal='payload_chunk_missing',expected_type=payload.base.ArchiveUnavailable)
    return {'case':'source_erasure_audit','witnesses_retained':len(saved),'outcomes_retained':outcome_count,
        'physical_sources_removed':True,'current_source_refused':True,'collector':refusal}


@contextmanager
def raw_catalog_db(path):
    """One retained generated-catalog owner; no substitute on uncertainty."""
    require(PENDING['raw_db'] is None,'catalog_owner_occupied');body=None;known=False;closed=False
    try:
        PENDING['raw_db']=sqlite3.connect(str(path),timeout=0.)
        db=PENDING['raw_db'];db.row_factory=sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        try:yield db
        except BaseException as error:
            body=error
            try:db.rollback();known=True
            except BaseException as cleanup:remember(error);remember(cleanup,True)
            raise
        else:
            try:db.commit();known=True
            except BaseException as error:body=error;remember(error);raise
    except BaseException as error:
        if body is None:body=error
        remember(body);raise
    finally:
        db=PENDING['raw_db']
        if db is not None:
            try:db.close();closed=True
            except BaseException as error:
                if body is not None:remember(body)
                remember(error,True)
                if body is None:raise
            if known and closed:PENDING['raw_db']=None



def old_catalog_controls(root,current):
    """Exact old strict catalog, NOT a full old Store or migration experiment."""
    statements=list(OLD_STRICT_SQL);require(len(statements)==28,'exact_old_28_object_catalog')
    tree=ast.parse(OLD_ADMISSION_SOURCE,'embedded:candidate12/strict_recording.py')
    require([node.name for node in tree.body]==['_canonical_ddl','admit_schema'],'exact_old_admission_nodes')
    strict_old={'re':re,'POLICY':'strict-primary-manual-v1','ARCHIVE_POLICY':'strict-primary-archive-v2',
        'PAYLOAD_POLICY':'strict-primary-payload-v2','schema_statements':lambda policy:statements}
    exec(compile(tree,'embedded:candidate12/strict_recording.py','exec'),strict_old)
    oldroot=root/'old_catalog';oldroot.mkdir();path=oldroot/'primary.sqlite'
    with raw_catalog_db(path) as db:
        # These five minimal tables merely permit original ON-table triggers.
        # They are not a fabricated claim to a complete prior application DB.
        for table in ('sessions','chunks','native_core_commits','speaker_support','speaker_support_vectors'):
            db.execute(f'CREATE TABLE {table}(id TEXT PRIMARY KEY)')
        for sql in statements:db.execute(sql)
        db.execute('INSERT INTO strict_payload_policy VALUES(1,?)',(payload.POLICY,))
        before=[tuple(row) for row in db.execute('SELECT name,type,sql FROM sqlite_master ORDER BY name LIMIT 64')]
        marker=tuple(db.execute('SELECT * FROM strict_payload_policy').fetchone())
    obj=Store.__new__(Store);PENDING['initializer']=obj;caught=None
    try:Store.__init__(obj,path,configuration(oldroot))
    except ValueError as error:
        caught=error;retain_expected(error);require(type(error) is ValueError and str(error)=='strict_schema_mismatch','new_refuses_exact_old_catalog')
    require(caught is not None,'old_catalog_not_adopted')
    from audio_backend.storage import pending_strict_initializer
    require(pending_strict_initializer() is None,'old_initializer_positive_close');positive(obj)
    with raw_catalog_db(path) as db:
        after=[tuple(row) for row in db.execute('SELECT name,type,sql FROM sqlite_master ORDER BY name LIMIT 64')]
        require(after==before and tuple(db.execute('SELECT * FROM strict_payload_policy').fetchone())==marker,'no_catalog_migration')
    reverse=None
    try:
        with current.store.db() as db:
            db.execute('BEGIN');strict_old['admit_schema'](db,strict_old['PAYLOAD_POLICY'])
    except ValueError as error:
        reverse=error;retain_expected(error);require(type(error) is ValueError and str(error)=='strict_schema_mismatch','old_refuses_new_exact_catalog')
    require(reverse is not None,'reverse_catalog_refusal');positive(current.store)
    with current.store.db() as db:
        db.execute('BEGIN')
        new_marker=tuple(db.execute('SELECT singleton,policy,format FROM strict_payload_policy').fetchone())
        count=db.execute("SELECT COUNT(*) FROM sqlite_master WHERE lower(name) GLOB 'strict_*'").fetchone()[0]
    require(new_marker==(1,payload.POLICY,payload.FORMAT) and count==32,'new_exact_marker_and_catalog')
    return {'case':'fresh_catalog_compatibility','old_objects':28,'new_objects':32,
        'old_source_sha256':OLD_SOURCE_SHA256,'forward_refused':True,'reverse_refused':True,
        'scope':'generated exact old strict catalog with minimal trigger support, not full old Store migration'}


def run(generated_root,*,deadline):
    require(STATE['deadline'] is None,'single_run_only');STATE['deadline']=deadline
    root=Path(generated_root).absolute();require(root.is_dir(),'generated_root')
    original_collect=payload._collect;original_builder=native_fixture.synthetic_provenance
    def observe_builder():
        require(STATE['builder_calls']<80,'pure_builder_source_capacity');STATE['builder_calls']+=1
        return original_builder()
    def observe_collect(owner,cid):
        index=STATE['collector_calls'];require(index<12,'twelve_collector_ceiling')
        PENDING['collectors'][index]=owner;STATE['collector_calls']+=1
        try:
            answer=original_collect(owner,cid)
            rows=answer[0]['metadata']['core_witnesses']
            require(len(rows)<=4,'bounded_archive_witness_observation')
            STATE['collector_witnesses'][index]=(len(rows),digest(rows))
            return answer
        except BaseException as error:PENDING['collector_errors'][index]=error;raise
    native_fixture.synthetic_provenance=observe_builder;payload._collect=observe_collect;first=None
    try:
        store,owner,authority=new_store(root,0);a,b=seed(store,owner,origin=10.5,count=2)
        commit_two(a,owner,authority,cut=.805,turn=(2320,27920))
        REPORT['cases'].append(cold_baseline(a,'fractional_cold_and_isolation'))
        store,owner,authority=new_store(root,1);empty,=seed(store,owner)
        commit_two(empty,owner,authority,modes=('retained','empty'))
        REPORT['cases'].append(cold_baseline(empty,'empty_native_turns_archived'))
        store,owner,authority=new_store(root,2);unknown,=seed(store,owner)
        commit_two(unknown,owner,authority,turn=(8000,24000),modes=('unknown_only','unknown_only'))
        REPORT['cases'].append(accepted_case(unknown,'actual_unknown_primary'))
        store,owner,authority=new_store(root,3);rollback,=seed(store,owner)
        commit_two(rollback,owner,authority,modes=('empty','retained'),faults=True)
        require(len(snapshot(rollback)['witnesses'])==1,'success_after_rollback_has_one_witness')
        REPORT['cases'].append(durable_witness_controls(empty,unknown))
        commit_two(b,PENDING['receipt_owners'][0],PENDING['authorities'][0])
        isolation=accepted_case(b,'next_hot_B')
        require(snapshot(a)==a.cold_snapshot,'cold_A_immutable_after_next_hot_B');cold_read(a)
        REPORT['cases'][0]['isolation']=isolation;REPORT['cases'][0]['cold_api_still_exact']=True
        REPORT['cases'].append(erasure_case(unknown))
        REPORT['cases'].append(old_catalog_controls(root,empty))
        require(STATE['chunks']==5 and STATE['commits']==10 and STATE['commit_attempts']==13,'fixed_commit_matrix')
        require(STATE['collector_calls']==12 and STATE['builder_calls']==52,'fixed_primitive_counts')
        owners=owner_summary();require(owners['cleanup_confirmed'] and owners['physical_io_clear'],'positive_original_owners')
        require(len(REPORT['cases'])==8,'eight_fixed_groups')
        REPORT.update(store_count=4,constructor_refusals=1,chunk_count=5,commit_count=10,commit_attempts=13,
            collector_calls=STATE['collector_calls'],builder_calls=STATE['builder_calls'],
            builder_source_bytes=STATE['builder_calls']*14870,old_source_reads=STATE['source_reads'],
            old_source_bytes=STATE['source_bytes'],owners=owners,
            scope='original component chain; synthetic numerical frontier/native/embedding edges; not Worker/model or long-run qualification')
        require(len(json.dumps(REPORT,sort_keys=True).encode())<=64*1024,'report_capacity');return REPORT
    except BaseException as error:first=error;remember(error);raise PENDING['first_error']
    finally:
        cleanup_error=None
        for module,name,observer,original in ((native_fixture,'synthetic_provenance',observe_builder,original_builder),
            (payload,'_collect',observe_collect,original_collect)):
            if getattr(module,name) is observer:setattr(module,name,original)
            else:
                error=AssertionError(name+'_observer_changed');remember(error,True)
                if cleanup_error is None:cleanup_error=error
        if first is None and cleanup_error is not None:raise cleanup_error


def test_strict_payload_turn_witness(tmp_path):
    """Exactly the separately bounded workload; no larger test collection."""
    run(tmp_path,deadline=time.monotonic()+15.)
