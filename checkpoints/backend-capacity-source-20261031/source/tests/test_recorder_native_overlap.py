"""Model-free geometry and synthetic-export coupling, not native qualification."""
import copy
import json
from urllib.parse import urlsplit,parse_qs
import pytest
from audio_backend import recorder_native_overlap as n
from audio_backend import recorder_overlap_candidates as candidate
from audio_backend import recorder_incremental_span_export as export
from test_recorder_incremental_span_status import fixture
from test_recorder_overlap_candidates import Fake,arguments


def evidence(sid='a'*32,cid='b'*32,frames=160000,start=0,sha='c'*64,turns=None):
    if turns is None:turns=[(0,64000,'e:speaker_1'),(32000,96000,'e:speaker_2')]
    body=dict(schema='bound-native-turn-evidence-v1',session_id=sid,chunk_id=cid,
        source_sha256=sha,frames=frames,timeline_start_frame=start,capture_start_frame=start,
        receipt_sha256='d'*64,source_mapping=[],cores=[dict(start_frame=0,end_frame=frames,
        slot='1'*64,status='retained',proof_sha256='2'*64,scope=dict(session_id=sid,epoch='e'),
        turns=[dict(ordinal=i,start_frame=a+start,end_frame=b+start,speaker_local_id=k)
               for i,(a,b,k) in enumerate(turns)])])
    body['evidence_sha256']=n.digest(body);return body


def package(chunks):
    frames=sum(c['frames'] for c in chunks)
    source=dict(frames=frames,target=dict(session_id='a'*32))
    doc=dict(schema=n.SCHEMA,source_content_binding=source,frames=frames,chunks=chunks)
    work=[dict(chunk_id=c['chunk_id'],span_start_frame=c['timeline_start_frame'],
        span_end_frame=c['timeline_start_frame']+c['frames']) for c in chunks]
    return doc,source,work,{c['chunk_id']:c['source_sha256'] for c in chunks}


def test_native_intersections_context_and_no_identity():
    result=n.select(*package([evidence()]))
    assert result['ranges']==[dict(start_frame=16000,end_frame=80000)]
    assert result['detected_overlap_frames']==32000
    assert result['selected_detected_overlap_frames']==32000
    assert result['reference_overlap_coverage'] is None and not result['global_speaker_identity']

@pytest.mark.parametrize('turns', [[],[(0,64000,'e:speaker_1')],
    [(0,64000,'e:speaker_1'),(0,64000,'e:speaker_1')],
    [(0,64000,'e:speaker_1'),(64000,96000,'e:speaker_2')]])
def test_empty_single_same_label_touching_are_not_overlap(turns):
    result=n.select(*package([evidence(turns=turns)]))
    assert result['ranges']==[] and result['detected_overlap_frames']==0
    assert result['absent_detection_does_not_prove_no_overlap']


def test_distinct_core_scopes_not_pooled():
    x=evidence(frames=160000,turns=[(0,80000,'e:speaker_1')])
    x['cores'][0]['end_frame']=80000
    x['cores'].append(dict(start_frame=80000,end_frame=160000,slot='3'*64,status='retained',
        proof_sha256='4'*64,scope=dict(session_id='a'*32,epoch='f'),
        turns=[dict(ordinal=0,start_frame=80000,end_frame=160000,speaker_local_id='f:speaker_2')]))
    x['evidence_sha256']=n.digest({k:v for k,v in x.items() if k!='evidence_sha256'})
    assert n.select(*package([x]))['ranges']==[]


def test_global_range_cap_and_omission():
    chunks=[evidence(cid=f'{i:032x}',start=i*160000) for i in range(9)]
    result=n.select(*package(chunks))
    assert len(result['ranges'])==8 and result['omitted_support_ranges']==1
    assert result['selected_detected_overlap_frames']==8*32000
    assert result['detected_overlap_frames']==9*32000

