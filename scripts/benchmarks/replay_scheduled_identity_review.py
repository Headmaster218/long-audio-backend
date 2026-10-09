#!/usr/bin/env python3
"""Finite scheduled-review budget curve, cache-only and separate from prior work."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import resource
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import replay_correction_identity as prior
from audio_backend.experimental_identity_memory import CorrectionMemory

PREFIXES=(0,1,3,6,9)
PROTOCOL=ROOT/'reports/identity_learning_review_budget_v1/protocol.json'


def stable_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def anchor_hash(memory):
    return stable_hash({p:[(a.observation_id,a.vector) for a in refs] for p,refs in sorted(memory.anchors.items())})


def check_frozen_inputs(protocol):
    for name,expected in (protocol['frozen_inputs']|protocol['original_report_directory_hashes']).items():
        path=ROOT/name
        if not path.is_file() or prior.digest(path)!=expected:
            raise ValueError(f'Frozen input changed or is unavailable: {name}')


def admission(memory,row,event):
    """Scheduled review is already charged. Never substitute for rejected input."""
    try:
        memory.verify_latest(row['speaker'],event,whole_evidence_verified=True)
    except OverflowError as exc:
        return 'rejected_capacity',str(exc)
    except ValueError as exc:
        return 'rejected_evidence',str(exc)
    return 'retained',None


def output_row(row,decision,known):
    return {'id':row['id'],'recording_id':row['recording_id'],'recording_date':prior.date(row.get('recording_date')),
      'start':row['start'],'end':row['end'],'seconds':row['end']-row['start'],
      'gold_person':row['speaker'],'known':row['speaker'] in known,
      'phase':row.get('_phase','later_chronological'),'assigned':decision.person_id,
      'status':decision.status,'score':decision.best_score,'anchor_score':decision.anchor_score,
      'margin':decision.margin}


def run_prefix(case,prefix,version,dimension):
    if type(prefix) is not int or prefix not in PREFIXES:
        raise ValueError('Prefix must be one of the predeclared budgets')
    prior.validate_case(case)
    target=case['additional_warmup']
    if prefix>len(target):raise ValueError('Scheduled prefix coverage is unavailable')
    memory=CorrectionMemory(version,dimension,'centroid',max_anchors=8,max_automatic=0)
    seq=0;retained=Counter();initial_reviews=[];target_reviews=[]
    for row in case['warmup']:
        decision=memory.query(prior.evidence(row,seq,version));seq+=1
        action='not_retained_initial_cap';reason=None
        if retained[row['speaker']]<3:
            action,reason=admission(memory,row,f'initial:{row["id"]}')
            if action!='retained':raise ValueError(f'Initial gallery unavailable: {reason}')
            retained[row['speaker']]+=1
        initial_reviews.append({'id':row['id'],'person':row['speaker'],'seconds':row['end']-row['start'],
                                'action':action,'reason':reason})
    for index,row in enumerate(target):
        decision=memory.query(prior.evidence(row,seq,version));seq+=1
        if index<prefix:
            action,reason=admission(memory,row,f'scheduled:{row["id"]}')
            target_reviews.append({'ordinal':index+1,'id':row['id'],'person':row['speaker'],
              'start':row['start'],'end':row['end'],'seconds':row['end']-row['start'],
              'before_assigned':decision.person_id,'before_status':decision.status,
              'before_correct':decision.person_id==row['speaker'],'action':action,'reason':reason,
              'person_reference_count_after':len(memory.anchors.get(row['speaker'],[]))})
    before=anchor_hash(memory);frozen_state=memory.summary();rows=[]
    for row in case['probes']:
        d=memory.query(prior.evidence(row,seq,version));seq+=1
        rows.append(output_row(row,d,case['known']))
    if before!=anchor_hash(memory) or memory.automatic:
        raise AssertionError('Later query changed the frozen reference bank')
    later=[r for r in rows if r['phase']=='later_chronological']
    unknown=[r for r in rows if r['phase']!='later_chronological']
    seconds=sum(r['seconds'] for r in target_reviews)
    initial_seconds=sum(r['seconds'] for r in initial_reviews)
    return {'scheduled_prefix':prefix,'initial_reviewed_clips':len(initial_reviews),
      'initial_reviewed_audio_seconds':initial_seconds,'initial_retained_anchors':sum(retained.values()),
      'initial_reviews_not_retained':sum(r['action']!='retained' for r in initial_reviews),
      'target_reviewed_clips':len(target_reviews),'target_reviewed_audio_seconds':seconds,
      'target_retained_anchors':sum(r['action']=='retained' for r in target_reviews),
      'target_capacity_rejections':sum(r['action']=='rejected_capacity' for r in target_reviews),
      'target_evidence_rejections':sum(r['action']=='rejected_evidence' for r in target_reviews),
      'target_duplicate_reviews':0,
      'target_already_correct_reviews':sum(r['before_correct'] for r in target_reviews),
      'total_reviewed_clips':len(initial_reviews)+len(target_reviews),
      'total_reviewed_audio_seconds':initial_seconds+seconds,
      'review_policy':'Chronological firstN, independent of scores/labels/outcomes; no replacement on rejection',
      'initial_reviews':initial_reviews,'target_reviews':target_reviews,
      'references_by_person':{p:len(refs) for p,refs in sorted(memory.anchors.items())},
      'frozen_anchor_state_sha256':before,'frozen_state':frozen_state,
      'later_bank_unchanged':True,'later':prior.summarize(later),
      'unknown_challenge':prior.summarize(unknown),
      'by_person':{p:prior.summarize([r for r in later if r['gold_person']==p]) for p in sorted(case['known'])},
      'by_person_review_effort':{p:{
        'initial_reviewed_clips':sum(r['person']==p for r in initial_reviews),
        'initial_reviewed_audio_seconds':sum(r['seconds'] for r in initial_reviews if r['person']==p),
        'initial_retained_anchors':retained[p],
        'target_reviewed_clips':sum(r['person']==p for r in target_reviews),
        'target_reviewed_audio_seconds':sum(r['seconds'] for r in target_reviews if r['person']==p),
        'target_retained_anchors':sum(r['person']==p and r['action']=='retained' for r in target_reviews),
        'target_rejections':sum(r['person']==p and r['action']!='retained' for r in target_reviews),
        'final_references':len(memory.anchors.get(p,[])),
        'later_correct':sum(r['gold_person']==p and r['assigned']==p for r in later),
        'later_queries':sum(r['gold_person']==p for r in later)} for p in sorted(case['known'])},
      'rows':rows}


def paired(candidate,baseline):
    b={r['id']:r for r in baseline};c={r['id']:r for r in candidate}
    if b.keys()!=c.keys():raise ValueError('Comparison query IDs differ')
    rescued=[];lost=[];new_wrong=[];new_unknown=[]
    for oid,row in c.items():
        old=b[oid]
        if (row['gold_person'],row['known'])!=(old['gold_person'],old['known']):raise ValueError('Comparison labels differ')
        if row['known'] and row['assigned']==row['gold_person'] and old['assigned']!=row['gold_person']:rescued.append(oid)
        if row['known'] and old['assigned']==row['gold_person'] and row['assigned']!=row['gold_person']:lost.append(oid)
        if row['assigned'] is not None and row['assigned']!=row['gold_person'] and row['assigned']!=old['assigned']:new_wrong.append(oid)
        if not row['known'] and row['assigned'] is not None and old['assigned'] is None:new_unknown.append(oid)
    return {'rescued_correct_ids':rescued,'lost_correct_ids':lost,'newly_wrong_ids':new_wrong,
            'new_unknown_false_accept_ids':new_unknown,'safety_regression_observed':bool(new_wrong or new_unknown)}


def load_inputs(protocol):
    check_frozen_inputs(protocol)
    profile=json.loads((ROOT/'audio_backend/profiles/eres2net-ami20-v1.json').read_text());model=profile['model']
    if model['dimension']!=512:raise ValueError('Expected unchanged512D encoder')
    rows=[];cache_hash=hashlib.sha256()
    for path in sorted((ROOT/'benchmarks/private_speaker_embeddings').glob('*.json')):
        row=json.loads(path.read_text())
        if row['model_sha256']==model['sha256']:
            if len(row['embedding'])!=512:raise ValueError('Cached dimension mismatch')
            cache_hash.update(path.name.encode());cache_hash.update(prior.digest(path).encode());rows.append(row)
    case=next(c for c in prior.cases(rows) if c['name']=='target_channel_unchanged')
    old=json.loads((ROOT/'reports/identity_learning_v1/replay.json').read_text())
    if cache_hash.hexdigest()!=old['selected_cache_aggregate_sha256']:
        raise ValueError('Original cache changed or is incomplete')
    base=next(c for c in old['cases'] if c['case']=='target_channel_unchanged' and c['strategy']=='centroid')
    abstention=next(c for c in old['cases'] if c['case']=='target_channel_corrected' and c['strategy']=='centroid')
    later=[r for r in case['probes'] if r.get('_phase')!='retrospective_open_set_challenge']
    if len(case['warmup'])!=18 or len(case['additional_warmup'])!=9 or len(later)!=51:
        raise ValueError('Required18/9/51 clip coverage unavailable')
    if [r['id'] for r in later]!=[r['id'] for r in base['rows'] if r['phase']=='later_chronological']:
        raise ValueError('Later query sequence differs from original frozen comparison')
    if len(case['probes'])!=89 or [r['id'] for r in case['probes']]!=[r['id'] for r in base['rows']]:
        raise ValueError('Complete known/unknown query sequence differs from original')
    prior.validate_case(case)
    for row in case['additional_warmup']:
        if row['recording_id']!='EN2001d' or row['microphone']!='far-field array1channel01' or not 2<=row['end']-row['start'] or row['end']>600:
            raise ValueError('Target warmup evidence unavailable or ineligible')
    if any(r['start']<600 for r in later):raise ValueError('Later evidence crosses cutoff')
    return case,base,abstention,model,cache_hash.hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'reports/identity_learning_review_budget_v1/replay.json');args=parser.parse_args()
    started=time.monotonic();protocol=json.loads(PROTOCOL.read_text())
    if protocol['fixed_prefix_clip_counts']!=list(PREFIXES):raise ValueError('Protocol budget mismatch')
    case,old,abstention,model,cache_hash=load_inputs(protocol)
    version=model['embedding_version']+'@sha256:'+model['sha256']
    results=[run_prefix(case,n,version,512) for n in PREFIXES]
    for x,y in zip(results[0]['rows'],old['rows']):
        if x['id']!=y['id'] or x['assigned']!=y['assigned'] or x['status']!=y['status']:
            raise AssertionError('Zero-review baseline differs from unchanged original')
        if x['score'] is not None and abs(x['score']-y['score'])>1e-12:
            raise AssertionError('Original scoring changed')
    for result in results:
        result['versus_zero_review']=paired(result['rows'],results[0]['rows'])
        result['versus_abstention_only']=paired(result['rows'],abstention['rows'])
    check_frozen_inputs(protocol)
    report={'protocol_id':protocol['protocol_id'],'protocol_sha256':prior.digest(PROTOCOL),
      'runner_sha256':prior.digest(Path(__file__)),'memory_module_sha256':prior.digest(ROOT/'audio_backend/experimental_identity_memory.py'),
      'selected_cache_aggregate_sha256':cache_hash,'model_version':version,'dimension':512,
      'fixed_budgets':list(PREFIXES),'coverage':{'initial_clips':18,'target_prefix_available':9,'later_known_queries':51,'retrospective_unknown_queries':38,
        'duplicate_source_clips':0,'cutoff_straddling_clips':0,'unavailable_or_substituted_queries':0},
      'initial_protocol':'Same curated initial known gallery,3 retained anchors/person and all18 initial reviews charged',
      'scope':'Exploratory reused same-recording curated oracle-clean onboarding; no new cross-day evidence, production-purity or production acceptance claim',
      'no_budget_selected_for_promotion':True,'automatic_memory_enabled':False,
      'original_inputs_preserved_and_hash_verified':True,'zero_prefix_reproduces_original_baseline':True,
      'earlier_abstention_only':{k:abstention[k] for k in ['initial_reviewed_clips','initial_reviewed_audio_seconds','additional_reviewed_clips','additional_reviewed_audio_seconds','additional_retained_anchors','summary']},
      'confidence_note':'Wilson intervals in summaries are descriptive. People/session correlations mean clips and repeated arms are not independent;0/38 unknown accepts does not establish1%risk.',
      'review_time_note':'Audio seconds exclude navigation, context playback, repeats or human deliberation. They are not wall-clock review time.',
      'capacity_rule':'8 references/person; scheduled rejected reviews are charged, logged and never replaced',
      'results':results,'elapsed_seconds':time.monotonic()-started,
      'process_peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    for r in results:
        print(r['scheduled_prefix'],'reviews',r['total_reviewed_clips'],r['total_reviewed_audio_seconds'],
          'anchors',r['target_retained_anchors'],'correct',r['later']['correct_known'],'review',r['later']['known_review_requests'],
          'wrong',r['later']['wrong_person_assignments'],'new_wrong',len(r['versus_zero_review']['newly_wrong_ids']),
          'unknown_accepts',r['unknown_challenge']['unknown_false_accepts'],flush=True)
    print(args.output)


if __name__=='__main__':main()
