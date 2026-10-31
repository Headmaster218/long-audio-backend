"""Model-free prospective retention checks. Every string/audio fixture is synthetic."""
import copy
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from audio_backend import overlap_diagnostics as d, overlap_store as os
from audio_backend.moss_td_candidates import plan_supports
from audio_backend.overlap_candidates import validate_candidates
from audio_backend.overlap_ownership import own_candidates


def turns():
    return [{'start':0,'end':20,'speaker_local_id':'a'},
            {'start':3,'end':4,'speaker_local_id':'b'},
            {'start':8,'end':9,'speaker_local_id':'b'}]


def group(raw='synthetic 少数发言\nsecond line', **extra):
    return dict(support_id='support-0',status='unresolved',reason_code='unparsed_text',
                raw_text=raw,schema_errors=['invalid native interval'],
                diagnostic_phrases=[{'start':-5,'end':-8,'text':'untrusted minority phrase'}],
                metadata={'error':'synthetic error'},phrases=[],**extra)


def bundle():
    regions,plans=plan_supports(turns(),20,{})
    return {'regions':regions,'tracks':[],'diagnostics':[d.support(plans[0],group())]}


@pytest.fixture
def db():
    connection=sqlite3.connect(':memory:');connection.row_factory=sqlite3.Row
    connection.executescript('PRAGMA foreign_keys=ON;CREATE TABLE chunks(id TEXT PRIMARY KEY,session_id TEXT);INSERT INTO chunks VALUES("c","s");')
    os.init_schema(connection)
    yield connection
    connection.close()


class Reader:
    def __init__(self,db):self.connection=db
    def session(self,sid):assert sid=='s'
    @contextmanager
    def db(self):yield self.connection


def commit(db,p=None,offset=0):
    p=bundle() if p is None else p
    return os.commit_candidates(db,dict(id='c',timeline_start=100,duration=100),offset,own_candidates(p,0,20),{})


def test_exact_multiline_unicode_capture_and_invalid_intervals_stay_opaque():
    b=bundle();validate_candidates(b,20)
    row=b['diagnostics'][0]
    assert row['retention']=='complete'
    cap=row['captures']['raw_text']
    assert cap['text']==group()['raw_text'] and cap['retention']=='complete'
    assert cap['source_bytes']==len(group()['raw_text'].encode())
    assert cap['sha256']==hashlib.sha256(group()['raw_text'].encode()).hexdigest()
    owned=own_candidates(b,2,20)
    assert owned['diagnostics'][0]['support_start']==-2
    assert owned['diagnostics'][0]['captures']==row['captures']
    assert json.loads(row['captures']['diagnostic_phrases']['text'])[0]['start']==-5
    assert not owned['tracks']
    validate_candidates(owned,18,owned=True)
    assert not own_candidates(b,20,30)['diagnostics']


@pytest.mark.parametrize('text',['汉🙂\n'*100000,'\x00'*100000,'x'*100000])
def test_byte_cap_counts_serialized_unicode_controls_and_hashes_full_observation(text):
    cap=d.capture(text,budget=4096)
    assert len(d.encoded(cap))<=4096
    assert cap['retention']=='partial' and cap['truncated'] and cap['reason']=='byte_budget'
    assert cap['full_content_observed'] and cap['sha256']==hashlib.sha256(text.encode('utf-8',errors='surrogatepass')).hexdigest()
    assert cap['retained_bytes']==len(cap['text'].encode('utf-8',errors='surrogatepass'))


