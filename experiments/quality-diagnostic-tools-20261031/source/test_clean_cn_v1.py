"""Synthetic selection controls only; no corpus references."""
import unittest
from decimal import Decimal as D
from select_clean_cn_v1 import candidates, normalize, parse

def row(s,a,b,text='啊'*20):
 return dict(speaker=s,start=D(str(a)),end=D(str(b)),text_raw=text,units=len(normalize(text)),tagged=('<' in text or '&' in text))

class CleanCNControls(unittest.TestCase):
 def test_balanced_complete(self):
  selected,_=candidates([row('a',0,1),row('b',1,2),row('c',2,3)],48000)
  self.assertEqual(len(selected),1)
  self.assertEqual(selected[0]['reference_characters'],60)
 def test_no_tag_deletion(self):
  self.assertEqual(normalize('<sil> 嗯，Ａ１'), 'sil嗯a1')
  for tag in ['<sil>','<#>','&']:
   selected,_=candidates([row('a',0,1,tag+'啊'*20),row('b',1,2),row('c',2,3)],48000)
   self.assertEqual(selected,[])
 def test_prior_audio_excluded(self):
  selected,_=candidates([row('a',125,126),row('b',126,127),row('c',127,128)],2048000)
  self.assertEqual(selected,[])
 def test_no_padding(self):
  self.assertEqual(candidates([row('a',0,1),row('b',1,2),row('c',2,3)],47999)[0],[])
 def test_crossing_utterance_rejected(self):
  rows=[row('a',0,1),row('b',1,2),row('c',2,3),row('d',D('2.5'),50)]
  self.assertEqual(candidates(rows,800000)[0],[])
 def test_parser(self):
  text='''item [1]:
 name = "A"
 intervals [1]:
 xmin = 0
 xmax = 1
 text = "啊"
 intervals [2]:
 xmin = 1
 xmax = 2
 text = ""
 item [2]:
 name = "B"
 intervals [1]:
 xmin = 0
 xmax = 1
 text = "嗯"
'''
  self.assertEqual([(r['speaker'],r['units']) for r in parse(text)],[('A',1),('B',1)])
if __name__=='__main__':unittest.main()