@pytest.mark.parametrize('change',['digest','source','missing','scope','outside','core_gap'])
def test_bad_binding_refuses(change):
    args=list(package([evidence()]));x=args[0]['chunks'][0]
    if change=='digest':x['evidence_sha256']='0'*64
    if change=='source':args[3][x['chunk_id']]='9'*64
    if change=='missing':args[0]['chunks']=[]
    if change=='scope':x['cores'][0]['turns'][0]['speaker_local_id']='foreign:speaker_1'
    if change=='outside':x['cores'][0]['turns'][0]['end_frame']=160001
    if change=='core_gap':x['cores'][0]['start_frame']=1
    if change in ('scope','outside','core_gap'):
        x['evidence_sha256']=n.digest({k:v for k,v in x.items() if k!='evidence_sha256'})
    with pytest.raises(Exception):n.select(*args)


def native_shaped_fixture_ids(app):
    # Existing export fixture uses work-N IDs. Native reader requires real-shaped
    # 32hex IDs; adjust this synthetic fixture before any export is observed.
    with app.state.store.db() as db:
        db.execute('BEGIN')
        db.execute('PRAGMA defer_foreign_keys=ON')
        rows=db.execute('SELECT id FROM chunks ORDER BY id').fetchall()
        for i,row in enumerate(rows):
            cid=f'{i+1:032x}'
            db.execute('UPDATE chunks SET id=? WHERE id=?',(cid,row['id']))
            db.execute('UPDATE recording_work SET chunk_id=? WHERE chunk_id=?',(cid,row['id']))


@pytest.mark.parametrize('empty',[False,True])
def test_export_auto_candidate_roundtrip_without_models(tmp_path,empty):
    with fixture(tmp_path,count=1,frames=32000) as (args,run,routes,app,*_):
        native_shaped_fixture_ids(app)
        class Native:
            reads=0
            def request(self,method,path,**kw):
                if '/native-turn-evidence?' not in path:return routes.request(method,path,**kw)
                assert method=='GET' and not kw
                self.reads+=1;q=parse_qs(urlsplit(path).query);parts=urlsplit(path).path.split('/')
                return evidence(parts[2],parts[4],int(q['frames'][0]),sha=q['source_sha256'][0],
                    turns=[] if empty else [(0,24000,'e:speaker_1'),(8000,32000,'e:speaker_2')])
        http=Native();kw=dict(run,transport=http)
        export.run(**kw,output_dir=tmp_path/'export',native_turns=True)
        assert http.reads==2
        complete=json.loads((tmp_path/'export/complete.json').read_text())
        assert set(complete['artifacts'])=={'export.json','transcript.txt','native-turns.json'}
        opts=arguments(tmp_path,args);opts.update(selected_ranges=None,auto_native_overlap=True)
        Fake.calls=0;result=candidate.run(**opts)
        assert Fake.calls==(0 if empty else 1)
        assert result['status']==('no_detected_native_overlap' if empty else 'candidates_recorded')
        assert (tmp_path/'candidates/selection.json').exists()
        if empty:assert not (tmp_path/'candidates/call-00.intent.json').exists()


def test_changed_native_reread_never_commits_export(tmp_path):
    with fixture(tmp_path,count=1,frames=32000) as (args,run,routes,app,*_):
        native_shaped_fixture_ids(app)
        class Native:
            reads=0
            def request(self,method,path,**kw):
                if '/native-turn-evidence?' not in path:return routes.request(method,path,**kw)
                self.reads+=1;q=parse_qs(urlsplit(path).query);parts=urlsplit(path).path.split('/')
                x=evidence(parts[2],parts[4],int(q['frames'][0]),sha=q['source_sha256'][0],turns=[])
                if self.reads>1:x['receipt_sha256']='8'*64;x['evidence_sha256']=n.digest({k:v for k,v in x.items() if k!='evidence_sha256'})
                return x
        with pytest.raises(Exception):export.run(**dict(run,transport=Native()),output_dir=tmp_path/'export',native_turns=True)
        assert not (tmp_path/'export/complete.json').exists()


