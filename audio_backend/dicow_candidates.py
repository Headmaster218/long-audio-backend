"""Opt-in English SE-DiCoW candidates from bounded, already accessible context.

Only supplied local turns select enrollment. No source labels, future recording
scan, global speaker assignment, or profile update is performed.
"""
from __future__ import annotations
import json,math,tempfile
from pathlib import Path
from types import SimpleNamespace
from .dicow_assets import dicow_readiness
from .resources import run_bounded
ROOT=Path(__file__).resolve().parents[1]

def _turns(rows,duration):
    if not isinstance(rows,list) or len(rows)>4096:raise ValueError('Too many conditioning turns')
    result=[]
    for row in rows:
        sid=row.get('speaker_local_id')
        lo,hi=row.get('start'),row.get('end')
        if not isinstance(sid,str) or not sid or len(sid)>200:raise ValueError('Invalid conditioning local ID')
        if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (lo,hi)) or not 0<=lo<min(hi,duration)<=duration or hi>duration+.15:
            raise ValueError('Invalid conditioning turn interval')
        result.append({'speaker':sid,'start':lo,'end':min(hi,duration)})
    if len({r['speaker'] for r in result})>8:raise ValueError('Conditioning capacity exceeds8 local speakers')
    return result

def _relative(rows,lo,hi):
    return [{'speaker':r['speaker'],'start':max(r['start'],lo)-lo,'end':min(r['end'],hi)-lo}
            for r in rows if r['start']<hi and r['end']>lo]

def choose_enrollment(rows,target,duration,minimum=2.):
    """Return predicted clean occupancy, never a verified identity judgment."""
    import numpy as np
    if not 2<=minimum<=30:raise ValueError('Enrollment minimum must be2–30seconds')
    n=max(1,math.ceil(duration*50));ids=sorted({r['speaker'] for r in rows}|{target})
    activity=np.zeros((len(ids),n),dtype=np.uint8)
    for row in rows:activity[ids.index(row['speaker']),round(row['start']*50):min(n,round(row['end']*50))]=1
    clean=(activity[ids.index(target)]==1)&(activity.sum(0)==1)
    frames=min(1500,n);cumulative=np.r_[0,np.cumsum(clean)]
    scores=cumulative[frames:]-cumulative[:-frames];first=int(scores.argmax())
    seconds=float(scores[first])/50
    return {'start':first/50,'end':min(duration,(first+frames)/50),'enrollment_seconds':seconds,
            'eligible':seconds+1e-8>=minimum,'reason_code':None if seconds+1e-8>=minimum else 'insufficient_nonoverlap_enrollment'}

def _english_gate(config):
    detected=config.get('overlap_detected_languages',[])
    if isinstance(detected,str):detected=[detected]
    known={str(x).lower() for x in detected if x and str(x).lower() not in ('auto','unknown')}
    explicit=str(config.get('language','auto')).lower()
    # Evidence of another language blocks even an explicit English preference.
    if known and known!={'en'}:return False,'non_english_or_mixed_language'
    if explicit in ('en','english') or known=={'en'}:return True,'explicit_en' if explicit in ('en','english') else 'mixture_detected_en'
    return False,'english_evidence_required'

