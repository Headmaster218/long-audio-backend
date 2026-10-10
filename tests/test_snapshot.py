"""Synthetic only: no downloaded corpus, recognizer import or model execution."""
import array, hashlib, json, tempfile, types, unittest, unicodedata, wave
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import common, prepare, recognize, score

class FakeMetrics:
    normalize = staticmethod(prepare.norm)
    @staticmethod
    def lexical(ref, hyp):
        a=''.join(prepare.norm(ref).split()); b=''.join(prepare.norm(hyp).split())
        # Independent scalar DP, intentionally separate from scored S/D/I tuples.
        prev=list(range(len(b)+1))
        for i,x in enumerate(a,1):
            cur=[i]
            for j,y in enumerate(b,1): cur.append(min(prev[j]+1,cur[-1]+1,prev[j-1]+(x!=y)))
            prev=cur
        return {'character_errors':prev[-1],'reference_characters':len(a)}

class MetricsTests(unittest.TestCase):
    def setUp(self): score.metrics=FakeMetrics
    def test_normalization(self): self.assertEqual(score.chars('Ａ， B!\n猫'),'ab猫')
    def test_edits(self):
        self.assertEqual(score.edits('abc','adc'),dict(errors=1,substitutions=1,deletions=0,insertions=0,reference_characters=3))
        self.assertEqual(score.edits('abc','')['deletions'],3)
        self.assertEqual(score.edits('abc','abcd')['insertions'],1)
    def examples(self):return [dict(id=str(i),session='synthetic'+str(i),stratum='quiet' if i<4 else 'ordinary',reference='ab') for i in range(8)]
    def records(self):return [dict(id=str(i),arm=a,status='success',text='ab',raw=None,truncated=False) for a in ('sensevoice','qwen') for i in range(8)]
    def test_missing_and_failed_rows_keep_denominator(self):
        examples=self.examples(); records=self.records(); records.pop()
        records[0].update(status='model_failure',text='partial')
        result=score.score(examples,records,[e['session'] for e in examples])
        for a in ('sensevoice','qwen'):
            self.assertEqual(result['arms'][a]['overall']['reference_characters'],16)
            self.assertEqual(result['arms'][a]['overall']['errors'],2)
            self.assertEqual(result['arms'][a]['overall']['failures'],1)
        self.assertFalse(result['gate']['passes'])
    def test_duplicate_rejected(self):
        rows=self.records()
        with self.assertRaises(ValueError): score.score(self.examples(),rows+[rows[0]],[e['session'] for e in self.examples()])
    def test_truncation_inconsistent_rejected(self):
        r=self.records()[0]; r['truncated']=True
        with self.assertRaises(ValueError):score.row_score(self.examples()[0],r)
    def test_repetition_threshold(self):
        self.assertTrue(score.repetition('abcd'*8)['flagged'])
        self.assertFalse(score.repetition('abcd'*7)['flagged'])

