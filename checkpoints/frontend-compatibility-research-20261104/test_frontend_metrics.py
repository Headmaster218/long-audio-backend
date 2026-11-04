"""Model-free, synthetic-vector checks. No encoder/library or audio loading."""
import json
import math
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
import frontend_experiment as h
ROOT = Path(__file__).resolve().parent.parent
SOURCE=Path(os.environ.get('BACKEND_SOURCE',str(ROOT / 'backend-resume-20261104/checkpoints/backend-capacity-source-20261031/source')))
SAMPLES=Path(os.environ.get('FRONTEND_BOUND_SAMPLES',str(ROOT/'backend-frontend-samples-20261104/bound_sample_manifest.json')))
sys.path.insert(0,str(SOURCE))
from audio_backend.support_vectors import normalized_vector
from audio_backend.verified_identity import centroid_score

class Metrics(unittest.TestCase):
    def fixture(self):
        truth = json.loads(SAMPLES.read_text())
        gallery = [{'label':c['expected_gallery_label'],'ordinal':c['ordinal']} for c in truth['crops'] if c['role']=='reference' and c['role_slot']==1]
        refs = [{'label':c['expected_gallery_label'],'ordinal':c['ordinal']} for c in truth['crops'] if c['role']=='reference']
        queries = [c['ordinal'] for c in truth['crops'] if c['role']=='query']
        vectors = np.zeros((36,512), dtype=np.float32)
        labels = {g['label']:i for i,g in enumerate(gallery)}
        for c in truth['crops']:
            vectors[c['ordinal']-1, labels.get(c['expected_gallery_label'], 7)] = 1
        return truth, {'gallery':gallery,'queries':queries}, refs, vectors

    def summary(self, vectors, edits=None):
        truth,predictor,refs,_ = self.fixture()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); tp=root/'truth.json'; h.write_json(tp,truth)
            frozen={'evaluation_truth':h.pinned(tp),'predictor':predictor,'diagnostic_gallery':refs}
            pins=[]
            for stage in ('native','candidate'):
                d=root/stage; d.mkdir(); rows=h.predict(predictor,vectors)
                if edits: edits(rows)
                h.write_json(d/'predictions.json',rows)
                np.savez(d/'vectors.npz', normalized=vectors)
                pins.extend([h.pinned(d/'predictions.json'),h.pinned(d/'vectors.npz')])
            h.write_json(root/'PREDICTION_SEAL.json',{'pins':pins})
            h.summarize(frozen,root)
            return h.read_json(root/'SUMMARY.json')

    def test_product_matcher_exact(self):
        rng=random.Random(20261104)
        for scale in (1e-20,1.0,1e20):
            for _ in range(12):
                a=[rng.uniform(-1,1)*scale for _ in range(512)]
                b=[rng.uniform(-1,1)*scale for _ in range(512)]
                na,_=normalized_vector(a); nb,_=normalized_vector(b)
                self.assertEqual(h.normal64(a),na)
                self.assertEqual(h.match_score(a,b),centroid_score([na],nb))

    def test_invalid_vectors_fail(self):
        for x in ([0.0]*512,[float('nan')]*512,[float('inf')]*512,[1.0]*511):
            with self.assertRaises(ValueError): h.normal64(x)

    def test_predictor_truth_rejected(self):
        _,p,_,v=self.fixture()
        for key in ('truth','labels','expected_gallery_label'):
            with self.assertRaises(ValueError): h.predict(dict(p,**{key:[]}),v)

    def test_complete_known_and_withheld(self):
        _,p,_,v=self.fixture(); rows=h.predict(p,v)
        self.assertEqual(len(rows),24)
        self.assertEqual(sum(len(r['scores']) for r in rows),144)
        out=self.summary(v)
        for arm in out['arms'].values():
            c=arm['counts']; self.assertEqual(c['known_correct'],18)
            self.assertEqual(c['known_wrong'],0);self.assertEqual(c['known_abstained'],0)
            self.assertEqual(c['unknown_false_accept'],0);self.assertEqual(c['unknown_abstained'],6)
            self.assertEqual(c['emission_precision'],1);self.assertEqual(c['coverage'],.75)
            pairs=arm['diagnostic_pairs'];self.assertEqual(len(pairs),288)
            self.assertEqual(sum(x['same'] for x in pairs),36)
            primary=[x for x in pairs if x['primary']]
            self.assertEqual(len(primary),144);self.assertEqual(sum(x['same'] for x in primary),18)
            unknown=set(p['queries'][-6:])
            self.assertEqual(sum(x['query_ordinal'] in unknown for x in pairs),72)
            self.assertEqual(sum(x['query_ordinal'] in unknown for x in primary),36)

    def test_zero_emission_precision_undefined(self):
        _,p,_,v=self.fixture()
        for ordinal in p['queries']: v[ordinal-1]=0;v[ordinal-1,8]=1
        c=self.summary(v)['arms']['native']['counts']
        self.assertEqual(c['known_abstained'],18);self.assertEqual(c['unknown_abstained'],6)
        self.assertEqual(c['emissions'],0);self.assertIsNone(c['emission_precision'])

    def test_wrong_and_false_accept_denominators(self):
        _,p,_,v=self.fixture()
        v[p['queries'][0]-1]=0;v[p['queries'][0]-1,1]=1
        v[p['queries'][-1]-1]=0;v[p['queries'][-1]-1,0]=1
        c=self.summary(v)['arms']['native']['counts']
        self.assertEqual(c['known_correct'],17);self.assertEqual(c['known_wrong'],1)
        self.assertEqual(c['unknown_false_accept'],1);self.assertEqual(c['unknown_abstained'],5)
        self.assertEqual(c['emissions'],19);self.assertEqual(c['emission_precision'],17/19)

    def test_summary_rejects_missing_query(self):
        _,_,_,v=self.fixture()
        with self.assertRaises(ValueError): self.summary(v,lambda rows: rows.pop())

    def test_summary_rejects_duplicate_query(self):
        _,_,_,v=self.fixture()
        def edit(rows): rows[-1]=rows[0]
        with self.assertRaises(ValueError): self.summary(v,edit)

    def test_summary_rejects_missing_rival(self):
        _,_,_,v=self.fixture()
        with self.assertRaises(ValueError): self.summary(v,lambda rows: rows[0]['scores'].pop())

    def test_ambiguous_tie_refused(self):
        _,p,_,v=self.fixture()
        for g in p['gallery']: v[g['ordinal']-1]=0;v[g['ordinal']-1,0]=1
        for q in p['queries']: v[q-1]=0;v[q-1,0]=1
        self.assertTrue(all(r['status']=='unknown_ambiguous' and r['emitted'] is None for r in h.predict(p,v)))

if __name__=='__main__': unittest.main(verbosity=2)
