"""Existing uploader to actual strict handlers, stopping BEFORE sealing/native work.

No lifespan, synthetic analysis, session creation retry, or publication is used.
This qualifies deterministic source admission only, not production readiness.
"""
import hashlib
import json
import wave

import pytest
from audio_backend import recording_uploader as u
from test_recording_session_contract import handler_app, session, HEADERS


class AdmissionBoundary(BaseException):
    pass


class StrictRoutes:
    def __init__(self, client, lose_put=False):
        self.client = client
        self.lose_put = lose_put
        self.calls = []
        self.puts = []

    def request(self, method, path, body=None, file=None, key=None):
        assert not (method == 'POST' and path == '/sessions')
        if path.endswith('/seal') or path.endswith('/finish'):
            raise AdmissionBoundary('Stop before seal/native work')
        headers = dict(HEADERS)
        kwargs = {}
        if body is not None:
            kwargs['json'] = body
        if file is not None:
            headers.update({'Content-Type': 'audio/wav', 'Idempotency-Key': key,
                            'Content-Length': str(file.stat().st_size)})
            digest = hashlib.sha256()
            def blocks():
                with file.open('rb') as source:
                    while block := source.read(u.READ_BYTES):
                        assert len(block) <= u.READ_BYTES
                        digest.update(block)
                        yield block
            kwargs['content'] = blocks()
        result = self.client.request(method, path, headers=headers, **kwargs)
        self.calls.append((method, path, result.status_code))
        if result.status_code >= 400:
            raise u.HTTPFailure(result.status_code, result.text, .001)
        if file is not None:
            self.puts.append((key, digest.hexdigest()))
            if self.lose_put:
                self.lose_put = False
                raise u.Uncertain('Synthetic response loss AFTER real strict commit')
        return result.json()


def source(path, frames):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
        remaining = frames
        while remaining:
            n = min(32768, remaining)
            out.writeframesraw(b'\x12\x34' * n)
            remaining -= n


@pytest.mark.parametrize('lose_put', [False, True])
def test_existing_uploader_pieces_resume_strict_admission(tmp_path, lose_put):
    original = tmp_path / 'original.wav'
    # Cross the real 120-second source boundary, inside one 294-second page.
    frames = u.SOURCE_FRAMES + 17
    source(original, frames)
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    with handler_app(tmp_path / 'server') as (client, app):
        sid = session(client)
        routes = StrictRoutes(client, lose_put)
        args = dict(input=original, session_id=sid, capture_id='known.capture',
                    capture_origin_frame=71, state_dir=tmp_path / 'journal', final=True,
                    limits=u.Limits(disk_floor=0, deadline_seconds=30, poll_seconds=.001),
                    transport=routes)
        with pytest.raises(AdmissionBoundary):
            u.Uploader(**args).run()
        saved = json.loads((tmp_path / 'journal/uploader.json').read_text())
        pieces = saved['pages'][0]['pieces']
        assert [p['frames'] for p in pieces] == [u.SOURCE_FRAMES, 17]
        assert [p['capture_start'] for p in pieces] == [71, 71 + u.SOURCE_FRAMES]
        assert not saved['complete'] and not saved['pages'][0]['released']
        assert len(routes.puts) == 2  # Lost success reconciled with GET, not another PUT.
        before = list(routes.puts)
        # A fresh uploader reconstructs exact bytes/keys from durable state.
        with pytest.raises(AdmissionBoundary):
            u.Uploader(**args).run()
        assert routes.puts == before
        after = json.loads((tmp_path / 'journal/uploader.json').read_text())
        assert after['pages'][0]['pieces'] == pieces
        page = client.get('/sessions/' + sid + '/recordings/known.capture/pages/1', headers=HEADERS).json()
        assert page['state'] == 'open' and not page['released']
        assert len(page['sources']) == 2
        assert [(s['idempotency_key'], s['digest']) for s in page['sources']] == before
        contract = client.get('/sessions/' + sid + '/recording-contract', headers=HEADERS).json()
        assert contract['strict_primary_policy'] == 'strict-primary-payload-v2'
        assert contract['readiness'] == 'not_assessed'
        assert not app.state.worker.thread.is_alive()
        with app.state.store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 1
            assert db.execute('SELECT COUNT(*) FROM recording_work').fetchone()[0] == 0
        assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