def test_partial_artifact_has_no_hash_for_unread_suffix(tmp_path):
    path=tmp_path/'result.json';path.write_bytes(('汉'*10000).encode())
    cap=d.artifact(path,budget=1024)
    assert cap['retention']=='partial' and cap['truncated']
    assert not cap['full_content_observed'] and cap['sha256'] is None
    assert cap['source_bytes']==30000 and len(d.encoded(cap))<=1024
    path.write_bytes(b'bad \xff json')
    cap=d.artifact(path)
    assert cap['reason']=='invalid_utf8' and cap['retention']=='partial'
    assert cap['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    path.unlink();assert d.artifact(path)['retention']=='unavailable'


def test_failure_capture_redacts_paths_credentials_and_no_ambient_metadata(tmp_path):
    _,plans=plan_supports(turns(),20,{})
    g=group();g['metadata'].update(error='password=synthetic-secret /private/local/file.wav',environment={'token':'never capture'})
    record=d.support(plans[0],g)
    encoded=d.encoded(record).decode()
    assert 'synthetic-secret' not in encoded and '/private/' not in encoded and 'never capture' not in encoded
    assert record['captures']['failure']['retention']=='partial'
    output=tmp_path/'result.json'
    output.with_suffix('.error.json').write_text('{"error":"Bearer secret-value", "path":"/private/file"}')
    batch=d.batch_failure(plans,output,'runtime_failure',RuntimeError('/secret/absolute password=bad'))
    data=d.encoded(batch).decode()
    assert 'secret-value' not in data and '/private/' not in data and '/secret/' not in data
    assert batch['captures']['failure']['text']=='RuntimeError'


def test_shared_support_dedup_private_export_pagination_and_public_shape(db):
    p=bundle();commit(db,p);commit(db,copy.deepcopy(p))
    assert db.execute('SELECT COUNT(*) FROM overlap_support_diagnostics').fetchone()[0]==1
    assert db.execute('SELECT COUNT(*) FROM overlap_regions').fetchone()[0]==2
    reader=Reader(db);public=os.candidates(reader,'s')
    assert 'diagnostics' not in public and group()['raw_text'] not in json.dumps(public,ensure_ascii=False)
    private=os.private_diagnostics(reader,'s',limit=1)
    env=private['diagnostics'][0]['envelope']
    assert env['support_start']==100 and env['time_axis']=='session_absolute'
    assert env['captures']==p['diagnostics'][0]['captures']
    refs=os.private_diagnostic_status(reader,'s')['regions']
    assert len({r['diagnostic_id'] for r in refs})==1 and all(r['full_raw_output_available'] for r in refs)
    assert os.private_diagnostics(reader,'s',private['next_cursor'])['diagnostics']==[]
    commit(db,p,offset=20)
    assert len(os.private_diagnostics(reader,'s',private['next_cursor'])['diagnostics'])==1
    assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    db.execute('DELETE FROM chunks WHERE id="c"')
    assert db.execute('SELECT COUNT(*) FROM overlap_support_diagnostics').fetchone()[0]==0


def test_conflicting_retry_never_overwrites_original(db):
    commit(db);modified=bundle();modified['diagnostics'][0]['captures']['raw_text']=d.capture('different')
    with pytest.raises(ValueError,match='Conflicting diagnostic retry'):commit(db,modified)
    env=os.private_diagnostics(Reader(db),'s')['diagnostics'][0]['envelope']
    assert env['captures']['raw_text']['text']==group()['raw_text']


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(key='other'), lambda r:r.update(support_start=1),
    lambda r:r.update(time_axis='session_absolute'),lambda r:r.update(candidate_only=False),
    lambda r:r['captures']['raw_text'].update(source_bytes=999),
    lambda r:r['captures']['raw_text'].update(full_content_observed=False),
    lambda r:r['captures']['raw_text'].update(sha256='0'*64),
    lambda r:r.update(secret='forbidden unexpected field')])
def test_validator_rejects_false_retention_and_unsafe_association(mutation):
    p=bundle();mutation(p['diagnostics'][0])
    with pytest.raises(ValueError):validate_candidates(p,20)


def test_record_window_and_page_serialization_limits(db):
    p=bundle();record=p['diagnostics'][0]
    record['captures']['raw_text']=d.capture('🙂'*100000)
    record['captures']['diagnostic_phrases']=d.capture(['字'*30000],budget=24*1024,json_value=True)
    record['retention']='partial'
    validate_candidates(p,20)
    assert len(d.encoded(record))<d.MAX_RECORD_BYTES
    for offset in range(0,80,20):commit(db,p,offset=offset)
    page=os.private_diagnostics(Reader(db),'s',limit=500)
    assert len(d.encoded(page))<=d.MAX_PAGE_BYTES
    assert len(page['diagnostics'])==4
    bad=copy.deepcopy(p);bad['diagnostics']*=6
    with pytest.raises(ValueError):validate_candidates(bad,20)


