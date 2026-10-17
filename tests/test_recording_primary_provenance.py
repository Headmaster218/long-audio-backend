"""Fresh synthetic numerical witnesses and real SQLite; no historical inputs."""
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from audio_backend import recording_primary_provenance as pp
from audio_backend import lexical_evidence as lexical
from audio_backend import native_owned_core_seams as core
from audio_backend import transcript_contract as contract
from audio_backend.native_owned_cells import RATE, SourceSpan, canonical, sha
from audio_backend.native_owned_cell_store import Budget
from audio_backend.settings import Settings
from audio_backend.storage import Store
from audio_backend.worker import validate_result, own_core
from test_native_owned_cells import observed_window, EPOCH


def settings(root):
    value = Settings(data_dir=root, start_worker=False, disk_reserve_bytes=0,
        diarization_backend='nemotron', native_diarization_model='synthetic',
        native_diarization_library='synthetic', native_owned_cells_enabled=True,
        native_owned_core_seams_enabled=True, asr_backend='sensevoice')
    value.recording_speaker_links_enabled = True
    value.moss_automatic_enabled = True
    value.native_owned_bounded_core_support_enabled = True
    return value


def setup(tmp_path, *, seconds=4):
    store = Store(tmp_path / 'primary.sqlite', settings(tmp_path))
    sid = store.create_session('numeric synthetic')['id']
    cid, _ = store.add_chunk(sid, 'synthetic', 'c' * 64, tmp_path / 'unused.audio', seconds)
    with store.db() as db:
        db.execute("UPDATE chunks SET status='running' WHERE id=?", (cid,))
        db.execute("INSERT INTO recording_batches(id,session_id,capture_id,created) VALUES('recording',?,'capture',0)", (sid,))
        db.execute("INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,capture_start_frame,native_receipt_status) VALUES(?,'recording',0,?,0,0,'synthetic')", (cid, seconds * RATE))
    return store, store.chunk(cid)


