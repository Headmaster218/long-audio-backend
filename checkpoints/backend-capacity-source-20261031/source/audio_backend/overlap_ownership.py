"""Separate candidate-source ownership; never modifies authoritative mixture rows."""
from __future__ import annotations
import json
import math
from .boundaries import lexical_core


def validate_candidates(bundle,duration):
    if not isinstance(bundle,dict):raise ValueError('Invalid overlap candidate envelope')
    regions=bundle.get('regions',[]);tracks=bundle.get('tracks',[])
    if not isinstance(regions,list) or not isinstance(tracks,list) or len(regions)>4096 or len(tracks)>256:
        raise ValueError('Overlap candidate count exceeded')
    group_ids=set()
    for region in regions:
        group=region.get('overlap_group_id')
        if not isinstance(group,str) or not group or len(group)>256 or group in group_ids:
            raise ValueError('Invalid or duplicate candidate group')
        group_ids.add(group)
        kind=region.get('region_kind','detected_overlap')
        if kind not in ('detected_overlap','speech_support_probe'):raise ValueError('Invalid candidate region kind')
        count=region.get('detected_source_count')
        minimum=1 if kind=='speech_support_probe' else 2
        if type(count) is not int or not minimum<=count<=4096:raise ValueError('Invalid detected source count')
        model_count=region.get('model_label_count')
        if model_count is not None and (type(model_count) is not int or not 1<=model_count<=8):raise ValueError('Invalid model label count')
        if region.get('original_mixture_retained') is not True:raise ValueError('Original mixture must be retained')
    track_ids=set()
    for track in tracks:
        from .overlap_store import validate_partial_track
        validate_partial_track(track, regions)
        tid=track.get('source_track_id')
        if not isinstance(tid,str) or not tid or len(tid)>256 or tid in track_ids:
            raise ValueError('Invalid or duplicate candidate track')
        track_ids.add(tid)
        if track.get('overlap_group_id') not in group_ids:raise ValueError('Candidate track has no region')
        if not isinstance(track.get('text',''),str) or len(track.get('text',''))>100000:raise ValueError('Invalid candidate text')
        units=track.get('_units',[])
        if not isinstance(units,list) or len(units)>4096 or (track.get('text','').strip() and not units):
            raise ValueError('Candidate text requires bounded native lexical units')
        if track.get('speaker_id') is not None or 'speaker_embedding' in track or track.get('profile_update_eligible') is not False or track.get('identity_status')!='unresolved':
            raise ValueError('Separated candidates cannot enroll or claim a global identity')
        if type(track.get('source_slot')) is not int or not 0<=track['source_slot']<8:raise ValueError('Invalid candidate source slot')
        if not isinstance(track.get('scope_id'),str) or len(track['scope_id'])>200:raise ValueError('Invalid source scope')
        for unit in units:
            if not isinstance(unit.get('text'),str):raise ValueError('Invalid candidate lexical text')
            if unit.get('source_track_id') not in (None,tid):raise ValueError('Candidate lexical track mismatch')
            for key in ('start','end','ownership_anchor'):
                value=unit.get(key)
                if not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=duration+.15:
                    raise ValueError('Candidate lexical timestamp outside input')
            if unit['end']<unit['start']:raise ValueError('Reversed candidate lexical interval')
    for item in regions+tracks:
        for key in ('start','end','support_start','support_end'):
            value=item.get(key,item.get(key.removeprefix('support_')))
            if not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=duration+.15:
                raise ValueError('Candidate timestamp outside analyzer input')
        if item['end']<=item['start']:raise ValueError('Empty/reversed candidate interval')
    if 'diagnostics' in bundle:
        from .overlap_diagnostics import validate as validate_diagnostics
        validate_diagnostics(bundle['diagnostics'],regions)
    if len(json.dumps(bundle,allow_nan=False).encode('utf-8'))>2*1024**2:raise ValueError('Candidate output exceeds2MiB')
    return bundle


