"""Bounded durable intents for timestamped recording overlap; no executor.

The existing Worker supplies one idle opportunity. The existing CellStore and
MossJobs own all PCM, native execution and cleanup. Historical admissions are
never inference retry instructions.
"""
from contextlib import contextmanager
from collections import deque
import hashlib
import json
import sqlite3
import time

from .native_owned_cells import (Cell, Unavailable, canonical, sha, validate_document,
    SourceSpan, PROOF_BYTES, BATCH_CELLS, exact_frame)
from .native_owned_support_cache import ReadError

_WINDOW_READ_DEFERRED = object()

TABLE = 'moss_automatic_intents_v1'
USAGE = 'moss_automatic_usage_v1'
MAX_ROWS, SESSION_ROWS = 4096, 1024
MAX_BYTES, SESSION_BYTES = 64 * 1024**2, 16 * 1024**2
MAX_PENDING, SESSION_PENDING = 256, 64
MAX_PROOFS, SESSION_PROOFS = 16 * 1024**2, 4 * 1024**2
BINDING_BYTES, ACCOUNTING_BYTES = 4096, 16384
MAX_CHARGE = ACCOUNTING_BYTES + PROOF_BYTES + BINDING_BYTES
CORE_INTENTS = BATCH_CELLS + 1
ACTIVE = ('queued', 'published', 'accepted')
POLICY = sha({'schema':'automatic-recording-overlap-v1', 'coverage':'ordinary-and-core-seam',
    'selection':'existing-native-positive-overlap', 'execution':'one-attempt-no-unknown-replay',
    'output':'separate-primary-and-moss-unreconciled'})
COLUMNS = ('seq,candidate_key,sid,kind,binding,proof_sha256,cell_id,slot1,slot2,cid1,cid2,'
           'state,reason,created,updated,job_id,jobs_epoch,request_sha256,charge_bytes')
SCHEMA = {
TABLE: f'''CREATE TABLE {TABLE} (
    seq INTEGER PRIMARY KEY AUTOINCREMENT CHECK(seq>0),
    candidate_key TEXT NOT NULL UNIQUE CHECK(length(candidate_key)=64),
    sid TEXT NOT NULL REFERENCES sessions(id), kind TEXT NOT NULL CHECK(kind IN ('ordinary','core_seam','reset_seam')),
    binding BLOB NOT NULL CHECK(typeof(binding)='blob' AND length(binding) BETWEEN 1 AND 4096),
    proof BLOB CHECK(proof IS NULL OR (typeof(proof)='blob' AND length(proof) BETWEEN 1 AND 65536)),
    proof_sha256 TEXT NOT NULL CHECK(length(proof_sha256)=64), cell_id TEXT NOT NULL CHECK(length(cell_id)=64),
    slot1 TEXT NOT NULL CHECK(length(slot1)=64), slot2 TEXT CHECK(slot2 IS NULL OR length(slot2)=64),
    cid1 TEXT NOT NULL, cid2 TEXT,
    state TEXT NOT NULL CHECK(state IN ('queued','published','accepted','committed','no_result','interrupted','cancelled')),
    reason TEXT NOT NULL CHECK(length(reason)<=80), created REAL NOT NULL, updated REAL NOT NULL,
    job_id TEXT, jobs_epoch TEXT, request_sha256 TEXT,
    charge_bytes INTEGER NOT NULL CHECK(charge_bytes BETWEEN 16385 AND 86016),
    CHECK((state IN ('queued','published','accepted') AND proof IS NOT NULL) OR state NOT IN ('queued','published','accepted')),
    CHECK((job_id IS NULL AND jobs_epoch IS NULL AND request_sha256 IS NULL) OR
          (length(job_id)=32 AND length(jobs_epoch)=32 AND length(request_sha256)=64)))''',
USAGE: f'''CREATE TABLE {USAGE} (scope TEXT PRIMARY KEY, rows INTEGER NOT NULL,
    charge INTEGER NOT NULL, pending INTEGER NOT NULL, proofs INTEGER NOT NULL,
    CHECK(rows BETWEEN 0 AND 4096), CHECK(charge BETWEEN 0 AND 67108864),
    CHECK(pending BETWEEN 0 AND 256), CHECK(proofs BETWEEN 0 AND 16777216))''',
'auto_session_seq': f'CREATE INDEX auto_session_seq ON {TABLE}(sid,seq)',
'auto_state_seq': f'CREATE INDEX auto_state_seq ON {TABLE}(state,seq)',
'auto_cell': f'CREATE INDEX auto_cell ON {TABLE}(cell_id)',
'auto_job': f'CREATE UNIQUE INDEX auto_job ON {TABLE}(job_id)',
'auto_cid1': f'CREATE INDEX auto_cid1 ON {TABLE}(cid1,state)',
'auto_cid2': f'CREATE INDEX auto_cid2 ON {TABLE}(cid2,state)',
'auto_slot1': f'CREATE INDEX auto_slot1 ON {TABLE}(slot1,state)',
'auto_slot2': f'CREATE INDEX auto_slot2 ON {TABLE}(slot2,state)',
'auto_cancel': f'''CREATE TRIGGER auto_cancel AFTER UPDATE OF status ON chunks WHEN NEW.status='cancelled' BEGIN
    UPDATE {USAGE} SET
      charge=charge-COALESCE((SELECT SUM(length(proof)) FROM {TABLE} WHERE sid=NEW.session_id AND state IN ('queued','published','accepted')),0),
      pending=pending-(SELECT COUNT(*) FROM {TABLE} WHERE sid=NEW.session_id AND state IN ('queued','published','accepted')),
      proofs=proofs-COALESCE((SELECT SUM(length(proof)) FROM {TABLE} WHERE sid=NEW.session_id AND state IN ('queued','published','accepted')),0)
      WHERE scope IN ('',NEW.session_id);
    UPDATE {TABLE} SET state='cancelled',reason='primary_cancelled',charge_bytes=charge_bytes-length(proof),proof=NULL
      WHERE sid=NEW.session_id AND state IN ('queued','published','accepted'); END''',
}