class PreparationTests(unittest.TestCase):
    def test_parser_precision_and_identity(self):
        text='''File type = "ooTextFile"
Object class = "TextGrid"
xmin = 0
xmax = 4
size = 1
item [1]:
class = "IntervalTier"
name = "synthetic"
intervals: size = 1
intervals [1]:
xmin = 0.00001
xmax = 2.00001
text = " test "
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'synthetic.TextGrid';p.write_text(text)
            rows,_=prepare.parse(p,'synthetic')
        self.assertEqual(rows[0]['native_start'],'0.00001')
        self.assertEqual(rows[0]['reference'],'test')
        expected=hashlib.sha256(json.dumps(['synthetic','synthetic','0.00001','2.00001'],separators=(',',':')).encode()).hexdigest()
        self.assertEqual(rows[0]['id'],expected)
    def test_quartile_selection_and_nonoverlap(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'synthetic.wav'
            with wave.open(str(p),'wb') as w:
                w.setparams((8,2,16000,0,'NONE','not compressed'))
                for i in range(16):w.writeframes(array.array('h',[i+1]*8).tobytes()*32000)
            rows=[dict(id=f'{i:02d}',speaker='a' if i%2 else 'b',native_start=str(i*2),native_end=str(i*2+2),reference='synthetic') for i in range(16)]
            with wave.open(str(p)) as w: selected=prepare.select(rows,w)
            self.assertEqual([r['id'] for r in selected],['00','01','02','03','04','05','06','07'])
            self.assertEqual([r['stratum'] for r in selected],['quiet']*4+['ordinary']*4)
            self.assertEqual(sum(r['frames'] for r in selected),256000)
            # A nonempty other annotation protects the overlap even if too short
            # to become an eligible selected utterance itself.
            rows.append(dict(id='other',speaker='c',native_start='0.5',native_end='0.6',reference='protect'))
            with wave.open(str(p)) as w: selected=prepare.select(rows,w)
            self.assertNotIn('00',[r['id'] for r in selected])
    def test_output_in_git_rejected(self):
        with self.assertRaises(ValueError):common.external_output(common.HERE/'audio.wav')
    def test_backend_output_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):common.external_output(Path(d)/'predictions.jsonl',d)
    def test_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'synthetic';p.write_bytes(b'a')
            with self.assertRaises(ValueError):common.verify(p,dict(bytes=1,sha256='0'*64))

class SnapshotTests(unittest.TestCase):
    def test_metadata_counts_and_aggregates(self):
        selected=common.read(common.HERE/'selection.json'); result=common.read(common.HERE/'results.json')
        self.assertEqual(len(selected),56);self.assertEqual(len({r['id'] for r in selected}),56)
        self.assertEqual(sum(r['frames'] for r in selected),5041440)
        self.assertEqual(sum(r['normalized_reference_characters'] for r in selected),1578)
        self.assertEqual(sum(r['stratum']=='quiet' for r in selected),26)
        for arm,total in [('sensevoice',191),('qwen',129)]:
            rows=result['arms'][arm]['rows']
            self.assertEqual(sum(r['errors'] for r in rows),total)
            self.assertEqual(sum(r['substitutions']+r['deletions']+r['insertions'] for r in rows),total)
            self.assertEqual({r['id'] for r in rows},{r['id'] for r in selected})
            self.assertTrue(all(r['status']=='success' and not r['truncated'] for r in rows))
    def test_no_reference_or_prediction_text_in_metadata(self):
        def walk(v):
            if isinstance(v,dict):
                self.assertFalse(set(v)&{'reference','native_text','text','raw_text','predictions'})
                for value in v.values():walk(value)
            elif isinstance(v,list):
                for value in v:walk(value)
        for name in ('selection.json','results.json','assets.json','runtime.json','backend-source.json'):
            walk(common.read(common.HERE/name))

if __name__=='__main__': unittest.main()

class NativeMappingTests(unittest.TestCase):
    def test_qwen_mapping_without_recognizer_import(self):
        fake = types.ModuleType('audio_backend.qwen_candidate_runner')
        raw = dict(status='returned_text',text='synthetic',issues=[],truncation_available=True,native_status=0,was_truncated=False)
        fake.decode_window=lambda *args: dict(raw)
        with patch.dict(sys.modules,{'audio_backend.qwen_candidate_runner':fake}):
            self.assertEqual(recognize.qwen_call(None,None,None)['status'],'success')
            raw.update(status='output_truncated',was_truncated=True,native_status=13)
            self.assertEqual(recognize.qwen_call(None,None,None)['status'],'truncated')
            self.assertTrue(recognize.qwen_call(None,None,None)['truncated'])
            raw.update(status='native_error',was_truncated=False,native_status=1)
            self.assertEqual(recognize.qwen_call(None,None,None)['status'],'infrastructure_failure')
            raw.update(native_status=None,truncation_available=False)
            self.assertEqual(recognize.qwen_call(None,None,None)['status'],'unknown')