def result_for(chunk, offset=0, end=2, *, unknown=True, lexical_retained=False, mode='mixed'):
    duration = chunk['duration']; witness, native, pcm, _ = observed_window(round(duration * RATE))
    label = EPOCH + ':speaker_1'
    turns = [{'start': 0., 'end': duration, 'speaker_local_id': label},
        {'start': .1, 'end': duration, 'speaker_local_id': label}]
    if unknown:turns.append({'start': offset + .8, 'end': duration, 'speaker_local_id': EPOCH + ':speaker_2'})
    definitions = [(offset + .25, offset + .5, 'bound', label)]
    if unknown:definitions.append((offset + 1, offset + 1.2, 'unknown', None))
    if mode == 'empty':definitions = []
    if mode == 'unknown_only':definitions = definitions[1:]
    if mode == 'unbound':
        turns = [{'start': offset + .8, 'end': duration, 'speaker_local_id': label}]
        definitions = [(offset + .25, offset + .5, 'unbound', None)]
    witness.observe({'turns': turns}); native['turns'] = deepcopy(turns); native['end'] = end
    segments = []
    for a, b, text, local in definitions:
        segments.append({'start': a, 'end': b, 'text': text, 'raw_text': text,
            'speaker_local_id': local, 'asr_backend': 'sensevoice', 'timestamp_kind': lexical.PRIMARY_TIMESTAMP,
            'confidence': 0, '_units': [{'start': a, 'end': b, 'ownership_anchor': a,
                'text': text, 'raw_text': text, 'anchor_kind': 'ctc_emission'}]})
    result = {'segments': segments, 'events': [], 'speaker_turns': deepcopy(turns),
        'metadata': {'diarization_turns': deepcopy(turns), 'asr_backend': 'sensevoice',
            'timestamp_semantics': lexical.PRIMARY_TIMESTAMP}}
    original = deepcopy(result); captured = pp.observe(result, native)
    assert result == original and captured[1] == 'pending'
    validate_result(result, duration); pp.validate_observation(result, captured)
    result['metadata']['native_diarization'] = native['state']
    result['metadata']['core_cut'] = {'nominal': end, 'actual': end, 'reason': 'fixed_or_final'}
    own_core(result, offset, end, retain_lexical=lexical_retained)
    if lexical_retained:
        result['_lexical_sources'] = [{**{k: chunk[k] for k in lexical.SOURCE_FIELDS},
            'seek_seconds': 0., 'requested_seconds': duration, 'analyzer_frames': [0, round(duration * RATE)]}]
    part = SourceSpan(chunk['session_id'], chunk['id'], 'b' * 64, chunk['digest'], round(duration * RATE),
        round(offset * RATE), round(end * RATE), round(offset * RATE), round(end * RATE))
    capture = SimpleNamespace(witness=witness, core_seams=SimpleNamespace(run='1' * 32),
        scope=('upload', chunk['session_id'], chunk['id']), scope_start=0., origin_frame=0,
        observed_reset_generation=1, analyzer_start=0, analyzer=pcm)
    evidence = {'chunk_id': chunk['id'], 'start': round(offset * RATE), 'end': round(end * RATE),
        'native': native['state'], 'core_cut': result['metadata']['core_cut']}
    _, observed = witness.prepared(native, 0., 0)
    commit = {'scope': {'session_id':chunk['session_id'],'kind':'upload','key':chunk['id'],
        'origin':float(0).hex(),'run':'1'*32,'reset':1,'epoch':observed['epoch_id'],'index':observed['epoch_index']},
        'part':asdict(part),'native_sha256':sha(native['state']),'cut_sha256':sha(evidence['core_cut']),
        'evidence_sha256':sha(evidence),'runtime_sha256':sha(observed['provenance'])}
    pp.freeze(capture, chunk, native, offset, end, result, (part,), evidence,
        pcm[round(offset * RATE) * 2:round(end * RATE) * 2])
    if mode == 'mixed':
        assert result['_primary_provenance']['packet']['commit'] == commit
    else:
        assert '_primary_provenance' not in result
        assert result['_primary_status'] == ('empty' if mode == 'empty' else 'unknown_only')
    result['_native_core_seam'] = {'commit':commit,'proof':None,'reason':'core_first_or_unavailable_left'}
    result['_native_bounded_support'] = {'budget':Budget.for_bounded(.75),'batch':None}
    return result


def get_packet(store, cid, start=0, end=32000):
    with store.db() as db:
        db.execute('BEGIN');return pp.read_exact(db,cid=cid,start=start,end=end)


def counts(store):
    with store.db() as db:return [tuple(r) for r in db.execute(f'SELECT * FROM {pp.USAGE} ORDER BY scope')]


def test_actual_sql_refs_restart_unknown_and_no_lexical(tmp_path):
    store, chunk = setup(tmp_path);assert store.commit_window(chunk,0,2,result_for(chunk))
    saved = get_packet(store,chunk['id']);assert saved is not None
    packet = saved['packet'];assert [u[5:] for u in packet['units']] == [[0,[0,1]],[None,[0,1,2]]]
    with store.db() as db:
        rows = db.execute('SELECT '+','.join(contract.PRIMARY_FIELDS)+' FROM segments ORDER BY id').fetchall()
        assert packet['refs'] == [[contract.primary_evidence(chunk['session_id'],r)['source_ref'],None] for r in rows]
        assert type(rows[0]['confidence']) is float and rows[1]['speaker_local_id'] is None
        db.execute('BEGIN');assert pp.current(db,saved,source_current=lambda part:True)
    assert b'bound' not in pp.encoded(packet) and b'unknown' not in pp.encoded(packet)
    restarted = Store(store.path,settings(tmp_path));assert get_packet(restarted,chunk['id']) == saved
    assert counts(restarted) == counts(store)