def failed_candidates(regions,backend,error):
    converted=[]
    for index,r in enumerate(regions):
        converted.append({'overlap_group_id':f'group-{index}', 'start':r['start'],'end':r['end'],
            'support_start':r['start'],'support_end':r['end'],
            'detected_speaker_local_ids':r.get('speaker_local_ids',[]),
            'detected_source_count':r.get('detected_source_count'), 'capacity':2,
            'status':'capacity_exceeded' if r.get('detected_source_count',0)>2 else 'separation_failed',
            'original_mixture_retained':True,'error':str(error)[:1000],
            'reason_code':'source_capacity_exceeded' if r.get('detected_source_count',0)>2 else 'candidate_adapter_failed',
            'failure_reason':str(error)[:1000]})
    return {'regions':converted,'tracks':[], 'metadata':{'backend':backend,'status':'failed',
        'error':str(error)[:1000],'original_mixture_retained':True,'profile_update_eligible':False}}


def own_candidates(bundle,start,end,base=0.):
    """Keep every core-intersecting unresolved region and each source independently.

    Source slots are analysis-window-local. Without verified acoustic continuity,
    identically named slots from neighboring windows MUST NOT be aligned together.
    """
    regions=[];valid_groups=set();region_bounds={}
    for region in bundle.get('regions',[]):
        lo=max(start,region['start']);hi=min(end,region['end'])
        if hi<=lo:continue
        owned=dict(region,start=lo-start,end=hi-start,
            support_start=region.get('support_start',region['start'])-start,
            support_end=region.get('support_end',region['end'])-start,
            original_mixture_retained=True)
        regions.append(owned);valid_groups.add(region['overlap_group_id'])
        region_bounds[region['overlap_group_id']]=(lo,hi)
    tracks=[];boundary_unresolved=set()
    for track in bundle.get('tracks',[]):
        primary=track['overlap_group_id']
        provenance=dict(track.get('provenance',{}))
        related=provenance.get('related_overlap_group_ids') or [primary]
        related=list(dict.fromkeys([primary,*related]))
        owned_related=sorted((gid for gid in related if gid in valid_groups),key=lambda gid:region_bounds[gid][0])
        if not owned_related:continue
        home=primary if primary in valid_groups else owned_related[0]
        intervals=[region_bounds[gid] for gid in owned_related]
        lo=max(start,track['start'],min(a for a,b in intervals))
        hi=min(end,track['end'],max(b for a,b in intervals))
        if hi<=lo and provenance.get('association_basis')!='support_not_model_timing':continue
        if provenance.get('related_overlap_group_ids'):
            # Public links refer only to rows materialized in this window/core.
            provenance['related_overlap_group_ids']=owned_related
        if home!=primary:
            provenance['evidence_routing_note']='shared candidate rehomed to an owned related overlap region'
        common=dict(track,overlap_group_id=home,provenance=provenance,
            support_start=track.get('support_start',track['start'])-start,
            support_end=track.get('support_end',track['end'])-start,
            speaker_id=None,profile_update_eligible=False,identity_status='unresolved',
            source_continuity='analysis_window_scoped_unverified')
        if provenance.get('association_basis')=='support_not_model_timing':
            # Association is to raw support, NOT evidence of phrase placement.
            # Keep model intervals unchanged and route the entire support once.
            anchor=provenance.get('support_owner_anchor')
            if not isinstance(anchor,(int,float)) or not start<=anchor<end:continue
            phrase_units=track.get('_units',[])
            selected=dict(provenance,related_overlap_group_ids=owned_related,
                native_unit_kind='native_phrase',native_time_axis='core_relative',
                support_owner_anchor=anchor-start,boundary_uncertain=True,
                native_phrase_intervals=[dict(start=u['start']-start,end=u['end']-start,
                    ownership_anchor=u['ownership_anchor']-start,text=u['text'],
                    unit_kind='native_phrase',boundary_uncertain=True) for u in phrase_units])
            common.update(start=track['start']-start,end=track['end']-start,provenance=selected,
                source_continuity='support_scoped_unverified')
            common.pop('_units',None);tracks.append(common)
            continue
        if track.get('_units'):
            # Eligibility is interval intersection because CTC starts may precede
            # diarizer onset. Core ownership uses the declared native/routing
            # anchor; nativephrase start/end are never interpolated or replaced.
            eligible=[u for u in track['_units'] if any(
                min(u['end'],b)>max(u['start'],a) or
                (u['end']==u['start'] and a<=u['ownership_anchor']<b) for a,b in intervals)]
            prepared=dict(track,_units=eligible)
            owned=lexical_core({'segments':[prepared],'metadata':{}},start,end,previous=[],base=base) if eligible else []
            if owned:
                owned_units=[u for u in eligible if start<=u['ownership_anchor']<end]
                actual_links=set()
                for unit in owned_units:
                    links=unit.get('related_overlap_group_ids',[primary])
                    actual_links.update(gid for gid in links if gid in valid_groups and
                        min(unit['end'],region_bounds[gid][1])>max(unit['start'],region_bounds[gid][0]))
                if not actual_links:actual_links={home}
                actual_links=sorted(actual_links,key=lambda gid:region_bounds[gid][0])
                actual_home=home if home in actual_links else actual_links[0]
                selected_provenance=dict(provenance,related_overlap_group_ids=actual_links)
                if actual_home!=primary:selected_provenance['evidence_routing_note']='shared candidate rehomed to an owned related overlap region'
                phrase_units=[u for u in owned_units if u.get('unit_kind')=='native_phrase']
                if phrase_units:
                    selected_provenance.update(native_unit_kind='native_phrase',native_time_axis='core_relative',
                        ownership_anchor_kind='overlap_evidence_routing',
                        boundary_uncertain=any(u.get('boundary_uncertain',False) for u in phrase_units),
                        native_phrase_intervals=[dict(start=u['start']-start,end=u['end']-start,
                            ownership_anchor=u['ownership_anchor']-start,text=u['text'],unit_kind='native_phrase',
                            ownership_anchor_kind=u.get('ownership_anchor_kind','native_phrase_start'),
                            boundary_uncertain=bool(u.get('boundary_uncertain',False)))for u in phrase_units])
                for segment in owned:
                    item=dict(common,**{k:segment[k] for k in ('start','end','text','raw_text')})
                    item['overlap_group_id']=actual_home;item['provenance']=selected_provenance
                    item.pop('_units',None);tracks.append(item)
            elif eligible:
                boundary_unresolved.update(owned_related)
        else:
            common.update(start=lo-start,end=hi-start);common.pop('_units',None);tracks.append(common)
    for region in regions:
        gid=region['overlap_group_id']
        if gid in boundary_unresolved:
            region['boundary_uncertain']=True
            if any(t['overlap_group_id']==gid or gid in t.get('provenance',{}).get('related_overlap_group_ids',[]) for t in tracks):
                region['reason_code']='partial_candidate_boundary_ownership'
            else:
                region['status']='unresolved_boundary_evidence'
                region['reason_code']='candidate_phrase_owned_outside_core'
    metadata=dict(bundle.get('metadata',{}),time_axis='core_relative',
        boundary_policy='whole native lexical units per window-scoped source; no unverified cross-window source joining',
        source_continuity='not_established',original_mixture_retained=True,profile_update_eligible=False,
        context_policy='native interval intersection with detected overlap; anchor-owned by core; intervals and CTC ends remain approximate')
    result={'regions':regions,'tracks':tracks,'metadata':metadata}
    if 'diagnostics' in bundle:
        from .overlap_diagnostics import own
        result['diagnostics']=own(bundle['diagnostics'],start,end,regions)
    return result
