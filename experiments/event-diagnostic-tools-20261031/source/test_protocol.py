"""No model loads; exercises positive-only metric semantics and frozen mapping."""
import unittest,sys,pathlib,argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo-root',type=pathlib.Path,required=True)
args,remaining=parser.parse_known_args()
sys.argv=[sys.argv[0]]+remaining
ROOT=args.repo_root.resolve(strict=True)
sys.path.insert(0,str(ROOT))
from audio_backend.events.detector import category,postprocess
from audio_backend.events.metrics import presence_metrics
class Contract(unittest.TestCase):
 def test_positive_only(self):
  out=presence_metrics([{'id':'synthetic-contract','labels':['rain']}],{'synthetic-contract':{'rain'}},['rain','door'])
  self.assertEqual(out['rain']['tp'],1);self.assertIsNone(out['rain']['precision']);self.assertIsNone(out['rain']['f1']);self.assertEqual(out['door']['unknown'],1)
 def test_rain_mapping_and_threshold(self):
  for x in ['Rain','Raindrop','Rain on surface','Thunderstorm']:self.assertEqual(category(x),'rain')
  def run(score):return postprocess([{'start':0,'end':5,'category_scores':{'rain':{'score':score,'source_label':'Rain'}}}],{'rain':.25})
  self.assertFalse(run(.249999));self.assertEqual(run(.25)[0]['time_precision'],'window_support');self.assertIsNone(run(.25)[0]['onset_precision_seconds'])
if __name__=='__main__':unittest.main()
