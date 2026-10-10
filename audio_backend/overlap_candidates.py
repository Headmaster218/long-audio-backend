"""Opt-in candidate-source analysis. Mixture transcript and identities are untouched.

The separator is a replaceable isolated subprocess. A candidate is unverified
speech evidence, never a trusted person or permission to enroll a voice profile.
"""
from __future__ import annotations
import json
import math
import tempfile
from pathlib import Path
from types import SimpleNamespace
from .overlap import overlapping_regions
from .resources import run_bounded
ROOT=Path(__file__).resolve().parents[1]


def plan_overlap_regions(turns,duration,config):
    budget=float(config.get('overlap_max_seconds',12))
    context=float(config.get('overlap_context_seconds',1))
    if not 0<budget<=12 or not 0<=context<=2:raise ValueError('Invalid overlap budget/context')
    split=max(.05,budget-2*context)
    core_start=float(config.get('candidate_core_start',0.))
    core_end=float(config.get('candidate_core_end',duration))
    if not all(math.isfinite(x) for x in (core_start,core_end)) or not 0<=core_start<=core_end<=duration+.001:
        raise ValueError('Invalid candidate core bounds')
    core_end=min(duration,core_end)
    regions=[];jobs=[];remaining=budget
    for original in overlapping_regions(turns,duration,capacity=2):
        if original['end']<=core_start or original['start']>=core_end:continue
        position=original['start'];region_end=original['end']
        while position<region_end-1e-8:
            end=min(region_end,position+split)
            if end<=core_start or position>=core_end:
                position=end;continue
            lo=max(0.,position-context);hi=min(duration,end+context)
            region={'overlap_group_id':f'group-{len(regions)}','start':position,'end':end,
                    'support_start':lo,'support_end':hi,'detected_source_count':original['detected_source_count'],
                    'detected_speaker_local_ids':original['speaker_local_ids'],'capacity':2,
                    'status':'unresolved','reason_code':'not_yet_attempted','original_mixture_retained':True}
            if original['detected_source_count']>2:region.update(status='capacity_exceeded',reason_code='model_capacity_exceeded')
            elif hi-lo<.05:region.update(status='unresolved_short_input',reason_code='insufficient_audio_evidence')
            elif hi-lo>remaining+1e-7 or len(jobs)>=16:region.update(status='unresolved_budget_limit',reason_code='analysis_budget_exhausted')
            else:
                remaining-=hi-lo;jobs.append(region)
            regions.append(region);position=end
    return regions,jobs


def moss_support_activity(turns,start,end):
    """All detected local voices in actual recognition support, not just core."""
    from collections import Counter
    from itertools import groupby
    events=[];speakers=set()
    for turn in turns:
        lo=max(start,float(turn['start']));hi=min(end,float(turn['end']))
        if hi<=lo:continue
        sid=turn['speaker_local_id'];speakers.add(sid)
        events.extend(((lo,1,sid),(hi,-1,sid)))
    active=Counter();maximum=0
    for time,rows in groupby(sorted(events),key=lambda row:row[0]):
        for _,delta,sid in rows:
            active[sid]+=delta
            if active[sid]<=0:active.pop(sid,None)
        if time<end:maximum=max(maximum,len(active))
    return {'max_simultaneous_speakers':maximum,'distinct_local_speakers':len(speakers),
            'speaker_local_ids':sorted(speakers),'identity_scope':'analysis_window_local'}


def plan_moss_support_jobs(regions,turns,config):
    """Moss-only guard; SE conditioning has different support semantics."""
    remaining=float(config.get('overlap_max_seconds',12));jobs=[]
    for region in regions:
        activity=moss_support_activity(turns,region['support_start'],region['support_end'])
        region['support_activity']=activity
        if region['status'] in ('capacity_exceeded','unresolved_short_input'):continue
        if activity['max_simultaneous_speakers']>2:
            region.update(status='support_capacity_exceeded',reason_code='recognition_support_exceeds_two_concurrent_sources')
            continue
        if activity['distinct_local_speakers']>2:
            region.update(status='ambiguous_support',reason_code='recognition_support_contains_additional_local_speakers')
            continue
        duration=region['support_end']-region['support_start']
        if duration>remaining+1e-7 or len(jobs)>=16:
            region.update(status='unresolved_budget_limit',reason_code='analysis_budget_exhausted')
            continue
        remaining-=duration
        region.update(status='unresolved',reason_code='not_yet_attempted');jobs.append(region)
    return jobs