def test_merged_supports_split_at_twenty_seconds_without_label_merging():
    chunks=[evidence(cid=f'{i:032x}',start=i*160000,
        turns=[(0,160000,'e:speaker_1'),(0,160000,'e:speaker_2')]) for i in range(3)]
    result=n.select(*package(chunks))
    assert result['ranges']==[dict(start_frame=0,end_frame=320000),dict(start_frame=320000,end_frame=480000)]
    assert len(result['detected_regions'])==3
    assert result['detected_overlap_frames']==480000


def test_auto_requires_bound_artifact_before_output(tmp_path):
    with fixture(tmp_path,count=1,frames=32000) as (args,run,*_):
        export.run(**run,output_dir=tmp_path/'export')
        opts=arguments(tmp_path,args);opts.update(selected_ranges=None,auto_native_overlap=True)
        Fake.calls=0
        with pytest.raises(Exception):candidate.run(**opts)
        assert Fake.calls==0 and not (tmp_path/'candidates').exists()


def test_global_turn_coordinates_receive_no_second_chunk_offset():
    first=evidence(frames=160000,turns=[])
    second=evidence(cid='9'*32,start=160000)
    result=n.select(*package([first,second]))
    assert result['ranges']==[dict(start_frame=176000,end_frame=240000)]
    assert result['detected_regions'][0]['start_frame']==192000
    assert result['detected_regions'][0]['end_frame']==224000


@pytest.mark.parametrize('kind',['empty','gap','duplicate','tail_missing'])
def test_incomplete_work_never_means_no_detection(kind):
    args=list(package([evidence(turns=[]),evidence(cid='9'*32,start=160000,turns=[])]))
    if kind=='empty':args[0]['chunks']=[];args[2]=[];args[3]={}
    if kind=='gap':args[2][1]['span_start_frame']+=1
    if kind=='duplicate':args[2][1]['chunk_id']=args[2][0]['chunk_id']
    if kind=='tail_missing':args[0]['chunks'].pop();args[2].pop();args[3].pop('9'*32)
    with pytest.raises(Exception):n.select(*args)


def test_selector_observes_cancellation_during_geometry():
    calls=0
    def check():
        nonlocal calls
        calls+=1
        if calls==4:raise RuntimeError('cancelled test')
    with pytest.raises(RuntimeError,match='cancelled test'):
        n.select(*package([evidence()]),check=check)
    assert calls==4


def test_oversized_committed_work_refuses_before_pcm_or_model(tmp_path,monkeypatch):
    with fixture(tmp_path,count=1,frames=320000) as (args,run,*_):
        export.run(**run,output_dir=tmp_path/'export')
        root=tmp_path/'export';primary=json.loads((root/'export.json').read_text())
        primary['work_text_coverage']=[dict(chunk_id='a'*32,span_start_frame=0,span_end_frame=320000)]
        raw=json.dumps(primary).encode();(root/'export.json').write_bytes(raw)
        document=dict(schema=n.SCHEMA,source_content_binding=primary['completion']['source_content_binding'],frames=320000,chunks=[])
        native_raw=json.dumps(document).encode();(root/'native-turns.json').write_bytes(native_raw)
        (root/'native-turns.json').chmod(0o600)
        manifest=json.loads((root/'complete.json').read_text())
        manifest['artifacts']['export.json']=dict(bytes=len(raw),sha256=candidate.digest(raw))
        manifest['artifacts']['native-turns.json']=dict(bytes=len(native_raw),sha256=candidate.digest(native_raw))
        (root/'complete.json').write_text(json.dumps(manifest))
        def forbidden(*a,**kw):raise AssertionError('PCM must not be allocated')
        monkeypatch.setattr(candidate,'pcm_for',forbidden)
        opts=arguments(tmp_path,args);opts.update(selected_ranges=None,auto_native_overlap=True)
        Fake.calls=0
        with pytest.raises(Exception,match='native_turn_work_coverage'):candidate.run(**opts)
        assert Fake.calls==0 and not (tmp_path/'candidates').exists()
