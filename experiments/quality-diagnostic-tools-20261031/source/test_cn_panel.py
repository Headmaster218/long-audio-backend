import unittest
from select_cn_panel import greedy,overlaps
class PanelControls(unittest.TestCase):
 def test_half_open(self):self.assertFalse(overlaps((0,10),(10,20)))
 def test_greedy(self):
  cs=[dict(start_frame=a,end_frame=b,frames=b-a) for a,b in [(0,10),(5,15),(20,30),(40,50),(60,70)]]
  self.assertEqual([x['start_frame'] for x in greedy(cs)],[0,20,40])
 def test_current_exclusion(self):self.assertEqual(greedy([dict(start_frame=15282880,end_frame=15468012)]),[])
if __name__=='__main__':unittest.main()