def init_schema(db):
    present = [db.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone() for name in SCHEMA]
    if any(present) and not all(present):raise ValueError('Incomplete automatic MOSS schema')
    for (name, sql), row in zip(SCHEMA.items(), present):
        if row is None:db.execute(sql)
        elif ' '.join(row[0].split()) != ' '.join(sql.split()):raise ValueError('Incompatible automatic MOSS schema')
    if not any(present):db.execute(f'INSERT INTO {USAGE} VALUES(?,0,0,0,0)',('',))


def enabled(settings):return bool(getattr(settings,'moss_automatic_enabled',False))


def recording(db, cid):
    return db.execute('SELECT 1 FROM recording_work WHERE chunk_id=? LIMIT 1',(cid,)).fetchone() is not None


def usage(db, sid):
    result=[]
    for scope in ('',sid):
        row=db.execute(f'SELECT rows,charge,pending,proofs FROM {USAGE} WHERE scope=?',(scope,)).fetchone()
        if row is None:
            if scope=='':raise ReadError(503,'automatic_usage_invalid')
            row=(0,0,0,0)
        values=tuple(row)
        if (any(type(v) is not int or v<0 for v in values) or values[0]>MAX_ROWS
                or values[1]>MAX_BYTES or values[2]>min(MAX_PENDING,values[0])
                or values[3]>min(MAX_PROOFS,values[2]*PROOF_BYTES)
                or values[1]<values[0]*ACCOUNTING_BYTES):
            raise ReadError(503,'automatic_usage_invalid')
        result.append(values)
    if any(a>b for a,b in zip(result[1],result[0])):raise ReadError(503,'automatic_usage_invalid')
    return result


def capacity(db, sid, *, rows=CORE_INTENTS, charge=CORE_INTENTS*MAX_CHARGE,
             pending=CORE_INTENTS, proofs=CORE_INTENTS*PROOF_BYTES):
    current=usage(db,sid)
    for have,limits in zip(current,((MAX_ROWS,MAX_BYTES,MAX_PENDING,MAX_PROOFS),
                                  (SESSION_ROWS,SESSION_BYTES,SESSION_PENDING,SESSION_PROOFS))):
        for a,b,c,name in zip(have,(rows,charge,pending,proofs),limits,
                             ('rows','bytes','pending','proof_bytes')):
            if a+b>c:raise ReadError(429,'automatic_'+name+'_capacity')
    return current


