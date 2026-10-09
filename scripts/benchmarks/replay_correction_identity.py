#!/usr/bin/env python3
"""Cache-only, model-free causal correction-policy diagnostic. Never runs inference.

All gold fields remain in this evaluator. Memory sees vectors/provenance and only
explicitly charged early human labels. Public output uses field allowlists, not
serialization of Evidence/Correction objects (which contain private vectors).
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import resource
import sqlite3
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from audio_backend.experimental_identity_memory import CorrectionMemory, Evidence

STRATEGIES=('centroid','verified_multi_anchor','anchored_auto')


def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
    return h.hexdigest()


def date(value):
    if value is None:return None
    for fmt in ('%Y-%m-%d','%d-%m-%Y','%d-%m-%y'):
        try:return datetime.strptime(value,fmt).date().isoformat()
        except ValueError:pass
    raise ValueError(f'Unrecognized source date: {value}')


def key(row):
    return (date(row.get('recording_date')) or '',row['recording_id'],float(row['start']),float(row['end']),row['id'])


def evidence(row, seq, version):
    # Same source utterance/microphone views share the unit. Different cropped
    # files do not imply independent observations. No gold ID enters this object.
    unit=f"{row['recording_id']}:{row['start']:.6f}:{row['end']:.6f}"
    return Evidence(row['id'],seq,tuple(row['embedding']),version,unit,
                    row['end']-row['start'],True,'oracle_clean_entire_clip')


def wilson(k,n):
    if not n:return None
    z=1.96;p=k/n;d=1+z*z/n;c=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0,c-h),min(1,c+h)]


def summarize(rows):
    known=[r for r in rows if r['known']];unknown=[r for r in rows if not r['known']]
    correct=sum(r['assigned']==r['gold_person'] for r in known)
    wrong=[r for r in rows if r['assigned'] is not None and r['assigned']!=r['gold_person']]
    accepted=sum(r['assigned'] is not None for r in rows)
    review=sum(r['assigned'] is None for r in known)
    unknown_accepts=sum(r['assigned'] is not None for r in unknown)
    return {'query_count':len(rows),'known_queries':len(known),'unknown_queries':len(unknown),
        'correct_known':correct,'known_accepted_recall':correct/len(known) if known else None,
        'known_recall_wilson95_descriptive':wilson(correct,len(known)),
        'known_review_requests':review,'known_review_fraction':review/len(known) if known else None,
        'known_wrong_assignments':sum(r['known'] for r in wrong),'wrong_person_assignments':len(wrong),
        'wrong_among_accepted':len(wrong)/accepted if accepted else None,
        'wrong_among_accepted_wilson95_descriptive':wilson(len(wrong),accepted),
        'unknown_false_accepts':unknown_accepts,'unknown_false_accept_fraction':unknown_accepts/len(unknown) if unknown else None,
        'unknown_false_accept_wilson95_descriptive':wilson(unknown_accepts,len(unknown)),
        'wrong_person_query_clip_seconds':sum(r['seconds'] for r in wrong),
        'accepted_assignments':accepted,'all_abstentions':sum(r['assigned'] is None for r in rows),
        'automatic_supported':sum(r['status']=='automatic_supported' for r in rows),
        'new_identity_rows':0,'same_person_extra_identity_rows':0,'canonical_merge_operations':0,
        'known_novelty_rejections':sum(r['known'] and r['status']=='unrecognized' for r in rows)}


def validate_case(case):
    initial=case['warmup']+case.get('additional_warmup',[])
    all_rows=initial+case['probes']
    if len({r['id'] for r in all_rows})!=len(all_rows):
        raise ValueError('Duplicate cached query across warmup/evaluation')
    units=[(r['recording_id'],r['start'],r['end']) for r in all_rows]
    if len(set(units))!=len(units):
        raise ValueError('Repeated source utterance/microphone view')
    by_recording=defaultdict(list)
    for row in all_rows:by_recording[row['recording_id']].append(row)
    for rows in by_recording.values():
        latest_end=-math.inf
        for row in sorted(rows,key=lambda r:r['start']):
            if row['start']<latest_end:raise ValueError('Overlapping cached evidence cannot establish independent replay queries')
            latest_end=row['end']
    for rows in (case['warmup'],case.get('additional_warmup',[])):
        if rows!=sorted(rows,key=key):raise ValueError('Nonchronological warmup')
    later=[r for r in case['probes'] if r.get('_phase')!='retrospective_open_set_challenge']
    if later!=sorted(later,key=key):raise ValueError('Nonchronological probes')
    if initial and later and max(key(r) for r in initial)>=min(key(r) for r in later):
        raise ValueError('Future warmup label would leak into later recognition')


def run_case(case, budget, strategy, version, dimension, extra_plan=()):
    validate_case(case)
    m=CorrectionMemory(version,dimension,strategy)
    retained=Counter();seq=0;reviewed=0;early_predictions=[]
    for row in case['warmup']:
        decision=m.query(evidence(row,seq,version));seq+=1
        if budget:
            # Every presented early clip is reviewed, including repeats beyond
            # the retained per-person cap. No free oracle label selection.
            reviewed+=1
            if row['speaker'] in case['known'] and retained[row['speaker']]<budget:
                m.verify_latest(row['speaker'],f'initial:{row["id"]}',whole_evidence_verified=True)
                retained[row['speaker']]+=1
        early_predictions.append(decision.status)
    added=0;extra_reviewed=0;extra_ids={r['id'] for r in extra_plan}
    extra_retained=Counter()
    for row in case.get('additional_warmup',[]):
        m.query(evidence(row,seq,version));seq+=1
        if row['id'] in extra_ids:
            extra_reviewed+=1
            if extra_retained[row['speaker']]<2:
                m.verify_latest(row['speaker'],f'correction:{row["id"]}',whole_evidence_verified=True)
                extra_retained[row['speaker']]+=1;added+=1
    initial=m.summary();results=[];contamination=[]
    # Gold for admission audit is available only to this evaluator, not memory.
    labels={r['id']:r['speaker'] for r in case['warmup']+case.get('additional_warmup',[])+case['probes']}
    for row in case['probes']:
        decision=m.query(evidence(row,seq,version));seq+=1
        results.append({'id':row['id'],'recording_id':row['recording_id'],'recording_date':date(row.get('recording_date')),
          'start':row['start'],'end':row['end'],'seconds':row['end']-row['start'],
          'gold_person':row['speaker'],'known':row['speaker'] in case['known'],
          'phase':row.get('_phase','later_chronological'),
          'assigned':decision.person_id,'status':decision.status,'score':decision.best_score,
          'anchor_score':decision.anchor_score,'margin':decision.margin,
          'automatic_support_ids':list(decision.automatic_ids)})
        for person,items in m.automatic.items():
            for item in items:
                if labels[item.observation_id]!=person:
                    contamination.append((item.observation_id,person,labels[item.observation_id]))
    by_recording={rid:summarize([r for r in results if r['recording_id']==rid]) for rid in sorted({r['recording_id'] for r in results})}
    by_person={p:summarize([r for r in results if r['gold_person']==p]) for p in sorted({r['gold_person'] for r in results})}
    chronological=[r for r in results if r['phase']=='later_chronological']
    midpoint=len(chronological)//2
    return {'case':case['name'],'strategy':strategy,'retained_initial_anchors_per_person_cap':budget,
            'initial_reviewed_clips':reviewed,'initial_retained_anchors':sum(retained.values()),
            'initial_reviewed_audio_seconds':sum(r['end']-r['start'] for r in case['warmup']) if budget else 0,
            'initial_reviews_not_retained':reviewed-sum(retained.values()),
            'additional_reviewed_clips':extra_reviewed,'additional_retained_anchors':added,
            'additional_reviewed_audio_seconds':sum(r['end']-r['start'] for r in case.get('additional_warmup',[]) if r['id'] in extra_ids),
            'additional_reviews_not_retained':extra_reviewed-added,
            'duplicate_queries':'Rejected before replay, including duplicate microphone views; no duplicated review hidden',
            'warmup_cutoff':case['cutoff'],'warmup_excluded_early_probe_clips':case.get('excluded',0),
            'initial_state':initial,'final_state':m.summary(),'summary':summarize(results),
            'by_recording':by_recording,'by_person':by_person,
            'later_halves':[summarize(chronological[:midpoint]),summarize(chronological[midpoint:])],
            'contaminated_auto_admissions':[{'id':oid,'assigned_person':p,'gold_person':g} for oid,p,g in sorted(set(contamination))],
            'rows':results}


def extra_reviews(case,version,dimension):
    m=CorrectionMemory(version,dimension,'centroid');retained=Counter();seq=0;plan=[];adds=Counter()
    for row in case['warmup']:
        m.query(evidence(row,seq,version));seq+=1
        if retained[row['speaker']]<3:
            m.verify_latest(row['speaker'],f'initial:{row["id"]}',whole_evidence_verified=True);retained[row['speaker']]+=1
    for row in case['additional_warmup']:
        d=m.query(evidence(row,seq,version));seq+=1
        if d.person_id is None and len(plan)<10:
            plan.append(row)
            if adds[row['speaker']]<2:
                m.verify_latest(row['speaker'],f'extra:{row["id"]}',whole_evidence_verified=True);adds[row['speaker']]+=1
    return plan


def cases(rows):
    cases=[]
    original=[r for r in rows if not r.get('split')]
    warm=[r for r in original if r['recording_id']=='EN2001a']
    first=defaultdict(list)
    for r in sorted(warm,key=key):first[r['speaker']].append(r)
    cutoff=max(key(rs[2]) for rs in first.values())
    warm=[r for r in sorted(warm,key=key) if key(r)<=cutoff]
    known=set(first);unknown=[dict(r,_phase='retrospective_open_set_challenge') for r in original if r['recording_id']=='ES2004a']
    for mic,name in [('Mix-Headset','original_headset'),('far-field array1channel01','original_farfield')]:
        later=sorted([r for r in original if r['recording_id'] in ('EN2001d','EN2001e') and r['microphone']==mic],key=key)
        common={'name':name,'warmup':warm,'probes':later+sorted(unknown,key=key),'known':known,'cutoff':cutoff[:3]}
        cases.append(common)
        cases.append(dict(common,name=name+'_early_corrections',additional_warmup=sorted([r for r in original if r['recording_id']=='EN2001b'],key=key)))
    ff=sorted([r for r in original if r['recording_id']=='EN2001d' and r['microphone']!='Mix-Headset'],key=key)
    prefix=[r for r in ff if r['end']<=600.0];late=[r for r in ff if r['start']>=600.0]
    crossing=len(ff)-len(prefix)-len(late)
    for corrected in (False,True):
        cases.append({'name':'target_channel_'+('corrected' if corrected else 'unchanged'),
            'warmup':warm,'additional_warmup':prefix,'hold_additional_labels':not corrected,
            'probes':late+sorted(unknown,key=key),'known':known,'cutoff':['2005-03-01','EN2001d',600.0],
            'excluded':crossing})
    fresh=[r for r in rows if r.get('path','').startswith('benchmarks/ami/fresh_holdout/')]

    fresh_enroll=sorted([r for r in fresh if r['split']=='enrollment'],key=key)
    fresh_cutoff=max(key(r) for r in fresh_enroll)
    people=sorted({r['speaker'] for r in fresh});unknown_people=set(people[-2:])
    for mic in ('Mix-Headset','Array1-01'):
        probes=[r for r in fresh if r['split']=='probe' and r['microphone']==mic]
        later=sorted([r for r in probes if key(r)>fresh_cutoff],key=key)
        for protocol in ('closed','open'):
            known=set(people)-(unknown_people if protocol=='open' else set())
            cases.append({'name':f'reused_fresh_{mic}_{protocol}',
                'warmup':[r for r in fresh_enroll if r['speaker'] in known],
                'probes':later,'known':known,'cutoff':fresh_cutoff[:3],'excluded':len(probes)-len(later)})
    zh=[r for r in rows if r['recording_id'].startswith('R')]
    for rid in sorted({r['recording_id'] for r in zh}):
        local=sorted([r for r in zh if r['recording_id']==rid],key=key)
        initial=[r for r in local if r.get('split')=='enrollment'];cutoff=max(key(r) for r in initial)
        warm=[r for r in local if key(r)<=cutoff];later=[r for r in local if key(r)>cutoff]
        for protocol in ('closed','open'):
            people=sorted({r['speaker'] for r in local});known=set(people)-(set(people[-1:]) if protocol=='open' else set())
            cases.append({'name':f'mandarin_{rid}_{protocol}','warmup':[r for r in warm if r['speaker'] in known],
                'probes':later,'known':known,'cutoff':cutoff[:3],
                'excluded':sum(r.get('split')=='probe' for r in warm)})
    return cases


def poison_demo(version):
    result={}
    for strategy in ('verified_multi_anchor','anchored_auto'):
        m=CorrectionMemory(version,3,strategy)
        m.query(Evidence('anchor-a',0,(1,0,0),version,'a',3,True));m.verify_latest('A','seed',whole_evidence_verified=True)
        x=(.66,0,math.sqrt(1-.66**2));y=(.46,0,math.sqrt(1-.46**2))
        dx=m.query(Evidence('unknown-x',1,x,version,'x',3,True))
        dy=m.query(Evidence('unknown-y',2,y,version,'y',3,True))
        result[strategy]={'first_impostor_prediction':dx.person_id,'later_impostor_prediction':dy.person_id,
                          'later_status':dy.status,'later_anchor_cosine':dy.anchor_score,'later_score':dy.best_score}
    return result


def cache_coverage():
    dbpath=ROOT/'reports/endurance_v02/identity_diagnostics/final/candidate.sqlite3'
    if not dbpath.exists():return {'available':False}
    db=sqlite3.connect(f'file:{dbpath}?mode=ro&immutable=1',uri=True)
    # Use the already validated immutable diagnostic as denominator source:
    obs=db.execute('select count(*),sum(matched_speaker_id is not null) from speaker_observations').fetchone()
    db.close()
    return {'database_path':str(dbpath.relative_to(ROOT)),'database_sha256':digest(dbpath),
       'retained_observation_rows':obs[0],'retained_matched_rows':obs[1],
       'validated_query_denominators':{'all':4389,'provisional':2209,'unknown_ambiguous':787,'unknown_uncertain':384,'generic_unknown':1009},
       'validated_joined_retained_queries':{'all':1683,'provisional':512,'unknown_ambiguous':787,'unknown_uncertain':384,'generic_unknown':0},
       'source':'reports/endurance_v02/identity_diagnostics/final/identity-report.json',
       'decision':'Not used for learning accuracy: selection-biased survivors; missing exact pooled interval lineage and labels. Counts are existing validated diagnostic, not reconstructed future labels.'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'reports/identity_learning_v1/replay.json');a=parser.parse_args()
    started=time.monotonic()
    protocol=ROOT/'reports/identity_learning_v1/protocol.json';profile_path=ROOT/'audio_backend/profiles/eres2net-ami20-v1.json';profile=json.loads(profile_path.read_text())
    model=profile['model'];version=model['embedding_version']+'@sha256:'+model['sha256']
    rows=[];cache_hash=hashlib.sha256();other=0
    for path in sorted((ROOT/'benchmarks/private_speaker_embeddings').glob('*.json')):
        row=json.loads(path.read_text())
        if row['model_sha256']!=model['sha256']:other+=1;continue
        if len(row['embedding'])!=model['dimension']:raise ValueError('Bad cached embedding dimension')
        cache_hash.update(path.name.encode());cache_hash.update(digest(path).encode());rows.append(row)
    reports=[]
    for case in cases(rows):
        extra=extra_reviews(case,version,model['dimension']) if case.get('additional_warmup') and not case.get('hold_additional_labels') else []
        for budget in ([3] if case.get('additional_warmup') else [0,1,3]):
            for strategy in STRATEGIES:
                r=run_case(case,budget,strategy,version,model['dimension'],extra)
                reports.append(r)
                print(case['name'],budget,strategy,r['summary']['correct_known'],r['summary']['known_queries'],
                      'wrong',r['summary']['wrong_person_assignments'],'review',r['summary']['known_review_requests'],flush=True)
    pairs=[]
    for r in reports:
        if r['strategy']=='centroid':continue
        for baseline in (['centroid','verified_multi_anchor'] if r['strategy']=='anchored_auto' else ['centroid']):
            b=next(x for x in reports if x['case']==r['case'] and x['retained_initial_anchors_per_person_cap']==r['retained_initial_anchors_per_person_cap'] and x['strategy']==baseline)
            br={x['id']:x for x in b['rows']};new_wrong=[];rescued=[];lost=[]
            for row in r['rows']:
                before=br[row['id']]
                if row['assigned'] is not None and row['assigned']!=row['gold_person'] and before['assigned']!=row['assigned']:new_wrong.append(row['id'])
                if row['known'] and row['assigned']==row['gold_person'] and before['assigned']!=row['gold_person']:rescued.append(row['id'])
                if row['known'] and before['assigned']==row['gold_person'] and row['assigned']!=row['gold_person']:lost.append(row['id'])
            pairs.append({'case':r['case'],'budget':r['retained_initial_anchors_per_person_cap'],'strategy':r['strategy'],'baseline_strategy':baseline,
                          'new_wrong_ids':new_wrong,'rescued_ids':rescued,'lost_correct_ids':lost,
                          'promotion_blocked_by_new_wrong':bool(new_wrong)})
    report={'protocol_id':'correction-memory-v1-2026-10-09','protocol_sha256':digest(protocol),
       'target_channel_protocol_sha256':digest(ROOT/'reports/identity_learning_v1/target_channel_protocol.json'),
       'model_version':version,'cache_files_selected':len(rows),'other_model_files_excluded':other,
       'selected_cache_aggregate_sha256':cache_hash.hexdigest(),'profile_sha256':digest(profile_path),
       'module_sha256':digest(ROOT/'audio_backend/experimental_identity_memory.py'),'evaluator_sha256':digest(Path(__file__)),
       'data_reuse':'All cohorts previously evaluated; no fresh confirmation and no fitting from these new outcomes.',
       'enrollment_curation':'Open-set scenarios preselect the known roster and omit unknown-person enrollment clips. They are curated gallery/enrollment diagnostics, not exhaustive unlabelled stream discovery or real user review-per-hour estimates.',
       'review_accounting':'Early clip reviews include every presented unit, even if not retained.1/3 is retained anchors/person, not an uncharged person-aware selection. Later review requests count known abstentions only; confidently wrong decisions are separately charged as hidden harmful errors, not successful burden reduction.',
       'fragmentation_accounting':'This closed verified registry creates no canonical identities/merges. Zero extra IDs is structural, not identity-discovery success. Known novelty rejections and all abstentions expose unresolved split/review pressure. Production automatic discovery/actual merge DER are not measured.',
       'chronology':'Official dates parsed explicitly; original known probes follow warmup dates; earlier ES2004a is separately labeled retrospective unknown challenge. Fresh probes before global enrollment cutoff are excluded and counted. Mandarin is within-recording only. No future label enters policy.',
       'scope':'Oracle gold-clean cached query clips, not production pooled-window purity, DER, clinical/cold or long-term population evidence.',
       'confidence_note':'Wilson intervals descriptive only: clips share people/sessions and are not independent; zero errors does not establish1% risk.',
       'poison_adversarial_demo':poison_demo(version),'historical_production_cache_coverage':cache_coverage(),
       'cases':reports,'paired_comparisons':pairs,
       'elapsed_seconds':time.monotonic()-started,'process_peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+'\n');print(a.output)


if __name__=='__main__':main()
