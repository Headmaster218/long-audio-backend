#!/usr/bin/env python3
"""Independent saved-only native-selector geometry report. No product imports/models."""
import argparse,hashlib,json,re
from pathlib import Path
RATE=16000

def read(p):return json.loads(Path(p).read_text())
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=True,allow_nan=False,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()
def union(iv):
 out=[]
 for a,b in sorted(iv):
  assert type(a) is int and type(b) is int and a<b
  if out and a<=out[-1][1]:out[-1]=(out[-1][0],max(b,out[-1][1]))
  else:out.append((a,b))
 return out
def inter(x,y):return union([(max(a,c),min(b,d))for a,b in union(x)for c,d in union(y)if max(a,c)<min(b,d)])
def minus(x,y):
 out=[]
 for a,b in union(x):
  cursor=a
  for c,d in union(y):
   if d<=cursor or c>=b:continue
   if c>cursor:out.append((cursor,min(c,b)))
   cursor=max(cursor,d)
   if cursor>=b:break
  if cursor<b:out.append((cursor,b))
 return out
def size(x):return sum(b-a for a,b in union(x))
def ratio(n,d):return n/d if d else None

def overlap_components(label_intervals):
 points=sorted({v for pairs in label_intervals.values() for pair in pairs for v in pair});out=[]
 for a,b in zip(points,points[1:]):
  labels=tuple(sorted(k for k,v in label_intervals.items() if any(c<b and d>a for c,d in v)))
  if len(labels)>=2:
   if out and out[-1][1]==a and out[-1][2]==labels:out[-1]=(out[-1][0],b,labels)
   else:out.append((a,b,labels))
 return out

def reconstruct(doc,inp,N):
 source=inp['source'];work=inp['work'];chunks=doc['chunks'];assert doc['schema']=='recorder-native-turn-export-v1' and doc['source_content_binding']==source and doc['frames']==N==source['frames'];assert len(chunks)==len(work)
 assert source['target']['capture_origin_frame']==0
 assert set(inp['source_hashes'])=={w['chunk_id'] for w in work}
 accepted=[];detected=[];public=[];pads=[];cursor=0;coreord=0
 for ci,(w,ch) in enumerate(zip(work,chunks),1):
  start,end=w['span_start_frame'],w['span_end_frame'];assert start==cursor<end<=N and end-start<=168000;cursor=end
  assert ch['schema']=='bound-native-turn-evidence-v1' and ch['chunk_id']==w['chunk_id'] and ch['frames']==end-start and ch['capture_start_frame']==ch['timeline_start_frame']==start and ch['session_id']==source['target']['session_id'] and ch['source_sha256']==inp['source_hashes'][w['chunk_id']]
  assert ch['evidence_sha256']==digest({k:v for k,v in ch.items() if k!='evidence_sha256'})
  cc=0
  for co in ch['cores']:
   coreord+=1;a,b=co['start_frame'],co['end_frame'];assert a==cc<b<=end-start;cc=b;a+=start;b+=start;accepted.append((a,b));assert co['status'] in ('retained','existing','empty','unknown_only')
   assert co['scope']['session_id']==ch['session_id'] and all(re.fullmatch('[a-f0-9]{64}',co[k])for k in ['slot','proof_sha256'])
   labels={};ordinals=set()
   for t in co['turns']:
    lo,hi=t['start_frame'],t['end_frame'];assert type(lo)is int and type(hi)is int and a<=lo<hi<=b
    assert t['speaker_local_id'] in {co['scope']['epoch']+':speaker_'+str(i)for i in range(1,9)} and t['ordinal']not in ordinals;ordinals.add(t['ordinal']);labels.setdefault(t['speaker_local_id'],[]).append((lo,hi))
   for lo,hi,ids in overlap_components(labels):
    detected.append(dict(start_frame=lo,end_frame=hi,chunk_id=ch['chunk_id'],core_slot=co['slot'],detected_source_count=len(ids)))
    pad=(max(a,lo-RATE),min(b,hi+RATE));pads.append(pad)
    public.append(dict(region_ordinal=len(public)+1,chunk_ordinal=ci,core_ordinal=coreord,start_frame=lo,end_frame=hi,detected_source_count=len(ids),context_start_frame=pad[0],context_end_frame=pad[1]))
  assert cc==end-start
 assert cursor==N
 allranges=[]
 for a,b in union(pads):
  while a<b:z=min(a+320000,b);allranges.append((a,z));a=z
 return accepted,detected,public,allranges

