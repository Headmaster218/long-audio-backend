import copy,importlib.util,itertools,json,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('whisper_text_score',ROOT/'score.py')
score=importlib.util.module_from_spec(spec);spec.loader.exec_module(score)

def example():
    return {'schema':'whole-excerpt-text-input-v1','excerpts':[
        {'id':'synthetic-a','reference':'甲乙','reference_characters':2,'window_ids':['a1','a2']},
        {'id':'synthetic-b','reference':'Ａb','reference_characters':2,'window_ids':['b1']}],
        'records':[{'window_id':'a1','status':'success','transcription':[{'text':'甲'}]},
                   {'window_id':'a2','status':'success','transcription':[{'text':'乙'}]},
                   {'window_id':'b1','status':'success','transcription':[{'text':'A b!'}]}]}

class LexicalTests(unittest.TestCase):
    def test_perfect(self):
        r=score.score(example());self.assertEqual(r['overall']['errors'],0);self.assertTrue(r['complete'])
    def test_missing_keeps_denominator(self):
        b=example();b['records']=[];r=score.score(b)
        self.assertEqual((r['overall']['errors'],r['overall']['deletions'],r['overall']['reference_characters']),(4,4,4));self.assertEqual(len(r['ledger']),3);self.assertFalse(r['complete'])
    def test_failed_text_is_empty(self):
        b=example();b['records'][0]['status']='failed';r=score.score(b)
        self.assertEqual(r['overall']['errors'],1);self.assertEqual(r['overall']['reference_characters'],4);self.assertFalse(r['complete'])
    def test_native_array_order_and_tokens_ignored(self):
        b=example();b['excerpts'][0]['window_ids']=['a1'];b['records']=b['records'][:1]+b['records'][2:]
        b['records'][0]['transcription']=[{'text':'甲','tokens':[{'text':'\udce4'}],'offsets':{'from':99,'to':99}},{'text':'乙','offsets':{'from':0,'to':0}}]
        self.assertEqual(score.score(b)['overall']['errors'],0)
        b['records'][0]['transcription'].reverse();self.assertEqual(score.score(b)['overall']['errors'],2)
    def test_duplicate_output_rejected(self):
        b=example();b['records'].append(b['records'][0])
        with self.assertRaises(ValueError):score.score(b)
    def test_foreign_output_rejected(self):
        b=example();b['records'][0]['window_id']='foreign'
        with self.assertRaises(ValueError):score.score(b)
    def test_reference_count_rejected(self):
        b=example();b['excerpts'][0]['reference_characters']=1
        with self.assertRaises(ValueError):score.score(b)
    def test_unicode_normalization_no_script_conversion(self):
        self.assertEqual(score.characters('Ａ B，甲💡'), 'ab甲');self.assertNotEqual(score.characters('汉'),score.characters('漢'))
    def test_explicit_truncated_status_is_incomplete(self):
        b=example();b['records'][0]['status']='truncated';r=score.score(b)
        self.assertEqual(r['overall']['errors'],0);self.assertEqual(r['overall']['explicit_truncated_windows'],1);self.assertFalse(r['complete'])
    def test_output_is_counts_only(self):
        self.assertNotIn('reference"',json.dumps(score.score(example())));self.assertNotIn('hypothesis"',json.dumps(score.score(example())))
    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'input.json';p.write_text('{"schema":1,"schema":2}')
            with self.assertRaises(ValueError):score.load(p)
    def test_exhaustive_edit_counts(self):
        strings=[''.join(s) for n in range(4) for s in itertools.product('ab',repeat=n)]
        def independent(a,b):
            cells={}
            for i in range(len(a)+1):
                for j in range(len(b)+1):
                    if not i:cells[i,j]=(j,0,0,j)
                    elif not j:cells[i,j]=(i,0,i,0)
                    else:
                        q=cells[i-1,j-1];c=int(a[i-1]!=b[j-1]);di=(q[0]+c,q[1]+c,q[2],q[3]);q=cells[i-1,j];de=(q[0]+1,q[1],q[2]+1,q[3]);q=cells[i,j-1];ins=(q[0]+1,q[1],q[2],q[3]+1)
                        cells[i,j]=sorted(enumerate((di,de,ins)),key=lambda x:(x[1][0],x[0]))[0][1]
            return cells[len(a),len(b)]
        for a in strings:
            for b in strings:
                got=score.edit_counts(a,b);self.assertEqual(tuple(got[k] for k in ('errors','substitutions','deletions','insertions')),independent(a,b))

class MetadataTests(unittest.TestCase):
    def test_support(self):
        d=json.loads((ROOT/'selection.json').read_text());self.assertEqual(len(d['excerpts']),2);ws=[w for e in d['excerpts'] for w in e['windows']]
        self.assertEqual(len(ws),11);self.assertEqual(sum(w['frames'] for w in ws),3039600);self.assertEqual(sum(e['reference_characters'] for e in d['excerpts']),1224)
        for e in d['excerpts']:
            self.assertEqual(e['partial_outer_turns'],0);self.assertEqual(e['windows'][0]['start_frame'],e['start_frame']);self.assertEqual(e['windows'][-1]['end_frame'],e['end_frame'])
            for a,b in zip(e['windows'],e['windows'][1:]):self.assertEqual(a['end_frame'],b['start_frame'])
    def test_counts_sum(self):
        d=json.loads((ROOT/'results.json').read_text());self.assertFalse(d['defaults_changed']);b=d['beam5_text_diagnostic'];self.assertEqual(b['calls'],11)
        for arm in b['full_reference_counts'].values():
            self.assertEqual(arm['overall']['reference_characters'],1224)
            for k in ('errors','substitutions','deletions','insertions','reference_characters'):self.assertEqual(arm['overall'][k],sum(m[k] for m in arm['meetings'].values()))
            self.assertEqual(arm['overall']['errors'],sum(arm['overall'][k] for k in ('substitutions','deletions','insertions')))
    def test_speed_and_policy_distinct(self):
        d=json.loads((ROOT/'results.json').read_text());a=d['greedy_smoke'];b=d['beam5_text_diagnostic'];self.assertEqual(a['beam_size'],1);self.assertEqual(b['beam_size'],5);self.assertFalse(a['cer_computed']);self.assertFalse(a['fallback']);self.assertFalse(b['fallback'])
        self.assertAlmostEqual(b['native_call_rtf'],b['sum_native_process_call_wall_seconds']/189.975);self.assertAlmostEqual(b['whole_phase_rtf'],b['whole_phase_wall_seconds']/189.975)
if __name__=='__main__':unittest.main()
