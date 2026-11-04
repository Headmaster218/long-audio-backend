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
                row.update(status='qualified',sample_rate=16000,source_local_path=str(original),crop_local_path=str(crop),
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
            row=next(r for r in duplicate['candidates'] if r['slot_id']==duplicate['selected'][1]['slot_id'] and r['candidate_rank']=='1')
            row['crop_pcm_sha256']=duplicate['selected'][0]['crop_pcm_sha256']
            with self.assertRaisesRegex(ValueError,'duplicate crop_pcm'):run('dup-crop',duplicate)

            malformed=copy.deepcopy(receipt);malformed['selected'][0]['source_wav_sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'selected candidate/history'):run('changed-selected',malformed)
            malformed=copy.deepcopy(receipt);malformed['candidates'][0]['sample_rate']=True
            with self.assertRaisesRegex(ValueError,'candidate source metadata'):run('bool-rate',malformed)

            # A separate, resource-stopped original fixture remains BLOCKED.
            # Only original complete roles can enter the prospective reducer.
            import reduce_resource_cohort as reduction
            import orchestrator
            original_roles=list(csv.DictReader(io.StringIO((corpus/'selection_slots_proposed.csv').read_text())))
            keep=set()
            for split in ('C','T'):
                for role,limit in [('known',3),('unknown',5)]:
                    names=[]
                    for r in original_roles:
                        if r['split']==split and r['roster_role']==role and r['speaker_id'] not in names:names.append(r['speaker_id'])
                    keep.update(r['slot_id'] for r in original_roles if r['split']==split and r['speaker_id'] in names[:limit])
            partial=copy.deepcopy(receipt);partial.update(status='BLOCKED',stop_reason='wall_limit')
            partial['selected']=[r for r in partial['selected'] if r['slot_id'] in keep];partial['selected_count']=len(partial['selected'])
            raw_candidates=list(csv.DictReader(io.StringIO((corpus/'selection_candidates_proposed.csv').read_text())))
            partial['candidates']=[r if r['slot_id'] in keep else dict(raw_candidates[i],status='unseen') for i,r in enumerate(partial['candidates'])]
            rp.write_text(json.dumps(partial));terminal=base/'synthetic-terminal.json'
            terminal.write_text(json.dumps({'positive_original_wait':True,'exit_code':2,'acquisition_receipt_sha256':p.h.pinned(rp)['sha256']}))
            derived=reduction.select(original_roles,partial)
            derived.update(plan=p.h.pinned(p.ROOT/'RESOURCE_ONLY_FALLBACK_PLAN.json'),original_acquisition_receipt=p.h.pinned(rp),original_terminal=p.h.pinned(terminal),source_selection_slots_sha256=p.SLOTS_SHA,original_selection_protocol_sha256=p.SELECTION_SHA)
            cohort=base/'synthetic-cohort.json';cohort.write_text(json.dumps(derived))
            with patch.object(p,'CORPUS',corpus),patch.object(sys,'argv',['prepare_inputs','--output',str(base/'reduced'),'--resource-cohort',str(cohort),'--resource-cohort-sha',p.h.pinned(cohort)['sha256']]):p.main()
            reduced=json.loads((base/'reduced/protocol.json').read_text())
            self.assertEqual(reduced['counts']['K_per_split'],3);self.assertEqual(reduced['counts']['unknown_people_per_split'],5)
            self.assertEqual(reduced['counts']['distinct_clips_total'],54);self.assertEqual(reduced['counts']['max_encoder_calls_total'],62)
            self.assertEqual(json.loads(rp.read_text())['status'],'BLOCKED')
            self.assertEqual(len(json.loads((base/'reduced/COHORT_EXCLUSIONS.json').read_text())['excluded_people']),16)
            orchestrator._protocol_inputs(p.h.pinned(base/'reduced/protocol.json'))
            too_small=copy.deepcopy(partial);too_small['selected']=[]
            self.assertEqual(reduction.select(original_roles,too_small)['status'],'BLOCKED_INSUFFICIENT_COMPLETE_PEOPLE')
            bad_stop=copy.deepcopy(partial);bad_stop['stop_reason']='acquisition_error'
            with self.assertRaises(ValueError):reduction.select(original_roles,bad_stop)

if __name__=='__main__':unittest.main(verbosity=2)
