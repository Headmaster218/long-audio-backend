"""Model-free binding of the single preselected SMIIP-TV pilot to exact input bytes.
Run only after the capped acquisition has settled every frozen slot. No model
construction, threshold fitting or candidate selection occurs here.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import io
import json
import resource
import signal
import struct
import time
import wave
import native_runner as runner
h = runner.h
ROOT=Path(__file__).resolve().parent
CORPUS=ROOT.parent/'chinese-corpus-feasibility-20261104'
SLOTS_SHA='12d3a3a68e50d89e60fc7067dca80fb64d9697c6e2ce75857cb3ce124b622093'
CANDIDATES_SHA='5973e14ff61fe37e3cb54746f09b5de43492d03f9a6869e2a62f13c62b87c56f'
SELECTION_SHA='b3f45fc7cb609a00cb9d0bca0d7b383e10c2a17daf3fe0e6a5e6f316ca966bd7'

def roles():
    path=CORPUS/'selection_slots_proposed.csv'
    if h.digest_file(path)!=SLOTS_SHA: raise ValueError('fixed slot selection changed')
    rows=list(csv.DictReader(io.StringIO(path.read_text())))
    records=[]
    for r in rows:
        role={'reference':'reference','primary':'primary','diagnostic':'diagnostic'}[r['query_role']]
        records.append(dict(id=r['slot_id'],split=r['split'],speaker_id='SMIIP-TV:'+r['speaker_id'],
            person_id=r['split']+':'+r['speaker_id'] if r['roster_role']=='known' else None,
            roster_role=r['roster_role'],role=role,recording_date=r['recording_date'],
            session_id='SMIIP-TV:'+r['speaker_id']+'@'+r['recording_date']))
    if len(records)!=108 or len({r['id'] for r in records})!=108: raise ValueError('slot population mismatch')
    people={p:{r['speaker_id'] for r in records if r['split']==p} for p in ('C','T')}
    if len(people['C'])!=16 or len(people['T'])!=16 or people['C']&people['T']: raise ValueError('speaker split mismatch')
    for p in ('C','T'):
        for person in people[p]:
            group=[r for r in records if r['split']==p and r['speaker_id']==person]
            queries=[r for r in group if r['role']!='reference'];refs=[r for r in group if r['role']=='reference']
            if len(queries)!=3 or len({r['recording_date'] for r in group})!=len(group): raise ValueError('cross-date population mismatch')
            if [r['role'] for r in sorted(queries,key=lambda r:r['recording_date'])]!=['primary','diagnostic','diagnostic']: raise ValueError('primary is not earliest frozen query')
            if group[0]['roster_role']=='known':
                if len(refs)!=1 or refs[0]['recording_date']>=min(r['recording_date'] for r in queries): raise ValueError('cross-date reference mismatch')
            elif refs: raise ValueError('unknown reference present')
    return {'schema':1,'source_selection_slots_sha256':SLOTS_SHA,'records':records}

def cohort_binding(args,role_doc,receipt,receipt_path):
    if not args.resource_cohort and not args.resource_cohort_sha:
        if receipt['status']!='ACQUIRED_ALL_SLOTS' or receipt['selected_count']!=108:
            raise ValueError('original108 acquisition incomplete; explicit separate resource cohort required')
        return role_doc,6,10,None,[]
    if not args.resource_cohort or not args.resource_cohort_sha:raise ValueError('both resource cohort path and external SHA required')
    import reduce_resource_cohort as reduction
    pin=h.pinned(args.resource_cohort)
    if pin['sha256']!=args.resource_cohort_sha:raise ValueError('resource cohort changed')
    bound=h.read_json(pin['path']); h.verify_pin(bound['plan'])
    if bound['plan']['sha256']!='165f26e47bd6e85100fb0560545baef103485039b09bd642a814bc109c170368':raise ValueError('unregistered resource-only rule')
    plan=h.read_json(bound['plan']['path']);h.verify_pin(plan['source'])
    if plan['source']!=h.pinned(reduction.__file__):raise ValueError('resource rule source changed')
    if bound['original_acquisition_receipt']!=h.pinned(receipt_path):raise ValueError('resource cohort no longer binds original receipt')
    h.verify_pin(bound['original_terminal']);terminal=h.read_json(bound['original_terminal']['path'])
    if terminal.get('positive_original_wait') is not True or terminal.get('exit_code')!=2 or terminal.get('acquisition_receipt_sha256')!=bound['original_acquisition_receipt']['sha256']:raise ValueError('original resource-limited process not settled')
    original_rows=list(csv.DictReader(io.StringIO((CORPUS/'selection_slots_proposed.csv').read_text())))
    expected=reduction.select(original_rows,receipt)
    if expected['status']!='RESOURCE_REDUCED_COHORT_FROZEN_NO_INFERENCE' or any(bound.get(k)!=v for k,v in expected.items()):raise ValueError('resource cohort differs from frozen rule')
    chosen=set(expected['selected_slot_ids']);all_roles=role_doc['records'];reduced=dict(role_doc)
    reduced['records']=[r for r in all_roles if r['id'] in chosen];reduced['resource_cohort']=pin
    excluded=[];seen=set();acquired={r['slot_id'] for r in receipt['selected']}
    for row in all_roles:
        key=(row['split'],row['speaker_id'])
        if key in seen:continue
        seen.add(key);group=[r for r in all_roles if (r['split'],r['speaker_id'])==key]
        if all(r['id'] in chosen for r in group):continue
        missing=[r['id'] for r in group if r['id'] not in acquired]
        excluded.append({'split':row['split'],'speaker_id':row['speaker_id'],'original_role':row['roster_role'],
                         'reason':'incomplete_original_slots' if missing else 'common_K_U_balance_original_order','missing_slot_ids':missing})
    return reduced,expected['K'],expected['U'],pin,excluded

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--resource-cohort');ap.add_argument('--resource-cohort-sha');args=ap.parse_args()
    start=time.monotonic(); signal.alarm(290); resource.setrlimit(resource.RLIMIT_CPU,(120,120));resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024**2,64*1024**2))
    out=Path(args.output).resolve();out.mkdir(exist_ok=False)
    role_doc=roles();all_roles=role_doc['records']
    receipt_path=CORPUS/'acquired_capped/acquisition_receipt.json'; receipt=h.read_json(receipt_path)
    if (receipt['planned_slots']!=108
        or receipt['protocol_sha256']!=SELECTION_SHA or receipt['candidate_manifest_sha256']!=CANDIDATES_SHA
        or receipt['cumulative_archive_bytes']>8589934592): raise ValueError('capped acquisition not complete or changed')
    if h.digest_file(CORPUS/'selection_protocol_candidates_v2.json')!=SELECTION_SHA: raise ValueError('selection protocol changed')
    if h.digest_file(CORPUS/'selection_candidates_proposed.csv')!=CANDIDATES_SHA: raise ValueError('candidate plan changed')
    expected_candidates=list(csv.DictReader(io.StringIO((CORPUS/'selection_candidates_proposed.csv').read_text())))
    candidates={(r['slot_id'],str(r['candidate_rank'])):r for r in receipt['candidates']}
    if len(receipt['candidates'])!=len(expected_candidates) or len(candidates)!=len(expected_candidates): raise ValueError('candidate history incomplete')
    for e in expected_candidates:
        actual=candidates.get((e['slot_id'],str(e['candidate_rank'])))
        if actual is None: raise ValueError('candidate source metadata missing')
        for key,value in e.items():
            observed=actual.get(key)
            # The frozen acquisition retains source metadata but overwrites this
            # one policy-string field with the measured WAV sample rate.
            if key=='sample_rate' and type(observed) is int:
                if observed==16000: continue
                if actual.get('status')=='rejected' and actual.get('reason')=='required_16k_PCM16_stereo' and observed>=0: continue
                raise ValueError('invalid observed sample rate/status')
            if observed!=value: raise ValueError('candidate source metadata changed')
    selected={r['slot_id']:r for r in receipt['selected']}
    for slot,chosen in selected.items():
        rank=int(chosen['candidate_rank'])
        actual=candidates.get((slot,str(rank)))
        if actual is None or actual['status']!='qualified': raise ValueError('selected candidate not qualified')
        for earlier in range(1,rank):
            if candidates[(slot,str(earlier))]['status']!='rejected': raise ValueError('selected over unresolved earlier candidate')
        if chosen!={k:v for k,v in actual.items() if k!='status'}: raise ValueError('selected candidate/history binding differs')
    if len(receipt['selected'])!=receipt['selected_count'] or len(selected)!=receipt['selected_count'] or not set(selected)<={r['id'] for r in all_roles}:raise ValueError('selected waveform population mismatch')
    reconstructed={}
    for role in all_roles:
        group=sorted((r for r in candidates.values() if r['slot_id']==role['id']),key=lambda r:int(r['candidate_rank']))
        for candidate in group:
            if candidate['status']=='unseen':break
            if candidate['status']=='qualified':
                reconstructed[role['id']]={k:v for k,v in candidate.items() if k!='status'};break
            if candidate['status']!='rejected':raise ValueError('invalid candidate resolution order')
    if reconstructed!=selected:raise ValueError('receipt selected list differs from full candidate history')
    role_doc,K,U,cohort_pin,exclusions=cohort_binding(args,role_doc,receipt,receipt_path)
    rows=role_doc['records'];selected={r['id']:selected[r['id']] for r in rows};distinct=2*(4*K+3*U)
    if len(rows)!=distinct or len(selected)!=distinct:raise ValueError('derived input population mismatch')
    h.write_json(out/'source_roles.json',role_doc)
    h.write_json(out/'COHORT_EXCLUSIONS.json',{'resource_cohort':cohort_pin,'original_status':receipt['status'],'excluded_people':exclusions})
    for field in ('source_wav_sha256','crop_pcm_sha256'):
        seen={}
        for slot,item in selected.items():
            digest=item[field]
            if digest in seen: raise ValueError('BLOCKED duplicate '+field+' across '+seen[digest]+' and '+slot)
            seen[digest]=slot
    all_samples={};plans={};primary_inputs={};diagnostic_inputs={} 
    for row in rows:
        s=selected[row['id']]; path=Path(s['crop_local_path']).resolve(strict=True)
        if not path.is_relative_to((CORPUS/'acquired_capped/crops').resolve()): raise ValueError('unexpected crop location')
        if s['split']!=row['split'] or 'SMIIP-TV:'+s['speaker_id']!=row['speaker_id'] or s['recording_date']!=row['recording_date']: raise ValueError('waveform role binding mismatch')
        pin=h.pinned(path)
        if pin['sha256']!=s['crop_wav_sha256']: raise ValueError('crop WAV bytes changed')
        sample={'id':row['id'],'wav':pin,'pcm_sha256':s['crop_pcm_sha256']}
        pcm=h.pcm_bytes(sample)
        original=Path(s['source_local_path']).resolve(strict=True)
        if not original.is_relative_to((CORPUS/'acquired_capped/sources').resolve()) or h.digest_file(original)!=s['source_wav_sha256']: raise ValueError('original source WAV changed')
        with wave.open(str(original),'rb') as w:
            if (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getcomptype())!=(2,2,16000,'NONE'): raise ValueError('original format mismatch')
            begin=(w.getnframes()-32000)//2
            if begin<0 or begin!=s['crop_start_sample']: raise ValueError('crop origin mismatch')
            w.setpos(begin);stereo=w.readframes(32000)
        expected_pcm=struct.pack('<32000h',*(v[0] for v in struct.iter_unpack('<hh',stereo)))
        if pcm!=expected_pcm: raise ValueError('crop differs from fixed original channel0/center rule')
        sample['float32_sha256']=hashlib.sha256(struct.pack('<32000f',*(v[0]/32768.0 for v in struct.iter_unpack('<h',pcm)))).hexdigest()
        all_samples[row['id']]=sample
    for split in ('C','T'):
        group=[r for r in rows if r['split']==split];samples=[all_samples[r['id']] for r in group]
        gallery=[{'person_id':r['person_id'],'sample_id':r['id']} for r in group if r['role']=='reference']
        if len(gallery)!=K or len(samples)!=4*K+3*U: raise ValueError('fixed gallery/clip count mismatch')
        for field,include in [('primary',lambda r:r['role']=='primary'),('all',lambda r:r['role']!='reference')]:
            queries=[r for r in group if include(r)]
            predictor={'split':split,'gallery':gallery,'queries':[r['id'] for r in queries]}
            truth={r['id']:{'kind':r['roster_role'],'person_id':r['person_id'],'speaker_id':r['speaker_id'],'session_id':r['session_id']} for r in queries}
            pp=out/(split+'_'+field+'_predictor.json');tp=out/(split+'_'+field+'_truth.json')
            h.write_json(pp,predictor);h.write_json(tp,truth)
            (primary_inputs if field=='primary' else diagnostic_inputs)[split]={'predictor':h.pinned(pp),'truth':h.pinned(tp)}
        anchors=[next(r['id'] for r in group if r['role']=='reference'),next(r['id'] for r in group if r['roster_role']=='unknown' and r['role']=='primary')]
        plan=runner.make_plan_template(split,samples,anchors)
        p=out/(split+'_RUN_PLAN.json');h.write_json(p,plan);plans[split]=h.pinned(p)
    protocol={'schema':1,'experiment':('smiip-tv-native-calibration-pilot-20261104' if cohort_pin is None else 'smiip-tv-native-calibration-pilot-resource-reduced-v1-20261104'),'status':'frozen_before_any_pilot_encoding',
        'scoring_source':h.pinned(ROOT/'scoring.py'),'orchestrator':h.pinned(ROOT/'orchestrator.py'),
        'scoring_inputs':primary_inputs,'diagnostic_inputs':diagnostic_inputs,'source_roles':h.pinned(out/'source_roles.json'),
        'calibration_run_plan':plans['C'],'holdout_run_plan':plans['T'],'input_preparer':h.pinned(__file__),
        'design_document':h.pinned(ROOT.parent/'backend-threshold-calibration-design-20261104/PILOT_PROTOCOL.md'),
        'acquisition_receipt':h.pinned(receipt_path),'selection_protocol':h.pinned(CORPUS/'selection_protocol_candidates_v2.json'),
        'selection_slots':h.pinned(CORPUS/'selection_slots_proposed.csv'),'selection_candidates':h.pinned(CORPUS/'selection_candidates_proposed.csv'),
        'resource_cohort':cohort_pin,'original_32_person_acquisition_status':receipt['status'],'availability_selection_bias':('none beyond original convenience selection' if cohort_pin is None else 'complete-person archive-prefix availability and common K/U truncation; not a random population sample'),'cohort_exclusions':h.pinned(out/'COHORT_EXCLUSIONS.json'),
        'counts':{'K_per_split':K,'unknown_people_per_split':U,'primary_queries_per_split':K+U,'all_queries_per_split':3*(K+U),'distinct_clips_total':distinct,'repeat_calls_total':8,'max_encoder_calls_total':distinct+8},
        'selection_rule':{'grid_start':0,'grid_stop':1,'grid_step':.01,'margin':.10,'baseline':.75,'C_primaries_only':True,'C_max_wrong_known':0,'C_max_unknown_accepted':0,'maximize_known_correct':True,'tie_break':'higher_threshold','no_useful_candidate':'stop_before_T'},
        'interval_policy':{'formal_population_or_safety_CI':None,'reference_only':'conditional_iid_binomial_model_not_validated_sampling_guarantee','primary_n_known':K,'primary_n_unknown':U,'confidence':.95,'C_selected_point_CI':None},
        'resource_limits':{'model_wall_seconds_total':1800,'model_cpu_seconds_total':1200,'sampled_peak_rss_bytes':3*1024**3,'model_output_bytes_total':64*1024**2,'preparation_wall_seconds':300,'preparation_cpu_seconds':120,'preparation_output_bytes':64*1024**2},
        'scope':'Chinese mobile prompted/free-text speech; format/length checks only, no purity/VAD/content verification; not meeting, domain-general, unseen-training-identity or production qualification'}
    h.write_json(out/'protocol.json',protocol); pp=h.pinned(out/'protocol.json')
    actual_c=h.read_json(plans['C']['path']);actual_c['protocol_sha256']=pp['sha256'];runner.validate_manifest(actual_c)
    h.write_json(out/'C_MANIFEST.json',actual_c)
    if time.monotonic()-start>300: raise RuntimeError('preparation wall limit exceeded')
    h.write_json(out/'PREPARATION_RESULT.json',{'status':'prepared_no_inference','protocol':pp,'C_manifest':h.pinned(out/'C_MANIFEST.json'),'distinct_clips':distinct,'calls_run':0,'elapsed_seconds':time.monotonic()-start})
    print(json.dumps({'protocol':pp,'C_manifest':h.pinned(out/'C_MANIFEST.json'),'model_calls':0}))
if __name__=='__main__': main()
