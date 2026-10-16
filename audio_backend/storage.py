"""SQLite-backed bounded queues. One OS process owns this store and its worker."""
from contextlib import contextmanager
import sqlite3
import json
import hashlib
import math
import time
import uuid
from pathlib import Path
from .boundary_store import init_stream_schema, register_stream_chunk, check_duplicate_stream, wake_stream_successor, PUBLISHED_CHUNK_SQL
from . import event_candidate_store, lifecycle, overlap_store, speaker_support, support_vectors, verified_identity, identity_canonical


STREAM_PENDING_COUNT_SQL="""SELECT COUNT(*) FROM chunks c INDEXED BY chunk_queue
    CROSS JOIN stream_chunks sc ON sc.chunk_id=c.id
    WHERE c.status IN ('queued','running','paused','waiting_input','failed','staging')
    AND sc.session_id=? AND sc.stream_id=?"""


def ident():
    return uuid.uuid4().hex


def normalize(values):
    if not isinstance(values, list) or not 8 <= len(values) <= 2048:
        return None
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return None
    norm = math.sqrt(sum(v*v for v in values))
    return [v/norm for v in values] if norm > 1e-8 else None


PROVENANCE_KEYS=('inference_revision','asr_backend','timestamp_semantics','asr_model_license',
                 'models','effective_config','requested_device','speaker_embedding_version',
                 'speaker_embedding_metric','speaker_embedding_sha256','speaker_embedding_dimension','event_window_seconds',
                 'event_hop_seconds','event_max_seconds','event_policy_revision','event_model_sha256',
                 'event_labelmap_sha256','event_artifact_identity_status','chinese_script','boundary_policy','speaker_identity_policy','warnings',
                 'diarization_backend','diarization_runtime_version','diarization_fingerprint',
                 'diarization_model_license','diarization_model_license_url','diarization_local_capacity',
                 'diarization_geometry','diarization_morphology_guard_seconds',
                 'diarization_epoch_input_cap_seconds','diarization_cpu_affinity_enforced',
                 'framemn_provenance')

def _retain_framemn_execution(provenance, metadata, offset):
    """Bounded per-chunk committed-window totals, separate from stable identities."""
    status = metadata.get('framemn_status')
    if status not in {'completed', 'failed', 'not_run_silence'}:
        return
    completed = metadata.get('framemn_model_call_count')
    expected = metadata.get('framemn_expected_model_calls')
    executed = metadata.get('framemn_model_inference_executed')
    if (type(completed) is not int or type(expected) is not int
            or not 0 <= completed <= expected <= 7 or type(executed) is not bool):
        raise ValueError('Invalid FrameMN execution counters')
    totals = provenance.setdefault('framemn_execution', {
        'schema': 'framemn_execution_v1', 'scope': 'committed_chunk_windows',
        'window_count': 0, 'completed_model_calls': 0, 'expected_model_calls': 0,
        'model_inference_windows': 0, 'failed_windows': 0, 'silence_windows': 0})
    totals['window_count'] += 1
    totals['completed_model_calls'] += completed
    totals['expected_model_calls'] += expected
    totals['model_inference_windows'] += int(executed)
    totals['failed_windows'] += int(status == 'failed')
    totals['silence_windows'] += int(status == 'not_run_silence')
    totals['last_window_offset'] = offset


def update_provenance(previous, metadata, offset):
    """Retain up to 16 distinct stable configurations; never relabel historical rows.

    Last-window dynamic metadata remains separate. Silence's empty model map does
    not create or replace stable model provenance. Bounds are independent of hours.
    """
    provenance=json.loads(previous) if previous else {'variants':[], 'variants_truncated':False}
    event_candidate_store.retain_projection_diagnostics(provenance,metadata,offset)
    _retain_framemn_execution(provenance, metadata, offset)
    stable={key:metadata[key] for key in PROVENANCE_KEYS if metadata.get(key) not in (None,{},[],'')}
    if not stable or metadata.get('silence_fast_path'):
        return provenance
    encoded=json.dumps(stable,sort_keys=True,ensure_ascii=False,allow_nan=False)
    if len(encoded.encode('utf-8'))>32768:
        stable={key:value for key,value in stable.items() if key in ('asr_backend','timestamp_semantics','inference_revision')}
        stable['details_omitted']='Stable metadata exceeded 32 KiB limit'
        encoded=json.dumps(stable,sort_keys=True,ensure_ascii=False,allow_nan=False)
        if len(encoded.encode('utf-8'))>32768:
            provenance['variants_truncated']=True
            return provenance
    fingerprint=hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    for variant in provenance['variants']:
        if variant['fingerprint']==fingerprint:
            variant['last_window_offset']=offset
            variant['window_count']+=1
            return provenance
    if len(provenance['variants'])>=16:
        provenance['variants_truncated']=True
        return provenance
    provenance['variants'].append({'fingerprint':fingerprint,'first_window_offset':offset,
        'last_window_offset':offset,'window_count':1,'details':stable})
    return provenance