def analyze_dicow_candidates(wav_path,turns,transcribe_callback,config):
    # Callback retained for backend interchangeability; no mixture retranscription.
    import soundfile as sf,numpy as np
    from .overlap_candidates import plan_overlap_regions,validate_candidates
    info=sf.info(wav_path)
    if info.samplerate!=16000 or info.channels!=1 or not 0<info.duration<=60.01:raise ValueError('DiCoW expects bounded16k mono analyzer input')
    rows=_turns(turns,info.duration)
    regions,_=plan_overlap_regions(turns,info.duration,config)
    core_start=float(config.get('candidate_core_start',0.));core_end=float(config.get('candidate_core_end',info.duration))
    if not all(math.isfinite(x) for x in (core_start,core_end)) or not 0<=core_start<core_end<=info.duration+.001:
        raise ValueError('Invalid DiCoW candidate core bounds')
    regions=[r for r in regions if r['start']<core_end and r['end']>core_start]
    jobs=[r for r in regions if r['detected_source_count']==2 and r['end']-r['start']>=.001]
    allowed,gate=_english_gate(config)
    payload={'regions':regions,'tracks':[],'metadata':{'enabled':True,'backend':'dicow_en_experimental',
        'candidate_only':True,'identity_status':'unresolved','profile_update_eligible':False,
        'time_axis':'analyzer_window','original_mixture_retained':True,'language_gate':gate,
        'candidate_core_start':core_start,'candidate_core_end':core_end,
        'note':'English target-speaker candidates; conditioning and source identity remain unverified. Original mixture is retained.'}}
    if not jobs:return validate_candidates(payload,info.duration)
    if not allowed:
        for region in jobs:region.update(status='unsupported_language',reason_code=gate)
        return validate_candidates(payload,info.duration)
    ready=dicow_readiness(config)
    if not ready['ready']:
        for region in jobs:region.update(status='separation_failed',reason_code=ready['reason'])
        return validate_candidates(payload,info.duration)
    minimum=float(config.get('dicow_min_enrollment_seconds',2.))
    enrollments={sid:choose_enrollment(rows,sid,info.duration,minimum) for sid in {r['speaker'] for r in rows}}
    eligible=[];plans=[];remaining=float(config.get('overlap_max_seconds',12))
    for region in jobs:
        targets=region['detected_speaker_local_ids']
        missing=[{'conditioning_local_id':sid,'reason_code':enrollments[sid]['reason_code'],
                  'enrollment_seconds':enrollments[sid]['enrollment_seconds']} for sid in targets if not enrollments[sid]['eligible']]
        if missing:
            region.update(status='needs_more_evidence',reason_code='insufficient_nonoverlap_enrollment',
                          conditioning_evidence=missing)
        elif info.duration<10:
            region.update(status='needs_more_evidence',reason_code='insufficient_bounded_context')
        else:
            # The shared separator's1s margins yielded bad English on a2.7s
            # crop. Require a real, accessible10s context; never invent silence
            # or read beyond the analyzer waveform to satisfy this threshold.
            width=max(10.,region['support_end']-region['support_start'])
            center=(region['support_start']+region['support_end'])/2
            lo=max(0.,min(center-width/2,info.duration-width));hi=lo+width
            matching=None
            for plan in plans:
                if sorted(targets)!=plan['targets'] or lo>=plan['end'] or hi<=plan['start']:continue
                true_lo=min(region['start'],*(r['start'] for r in plan['regions']))
                true_hi=max(region['end'],*(r['end'] for r in plan['regions']))
                combined_width=max(10.,true_hi-true_lo+2*float(config.get('overlap_context_seconds',1.)))
                extra=combined_width-(plan['end']-plan['start'])
                if combined_width<=12+.001 and extra<=remaining+1e-7:
                    combined_start=max(0.,min((true_lo+true_hi-combined_width)/2,info.duration-combined_width))
                    matching=plan;remaining-=extra
                    plan.update(start=combined_start,end=combined_start+combined_width);break
            if matching is None:
                if width>remaining+1e-7 or len(plans)>=4:
                    region.update(status='unresolved_budget_limit',reason_code='insufficient_dicow_context_budget');continue
                matching={'shared_support_id':f'dicow-support-{len(plans)}','start':lo,'end':hi,
                          'targets':sorted(targets),'regions':[],'overlap_group_id':region['overlap_group_id']}
                remaining-=width;plans.append(matching)
            matching['regions'].append(region);eligible.append(region)
            for linked in matching['regions']:
                linked.update(support_start=matching['start'],support_end=matching['end'],
                              shared_support_id=matching['shared_support_id'],status='unresolved')
    if not eligible:return validate_candidates(payload,info.duration)
    parent=Path(config['data_dir'])/'tmp' if config.get('data_dir') else None
    if parent:parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dicow-candidates-',dir=parent) as temp:
        directory=Path(temp);references={};requests=[]
        for sid in {s for r in eligible for s in r['detected_speaker_local_ids']}:
            selection=enrollments[sid];lo,hi=selection['start'],selection['end']
            samples,sr=sf.read(wav_path,start=round(lo*16000),frames=round(hi*16000)-round(lo*16000),dtype='float32')
            if not np.isfinite(samples).all():raise ValueError('Nonfinite enrollment samples')
            path=directory/f'enrollment-{len(references)}.wav';sf.write(path,samples,sr,subtype='FLOAT')
            references[sid]={'enrollment_path':str(path),'enrollment_intervals':_relative(rows,lo,hi)}
        for plan in plans:
            lo,hi=plan['start'],plan['end']
            path=directory/f"{plan['shared_support_id']}.wav"
            samples,sr=sf.read(wav_path,start=round(lo*16000),frames=round(hi*16000)-round(lo*16000),dtype='float32')
            if not np.isfinite(samples).all():raise ValueError('Nonfinite candidate samples')
            sf.write(path,samples,sr,subtype='FLOAT')
            targets=[{'conditioning_local_id':sid,'source_slot':slot,**references[sid]}
                     for slot,sid in enumerate(plan['targets'])]
            requests.append({'overlap_group_id':plan['overlap_group_id'],'path':str(path),
                             'intervals':_relative(rows,lo,hi),'targets':targets})
        output=directory/'result.json';request=directory/'request.json'
        request.write_text(json.dumps({'config':config,'jobs':requests,'output':str(output)},allow_nan=False))
        limits=SimpleNamespace(data_dir=directory,disk_reserve_bytes=int(config.get('disk_reserve_bytes',2*1024**3)),
            process_memory_limit_bytes=int(config.get('process_memory_limit_bytes',6*1024**3)),cpu_threads=2)
        try:
            run_bounded([str(Path(config['dicow_python']).expanduser().absolute()),str(ROOT/'audio_backend/dicow_runner.py'),str(request)],
                limits,timeout=min(600,max(30,float(config.get('dicow_timeout_seconds',300)))),stdout_path=directory/'runtime.log')
            if not output.is_file() or output.stat().st_size>1024**2:raise ValueError('Missing/oversized DiCoW response')
            result=json.loads(output.read_text());groups=result.get('groups')
            if not isinstance(groups,list) or len(groups)!=len(plans):raise ValueError('Wrong DiCoW group count')
            indexed={r['overlap_group_id']:r for r in plans};seen=set()
            for group in groups:
                gid=group.get('overlap_group_id')
                if gid not in indexed or gid in seen:raise ValueError('Unknown/duplicate DiCoW group')
                seen.add(gid);plan=indexed[gid];local=group.get('tracks',[])
                expected=plan['targets'];support_id=plan['shared_support_id']
                assignments={r['overlap_group_id']:[] for r in plan['regions']}
                if len(local)!=2 or sorted(t.get('conditioning_local_id','') for t in local)!=expected:raise ValueError('Missing/duplicate conditioned source')
                for raw in local:
                    sid=raw['conditioning_local_id'];slot=expected.index(sid);selection=enrollments[sid]
                    if raw.get('source_slot')!=slot:raise ValueError('Incorrect local source slot')
                    lo,hi=plan['start'],plan['end'];units=[]
                    phrases=raw.get('phrases',[])
                    if not isinstance(phrases,list) or len(phrases)>160:raise ValueError('Invalid native phrase count')
                    for p in phrases:
                        u={k:p[k] for k in ('text','raw_text','start','end','ownership_anchor','unit_kind')}
                        if u['unit_kind']!='native_phrase':raise ValueError('DiCoW has no verified native word timing')
                        for k in ('start','end','ownership_anchor'):
                            if not isinstance(u[k],(int,float)) or not math.isfinite(u[k]) or not 0<=u[k]<=hi-lo+.15:raise ValueError('Native phrase outside crop')
                            u[k]=min(hi,lo+u[k])
                        if u['end']<=u['start']:raise ValueError('Reversed native phrase span')
                        u.update(confidence=None);units.append(u)
                    text=raw.get('text','')
                    if not isinstance(text,str) or len(text)>100000 or (text.strip() and not units):raise ValueError('Invalid DiCoW text')
                    owned={r['overlap_group_id']:[] for r in plan['regions']};related=set()
                    for unit in units:
                        intersecting=[r for r in plan['regions'] if unit['start']<r['end'] and unit['end']>r['start']]
                        if not intersecting:continue  # Real context-only speech is not overlap evidence.
                        related.update(r['overlap_group_id'] for r in intersecting)
                        owner=min(intersecting,key=lambda r:(r['start'],r['overlap_group_id']))
                        anchor=max(unit['start'],owner['start'])
                        owned[owner['overlap_group_id']].append(dict(unit,
                            ownership_anchor=anchor,ownership_anchor_kind='overlap_evidence_routing',
                            boundary_uncertain=abs(anchor-unit['start'])>1e-8,
                            related_overlap_group_ids=[r['overlap_group_id'] for r in intersecting]))
                    for region in plan['regions']:
                        rid=region['overlap_group_id'];tid=f'{rid}:{support_id}:source-{slot}'
                        selected=[dict(u,source_track_id=tid) for u in owned[rid]]
                        selected_text=' '.join(u['text'] for u in selected)
                        quality='unverified' if selected else ('shared_native_phrase' if rid in related else ('no_overlap_phrase' if text else 'no_text'))
                        assignments[rid].append(quality)
                        if quality=='shared_native_phrase':continue  # One physical phrase; related-region links reference its existing track.
                        related_for_track=sorted({rid}|{other for u in selected for other in u['related_overlap_group_ids']})
                        payload['tracks'].append({'source_track_id':tid,'overlap_group_id':rid,'source_slot':slot,'scope_id':support_id,
                        'start':lo,'end':hi,'support_start':lo,'support_end':hi,'text':selected_text,'raw_text':selected_text,
                        '_units':selected,'identity_status':'unresolved','quality_status':quality,
                        'speaker_id':None,'profile_update_eligible':False,'conditioning_local_id':sid,
                        'conditioning_source':'provided_local_diarization','language_gate':gate,
                        'enrollment_seconds':selection['enrollment_seconds'],'enrollment_source':'bounded_analyzer_context',
                        'provenance':{'model_id':'BUT-FIT/SE-DiCoW','model_revision':'470fce9ffff844dd53a27751cdf6c6df9efecb39',
                            'weights':'BF16','runtime':'PyTorch CPU','device':'cpu','asr_language':'en','conditioning_local_id':sid,
                            'conditioning_source':'provided_local_diarization','enrollment_source':'bounded_analyzer_context',
                            'enrollment_start':selection['start'],'enrollment_end':selection['end'],
                            'enrollment_seconds':selection['enrollment_seconds'],'enrollment_identity_verified':False,
                            'shared_support_id':support_id,'related_overlap_group_ids':related_for_track,
                            'ownership_anchor_kind':'overlap_evidence_routing',
                            'boundary_uncertain':any(u['boundary_uncertain'] for u in selected),
                            'timestamp_semantics':'Native phrase times retained; whole-phrase routing anchor is first overlap evidence, not word alignment',
                            'original_mixture_retained':True,'profile_update_eligible':False}})
                for region in plan['regions']:
                    statuses=assignments[region['overlap_group_id']]
                    shared='unverified' not in statuses and 'shared_native_phrase' in statuses
                    region.update(status='candidate_evidence_shared' if shared else 'candidate_tracks_unverified',
                        reason_code='native_phrase_owned_by_neighbor_region' if shared else 'experimental_english_conditioned_asr',
                        continuity_status='conditioning_local_scope_only')
            payload['metadata']['dicow_runtime']=result.get('metadata',{})
            validate_candidates(payload,info.duration)
        except (InterruptedError,KeyboardInterrupt,SystemExit):raise
        except Exception as exc:
            payload['tracks']=[]
            detail=f'{type(exc).__name__}: {exc}'[:500]
            if isinstance(exc,TimeoutError):reason='timeout'
            elif 'Disk reserve' in str(exc):reason='disk_reserve'
            elif 'Host available memory' in str(exc):reason='host_memory_reserve'
            elif 'process-tree memory budget' in str(exc):reason='aggregate_memory_guard'
            elif 'code 75' in str(exc):reason='resource_guard'
            elif isinstance(exc,ValueError):reason='invalid_model_output'
            else:reason='backend_failure'
            error_file=output.with_suffix('.error.json')
            if error_file.is_file() and error_file.stat().st_size<=65536:
                try:
                    error=json.loads(error_file.read_text())
                    if error.get('reason_code') in ('memory_guard','host_memory_reserve','dependency_error','model_load_error','inference_error'):
                        reason=error['reason_code'];detail=str(error.get('message',''))[:500]
                except (OSError,ValueError):pass
            for region in eligible:region.update(status='separation_failed',reason_code=reason,failure_reason=detail)
    return validate_candidates(payload,info.duration)