def test_optional_lexical_ref_and_retention_quota(tmp_path):
    store, chunk = setup(tmp_path);store.settings.lexical_evidence_enabled=True
    assert store.commit_window(chunk,0,2,result_for(chunk,lexical_retained=True))
    saved=get_packet(store,chunk['id']);assert all(ref[1] is not None for ref in saved['packet']['refs'])
    with store.db() as db:db.execute('UPDATE segments SET lexical_evidence=NULL')
    with store.db() as db:
        db.execute('BEGIN');assert pp.current(db,saved,source_current=lambda part:True)
    with store.db() as db:db.execute('UPDATE sessions SET lexical_evidence_bytes=?',(lexical.MAX_SESSION_BYTES,))
    assert store.commit_window(chunk,2,4,result_for(chunk,2,4,lexical_retained=True))
    saved=get_packet(store,chunk['id'],32000,64000);assert saved is not None
    assert all(ref[1] is None for ref in saved['packet']['refs'])


@pytest.mark.parametrize('mode',['empty','unknown_only','unbound'])
def test_empty_or_wholly_unbound_preserves_primary_without_packet(tmp_path,mode):
    store,chunk=setup(tmp_path);result=result_for(chunk,mode=mode)
    assert store.commit_window(chunk,0,2,result)
    assert get_packet(store,chunk['id']) is None and store.chunk(chunk['id'])['next_offset']==2
    assert counts(store)==[('',0,0)]


@pytest.mark.parametrize('mutation',['row','core','source','run'])
def test_transaction_refusal_keeps_primary(tmp_path,mutation):
    store,chunk=setup(tmp_path);result=result_for(chunk)
    if mutation=='row':result['segments'][0]['text']='changed'
    if mutation=='core':result.pop('_native_core_seam')
    if mutation=='source':
        with store.db() as db:db.execute('UPDATE chunks SET digest=? WHERE id=?',('d'*64,chunk['id']))
    if mutation=='run':result['_primary_provenance']['packet']['commit']['scope']['run']='2'*32
    assert store.commit_window(chunk,0,2,result)
    assert get_packet(store,chunk['id']) is None and store.chunk(chunk['id'])['next_offset']==2


def test_recording_quota_refusal_survives_later_success(tmp_path):
    store,chunk=setup(tmp_path);store.settings.recording_primary_provenance_recording_bytes=1
    assert store.commit_window(chunk,0,2,result_for(chunk))
    assert counts(store)==[('',0,0)]
    store.settings.recording_primary_provenance_recording_bytes=128*1024**2
    assert store.commit_window(chunk,2,4,result_for(chunk,2,4))
    summary=store.chunk(chunk['id'])['provenance']['recording_primary_provenance']
    assert summary['counts']=={'recording_quota':1,'retained':1} and summary['windows']==2


def test_idempotency_config_reduction_and_budget_preflight(tmp_path):
    store,chunk=setup(tmp_path);result=result_for(chunk);assert store.commit_window(chunk,0,2,result)
    before=counts(store);store.settings.recording_primary_provenance_recording_bytes=1;store.settings.recording_primary_provenance_global_bytes=1
    result['_native_bounded_support']={'budget':Budget.for_bounded(.75),'reserved':True}
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE');ids=[r[0] for r in db.execute('SELECT id FROM segments ORDER BY id')]
        assert pp.persist(store,db,chunk,0,2,result,ids)=='existing'
    assert counts(store)==before
    result['_native_bounded_support']['budget'].sql_bytes=247*1024
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE');statements=[];db.set_trace_callback(statements.append)
        assert pp.persist(store,db,chunk,0,2,result,ids)=='capacity' and not statements


@pytest.mark.parametrize('fault',['incremental','baseline','identity','io'])
def test_disk_refusal_boundary(tmp_path,monkeypatch,fault):
    from audio_backend.moss_result_store import FREE_FLOOR,WRITE_ALLOWANCE,SCRATCH_ALLOWANCE,CELL_ALLOWANCE
    store,chunk=setup(tmp_path);result=result_for(chunk)
    def usage(path):
        wal=Path(str(store.path)+'-wal');base=FREE_FLOOR+WRITE_ALLOWANCE+SCRATCH_ALLOWANCE+CELL_ALLOWANCE+(wal.stat().st_size if wal.exists() else 0)
        return SimpleNamespace(free=base+1 if fault=='incremental' else 0)
    if fault in ('incremental','baseline'):monkeypatch.setattr(pp.shutil,'disk_usage',usage)
    elif fault=='identity':store._primary_provenance_identity=(-1,-1)
    else:monkeypatch.setattr(pp,'headroom',lambda *a:(_ for _ in ()).throw(OSError('synthetic IO')))
    if fault=='incremental':
        assert store.commit_window(chunk,0,2,result)
        assert store.chunk(chunk['id'])['provenance']['recording_primary_provenance']['counts']['disk_headroom']==1
    else:
        with pytest.raises(OSError):store.commit_window(chunk,0,2,result)
        assert store.chunk(chunk['id'])['next_offset']==0
    assert get_packet(store,chunk['id']) is None and counts(store)==[('',0,0)]