def metrics(A,B,O,D,U,W):
 vals={'accepted_source_frames':size(A),'annotated_speech_support_frames':size(B),'annotated_overlap_frames':size(O),'detected_overlap_frames':size(D),'detected_true_overlap_frames':size(inter(D,O)),'missed_annotated_overlap_frames':size(minus(O,D)),'detected_nonoverlap_frames':size(minus(D,O)),'selected_support_frames':size(W),'selected_annotated_overlap_frames':size(inter(W,O)),'selection_missed_annotated_overlap_frames':size(minus(O,W)),'selected_nonoverlap_frames':size(minus(W,O)),'selected_native_detection_frames':size(inter(W,D)),'unselected_native_detection_frames':size(minus(D,W)),'context_frames_outside_native_detection':size(minus(W,D)),'selected_single_reference_support_frames':size(inter(W,minus(B,O))),'selected_outside_any_reference_support_frames':size(minus(W,B)),'prebudget_support_frames':size(U),'budget_omitted_support_frames':size(minus(U,W)),'budget_omitted_annotated_overlap_frames':size(inter(minus(U,W),O)),'annotated_overlap_outside_accepted_source_frames':size(minus(O,A))}
 vals.update(detection_annotation_recall=ratio(vals['detected_true_overlap_frames'],size(O)),detection_annotation_precision=ratio(vals['detected_true_overlap_frames'],size(D)),selection_annotation_overlap_coverage=ratio(vals['selected_annotated_overlap_frames'],size(O)),selected_overlap_fraction=ratio(vals['selected_annotated_overlap_frames'],size(W)),selected_support_per_covered_overlap=ratio(size(W),vals['selected_annotated_overlap_frames']))
 return vals

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--index',required=True);ap.add_argument('--manifest',required=True);ap.add_argument('--protocol',required=True);ap.add_argument('--source-manifest',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 index=read(a.index);manifest=read(a.manifest);protocol=read(a.protocol);source_manifest=read(a.source_manifest)
 assert sha(a.protocol)=='2c1ddfb7df4b31c7f3a56c2ff34bb3db2b1653368bc9e308b9adbe4eb166aa3f'
 assert sha(a.manifest)==protocol['existing_result_preservation']['input_manifest_sha256']
 assert index['status']=='completed' and index['saved_files_unchanged'] and index['no_models'] and index['no_candidate_run'] and index['no_gold_used']
 assert sha(a.source_manifest)==index['source_manifest_sha256'];source_root=Path(source_manifest['source'])
 for rel,h in index['source_module_pins'].items():assert sha(source_root/rel)==h
 for path,pin in index['saved_file_pins'].items():assert sha(path)==pin[-1]
 cases={c['case_id']:c for c in manifest['cases']};assert [c['case_id']for c in index['cases']]==manifest['case_order'];rows=[]
 for e in index['cases']:
  cid=e['case_id'];c=cases[cid]
  if c['status']!='ready':rows.append({'case_id':cid,'language':c['language'],'class':c['class'],'status':'reference_unavailable','evidence_status':e['status']});continue
  if e['status']!='completed':rows.append({'case_id':cid,'language':c['language'],'class':c['class'],'status':'evidence_unavailable','evidence_status':e['status']});continue
  base=Path(a.index).parent/cid
  try:
   for name,pin in e['artifacts'].items():assert sha(base/name)==pin['sha256']
   inp=read(base/'inputs.json');doc=read(base/'native-turns.json');decision=read(base/'selection.json');gold=read(c['gold_file']['path']);assert sha(c['gold_file']['path'])==c['gold_file']['sha256'];N=c['crop']['frames'];assert sha(c['crop']['path'])==c['crop']['sha256']
   originals=inp['source']['originals'];assert len(originals)==1;delivery=originals[0]['delivery']
   assert delivery['wav_sha256']==c['crop']['sha256'] and delivery['pcm_sha256']==c['crop']['pcm_sha256'] and delivery['frames']==N and delivery['sample_rate_hz']==RATE and delivery['channels']==1
   A,detect,regions,allranges=reconstruct(doc,inp,N);D=union([(r['start_frame'],r['end_frame'])for r in detect]);U=union(allranges);chosen=allranges[:8];W=union(chosen)
   assert decision['detected_regions']==detect and decision['ranges']==[dict(start_frame=x,end_frame=y)for x,y in chosen]
   assert decision['evidence_sha256']==digest(doc) and decision['max_ranges']==8 and decision['max_range_frames']==320000 and decision['omitted_support_ranges']==max(0,len(allranges)-8)
   assert decision['detected_overlap_frames']==size(D) and decision['selected_detected_overlap_frames']==size(inter(D,W)) and decision['selected_support_frames']==size(W)
   ref={};iv=[]
   if gold['class']=='synthetic_zero':
    assert cid=='digital-zero-1' and gold.get('reference_intervals',[])==[];reference_intervals=[]
   else:
    assert isinstance(gold['reference_intervals'],list) and gold['reference_intervals'];reference_intervals=gold['reference_intervals']
   for q in reference_intervals:
    lo,hi=q['start_frame']-c['start_frame'],q['end_frame']-c['start_frame'];assert 0<=lo<hi<=N;ref.setdefault(q['speaker'],[]).append((lo,hi));iv.append((lo,hi))
   B=union(iv);O=union([(x,y)for x,y,_ in overlap_components(ref)]);m=metrics(A,B,O,D,U,W)
   ranges=[]
   for k,(lo,hi) in enumerate(chosen,1):
    touched=[(x,y)for x,y in iv if x<hi and y>lo];full=[(x,y)for x,y in touched if lo<=x and y<=hi];cuts=[(x,y)for x,y in touched if x<lo<y or x<hi<y]
    ranges.append({'range_ordinal':k,'start_frame':lo,'end_frame':hi,'seconds':(hi-lo)/RATE,'contributing_region_ordinals':[r['region_ordinal']for r in regions if r['context_start_frame']<hi and r['context_end_frame']>lo],'intersected_reference_intervals':len(touched),'fully_contained_reference_intervals':len(full),'boundary_crossing_reference_intervals':len(cuts),'left_boundary_crossings':sum(x<lo<y for x,y in touched),'right_boundary_crossings':sum(x<hi<y for x,y in touched),'lexical_boundary_status':'unscorable_boundary' if cuts else 'no_annotation_interval_cut_not_lexical_score_authorization'})
   events=[{'event_ordinal':k,'start_frame':lo,'end_frame':hi,'frames':hi-lo,'detected_intersection_frames':size(inter([(lo,hi)],D)),'selected_intersection_frames':size(inter([(lo,hi)],W)),'detected_missed_frames':size(minus([(lo,hi)],D)),'selected_missed_frames':size(minus([(lo,hi)],W))}for k,(lo,hi)in enumerate(O,1)]
   rows.append({'case_id':cid,'language':c['language'],'class':c['class'],'status':'scored_source_only_saved_geometry','annotation_resolution':'synthetic_exact_zero'if c['class']=='synthetic_zero'else'forced_aligned_human_word'if c['language']=='en'else'human_utterance','crop_frames':N,'crop_seconds':N/RATE,'artifacts_sha256':{name:pin['sha256']for name,pin in e['artifacts'].items()},'gold_sha256':c['gold_file']['sha256'],'crop_sha256':c['crop']['sha256'],'source_content_binding_sha256':digest(inp['source']),'independent_geometry_matches_selector':True,'metrics':m,'duration_seconds':{k.removesuffix('_frames')+'_seconds':v/RATE for k,v in m.items()if k.endswith('_frames')},'selection_cost':{'planned_candidate_calls':len(chosen),'executed_candidate_calls':0,'selected_range_sum_frames':sum(y-x for x,y in chosen),'selected_unique_frames':size(W),'duplicate_dispatch_frames':sum(y-x for x,y in chosen)-size(W),'max_selected_range_frames':max([y-x for x,y in chosen],default=0),'prebudget_range_count':len(allranges),'omitted_range_count':len(allranges)-len(chosen),'selected_fraction_of_crop':size(W)/N},'detected_regions':regions,'selected_ranges':ranges,'omitted_ranges':[dict(start_frame=x,end_frame=y)for x,y in allranges[8:]],'sets':{'A':A,'B':B,'O':O,'D':D,'U':U,'W':W,'J':minus(U,W)},'reference_events':events,'event_counts':{'total':len(events),'any_detected':sum(x['detected_intersection_frames']>0 for x in events),'fully_detected':sum(x['detected_missed_frames']==0 for x in events),'any_selected':sum(x['selected_intersection_frames']>0 for x in events),'fully_selected':sum(x['selected_missed_frames']==0 for x in events)},'annotated_negative_trigger':bool(D) if not O else None,'annotated_negative_selected':bool(W)if not O else None})
  except (AssertionError,ValueError,KeyError,TypeError,OSError) as ex:
   rows.append({'case_id':cid,'language':c['language'],'class':c['class'],'status':'unscorable_source_binding_or_geometry','error_type':type(ex).__name__})
 totals=[]
 for language in ('en','zh'):
  rr=[r for r in rows if r['status']=='scored_source_only_saved_geometry' and r['class']=='overlap' and r['language']==language];ms=[r['metrics']for r in rr];keys=['annotated_overlap_frames','detected_overlap_frames','detected_true_overlap_frames','missed_annotated_overlap_frames','detected_nonoverlap_frames','selected_support_frames','selected_annotated_overlap_frames','selection_missed_annotated_overlap_frames','selected_nonoverlap_frames']
  sums={k:sum(m[k]for m in ms)for k in keys};sums.update(detection_annotation_recall=ratio(sums['detected_true_overlap_frames'],sums['annotated_overlap_frames']),detection_annotation_precision=ratio(sums['detected_true_overlap_frames'],sums['detected_overlap_frames']),selection_annotation_overlap_coverage=ratio(sums['selected_annotated_overlap_frames'],sums['annotated_overlap_frames']),selected_overlap_fraction=ratio(sums['selected_annotated_overlap_frames'],sums['selected_support_frames']))
  ratekeys=['detection_annotation_recall','detection_annotation_precision','selection_annotation_overlap_coverage','selected_overlap_fraction'];macro={k:sum(v)/len(v)if(v:=[m[k]for m in ms if m[k]is not None])else None for k in ratekeys}
  totals.append({'language':language,'scored_overlap_cases':len(rr),'micro':sums,'macro_defined_rates':macro})
 result={'schema':'native-selector-public-geometry-score-v1','status':'posthoc_familiar_panel_saved_source_only_geometry','protocol_sha256':sha(a.protocol),'private_index_sha256':sha(a.index),'private_input_manifest_sha256':sha(a.manifest),'source_manifest_sha256':sha(a.source_manifest),'scorer_sha256':sha(__file__),'source_module_hashes_checked':len(index['source_module_pins']),'saved_file_hashes_rechecked':len(index['saved_file_pins']),'product_geometry_source_sha256':{k:index['source_module_pins'][k]for k in ['audio_backend/recorder_native_overlap.py','audio_backend/overlap_geometry.py']},'sample_rate_hz':RATE,'model_calls':0,'service_calls':0,'installed_service_qualified':False,'recognition_evaluated':False,'original_quality_results_changed':False,'case_count':len(rows),'rows':rows,'language_overlap_totals':totals,'limitations':['Already exposed fixed cases; posthoc familiar-panel result, not heldout performance.','Source-only readonly facade validation, not installed HTTP/API or deployment qualification.','D is native overlap detection; W is padded dispatch support, so selected nonoverlap includes deliberate context.','Annotation support is forced-aligned lexical words in English and utterance spans in Mandarin, not exact acoustic overlap truth.','Outside-reference support is unannotated, not proved silent.','Any boundary-crossing Mandarin utterance makes a full subrange transcript denominator unavailable; no character apportionment or gold-driven range change.','No candidate calls or lexical recognition evidence follow from geometry coverage.'],'public_text_policy':'No transcript text, original speaker/session/chunk labels, audio or private paths; anonymous ordinal geometry and hashes only.'}
 out=Path(a.out)
 with out.open('x')as f:json.dump(result,f,indent=2);f.write('\n')
 print(json.dumps({'output':str(out),'sha256':sha(out),'statuses':[(r['case_id'],r['status'])for r in rows]}))
if __name__=='__main__':main()
