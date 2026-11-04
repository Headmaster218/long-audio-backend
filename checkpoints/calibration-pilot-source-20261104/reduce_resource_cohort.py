"""Prospective resource-only cohort reduction, before any pilot model call.
The original acquisition remains incomplete; no audio, vectors or scores are
read and no new download occurs. Original C/T and known/unknown roles survive.
"""
from pathlib import Path
import argparse,csv,hashlib,io,json
import native_runner as runner
h=runner.h
ROOT=Path(__file__).resolve().parent
CORPUS=ROOT.parent/'chinese-corpus-feasibility-20261104'
SLOTS_SHA='12d3a3a68e50d89e60fc7067dca80fb64d9697c6e2ce75857cb3ce124b622093'
SELECTION_SHA='b3f45fc7cb609a00cb9d0bca0d7b383e10c2a17daf3fe0e6a5e6f316ca966bd7'

def select(roles,receipt):
    if receipt.get('status')!='BLOCKED' or receipt.get('stop_reason') not in ('wall_limit','cpu_limit','transfer_cap'):
        raise ValueError('Only a settled resource-limit incomplete acquisition qualifies')
    if len(receipt['selected'])!=len({r['slot_id'] for r in receipt['selected']}):raise ValueError('duplicate selected slot')
    selected={r['slot_id'] for r in receipt['selected']}
    if not selected<={r['slot_id'] for r in roles}:raise ValueError('unexpected acquired slot')
    ordered={p:{r:[] for r in ('known','unknown')} for p in ('C','T')}
    for row in roles:
        bucket=ordered[row['split']][row['roster_role']]
        if row['speaker_id'] not in bucket:bucket.append(row['speaker_id'])
    complete={p:{role:[sp for sp in ordered[p][role] if all(r['slot_id'] in selected for r in roles if r['split']==p and r['speaker_id']==sp)] for role in ('known','unknown')} for p in ('C','T')}
    k=min(6,len(complete['C']['known']),len(complete['T']['known']))
    u=min(10,len(complete['C']['unknown']),len(complete['T']['unknown']))
    result={'status':'BLOCKED_INSUFFICIENT_COMPLETE_PEOPLE','K':k,'U':u,'complete_people':complete,
            'minimum_K':3,'minimum_U':5,'original_acquisition_status':receipt['status'],
            'original_stop_reason':receipt['stop_reason'],'no_model_or_score_inputs':True}
    if k<3 or u<5:return result
    keep={p:set(complete[p]['known'][:k]+complete[p]['unknown'][:u]) for p in ('C','T')}
    chosen=[r['slot_id'] for r in roles if r['speaker_id'] in keep[r['split']]]
    if len(chosen)!=2*(4*k+3*u):raise ValueError('complete reduced population mismatch')
    result.update(status='RESOURCE_REDUCED_COHORT_FROZEN_NO_INFERENCE',selected_slot_ids=chosen,
                  selected_people={p:{role:complete[p][role][:(k if role=='known' else u)] for role in ('known','unknown')} for p in ('C','T')},
                  distinct_clips=len(chosen),max_encoder_calls=len(chosen)+8)
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--plan',required=True);ap.add_argument('--plan-sha',required=True);ap.add_argument('--terminal-receipt',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
    plan_pin=h.pinned(args.plan)
    if plan_pin['sha256']!=args.plan_sha:raise ValueError('prospective plan changed')
    plan=h.read_json(args.plan)
    h.verify_pin(plan['source'])
    if plan['source']['path']!=str(Path(__file__).resolve()):raise ValueError('reducer source changed')
    if plan['minimum_K']!=3 or plan['minimum_U']!=5 or plan['allowed_original_stops']!=['wall_limit','cpu_limit','transfer_cap']:raise ValueError('reduction rule changed')
    terminal_pin=h.pinned(args.terminal_receipt);terminal=h.read_json(args.terminal_receipt)
    if terminal.get('positive_original_wait') is not True or terminal.get('exit_code')!=2:raise ValueError('settled original incomplete operation required')
    receipt_pin=h.pinned(CORPUS/'acquired_capped/acquisition_receipt.json');receipt=h.read_json(receipt_pin['path'])
    if receipt['protocol_sha256']!=SELECTION_SHA or receipt['cumulative_archive_bytes']>8589934592:raise ValueError('original acquisition boundary changed')
    if terminal.get('acquisition_receipt_sha256')!=receipt_pin['sha256']:raise ValueError('terminal/receipt binding differs')
    slots=CORPUS/'selection_slots_proposed.csv'
    if h.digest_file(slots)!=SLOTS_SHA:raise ValueError('original slot roles changed')
    result=select(list(csv.DictReader(io.StringIO(slots.read_text()))),receipt)
    result.update(plan=plan_pin,original_acquisition_receipt=receipt_pin,original_terminal=terminal_pin,
                  source_selection_slots_sha256=SLOTS_SHA,original_selection_protocol_sha256=SELECTION_SHA)
    h.verify_pin(receipt_pin);h.verify_pin(terminal_pin);h.write_json(args.output,result)
    print(json.dumps({'status':result['status'],'K':result['K'],'U':result['U'],'output':h.pinned(args.output)}))
    return 0 if result['status']=='RESOURCE_REDUCED_COHORT_FROZEN_NO_INFERENCE' else 2
if __name__=='__main__':raise SystemExit(main())
