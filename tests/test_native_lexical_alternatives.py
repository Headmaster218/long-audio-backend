"""Model-free standard-library tests for the isolated untimed support contract."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import unittest

from audio_backend.native_lexical_alternatives import (
    Alternative, ContractError, SourceSupport, bundle, MAX_RAW_BYTES, MAX_TEXT_BYTES,
    MAX_FRAME, MAX_SUPPORT_FRAMES, MAX_ALTERNATIVE_BYTES, MAX_BUNDLE_BYTES,
)


def support(**changes):
    values = dict(source_generation='1'*64, plan_sha256='2'*64,
        native_runtime_sha256='3'*64, core_sha256='4'*64, source_frames=400000,
        core_start_frame=0, core_end_frame=400000, start_frame=80000, end_frame=240000,
        pcm16le_sha256='5'*64, float32le_sha256='6'*64, sample_rate=16000)
    values.update(changes)
    return SourceSupport(**values)


def alternative(**changes):
    values = dict(support=support(), provider='qwen', model_sha256='7'*64,
        runtime_sha256='8'*64, config_sha256='9'*64, text='原始 Text。', outcome='returned',
        native_status=0, eos=True, truncated=False, raw=b'{"synthetic":true}')
    values.update(changes)
    return Alternative(**values)


class SupportTests(unittest.TestCase):
    def test_valid_roundtrip(self):
        s = support()
        self.assertEqual(SourceSupport.from_dict(s.to_dict()), s)
        self.assertEqual(s.support_id, hashlib.sha256(s.canonical_bytes()).hexdigest())

    def test_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            support().start_frame = 0

    def test_frame_types(self):
        for key in ('source_frames', 'core_start_frame', 'core_end_frame', 'start_frame', 'end_frame', 'sample_rate'):
            for value in (True, 1.0, '1', None, float('nan'), float('inf')):
                with self.subTest(key=key, value=value), self.assertRaises(ContractError):
                    support(**{key: value})

    def test_frame_range(self):
        for change in ({'start_frame': -1}, {'source_frames': MAX_FRAME+1},
                       {'end_frame': 80000}, {'end_frame': 79999}, {'sample_rate': 8000},
                       {'core_start_frame': 80001}, {'core_end_frame': 239999},
                       {'source_frames': 399999}):
            with self.subTest(change=change), self.assertRaises(ContractError): support(**change)

    def test_full_20_second_support(self):
        self.assertEqual(support(start_frame=0, end_frame=MAX_SUPPORT_FRAMES).end_frame, 320000)
        with self.assertRaises(ContractError): support(start_frame=0, end_frame=MAX_SUPPORT_FRAMES+1)

    def test_true_eof_and_one_frame(self):
        s = support(source_frames=183200, core_end_frame=183200, start_frame=183199, end_frame=183200)
        self.assertEqual(s.end_frame, s.source_frames)
        with self.assertRaises(ContractError): replace(s, end_frame=183201)

    def test_max_exact_integer_geometry(self):
        s = support(source_frames=MAX_FRAME, core_start_frame=MAX_FRAME-1,
                    core_end_frame=MAX_FRAME, start_frame=MAX_FRAME-1, end_frame=MAX_FRAME)
        self.assertEqual(json.loads(s.canonical_bytes())['end_frame'], MAX_FRAME)

    def test_hash_types(self):
        for name in ('source_generation', 'plan_sha256', 'native_runtime_sha256', 'core_sha256',
                     'pcm16le_sha256', 'float32le_sha256'):
            for value in ('a'*63, 'A'*64, 'g'*64, 'a'*64+'\n', None, False):
                with self.subTest(name=name, value=value), self.assertRaises(ContractError): support(**{name: value})

    def test_every_support_binding_affects_identity(self):
        s = support()
        for field in ('source_generation', 'plan_sha256', 'native_runtime_sha256', 'core_sha256',
                      'pcm16le_sha256', 'float32le_sha256'):
            self.assertNotEqual(s.support_id, replace(s, **{field: 'a'*64}).support_id)
        self.assertNotEqual(s.support_id, replace(s, start_frame=s.start_frame+1).support_id)

    def test_exact_fields_and_no_fabricated_identity(self):
        value = support().to_dict()
        for key in ('speaker_id', 'word_times', 'stored_path', 'support_id'):
            with self.subTest(key=key), self.assertRaises(ContractError): SourceSupport.from_dict(dict(value, **{key:'x'}))
        value.pop('source_generation')
        with self.assertRaises(ContractError): SourceSupport.from_dict(value)

    def test_mapping_must_be_plain_dict(self):
        class Mapping(dict): pass
        with self.assertRaises(ContractError): SourceSupport.from_dict(Mapping(support().to_dict()))


class AlternativeTests(unittest.TestCase):
    def test_valid_roundtrip_and_original_text(self):
        a = alternative(text='  原始\n Text。  ')
        self.assertTrue(a.complete_content)
        self.assertEqual(Alternative.from_dict(a.to_dict(), support=a.support, raw=a.raw), a)
        self.assertEqual(a.to_dict()['text'], '  原始\n Text。  ')

    def test_immutable(self):
        with self.assertRaises(FrozenInstanceError): alternative().text = 'replacement'

    def test_unknown_failure_and_partial_are_diagnostic(self):
        cases = [dict(native_status=18), dict(native_status=1), dict(native_status=-9),
            dict(outcome='failed'), dict(outcome='unknown', native_status=None),
            dict(native_status=None), dict(eos=False), dict(eos=None), dict(truncated=True),
            dict(truncated=None), dict(text=''), dict(text=' \t\n'), dict(raw=b'')]
        for changes in cases:
            with self.subTest(changes=changes):
                a = alternative(**changes)
                self.assertFalse(a.complete_content)
                self.assertEqual(Alternative.from_dict(a.to_dict(), support=a.support, raw=a.raw), a)

    def test_native18_cannot_be_promoted(self):
        a = alternative(native_status=18)
        data = dict(a.to_dict(), complete_content=True)
        with self.assertRaises(ContractError): Alternative.from_dict(data, support=a.support, raw=a.raw)

    def test_strict_native_status(self):
        for value in (False, True, 0.0, '0', 1<<31, -(1<<31)-1):
            with self.subTest(value=value), self.assertRaises(ContractError): alternative(native_status=value)
        with self.assertRaises(ContractError): alternative(outcome='unknown', native_status=0)

    def test_strict_booleans(self):
        for name in ('eos','truncated'):
            for value in (0,1,'true',[],{}):
                with self.subTest(name=name,value=value), self.assertRaises(ContractError): alternative(**{name:value})

    def test_provider_and_outcome(self):
        self.assertTrue(alternative(provider='moss').complete_content)
        for value in ('Qwen','other',None,True):
            with self.subTest(value=value), self.assertRaises(ContractError): alternative(provider=value)
        for value in ('complete','partial','',None):
            with self.subTest(value=value), self.assertRaises(ContractError): alternative(outcome=value)

    def test_profile_hashes(self):
        for name in ('model_sha256','runtime_sha256','config_sha256'):
            with self.subTest(name=name), self.assertRaises(ContractError): alternative(**{name:'bad'})

    def test_bounded_raw_type(self):
        self.assertEqual(len(alternative(raw=b'x'*MAX_RAW_BYTES).raw), MAX_RAW_BYTES)
        for value in (b'x'*(MAX_RAW_BYTES+1), bytearray(b'x'), 'x', None):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ContractError): alternative(raw=value)

    def test_text_utf8_boundary(self):
        self.assertEqual(len(alternative(text='a'*MAX_TEXT_BYTES).text), MAX_TEXT_BYTES)
        self.assertEqual(len(alternative(text='中'*(MAX_TEXT_BYTES//3)).text.encode()), 65535)
        for value in ('a'*(MAX_TEXT_BYTES+1), '中'*(MAX_TEXT_BYTES//3+1), '\ud800', None, []):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ContractError): alternative(text=value)

    def test_control_escape_projection_budget(self):
        with self.assertRaises(ContractError): alternative(text='\x00'*MAX_TEXT_BYTES)
        self.assertLessEqual(len(alternative(text='\x00'*100).canonical_bytes()), MAX_ALTERNATIVE_BYTES)

    def test_exact_projection_byte_boundary(self):
        overhead = len(alternative(text='X').canonical_bytes()) - 1
        escaped_bytes = MAX_ALTERNATIVE_BYTES - overhead
        count, remainder = divmod(escaped_bytes, 6)
        value = '\x00'*count + 'a'*remainder
        self.assertEqual(len(alternative(text=value).canonical_bytes()), MAX_ALTERNATIVE_BYTES)
        with self.assertRaises(ContractError): alternative(text=value+'a')

    def test_raw_material_mismatch(self):
        a = alternative()
        for changes in ({'raw_sha256':'a'*64}, {'raw_bytes':len(a.raw)+1}, {'pcm16le_sha256':'a'*64},
                        {'float32le_sha256':'a'*64}, {'support_id':'a'*64}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                Alternative.from_dict(dict(a.to_dict(), **changes), support=a.support, raw=a.raw)
        with self.assertRaises(ContractError): Alternative.from_dict(a.to_dict(), support=a.support, raw=b'other')

    def test_wrong_expected_source_or_provenance(self):
        a = alternative()
        for name in ('source_generation','plan_sha256','native_runtime_sha256','core_sha256','pcm16le_sha256','float32le_sha256'):
            with self.subTest(name=name), self.assertRaises(ContractError):
                Alternative.from_dict(a.to_dict(), support=replace(a.support, **{name:'a'*64}), raw=a.raw)

    def test_timing_and_identity_rejection(self):
        a = alternative()
        for changes in ({'word_times':[]}, {'global_speaker_id':'spk1'}, {'speaker_identity_valid':True},
                        {'profile_update_allowed':True}, {'timing_scope':'word'}, {'schema':'timed-v1'}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                Alternative.from_dict(dict(a.to_dict(), **changes), support=a.support, raw=a.raw)
        self.assertIsNone(a.to_dict()['word_times'])
        self.assertIsNone(a.to_dict()['global_speaker_id'])

    def test_projection_numeric_bool_substitutions(self):
        a = alternative(raw=b'x')
        for changes in ({'raw_bytes':True},{'complete_content':1},{'speaker_identity_valid':0},
                        {'profile_update_allowed':0}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                Alternative.from_dict(dict(a.to_dict(), **changes), support=a.support, raw=a.raw)

    def test_projection_exact_field_set(self):
        a = alternative(); value = a.to_dict()
        with self.assertRaises(ContractError): Alternative.from_dict(dict(value, words=[]), support=a.support, raw=a.raw)
        del value['outcome']
        with self.assertRaises(ContractError): Alternative.from_dict(value, support=a.support, raw=a.raw)

    def test_projection_large_opaque_values_rejected_before_serialization(self):
        a = alternative()
        for changes in ({'schema':'x'*1000000},{'word_times':[0]*100000},{'support_id':{'nested':[]}}):
            with self.subTest(key=next(iter(changes))), self.assertRaises(ContractError):
                Alternative.from_dict(dict(a.to_dict(), **changes), support=a.support, raw=a.raw)

    def test_unvalidated_support_rejected(self):
        with self.assertRaises(ContractError): alternative(support={})


class BundleTests(unittest.TestCase):
    def test_distinct_providers_share_one_support(self):
        q = alternative(); m = replace(q, provider='moss')
        value = json.loads(bundle(q.support, [m,q]))
        self.assertEqual([x['provider'] for x in value['alternatives']], ['moss','qwen'])
        self.assertEqual(value['support'], q.support.to_dict())

    def test_distinct_profiles_allowed(self):
        a = alternative(); b = replace(a, model_sha256='a'*64)
        self.assertEqual(len(json.loads(bundle(a.support, [a,b]))['alternatives']), 2)

    def test_duplicate_provider_profile_rejected(self):
        a = alternative()
        with self.assertRaises(ContractError): bundle(a.support, [a,replace(a,text='other')])

    def test_mixed_support_rejected(self):
        a = alternative(); b = replace(a, support=replace(a.support, start_frame=80001))
        with self.assertRaises(ContractError): bundle(a.support, [a,b])

    def test_count_and_finite_type(self):
        a = alternative()
        with self.assertRaises(ContractError): bundle(a.support, [a]*9)
        with self.assertRaises(ContractError): bundle(a.support, iter([a]))
        with self.assertRaises(ContractError): bundle(a.support, [{}])
        self.assertEqual(json.loads(bundle(a.support, []))['alternatives'], [])

    def test_aggregate_byte_budget(self):
        a = alternative(text='x'*MAX_TEXT_BYTES)
        cases = [replace(a, config_sha256=f'{n:064x}') for n in range(4)]
        self.assertLessEqual(len(bundle(a.support, cases[:3])), MAX_BUNDLE_BYTES)
        with self.assertRaises(ContractError): bundle(a.support, cases)

    def test_diagnostics_not_dropped(self):
        a = alternative(native_status=18)
        result = json.loads(bundle(a.support, [a]))
        self.assertEqual(result['alternatives'][0]['native_status'], 18)
        self.assertFalse(result['alternatives'][0]['complete_content'])

    def test_bundle_is_canonical_json(self):
        a = alternative()
        value = bundle(a.support, [a])
        self.assertEqual(value, json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True, separators=(',',':')).encode())


if __name__ == '__main__':
    unittest.main()
