import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
load = lambda name: json.loads((ROOT/name).read_text())

def intersects(a, b):
    return max(a['start_frame'],b['start_frame']) < min(a['end_frame'],b['end_frame'])

class RecoveryEvidenceTests(unittest.TestCase):
    def test_original_fail_unchanged(self):
        old = json.loads((ROOT.parent/'continuous/results.json').read_text())
        a = load('results.json')['posthoc_A']
        self.assertFalse(old['gate']['passes'])
        self.assertEqual(old['arms']['qwen']['overall']['errors'],1637)
        self.assertTrue(a['original_gate_remains_FAIL'])
        self.assertFalse(a['clean_first_pass_gate_eligible'])
        self.assertEqual(a['counterfactual_arms']['sensevoice']['overall'],old['arms']['sensevoice']['overall'])

    def test_all_count_summaries(self):
        r = load('results.json')
        groups = [(r['posthoc_A']['counterfactual_arms'],4182,{'sensevoice':(1360,220,1107,33),'qwen':(1272,206,1017,49)}),
                  (r['prospective_B']['first_pass'],4000,{'sensevoice':(1416,193,1204,19),'qwen':(1718,510,935,273)}),
                  (r['prospective_B']['recovered'],4000,{'sensevoice':(1416,193,1204,19),'qwen':(1370,207,1146,17)})]
        keys = ('errors','substitutions','deletions','insertions')
        for arms, denominator, expected in groups:
            for arm, a in arms.items():
                self.assertEqual(tuple(a['overall'][k] for k in keys),expected[arm])
                self.assertEqual(a['overall']['reference_characters'],denominator)
                for k in keys:
                    self.assertEqual(sum(x[k] for x in a['meetings'].values()),a['overall'][k])
                self.assertEqual(sum(x['reference_characters'] for x in a['meetings'].values()),denominator)
                self.assertAlmostEqual(a['overall']['cer'],a['overall']['errors']/denominator)
                for x in a['meetings'].values():
                    self.assertEqual(x['errors'],sum(x[k] for k in keys[1:]))
        for arms, expected in [(groups[0][0],6),(groups[2][0],4)]:
            self.assertEqual(sum(arms['qwen']['meetings'][m]['errors'] <= arms['sensevoice']['meetings'][m]['errors'] for m in arms['qwen']['meetings']),expected)
        self.assertFalse(r['prospective_B']['gate']['passes'])

    def test_prospective_support_disjointness_and_tiling(self):
        sel = load('selection.json')['prospective_B']
        prior = json.loads((ROOT.parent/'continuous/selection.json').read_text())['excerpts']
        oracle = json.loads((ROOT.parent/'selection.json').read_text())
        self.assertEqual(len(sel['excerpts']),8)
        self.assertEqual(sum(e['reference_characters'] for e in sel['excerpts']),4000)
        self.assertEqual(len(sel['windows']),40)
        self.assertEqual(sum(w['frames'] for w in sel['windows']),11138400)
        for e in sel['excerpts']:
            for x in prior:
                if e['meeting']==x['meeting']: self.assertFalse(intersects(e,x))
            for x in oracle:
                if e['meeting']==x['session']: self.assertFalse(intersects(e,x))
            ws = [w for w in sel['windows'] if w['meeting']==e['meeting']]
            self.assertEqual(ws[0]['start_frame'],e['start_frame'])
            self.assertEqual(ws[-1]['end_frame'],e['end_frame'])
            self.assertTrue(60 <= e['seconds'] <= 120)
            self.assertEqual(e['partial_outer_turns'],0)
            for i,w in enumerate(ws):
                self.assertEqual(w['index'],i)
                self.assertEqual(w['frames'],w['end_frame']-w['start_frame'])
                self.assertTrue(0 < w['frames'] <= 320000)
                if i:
                    self.assertEqual(ws[i-1]['end_frame'],w['start_frame'])
                    self.assertEqual(ws[i-1]['frames'],320000)

    def test_children_and_calls(self):
        sel = load('selection.json')['prospective_B']
        children = sel['potential_children']
        self.assertEqual(len(children),64)
        self.assertEqual(sum(c['attempted'] for c in children),4)
        self.assertEqual(sum(w['frames']==320000 for w in sel['windows']),32)
        for w in sel['windows']:
            cs = [c for c in children if c['meeting']==w['meeting'] and c['parent_window_index']==w['index']]
            self.assertEqual(len(cs),2 if w['frames']==320000 else 0)
            if cs:
                self.assertEqual(cs[0]['start_frame'],w['start_frame'])
                self.assertEqual(cs[0]['end_frame'],cs[1]['start_frame'])
                self.assertEqual(cs[1]['end_frame'],w['end_frame'])
                for c in cs: self.assertEqual(c['frames'],160000)
        r = load('results.json')['prospective_B']
        self.assertEqual(r['calls'],dict(sensevoice=40,qwen_base=40,qwen_children=4,total=84))
        self.assertEqual(r['untriggered_potential_children'],60)
        self.assertEqual(r['audio_seconds']['qwen_total'],736.15)
        self.assertEqual(r['observed_distinct_qwen_sessions'],44)

    def test_posthoc_pair_supports(self):
        ps = load('selection.json')['posthoc_A']['parents']
        self.assertEqual(len(ps),2)
        for p in ps:
            cs = p['children']
            self.assertEqual(len(cs),2)
            self.assertEqual(cs[0]['source_start_frame'],p['parent_start_frame'])
            self.assertEqual(cs[0]['source_end_frame'],cs[1]['source_start_frame'])
            self.assertEqual(cs[1]['source_end_frame'],p['parent_end_frame'])
            self.assertEqual([c['frames'] for c in cs],[160000,160000])

    def test_policy_and_privacy(self):
        p = load('policy.json')
        self.assertEqual(p['parent_eligibility']['native_status_equals'],18)
        self.assertEqual(p['children']['max_depth'],1)
        self.assertEqual(p['quality_gates']['meeting_nonregressions_at_least'],6)
        def visit(x):
            if isinstance(x,dict):
                self.assertFalse(set(x)&{'text','raw','raw_text','block','span','id','parent_id','session_id','worker_pid','execution_sha256','path','resolved_path'})
                for value in x.values(): visit(value)
            elif isinstance(x,list):
                for value in x: visit(value)
        visit(load('results.json'));visit(load('selection.json'))

if __name__=='__main__': unittest.main()