def validate_candidates(payload,duration,*,owned=False):
    if not isinstance(payload,dict):raise ValueError('Invalid overlap candidate payload')
    regions=payload.get('regions',[]);tracks=payload.get('tracks',[])
    if not isinstance(regions,list) or len(regions)>4096 or not isinstance(tracks,list) or len(tracks)>256:raise ValueError('Overlap candidate limit exceeded')
    ids=set()
    for region in regions:
        gid=region.get('overlap_group_id')
        if not isinstance(gid,str) or not gid or len(gid)>200 or gid in ids:raise ValueError('Invalid overlap group ID')
        ids.add(gid)
        count=region.get('detected_source_count')
        kind=region.get('region_kind','detected_overlap')
        if kind not in ('detected_overlap','speech_support_probe'):raise ValueError('Invalid candidate region kind')
        count=region.get('detected_source_count')
        minimum=1 if kind=='speech_support_probe' else 2
        if type(count) is not int or not minimum<=count<=4096:raise ValueError('Invalid detected source count')
        model_count=region.get('model_label_count')
        if model_count is not None and (type(model_count) is not int or not 1<=model_count<=8):raise ValueError('Invalid model label count')
        if region.get('original_mixture_retained') is not True:raise ValueError('Original overlap mixture must remain retained')
    tids=set()
    for item in regions+tracks:
        for key in ('start','end','support_start','support_end'):
            value=item.get(key)
            if not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Nonfinite overlap timestamp')
        if owned and item in tracks:
            if not item['support_start']-.001<=item['start']<item['end']<=item['support_end']+.001:raise ValueError('Owned source word outside original support')
        elif not 0<=item['start']<item['end']<=duration+.15:raise ValueError('Overlap timestamp outside analyzer window')
        if not 0<=item['support_end']-item['support_start']<=60.01 or item['support_start'] < -60 or item['support_end']>duration+60:raise ValueError('Invalid overlap support')
    for track in tracks:
        tid=track.get('source_track_id')
        if not isinstance(tid,str) or not tid or len(tid)>300 or tid in tids:raise ValueError('Invalid source track ID')
        tids.add(tid)
        if track.get('overlap_group_id') not in ids:raise ValueError('Orphan overlap track')
        for key in ('text','raw_text'):
            if not isinstance(track.get(key,''),str) or len(track.get(key,''))>100000:raise ValueError('Oversized overlap text')
            track.get(key,'').encode('utf-8')
        if track.get('speaker_id') is not None or track.get('profile_update_eligible') is not False or track.get('identity_status')!='unresolved':raise ValueError('Experimental source cannot claim/enroll person identity')
        if 'speaker_embedding' in track:raise ValueError('Separated source cannot supply enrollment embedding')
        if type(track.get('source_slot')) is not int or not 0<=track['source_slot']<8:raise ValueError('Invalid bounded source slot')
        if not isinstance(track.get('_units',[]),list) or len(track.get('_units',[]))>4096:raise ValueError('Invalid source lexical units')
        for unit in track.get('_units',[]):
            if not isinstance(unit.get('text'),str):raise ValueError('Invalid source lexical text')
            for key in ('start','end','ownership_anchor'):
                if not isinstance(unit.get(key),(int,float)) or not math.isfinite(unit[key]) or not 0<=unit[key]<=duration+.15:raise ValueError('Invalid source lexical timestamp')
            if unit['end']<unit['start']:raise ValueError('Reversed source lexical interval')
    if 'diagnostics' in payload:
        from .overlap_diagnostics import validate as validate_diagnostics
        validate_diagnostics(payload['diagnostics'],regions,owned=owned)
    if len(json.dumps(payload,allow_nan=False).encode('utf-8'))>2*1024**2:raise ValueError('Overlap candidate response exceeds2MiB')
    return payload


