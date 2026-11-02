"""Synthetic interval controls; no corpus input or output."""
import unittest
from diagnose_activity_geometry import overlap,union
class GeometryControls(unittest.TestCase):
 def test_half_open_adjacent(self):self.assertEqual(overlap(0,1,1,2),0)
 def test_union(self):self.assertEqual(union([(2,3),(0,1),(1,2),(.5,1.5)]),[[0,3]])
 def test_intersection(self):self.assertEqual(overlap(1,4,2,3),1)
if __name__=='__main__':unittest.main()
