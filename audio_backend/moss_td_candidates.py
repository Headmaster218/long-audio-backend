"""Raw-mixture support-associated MOSS-TD hypotheses; never person identities.

Native activity only gates bounded supports. Model phrase times are retained but
are not used to discard minority words or assert their placement in overlap.
"""
from __future__ import annotations
import json, math, tempfile, hashlib
from pathlib import Path
from types import SimpleNamespace
from .overlap import overlapping_regions
from .overlap_candidates import validate_candidates,moss_support_activity
from .resources import run_bounded
from . import overlap_diagnostics
ROOT=Path(__file__).resolve().parents[1]


class UnsafeCppEnvironment(RuntimeError):pass


class CandidateEnvelopeBudgetError(ValueError):pass


def reject_cpp_loader_overrides(config):
    if config.get('moss_td_runtime')=='cpp':
        from .moss_td_cpp_assets import check_runtime_environment
        try:check_runtime_environment()
        except ValueError as exc:raise UnsafeCppEnvironment(str(exc)) from exc


def plan_supports(turns,duration,config):
    start=float(config.get('candidate_core_start',0));end=float(config.get('candidate_core_end',duration))
    budget=float(config.get('moss_td_max_seconds',40));maximum=float(config.get('moss_td_max_support_seconds',20));capacity=int(config.get('moss_td_capacity',8))
    if not all(math.isfinite(x) for x in (start,end,budget,maximum)) or not 0<=start<end<=duration+.001 or not 0<budget<=60 or not 0<maximum<=20 or not 2<=capacity<=8:
        raise ValueError('Invalid MOSS support bounds/budget/capacity')
    trigger=config.get('moss_td_trigger','native_overlap')
    if trigger not in ('native_overlap','speech_probe'):raise ValueError('Invalid MOSS trigger')
    originals=overlapping_regions(turns,duration,capacity=capacity)
    regions=[];plans=[];cursor=start
    while cursor<end-1e-8:
        stop=min(end,cursor+maximum);sid=f'support-{round(cursor*16000)}';linked=[]
        activity=moss_support_activity(turns,cursor,stop)
        for original in originals:
            lo=max(cursor,original['start']);hi=min(stop,original['end'])
            if hi<=lo:continue
            r=dict(overlap_group_id=f'group-{len(regions)}',region_kind='detected_overlap',start=lo,end=hi,
                support_start=cursor,support_end=stop,shared_support_id=sid,
                detected_source_count=original['detected_source_count'],
                detected_speaker_local_ids=original['speaker_local_ids'],capacity=capacity,
                original_mixture_retained=True,status='unresolved',reason_code='not_yet_attempted',support_activity=activity)
            regions.append(r);linked.append(r)
        if not linked and trigger=='speech_probe' and activity['max_simultaneous_speakers']>=1:
            r=dict(overlap_group_id=f'group-{len(regions)}',region_kind='speech_support_probe',
                start=cursor,end=stop,support_start=cursor,support_end=stop,shared_support_id=sid,
                detected_source_count=activity['max_simultaneous_speakers'],
                detected_speaker_local_ids=activity['speaker_local_ids'],capacity=capacity,
                original_mixture_retained=True,status='unresolved',reason_code='not_yet_attempted',support_activity=activity)
            regions.append(r);linked.append(r)
        if linked:
            labels=set(activity['speaker_local_ids'])
            reason=('native_support_capacity_exceeded' if len(labels)>capacity else
                    'analysis_budget_exhausted' if stop-cursor>budget+1e-7 or len(plans)>=4 else None)
            if reason:
                for r in linked:r.update(status='unresolved_capacity' if len(labels)>capacity else 'unresolved_budget_limit',reason_code=reason)
            else:
                budget-=stop-cursor
                plans.append(dict(shared_support_id=sid,start=cursor,end=stop,regions=linked,native_distinct_speakers=len(labels),native_peak_speakers=activity['max_simultaneous_speakers']))
        cursor=stop
    return regions,plans