def _charge(db,sid,delta):
    usage(db,sid)
    db.execute(f'INSERT OR IGNORE INTO {USAGE} VALUES(?,0,0,0,0)',(sid,))
    for scope in ('',sid):
        db.execute(f'UPDATE {USAGE} SET rows=rows+?,charge=charge+?,pending=pending+?,proofs=proofs+? WHERE scope=?',(*delta,scope))


def _terminal(db,row,state,reason):
    if row['state'] not in ACTIVE:return
    n=db.execute(f'SELECT length(proof) FROM {TABLE} WHERE candidate_key=?',(row['candidate_key'],)).fetchone()[0]
    charge=ACCOUNTING_BYTES+len(row['binding'])
    _charge(db,row['sid'],(0,charge-row['charge_bytes'],-1,-n))
    db.execute(f'UPDATE {TABLE} SET state=?,reason=?,proof=NULL,charge_bytes=?,updated=? WHERE candidate_key=? AND state=?',
        (state,reason,charge,time.time(),row['candidate_key'],row['state']))


def cancel_session(db,sid,reason='primary_cancelled'):
    # Bounded by the explicit per-session retained row cap; no proof projection.
    for row in db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE sid=? AND state IN (\'queued\',\'published\',\'accepted\')',(sid,)).fetchall():
        _terminal(db,row,'cancelled',reason)


def erase_session(db,sid):
    total,own=usage(db,sid)
    db.execute(f'DELETE FROM {TABLE} WHERE sid=?',(sid,))
    db.execute(f'UPDATE {USAGE} SET rows=?,charge=?,pending=?,proofs=? WHERE scope=?',
               (*(a-b for a,b in zip(total,own)),''))
    db.execute(f'DELETE FROM {USAGE} WHERE scope=?',(sid,))


def root(db, *, cid=None, slot=None):
    name,value=('cid',cid) if cid is not None else ('slot',slot)
    if value is None:return False
    for suffix in ('1','2'):
        field=name+suffix
        if db.execute(f'SELECT 1 FROM {TABLE} INDEXED BY auto_{field} WHERE {field}=? AND state IN (\'queued\',\'published\',\'accepted\') LIMIT 1',(value,)).fetchone():return True
    return False


def ordinary_binding(doc,commit):
    from .native_owned_core_seams import validate_commit
    p=validate_commit(commit); c=doc['commit']; evidence=doc['commit_evidence']; support=doc['support']
    if (c!={'chunk_id':p.chunk_id,'start':p.source_start,'end':p.source_end,'sha256':commit['evidence_sha256']}
            or sha(evidence)!=commit['evidence_sha256'] or sha(evidence['native'])!=commit['native_sha256']
            or sha(evidence['core_cut'])!=commit['cut_sha256']
            or sha(doc['native']['provenance'])!=commit['runtime_sha256']
            or support['core_start_frame']!=p.logical_start or support['core_end_frame']!=p.logical_end):
        raise Unavailable('automatic_core_binding')
    for raw in doc['dependencies']:
        part=SourceSpan(**raw)
        if (part.session_id!=p.session_id or part.chunk_id!=p.chunk_id
                or part.receipt_sha256!=p.receipt_sha256 or part.source_sha256!=p.source_sha256
                or part.source_frames!=p.source_frames or not p.source_start<=part.source_start<part.source_end<=p.source_end
                or part.logical_start-part.source_start!=p.logical_start-p.source_start):
            raise Unavailable('automatic_source_binding')


def source_binding(kind,doc,support,parts,commits):
    binding={'automation_policy':POLICY,'selection_policy':doc['policy'],
        'kind':kind,'session_id':parts[0].session_id,
        'parts':[p.content() for p in parts],
        'source_aliases':[{'chunk_id':p.chunk_id,'receipt_sha256':p.receipt_sha256} for p in parts],
        'support_frames':[support.start_frame,support.end_frame],
        'core_frames':[[c['part']['logical_start'],c['part']['logical_end']] for c in commits]}
    return canonical(binding,BINDING_BYTES)