@pytest.mark.parametrize('fault',['full','io','after_insert','uncertain_commit'])
def test_sql_and_commit_errors_are_hard(tmp_path,monkeypatch,fault):
    store,chunk=setup(tmp_path);result=result_for(chunk);connect=sqlite3.connect
    class Fault(sqlite3.Connection):
        def execute(self,sql,params=()):
            if fault in ('full','io') and sql.startswith('INSERT INTO '+pp.TABLE):
                error=sqlite3.OperationalError('synthetic error');error.sqlite_errorcode=sqlite3.SQLITE_FULL if fault=='full' else sqlite3.SQLITE_IOERR;raise error
            if fault=='after_insert' and sql.startswith('UPDATE chunks SET next_offset='):raise sqlite3.OperationalError('synthetic after insert')
            return super().execute(sql,params)
        def commit(self):
            super().commit()
            if fault=='uncertain_commit':raise sqlite3.OperationalError('synthetic lost acknowledgement')
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,'connect',lambda *a,**kw:connect(*a,**kw,factory=Fault))
        with pytest.raises(sqlite3.OperationalError):store.commit_window(chunk,0,2,result)
    saved=get_packet(store,chunk['id'])
    if fault=='uncertain_commit':
        assert store.chunk(chunk['id'])['next_offset']==2 and saved is not None
        assert not store.commit_window(chunk,0,2,result)
    else:
        assert store.chunk(chunk['id'])['next_offset']==0 and saved is None and counts(store)==[('',0,0)]


def test_current_authority_loss_keeps_audit(tmp_path):
    store,chunk=setup(tmp_path);assert store.commit_window(chunk,0,2,result_for(chunk));saved=get_packet(store,chunk['id'])
    with store.db() as db:
        db.execute('BEGIN');assert not pp.current(db,saved,source_current=lambda part:False)
    with store.db() as db:db.execute("UPDATE segments SET text='mutated' WHERE id=1")
    with store.db() as db:
        db.execute('BEGIN');assert not pp.current(db,saved,source_current=lambda part:True)
    with store.db() as db:db.execute('DELETE FROM native_core_commits')
    assert get_packet(store,chunk['id'])==saved
    with store.db() as db:
        db.execute('BEGIN');assert not pp.current(db,saved,source_current=lambda part:True)


@pytest.mark.parametrize('mutation',['unit_label','turns','refs','header','query','membership','synthetic_body'])
def test_current_strict_shape_and_exact_saved_binding(tmp_path,mutation):
    store,chunk=setup(tmp_path);assert store.commit_window(chunk,0,2,result_for(chunk));saved=get_packet(store,chunk['id']);altered=deepcopy(saved)
    if mutation=='membership':
        with store.db() as db:db.execute('DELETE FROM recording_work WHERE chunk_id=?',(chunk['id'],))
        with store.db() as db:
            db.execute('BEGIN');assert not pp.current(db,saved,source_current=lambda part:True)
        assert get_packet(store,chunk['id'])==saved;return
    if mutation=='synthetic_body':
        altered['packet']['owned_pcm16le_sha256']='a'*64;altered['header']['digest']=pp.digest(altered['packet'])
        with store.db() as db:
            db.execute('BEGIN');assert not pp.current(db,altered,source_current=lambda part:True)
        return
    if mutation=='unit_label':altered['packet']['units'][0][5]=1
    if mutation=='turns':altered['packet']['units'][0][6]=[0]
    if mutation=='refs':altered['packet']['refs'][0][0]='p.0001.'+'a'*64
    if mutation=='header':altered['header']['cid']='different'
    if mutation=='query':altered['packet']['query'][1]=0
    with store.db() as db:
        db.execute('BEGIN')
        with pytest.raises((ValueError,RuntimeError,pp.Refused)):pp.current(db,altered,source_current=lambda part:True)


