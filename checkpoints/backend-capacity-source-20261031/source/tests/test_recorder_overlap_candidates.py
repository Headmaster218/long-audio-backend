import json
import pytest
from audio_backend import recorder_overlap_candidates as candidate
from audio_backend import recorder_incremental_span_export as export
from test_recorder_incremental_span_status import fixture

class Fake:
    calls = 0
    def __init__(self, *args, **kwargs): pass
    def submit(self, support, pcm):
        self.support = support
        assert len(pcm) == 2*(support.end_frame-support.start_frame)
        return 'a'*32
    def run(self, job):
        type(self).calls += 1
        return dict(state='returned_diagnostic', publication_valid=True,
            cleanup=dict(cleanup_verified=True,original_handle_waited_and_reaped=True,remaining_live_owned=[]),
            result=dict(complete_content=False, speaker_identity_valid=False, diagnostic_phrases=[]))
    def exact_raw(self, job): return b''
    def cancel(self, job): pass

def arguments(tmp_path, args):
    return dict(job_dir=args['job_dir'], export_dir=tmp_path/'export',
        selected_ranges=[dict(start_frame=0,end_frame=16000)], model='/fixed.gguf',
        output_dir=tmp_path/'candidates',exclusive_inputs=True,aggregate_budget_managed=True,_controller=Fake)

def test_end_to_end_local_completed_baseline_and_no_network(tmp_path):
    with fixture(tmp_path, count=1, frames=32000) as (args, run, routes, app, state):
        export.run(**run, output_dir=tmp_path/'export')
        before = len(routes.calls); Fake.calls=0
        result = candidate.run(**arguments(tmp_path,args))
        assert result['status']=='candidates_recorded' and Fake.calls==1
        assert result['complete_content'] is False and len(routes.calls)==before
        with pytest.raises(Exception): candidate.run(**arguments(tmp_path,args))
        assert Fake.calls==1

@pytest.mark.parametrize('ranges', [[],[dict(start_frame=0,end_frame=320001)],
    [dict(start_frame=True,end_frame=10)], [dict(start_frame=1,end_frame=0)]])
def test_bad_ranges_no_effect(tmp_path,ranges):
    with fixture(tmp_path,count=1,frames=32000) as (args,run,*_):
        export.run(**run,output_dir=tmp_path/'export'); kw=arguments(tmp_path,args)
        kw['selected_ranges']=ranges; Fake.calls=0
        with pytest.raises(Exception): candidate.run(**kw)
        assert Fake.calls==0 and not (tmp_path/'candidates').exists()

def test_changed_baseline_refuses_before_dispatch(tmp_path):
    with fixture(tmp_path,count=1,frames=32000) as (args,run,*_):
        export.run(**run,output_dir=tmp_path/'export')
        (tmp_path/'export/transcript.txt').write_text('changed')
        Fake.calls=0
        with pytest.raises(Exception): candidate.run(**arguments(tmp_path,args))
        assert Fake.calls==0 and not (tmp_path/'candidates').exists()

@pytest.mark.parametrize('kind', ['unknown','cleanup','cancelled'])
def test_failed_call_stops_and_never_replays(tmp_path,kind):
    class Failed(Fake):
        def run(self,job):
            observed=super().run(job)
            if kind=='cleanup': observed['cleanup']['cleanup_verified']=False
            else: observed.update(state='failed',publication_valid=False,result=None)
            return observed
    with fixture(tmp_path,count=1,frames=32000) as (args,run,*_):
        export.run(**run,output_dir=tmp_path/'export'); kw=arguments(tmp_path,args)
        kw.update(_controller=Failed, selected_ranges=[dict(start_frame=0,end_frame=16000),dict(start_frame=16000,end_frame=32000)])
        Failed.calls=0
        with pytest.raises(Exception): candidate.run(**kw)
        assert Failed.calls==1
        assert (tmp_path/'candidates/call-00.intent.json').exists()
        assert (tmp_path/'candidates/failure.json').exists()
        assert not (tmp_path/'candidates/complete.json').exists()
        with pytest.raises(Exception): candidate.run(**kw)
        assert Failed.calls==1

def test_cancellation_before_work_has_no_effect(tmp_path):
    import threading
    event=threading.Event();event.set()
    with pytest.raises(Exception): candidate.run(job_dir=tmp_path/'absent',export_dir=tmp_path/'absent2',
        selected_ranges=[],model='/fixed',output_dir=tmp_path/'output',exclusive_inputs=True,
        aggregate_budget_managed=True,cancel=event,_controller=Fake)
    assert not (tmp_path/'output').exists()

def test_tracks_keep_empty_labels_and_invalid_timing_text():
    raw=b'[0][S1]hello[99][0][S2][1][2][S1]again[3]'
    tracks,errors=candidate.voice_tracks(raw,'scope')
    assert tracks==[dict(channel='S1',text='hello again',scope_id='scope'),
                    dict(channel='S2',text='',scope_id='scope')]
    assert errors==[]
    tracks,errors=candidate.voice_tracks(b'not parseable','scope')
    assert tracks==[dict(channel='__unassigned__',text='not parseable',scope_id='scope')] and 'unparsed_text' in errors

def test_source_change_after_call_keeps_failure_not_completion(tmp_path):
    class Change(Fake):
        def run(self,job):
            observed=super().run(job)
            (tmp_path/'export/transcript.txt').write_text('changed')
            return observed
    with fixture(tmp_path,count=1,frames=32000) as (args,run,*_):
        export.run(**run,output_dir=tmp_path/'export');kw=arguments(tmp_path,args);kw['_controller']=Change
        with pytest.raises(Exception): candidate.run(**kw)
        assert not (tmp_path/'candidates/complete.json').exists()
        assert (tmp_path/'candidates/call-00.status.json').exists()

