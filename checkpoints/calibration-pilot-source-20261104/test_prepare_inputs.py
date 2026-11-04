"""One synthetic binding fixture; no corpus audio or model execution."""
import copy,csv,hashlib,io,json,sys,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch
import prepare_inputs as p

class Preparation(unittest.TestCase):
    def test_complete_binding_and_duplicate_failures(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT,prefix='synthetic-preparation-') as td:
            base=Path(td); corpus=base/'corpus';corpus.mkdir(); acquired=corpus/'acquired_capped'
            (acquired/'sources').mkdir(parents=True);(acquired/'crops').mkdir()
            for name in ('selection_slots_proposed.csv','selection_candidates_proposed.csv','selection_protocol_candidates_v2.json'):
                (corpus/name).write_bytes((p.CORPUS/name).read_bytes())
            candidates=list(csv.DictReader(io.StringIO((corpus/'selection_candidates_proposed.csv').read_text())))
            selected=[];number=0
            for row in candidates:
                row['status']='unseen'
                if row['candidate_rank']!='1': continue
                number+=1; mono=number.to_bytes(2,'little',signed=True)*32000
                stereo=(number.to_bytes(2,'little',signed=True)+(-number).to_bytes(2,'little',signed=True))*32000
                original=acquired/'sources'/(row['utterance_id']+'.wav'); crop=acquired/'crops'/(row['utterance_id']+'.wav')
                for path,channels,data in ((original,2,stereo),(crop,1,mono)):
                    with wave.open(str(path),'wb') as w:w.setparams((channels,2,16000,0,'NONE','not compressed'));w.writeframes(data)
                row.update(status='qualified',source_local_path=str(original),crop_local_path=str(crop),
                           source_wav_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
                           crop_wav_sha256=hashlib.sha256(crop.read_bytes()).hexdigest(),
                           crop_pcm_sha256=hashlib.sha256(mono).hexdigest(),crop_start_sample=0)
                selected.append({k:v for k,v in row.items() if k!='status'})
            receipt={'status':'ACQUIRED_ALL_SLOTS','selected_count':108,'planned_slots':108,
                     'protocol_sha256':p.SELECTION_SHA,'candidate_manifest_sha256':p.CANDIDATES_SHA,
                     'cumulative_archive_bytes':4194304,'selected':selected,'candidates':candidates}
            rp=acquired/'acquisition_receipt.json'
            def run(name,value):
                rp.write_text(json.dumps(value))
                with patch.object(p,'CORPUS',corpus),patch.object(sys,'argv',['prepare_inputs','--output',str(base/name)]): p.main()
            run('good',receipt)
            protocol=json.loads((base/'good/protocol.json').read_text())
            c=json.loads((base/'good/C_MANIFEST.json').read_text())
            self.assertEqual(len(c['samples']),54)
            self.assertEqual(protocol['counts']['max_encoder_calls_total'],116)
            self.assertEqual(c['protocol_sha256'],hashlib.sha256((base/'good/protocol.json').read_bytes()).hexdigest())
            duplicate=copy.deepcopy(receipt);duplicate['selected'].append(duplicate['selected'][0])
            with self.assertRaisesRegex(ValueError,'selected waveform population'):run('dup-selected',duplicate)
            duplicate=copy.deepcopy(receipt);duplicate['candidates'].append(duplicate['candidates'][0])
            with self.assertRaisesRegex(ValueError,'candidate history'):run('dup-history',duplicate)
            duplicate=copy.deepcopy(receipt);duplicate['selected'][1]['crop_pcm_sha256']=duplicate['selected'][0]['crop_pcm_sha256']
            with self.assertRaisesRegex(ValueError,'duplicate crop_pcm'):run('dup-crop',duplicate)

if __name__=='__main__':unittest.main(verbosity=2)
