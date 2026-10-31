"""Raw-first and single-invocation fixtures; all APIs are injected Python stubs."""
from array import array
import hashlib
from types import SimpleNamespace
import unittest

from audio_backend.moss_handy_adapter import MAX_DIAGNOSTIC_BYTES, MAX_RESULT_BYTES, encoded, project, retain_raw
from audio_backend.moss_handy_profile import NAME
from audio_backend.moss_handy_runner import context_frames, one_call
from audio_backend.native_lexical_alternatives import SourceSupport


def support():
    return SourceSupport('1'*64, '2'*64, '3'*64, '4'*64, 320000, 0, 320000, 0, 320000, '5'*64, '6'*64)


class Partial(Exception):
    def __init__(self, result): self.partial_result = result


class Truncated(Partial): pass
class Aborted(Partial): pass


def stub(failure=None, raw='[0][S1]原文[1]'):
    events, counts = [], {'model': 0, 'session': 0, 'run': 0}
    class Session:
        limits = SimpleNamespace(effective_n_ctx=context_frames(16000), effective_max_audio_ms=20000)
        def run(self, samples, **kwargs):
            counts['run'] += 1; events.append('run')
            if failure == 'run': raise RuntimeError('synthetic')
            native = SimpleNamespace(raw_text=raw)
            if failure == 'truncated': raise Truncated(native)
            if failure == 'aborted': raise Aborted(native)
            return native
        def close(self):
            events.append('session.close')
            if failure == 'session_close': raise RuntimeError('synthetic')
    class Model:
        device = SimpleNamespace(kind='cpu'); backend='cpu'; arch='moss'; variant='moss-transcribe-diarize'
        def __init__(self, path, **kwargs):
            counts['model'] += 1
            if failure == 'model': raise RuntimeError('synthetic')
        def session(self, **kwargs):
            counts['session'] += 1
            if failure == 'session': raise RuntimeError('synthetic')
            result = Session()
            if failure == 'limits': result.limits = SimpleNamespace(effective_n_ctx=1, effective_max_audio_ms=20000)
            return result
        def close(self):
            events.append('model.close')
            if failure == 'model_close': raise RuntimeError('synthetic')
    return SimpleNamespace(Model=Model, OutputTruncated=Truncated, Aborted=Aborted), events, counts