def test_real_dev8_schema_upgrade_keeps_old_rows_and_labels_absence():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript((Path(__file__).parent/'fixtures/overlap_schema_dev8.sql').read_text())
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='overlap_support_diagnostics'").fetchone() is None
    db.execute('INSERT INTO chunks VALUES("c","s")')
    # Historical columns are genuinely pre-migration, with no diagnostic table.
    db.execute("""INSERT INTO overlap_regions(chunk_id,window_start,local_group_id,overlap_group_id,start,end,support_start,support_end,status,reason_code,detected_source_count,detected_speaker_local_ids_json,capacity,original_mixture_retained,schema_error_count,raw_output_sha256)
        VALUES('c',0,'g','c/g',0,1,0,2,'unresolved','unparsed_text',2,'[]',8,1,2,?)""",('a'*64,))
    before=dict(db.execute('SELECT * FROM overlap_regions').fetchone())
    os.init_schema(db);os.init_schema(db)
    assert dict(db.execute('SELECT * FROM overlap_regions').fetchone())==before
    refs=os.private_diagnostic_status(Reader(db),'s')['regions']
    assert refs[0]['diagnostic_retention']=='hash_and_counts_only'
    assert refs[0]['unavailability_reason']=='not_persisted_by_this_backend_version'
    assert not refs[0]['full_raw_output_available'] and refs[0]['diagnostic_id'] is None
    assert not os.private_diagnostics(Reader(db),'s')['diagnostics']
    db.execute('UPDATE overlap_regions SET schema_error_count=NULL,raw_output_sha256=NULL')
    assert os.private_diagnostic_status(Reader(db),'s')['regions'][0]['diagnostic_retention']=='status_and_reason_only'
    db.close()