def plans(cells,packet):
    from .native_owned_core_seams import slot, is_core, validate_commit, RESET_SCHEMA
    if type(cells) is not tuple or len(cells)>BATCH_CELLS:raise Unavailable('automatic_core_candidate_capacity')
    if type(packet) is not dict:raise Unavailable('automatic_core_commit_required')
    commit=packet['commit']; validate_commit(commit)
    raw=[]
    for cell in cells:
        if type(cell) is not Cell:raise Unavailable('automatic_cell_required')
        doc,support,parts=validate_document(cell.proof)
        if 'schema' in doc:raise Unavailable('automatic_ordinary_required')
        ordinary_binding(doc,commit)
        raw.append(('ordinary',cell.proof,support,parts,(commit,)))
    if packet['proof'] is not None:
        doc,support,parts=validate_document(packet['proof'])
        if not is_core(doc) or doc['commits'][1]!=commit:raise Unavailable('automatic_seam_binding')
        # Restart replay remains in the existing core_seam family. Its distinct
        # selection policy is already bound into ownership; no SQL migration,
        # relabeling of historical rows, or retry of accepted work is needed.
        raw.append(('reset_seam' if doc['schema']==RESET_SCHEMA else 'core_seam',packet['proof'],support,parts,tuple(doc['commits'])))
    if len(raw)>CORE_INTENTS:raise Unavailable('automatic_core_candidate_capacity')
    result=[]
    for kind,proof,support,parts,commits in raw:
        if len(proof)>PROOF_BYTES or not 1<=len(parts)<=2:raise Unavailable('automatic_proof_capacity')
        cell_id=sha({'policy':json.loads(proof)['policy'],'support':support.to_dict()})
        # Epoch/run labels are proof evidence, not the candidate's ownership key.
        body=source_binding(kind,json.loads(proof),support,parts,commits); key=hashlib.sha256(body).hexdigest()
        roots=[c['part']['chunk_id'] for c in commits]; slots=[slot(c) for c in commits]
        result.append(dict(key=key,sid=parts[0].session_id,kind=kind,binding=body,proof=proof,
            proof_sha256=hashlib.sha256(proof).hexdigest(),cell_id=cell_id,
            slot1=slots[0],slot2=slots[1] if len(slots)==2 else None,
            cid1=roots[0],cid2=roots[1] if len(roots)==2 else None))
    if len({r['key'] for r in result})!=len(result):raise Unavailable('automatic_duplicate_ownership')
    return result


def enqueue(db,cells,packet):
    from .native_owned_core_seams import slot,canonical as core_canonical,COMPACT_BYTES
    prepared=plans(cells,packet)
    if not prepared:return
    sid=prepared[0]['sid']; new=[]
    for p in prepared:
        old=db.execute(f'SELECT {COLUMNS},proof FROM {TABLE} WHERE candidate_key=?',(p['key'],)).fetchone()
        if old is not None:
            if old['binding']!=p['binding'] or old['proof_sha256']!=p['proof_sha256'] or old['cell_id']!=p['cell_id']:
                raise Unavailable('automatic_candidate_conflict')
            continue
        new.append(p)
    capacity(db,sid,rows=len(new),pending=len(new),proofs=sum(len(p['proof']) for p in new),
             charge=sum(ACCOUNTING_BYTES+len(p['proof'])+len(p['binding']) for p in new))
    # Exact compact existence is checked inside the very same primary commit.
    for p in new:
        for s in (p['slot1'],p['slot2']):
            if s is not None and not db.execute("SELECT 1 FROM native_core_commits WHERE slot=? AND state!='cancelled'",(s,)).fetchone():
                raise Unavailable('automatic_core_missing')
        charge=ACCOUNTING_BYTES+len(p['proof'])+len(p['binding']); now=time.time()
        db.execute(f'''INSERT INTO {TABLE}(candidate_key,sid,kind,binding,proof,proof_sha256,cell_id,slot1,slot2,cid1,cid2,state,reason,created,updated,charge_bytes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued','awaiting_publication',?,?,?)''',
            (p['key'],sid,p['kind'],p['binding'],p['proof'],p['proof_sha256'],p['cell_id'],p['slot1'],p['slot2'],p['cid1'],p['cid2'],now,now,charge))
        _charge(db,sid,(1,charge,1,len(p['proof'])))


def _linked(db,key,identity):
    row=db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE candidate_key=? AND sid=?',(key,identity.session_id)).fetchone()
    if row is None:raise ReadError(409,'automatic_intent_missing')
    if (row['job_id'],row['jobs_epoch'],row['request_sha256'])!=(identity.job_id,identity.original_jobs_epoch,identity.canonical_request_sha256):
        raise ReadError(409,'automatic_result_binding')
    return row


