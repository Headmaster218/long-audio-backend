"""Fixed metadata-only shapes; never acoustic or complete-scheduler claims."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from audio_backend import recording_primary_provenance as pp
from audio_backend import lexical_evidence as lexical
from audio_backend.native_owned_cells import RATE,SourceSpan
from audio_backend.native_owned_cell_store import Budget
from audio_backend.worker import validate_result,own_core
from test_native_owned_cells import observed_window,EPOCH
from test_recording_primary_provenance import setup,get_packet

SHAPES={
    'sparse':{'unit_count':24,'labels':1,'raw_turns':2,'seconds':10.5,
        'anchor_rule':'round((i+0.5)*10.5/count,3)','duplicate_turns':2},
    'dense':{'unit_count':512,'labels':4,'raw_turns':8,'seconds':10.5,
        'anchor_rule':'round((i+0.5)*10.5/count,3)','duplicate_turns':2},
}


def shape_result(chunk,scenario):
    shape=SHAPES[scenario];duration=shape['seconds'];frames=round(duration*RATE)
    witness,native,pcm,_=observed_window(frames);turns=[]
    for label in range(shape['labels']):
        for _ in range(shape['duplicate_turns']):
            turns.append({'start':label*duration/shape['labels'],'end':(label+1)*duration/shape['labels'],
                'speaker_local_id':EPOCH+':speaker_'+str(label+1)})
    witness.observe({'turns':turns});native['turns']=deepcopy(turns);segments=[]
    for index in range(shape['unit_count']):
        anchor=round((index+.5)*duration/shape['unit_count'],3);end=min(duration,anchor+.005)
        active={t['speaker_local_id'] for t in turns if t['start']-.03<=anchor<=t['end']+.03}
        label=next(iter(active)) if len(active)==1 else None
        unit={'start':anchor,'end':end,'ownership_anchor':anchor,'text':'x','raw_text':'x','anchor_kind':'ctc_emission'}
        if segments and segments[-1]['speaker_local_id']==label:
            segment=segments[-1];segment['_units'].append(unit);segment['end']=end;segment['text']+='x';segment['raw_text']+='x'
        else:segments.append({'start':anchor,'end':end,'text':'x','raw_text':'x','speaker_local_id':label,
            'asr_backend':'sensevoice','timestamp_kind':lexical.PRIMARY_TIMESTAMP,'confidence':None,'_units':[unit]})
    result={'segments':segments,'events':[],'speaker_turns':deepcopy(turns),'metadata':{
        'diarization_turns':deepcopy(turns),'asr_backend':'sensevoice','timestamp_semantics':lexical.PRIMARY_TIMESTAMP}}
    snapshot=pp.observe(result,native);validate_result(result,duration);pp.validate_observation(result,snapshot)
    assert result.get('_primary_status')=='pending'
    result['metadata']['native_diarization']=native['state'];result['metadata']['core_cut']={'nominal':duration,'actual':duration,'reason':'fixed_or_final'}
    own_core(result,0.,duration)
    part=SourceSpan(chunk['session_id'],chunk['id'],'b'*64,chunk['digest'],frames,0,frames,0,frames)
    capture=SimpleNamespace(witness=witness,core_seams=SimpleNamespace(run='1'*32),scope=('upload',chunk['session_id'],chunk['id']),
        scope_start=0.,origin_frame=0,observed_reset_generation=1,analyzer_start=0,analyzer=pcm)
    evidence={'chunk_id':chunk['id'],'start':0,'end':frames,'native':native['state'],'core_cut':result['metadata']['core_cut']}
    pp.freeze(capture,chunk,native,0.,duration,result,(part,),evidence,pcm)
    assert '_primary_provenance' in result,result.get('_primary_status')
    result['_native_core_seam']={'commit':deepcopy(result['_primary_provenance']['packet']['commit']),'proof':None,'reason':'core_first_or_unavailable_left'}
    result['_native_bounded_support']={'budget':Budget.for_bounded(.75),'batch':None}
    return result


@pytest.mark.parametrize('scenario',tuple(SHAPES))
def test_frozen_shape_encoder_measurements(tmp_path,scenario):
    store,chunk=setup(tmp_path,seconds=10.5);result=shape_result(chunk,scenario)
    assert store.commit_window(chunk,0.,10.5,result)
    saved=get_packet(store,chunk['id'],end=168000);assert saved is not None
    packet=saved['packet'];raw=pp.encoded(packet)
    with store.db() as db:
        page_count=db.execute('PRAGMA page_count').fetchone()[0];page_size=db.execute('PRAGMA page_size').fetchone()[0];free=db.execute('PRAGMA freelist_count').fetchone()[0]
    report={'scenario':scenario,'geometry':SHAPES[scenario],'geometry_sha256':pp.digest(SHAPES[scenario]),
        'body_bytes':len(raw),'charge_bytes':saved['header']['charge'],'parents':len(packet['rows']),'units':len(packet['units']),
        'turns':len(packet['turns']),'labels':len(packet['labels']),'db_bytes':store.path.stat().st_size,
        'page_count':page_count,'page_size':page_size,'freelist_count':free,
        'logical_12h_projection':4115*saved['header']['charge'],'logical_48h_projection':16458*saved['header']['charge'],
        'claim':'One fresh synthetic packet measured; duration totals are arithmetic projections only.'}
    reports=Path(__file__).resolve().parents[2]/'reports';(reports/('shape-'+scenario+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    assert report['units']==SHAPES[scenario]['unit_count'] and report['turns']==SHAPES[scenario]['raw_turns']


def physical(db,path,*,allocations=False):
    family={}
    for suffix in ('','-wal','-shm'):
        p=Path(str(path)+suffix)
        if p.exists():
            info=p.stat();family[suffix or 'db']={'bytes':info.st_size,'allocated_bytes':info.st_blocks*512}
        else:family[suffix or 'db']={'bytes':0,'allocated_bytes':0}
    result={'family':family,'page_size':db.execute('PRAGMA page_size').fetchone()[0],
        'page_count':db.execute('PRAGMA page_count').fetchone()[0],
        'freelist_count':db.execute('PRAGMA freelist_count').fetchone()[0]}
    if allocations:
        import sqlite3
        try:
            result['table_index_allocation']={'available':True,'entries':[dict(row) for row in db.execute(
                'SELECT name,COUNT(*) AS pages,SUM(pgsize) AS bytes,SUM(payload) AS payload_bytes,SUM(unused) AS unused_bytes FROM dbstat GROUP BY name ORDER BY name')]}
        except sqlite3.OperationalError as exc:
            if str(exc)!='no such table: dbstat':raise
            result['table_index_allocation']={'available':False,'reason':'SQLite dbstat is unavailable'}
    return result


def metadata_window(template, base_chunk, index):
    """Independent 10.5s synthetic upload scope per opportunity; no epoch bridge."""
    from audio_backend.native_owned_cells import sha
    chunk=dict(base_chunk,id='metadata-'+str(index).zfill(23),duration=10.5,timeline_start=index*10.5)
    result={'metadata':deepcopy(template['metadata']),'segments':deepcopy(template['segments']),
        '_primary_provenance':deepcopy(template['_primary_provenance'])}
    draft=result['_primary_provenance'];packet=draft['packet'];commit=packet['commit'];part=commit['part']
    epoch=format(index+1,'032x')
    old_labels=packet['labels'];labels=[epoch+label[32:] for label in old_labels];packet['labels']=labels
    for segment in result['segments']:
        if segment['speaker_local_id'] is not None:segment['speaker_local_id']=labels[old_labels.index(segment['speaker_local_id'])]
    commit['scope'].update(key=chunk['id'],origin=float(chunk['timeline_start']).hex(),epoch=epoch,index=0,reset=1)
    part.update(chunk_id=chunk['id'])
    commit['evidence_sha256']=sha({'synthetic_metadata_window':index,'source':chunk['id'],'start':0,'end':168000})
    rows=[pp._row_content(chunk,0.,segment,result['metadata']) for segment in result['segments']]
    draft['row_hashes']=[pp.digest(row) for row in rows]
    return chunk,result,rows


@pytest.mark.parametrize('scenario',tuple(SHAPES))
def test_metadata_scope_geometry_pure(scenario):
    from audio_backend import native_owned_core_seams as core
    from audio_backend.native_owned_cells import Unavailable
    base={'id':'a'*32,'session_id':'b'*32,'digest':'c'*64,'duration':10.5,'timeline_start':0.}
    template=shape_result(base,scenario)
    old=deepcopy(template['_primary_provenance']['packet']['commit'])
    old['part'].update(source_frames=128*168000,source_start=28*168000,source_end=29*168000,
        logical_start=28*168000,logical_end=29*168000)
    with pytest.raises(Unavailable,match='core_seam_reset_unsupported'):core.validate_commit(old)
    for index in (0,1,27,28,29,127,128,4114,4115,16457):
        chunk,result,rows=metadata_window(template,base,index);packet=deepcopy(result['_primary_provenance']['packet'])
        packet['refs']=[['p.'+str(index*len(rows)+i+1)+'.'+'d'*64,None] for i in range(len(rows))]
        core.validate_commit(packet['commit']);raw=pp.encoded(packet)
        header={'sequence':index+1,'sid':chunk['session_id'],'rid':'recording','cid':chunk['id'],
            'core_start':0,'core_end':168000,'core_slot':core.slot(packet['commit']),'digest':pp.digest(packet)}
        header['charge']=pp._charge(raw,header['sid'],header['rid'],header['cid'],header['core_slot'],header['digest'])
        pp.validate_packet(packet,header)
        assert packet['query'][:3]==[0,168000,0]
        assert packet['commit']['part']['source_frames']==168000
        assert all(pp._row_binding(packet,i,row) for i,row in enumerate(rows))


def metadata_series(tmp_path,scenario,count,*,pin_start,pin_length):
    """Real encoder/persist transactions, fresh synthetic upload per 10.5s core.

    No core crosses the native 300s epoch. These independent synthetic scopes
    preserve the fixed numerical shape; they do not demonstrate continuous
    capture, native epoch scheduling or historical source authority. Fixture
    core rows are removed after persistence; packets remain immutable audit.
    """
    import sqlite3
    import time
    from audio_backend import native_owned_core_seams as core
    from audio_backend import transcript_contract as contract
    from audio_backend.native_owned_cells import canonical
    store,base=setup(tmp_path,seconds=10.5);template=shape_result(base,scenario)
    reports=[];statuses={};reader=None;pin_peak=None;pin_release=None;began=time.monotonic();max_steps=max_projection=0
    refused_primary_verified=0
    timings={name:{'windows':0,'seconds':0.} for name in ('ordinary','pinned_reader')}
    with store.db() as db:
        db.execute('PRAGMA wal_autocheckpoint=1000');db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        initial=physical(db,store.path,allocations=True)
        try:
            for index in range(count):
                window_began=time.monotonic();phase='pinned_reader' if reader is not None else 'ordinary'
                chunk,result,rows=metadata_window(template,base,index);commit=result['_primary_provenance']['packet']['commit']
                budget=Budget.for_bounded(.75);result['_native_bounded_support']={'budget':budget,'reserved':budget.reserve_planning()}
                with budget.notice_phase():
                    budget.primary_sql=True;db.set_progress_handler(budget.progress,100)
                    db.execute('BEGIN IMMEDIATE');ids=[]
                    db.execute("INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,timeline_start,status,next_offset,created) VALUES(?,?,?,?,?,?,?,'running',0,0)",
                        (chunk['id'],chunk['session_id'],chunk['id'],chunk['digest'],str(tmp_path/'unused.audio'),10.5,chunk['timeline_start']))
                    db.execute("INSERT INTO recording_work(chunk_id,batch_id,position,frames,bytes,capture_start_frame,native_receipt_status) VALUES(?,'recording',?,168000,336000,?,'synthetic')",
                        (chunk['id'],index+1,index*168000))
                    db.execute('UPDATE sessions SET timeline_end=? WHERE id=?',((index+1)*10.5,chunk['session_id']))
                    for row in rows:
                        fields=[k for k in contract.PRIMARY_FIELDS if k!='id']
                        ids.append(db.execute('INSERT INTO segments('+','.join(fields)+',speaker_status) VALUES('+','.join('?' for _ in fields)+',?)',[row[k] for k in fields]+['unknown']).lastrowid)
                    slot=core.slot(commit)
                    db.execute('INSERT INTO native_core_commits(slot,sid,cid,scope_key,logical_end,body,pending,state,reason) VALUES(?,?,?,?,?,?,NULL,?,?)',
                        (slot,chunk['session_id'],chunk['id'],core.scope_key(commit['scope']),168000,canonical(commit,core.COMPACT_BYTES),'none','synthetic_metadata_fixture'))
                    status=pp.persist(store,db,chunk,0.,10.5,result,ids)
                    assert status in ('retained','recording_quota','global_quota'),(index,status)
                    statuses[status]=statuses.get(status,0)+1;summary={};pp.retain_status(summary,status,0.)
                    db.execute('UPDATE chunks SET next_offset=10.5,provenance=? WHERE id=?',(json.dumps(summary),chunk['id']))
                    db.execute('DELETE FROM native_core_commits WHERE slot=?',(slot,))
                    budget.check();db.commit();budget.check()
                    db.set_progress_handler(None,0);budget.primary_sql=False
                max_steps=max(max_steps,budget.steps);max_projection=max(max_projection,budget.sql_bytes)
                if status!='retained':
                    # Test-only post-commit checks are outside admission/budget.
                    actual_rows=[dict(row) for row in db.execute('SELECT '+','.join(contract.PRIMARY_FIELDS)+' FROM segments WHERE chunk_id=? ORDER BY id',(chunk['id'],))]
                    assert actual_rows==[dict(row,id=ident) for row,ident in zip(rows,ids)]
                    assert json.loads(db.execute('SELECT provenance FROM chunks WHERE id=?',(chunk['id'],)).fetchone()[0])==summary
                    refused_primary_verified+=1
                timings[phase]['windows']+=1;timings[phase]['seconds']+=time.monotonic()-window_began
                completed=index+1
                if reader is not None:
                    sample=physical(db,store.path)
                    if pin_peak is None or sample['family']['-wal']['bytes']>pin_peak['family']['-wal']['bytes']:pin_peak=sample
                    if completed==pin_start+pin_length:
                        reader.rollback();reader.close();reader=None
                        pin_release={'windows':completed,'before_checkpoint':physical(db,store.path,allocations=True)}
                        pin_release['checkpoint_result']=list(db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
                        pin_release['after_checkpoint']=physical(db,store.path,allocations=True)
                if completed in {4115,16458,count}:
                    # Verification aggregates are outside admission and its budget.
                    db.execute('BEGIN')
                    usage=dict(db.execute(f"SELECT packets,charge FROM {pp.USAGE} WHERE scope='' ").fetchone())
                    recording=dict(db.execute(f"SELECT packets,charge FROM {pp.USAGE} WHERE scope='recording'").fetchone())
                    actual=dict(db.execute(f'SELECT COUNT(*) AS packets,COALESCE(SUM(charge),0) AS charge,COALESCE(SUM(length(body)),0) AS body_bytes FROM {pp.TABLE}').fetchone())
                    assert usage==recording=={k:actual[k] for k in ('packets','charge')}
                    assert actual['packets']==statuses.get('retained',0)
                    primary=db.execute("SELECT COUNT(*) FROM segments WHERE chunk_id LIKE 'metadata-%'").fetchone()[0]
                    completed_chunks=db.execute("SELECT COUNT(*) FROM chunks WHERE id LIKE 'metadata-%' AND next_offset=10.5").fetchone()[0]
                    assert primary==completed*len(template['segments']) and completed_chunks==completed
                    retained_status={reason:db.execute("SELECT COALESCE(SUM(json_extract(provenance,?)),0) FROM chunks WHERE id LIKE 'metadata-%'",('$.recording_primary_provenance.counts.'+reason,)).fetchone()[0] for reason in statuses}
                    assert retained_status==statuses
                    reports.append({'windows':completed,'elapsed_seconds':time.monotonic()-began,'usage':usage,'recording_usage':recording,
                        'actual_retained':actual,'primary_rows':primary,'completed_chunks':completed,'statuses':dict(statuses),
                        'refused_windows_exact_primary_and_status_verified':refused_primary_verified,
                        'stored_status_counts':retained_status,**physical(db,store.path,allocations=True)})
                    db.rollback()
                if completed==pin_start:
                    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                    reader=sqlite3.connect('file:'+str(store.path)+'?mode=ro',uri=True)
                    reader.execute('BEGIN');reader.execute(f"SELECT charge FROM {pp.USAGE} WHERE scope='' ").fetchone()
            db.set_progress_handler(None,0);reads=[]
            for after,rid,sid in [(0,'recording',base['session_id']),(max(0,statuses.get('retained',0)-8),'recording',base['session_id']),(0,'recording','wrong')]:
                steps=[0];sql=[]
                def progress():steps[0]+=1;return 0
                db.execute('BEGIN');db.set_progress_handler(progress,1);db.set_trace_callback(sql.append);start=time.monotonic()
                page=pp.read_page(db,sid=sid,rid=rid,after=after)
                seconds=time.monotonic()-start;db.set_progress_handler(None,0);db.set_trace_callback(None);db.rollback()
                assert len(page['packets'])<=8 and page['body_bytes']<=pp.MAX_BODY
                reads.append({'after':after,'matching_session':sid==base['session_id'],'packets':len(page['packets']),
                    'body_bytes':page['body_bytes'],'sql_statements':len(sql),'sqlite_vm_steps':steps[0],'seconds':seconds})
            plan=[r['detail'] for r in db.execute(f'EXPLAIN QUERY PLAN SELECT sequence FROM {pp.TABLE} INDEXED BY recording_primary_recording_v1 WHERE sid=? AND rid=? AND sequence>? ORDER BY sequence LIMIT 8',(base['session_id'],'recording',0))]
            assert any('sid=? AND rid=? AND sequence>?' in p for p in plan) and reads[-1]['packets']==0
            final_checkpoint=list(db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
            final_physical=physical(db,store.path,allocations=True)
        finally:
            db.set_progress_handler(None,0)
            if reader is not None:reader.rollback();reader.close()
    return {'scenario':scenario,'geometry':SHAPES[scenario],'geometry_sha256':pp.digest(SHAPES[scenario]),
        'claim':'Metadata-only real encoder/persist; independent synthetic10.5s upload per opportunity, fixture-owned primary/core rows, no continuous capture/scheduler/duration claim.',
        'windows':count,'seconds':time.monotonic()-began,'statuses':statuses,'measurements':reports,'transaction_timings':timings,
        'initial_physical':initial,'final_checkpoint_result':final_checkpoint,'final_physical':final_physical,
        'refused_windows_exact_primary_and_status_verified':refused_primary_verified,
        'pinned_reader':{'starts_after_window':pin_start,'write_windows':pin_length,'peak':pin_peak,'release':pin_release},
        'bounded_reads':reads,'query_plan':plan,'maximum_window_sql_vm_steps':max_steps,'maximum_window_projected_sql_bytes':max_projection}


@pytest.mark.parametrize('scenario',tuple(SHAPES))
def test_metadata_128_transaction_calibration(tmp_path,scenario):
    report=metadata_series(tmp_path,scenario,128,pin_start=32,pin_length=64)
    assert report['statuses']=={'retained':128}
    reports=Path(__file__).resolve().parents[2]/'reports';(reports/('metadata-128-'+scenario+'.json')).write_text(json.dumps(report,indent=2)+'\n')


@pytest.mark.parametrize('scenario',tuple(SHAPES))
def test_metadata_16458_opportunity_sizing(tmp_path,scenario):
    report=metadata_series(tmp_path,scenario,16458,pin_start=4115,pin_length=256)
    assert [m['windows'] for m in report['measurements']]==[4115,16458]
    if scenario=='sparse':assert report['statuses']=={'retained':16458}
    else:
        assert set(report['statuses'])=={'retained','recording_quota'}
        assert report['statuses']['retained']>4115 and report['statuses']['recording_quota']>0
    report['count_basis']={'twelve_hour_opportunities':4115,'forty_eight_hour_opportunities':16458,
        'nominal_seconds_per_opportunity':10.5,
        'limitation':'Independent synthetic sources; opportunity counts do not establish continuous capture or universal duration retention.'}
    reports=Path(__file__).resolve().parents[2]/'reports';(reports/('metadata-16458-'+scenario+'.json')).write_text(json.dumps(report,indent=2)+'\n')