@pytest.fixture
def adapter(tmp_path,monkeypatch):
    import wave
    from audio_backend import moss_td_candidates as m
    wav=tmp_path/'input.wav'
    with wave.open(str(wav),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0\0'*20*16000)
    def run(mode):
        def fake(args,limits,**kwargs):
            request=json.loads(Path(args[-1]).read_text());output=Path(request['output'])
            if mode=='missing':raise RuntimeError('password=unsafe-secret /private/model')
            if mode=='malformed':output.write_text('{"synthetic":"unfinished');return
            if mode=='oversized':output.write_text('汉'*800000);return
            if mode=='invalid_utf8':output.write_bytes(b'\xffbad');return
            g=group()
            if mode=='native_budget':g.update(reason_code='native_generation_budget_exhausted')
            if mode=='bad_time':g.update(status='ok',phrases=[{'speaker_local_id':'S00','start':-1,'end':2,'text':'invalid candidate'}])
            if mode=='oversized_raw':g['raw_text']='汉'*200000
            if mode=='bad_errors':g['schema_errors']={'unexpected':'shape'}
            output.write_text(json.dumps({'supports':[g]}))
        monkeypatch.setattr(m,'run_bounded',fake)
        return m.analyze_candidates(wav,turns(),dict(data_dir=str(tmp_path),moss_td_python='/unused'))
    return run


@pytest.mark.parametrize('mode',['normal','native_budget','bad_time','missing','malformed','oversized','oversized_raw','bad_errors','invalid_utf8'])
def test_adapter_retains_failed_evidence_without_usable_tracks(adapter,mode):
    result=adapter(mode)
    assert result['diagnostics'] and not result['tracks']
    validate_candidates(result,20)
    assert len(d.encoded(result))<2*1024**2
    assert all(r['status']=='unresolved' for r in result['regions'])
    if mode in ('normal','native_budget','bad_time','oversized_raw','bad_errors'):
        support=result['diagnostics'][0]
        assert support['captures']['raw_text']['text']
        assert support['evidence_time_axis']=='support_relative_unverified'
    if mode=='oversized':
        part=result['diagnostics'][-1]['captures']['result_artifact']
        assert part['retention']=='partial' and part['sha256'] is None
    if mode=='missing':
        batch=result['diagnostics'][0]
        assert batch['captures']['result_artifact']['retention']=='unavailable'
        assert 'unsafe-secret' not in json.dumps(result) and '/private/' not in json.dumps(result)


def test_cursor_evidence_atomic_rollback_retry_and_main_identity_unchanged(tmp_path,monkeypatch):
    from audio_backend.settings import Settings
    from audio_backend.storage import Store
    settings=Settings(data_dir=tmp_path,disk_reserve_bytes=0,start_worker=False)
    store=Store(tmp_path/'test.sqlite',settings)
    sid=store.create_session('synthetic retention')['id']
    source=tmp_path/'synthetic.audio';source.write_bytes(b'synthetic')
    cid,_=store.add_chunk(sid,'key','digest',source,20);chunk=store.claim()
    result={'segments':[{'start':0,'end':2,'text':'original mixture','speaker_local_id':None}],
            'speaker_turns':[],'events':[],'metadata':{},'overlap_candidates':own_candidates(bundle(),0,20)}
    with store.db() as db:
        db.execute("CREATE TRIGGER synthetic_fail_region BEFORE INSERT ON overlap_regions BEGIN SELECT RAISE(ABORT,'synthetic failure after diagnostics'); END")
    with pytest.raises(sqlite3.IntegrityError,match='synthetic failure'):store.commit_window(chunk,0,20,result)
    assert store.chunk(cid)['next_offset']==0
    with store.db() as db:
        for table in ('overlap_support_diagnostics','overlap_regions','segments','speakers','speaker_examples','speaker_observations'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0
        db.execute('DROP TRIGGER synthetic_fail_region')
    assert store.commit_window(chunk,0,20,result)
    assert not store.commit_window(chunk,0,20,result)
    assert store.chunk(cid)['next_offset']==20
    assert [r['text'] for r in store.results(sid)['segments']]==['original mixture']
    assert len(store.overlap_diagnostics(sid)['diagnostics'])==1
    assert not store.overlap_candidates(sid)['tracks']
    with store.db() as db:
        for table in ('speakers','speaker_examples','speaker_observations'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0


def test_disk_quota_failure_does_not_advance_cursor(tmp_path,monkeypatch):
    from audio_backend.settings import Settings
    from audio_backend.storage import Store
    from audio_backend import resources
    store=Store(tmp_path/'test.sqlite',Settings(data_dir=tmp_path,disk_reserve_bytes=0,start_worker=False))
    sid=store.create_session('synthetic quota')['id'];source=tmp_path/'source';source.write_bytes(b'synthetic')
    cid,_=store.add_chunk(sid,'k','d',source,20);chunk=store.claim()
    def reject(settings,additional_disk=0):
        assert additional_disk>65536
        raise RuntimeError('synthetic disk reserve')
    monkeypatch.setattr(resources,'check_resources',reject)
    with pytest.raises(RuntimeError,match='synthetic disk reserve'):
        store.commit_window(chunk,0,20,{'segments':[],'events':[],'overlap_candidates':own_candidates(bundle(),0,20)})
    assert store.chunk(cid)['next_offset']==0 and not store.overlap_diagnostics(sid)['diagnostics']


def test_private_page_byte_limit_stops_and_continues_at_immutable_row(db):
    p=bundle();record=p['diagnostics'][0]
    for name in ('raw_text','schema_errors','diagnostic_phrases','emitted_phrases'):
        record['captures'][name]=d.capture('🙂'*100000,budget=24*1024)
    record['retention']='partial'
    # Multiple isolated chunks simulate a long session without unbounded output.
    for index in range(12):
        cid=f'page-{index}';db.execute('INSERT INTO chunks VALUES(?,"s")',(cid,))
        os.commit_candidates(db,dict(id=cid,timeline_start=0,duration=20),0,own_candidates(p,0,20),{})
    reader=Reader(db);cursor=0;ids=[]
    while True:
        page=os.private_diagnostics(reader,'s',cursor,50)
        assert len(d.encoded(page))<=d.MAX_PAGE_BYTES
        if not page['diagnostics']:break
        assert 0<len(page['diagnostics'])<12
        ids += [r['id'] for r in page['diagnostics']]
        assert page['next_cursor']>cursor
        cursor=page['next_cursor']
    assert len(ids)==len(set(ids))==12


def test_multiple_supports_and_empty_diagnostic_phrases_preserve_failed_emitted_phrases(tmp_path,monkeypatch):
    import wave
    from audio_backend import moss_td_candidates as m
    wav=tmp_path/'input.wav'
    with wave.open(str(wav),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*40*16000)
    ts=[{'start':0,'end':40,'speaker_local_id':x} for x in ('a','b')]
    def fake(args,*a,**kw):
        req=json.loads(Path(args[-1]).read_text())
        first=group();first.update(status='ok',diagnostic_phrases=[],phrases=[{'speaker_local_id':'S00','start':-1,'end':2,'text':'invalid emitted phrase'}])
        second=group();second['support_id']='support-320000'
        Path(req['output']).write_text(json.dumps({'supports':[first,second]}))
    monkeypatch.setattr(m,'run_bounded',fake)
    result=m.analyze_candidates(wav,ts,dict(data_dir=str(tmp_path),moss_td_python='/unused'))
    assert len(result['diagnostics'])==3 and not result['tracks']
    first=result['diagnostics'][0]
    assert json.loads(first['captures']['diagnostic_phrases']['text'])==[]
    assert json.loads(first['captures']['emitted_phrases']['text'])[0]['start']==-1
    assert result['diagnostics'][-1]['captures']['result_artifact']['reason']=='already_retained_by_support'
    # Both supports survive failure in the first parsed support.
    assert result['diagnostics'][1]['key']=='support-320000'


def test_nonfinite_native_evidence_stays_opaque_json_text():
    regions,plans=plan_supports(turns(),20,{})
    g=group();g['diagnostic_phrases']=[{'start':float('nan'),'end':float('inf'),'text':'synthetic invalid'}]
    record=d.support(plans[0],g)
    p={'regions':regions,'tracks':[],'diagnostics':[record]}
    validate_candidates(p,20)
    assert 'NaN' in record['captures']['diagnostic_phrases']['text']
    assert not own_candidates(p,0,20)['tracks']


def test_wrong_support_count_retains_available_support_before_batch_failure(tmp_path,monkeypatch):
    import wave
    from audio_backend import moss_td_candidates as m
    wav=tmp_path/'input.wav'
    with wave.open(str(wav),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*40*16000)
    def fake(args,*a,**kw):
        req=json.loads(Path(args[-1]).read_text())
        Path(req['output']).write_text(json.dumps({'supports':[group()]}))
    monkeypatch.setattr(m,'run_bounded',fake)
    result=m.analyze_candidates(wav,[{'start':0,'end':40,'speaker_local_id':x} for x in ('a','b')],dict(data_dir=str(tmp_path),moss_td_python='/unused'))
    assert len(result['diagnostics'])==2 and not result['tracks']
    assert result['diagnostics'][0]['captures']['raw_text']['text']==group()['raw_text']
    cap=result['diagnostics'][1]['captures']['result_artifact']
    assert cap['full_content_observed'] and cap['sha256']


def test_serialized_candidate_budget_failure_preserves_quarantined_output(adapter,monkeypatch):
    from audio_backend import moss_td_candidates as m
    # An oversized usable projection must become an explicit failed candidate
    # with bounded evidence rather than escaping to the lossy outer fallback.
    def oversized(*args):
        return [{'provenance':{'emitted_label_count':1},'scope_id':'support-0',
                 'text':'x'*(2*1024**2)}]
    monkeypatch.setattr(m,'parse_support',oversized)
    result=adapter('bad_time')
    assert not result['tracks'] and len(result['diagnostics'])==2
    assert result['diagnostics'][-1]['reason_code']=='candidate_envelope_budget_exceeded'
    assert result['diagnostics'][0]['captures']['emitted_phrases']['text']


def test_mismatched_shared_region_support_bounds_are_rejected():
    p=bundle();p['regions'][0]['support_start']=1
    with pytest.raises(ValueError,match='mismatched'):validate_candidates(p,20)


def test_malformed_error_artifact_cannot_escape_failure_retention(tmp_path,monkeypatch):
    import wave
    from audio_backend import moss_td_candidates as m
    wav=tmp_path/'input.wav'
    with wave.open(str(wav),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0\0'*20*16000)
    def fake(args,*a,**kw):
        req=json.loads(Path(args[-1]).read_text())
        Path(req['output']).with_suffix('.error.json').write_text('["synthetic malformed error envelope"]')
        raise RuntimeError('synthetic runtime failure')
    monkeypatch.setattr(m,'run_bounded',fake)
    result=m.analyze_candidates(wav,turns(),dict(data_dir=str(tmp_path),moss_td_python='/unused'))
    assert not result['tracks'] and len(result['diagnostics'])==1
    assert 'synthetic malformed error envelope' in result['diagnostics'][0]['captures']['error_artifact']['text']


@pytest.mark.parametrize('value',[{'evidence':'MALFORMED RAW VALUE'},['MALFORMED RAW LIST'],42,None])
def test_malformed_raw_types_are_opaque_json_not_absent(value):
    regions,plans=plan_supports(turns(),20,{})
    g=group();g['raw_text']=value
    row=d.support(plans[0],g);cap=row['captures']['raw_text']
    assert cap['retention']=='partial' and cap['reason']=='invalid_field_type'
    assert cap['format']=='json' and json.loads(cap['text'])==value
    assert cap['sha256']==hashlib.sha256(cap['text'].encode()).hexdigest()
    validate_candidates({'regions':regions,'tracks':[],'diagnostics':[row]},20)


def test_lone_surrogate_uses_truthful_utf8_safe_escaped_json():
    for value in ('\ud800','before\udfffafter','\ud800'*100000):
        cap=d.capture(value,budget=4096)
        assert cap['retention']=='partial' and cap['reason']=='invalid_unicode'
        assert cap['format']=='json'
        cap['text'].encode('utf-8')
        source=json.dumps(value,ensure_ascii=True,separators=(',',':')).encode()
        assert cap['source_bytes']==len(source) and cap['sha256']==hashlib.sha256(source).hexdigest()
        assert len(d.encoded(cap))<=4096
        if not cap['truncated']:assert json.loads(cap['text'])==value


@pytest.fixture
def synthetic_envelope_adapter(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    from audio_backend import moss_td_candidates as m
    class Source:
        def __init__(self,*a,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def seek(self,*a):pass
        def read(self,*a,**kw):return []
    def invoke(groups,duration):
        fake=SimpleNamespace(info=lambda _:SimpleNamespace(samplerate=16000,channels=1,duration=duration),SoundFile=Source,write=lambda *a,**kw:None)
        monkeypatch.setitem(sys.modules,'soundfile',fake)
        def run(args,*a,**kw):
            req=json.loads(Path(args[-1]).read_text());Path(req['output']).write_text(json.dumps({'supports':groups}))
        monkeypatch.setattr(m,'run_bounded',run)
        ts=[{'start':0,'end':duration,'speaker_local_id':x} for x in ('a','b')]
        return m.analyze_candidates(tmp_path/'no-audio.wav',ts,dict(data_dir=str(tmp_path),moss_td_python='/unused',moss_td_max_seconds=60))
    return invoke


def test_three_supports_over_track_limit_still_return_quarantined_evidence(synthetic_envelope_adapter):
    groups=[]
    for index in range(3):
        item=group();item.update(support_id=f'support-{index*320000}',status='ok',
            phrases=[dict(speaker_local_id='S00',start=0,end=1,text='synthetic') for _ in range(90)])
        groups.append(item)
    result=synthetic_envelope_adapter(groups,60)
    assert not result['tracks'] and len(result['diagnostics'])==4
    assert len([r for r in result['diagnostics'] if r['kind']=='support'])==3
    assert result['diagnostics'][-1]['reason_code']=='candidate_schema_failure'
    validate_candidates(result,60)


def test_bad_or_duplicate_id_does_not_erase_later_associated_support(synthetic_envelope_adapter):
    invalid=group();invalid.update(support_id='unexpected',raw_text='x'*70000)
    valid=group();valid.update(support_id='support-320000',raw_text='LATER SAFE SUPPORT')
    for groups in ([invalid,valid],[group(),group(raw='duplicate first'),valid]):
        result=synthetic_envelope_adapter(groups,40)
        assert not result['tracks']
        assert 'LATER SAFE SUPPORT' in json.dumps(result)
        if groups[0] is invalid:
            assert result['diagnostics'][-1]['captures']['result_artifact']['retention']=='partial'


def test_adapter_malformed_raw_and_surrogate_retain_private_evidence(synthetic_envelope_adapter):
    for raw in ({'evidence':'MALFORMED MUST SURVIVE'},'\ud800'):
        item=group();item['raw_text']=raw
        result=synthetic_envelope_adapter([item],20)
        assert not result['tracks']
        cap=result['diagnostics'][0]['captures']['raw_text']
        assert cap['format']=='json' and json.loads(cap['text'])==raw
        assert cap['retention']=='partial'
        d.encoded(result).decode().encode('utf-8')


@pytest.mark.parametrize('field',['text','raw_text','speaker_local_id'])
def test_invalid_usable_phrase_unicode_commits_only_private_evidence(synthetic_envelope_adapter,tmp_path,field):
    from audio_backend.settings import Settings
    from audio_backend.storage import Store
    phrase={'speaker_local_id':'S00','start':0,'end':1,'text':'synthetic','raw_text':'synthetic'}
    phrase[field]='before\ud800after'
    item=group();item.update(status='ok',phrases=[phrase])
    result=synthetic_envelope_adapter([item],20)
    assert not result['tracks']
    retained=result['diagnostics'][0]['captures']['emitted_phrases']
    assert json.loads(retained['text'])[0][field]==phrase[field]
    store=Store(tmp_path/'unicode.sqlite',Settings(data_dir=tmp_path,disk_reserve_bytes=0,start_worker=False))
    sid=store.create_session('synthetic unicode')['id'];source=tmp_path/'input';source.write_bytes(b'synthetic')
    cid,_=store.add_chunk(sid,'k','d',source,20);chunk=store.claim()
    committed=store.commit_window(chunk,0,20,dict(segments=[{'start':0,'end':1,'text':'original mixture'}],
        events=[],speaker_turns=[],metadata={},overlap_candidates=own_candidates(result,0,20)))
    assert committed and store.chunk(cid)['next_offset']==20
    assert not store.overlap_candidates(sid)['tracks']
    assert len(store.overlap_diagnostics(sid)['diagnostics'])==2
    assert store.results(sid)['segments'][0]['text']=='original mixture'
    json.dumps(store.overlap_diagnostics(sid),ensure_ascii=False).encode('utf-8')
    with store.db() as connection:
        for table in ('speakers','speaker_observations','speaker_examples'):
            assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0


@pytest.mark.parametrize('present_raw,present_errors',[(False,True),(True,False),(False,False),(True,True)])
def test_absent_and_explicit_empty_counts_hashes_persist_independently(synthetic_envelope_adapter,db,present_raw,present_errors):
    item=group(raw='');item['schema_errors']=[]
    if not present_raw:item.pop('raw_text')
    if not present_errors:item.pop('schema_errors')
    result=synthetic_envelope_adapter([item],20)
    expected_hash=hashlib.sha256(b'').hexdigest() if present_raw else None
    expected_count=0 if present_errors else None
    assert result['regions'][0].get('raw_output_sha256')==expected_hash
    assert result['regions'][0].get('schema_error_count')==expected_count
    commit(db,result)
    row=db.execute('SELECT * FROM overlap_regions LIMIT 1').fetchone()
    assert row['raw_output_sha256']==expected_hash and row['schema_error_count']==expected_count
    raw=result['diagnostics'][0]['captures']['raw_text']
    errors=result['diagnostics'][0]['captures']['schema_errors']
    assert raw['retention']==('complete' if present_raw else 'unavailable')
    assert errors['retention']==('complete' if present_errors else 'unavailable')


@pytest.mark.parametrize('method,collection',[(os.private_diagnostics,'diagnostics'),(os.private_diagnostic_status,'regions')])
def test_private_page_cap_includes_new_cursor_and_never_skips_rejected_row(db,monkeypatch,method,collection):
    p=bundle();p['regions']=p['regions'][:1]
    for index in range(10):
        cid=f'cursor-{index}';db.execute('INSERT INTO chunks VALUES(?,"s")',(cid,))
        os.commit_candidates(db,dict(id=cid,timeline_start=0,duration=20),0,own_candidates(p,0,20),{})
    reader=Reader(db);full=method(reader,'s',limit=50)
    assert len(full[collection])==10 and full['next_cursor']==10
    cap=len(d.encoded(full))-1
    monkeypatch.setattr(d,'MAX_PAGE_BYTES',cap)
    first=method(reader,'s',limit=50)
    assert len(d.encoded(first))<=cap
    assert len(first[collection])==9 and first['next_cursor']==9
    second=method(reader,'s',after=first['next_cursor'],limit=50)
    assert len(d.encoded(second))<=cap
    assert len(second[collection])==1 and second['next_cursor']==10