def _native_units(prediction,duration,offset,track_id):
    # Reuse the same native-token handling as mixture ASR, with an explicitly
    # unknown local source. It does not consult or update any speaker profile.
    from .inference.analyzer import align_tokens
    segments=align_tokens(prediction.get('transcription',[]),
        [{'start':0.,'end':duration,'speaker_local_id':'unresolved'}],{},duration,60.)
    units=[]
    for segment in segments:
        for raw in segment.get('_units',[]):
            u={key:raw[key] for key in ('text','raw_text') if key in raw}
            for key in ('start','end','ownership_anchor'):u[key]=min(duration,max(0.,float(raw[key])))+offset
            u.update(source_track_id=track_id,confidence=None);units.append(u)
    return units


class SeparatorFailure(RuntimeError):
    def __init__(self,code,detail=''):
        self.code=code
        super().__init__(detail or code)


def overlap_readiness(config):
    backend=config.get('overlap_backend','none')
    if backend=='none':return {'ready':True,'status':'disabled','checks':'not_requested','errors':[]}
    if backend=='moss_td_experimental':
        from .moss_td_assets import moss_td_readiness
        result=moss_td_readiness(config)
        return {**result,'status':'configured_experimental' if result['ready'] else 'configuration_error',
            'checks':'required_local_files_and_sizes; pinned hashes/dependencies checked at execution',
            'errors':[] if result['ready'] else [result['reason']]}
    if backend=='dicow_en_experimental':
        from .dicow_assets import dicow_readiness
        result=dicow_readiness(config)
        return {**result,'status':'configured_experimental' if result['ready'] else 'configuration_error',
            'checks':'required_local_files_and_sizes; runtime hashes/dependencies checked at execution',
            'errors':[] if result['ready'] else [result['reason']]}
    errors=[]
    python=config.get('overlap_python')
    if not python or not Path(python).is_file():errors.append('missing_overlap_runtime')
    weights=Path(config.get('model_dir',ROOT/'models'))/'mossformer2/model-weights-only.pt'
    if not weights.is_file():errors.append('missing_overlap_weights')
    source=Path(config.get('overlap_source_dir') or ROOT/'vendor/clearvoice-research/source/clearvoice/clearvoice/models')
    if not (source/'mossformer2_ss/mossformer2.py').is_file():errors.append('missing_overlap_source')
    return {'ready':not errors,'status':'configured_experimental' if not errors else 'configuration_error',
            'checks':'required_local_files; runtime imports and model load validated at execution','errors':errors}


def _validate_separator_response(separated,jobs,directory):
    import soundfile as sf
    if isinstance(separated,dict) and separated.get('error'):
        error=separated['error'];raise SeparatorFailure(error.get('code','separator_failed'),error.get('detail',''))
    if not isinstance(separated,dict) or not isinstance(separated.get('groups'),list) or len(separated['groups'])>16:
        raise ValueError('Malformed separator group response')
    known={job['overlap_group_id']:job for job in jobs};seen=set();total=0
    for group in separated['groups']:
        gid=group.get('overlap_group_id')
        if gid not in known or gid in seen:raise ValueError('Unexpected/duplicate separator group')
        seen.add(gid);region=known[gid];duration=region['support_end']-region['support_start']
        tracks=group.get('tracks')
        if not isinstance(tracks,list) or len(tracks)>32:raise ValueError('Invalid separator track count')
        local=set()
        for track in tracks:
            if not isinstance(track.get('scope_id'),str) or len(track['scope_id'])>100 or track.get('source_slot') not in (0,1):
                raise ValueError('Invalid separator source scope')
            key=(track['scope_id'],track['source_slot'])
            if key in local:raise ValueError('Duplicate separator source scope')
            local.add(key)
            if not all(isinstance(track.get(k),(int,float)) and math.isfinite(track[k]) for k in ('start','end')) or not 0<=track['start']<track['end']<=duration+.001:
                raise ValueError('Separator source interval outside support')
            path=Path(track['path']).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():raise ValueError('Invalid separator audio path')
            info=sf.info(path)
            if info.samplerate!=16000 or info.channels!=1 or not 0<info.duration<=12 or abs(info.duration-(track['end']-track['start']))>.001:
                raise ValueError('Invalid separator audio duration')
        total+=len(tracks)
    if total>64:raise ValueError('Separator batch track limit exceeded')
    return separated