def test_immutable_explicit_erasure_and_accounting(tmp_path):
    store,chunk=setup(tmp_path);assert store.commit_window(chunk,0,2,result_for(chunk))
    for sql in (f'UPDATE {pp.TABLE} SET charge=charge',f'DELETE FROM {pp.TABLE}','DELETE FROM chunks'):
        with pytest.raises(sqlite3.IntegrityError):
            with store.db() as db:db.execute(sql)
    assert get_packet(store,chunk['id']) is not None
    with store.db() as db:db.execute("UPDATE chunks SET status='paused' WHERE id=?",(chunk['id'],))
    from audio_backend.lifecycle import delete_session
    delete_session(store,chunk['session_id'],delete_file=lambda path:None)
    assert counts(store)==[('',0,0)]
    delete_session(store,chunk['session_id'],delete_file=lambda path:None)
    assert counts(store)==[('',0,0)]


def test_old_dev45_whole_package_restart_and_erasure(tmp_path):
    import os
    baseline=os.environ.get('LONG_AUDIO_DEV45_BASELINE')
    if baseline is None:pytest.skip('Set LONG_AUDIO_DEV45_BASELINE to the exact dev45 source package')
    assert baseline and Path(baseline).is_absolute(),'Supplied dev45 baseline must be an absolute source path'
    installed=Path(baseline)/'audio_backend'
    assert (installed/'__init__.py').is_file(),'Supplied dev45 baseline is missing'
    store,chunk=setup(tmp_path);assert store.commit_window(chunk,0,2,result_for(chunk));before=counts(store);saved=get_packet(store,chunk['id'])
    name='primary_provenance_legacy_dev45'
    spec=importlib.util.spec_from_file_location(name,installed/'__init__.py',submodule_search_locations=[str(installed)])
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    assert module.__version__=='0.2.0rc3.dev45'
    old_storage=importlib.import_module(name+'.storage');old_settings=importlib.import_module(name+'.settings');old_lifecycle=importlib.import_module(name+'.lifecycle')
    for loaded in (old_storage,old_settings,old_lifecycle):assert Path(loaded.__file__).resolve().parent==installed.resolve()
    old=old_storage.Store(store.path,old_settings.Settings(data_dir=tmp_path,start_worker=False,disk_reserve_bytes=0))
    assert get_packet(store,chunk['id'])==saved and counts(store)==before
    with old.db() as db:db.execute("UPDATE chunks SET status='paused' WHERE id=?",(chunk['id'],))
    old_lifecycle.delete_session(old,chunk['session_id'],delete_file=lambda path:None)
    assert get_packet(store,chunk['id']) is None and counts(store)==[('',0,0)]
    old_lifecycle.delete_session(old,chunk['session_id'],delete_file=lambda path:None)
    assert counts(store)==[('',0,0)]


def test_unknown_schema_fails_safe(tmp_path):
    store,_=setup(tmp_path)
    with store.db() as db:db.execute('DROP TRIGGER recording_primary_insert_v1')
    with pytest.raises(ValueError,match='Unknown primary provenance schema'):Store(store.path,settings(tmp_path))
    with store.db() as db:assert db.execute("SELECT 1 FROM sqlite_master WHERE name='recording_primary_insert_v1'").fetchone() is None