class Store:
    def __init__(self, path: Path, settings):
        self.path, self.settings = path, settings
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, identity_group TEXT NOT NULL, created REAL NOT NULL,
                timeline_end REAL NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS chunks (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, session_id TEXT NOT NULL REFERENCES sessions(id),
                idempotency_key TEXT NOT NULL, digest TEXT NOT NULL, path TEXT NOT NULL,
                duration REAL NOT NULL, timeline_start REAL NOT NULL, recorded_at TEXT,
                status TEXT NOT NULL, next_offset REAL NOT NULL DEFAULT 0,
                error TEXT, created REAL NOT NULL, metadata TEXT, provenance TEXT,
                UNIQUE(session_id, idempotency_key));
            CREATE INDEX IF NOT EXISTS chunk_queue ON chunks(status, created);
            CREATE INDEX IF NOT EXISTS chunk_path ON chunks(path);
            CREATE INDEX IF NOT EXISTS chunk_timeline ON chunks(session_id,timeline_start);
            CREATE TABLE IF NOT EXISTS speakers (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, identity_group TEXT NOT NULL,
                name TEXT, embedding TEXT, state TEXT NOT NULL, merged_into TEXT,
                created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS speaker_examples (
                id TEXT PRIMARY KEY, speaker_id TEXT NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
                observation_id TEXT NOT NULL, source_session_id TEXT NOT NULL, source_segment_id INTEGER NOT NULL,
                embedding TEXT NOT NULL, embedding_version TEXT, embedding_evidence TEXT, created REAL NOT NULL,
                UNIQUE(speaker_id,observation_id));
            CREATE INDEX IF NOT EXISTS speaker_examples_person ON speaker_examples(speaker_id);
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL REFERENCES chunks(id),
                start REAL NOT NULL, end REAL NOT NULL, text TEXT NOT NULL,
                speaker_id TEXT, speaker_local_id TEXT, confidence REAL,
                speaker_match_score REAL, speaker_status TEXT NOT NULL,
                asr_backend TEXT, timestamp_kind TEXT, raw_text TEXT, candidate_observation_id TEXT);
            CREATE INDEX IF NOT EXISTS segments_chunk ON segments(chunk_id, start);
            CREATE TABLE IF NOT EXISTS speaker_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL REFERENCES chunks(id),
                start REAL NOT NULL, end REAL NOT NULL, speaker_id TEXT, speaker_local_id TEXT,
                speaker_match_score REAL, speaker_status TEXT NOT NULL, overlap INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS speaker_turns_chunk ON speaker_turns(chunk_id,start);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL REFERENCES chunks(id),
                start REAL NOT NULL, end REAL NOT NULL, label TEXT NOT NULL, score REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS speaker_observations (
                id TEXT PRIMARY KEY, identity_group TEXT NOT NULL, session_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL REFERENCES chunks(id), window_start REAL NOT NULL,
                local_speaker_id TEXT NOT NULL, embedding TEXT NOT NULL,
                enrolled_speaker_id TEXT, created REAL NOT NULL,
                UNIQUE(chunk_id,window_start,local_speaker_id));
            CREATE INDEX IF NOT EXISTS observation_group ON speaker_observations(identity_group);
            CREATE INDEX IF NOT EXISTS observation_session ON speaker_observations(session_id);
            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
            ''')
            init_stream_schema(db)
            overlap_store.init_schema(db)
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            event_candidate_store.init_schema(db)
            lifecycle.init_schema(db)
            from .recording_overlap import init_schema as init_recording_schema
            init_recording_schema(db)
            speaker_support.init_schema(db)
            verified_identity.init_schema(db)
            # Additive migration only. Existing rows remain NULL: a chunk's latest
            # metadata is not evidence about the backend/timings of older rows.
            for table, additions in {'segments': ('asr_backend','timestamp_kind','raw_text','candidate_observation_id','speaker_window_start','speaker_support_id','speaker_support_status','identity_decision_id','identity_profile_revision','identity_state'),
                                      'chunks': ('provenance',),
                                      'events': ('time_precision','source_label','onset_precision_seconds','method','verification_status','inference_source'),
                                      'speaker_turns': ('speaker_window_start','speaker_support_id','speaker_support_status','identity_decision_id','identity_profile_revision','identity_state'),
                                      'speakers': ('embedding_version','embedding_evidence'),
                                      'speaker_observations': ('embedding_version','embedding_evidence','matched_speaker_id')}.items():
                columns={row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                for column in additions:
                    if column not in columns:
                        kind='INTEGER' if column=='identity_profile_revision' else 'REAL' if column=='speaker_window_start' or (table=='events' and column=='onset_precision_seconds') else 'TEXT'
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {kind}')
            verified_identity.install_lifecycle(db)
            from .fragment_http import init_schema
            init_schema(db)
            if getattr(settings, 'native_owned_core_seams_enabled', False):
                from .native_owned_core_seams import init_schema as init_core_seams
                init_core_seams(db)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE chunks SET status='queued', error='Recovered after process restart' WHERE status='running'")
            lifecycle.recover_cancelled_streams(db)
            from .recording_overlap import recover as recover_recordings
            recover_recordings(db)

    def create_session(self, name, identity_group="default", identity_mode=None, processing_mode="legacy"):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM sessions WHERE deleted=0').fetchone()[0] >= self.settings.max_sessions:
                raise OverflowError('Session capacity reached; delete unused sessions')
            sid = ident()
            if processing_mode not in ('legacy', 'fragment_pcm16_v1'):
                raise ValueError('Unknown session processing mode')
            if processing_mode != 'legacy':
                if identity_mode is not None or identity_group != 'default':
                    raise ValueError('Experimental sessions use isolated, disabled global identity')
                identity_group = 'fragment.' + sid
            verified_identity.create_group(self,db,identity_group,identity_mode)
            db.execute('INSERT INTO sessions(id,name,identity_group,created) VALUES(?,?,?,?)', (sid,name,identity_group,time.time()))
            if processing_mode != 'legacy':
                if not hasattr(self, 'fragment_http'):
                    raise ValueError('Experimental fragment provider is not configured')
                self.fragment_http.create_session(db,sid)
                db.execute('UPDATE sessions SET processing_mode=? WHERE id=?',(processing_mode,sid))
        return self.session(sid)

    def session(self, sid):
        with self.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=? AND deleted=0',(sid,)).fetchone()
            if not row: raise KeyError('Session not found')
            return dict(row)

    def chunk(self, cid):
        with self.db() as db:
            row = db.execute('SELECT * FROM chunks WHERE id=?',(cid,)).fetchone()
            if not row: raise KeyError('Chunk not found')
            result = dict(row)
            result.pop('path')
            stream=db.execute('SELECT stream_id,stream_final,position FROM stream_chunks WHERE chunk_id=?',(cid,)).fetchone()
            if stream:result.update(dict(stream));result['stream_final']=bool(result['stream_final'])
            if result['metadata']: result['metadata'] = json.loads(result['metadata'])
            if result['provenance']: result['provenance'] = json.loads(result['provenance'])
            return result

    def duplicate(self, sid, key):
        with self.db() as db:
            row = db.execute('SELECT id,digest FROM chunks WHERE session_id=? AND idempotency_key=?',(sid,key)).fetchone()
            return dict(row) if row else None

    def _check_queue_limit(self,db,sid=None,stream_id=None,release_chunk_id=None,additional=1):
        # The legacy setting name bounds work units: an explicit continuous
        # stream is one unit; an independent chunk is one unit. Recoverable
        # failed stream receipts remain in its bounded buffer so retry never
        # needs an extra slot and cannot strand dependent chunks.
        live="(c.status IN ('queued','running','paused','waiting_input','staging') OR (sc.stream_id IS NOT NULL AND c.status='failed'))"
        count=db.execute("SELECT COUNT(DISTINCT CASE WHEN sc.stream_id IS NULL THEN 'chunk:'||c.id ELSE 'stream:'||sc.session_id||':'||sc.stream_id END) FROM chunks c LEFT JOIN stream_chunks sc ON sc.chunk_id=c.id WHERE "+live).fetchone()[0]
        retry=release_chunk_id is not None
        if retry:
            source=db.execute('SELECT c.session_id,sc.stream_id FROM chunks c LEFT JOIN stream_chunks sc ON sc.chunk_id=c.id WHERE c.id=?',(release_chunk_id,)).fetchone()
            if source:sid,stream_id=source['session_id'],source['stream_id']
        if sid is not None and stream_id is not None:
            fragments=db.execute(STREAM_PENDING_COUNT_SQL,(sid,stream_id)).fetchone()[0]
            if fragments:
                if not retry and fragments+additional>128:
                    raise OverflowError('Stream has128 pending fragments; wait for processing, retry/cancel its blocked predecessor, or finalize the stream')
                return count
        if count>=self.settings.max_pending_chunks:
            raise OverflowError('Durable work queue is full; finish or cancel an existing work unit')
        return count

    def capacity(self,sid=None,stream_id=None,additional=1):
        with self.db() as db:return self._check_queue_limit(db,sid,stream_id,additional=additional)

    def add_chunk(self, sid, key, digest, path, duration, offset=None, recorded_at=None, stream_id=None, stream_final=False, fragment_source=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._add_chunk(db, sid, key, digest, path, duration, offset,
                recorded_at, stream_id, stream_final, fragment_source)

    def _add_chunk(self, db, sid, key, digest, path, duration, offset=None, recorded_at=None,
                   stream_id=None, stream_final=False, fragment_source=None, *, _status='queued'):
        """Shared transaction body; recording batches publish staged ordinary chunks atomically."""
        if _status not in ('queued', 'staging'):
            raise ValueError('Invalid initial chunk state')
        if _status == 'queued' and db.execute('SELECT 1 FROM recording_batches WHERE session_id=?', (sid,)).fetchone():
            raise ValueError('Recording-reserved sessions accept work only through recording finish')
        session = db.execute('SELECT * FROM sessions WHERE id=? AND deleted=0',(sid,)).fetchone()
        if not session: raise KeyError('Session not found')
        old = db.execute('SELECT id,digest FROM chunks WHERE session_id=? AND idempotency_key=?',(sid,key)).fetchone()
        if old:
            if old['digest'] != digest: raise ValueError('Idempotency key already used for different bytes')
            check_duplicate_stream(db,old['id'],stream_id,stream_final)
            return old['id'], False
        if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0:
            raise ValueError('Chunk duration must be finite and positive')
        if stream_id is not None and not stream_final and duration<.5-1e-6:
            raise ValueError('Nonfinal stream fragments must contain at least0.5seconds; coalesce tiny packets or send an explicit final fragment')
        self._check_queue_limit(db,sid,stream_id)
        start = session['timeline_end'] if offset is None else offset
        if session['processing_mode'] != 'legacy':
            if fragment_source is None or fragment_source.sha256 != digest or duration != fragment_source.frames/16000:
                raise ValueError('Experimental upload requires server-verified PCM source')
            total = db.execute('SELECT source FROM fragment_http_sources WHERE session_id=?',(sid,))
            expected = sum(json.loads(row['source'])['frames'] for row in total)/16000
            if start != expected:
                raise ValueError('Experimental uploads require exact contiguous frame offsets from zero')
            if expected+duration > self.settings.max_audio_seconds:
                raise ValueError('Experimental stream duration limit exceeded')
            start = expected
        if start < 0 or not math.isfinite(start): raise ValueError('Invalid timeline offset')
        # Existing chunk intervals are nonoverlapping. Only the latest
        # predecessor whose start is before our end can intersect us.
        predecessor=db.execute('SELECT id,timeline_start+duration AS endpoint FROM chunks WHERE session_id=? AND timeline_start<? ORDER BY timeline_start DESC LIMIT 1',(sid,start+duration)).fetchone()
        if session['processing_mode']=='legacy' and predecessor and predecessor['endpoint']>start:
            raise ValueError('Chunk overlaps an existing timeline interval')
        cid = ident()
        db.execute('INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,timeline_start,recorded_at,status,created) VALUES(?,?,?,?,?,?,?,?,?,?)', (cid,sid,key,digest,str(path),duration,start,recorded_at,_status,time.time()))
        register_stream_chunk(db,cid,sid,stream_id,stream_final)
        db.execute('UPDATE sessions SET timeline_end=MAX(timeline_end,?) WHERE id=?',(start+duration,sid))
        if session['processing_mode'] != 'legacy':
            self.fragment_http.register_upload(db,sid,cid,stream_id,fragment_source,stream_final)
            db.execute('UPDATE sessions SET timeline_end=? WHERE id=?',((round(start*16000)+fragment_source.frames)/16000,sid))
        return cid, True

    def claim(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(f"""SELECT c.* FROM chunks c JOIN sessions s ON s.id=c.session_id WHERE c.status='queued' AND s.processing_mode='legacy' AND {PUBLISHED_CHUNK_SQL} AND NOT EXISTS (
                SELECT 1 FROM stream_chunks sc JOIN stream_chunks prior
                ON prior.session_id=sc.session_id AND prior.stream_id=sc.stream_id AND prior.position=sc.position-1
                JOIN chunks pc ON pc.id=prior.chunk_id WHERE sc.chunk_id=c.id AND pc.status!='completed')
                ORDER BY c.created,c.id LIMIT 1""").fetchone()
            if not row: return None
            db.execute("UPDATE chunks SET status='running',error=NULL WHERE id=?",(row['id'],))
            return dict(row)

    def set_state(self, cid, action):
        from .fragment_http import is_experimental
        if is_experimental(self,self.chunk(cid)['session_id']):
            return self.fragment_http.control(cid,action)
        states = {'pause': ('queued','running','waiting_input'), 'resume': ('paused',), 'retry': ('failed',), 'cancel': ('queued','running','paused','failed','waiting_input')}
        targets = {'pause':'paused','resume':'queued','retry':'queued','cancel':'cancelled'}
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status FROM chunks WHERE id=?',(cid,)).fetchone()
            if not row: raise KeyError('Chunk not found')
            if row['status'] not in states[action]: raise ValueError(f"Cannot {action} a {row['status']} chunk")
            if targets[action]=='queued':
                if row['status']!='paused':self._check_queue_limit(db,release_chunk_id=cid)
            db.execute('UPDATE chunks SET status=?,error=NULL WHERE id=?',(targets[action],cid))
            if action=='cancel':lifecycle.cancel_descendants(db,cid)

    def fail(self, cid, message):
        with self.db() as db:
            db.execute("UPDATE chunks SET status='failed',error=? WHERE id=? AND status='running'",(str(message)[:2000],cid))

    def _speaker(self, db, sid, embedding, embedding_version=None, evidence=None):
        embedding=normalize(embedding)
        if embedding is None: return None, None, 'unknown'
        calibration=self.settings.speaker_calibration_profile
        if calibration is not None:
            model=calibration['model'];expected=model['embedding_version']+'@sha256:'+model['sha256']
            if embedding_version!=expected or len(embedding)!=model['dimension']:
                raise ValueError('Speaker calibration profile does not match the actual embedding model/version/dimension')
        if evidence is not None:
            if evidence.get('overlap_excluded') is False:return None,None,'unknown_embedding_quality'
            seconds=evidence.get('clean_speech_seconds')
            if not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<self.settings.speaker_min_query_seconds:
                return None,None,'unknown_insufficient_speech'
        group=db.execute('SELECT identity_group FROM sessions WHERE id=?',(sid,)).fetchone()[0]
        rows=db.execute('SELECT * FROM speakers WHERE identity_group=?',(group,)).fetchall()
        by_id={row['id']:row for row in rows}
        # Manual merges preserve source exemplars; compare DISTINCT canonical identities.
        # No automatic averaging or template update, preventing uncertain-match drift.
        scores_by_identity={}
        profile_vectors={}
        example_rows=[]
        for example in db.execute('SELECT e.* FROM speaker_examples e JOIN speakers p ON p.id=e.speaker_id WHERE p.identity_group=?',(group,)):
            row=dict(by_id[example['speaker_id']]);row.update(embedding=example['embedding'],embedding_version=example['embedding_version'])
            example_rows.append(row)
        for row in list(rows)+example_rows:
            if not row['embedding'] or row['embedding_version']!=embedding_version: continue
            ref=json.loads(row['embedding'])
            if len(ref)!=len(embedding): continue
            canonical=row;visited=set()
            while canonical['merged_into'] and canonical['id'] not in visited:
                visited.add(canonical['id'])
                canonical=by_id.get(canonical['merged_into'],canonical)
            profile_vectors.setdefault(canonical['id'],[]).append(ref)
            score=sum(a*b for a,b in zip(ref,embedding))
            old=scores_by_identity.get(canonical['id'])
            if old is None or score>old[0]: scores_by_identity[canonical['id']]=(score,canonical)
        if self.settings.speaker_profile_strategy=='centroid':
            # Only human merges supply additional exemplars. A successful
            # automatic match never feeds its own prediction into the profile.
            for pid,vectors in profile_vectors.items():
                reference=normalize([sum(values)/len(vectors) for values in zip(*vectors)])
                if reference is not None:
                    scores_by_identity[pid]=(sum(a*b for a,b in zip(reference,embedding)),scores_by_identity[pid][1])
        scores=sorted(scores_by_identity.values(),key=lambda v:v[0],reverse=True)
        best=scores[0][0] if scores else None
        if rows and not scores:
            # Equal vector dimensions do not imply model compatibility. Legacy
            # profiles remain unversioned rather than silently relabeling them.
            return None,None,'unknown_model_version'
        margin=best-scores[1][0] if len(scores)>1 else 2
        match_threshold=self.settings.speaker_match_threshold
        if calibration is not None and scores and len(profile_vectors[scores[0][1]['id']])>1:
            match_threshold=self.settings.speaker_centroid_threshold
        if scores and best>=match_threshold and margin>=self.settings.speaker_match_margin:
            row=scores[0][1]
            return row['id'],best,('confirmed' if row['state']=='confirmed' else 'provisional')
        # A strong but ambiguous match is not evidence of a new person. Creating
        # another prototype here amplifies near-duplicate competitors forever.
        if scores and best>=match_threshold:
            return None,best,'unknown_ambiguous'
        if scores and best>=self.settings.speaker_new_threshold:
            return None,best,'unknown_uncertain'
        # Short genuine queries can verify an established profile, but may never
        # create a new person from weaker evidence.
        if evidence is not None and evidence['clean_speech_seconds']<self.settings.speaker_min_clean_seconds:
            return None,best,'unknown_short_enrollment'
        # Includes alias exemplars, so human merges cannot grow memory without bound.
        if len(rows)>=self.settings.max_speakers_per_session or self._template_count(db)>=self.settings.max_speaker_templates_total:return None,best,'unknown_capacity'
        pid=ident()
        db.execute('INSERT INTO speakers(id,session_id,identity_group,embedding,state,created,embedding_version,embedding_evidence) VALUES(?,?,?,?,?,?,?,?)', (pid,sid,group,json.dumps(embedding),'provisional',time.time(),embedding_version,json.dumps(evidence) if evidence is not None else None))
        return pid,best,'provisional'

    def _window_identity(self,db,chunk,offset,metadata,group,local_id,raw_embedding):
        embedding=normalize(raw_embedding)
        version=metadata.get('speaker_embedding_version')
        if version is not None and (not isinstance(version,str) or not version or len(version)>200):
            raise ValueError('Invalid speaker embedding version')
        fingerprint=metadata.get('speaker_embedding_sha256')
        if fingerprint is not None:
            if not isinstance(fingerprint,str) or len(fingerprint)!=64 or any(c not in '0123456789abcdef' for c in fingerprint):
                raise ValueError('Invalid speaker embedding fingerprint')
            version=(version or 'unversioned')+'@sha256:'+fingerprint
        evidence_map=metadata.get('speaker_embedding_evidence',{})
        if not isinstance(evidence_map,dict):raise ValueError('Invalid speaker embedding evidence map')
        evidence=evidence_map.get(str(local_id))
        if evidence is not None:
            if not isinstance(evidence,dict):raise ValueError('Invalid speaker embedding evidence')
            evidence={k:evidence[k] for k in ('clean_speech_seconds','overlap_excluded','source') if k in evidence}
        speaker,score,status=self._speaker(db,chunk['session_id'],embedding,version,evidence)
        observation=None
        if embedding is not None and status not in ('unknown_insufficient_speech','unknown_embedding_quality') and (evidence is None or evidence['clean_speech_seconds']>=self.settings.speaker_min_clean_seconds):
            if speaker is None:
                count=db.execute('SELECT COUNT(*) FROM speaker_observations WHERE identity_group=? AND matched_speaker_id IS NULL',(group,)).fetchone()[0]
                retain=count<self.settings.max_speaker_observations_per_group
            else:
                # Separate bounded recent cache: known speech cannot exhaust the
                # unknown enrollment pool. Old pointers safely lose availability.
                count=db.execute('SELECT COUNT(*) FROM speaker_observations WHERE identity_group=? AND matched_speaker_id IS NOT NULL',(group,)).fetchone()[0]
                if count>=self.settings.max_matched_speaker_observations_per_group:
                    db.execute('DELETE FROM speaker_observations WHERE id IN (SELECT id FROM speaker_observations WHERE identity_group=? AND matched_speaker_id IS NOT NULL ORDER BY created,id LIMIT ?)',
                        (group,count-self.settings.max_matched_speaker_observations_per_group+1))
                retain=True
            if retain and db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]>=self.settings.max_speaker_observations_total:
                db.execute('DELETE FROM speaker_observations WHERE id IN (SELECT id FROM speaker_observations WHERE matched_speaker_id IS NOT NULL ORDER BY created,id LIMIT 1)')
                retain=db.execute('SELECT COUNT(*) FROM speaker_observations').fetchone()[0]<self.settings.max_speaker_observations_total
            if retain:
                observation=ident()
                db.execute('INSERT INTO speaker_observations(id,identity_group,session_id,chunk_id,window_start,local_speaker_id,embedding,created,embedding_version,embedding_evidence,matched_speaker_id,enrolled_speaker_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (observation,group,chunk['session_id'],chunk['id'],offset,str(local_id),json.dumps(embedding),time.time(),version,json.dumps(evidence) if evidence is not None else None,speaker,speaker if status=='provisional' and (score is None or score<self.settings.speaker_new_threshold) else None))
        return speaker,score,status,observation


    def commit_window(self, chunk, offset, end, result):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM chunks WHERE id=?',(chunk['id'],)).fetchone()
            if not current or current['status'] not in ('running','paused'): return False
            if abs(current['next_offset']-offset)>1e-5: return False
            diagnostic_rows=(result.get('overlap_candidates') or {}).get('diagnostics',[])
            if diagnostic_rows:
                from .overlap_diagnostics import encoded, MAX_WINDOW_BYTES
                from .resources import check_resources
                size=len(encoded(diagnostic_rows))
                if size>MAX_WINDOW_BYTES:raise ValueError('Diagnostic window exceeds byte budget')
                # Allow for SQLite pages, WAL, indexes and transaction overhead.
                check_resources(self.settings,additional_disk=4*size+65536)
            local={};local_turn_ids={}
            metadata=dict(result.get('metadata',{}))
            metadata['speaker_identity_policy']={'profile_strategy':self.settings.speaker_profile_strategy,
                'match_threshold':self.settings.speaker_match_threshold,'new_threshold':self.settings.speaker_new_threshold,
                'margin':self.settings.speaker_match_margin,'min_enrollment_seconds':self.settings.speaker_min_clean_seconds,'min_query_seconds':self.settings.speaker_min_query_seconds,
                'automatic_template_updates':False}
            if self.settings.speaker_calibration_profile is not None:
                calibration=self.settings.speaker_calibration_profile
                metadata['speaker_identity_policy'].update(calibration_profile_id=calibration['profile_id'],calibration_profile_sha256=calibration['artifact_sha256'],verified_centroid_threshold=self.settings.speaker_centroid_threshold,model_binding=calibration['model'])
            group=db.execute('SELECT identity_group FROM sessions WHERE id=?',(chunk['session_id'],)).fetchone()[0]
            prepared_support=result.get('_speaker_support',{})
            support_refs={};decision_refs={}
            mode=verified_identity.group_policy(db,group)
            verified=bool(mode and mode['mode']==verified_identity.MODE)
            if verified:
                metadata['speaker_identity_policy']={'identity_mode':verified_identity.MODE,
                    'policy_sha256':mode['policy_sha256'],'experimental':True,'automatic_template_updates':False}
            def support_for(local_id, observation):
                if local_id is None:return None,'no_local_speaker'
                if local_id not in support_refs:
                    value=prepared_support.get(str(local_id)) if isinstance(prepared_support,dict) else None
                    support_refs[local_id]=speaker_support.commit_support(self,db,chunk,offset,group,local_id,observation,value)
                    try:
                        proof=support_vectors.query_proof(value.get('raw_vector'),metadata,value)
                    except (ValueError,TypeError,KeyError,AttributeError):
                        proof=None
                    support_vectors.commit(self,db,support_refs[local_id][0],group,proof)
                return support_refs[local_id]
            raw_turns=result.get('speaker_turns',[])
            if verified:
                # Score the whole window against one transaction/gallery snapshot
                # before writing any immutable identity decision or projection.
                drafts={}
                for row in list(raw_turns)+[segment for segment in result['segments']
                        if not segment.get('overlap') and not segment.get('is_overlap')]:
                    local_id=row.get('speaker_local_id')
                    if local_id is None or local_id in drafts:continue
                    retained=support_for(local_id,None)
                    value=prepared_support.get(str(local_id)) if isinstance(prepared_support,dict) else None
                    drafts[local_id]=verified_identity.prepare_decision(
                        self,db,chunk,offset,group,local_id,metadata,value,retained)
                verified_identity.exclude_overlap_collisions(drafts,raw_turns)
                for local_id,snapshot in drafts.items():
                    value=prepared_support.get(str(local_id)) if isinstance(prepared_support,dict) else None
                    speaker,score,status,decision,revision=verified_identity.persist_decision(
                        db,chunk,offset,group,local_id,value,snapshot)
                    decision_refs[local_id]=(decision,revision)
                    local[local_id]=(speaker,score,status,None)
            def resolve_identity(local_id,raw):
                return self._window_identity(db,chunk,offset,metadata,group,local_id,raw)
            for turn in raw_turns:
                local_id=turn.get('speaker_local_id')
                if local_id is None:
                    speaker,score,status=None,None,'unknown'
                else:
                    if local_id not in local:
                        local[local_id]=resolve_identity(local_id,turn.get('speaker_embedding'))
                    speaker,score,status,_=local[local_id]
                support_id,support_status=support_for(local_id,local.get(local_id,(None,None,None,None))[3])
                turn_cursor=db.execute('INSERT INTO speaker_turns(chunk_id,start,end,speaker_id,speaker_local_id,speaker_match_score,speaker_status,overlap,speaker_window_start,speaker_support_id,speaker_support_status,identity_decision_id,identity_profile_revision,identity_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (chunk['id'],chunk['timeline_start']+offset+turn['start'],chunk['timeline_start']+offset+turn['end'],speaker,local_id,score,status,int(bool(turn.get('overlap')) or any(other.get('speaker_local_id')!=local_id and min(turn['end'],other['end'])>max(turn['start'],other['start']) for other in raw_turns)),offset,support_id,support_status,*decision_refs.get(local_id,(None,None)),'current' if local_id in decision_refs else None))
                if local_id is not None:local_turn_ids.setdefault(local_id,[]).append(turn_cursor.lastrowid)
            for segment in result['segments']:
                local_id=segment.get('speaker_local_id')
                # IDs are only stable within this analyzer window. Never carry a local ID across windows.
                key=local_id if local_id is not None else ident()
                eligible=local_id is not None and not segment.get('overlap') and not segment.get('is_overlap')
                if not eligible:
                    speaker,score,status,observation=None,None,'unknown',None
                    if verified and local_id in local and local[local_id][2] in ('unknown_overlap_identity_conflict','unknown_overlap_identity_capacity'):
                        speaker,score,status,observation=local[local_id]
                else:
                    if key not in local:
                        local[key]=resolve_identity(local_id,segment.get('speaker_embedding'))
                    speaker,score,status,observation=local[key]
                support_id,support_status=support_for(local_id,observation)
                db.execute('INSERT INTO segments(chunk_id,start,end,text,speaker_id,speaker_local_id,confidence,speaker_match_score,speaker_status,asr_backend,timestamp_kind,raw_text,candidate_observation_id,speaker_window_start,speaker_support_id,speaker_support_status,identity_decision_id,identity_profile_revision,identity_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (chunk['id'],chunk['timeline_start']+offset+segment['start'],chunk['timeline_start']+offset+segment['end'],segment.get('text',''),speaker,local_id,segment.get('confidence'),score,status,segment.get('asr_backend') or metadata.get('asr_backend'),segment.get('timestamp_kind') or metadata.get('timestamp_semantics'),segment.get('raw_text'),observation,offset,support_id,support_status,*decision_refs.get(local_id,(None,None)),'current' if local_id in decision_refs else None))
            for event in result['events']:
                for key in ('time_precision','source_label','method'):
                    value=event.get(key)
                    if value is not None and (not isinstance(value,str) or len(value)>512):raise ValueError('Invalid event '+key)
                precision=event.get('onset_precision_seconds')
                if precision is not None and (not isinstance(precision,(float,int)) or not math.isfinite(precision) or precision<0):raise ValueError('Invalid event onset precision')
                db.execute('INSERT INTO events(chunk_id,start,end,label,score,time_precision,source_label,onset_precision_seconds,method,verification_status,inference_source) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (chunk['id'],chunk['timeline_start']+offset+event['start'],chunk['timeline_start']+offset+event['end'],event['label'],event['score'],event.get('time_precision'),event.get('source_label'),precision,event.get('method'),'unverified_inference','rule' if event.get('method') in ('peak-amplitude fast path','signal_threshold_silence_rule') or event.get('source_label')=='digital_or_near_digital_silence' else 'model'))
            event_candidate_store.commit_candidates(db,chunk,offset,result.get('event_candidates',[]),metadata)
            overlap_store.commit_candidates(db,chunk,offset,result.get('overlap_candidates'),metadata,
                resolved_local_identities={key:{'speaker_id':None if verified else value[0],'speaker_status':'identity_decision_required' if verified else value[2],'speaker_turn_ids':local_turn_ids.get(key,[])} for key,value in local.items()})
            status='completed' if end>=chunk['duration']-1e-5 else current['status']
            provenance=update_provenance(current['provenance'],metadata,offset)
            if getattr(self.settings, 'native_owned_core_seams_enabled', False):
                from .native_owned_core_seams import transaction as commit_core_seam
                optional_reason = commit_core_seam(db, chunk, offset, end, result)
                result['_native_core_seam_status'] = optional_reason
                if optional_reason is not None:
                    metadata['native_core_seam'] = {'status': optional_reason}
            db.execute('UPDATE chunks SET next_offset=?,status=?,metadata=?,provenance=? WHERE id=?',(end,status,json.dumps(metadata),json.dumps(provenance),chunk['id']))
            if status=='completed':wake_stream_successor(db,chunk['id'])
            return True

    def results(self, sid, after=0, limit=500):
        self.session(sid)
        with self.db() as db:
            db.execute('BEGIN')
            segs=[dict(r) for r in db.execute('SELECT s.*,CASE WHEN o.id IS NOT NULL AND o.enrolled_speaker_id IS NULL AND s.speaker_id IS NULL THEN 1 ELSE 0 END AS enrollment_available,CASE WHEN o.id IS NOT NULL AND o.enrolled_speaker_id IS NULL THEN 1 ELSE 0 END AS profile_example_available FROM segments s JOIN chunks c ON c.id=s.chunk_id LEFT JOIN speaker_observations o ON o.id=s.candidate_observation_id WHERE c.session_id=? AND s.id>? ORDER BY s.id LIMIT ?', (sid,after,limit))]
            session_group=db.execute('SELECT identity_group FROM sessions WHERE id=?',(sid,)).fetchone()[0]
            policy=verified_identity.group_policy(db,session_group)
            canonical=identity_canonical.snapshot(db,session_group,policy)
            for s in segs:
                verified_identity.project_result(s,policy,canonical)
                s.pop('candidate_observation_id',None)
                s.pop('speaker_support_id',None);s.pop('speaker_support_status',None)
                if not verified_identity.is_verified(db,sid):
                    for field in ('identity_decision_id','identity_profile_revision','identity_state'):s.pop(field,None)
                else:s['enrollment_available']=s['profile_example_available']=False
                s['enrollment_available']=bool(s['enrollment_available'])
                s['profile_example_available']=bool(s['profile_example_available'])
                if policy and policy['mode']==verified_identity.MODE:
                    continue
                speaker=s['speaker_id']; visited=set()
                while speaker and speaker not in visited:
                    visited.add(speaker)
                    row=db.execute('SELECT merged_into FROM speakers WHERE id=?',(speaker,)).fetchone()
                    if not row or not row[0]: break
                    speaker=row[0]
                s['speaker_id']=speaker
            return {'segments':segs, 'next_cursor':segs[-1]['id'] if segs else after}

    def speaker_turns(self,sid,after=0,limit=500):
        self.session(sid)
        with self.db() as db:
            db.execute('BEGIN')
            rows=[dict(r) for r in db.execute('SELECT t.* FROM speaker_turns t JOIN chunks c ON c.id=t.chunk_id WHERE c.session_id=? AND t.id>? ORDER BY t.id LIMIT ?',(sid,after,limit))]
            session_group=db.execute('SELECT identity_group FROM sessions WHERE id=?',(sid,)).fetchone()[0]
            policy=verified_identity.group_policy(db,session_group)
            canonical=identity_canonical.snapshot(db,session_group,policy)
            for row in rows:
                verified_identity.project_result(row,policy,canonical)
                row.pop('speaker_support_id',None);row.pop('speaker_support_status',None)
                if not verified_identity.is_verified(db,sid):
                    for field in ('identity_decision_id','identity_profile_revision','identity_state'):row.pop(field,None)
                row['overlap']=bool(row['overlap'])
                if policy and policy['mode']==verified_identity.MODE:
                    continue
                speaker=row['speaker_id'];visited=set()
                while speaker and speaker not in visited:
                    visited.add(speaker)
                    target=db.execute('SELECT merged_into FROM speakers WHERE id=?',(speaker,)).fetchone()
                    if not target or not target[0]:break
                    speaker=target[0]
                row['speaker_id']=speaker
            return {'speaker_turns':rows,'next_cursor':rows[-1]['id'] if rows else after}

    def overlap_diagnostics(self,sid,after=0,limit=10):
        """Explicit private/local export; not exposed by ordinary result routes."""
        return overlap_store.private_diagnostics(self,sid,after,limit)

    def overlap_diagnostic_status(self,sid,after=0,limit=100):
        return overlap_store.private_diagnostic_status(self,sid,after,limit)

    def overlap_candidates(self,sid,after=0,limit=100):
        return overlap_store.candidates(self,sid,after,limit)

    def event_candidates(self,sid,after=0,limit=500):
        return event_candidate_store.candidates(self,sid,after,limit)

    def events(self,sid,after=0,limit=500):
        self.session(sid)
        with self.db() as db:
            rows=[dict(r) for r in db.execute('SELECT e.* FROM events e JOIN chunks c ON c.id=e.chunk_id WHERE c.session_id=? AND e.id>? ORDER BY e.id LIMIT ?',(sid,after,limit))]
            for row in rows:
                row['verification_status']=row['verification_status'] or 'unverified_legacy_inference'
                row['inference_source']=row['inference_source'] or 'not_recorded'
            return {'events':rows,'next_cursor':rows[-1]['id'] if rows else after}

    def speakers(self,sid):
        self.session(sid)
        with self.db() as db:
            db.execute('BEGIN')
            group=db.execute('SELECT identity_group FROM sessions WHERE id=?',(sid,)).fetchone()[0]
            policy=verified_identity.group_policy(db,group)
            canonical=identity_canonical.snapshot(db,group,policy)
            return [identity_canonical.project_person(dict(r),policy,canonical) for r in db.execute(
                'SELECT id,name,state,merged_into,created FROM speakers WHERE identity_group=?',(group,))]

    def _template_count(self,db):
        return db.execute('SELECT (SELECT COUNT(*) FROM speakers)+(SELECT COUNT(*) FROM speaker_examples)').fetchone()[0]

    def _profile_members(self,db,pid):
        rows=db.execute('SELECT id,merged_into FROM speakers WHERE identity_group=(SELECT identity_group FROM speakers WHERE id=?)',(pid,)).fetchall()
        by_id={r['id']:r for r in rows};members=[]
        for row in rows:
            current=row;seen=set()
            while current['merged_into'] and current['id'] not in seen:
                seen.add(current['id']);current=by_id.get(current['merged_into'],current)
            if current['id']==pid:members.append(row['id'])
        return members

    def _profile_size(self,db,pid):
        members=self._profile_members(db,pid)
        return len(members)+sum(db.execute('SELECT COUNT(*) FROM speaker_examples WHERE speaker_id=?',(member,)).fetchone()[0] for member in members)

    def add_speaker_example(self,sid,pid,source_segment_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if verified_identity.is_verified(db,sid):raise ValueError('review_required: use the identity-reviews route')
            target=db.execute('SELECT p.* FROM speakers p JOIN sessions s ON s.identity_group=p.identity_group WHERE p.id=? AND s.id=? AND s.deleted=0 AND p.merged_into IS NULL',(pid,sid)).fetchone()
            if not target:raise KeyError('Unmerged target speaker not found in session identity group')
            source=db.execute('SELECT s.* FROM segments s JOIN chunks c ON c.id=s.chunk_id WHERE s.id=? AND c.session_id=?',(source_segment_id,sid)).fetchone()
            if not source:raise KeyError('Source segment not found in session')
            observation=db.execute('SELECT * FROM speaker_observations WHERE id=? AND identity_group=? AND session_id=?',(source['candidate_observation_id'],target['identity_group'],sid)).fetchone()
            if not observation:raise ValueError('No retained reliable embedding is available for this segment')
            if observation['enrolled_speaker_id']:raise ValueError('This observation was already used for enrollment')
            if self._profile_size(db,pid)>=self.settings.max_speaker_examples_per_person or self._template_count(db)>=self.settings.max_speaker_templates_total:raise OverflowError('Speaker example capacity reached')
            embedding=normalize(json.loads(observation['embedding']))
            if embedding is None:raise ValueError('Invalid speaker example embedding')
            eid=ident()
            db.execute('INSERT INTO speaker_examples(id,speaker_id,observation_id,source_session_id,source_segment_id,embedding,embedding_version,embedding_evidence,created) VALUES(?,?,?,?,?,?,?,?,?)',
                (eid,pid,observation['id'],sid,source_segment_id,json.dumps(embedding),observation['embedding_version'],observation['embedding_evidence'],time.time()))
            db.execute('UPDATE speaker_observations SET enrolled_speaker_id=? WHERE id=?',(pid,observation['id']))
            db.execute("UPDATE segments SET speaker_id=?,speaker_status='human_example',speaker_match_score=NULL WHERE id=?",(pid,source_segment_id))
            db.execute('INSERT INTO corrections(session_id,action,detail,created) VALUES(?,?,?,?)',(sid,'speaker_example',json.dumps({'speaker_id':pid,'source_segment_id':source_segment_id,'example_id':eid}),time.time()))
            return {'id':eid,'speaker_id':pid,'source_segment_id':source_segment_id,'template_count':self._profile_size(db,pid)}

    def correct_speaker(self,sid,pid,name=None,merge_into=None,undo_merge=False):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            verified=verified_identity.is_verified(db,sid)
            if verified and (merge_into or undo_merge):raise ValueError('verified_merge_unsupported_on_legacy_route: use versioned identity-relations and exact event retraction')
            source=db.execute('SELECT * FROM speakers WHERE id=? AND identity_group=(SELECT identity_group FROM sessions WHERE id=?)',(pid,sid)).fetchone()
            if not source: raise KeyError('Speaker not found')
            if undo_merge:
                db.execute('UPDATE speakers SET merged_into=NULL WHERE id=?',(pid,))
            elif merge_into:
                target=db.execute('SELECT * FROM speakers WHERE id=? AND identity_group=(SELECT identity_group FROM sessions WHERE id=?)',(merge_into,sid)).fetchone()
                if not target: raise KeyError('Target speaker not found in session')
                if merge_into==pid or target['merged_into'] or source['merged_into']: raise ValueError('Merge requires distinct unmerged speakers')
                if self._profile_size(db,pid)+self._profile_size(db,merge_into)>self.settings.max_speaker_examples_per_person:
                    raise OverflowError('Merged speaker would exceed example capacity')
                db.execute('UPDATE speakers SET merged_into=? WHERE id=?',(merge_into,pid))
            else:
                if verified:db.execute('UPDATE speakers SET name=? WHERE id=?',(name,pid))
                else:db.execute("UPDATE speakers SET name=?,state='confirmed' WHERE id=?",(name,pid))
            db.execute('INSERT INTO corrections(session_id,action,detail,created) VALUES(?,?,?,?)',(sid,'speaker',json.dumps({'speaker_id':pid,'name':name,'merge_into':merge_into,'undo_merge':undo_merge}),time.time()))

    def enroll_speaker(self,sid,name,source_segment_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if verified_identity.is_verified(db,sid):raise ValueError('review_required: use the identity-reviews route')
            session=db.execute('SELECT identity_group FROM sessions WHERE id=? AND deleted=0',(sid,)).fetchone()
            if not session: raise KeyError('Session not found')
            source=db.execute('SELECT s.* FROM segments s JOIN chunks c ON c.id=s.chunk_id WHERE s.id=? AND c.session_id=?',(source_segment_id,sid)).fetchone()
            if not source: raise KeyError('Source segment not found in session')
            if source['speaker_id'] is not None: raise ValueError('Source already has an identity; use rename or merge')
            observation=db.execute('SELECT * FROM speaker_observations WHERE id=? AND identity_group=? AND session_id=?',
                (source['candidate_observation_id'],session['identity_group'],sid)).fetchone()
            if not observation: raise ValueError('No reliable enrollment embedding is available for this segment')
            if observation['enrolled_speaker_id']: raise ValueError('This observation was already enrolled; assign the existing identity')
            embedding=normalize(json.loads(observation['embedding']))
            if embedding is None: raise ValueError('Enrollment embedding is invalid')
            count=db.execute('SELECT COUNT(*) FROM speakers WHERE identity_group=?',(session['identity_group'],)).fetchone()[0]
            if count>=self.settings.max_speakers_per_session or self._template_count(db)>=self.settings.max_speaker_templates_total:raise OverflowError('Speaker template capacity reached')
            pid=ident()
            db.execute('INSERT INTO speakers(id,session_id,identity_group,name,embedding,state,created,embedding_version,embedding_evidence) VALUES(?,?,?,?,?,?,?,?,?)',
                (pid,sid,session['identity_group'],name,json.dumps(embedding),'confirmed',time.time(),observation['embedding_version'],observation['embedding_evidence']))
            db.execute('UPDATE speaker_observations SET enrolled_speaker_id=? WHERE id=?',(pid,observation['id']))
            db.execute("UPDATE segments SET speaker_id=?,speaker_status='human_enrolled',speaker_match_score=NULL WHERE id=?",(pid,source_segment_id))
            db.execute('INSERT INTO corrections(session_id,action,detail,created) VALUES(?,?,?,?)',
                (sid,'enroll',json.dumps({'source_segment_id':source_segment_id,'speaker_id':pid,'name':name}),time.time()))
            return {'id':pid,'name':name,'state':'confirmed','source_segment_id':source_segment_id}

    def assign_segment(self,sid,segment_id,speaker_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if speaker_id and not db.execute('SELECT id FROM speakers WHERE id=? AND identity_group=(SELECT identity_group FROM sessions WHERE id=?) AND merged_into IS NULL',(speaker_id,sid)).fetchone(): raise KeyError('Speaker not found')
            row=db.execute('SELECT s.id FROM segments s JOIN chunks c ON c.id=s.chunk_id WHERE s.id=? AND c.session_id=?',(segment_id,sid)).fetchone()
            if not row: raise KeyError('Segment not found')
            db.execute("UPDATE segments SET speaker_id=?,speaker_status='human_corrected',speaker_match_score=NULL WHERE id=?",(speaker_id,segment_id))
            db.execute('INSERT INTO corrections(session_id,action,detail,created) VALUES(?,?,?,?)',(sid,'segment',json.dumps({'segment_id':segment_id,'speaker_id':speaker_id}),time.time()))

    def delete_session(self,sid,delete_file=None):
        return lifecycle.delete_session(self,sid,delete_file)