def analyze_overlap_candidates(wav_path,turns,transcribe_callback,config):
    import soundfile as sf
    import numpy as np
    backend=config.get('overlap_backend','none')
    if backend=='moss_td_experimental':
        from .moss_td_candidates import analyze_moss_td_candidates
        return analyze_moss_td_candidates(wav_path,turns,transcribe_callback,config)
    if backend=='dicow_en_experimental':
        from .dicow_candidates import analyze_dicow_candidates
        return analyze_dicow_candidates(wav_path,turns,transcribe_callback,config)
    if backend not in ('none','mossformer2_experimental'):raise ValueError('Unknown experimental overlap backend')
    info=sf.info(wav_path)
    if info.samplerate!=16000 or info.channels!=1 or not 0<info.duration<=60.01:raise ValueError('Expected bounded mono16k candidate input')
    regions,jobs=plan_overlap_regions(turns,info.duration,config)
    if backend=='mossformer2_experimental':jobs=plan_moss_support_jobs(regions,turns,config)
    payload={'regions':regions,'tracks':[],'metadata':{'enabled':backend!='none','backend':backend,
        'candidate_only':True,'identity_status':'unresolved','profile_update_eligible':False,
        'time_axis':'analyzer_window','original_mixture_retained':True,
        'note':'Experimental separate-source candidates; original mixture transcript remains unchanged. Source slots are not people.'}}
    if backend=='none' or not jobs:return validate_candidates(payload,info.duration)
    python=config.get('overlap_python')
    readiness=overlap_readiness(config);payload['metadata']['readiness']=readiness
    if not readiness['ready']:
        for region in jobs:region.update(status='separation_failed',reason_code=readiness['errors'][0],failure_reason=','.join(readiness['errors']))
        return validate_candidates(payload,info.duration)
    temp_parent=Path(config['data_dir'])/'tmp' if config.get('data_dir') else None
    if temp_parent is not None:temp_parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='overlap-candidates-',dir=temp_parent) as td:
        td=Path(td);requests=[]
        for index,region in enumerate(jobs):
            start=round(region['support_start']*16000);end=round(region['support_end']*16000)
            samples,sr=sf.read(wav_path,start=start,frames=end-start,dtype='float32')
            if not np.isfinite(samples).all():raise ValueError('Nonfinite overlap input')
            if not len(samples) or float(np.max(np.abs(samples)))<=1e-9:
                region.update(status='needs_more_evidence',reason_code='silent_overlap_input')
                continue
            path=td/f'mixture-{index}.wav';sf.write(path,samples,sr,subtype='FLOAT')
            requests.append({'overlap_group_id':region['overlap_group_id'],'path':str(path)})
        if not requests:return validate_candidates(payload,info.duration)
        output=td/'result.json';request=td/'request.json'
        request.write_text(json.dumps({'config':config,'jobs':requests,'output':str(output)}))
        limits=SimpleNamespace(data_dir=td,disk_reserve_bytes=int(config.get('disk_reserve_bytes',2*1024**3)),
            process_memory_limit_bytes=int(config.get('process_memory_limit_bytes',6*1024**3)),cpu_threads=min(2,int(config.get('overlap_threads',2))))
        try:
            run_bounded([str(Path(python).absolute()),str(ROOT/'audio_backend/overlap_runner.py'),str(request)],limits,
                timeout=min(600,max(1,float(config.get('overlap_timeout_seconds',300)))),stdout_path=td/'runtime.log')
            if not output.is_file() or output.stat().st_size>1024**2:raise RuntimeError('Missing/oversized separator response')
            separated=_validate_separator_response(json.loads(output.read_text()),jobs,td)
        except InterruptedError:
            raise
        except Exception as exc:
            code=(exc.code if isinstance(exc,SeparatorFailure) else 'separator_timeout' if isinstance(exc,TimeoutError) else 'separator_resource_guard' if 'code 75' in str(exc) or 'memory' in str(exc).lower() or 'reserve' in str(exc).lower() else 'invalid_separator_output' if isinstance(exc,(ValueError,KeyError)) else 'separator_runtime_failed')
            for region in jobs:region.update(status='separation_failed',reason_code=code,failure_reason=f'{type(exc).__name__}: {exc}'[:500])
            return validate_candidates(payload,info.duration)
        # The child has exited before any ASR model callback is invoked.
        payload['metadata']['separator_runtime']=separated.get('metadata',{})
        by_id={r['overlap_group_id']:r for r in jobs}
        for group in separated.get('groups',[]):
            region=by_id.get(group.get('overlap_group_id'))
            if region is None:raise ValueError('Unexpected overlap group from separator')
            base=region['support_start'];region['status']='candidate_tracks_unverified'
            region['continuity_status']='matched_scope_only' if group.get('all_seams_matched') else 'uncertain_seams'
            region['reason_code']='unverified_text_recovery' if group.get('all_seams_matched') else 'ambiguous_source_continuity'
            for raw in group.get('tracks',[]):
                path=Path(raw['path']).resolve()
                if not path.is_relative_to(td.resolve()) or not path.is_file():raise ValueError('Invalid separator audio path')
                audio_info=sf.info(path)
                if audio_info.samplerate!=16000 or audio_info.channels!=1 or not 0<audio_info.duration<=12:raise ValueError('Invalid separator audio bounds')
                start=base+float(raw['start']);end=min(info.duration,base+float(raw['end']))
                track_id=f"{region['overlap_group_id']}:{raw['scope_id']}:source-{raw['source_slot']}"
                track={'source_track_id':track_id,'overlap_group_id':region['overlap_group_id'],
                    'scope_id':str(raw['scope_id']),'source_slot':int(raw['source_slot']),
                    'start':start,'end':end,'support_start':start,'support_end':end,
                    'text':'','raw_text':'','_units':[],'identity_status':'unresolved','quality_status':'unverified','reason_code':'unverified_candidate_text',
                    'speaker_id':None,'profile_update_eligible':False,
                    'provenance':{**group.get('provenance',{}),'asr_backend':config.get('asr_backend','whisper'),
                    'timestamp_semantics':'Native ASR token anchors where available; ends approximate, source identity unresolved'}}
                try:
                    prediction=transcribe_callback(str(path));text=' '.join(row.get('text','') for row in prediction.get('transcription',[]))
                    track.update(text=text,raw_text=text,_units=_native_units(prediction,audio_info.duration,start,track_id))
                    script=config.get('chinese_script','simplified')
                    if script!='as_is':
                        from opencc import OpenCC
                        converter=OpenCC('t2s' if script=='simplified' else 's2t')
                        track['text']=converter.convert(text)
                        for unit in track['_units']:
                            unit['raw_text']=unit['text'];unit['text']=converter.convert(unit['text'])
                    if not text.strip():track.update(quality_status='no_text',reason_code='no_text_recovered')
                except InterruptedError:
                    raise
                except Exception as exc:
                    track['quality_status']='asr_failed';track['reason_code']='candidate_asr_failed';track['failure_reason']=f'{type(exc).__name__}: {exc}'[:500]
                payload['tracks'].append(track)
            if not group.get('tracks'):region.update(status='unresolved_no_sources',reason_code='no_separated_sources')
        for region in jobs:
            if region['status']=='unresolved':region.update(status='unresolved_no_sources',reason_code='no_separated_sources')
    return validate_candidates(payload,info.duration)