def test_read_transaction_bounds_and_index_prefix(tmp_path):
    store,chunk=setup(tmp_path);assert store.commit_window(chunk,0,2,result_for(chunk))
    with store.db() as db:
        with pytest.raises(ValueError,match='transaction'):pp.read_page(db,sid=chunk['session_id'])
        db.execute('BEGIN');page=pp.read_page(db,sid=chunk['session_id'],rid='recording')
        assert len(page['packets'])==1 and page['body_bytes']<=pp.MAX_BODY
        assert pp.read_page(db,sid=chunk['session_id'],rid='recording',after=page['next_cursor'])['packets']==[]
        assert pp.read_page(db,sid='wrong',rid='recording')['packets']==[]
        assert pp.read_exact(db,cid=chunk['id'],start=0,end=64000) is None
        assert [r['name'] for r in db.execute('PRAGMA index_info(recording_primary_recording_v1)')]==['sid','rid','sequence']
        plan=db.execute(f'EXPLAIN QUERY PLAN SELECT sequence FROM {pp.TABLE} INDEXED BY recording_primary_recording_v1 WHERE sid=? AND rid=? AND sequence>? ORDER BY sequence LIMIT 8',(chunk['session_id'],'recording',0)).fetchall()
        assert any('sid=? AND rid=? AND sequence>?' in row['detail'] for row in plan)


def test_global_quota_and_live_membership(tmp_path):
    store,chunk=setup(tmp_path);store.settings.recording_primary_provenance_global_bytes=1
    assert store.commit_window(chunk,0,2,result_for(chunk));assert counts(store)==[('',0,0)]
    assert store.chunk(chunk['id'])['provenance']['recording_primary_provenance']['counts']=={'global_quota':1}
    store.settings.recording_primary_provenance_global_bytes=512*1024**2
    with store.db() as db:db.execute('UPDATE sessions SET deleted=1 WHERE id=?',(chunk['session_id'],))
    assert store.commit_window(chunk,2,4,result_for(chunk,2,4))
    assert get_packet(store,chunk['id'],32000,64000) is None


def producer():
    label=EPOCH+':speaker_1';turns=[{'start':0.,'end':2.,'speaker_local_id':label}]
    unit={'start':.25,'end':.5,'ownership_anchor':.25,'anchor_kind':'ctc_emission','text':'x','raw_text':'x'}
    result={'segments':[{'start':.25,'end':.5,'text':'x','raw_text':'x','speaker_local_id':label,
        'asr_backend':'sensevoice','timestamp_kind':lexical.PRIMARY_TIMESTAMP,'_units':[unit]}],
        'events':[],'speaker_turns':deepcopy(turns),'metadata':{'diarization_turns':deepcopy(turns)}}
    return result,{'turns':turns}


@pytest.mark.parametrize('mutation',['normalized','label','private','backend','anchor','large','extra'])
def test_external_observation_boundaries(mutation):
    result,native=producer()
    if mutation=='label':result['segments'][0]['speaker_local_id']='bad'
    if mutation=='private':result['segments'][0]['_units'][0]['_primary_unit_ordinal']=5
    if mutation=='backend':result['segments'][0]['asr_backend']='whisper'
    if mutation=='anchor':result['segments'][0]['_units'][0]['ownership_anchor']=.3
    if mutation=='large':result['segments'][0]['_units'][0]['text']='x'*(pp.MAX_BODY+1)
    if mutation=='extra':result['segments'][0]['_units'][0]['arbitrary_extra']=object()
    original=deepcopy(result) if mutation!='extra' else None;captured=pp.observe(result,native)
    if original is not None:assert result==original
    if mutation in ('normalized','extra'):
        assert captured[1]=='pending'
        if mutation=='normalized':result['segments'][0]['end']=.6
        pp.validate_observation(result,captured)
        assert ('_primary_observation' in result)==(mutation=='extra')
    else:assert captured[0] is None and captured[1] in ('attribution','shape','unsupported','capacity')


def test_gate_defaults_and_two_quota_settings(tmp_path):
    value=Settings(data_dir=tmp_path,start_worker=False)
    assert not pp.enabled(value)
    assert value.recording_primary_provenance_recording_bytes==128*1024**2
    assert value.recording_primary_provenance_global_bytes==512*1024**2
    assert not hasattr(value,'recording_primary_provenance_enabled')
    for kwargs in ({'recording_primary_provenance_recording_bytes':0},{'recording_primary_provenance_global_bytes':True},{'recording_primary_provenance_global_bytes':1}):
        with pytest.raises(ValueError):Settings(data_dir=tmp_path,start_worker=False,**kwargs)