def parse_support(plan,phrases,capacity,provenance=None):
    if not isinstance(phrases,list) or not 0<len(phrases)<=128:raise ValueError('Missing/oversized MOSS phrase list')
    labels=[];tracks=[];duration=plan['end']-plan['start']
    for i,p in enumerate(phrases):
        label=p.get('speaker_local_id');text=p.get('text');raw_text=p.get('raw_text',text);a=p.get('start');b=p.get('end')
        if not isinstance(label,str) or not label or len(label)>100 or not isinstance(text,str) or not text.strip() or len(text)>10000 or not isinstance(raw_text,str) or len(raw_text)>10000:
            raise ValueError('Invalid MOSS phrase text/local label')
        # JSON can decode lone surrogates which cannot be bound to SQLite or
        # emitted as UTF-8. Preserve them only in opaque diagnostic JSON.
        for value in (label,text,raw_text):value.encode('utf-8')
        if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (a,b)) or not 0<=a<b<=duration+.001:
            raise ValueError('Invalid MOSS native phrase interval')
        if label not in labels:labels.append(label)
        if len(labels)>capacity:raise ValueError('MOSS output capacity exceeded')
        a+=plan['start'];b+=plan['start'];sid=plan['shared_support_id'];tid=f'{sid}-phrase-{i}'
        links=[r['overlap_group_id'] for r in plan['regions']]
        prov=dict(provenance or {},shared_support_id=sid,related_overlap_group_ids=links,
            association_basis='support_not_model_timing',support_owner_anchor=plan['start'],
            timestamp_semantics='unverified native coarse phrase times',boundary_uncertain=True,
            source_identity_semantics='anonymous model label within this support only',
            model_local_speaker_label=label,output_normalization='none',capacity=capacity)
        unit=dict(start=a,end=b,ownership_anchor=a,text=text,raw_text=raw_text,source_track_id=tid,
            unit_kind='native_phrase',boundary_uncertain=True)
        tracks.append(dict(source_track_id=tid,overlap_group_id=links[0],start=a,end=b,
            support_start=plan['start'],support_end=plan['end'],text=text,raw_text=raw_text,
            scope_id=sid,source_slot=labels.index(label),speaker_id=None,profile_update_eligible=False,
            identity_status='unresolved',quality_status='unverified',reason_code='support_associated_hypothesis',
            provenance=prov,_units=[unit]))
    multi=len(labels)>=3 or plan.get('native_distinct_speakers',0)>=3
    for track in tracks:
        track['provenance'].update(multi_speaker_quality_unverified=multi,possible_missing_minority=True,emitted_label_count=len(labels),native_detected_label_count=plan.get('native_distinct_speakers',0),native_peak_simultaneous_count=plan.get('native_peak_speakers',0),count_mismatch_uncertain=len(labels)!=plan.get('native_distinct_speakers',len(labels)),capacity_semantics='schema safety bound, not validated speaker recovery')
    return tracks