@pytest.mark.parametrize('raw,word', [(b'[oops][S1]hello[1.0]','hello'), (b'[-2][S1]hello[1]','hello'), (b'[0][S1]hello','hello')])
def test_malformed_timing_retains_voice(raw,word):
    tracks,errors=candidate.voice_tracks(raw,'one')
    assert next(t['text'] for t in tracks if t['channel']=='S1')==word
    assert errors

def test_interior_bracketed_text_not_pruned():
    tracks,_=candidate.voice_tracks(b'[0][S1]code [123] is important[1]','scope')
    assert tracks[0]['text']=='code [123] is important'

@pytest.mark.parametrize('change',['remove','wav','origin','producer'])
def test_export_content_binding_required(tmp_path,change):
    import hashlib
    from audio_backend import recording_uploader as u
    with fixture(tmp_path,count=1,frames=32000) as (args,run,*_):
        export.run(**run,output_dir=tmp_path/'export')
        path=tmp_path/'export/export.json'; data=json.loads(path.read_bytes())
        binding=data['completion']['source_content_binding']
        if change=='remove': del data['completion']['source_content_binding']
        elif change=='wav': binding['originals'][0]['delivery']['wav_sha256']='0'*64
        elif change=='origin': binding['target']['origin']='http://other.example:99'
        else: binding['originals'][0]['delivery']['capture_identity_sha256']='0'*64
        raw=u._json(data);path.write_bytes(raw)
        manifest_path=tmp_path/'export/complete.json';manifest=json.loads(manifest_path.read_bytes())
        manifest['artifacts']['export.json']=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        manifest_path.write_bytes(u._json(manifest));Fake.calls=0
        with pytest.raises(Exception): candidate.run(**arguments(tmp_path,args))
        assert Fake.calls==0 and not (tmp_path/'candidates').exists()

def test_cancel_during_submit_never_dispatches(tmp_path):
    import threading
    event=threading.Event()
    class CancelSubmit(Fake):
        def submit(self,support,pcm):
            result=super().submit(support,pcm);event.set();return result
    with fixture(tmp_path,count=1,frames=32000) as (args,run,*_):
        export.run(**run,output_dir=tmp_path/'export');kw=arguments(tmp_path,args)
        kw.update(cancel=event,_controller=CancelSubmit);CancelSubmit.calls=0
        with pytest.raises(Exception): candidate.run(**kw)
        assert CancelSubmit.calls==0 and not (tmp_path/'candidates/complete.json').exists()

def test_raw_label_spans_reconstruct_every_character():
    raw=b'[oops][S1]code[123][1][0][S2][1][S1]unfinished'
    rows=candidate.raw_label_spans(raw)
    assert ''.join(row['raw_text'] for row in rows).encode()==raw
    assert [row['channel'] for row in rows]==[None,'S1','S2','S1']

def test_selected_slice_crosses_primary_chunks(tmp_path):
    with fixture(tmp_path,count=1,frames=20*16000) as (args,run,*_):
        export.run(**run,output_dir=tmp_path/'export');kw=arguments(tmp_path,args)
        kw['selected_ranges']=[dict(start_frame=5*16000,end_frame=17*16000)]
        candidate.run(**kw)
        data=json.loads((tmp_path/'candidates/candidate-00.json').read_bytes())
        assert len(data['source_binding']['export_declared_primary_work'])==2
        assert data['source_binding']['native_core_attested'] is False

def test_selected_slice_crosses_original_seam(tmp_path):
    with fixture(tmp_path,count=3,close='STOP') as (args,run,*_):
        export.run(**run,output_dir=tmp_path/'export');kw=arguments(tmp_path,args)
        kw['selected_ranges']=[dict(start_frame=2,end_frame=10)]
        candidate.run(**kw)
        data=json.loads((tmp_path/'candidates/call-00.intent.json').read_bytes())
        assert len(data['source_binding']['input_mapping'])==3
        assert data['support']['end_frame']-data['support']['start_frame']==8

def test_packaged_manifest_identical_to_reviewed_runtime():
    from audio_backend import moss_td_cpp_assets as assets
    from audio_backend.moss_handy_profile import RUNTIME_SHA256
    from pathlib import Path
    packaged=Path(candidate.__file__).parent/'profiles/moss-td-cpp-model-manifest.json'
    assert assets.sha256(packaged)==RUNTIME_SHA256==assets.sha256(assets.MANIFEST_PATH)

@pytest.mark.parametrize('kind',['absent','dangling','invalid','denied'])
def test_manifest_fallback_only_genuine_absence(tmp_path,monkeypatch,kind):
    from pathlib import Path
    from audio_backend import moss_td_cpp_assets as assets
    source=Path(assets.__file__).read_text().split('def manifest():')[0]
    root=tmp_path/'layout';package=root/'audio_backend';package.mkdir(parents=True)
    original=root/'moss-td-cpp-model-manifest.json'
    if kind=='dangling': original.symlink_to(root/'missing')
    if kind=='invalid': original.write_text('not JSON')
    if kind=='denied':
        real=Path.lstat
        def denied(path,*a,**kw):
            if path==original: raise PermissionError('denied test')
            return real(path,*a,**kw)
        monkeypatch.setattr(Path,'lstat',denied)
    namespace={'__file__':str(package/'moss_td_cpp_assets.py')}
    if kind=='denied':
        with pytest.raises(PermissionError): exec(source,namespace)
    else:
        exec(source,namespace)
        expected=package/'profiles/moss-td-cpp-model-manifest.json' if kind=='absent' else original
        assert namespace['MANIFEST_PATH']==expected