def admit(db,key,identity):
    row=db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE candidate_key=? AND sid=?',(key,identity.session_id)).fetchone()
    if row is None or row['state']!='published' or row['job_id'] is not None:raise ReadError(409,'automatic_admission_closed')
    if db.execute("SELECT 1 FROM chunks WHERE session_id=? AND status='cancelled' LIMIT 1",(identity.session_id,)).fetchone():
        raise ReadError(409,'automatic_cancelled')
    db.execute(f"UPDATE {TABLE} SET state='accepted',reason='admitted_no_replay',job_id=?,jobs_epoch=?,request_sha256=?,updated=? WHERE candidate_key=? AND state='published'",
               (identity.job_id,identity.original_jobs_epoch,identity.canonical_request_sha256,time.time(),key))


def complete(db,key,identity,*,state='committed',reason='result_committed'):
    row=_linked(db,key,identity)
    if row['state']==state:return
    if row['state']!='accepted':raise ReadError(409,'automatic_publication_veto')
    if db.execute("SELECT 1 FROM chunks WHERE session_id=? AND status='cancelled' LIMIT 1",(identity.session_id,)).fetchone():
        raise ReadError(409,'automatic_publication_veto')
    _terminal(db,row,state,reason)


def reconcile_job(db,job_id):
    row=db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE job_id=?',(job_id,)).fetchone()
    if row is not None and row['state']=='accepted':_terminal(db,row,'interrupted','prior_admission_unknown_no_replay')


def authority_valid(authority,key,doc,budget):
    from .native_owned_core_seams import read_commit,is_core,current,slot
    raw=canonical(doc); proof_hash=hashlib.sha256(raw).hexdigest()
    def read(db):
        row=db.execute(f'SELECT {COLUMNS},length(proof) AS pn FROM {TABLE} WHERE candidate_key=?',(key,)).fetchone()
        if (row is None or row['state'] not in ACTIVE or row['pn']!=len(raw) or row['proof_sha256']!=proof_hash
                or hashlib.sha256(row['binding']).hexdigest()!=key):return None
        same=db.execute(f'SELECT proof=? FROM {TABLE} WHERE candidate_key=?',(raw,key)).fetchone()[0]
        if not same:return None
        commits=[]
        for s in (row['slot1'],row['slot2']):
            if s is not None:
                c=read_commit(db,s,budget)
                live=db.execute("SELECT 1 FROM native_core_commits WHERE slot=? AND state!='cancelled'",(s,)).fetchone()
                if c is None or not live:return None
                commits.append(c)
        budget.projection(len(row['binding'])+256)
        return row,commits
    found=authority._read(budget,'automatic_intent',read)
    if found is None:return False
    row,commits=found
    parsed,support,parts=validate_document(raw)
    if (row['binding']!=source_binding(row['kind'],doc,support,parts,commits)
            or row['cell_id']!=sha({'policy':doc['policy'],'support':support.to_dict()})
            or row['cid1']!=commits[0]['part']['chunk_id']
            or row['cid2']!=(commits[1]['part']['chunk_id'] if len(commits)==2 else None)):
        return False
    if is_core(doc):
        return doc['commits']==commits and current(authority,doc,budget)
    if len(commits)!=1:return False
    ordinary_binding(doc,commits[0])
    if not authority.cell_valid(doc,budget,publishing=False):return False
    c=authority.row(doc['commit']['chunk_id'],budget)
    if c is None or c['status'] in ('cancelled','failed','staging'):return False
    support=doc['support']; origin=float.fromhex(commits[0]['scope']['origin'])
    return (authority.mapping(c,origin,support['start_frame'],support['end_frame'],budget)
            ==tuple(SourceSpan(**p) for p in doc['dependencies']))


