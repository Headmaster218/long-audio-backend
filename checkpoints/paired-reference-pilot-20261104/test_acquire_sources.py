import hashlib,io,json,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch
import acquire_sources as a
class Response(io.BytesIO):
 def __init__(self,b,url,etag='fixed',status=200):super().__init__(b);self.url=url;self.status=status;self.headers={'Content-Length':str(len(b)),'ETag':etag}
 def geturl(self):return self.url
class Tests(unittest.TestCase):
 def setup_inputs(self,p):
  b=io.BytesIO()
  with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\x00\x01'*32000)
  body=b.getvalue();heads=[];rows=[]
  for meeting in ['ES2014a','ES2014d','ES2015a','ES2015d']:
   url=a.ROOT+meeting+'/audio/'+meeting+'.Mix-Headset.wav';rows.append({'official_wav_url':url});heads.append({'url':url,'body_bytes':44,'headers':{'content-range':f'bytes 0-43/{len(body)}','etag':'fixed'},'header_sha256':hashlib.sha256(body[:44]).hexdigest()})
  h=p/'headers.json';c=p/'candidates.json';h.write_text(json.dumps(heads));c.write_text(json.dumps({'rows':rows}));return body,h,c
 def run_case(self,mode):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);body,h,c=self.setup_inputs(p)
   def fetch(req,timeout):
    r=Response(body,req.full_url,etag='bad' if mode=='changed' else 'fixed',status=206 if mode=='range' else 200)
    if mode=='truncated':r=Response(body[:-2],req.full_url);r.headers['Content-Length']=str(len(body))
    return r
   with patch.object(a.urllib.request,'urlopen',fetch),patch.object(a.resource,'setrlimit'),patch.object(a,'free',return_value=a.RESERVE+10000000):
    if mode=='good':
     r=a.acquire(h,a.digest(h),c,a.digest(c),p/'out');self.assertEqual(r['status'],'complete');self.assertEqual(len(r['files']),4);self.assertEqual(r['downloaded_bytes'],len(body)*4)
    else:
     with self.assertRaises(ValueError):a.acquire(h,a.digest(h),c,a.digest(c),p/'out')
     r=json.loads((p/'out/ACQUISITION_RECEIPT.json').read_text());self.assertEqual(r['status'],'blocked');self.assertEqual(r['files'],[])
 def test_complete(self):self.run_case('good')
 def test_changed_etag(self):self.run_case('changed')
 def test_no_partial_response(self):self.run_case('range')
 def test_truncation_preserved(self):self.run_case('truncated')
 def test_pin_before_network(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);body,h,c=self.setup_inputs(p)
   with patch.object(a.urllib.request,'urlopen') as mock:
    with self.assertRaises(ValueError):a.acquire(h,'0'*64,c,a.digest(c),p/'out')
    mock.assert_not_called();self.assertFalse((p/'out').exists())
if __name__=='__main__':unittest.main()
