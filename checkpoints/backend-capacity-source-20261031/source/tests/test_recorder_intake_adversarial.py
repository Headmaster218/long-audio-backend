"""Independent negative review of explicit intake; real admission, no native work."""
import copy
import fcntl
import json
import os
import pytest
from audio_backend import recorder_intake as intake
from audio_backend import recording_uploader as u
from test_recorder_intake import fixture
from test_recording_session_contract import handler_app, session
from test_strict_uploader_admission import StrictRoutes, AdmissionBoundary

class ChangedRoutes(StrictRoutes):
    change = None
    def request(self, method, path, **kwargs):
        answer = super().request(method,path,**kwargs)
        if self.change and method == 'GET':
            answer = copy.deepcopy(answer)
            if path.endswith('/recordings/explicit-capture'):
                if self.change == 'extra_page': answer['page_count'] += 1
                if self.change == 'eos': answer['eos'] = True
                if self.change == 'origin': answer['capture_origin_frame'] += 1
            elif path.endswith('/recordings/explicit-capture/pages/1'):
                if self.change == 'source_format': answer['sources'][0]['format'] = 'different'
                if self.change == 'source_key': answer['sources'][0]['idempotency_key'] = 'foreign'
                if self.change == 'source_id': answer['sources'][0]['id'] = 'foreign'
                if self.change == 'page_id': answer['id'] = 'foreign'
                if self.change == 'source_duplicate': answer['sources'].append(copy.deepcopy(answer['sources'][0]))
        return answer

@pytest.mark.parametrize('change',['extra_page','eos','origin','source_format','source_key','source_id','page_id','source_duplicate'])
def test_changed_remote_never_writes(tmp_path,change):
    args=fixture(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=ChangedRoutes(client);args.update(session_id=session(client),transport=routes)
        with pytest.raises(AdmissionBoundary):intake.run(**args)
        path=args['job_dir']/'uploader/uploader.json'
        original=json.loads(path.read_text())['pages']
        before=len(routes.calls);routes.change=change
        with pytest.raises((intake.Refused,u.Incomplete)):intake.run(**args,resume=True)
        assert all(method=='GET' for method,_,_ in routes.calls[before:])
        assert json.loads(path.read_text())['pages']==original


def test_busy_job_no_network(tmp_path):
    args=fixture(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=ChangedRoutes(client);args.update(session_id=session(client),transport=routes)
        with pytest.raises(AdmissionBoundary):intake.run(**args)
        with (args['job_dir']/'intake.lock').open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            before=len(routes.calls)
            with pytest.raises(BlockingIOError):intake.run(**args,resume=True)
            assert len(routes.calls)==before


@pytest.mark.parametrize('url',['http://example.org','https://user:secret@example.org','https://example.org/path','https://example.org?q=x','https://example.org#frag'])
def test_bad_origin_no_local_job(tmp_path,url):
    args=fixture(tmp_path);args['api']=url
    with pytest.raises(intake.Refused):intake.run(**args)
    assert not args['job_dir'].exists()


@pytest.mark.parametrize('file',['intake.lock','manifest.json','uploader/uploader.json'])
def test_control_hardlink_refused(tmp_path,file):
    args=fixture(tmp_path)
    with handler_app(tmp_path/'server') as (client,app):
        routes=ChangedRoutes(client);args.update(session_id=session(client),transport=routes)
        with pytest.raises(AdmissionBoundary):intake.run(**args)
        original=args['job_dir']/file;os.link(original,tmp_path/'linked')
        before=len(routes.calls)
        with pytest.raises(intake.Refused,match='unsafe_control'):intake.run(**args,resume=True)
        assert len(routes.calls)==before