class Automatic:
    def __init__(self,store,results,*,capture=None,jobs=None):
        self.store,self.results,self.capture,self.jobs=store,results,capture,jobs
        self.flight=None; self.next_attempt=0.; self.last_reason='not_started'
        self._round={};self._sessions=deque()
        self.closed=False

    def current(self):
        return (not self.closed and enabled(self.store.settings) and self.capture is not None
                and self.jobs is not None and self.jobs._enabled())

    def before_window(self,chunk):
        if not enabled(self.store.settings):return True
        from .moss_result_store import _ReadDeferred
        try:
            with self.results._database() as db:
                if not recording(db,chunk['id']):return True
                try:capacity(db,chunk['session_id'])
                except ReadError as exc:self.last_reason=exc.reason; return False
                from .native_owned_core_seams import MAX_COMPACTS
                if db.execute('SELECT COUNT(*) FROM native_core_commits').fetchone()[0]>=MAX_COMPACTS:
                    self.last_reason='automatic_compact_capacity'; return False
        except _ReadDeferred as exc:
            self.last_reason='automatic_preflight_read_deferred'
            try:
                import logging
                fields={'operation':'read','callsite':'before_window','phase':exc.phase,
                    'reason':'result_storage_unavailable','sqlite_code':exc.sqlite_code}
                logging.getLogger(__name__).warning('moss_automatic_preflight_deferred %s',
                    json.dumps(fields,sort_keys=True),extra={'automatic_preflight_deferred':fields})
            except BaseException:pass
            return _WINDOW_READ_DEFERRED
        if not self.current():self.last_reason='automatic_configuration_unavailable'; return False
        self.results._headroom()
        return True

    def read_candidate(self,key,*,proof=False):
        with self.results._database() as db:
            length=db.execute(f'SELECT length(binding),length(proof) FROM {TABLE} WHERE candidate_key=?',(key,)).fetchone()
            if length is None:return None
            if not 1<=length[0]<=BINDING_BYTES or proof and (length[1] is None or not 1<=length[1]<=PROOF_BYTES):
                raise ReadError(503,'automatic_payload_capacity')
            row=db.execute(f'SELECT {COLUMNS}'+(',proof' if proof else '')+f' FROM {TABLE} WHERE candidate_key=?',(key,)).fetchone()
            return dict(row) if row is not None else None

    def note(self,key,reason,*,published=False):
        if type(reason) is not str or len(reason)>80:reason='automatic_unavailable'
        with self.results._database(write=True) as db:
            db.execute(f"UPDATE {TABLE} SET reason=?,updated=?,state=CASE WHEN ? AND state='queued' THEN 'published' ELSE state END WHERE candidate_key=? AND state IN ('queued','published')",
                       (reason,time.time(),int(published),key))
        self.last_reason=reason

    def validate_record(self,record):
        from .native_owned_cell_store import Budget
        with Budget().phase() as budget:
            if not authority_valid(self.capture.authority,record['automatic_key'],json.loads(record['token'].proof),budget):
                raise ReadError(409,'automatic_source_revoked')
        return True

    def _snapshot(self):
        if self._sessions:return True
        with self.results._database() as db:
            rows=[]
            for state in ('queued','published'):
                rows.extend(db.execute(f"SELECT seq,sid,candidate_key,state FROM {TABLE} INDEXED BY auto_state_seq WHERE state=? ORDER BY seq LIMIT ?",
                    (state,MAX_PENDING+1)).fetchall())
        if len(rows)>MAX_PENDING:raise ReadError(503,'automatic_pending_capacity')
        grouped={}
        for row in sorted(rows,key=lambda x:x['seq']):
            group=grouped.setdefault(row['sid'],deque());group.append((row['seq'],row['candidate_key']))
            if len(group)>SESSION_PENDING:raise ReadError(503,'automatic_session_pending_capacity')
        self._round=grouped;self._sessions=deque(sorted(grouped))
        return bool(self._sessions)

    def _select_key(self):
        if not self._snapshot():return None
        sid=self._sessions.popleft();group=self._round[sid]
        _,key=group.popleft()
        if group:self._sessions.append(sid)
        else:del self._round[sid]
        return key

    def maintain_text_storage(self, *, stop=lambda:False):
        # Retained text ownership deliberately prevents a new automatic turn.
        # Its exact metadata-only acknowledgement must be reachable first.
        if self.jobs is None or stop() or time.monotonic() < self.next_attempt:return False
        try:return self.jobs.maintain_automatic_text_storage(self, stop=stop)
        except Exception:return False  # Ownership and the frozen payload remain.

    def primary_yield_due(self):
        """Bounded metadata hint; no proof/PCM, admission or storage retry."""
        try:
            if not self.current() or time.monotonic()<self.next_attempt:return False
            permit=self.jobs.permit
            state=permit.status()
            if state['admission_closed'] or state['optional_admission_unavailable'] or permit._optional is not None:return False
            # Storage acknowledgement is a separate turn even with no queued
            # model candidate. It never grants model readiness before the ack.
            if self.jobs.automatic_maintenance_hint():return True
            if not self.jobs.automatic_ready_hint():return False
            if self.flight is not None:return self.flight[1].slot_released.is_set()
            if self.capture.owner._slot is not None:return False
            return self._snapshot()
        except Exception:return False

    def _admit_key(self,key):
        selected=self.read_candidate(key)
        if selected is None or selected['state']!='published':return
        try:self.jobs.submit_automatic(selected)
        except ReadError as exc:self.note(key,exc.reason)

    def _finish_flight(self,stop):
        key,op,cell_id=self.flight
        # The existing owner deadline includes queue time. No new budget, no
        # second publication operation, and no wait on unknown model attempts.
        while not op.slot_released.is_set():
            remaining=op.deadline-time.monotonic()
            if stop() or remaining<=0:
                self.last_reason='automatic_publication_pending';return
            op.slot_released.wait(min(.05,remaining))
        if stop():return
        outcome=op.result or {};rows=outcome.get('cells',[])
        if outcome.get('status')=='durable' and len(rows)==1 and rows[0].get('cell_id')==cell_id:
            status=rows[0].get('status')
            if status in ('retained','existing'):
                self.note(key,'awaiting_admission',published=True)
                self.flight=None
                self._admit_key(key);return
            if status=='not_retained_pcm':self.note(key,'cell_pcm_not_retained');self.flight=None;return
            if status=='not_retained_metadata_capacity':self.note(key,'cell_metadata_capacity');self.flight=None;return
        self.note(key,'automatic_publication_unacknowledged');self.flight=None

    def step(self, *, stop=lambda:False):
        if not self.current() or time.monotonic()<self.next_attempt or stop():return
        self.next_attempt=time.monotonic()+1.
        try:
            if not self.jobs.automatic_idle():self.last_reason='automatic_job_slot_occupied';return
            if self.flight is not None:
                self._finish_flight(stop);return
            key=self._select_key()
            if key is None:
                with self.results._database() as db:
                    unknown=db.execute(f"SELECT 1 FROM {TABLE} INDEXED BY auto_state_seq WHERE state='accepted' LIMIT 1").fetchone()
                self.last_reason='automatic_recovery_required' if unknown else 'automatic_idle';return
            selected=self.read_candidate(key)
            if selected is None or selected['state'] not in ('queued','published'):return
            if selected['state']=='published':self._admit_key(key);return
            from .native_owned_cell_store import Budget,Operation
            from .native_owned_seam_cells import anchor
            if self.capture.owner._slot is not None:self.note(key,'cell_owner_busy');return
            selected=self.read_candidate(key,proof=True)
            if selected is None or selected['state']!='queued' or stop():return
            with Budget().phase() as budget:
                doc,support,parts=validate_document(selected['proof'])
                if not authority_valid(self.capture.authority,key,doc,budget):
                    self.note(key,'automatic_source_unavailable');return
                pcm=self.capture.authority.original_pcm(parts,budget);a=anchor(doc)
                cell=Cell(support,parts,selected['proof'],pcm,selected['sid'],a['chunk_id'],a['start'],a['end'],a['sha256'])
                op=self.capture.owner.submit('publish_auto',(key,cell))
                if isinstance(op,Operation):self.flight=(key,op,cell.cell_id)
                else:self.note(key,'cell_owner_busy');return
            self._finish_flight(stop)
        except Exception as exc:
            self.last_reason=exc.reason if isinstance(exc,ReadError) else 'automatic_maintenance_pending'

    def _projection(self,row,epoch=None):
        value={'sequence':row['seq'],'candidate_key':row['candidate_key'],'session_id':row['sid'],
            'kind':row['kind'],'state':row['state'],'reason':row['reason'],'created_at':row['created'],
            'updated_at':row['updated'],'cell_id':row['cell_id'],'proof_sha256':row['proof_sha256'],
            'source_binding':json.loads(row['binding']),'job_id':row['job_id'],
            'original_jobs_epoch':row['jobs_epoch'],'recovery_required':row['state']=='interrupted',
            'structured_result_url':('/sessions/'+row['sid']+'/moss-jobs/history/'+row['job_id']+'/result'
                if row['state']=='committed' and row['job_id'] else None)}
        live=getattr(self.jobs,'_live',None) if self.jobs is not None else None
        exact_live=(live is not None and live.get('automatic_key')==row['candidate_key']
            and live.get('job_id')==row['job_id']
            and (not live.get('cleaned',False) or
                 live.get('storage_pending',False) and live.get('history_snapshot') is not None))
        if row['state']=='accepted' and (epoch!=row['jobs_epoch'] or not exact_live):
            value['state']='interrupted_or_unknown';value['recovery_required']=True
        return value

    def health(self):
        try:return self.status()
        except Exception:return {'enabled':enabled(self.store.settings),'candidate_count':None,
            'pending_count':None,'oldest_queue_age_seconds':None,'reason':'automatic_status_unavailable',
            'in_flight':self.flight is not None,'discovery_complete':False}

    def status(self,sid=None):
        with self.results._database() as db:
            if sid is not None:self.results._session(db,sid)
            scope=sid or ''
            row=db.execute(f'SELECT rows,charge,pending,proofs FROM {USAGE} WHERE scope=?',(scope,)).fetchone() or (0,0,0,0)
            params=(sid,) if sid else ()
            where=' AND sid=?' if sid else ''
            counts={state:db.execute(f'SELECT COUNT(*) FROM {TABLE} WHERE state=?'+where,(state,*params)).fetchone()[0] for state in ACTIVE}
            oldest_rows=[db.execute(f'SELECT seq,created FROM {TABLE} INDEXED BY auto_state_seq WHERE state=?'+where+' ORDER BY seq LIMIT 1',(state,*params)).fetchone() for state in ('queued','published')]
            oldest=min((r for r in oldest_rows if r is not None),key=lambda r:r['seq'],default=None)
            pending_primary=db.execute("SELECT 1 FROM chunks WHERE (status IN ('queued','running','waiting_input','staging','paused') OR (status='failed' AND next_offset<duration-0.00001 AND EXISTS (SELECT 1 FROM recording_work rw WHERE rw.chunk_id=chunks.id)))"+(' AND session_id=?' if sid else '')+' LIMIT 1',params).fetchone()
        return {'enabled':enabled(self.store.settings),'candidate_count':row[0],'charge_bytes':row[1],
            'pending_count':row[2],'proof_bytes':row[3],**counts,
            'oldest_queue_age_seconds':max(0.,time.time()-oldest['created']) if oldest else None,
            'reason':self.last_reason,'in_flight':self.flight is not None,
            'discovery_complete':pending_primary is None,
            'limits':{'rows':MAX_ROWS,'session_rows':SESSION_ROWS,'bytes':MAX_BYTES,'session_bytes':SESSION_BYTES,
                'pending':MAX_PENDING,'session_pending':SESSION_PENDING,'proof_bytes':MAX_PROOFS,'session_proof_bytes':SESSION_PROOFS}}

    def list(self,sid,after=0,limit=20):
        if type(after) is not int or not 0<=after<(1<<63) or type(limit) is not int or not 1<=limit<=20:raise ReadError(409,'invalid_cursor')
        with self.results._database() as db:
            self.results._session(db,sid)
            rows=db.execute(f'SELECT {COLUMNS} FROM {TABLE} INDEXED BY auto_session_seq WHERE sid=? AND seq>? ORDER BY seq LIMIT ?',(sid,after,limit+1)).fetchall()
        values=[];last=after;more=False;epoch=self.jobs.jobs_epoch if self.jobs else None
        for row in rows:
            value=self._projection(row,epoch)
            if len(values)>=limit or len(canonical({'candidates':values+[value]},128*1024))>24576:more=True;break
            values.append(value);last=row['seq']
        return canonical({'candidates':values,'next_cursor':last,'has_more':more,'capacity':self.status(sid)},32768)

    def detail(self,sid,key):
        with self.results._database() as db:
            self.results._session(db,sid)
            row=db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE sid=? AND candidate_key=?',(sid,key)).fetchone()
            if row is None:raise ReadError(404,'automatic_candidate_not_found')
        return canonical(self._projection(row,self.jobs.jobs_epoch if self.jobs else None),16384)
