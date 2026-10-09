#!/usr/bin/env python3
"""Approved bounded old-V1 array-reference control. Private inputs never exported.
No production writes, automatic learning, source substitutions or score fitting.
"""
from __future__ import annotations
import argparse, hashlib, json, math, os, pathlib, re, resource, shutil, signal, struct
import subprocess, sys, time, urllib.request, urllib.error, wave
from datetime import datetime, timezone
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from audio_backend.experimental_identity_memory import normalized, cosine
import replay_correction_identity as replay
PUB=ROOT/'reports/identity_array_replacement_v1'
PRIVATE=ROOT/'benchmarks/private_array_identity_replacement_v1'
PLAN=PUB/'execution_plan.json'
PLAN_SHA='521fe05e629f9b2d64bdf2bb203d01efb907f1f506eb712d22aba29da6cc84c4'
LIVE=ROOT/'reports/endurance_v03/moss_cpp_q8_native_overlap_v03'
GIB=1024**3


def read(path):return json.loads(pathlib.Path(path).read_text())
def sha(path):return replay.digest(path)
def write(path,obj,private=False):
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if private:path.parent.chmod(0o700)
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
    if private:tmp.chmod(0o600)
    os.replace(tmp,path)


def validate_frozen():
    if sha(PLAN)!=PLAN_SHA:raise RuntimeError('Approved plan changed')
    p=read(PLAN)
    checks=p['dependency_sha256']|p['model_and_frontend']['runtime_file_sha256']
    checks[p['frozen_interval_plan']]=p['frozen_interval_plan_sha256']
    checks[p['model_and_frontend']['model_path']]=p['model_and_frontend']['model_sha256']
    checks['audio_backend/profiles/eres2net-ami20-v1.json']=p['model_and_frontend']['profile_sha256']
    checks[p['model_and_frontend']['ffmpeg']['path']]=p['model_and_frontend']['ffmpeg']['sha256']
    for path,expected in checks.items():
        actual=pathlib.Path(path)
        if not actual.is_absolute():actual=ROOT/actual
        if not actual.is_file() or sha(actual)!=expected:raise RuntimeError('Frozen dependency mismatch: '+path)
    import importlib.metadata,importlib.util
    for name,version in p['model_and_frontend']['packages'].items():
        if importlib.metadata.version(name)!=version:raise RuntimeError('Runtime package changed: '+name)
    init=ROOT/'.venv/lib/python3.12/site-packages/sherpa_onnx/__init__.py'
    if pathlib.Path(importlib.util.find_spec('sherpa_onnx').origin).resolve()!=init.resolve():raise RuntimeError('Unexpected runtime resolution')
    seal=PUB/'execution-seal.json'
    if seal.exists() and sha(__file__)!=read(seal)['runner_sha256']:raise RuntimeError('Runner changed after sealing')
    intervals=read(ROOT/p['frozen_interval_plan'])['reviewed_intervals']
    for row in intervals:
        if sha(ROOT/row['local_gold'])!=row['local_gold_sha256']:raise RuntimeError('Gold changed')
    return p,intervals


def last_complete_metric(path):
    with open(path,'rb') as f:
        f.seek(max(0,path.stat().st_size-65536));tail=f.read()
    lines=tail.splitlines(keepends=True)
    full=[x for x in lines if x.endswith(b'\n')]
    if not full:raise RuntimeError('No complete authoritative metric sample')
    return json.loads(full[-1])


def telemetry(now=None,live=LIVE):
    now=time.time() if now is None else now
    path=live/'metrics.jsonl';progress=read(live/'progress.json')
    age=now-path.stat().st_mtime
    utc=datetime.fromisoformat(progress['utc']).timestamp();progress_age=now-utc
    if not -2<=age<=15 or not -2<=progress_age<=15:raise RuntimeError('Authoritative endurance telemetry stale')
    metric=last_complete_metric(path)
    if abs(progress['wall_seconds_this_invocation']-metric['wall_seconds'])>15:
        raise RuntimeError('Complete metric sample not contemporaneous with progress')
    if not any(c['status']=='running' for c in progress['chunks']):raise RuntimeError('Active endurance restart not confirmed')
    for key in ('rss_bytes','host_available_memory_bytes','disk_free_bytes'):
        if type(metric[key]) not in (int,float) or not math.isfinite(metric[key]) or metric[key]<0:raise RuntimeError('Invalid authoritative metric')
    return {'run_rss_bytes':metric['rss_bytes'],'metric_age_seconds':age,'progress_age_seconds':progress_age,
            'processed_media_seconds':metric['processed_media_seconds']}


