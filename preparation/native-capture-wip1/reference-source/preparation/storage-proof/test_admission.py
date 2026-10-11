"""Pure finite-producer negative fixtures, prepared without actual model input."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('finite_admission',HERE/'admission.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
SHA='a'*64

def result():
    return {'segments':[{'start':0.,'end':1.,'text':'synthetic','raw_text':'synthetic','asr_backend':a.BACKEND,'timestamp_kind':a.TIMING,'speaker_local_id':'native0'}], 'events':[],
        'speaker_turns':[{'start':0.,'end':1.,'speaker_local_id':'native0','speaker_embedding':[1.]*8}],
        'metadata':{'asr_backend':a.BACKEND,'timestamp_semantics':a.TIMING,'speaker_embedding_sha256':SHA},'overlap_candidates':None}

def gate(value):return a.admit(value,native_labels=frozenset({'native0'}),expected_speaker_sha256=SHA)

class Admission(unittest.TestCase):
    def test_accepted_object_unchanged(self):
        value=result();before=copy.deepcopy(value);proof=gate(value);self.assertEqual(value,before);self.assertEqual(proof['turns'],1)
    def test_wire_hash_exact(self):
        value=result();raw=json.dumps(value).encode();proof=a.admit_raw(raw,native_labels=frozenset({'native0'}),expected_speaker_sha256=SHA)
        self.assertEqual(proof['raw_wire_sha256'],hashlib.sha256(raw).hexdigest())
    def test_raw_before_parse_bound(self):
        with self.assertRaises(a.Rejected):a.admit_raw(b' '* (a.MAX_ENVELOPE+1),native_labels=frozenset(),expected_speaker_sha256=SHA)
    def test_nonfinite_raw(self):
        for raw in [b'{"x":NaN}',b'{"x":Infinity}',b'{"x":-Infinity}',b'\xff']:
            with self.assertRaises(a.Rejected):a.admit_raw(raw,native_labels=frozenset(),expected_speaker_sha256=SHA)
    def test_backend_and_timing(self):
        for key in ['asr_backend','timestamp_semantics']:
            value=result();value['metadata'][key]='wrong'
            with self.assertRaises(a.Rejected):gate(value)
    def test_segment_provenance(self):
        for key in ['asr_backend','timestamp_kind']:
            value=result();value['segments'][0][key]='wrong'
            with self.assertRaises(a.Rejected):gate(value)
    def test_model_hash(self):
        value=result();value['metadata']['speaker_embedding_sha256']='b'*64
        with self.assertRaises(a.Rejected):gate(value)
    def test_metadata_byte_limit(self):
        value=result();value['metadata']['pad']='x'*a.MAX_METADATA
        with self.assertRaises(a.Rejected):gate(value)
    def test_dropped_native_label(self):
        value=result();value['speaker_turns']=[]
        with self.assertRaises(a.Rejected):gate(value)
    def test_added_native_label(self):
        value=result();value['speaker_turns'][0]['speaker_local_id']='other'
        with self.assertRaises(a.Rejected):gate(value)
    def test_turn_limit(self):
        value=result();value['speaker_turns']*=33
        with self.assertRaises(a.Rejected):gate(value)
    def test_nine_native_labels(self):
        with self.assertRaises(a.Rejected):a.admit(result(),native_labels=frozenset(str(i) for i in range(9)),expected_speaker_sha256=SHA)
    def test_exact_native_frames(self):
        for frames in [True,307040.0,307039,307120]:
            with self.assertRaises(a.Rejected):a.admit_native([],frames=frames)
    def test_native_interval(self):
        for start,end in [(False,1.),(0.,float('nan')),(-.1,1.),(0.,19.195),(1.,1.)]:
            with self.assertRaises(a.Rejected):a.admit_native([{'start':start,'end':end,'speaker_local_id':'native0'}])
    def test_native_boundary(self):
        self.assertEqual(a.admit_native([{'start':0.,'end':19.19,'speaker_local_id':'native0'}]),frozenset({'native0'}))
    def test_invalid_vectors(self):
        for vector in [[True]*8,[float('inf')]*8,[1.]*7,[1.]*2049]:
            value=result();value['speaker_turns'][0]['speaker_embedding']=vector
            with self.assertRaises(a.Rejected):gate(value)
    def test_forbidden_lexical_candidate(self):
        value=result();value['overlap_candidates']={}
        with self.assertRaises(a.Rejected):gate(value)
    def test_error_envelope(self):
        value=result();value['_error']='synthetic failure'
        with self.assertRaises(a.Rejected):gate(value)
    def test_strict_label_collection(self):
        with self.assertRaises(a.Rejected):a.admit(result(),native_labels=['native0'],expected_speaker_sha256=SHA)