class AdapterTests(unittest.TestCase):
    def project(self, text, **kwargs):
        raw, reason = retain_raw(text); self.assertIsNone(reason)
        return project(raw, support=support(), profile_name=NAME, **kwargs)

    def test_unknown_eos_with_one_untimed_alternative(self):
        result = self.project('[4][S1]甲 [8][1][S2]乙[5][8][S1]丙[9]')
        alt = result['alternative']
        self.assertIsNone(alt['eos']); self.assertIsNone(alt['native_status'])
        self.assertFalse(alt['complete_content']); self.assertEqual(alt['text'], '甲 乙丙')
        self.assertEqual(len(result['diagnostic_phrases']), 3)
        self.assertEqual([row['start'] for row in result['diagnostic_phrases']], [4, 1, 8])
        self.assertFalse(result['speaker_identity_valid'])

    def test_exact_unicode_raw_without_normalization(self):
        text = '  [0][S01]原文\n Text。[1]  '
        raw, reason = retain_raw(text)
        self.assertEqual(raw, text.encode()); self.assertIsNone(reason)
        result = self.project(text)
        self.assertEqual(result['raw_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['alternative']['text'], '原文\n Text。')

    def test_raw_caps_and_invalid_unicode(self):
        self.assertEqual(len(retain_raw('x'*131072)[0]), 131072)
        for value in ('x'*131073, '中'*43691, '\ud800', None):
            with self.subTest(value_type=type(value).__name__):
                raw, reason = retain_raw(value); self.assertIsNone(raw); self.assertTrue(reason)

    def test_partial_flags_do_not_complete(self):
        for flags in ({'truncated': True}, {'aborted': True}, {'outcome': 'failed'}, {'outcome': 'unknown'}):
            result = self.project('[0][S1]a[1]', **flags)
            self.assertIsNone(result['eos']); self.assertFalse(result['alternative']['complete_content'])

    def test_invalid_timestamp_keeps_untimed_evidence(self):
        result = self.project('[0][S1]a[21]')
        self.assertEqual(result['diagnostic_phrases'], [])
        self.assertEqual(result['alternative']['text'], 'a')
        self.assertIn('invalid_model_timestamps', result['schema_errors'])

    def test_malformed_envelope_reported(self):
        raw = 'prefix [0][S1]a[1] suffix'
        result = self.project(raw)
        self.assertIn('unparsed_text', result['schema_errors'])
        self.assertFalse(result['complete_content'])
        self.assertEqual(result['alternative']['text'],'a')
        self.assertFalse(result['alternative']['complete_content'])
        self.assertEqual(result['raw_sha256'],hashlib.sha256(raw.encode()).hexdigest())

    def test_eight_anonymous_tracks_only(self):
        result = self.project(''.join(f'[0][S{i}]x[1]' for i in range(9)))
        self.assertEqual(len(result['diagnostic_phrases']), 8)
        self.assertIn('speaker_capacity_exceeded', result['schema_errors'])

    def test_phrase_cap(self):
        self.assertIn('phrase_limit', self.project('[0][S1]x[1]'*257)['schema_errors'])

    def test_text_and_projection_caps(self):
        for text in ('a'*65537, '\x00'*50000, '中'*30000):
            result = self.project('[0][S1]'+text+'[1]')
            self.assertLessEqual(len(encoded(result)), MAX_RESULT_BYTES)
            self.assertLessEqual(len(encoded(result['diagnostic_phrases'])), MAX_DIAGNOSTIC_BYTES)
            self.assertIsNone(result['alternative'])

    def test_invalid_raw_utf8(self):
        result = project(b'\xff', support=support(), profile_name=NAME)
        self.assertEqual(result['schema_errors'], ['invalid_raw_utf8'])


class CallTests(unittest.TestCase):
    def call(self, failure=None, raw='[0][S1]a[1]'):
        api, events, counts = stub(failure, raw)
        retained = []
        def retain(value): events.append('retain'); retained.append(value)
        result = one_call(api, 'synthetic-model', array('f', [0.1])*16000, retain=retain, check=lambda: None)
        return result, events, counts, retained

    def test_raw_retained_before_close(self):
        result, events, counts, retained = self.call()
        self.assertEqual(events, ['run', 'retain', 'session.close', 'model.close'])
        self.assertEqual(counts, {'model':1, 'session':1, 'run':1})
        self.assertTrue(result['raw_retained']); self.assertIsNone(result['eos'])
        self.assertEqual(retained, [b'[0][S1]a[1]'])

    def test_no_retry_at_any_failure(self):
        for failure in ('model', 'session', 'limits', 'run', 'session_close', 'model_close'):
            with self.subTest(failure=failure):
                result, events, counts, retained = self.call(failure)
                self.assertTrue(result['error_type'] or result['close_errors'])
                self.assertLessEqual(counts['model'],1); self.assertLessEqual(counts['session'],1); self.assertLessEqual(counts['run'],1)

    def test_truncation_and_abort_retain_raw(self):
        for failure in ('truncated', 'aborted'):
            result, _, counts, retained = self.call(failure)
            self.assertTrue(result[failure]); self.assertEqual(counts['run'], 1)
            self.assertTrue(retained); self.assertIsNone(result['generated_token_count'])

    def test_oversize_is_unavailable(self):
        result, events, counts, retained = self.call(raw='x'*131073)
        self.assertEqual(retained, []); self.assertEqual(result['raw_unavailable_reason'], 'raw_text_oversize')
        self.assertEqual(counts['run'], 1); self.assertTrue(result['model_closed'])

    def test_retention_failure_does_not_retry(self):
        api, _, counts = stub()
        def fail(raw): raise OSError('synthetic retention error')
        result = one_call(api, 'synthetic-model', array('f', [0.1])*16000, retain=fail, check=lambda: None)
        self.assertEqual(result['error_type'], 'OSError'); self.assertEqual(counts['run'],1)
        self.assertFalse(result['raw_retained']); self.assertTrue(result['model_closed'])

    def test_bad_frames_fail_before_model(self):
        api, _, counts = stub()
        result = one_call(api, 'synthetic-model', [], retain=lambda raw: None, check=lambda: None)
        self.assertEqual(counts['model'],0); self.assertEqual(result['error_type'],'ValueError')


if __name__ == '__main__':
    unittest.main()