def analyze_candidates(wav_path,turns,config):
    import soundfile as sf
    info=sf.info(wav_path)
    if info.samplerate!=16000 or info.channels!=1 or not 0<info.duration<=60.01:raise ValueError('MOSS requires bounded 16 kHz mono input')
    regions,plans=plan_supports(turns,info.duration,config)
    payload={'regions':regions,'tracks':[],'diagnostics':[],'metadata':{'backend':'moss_td_experimental',
        'association_basis':'support_not_model_timing','trigger':config.get('moss_td_trigger','native_overlap'),'original_mixture_retained':True,
        'profile_update_eligible':False,'source_continuity':'not_established',
        'boundary_policy':'disjoint core-contained supports; context truncated; native times unverified'}}
    if not plans:return validate_candidates(payload,info.duration)
    parent=Path(config['data_dir'])/'tmp';parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='moss-td-',dir=parent) as temp:
        directory=Path(temp);jobs=[]
        for plan in plans:
            path=directory/(plan['shared_support_id']+'.wav')
            with sf.SoundFile(wav_path) as source:
                source.seek(round(plan['start']*info.samplerate))
                samples=source.read(round(plan['end']*info.samplerate)-round(plan['start']*info.samplerate),dtype='float32')
            sf.write(path,samples,info.samplerate,subtype='FLOAT')
            jobs.append({'support_id':plan['shared_support_id'],'path':str(path)})
        output=directory/'result.json';request=directory/'request.json'
        request.write_text(json.dumps({'config':config,'supports':jobs,'output':str(output)},allow_nan=False))
        limits=SimpleNamespace(data_dir=directory,disk_reserve_bytes=int(config.get('disk_reserve_bytes',5*1024**3)),
            process_memory_limit_bytes=min(int(config.get('moss_td_memory_limit_bytes',3.5*1024**3)),int(config.get('process_memory_limit_bytes',6*1024**3))),cpu_threads=int(config.get('cpu_threads',2)))
        result_bytes=None;association_complete=False
        try:
            reject_cpp_loader_overrides(config)
            run_bounded([str(Path(config['moss_td_python']).absolute()),str(ROOT/'audio_backend/moss_td_runner.py'),str(request)],limits,
                timeout=float(config.get('moss_td_timeout_seconds',300)),stdout_path=directory/'runtime.log')
            if not output.is_file() or output.stat().st_size>2*1024**2:raise ValueError('Missing/oversized MOSS result')
            result_bytes=output.read_bytes()
            if len(result_bytes)>2*1024**2:raise ValueError('Oversized MOSS result')
            result=json.loads(result_bytes);groups=result.get('supports')
            if not isinstance(groups,list):raise ValueError('Wrong MOSS support count')
            indexed={p['shared_support_id']:p for p in plans};seen=set();tracks=[]
            # Capture every safely associated group before validating usable
            # phrases. One bad interval must not erase other raw support output.
            association_errors=False
            # The entire parsed result is bounded to 2 MiB; capture at most the
            # four planned supports. An early bad/duplicate ID must not stop a
            # scan for safely associated later supports.
            for group in groups:
                if not isinstance(group,dict):
                    association_errors=True;continue
                sid=group.get('support_id')
                if not isinstance(sid,str) or sid not in indexed or sid in seen:
                    association_errors=True;continue
                seen.add(sid)
                payload['diagnostics'].append(overlap_diagnostics.support(indexed[sid],group))
            association_complete=not association_errors and len(groups)==len(plans)
            if not association_complete:raise ValueError('Wrong/unexpected/duplicate MOSS supports')
            for group in groups:
                sid=group['support_id']
                if group.get('status')!='ok':
                    evidence={'status':'unresolved','reason_code':overlap_diagnostics.safe_code(group.get('reason_code'),'candidate_schema_failure')}
                    # Missing fields are not observed empty outputs. Count/hash
                    # independently only values actually emitted by the runner.
                    if 'schema_errors' in group:
                        errors=group['schema_errors']
                        if not isinstance(errors,list) or len(errors)>128:raise ValueError('Invalid MOSS schema errors')
                        evidence['schema_error_count']=len(errors)
                    if 'raw_text' in group:
                        raw=group['raw_text']
                        if not isinstance(raw,str) or len(raw)>100000:raise ValueError('Invalid MOSS raw text')
                        evidence['raw_output_sha256']=hashlib.sha256(raw.encode('utf-8')).hexdigest()
                    for r in indexed[sid]['regions']:r.update(evidence)
                    continue
                published=parse_support(indexed[sid],group.get('phrases'),int(config.get('moss_td_capacity',8)),group.get('metadata'))
                tracks.extend(published)
                for r in indexed[sid]['regions']:r['model_label_count']=published[0]['provenance']['emitted_label_count']
            payload['tracks']=tracks
            multi_supports={t['scope_id'] for t in tracks if t['provenance'].get('multi_speaker_quality_unverified')}
            for plan in plans:
                for r in plan['regions']:
                    if r['reason_code']=='not_yet_attempted':r.update(status='unverified_hypothesis',reason_code='multi_speaker_quality_unverified' if plan['shared_support_id'] in multi_supports else 'speech_support_probe_unverified' if r.get('region_kind')=='speech_support_probe' else 'support_not_model_timing')
            if len(json.dumps(payload,allow_nan=False).encode('utf-8'))>2*1024**2:
                raise CandidateEnvelopeBudgetError('Candidate response exceeds byte budget')
            # Validate every usable projection while temporary evidence still
            # exists, including track-count and timestamp/schema constraints.
            validate_candidates(payload,info.duration)
        except InterruptedError:
            raise
        except Exception as exc:
            payload['tracks']=[]
            code='candidate_envelope_budget_exceeded' if isinstance(exc,CandidateEnvelopeBudgetError) else 'unsafe_cpp_loader_environment' if isinstance(exc,UnsafeCppEnvironment) else 'candidate_timeout' if isinstance(exc,TimeoutError) else 'candidate_schema_failure' if isinstance(exc,(ValueError,KeyError,TypeError)) else 'candidate_runtime_or_resource_failure'
            error_path=output.with_suffix('.error.json')
            if error_path.is_file() and error_path.stat().st_size<=8192:
                try:
                    detail=json.loads(error_path.read_text())
                    reason=overlap_diagnostics.safe_code(detail.get('reason_code'),None) if isinstance(detail,dict) else None
                    if reason is not None and len(reason)<=80:code='candidate_'+reason
                except (OSError,ValueError,TypeError):pass
            payload['diagnostics'].append(overlap_diagnostics.batch_failure(plans,output,code,exc,associated=association_complete,observed_result=result_bytes))
            for plan in plans:
                for r in plan['regions']:r.update(status='unresolved',reason_code=code)
    return validate_candidates(payload,info.duration)


def analyze_moss_td_candidates(wav_path,turns,callback,config):
    """Common candidate adapter signature; mixture ASR callback is unused."""
    return analyze_candidates(wav_path,turns,config)