def directory_bytes(path):
    total=0
    for p in path.rglob('*'):
        try:
            if p.is_file():total+=p.stat().st_size
        except FileNotFoundError:pass  # Atomic evidence writes can rename a temporary file.
    return total

def private_bytes():return directory_bytes(PRIVATE) if PRIVATE.exists() else 0

def guard_resources(own_rss,admission=False):
    import psutil
    t=telemetry();available=psutil.virtual_memory().available;free=shutil.disk_usage(ROOT).free
    if admission and t['run_rss_bytes']>=5*GIB:raise RuntimeError('Run RSS exceeds admission limit')
    if own_rss>GIB or own_rss+t['run_rss_bytes']>=6*GIB:raise RuntimeError('Own or aggregate RSS ceiling')
    if available<2*GIB or free<5*GIB:raise RuntimeError('Memory/disk reserve exhausted')
    if private_bytes()+directory_bytes(PUB)>128*1024**2:raise RuntimeError('Study disk ceiling')
    return t|{'own_rss_bytes':own_rss,'aggregate_rss_bytes':own_rss+t['run_rss_bytes'],'host_available_bytes':available,'disk_free_bytes':free}


def prepare():
    p,rows=validate_frozen()
    if len(rows)!=24 or sum(r['retained_in_existing_frozen_schedule'] for r in rows)!=20:raise RuntimeError('Fixed interval coverage changed')
    PRIVATE.mkdir(parents=True,exist_ok=True);PRIVATE.chmod(0o700)
    if not(PUB/'execution-seal.json').exists():
        write(PUB/'execution-seal.json',{'plan_sha256':PLAN_SHA,'runner_sha256':sha(__file__),
          'approval':'Root approved this exact plan hash before acquisition/inference',
          'root_clarification':'Annotation-proxy gallery only; array audibility/purity is not certified by headset-clean gold',
          'telemetry_contract':'Owner-confirmed metrics/progress every5s; both must be fresh<=15s; foreign PIDs are never signaled',
          'created_utc':datetime.now(timezone.utc).isoformat()})
    guard_resources(0,admission=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('Redirect refused pending review')


def validate_range_response(status,headers,start,end):
    if status!=206:raise RuntimeError(f'Expected HTTP206, received{status}; body refused')
    match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',headers.get('Content-Range',''))
    if not match:raise RuntimeError('Missing/invalid Content-Range')
    a,b,total=map(int,match.groups())
    if (a,b)!=(start,end) or b>=total:raise RuntimeError('Incorrect Content-Range')
    if headers.get('Content-Encoding','identity').lower() not in ('','identity'):raise RuntimeError('Encoded range refused')
    if int(headers.get('Content-Length','-1'))!=end-start+1:raise RuntimeError('Incorrect Content-Length')
    return total


def acquire_range(url,start,end,key):
    ledger_path=PRIVATE/'acquisition-attempts.json';ledger=read(ledger_path) if ledger_path.exists() else []
    if any(x['status']=='running' for x in ledger):raise RuntimeError('Unresolved acquisition attempt; do not blindly resume')
    previous=[x for x in ledger if x['request_key']==key]
    successful=[x for x in previous if x['status']=='complete']
    if successful:
        saved=successful[-1];path=PRIVATE/saved['path']
        if sha(path)!=saved['sha256']:raise RuntimeError('Sealed range changed')
        return path.read_bytes(),saved
    for attempt_number in range(len(previous),2):
        if len(ledger)>=64 or sum(x['received_bytes'] for x in ledger)>=10*1024**2:raise RuntimeError('Acquisition request/byte budget exhausted')
        if sum(x['wall_seconds'] for x in ledger)>=600:raise RuntimeError('Acquisition wall budget exhausted')
        import psutil
        guard_resources(psutil.Process().memory_info().rss)
        row={'request_key':key,'url':url,'start':start,'end':end,'attempt':attempt_number+1,'status':'running',
             'received_bytes':0,'wall_seconds':0,'started_unix':time.time()};ledger.append(row);write(ledger_path,ledger,True)
        tick=time.monotonic();response=None;body=bytearray();error=None
        try:
            req=urllib.request.Request(url,headers={'Range':f'bytes={start}-{end}','Accept-Encoding':'identity','User-Agent':'bounded-AMI-research/1.0'})
            response=urllib.request.build_opener(NoRedirect).open(req,timeout=10)
            row['http_status']=response.status
            total=validate_range_response(response.status,response.headers,start,end)
            row['response_headers']={k:response.headers.get(k) for k in ['Content-Range','Content-Length','ETag','Last-Modified']}
            row['source_total_bytes']=total
            while len(body)<end-start+1:
                chunk=response.read(min(65536,end-start+1-len(body)))
                if not chunk:raise RuntimeError('Truncated range body')
                body.extend(chunk);row['received_bytes']+=len(chunk);row['wall_seconds']=time.monotonic()-tick
                write(ledger_path,ledger,True)
                if sum(x['received_bytes'] for x in ledger)>10*1024**2 or sum(x['wall_seconds'] for x in ledger)>600:raise RuntimeError('Cumulative acquisition budget exceeded')
                guard_resources(psutil.Process().memory_info().rss)
            path=PRIVATE/'ranges'/f'{key}.bin';path.parent.mkdir(exist_ok=True);path.write_bytes(body);path.chmod(0o600)
            row.update(status='complete',path=str(path.relative_to(PRIVATE)),sha256=sha(path))
        except Exception as exc:
            error=exc;row.update(status='failed',error=repr(exc))
        finally:
            if response is not None:response.close()
            row['wall_seconds']=time.monotonic()-tick;write(ledger_path,ledger,True)
        if error is None:return bytes(body),row
        # Only transient transport/short-read failures get the single approved retry.
        transient=isinstance(error,(TimeoutError,urllib.error.URLError)) or str(error)=='Truncated range body'
        if not transient or attempt_number==1:raise error
    raise RuntimeError('Range retries exhausted')


def parse_wav_header(data,total):
    if len(data)<12:return None
    if data[:4]!=b'RIFF' or data[8:12]!=b'WAVE':raise RuntimeError('Only RIFF/WAVE supported')
    if struct.unpack_from('<I',data,4)[0]+8!=total:raise RuntimeError('RIFF total differs from source object')
    offset=12;fmt=None
    while offset+8<=len(data):
        kind=data[offset:offset+4];size=struct.unpack_from('<I',data,offset+4)[0];payload=offset+8
        if payload+size>total:raise RuntimeError('WAV chunk outside source object')
        if kind==b'fmt ':
            if payload+size>len(data):return None
            if size<16:raise RuntimeError('Invalid fmt chunk')
            fmt=struct.unpack_from('<HHIIHH',data,payload)
            if fmt!=(1,1,16000,32000,2,16):raise RuntimeError('Unexpected WAV format; no silent conversion')
        if kind==b'data':
            if fmt is None:raise RuntimeError('Missing PCM format before data')
            if size%2:raise RuntimeError('Unaligned PCM payload')
            return {'data_offset':payload,'data_bytes':size,'sample_rate':16000,'channels':1,'bits_per_sample':16,'frames':size//2,'source_total_bytes':total}
        offset=payload+size+(size%2)
    return None


def union_seconds(intervals):
    end=-math.inf;total=0.
    for lo,hi in sorted(intervals):
        if hi>max(lo,end):total+=hi-max(lo,end)
        end=max(end,hi)
    return total


def annotation_audit(row):
    gold=read(ROOT/row['local_gold']);lo,hi=row['start_seconds'],row['end_seconds']
    competitors=[(max(lo,x['start']),min(hi,x['end'])) for x in gold['segments'] if x['speaker']!=row['person'] and x['start']<hi and x['end']>lo]
    events=[(max(lo,x['start']),min(hi,x['end'])) for x in gold.get('events',[]) if x['start']<hi and x['end']>lo]
    other=union_seconds(competitors);vocal=union_seconds(events)
    return {'gold_competing_speech_seconds':other,'gold_event_seconds':vocal,'annotation_contradiction':other>1/16000 or vocal>1/16000,
      'actual_array_identity_or_crosstalk_certified':False,'interpretation':'Annotation proxy only; no acoustic speaker-purity classifier or human listening was performed'}


def acquire_worker():
    p,intervals=validate_frozen();sources={};records=[]
    for rid,source in p['source_strategy']['official_sources'].items():
        header=b'';parsed=None;object_total=None
        for index in range(4):
            chunk,event=acquire_range(source['file_url'],index*65536,(index+1)*65536-1,f'{rid}-header-{index}')
            if object_total is not None and event['source_total_bytes']!=object_total:raise RuntimeError('Source object changed between ranges')
            object_total=event['source_total_bytes'];header+=chunk;parsed=parse_wav_header(header,object_total)
            if parsed:break
        if parsed is None:raise RuntimeError('Header not found within256KiB limit')
        sources[rid]=parsed|{'official_url':source['file_url'],'header_received_sha256':hashlib.sha256(header).hexdigest(),'header_bytes_received':len(header),'etag':event['response_headers'].get('ETag'),'last_modified':event['response_headers'].get('Last-Modified'),'whole_source_sha256':None,'whole_source_sha256_note':'Full file not downloaded'}
    for row in intervals:
        rid=row['recording_id'];source=sources[rid];lo=row['expected_16khz_start_sample'];hi=row['expected_16khz_end_sample_exclusive']
        if not 0<=lo<hi<=source['frames']:raise RuntimeError('Requested interval outside declared source frames')
        a=source['data_offset']+lo*2;b=source['data_offset']+hi*2-1
        pcm,event=acquire_range(source['official_url'],a,b,row['existing_headset_cache_id'])
        if event['source_total_bytes']!=source['source_total_bytes']:raise RuntimeError('Source length changed after header')
        for field,header in [('etag','ETag'),('last_modified','Last-Modified')]:
            if source[field] is not None and event['response_headers'].get(header)!=source[field]:raise RuntimeError('Source version changed after header')
        # Pure input statistics, never used to select favorable clips.
        import numpy as np
        values=np.frombuffer(pcm,dtype='<i2').astype(np.float64)
        stats={'frames':len(values),'duration_seconds':len(values)/16000,'rms_pcm16':float(np.sqrt(np.mean(values*values))),
          'peak_abs_pcm16':float(np.max(np.abs(values))),'zero_fraction':float(np.mean(values==0)),
          'clipped_fraction':float(np.mean(np.abs(values)>=32767))}
        if not np.any(values):raise RuntimeError('All-zero target audio; failed review, no substitute')
        audit=annotation_audit(row)
        if audit['annotation_contradiction']:raise RuntimeError('Existing gold contradicts clean-interval proxy; no purification/reselection')
        path=PRIVATE/'audio'/f'{row["existing_headset_cache_id"]}.wav';path.parent.mkdir(exist_ok=True)
        with wave.open(str(path),'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm)
        path.chmod(0o600)
        record={k:v for k,v in row.items()}|{'clip_path':str(path.relative_to(ROOT)),'clip_sha256':sha(path),'pcm_sha256':hashlib.sha256(pcm).hexdigest(),
          'range_start':a,'range_end_inclusive':b,'signal_statistics':stats,'annotation_audit':audit,
          'review_status':'Simulated gold label; actual audible identity/purity certification unavailable'}
        records.append(record);write(PRIVATE/'acquisition.json',{'sources':sources,'records':records,'complete':len(records)==24},True)
    ledger=read(PRIVATE/'acquisition-attempts.json')
    write(PUB/'acquisition-summary.json',{'status':'complete','source_count':2,'clip_count':24,'pcm_bytes':sum(r['range_end_inclusive']-r['range_start']+1 for r in records),
      'received_bytes_including_failed_attempts':sum(r['received_bytes'] for r in ledger),'request_attempts':len(ledger),'sources':sources,
      'reviews':[{'id':r['existing_headset_cache_id'],'person':r['person'],'signal_statistics':r['signal_statistics'],'annotation_audit':r['annotation_audit'],'review_status':r['review_status']} for r in records],
      'private_acquisition_sha256':sha(PRIVATE/'acquisition.json'),'no_actual_human_or_acoustic_purity_certification':True})


def no_network(event,args):
    if event in {'socket.connect','socket.getaddrinfo','socket.bind'}:raise RuntimeError('Inference/scoring networking disabled')


def vector_from_clip(ext,path,expected_seconds):
    import numpy as np
    raw=subprocess.check_output(['ffmpeg','-nostdin','-v','error','-threads','1','-i',str(path),'-threads','1','-filter_threads','1','-ac','1','-ar','16000','-f','f32le','-'],timeout=30)
    values=np.frombuffer(raw,dtype='<f4').copy()
    if abs(len(values)/16000-expected_seconds)>1/16000:raise RuntimeError('PCM duration mismatch')
    stream=ext.create_stream();stream.accept_waveform(sample_rate=16000,waveform=values);stream.input_finished()
    if not ext.is_ready(stream):raise RuntimeError('Extractor not ready; failed review, no substitute')
    result=normalized(ext.compute(stream))
    if len(result)!=512:raise RuntimeError('Unexpected embedding dimension')
    return result,hashlib.sha256(raw).hexdigest()


def inference_worker(stage):
    sys.addaudithook(no_network);p,intervals=validate_frozen()
    import sherpa_onnx
    ext=sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(ROOT/'models/speaker.onnx'),num_threads=1,provider='cpu'))
    if ext.dim!=512:raise RuntimeError('Old512D model required')
    if stage=='parity':
        results=[]
        for row in p['model_and_frontend']['parity_crops_existing_calibration_only']:
            if sha(ROOT/row['local_audio'])!=row['audio_sha256']:raise RuntimeError('Parity source changed')
            cache=ROOT/'benchmarks/private_speaker_embeddings'/f'{row["cache_id"]}.json'
            if sha(cache)!=row['cache_sha256']:raise RuntimeError('Old parity cache changed')
            old=read(cache)['embedding'];v,pcm=vector_from_clip(ext,ROOT/row['local_audio'],row['seconds'])
            score=cosine(v,normalized(old));error=max(abs(a-b) for a,b in zip(v,old));ok=score>=.999999 and error<=.00001
            results.append({'id':row['cache_id'],'cosine':score,'max_abs_error':error,'passed':ok,'pcm_f32_sha256':pcm})
        write(PUB/'parity.json',{'status':'passed' if all(r['passed'] for r in results) else 'failed','rows':results})
        if not all(r['passed'] for r in results):raise RuntimeError('Legacy frontend parity failed; no gate relaxation')
    else:
        acquired=read(PRIVATE/'acquisition.json')
        if not acquired['complete'] or len(acquired['records'])!=24:raise RuntimeError('Acquisition incomplete; denominator cannot shrink')
        saved=read(PRIVATE/'embeddings.json') if (PRIVATE/'embeddings.json').exists() else {'records':[]}
        previous={r['existing_headset_cache_id']:r for r in saved['records']}
        if len(previous)!=len(saved['records']):raise RuntimeError('Duplicate partial embedding cache')
        rows=[]
        for row in acquired['records']:
            if sha(ROOT/row['clip_path'])!=row['clip_sha256']:raise RuntimeError('Acquired clip changed')
            if row['existing_headset_cache_id'] in previous:
                item=previous[row['existing_headset_cache_id']]
                if item['clip_sha256']!=row['clip_sha256'] or item['model_sha256']!=p['model_and_frontend']['model_sha256'] or len(item['embedding'])!=512 or not all(math.isfinite(x) for x in item['embedding']) or abs(sum(x*x for x in item['embedding'])-1)>1e-8:raise RuntimeError('Partial embedding cache binding/normalization failure')
                rows.append(item);continue
            v,pcm=vector_from_clip(ext,ROOT/row['clip_path'],row['reviewed_audio_seconds'])
            rows.append({'existing_headset_cache_id':row['existing_headset_cache_id'],'paired_interval_id':row['paired_interval_id'],
                         'model_sha256':p['model_and_frontend']['model_sha256'],'frontend':'frozen legacy sherpa1.13.8',
                         'clip_sha256':row['clip_sha256'],'pcm_f32_sha256':pcm,'embedding':list(v)})
            write(PRIVATE/'embeddings.json',{'records':rows,'complete':len(rows)==24},True)
        write(PRIVATE/'embeddings.json',{'records':rows,'complete':len(rows)==24},True)
        write(PUB/'extraction-summary.json',{'status':'complete','crops':24,'seconds_of_audio':sum(r['reviewed_audio_seconds'] for r in acquired['records']),
              'dimension':512,'model_sha256':p['model_and_frontend']['model_sha256'],'private_embedding_cache_sha256':sha(PRIVATE/'embeddings.json'),
              'actual_human_verified_reviews':0,'simulated_review_labels':24,'purity_claim':'Unavailable; annotation-proxy control only'})


def score_gallery(gallery,queries,known):
    means={person:normalized(sum(values) for values in zip(*refs)) for person,refs in gallery.items()};out=[]
    for row in queries:
        v=normalized(row['embedding']);scores={p:cosine(v,mean) for p,mean in means.items()}
        order=sorted(scores,key=lambda x:(-scores[x],x));winner=order[0];best=scores[winner];margin=best-scores[order[1]] if len(order)>1 else 2
        threshold=.55 if len(gallery[winner])==1 else .56
        accepted=best>=threshold and margin>=.10
        status='conditional_annotation_centroid_match' if accepted else 'ambiguous' if best>=threshold else 'uncertain' if best>=.45 else 'unrecognized'
        out.append({'id':row['id'],'recording_id':row['recording_id'],'gold_person':row['speaker'],'known':row['speaker'] in known,
          'start':row['start'],'end':row['end'],'seconds':row['end']-row['start'],'assigned':winner if accepted else None,'score':best,'margin':margin,'status':status})
    return out


def comparison(new,old):
    old={x['id']:x for x in old};rescued=[];lost=[];wrong=[];unknown=[]
    if set(old)!={x['id'] for x in new}:raise RuntimeError('Probe denominator changed')
    for x in new:
        b=old[x['id']]
        if x['assigned']==x['gold_person'] and b['assigned']!=x['gold_person']:rescued.append(x['id'])
        if b['assigned']==x['gold_person'] and x['assigned']!=x['gold_person']:lost.append(x['id'])
        if x['assigned'] is not None and x['assigned']!=x['gold_person'] and x['assigned']!=b['assigned']:wrong.append(x['id'])
        if not x['known'] and x['assigned'] is not None and b['assigned'] is None:unknown.append(x['id'])
    return {'rescued_correct_ids':rescued,'lost_correct_ids':lost,'newly_wrong_ids':wrong,'new_unknown_accept_ids':unknown}


def score_worker():
    sys.addaudithook(no_network);p,intervals=validate_frozen();cache=read(PRIVATE/'embeddings.json')
    if not cache['complete'] or len(cache['records'])!=24:raise RuntimeError('Replacement cache incomplete')
    lookup={x['existing_headset_cache_id']:x for x in cache['records']};old_report=read(ROOT/'reports/identity_learning_v1/replay.json')
    original={path.stem:read(path) for path in (ROOT/'benchmarks/private_speaker_embeddings').glob('*.json')}
    suffix_case=next(c for c in old_report['cases'] if c['case']=='target_channel_unchanged' and c['strategy']=='centroid')
    suffix_ids={x['id'] for x in suffix_case['rows'] if x['phase']=='later_chronological'}
    results=[]
    for count,ending in [(18,''),(24,'_early_corrections')]:
        selected=intervals[:count];gallery={};old_gallery={}
        for r in selected:
            if r['retained_in_existing_frozen_schedule']:
                gallery.setdefault(r['person'],[]).append(normalized(lookup[r['existing_headset_cache_id']]['embedding']))
                old_gallery.setdefault(r['person'],[]).append(normalized(original[r['existing_headset_cache_id']]['embedding']))
        if max(map(len,gallery.values()))>8 or len(gallery)!=5:raise RuntimeError('Reference budget/person coverage changed')
        arm={'simulated_reviewed_clips':count,'simulated_reviewed_audio_seconds':sum(r['reviewed_audio_seconds'] for r in selected),
             'simulated_retained_references':sum(map(len,gallery.values())),'references_by_person':{p:len(v) for p,v in gallery.items()},
             'actual_human_verified_reviews':0,'acoustic_certification':'Unavailable; all scores conditional on annotation-proxy person labels','slices':{}}
        for name in ('original_farfield','original_headset'):
            baseline=next(c for c in old_report['cases'] if c['case']==name+ending and c['strategy']=='centroid' and c['retained_initial_anchors_per_person_cap']==3)
            queries=[original[x['id']] for x in baseline['rows']]
            reproduced=score_gallery(old_gallery,queries,set(gallery))
            for x,y in zip(reproduced,baseline['rows']):
                if x['assigned']!=y['assigned'] or abs(x['score']-y['score'])>1e-12:raise RuntimeError('Old paired baseline not reproduced')
            scored=score_gallery(gallery,queries,set(gallery));known=[x for x in scored if x['known']];unknown=[x for x in scored if not x['known']]
            expected=60 if name=='original_farfield' else 116
            if len(known)!=expected or len(unknown)!=38:raise RuntimeError('Fixed denominator unavailable')
            arm['slices'][name]={'known':replay.summarize(known),'unknown_challenge':replay.summarize(unknown),'paired':comparison(scored,baseline['rows']),
              'by_person':{person:replay.summarize([x for x in known if x['gold_person']==person]) for person in sorted(gallery)},'rows':scored}
            if name=='original_farfield':
                suffix=[x for x in scored if x['id'] in suffix_ids];old_suffix=[x for x in baseline['rows'] if x['id'] in suffix_ids]
                if len(suffix)!=51:raise RuntimeError('Suffix denominator unavailable')
                arm['slices']['same51suffix']={'known':replay.summarize(suffix),'paired':comparison(suffix,old_suffix),'rows':suffix}
        far=arm['slices']['original_farfield'];head=arm['slices']['original_headset'];base_head=74 if count==18 else 87
        arm['gates']={'far_ge48of60':far['known']['correct_known']>=48,'suffix_ge41of51':arm['slices']['same51suffix']['known']['correct_known']>=41,
          'no_new_wrong':not any(s['paired']['newly_wrong_ids'] for s in arm['slices'].values()),
          'no_new_unknown_accepts':not any(s['paired']['new_unknown_accept_ids'] for s in arm['slices'].values()),
          'headset_no_aggregate_recall_regression':head['known']['correct_known']>=base_head}
        arm['all_numerical_gates_pass']=all(arm['gates'].values());results.append(arm)
    validate_frozen()
    write(PUB/'replay.json',{'status':'complete_conditional_annotation_proxy','plan_sha256':PLAN_SHA,'execution_seal_sha256':sha(PUB/'execution-seal.json'),
      'source_and_embedding_provenance':{'acquisition_summary_sha256':sha(PUB/'acquisition-summary.json'),'extraction_summary_sha256':sha(PUB/'extraction-summary.json')},
      'profile':'unchanged eres2net-ami20-v1 plus frozen legacy frontend','automatic_memory':False,'production_changed':False,
      'limitations':['Reused oracle annotation-proxy labels, not human-certified audible array identity or cross-talk purity','No new holdout, cross-day source dates are genuine but query results were seen before','Unknown challenge uses headset audio, not condition-matched array unknown speakers','No new IDs/merges; production diarization/fragmentation not measured'],
      'actual_human_verified_reviews':0,'results':results,'promotion_authorized':False})


def guarded(stage):
    import psutil
    p,_=validate_frozen();ledger_path=PRIVATE/'stage-attempts.json';ledger=read(ledger_path) if ledger_path.exists() else []
    if any(x['status']=='running' for x in ledger):raise RuntimeError('Unresolved stage attempt; inspect original executor before resuming')
    outputs={'parity':'parity.json','acquire':'acquisition-summary.json','extract':'extraction-summary.json','score':'replay.json'}
    prior_success=[x for x in ledger if x['stage']==stage and x['status']=='complete']
    if prior_success:
        if sha(PUB/outputs[stage])!=prior_success[-1]['output_sha256']:raise RuntimeError('Completed output changed')
        print(stage,'already complete; no repeated operation',flush=True);return
    used=sum(x['wall_seconds'] for x in ledger if x['stage'] in ('parity','extract'))
    budget=300-used if stage in ('parity','extract') else (600 if stage=='acquire' else 60)-sum(x['wall_seconds'] for x in ledger if x['stage']==stage)
    if budget<=0:raise RuntimeError('Cumulative compute budget exhausted')
    if 8 not in os.sched_getaffinity(0):raise RuntimeError('Approved CPU8 unavailable')
    guard_resources(psutil.Process().memory_info().rss,admission=True)
    row={'stage':stage,'status':'running','started_unix':time.time(),'wall_seconds':0,'peak_tree_rss_bytes':0,'peak_aggregate_rss_bytes':0,'guard_samples':0};ledger.append(row);write(ledger_path,ledger,True)
    env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',OMP_THREAD_LIMIT='1')
    start=time.monotonic();proc=None;error=None
    with open(PUB/f'{stage}.log','w') as log:
        try:
            proc=subprocess.Popen([sys.executable,str(pathlib.Path(__file__).resolve()),'worker',stage],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            while proc.poll() is None:
                tree=[psutil.Process(proc.pid)]+psutil.Process(proc.pid).children(recursive=True);rss=psutil.Process().memory_info().rss
                for child in tree:
                    try:rss+=child.memory_info().rss
                    except psutil.NoSuchProcess:pass
                measurement=guard_resources(rss);row['peak_tree_rss_bytes']=max(row['peak_tree_rss_bytes'],rss)
                row['peak_aggregate_rss_bytes']=max(row['peak_aggregate_rss_bytes'],measurement['aggregate_rss_bytes']);row['guard_samples']+=1
                row['wall_seconds']=time.monotonic()-start
                if row['wall_seconds']>budget:raise RuntimeError('Stage cumulative wall limit exceeded')
                write(ledger_path,ledger,True);time.sleep(.25)
            if proc.returncode:raise RuntimeError(f'{stage} worker exit{proc.returncode}; see preserved stage log')
            guard_resources(psutil.Process().memory_info().rss)
            row.update(status='complete',output_sha256=sha(PUB/outputs[stage]))
        except Exception as exc:
            error=exc
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            row.update(status='failed',error=repr(exc))
        finally:
            row['wall_seconds']=time.monotonic()-start;write(ledger_path,ledger,True)
    write(PUB/'resource-summary.json',{'attempts':ledger,'compute_wall_seconds_all_attempts':sum(x['wall_seconds'] for x in ledger if x['stage'] in ('parity','extract')),
       'peak_tree_rss_bytes':max(x['peak_tree_rss_bytes'] for x in ledger),'peak_aggregate_rss_bytes':max(x['peak_aggregate_rss_bytes'] for x in ledger),
       'aggregate_method':'Fresh owner metrics rss_bytes plus own supervisor/worker/ffmpeg RSS, not foreign PID scans','foreign_process_signals_sent':False})
    if error:raise error
    print(stage,'complete',round(row['wall_seconds'],3),'seconds',flush=True)


def main():
    a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','run','worker']);a.add_argument('stage',nargs='?');args=a.parse_args()
    if args.action=='worker':
        os.sched_setaffinity(0,{8});os.nice(19)
        if args.stage=='acquire':acquire_worker()
        elif args.stage in ('parity','extract'):inference_worker(args.stage)
        elif args.stage=='score':score_worker()
        else:raise RuntimeError('Unknown stage')
    else:
        prepare()
        if args.action=='run':
            for stage in ('parity','acquire','extract','score'):guarded(stage)

if __name__=='__main__':main()
