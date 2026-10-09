#!/usr/bin/env python3
"""Fixed first30 genuine short queries, causal persisted gallery, no bank writes."""
import collections,json,sqlite3,pathlib,hashlib,time,resource,math,sys
import numpy as np
import soundfile as sf
import sherpa_onnx as sherpa
from replay_epoch_identity import ROOT,SOURCE,added_breakdown
from score_predictions import diarization
sys.path.insert(0,str(ROOT))
from audio_backend.speaker_evidence import subtract_intervals

def main():
 started=time.monotonic();db=sqlite3.connect('file:'+str(ROOT/'reports/endurance_v02/candidate_native_v02/checkpoint/jobs.sqlite3')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('BEGIN')
 assert db.execute('select count(*) from corrections').fetchone()[0]==0
 assert db.execute('select count(*) from speaker_examples').fetchone()[0]==0
 assert db.execute('select count(*) from speakers where merged_into is not null').fetchone()[0]==0
 profiles=[dict(r) for r in db.execute('select * from speakers')]
 creates={r['entity_id']:r['c'] for r in db.execute("select entity_id,min(cursor) c from result_changes where entity='speakers' group by entity_id")}
 turn_cursors={int(r['entity_id']):r['c'] for r in db.execute("select entity_id,min(cursor) c from result_changes where entity='speaker_turns' group by entity_id")}
 paths={r['id']:r['path'] for r in db.execute('select id,path from chunks')};db.close()
 predictions=[json.loads(p.read_text()) for p in sorted((SOURCE/'predictions').glob('*.json')) if (ROOT/'benchmarks/alimeeting/gold'/p.name).exists()]
 candidates=[]
 for pred in predictions:
  groups=collections.defaultdict(list)
  for t in pred['speaker_turns']:groups[(t['chunk_id'],t['speaker_window_start'])].append(t)
  for (chunk,window),rows in sorted(groups.items(),key=lambda x:x[0][1]):
   firstcursor=min(turn_cursors[t['id']] for t in rows)
   for local in sorted({r['speaker_local_id'] for r in rows}):
    own=[r for r in rows if r['speaker_local_id']==local]
    if any(r['speaker_id'] is not None or r['speaker_status']!='unknown' for r in own):continue
    clean=subtract_intervals([(r['concatenation_start'],r['concatenation_end']) for r in own],[(r['concatenation_start'],r['concatenation_end']) for r in rows if r['speaker_local_id']!=local])
    samples=sum(max(0,math.floor(b*16000)-math.ceil(a*16000)) for a,b in clean)
    if 16000<=samples<32000:candidates.append(dict(recording_id=pred['recording_id'],chunk=chunk,window=window,local=local,clean=clean,seconds=samples/16000,turn_ids=[r['id'] for r in own],before_cursor=firstcursor))
 candidates=sorted(candidates,key=lambda x:(x['window'],x['local']))[:30]
 model=ROOT/'models/speaker.onnx';modelhash=hashlib.sha256(model.read_bytes()).hexdigest();version='eres2net-base-zh-cn-3dspeaker-16k-v1@sha256:'+modelhash
 policy={'selection':'first30 chronological unknown native core/slot groups with genuine overlap-subtracted1–2s audio; complete frozen Mandarin source projections only','match_threshold':.55,'margin':.10,'minimum_query_seconds':1.,'minimum_new_enrollment_seconds':2.,'new_profiles':False,'profile_updates':False,'gallery':'public creation cursor strictly before first raw turn in current window; no corrections/examples/merges present','evidence':'core-only native intervals; historical analyzer context is not reconstructed','threads':1}
 (ROOT/'reports/short-acoustic-query-plan.json').write_text(json.dumps({'policy':policy,'trials':candidates,'model_sha256':modelhash},indent=2))
 extractor=sherpa.SpeakerEmbeddingExtractor(sherpa.SpeakerEmbeddingExtractorConfig(model=str(model),num_threads=1,provider='cpu'));accepted={};trials=[]
 for c in candidates:
  pieces=[]
  with sf.SoundFile(paths[c['chunk']]) as audio:
   assert audio.samplerate==16000 and audio.channels==1
   for a,b in c['clean']:
    lo=math.ceil(a*16000);hi=math.floor(b*16000);audio.seek(lo);pieces.append(audio.read(hi-lo,dtype='float32'))
  wave=np.concatenate(pieces);stream=extractor.create_stream();stream.accept_waveform(sample_rate=16000,waveform=wave);stream.input_finished()
  if not extractor.is_ready(stream):trials.append(c|{'decision':'extractor_not_ready'});continue
  vec=np.asarray(extractor.compute(stream));vec/=np.linalg.norm(vec);gallery=[p for p in profiles if creates[p['id']]<c['before_cursor'] and p['embedding_version']==version];scores=sorted([(float(np.dot(vec,json.loads(p['embedding']))),p['id']) for p in gallery],reverse=True)
  best,pid=scores[0] if scores else (None,None);margin=best-scores[1][0] if len(scores)>1 else 2.;ok=best is not None and best>=.55 and margin>=.10
  trials.append(c|{'gallery_count':len(gallery),'best_score':best,'margin':margin,'accepted_speaker_id':pid if ok else None,'decision':'fresh_short_acoustic_match' if ok else 'unknown'})
  if ok:
   for tid in c['turn_ids']:accepted[tid]=pid
 results=[]
 for pred in predictions:
  gold=json.loads((ROOT/'benchmarks/alimeeting/gold'/f"{pred['recording_id']}.json").read_text());rows=[dict(r,speaker=r.get('speaker_id')) for r in pred['speaker_turns']];base=diarization(gold['segments'],rows);after=[]
  for row in rows:
   if row['id'] in accepted:row=dict(row,speaker_id=accepted[row['id']],speaker=accepted[row['id']],speaker_status='fresh_short_acoustic_match',continuity_added=True)
   after.append(row)
  added,people=added_breakdown(gold['segments'],after,base['mapping']);results.append({'recording_id':pred['recording_id'],'before':base,'after':diarization(gold['segments'],after),'added_assignment_fixed_baseline_mapping':added,'per_assigned_reference_person':people})
 for trial in trials:
  if not trial.get('accepted_speaker_id'):continue
  pred=next(p for p in predictions if p['recording_id']==trial['recording_id']);gold=json.loads((ROOT/'benchmarks/alimeeting/gold'/f"{trial['recording_id']}.json").read_text())['segments'];evidence_overlap=collections.Counter();prior_overlap=collections.Counter()
  for a,b in trial['clean']:
   for ref in gold:evidence_overlap[ref['speaker']]+=max(0,min(b-pred['source_start_seconds'],ref['end'])-max(a-pred['source_start_seconds'],ref['start']))
  for turn in pred['speaker_turns']:
   if turn['speaker_id']==trial['accepted_speaker_id'] and turn['speaker_window_start']<trial['window']:
    for ref in gold:prior_overlap[ref['speaker']]+=max(0,min(turn['end'],ref['end'])-max(turn['start'],ref['start']))
  fixed=next(r for r in results if r['recording_id']==trial['recording_id'])['before']['mapping'].get(trial['accepted_speaker_id'])
  trial['evaluation_only']={'fixed_baseline_person':fixed,'query_clean_gold_overlap_seconds':dict(evidence_overlap),'prior_accepted_uuid_gold_overlap_seconds':dict(prior_overlap),'query_dominant_person':max(evidence_overlap,key=evidence_overlap.get),'prior_uuid_dominant_person':max(prior_overlap,key=prior_overlap.get) if prior_overlap else None,'existing_unmapped_split':fixed is None}
 output={'policy':policy,'model_sha256':modelhash,'trials':trials,'results':results,'cer_changed':False,'new_identity_splits':0,'wall_seconds':time.monotonic()-started,'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,'limitations':'Small development diagnostic. No independent confirmation. Baseline global-ID mapping fixed for added assignment assessment. No transcript edits or profile writes.'}
 (ROOT/'reports/short-acoustic-query-replay.json').write_text(json.dumps(output,indent=2));print(json.dumps({'trials':len(trials),'accepted_queries':sum(t.get('accepted_speaker_id') is not None for t in trials),'results':[{k:v for k,v in r.items() if k in ('recording_id','added_assignment_fixed_baseline_mapping')} for r in results],'wall_seconds':output['wall_seconds'],'rss_mib':output['peak_rss_mib']},indent=2))
if __name__=='__main__':main()
