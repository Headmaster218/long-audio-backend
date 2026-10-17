"""Bounded lexical ownership and timestamp-constrained overlap alignment.

Times are native model estimates, never forced-alignment claims. This module
operates on a few seconds of recognition context, not an entire recording.
"""
from __future__ import annotations
import unicodedata
import math


def candidate_core_bounds(options,turns,duration):
    """Plan candidates against the same cut the worker later commits.

    Diarization is available inside the analyzer before optional expensive
    candidate work. Primary ASR ownership/cut behavior remains unchanged.
    """
    options=options or {}
    start=float(options.get('candidate_core_start',0.))
    end=float(options.get('candidate_core_end',duration))
    if not all(math.isfinite(x) for x in (start,end,duration)) or not 0<=start<end<=duration+.01:
        raise ValueError('Invalid candidate owned-core bounds')
    end=min(end,duration)
    if options.get('candidate_core_apply_safe_cut',False):
        lower=float(options['candidate_core_cut_lower'])
        if not math.isfinite(lower) or lower<start:raise ValueError('Invalid candidate core cut lower bound')
        end=safe_cut(turns,end,lower,duration)
    return start,end


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold()
                   if c.isalnum())


def committed_punctuation(unit, previous):
    """Only exact punctuation/time repeats on the same source may be removed.

    Lexical seam alignment intentionally ignores punctuation. Its seam can
    otherwise re-admit punctuation immediately following an already committed
    word. Do not broaden this to repeated words, approximate times, or voices.
    """
    text=unit['text'].strip()
    if not text or not all(unicodedata.category(c).startswith('P') for c in text):
        return False
    return any(p.get('emitted') and p.get('source_track_id')==unit.get('source_track_id')
               and p['text'].strip()==text
               and abs(p['start']-unit['start'])<=1e-6
               and abs(p['end']-unit['end'])<=1e-6 for p in previous)


def safe_cut(turns, target, lower, duration, min_silence=.2):
    """Prefer the latest neural-speech gap before the nominal core boundary."""
    if target >= duration-1e-5 or target <= lower: return target
    intervals=[]
    for t in sorted(turns,key=lambda x:x['start']):
        a=max(0.,float(t['start']));b=min(duration,float(t['end']))
        if b<=a:continue
        if intervals and a<=intervals[-1][1]:intervals[-1][1]=max(b,intervals[-1][1])
        else:intervals.append([a,b])
    if not intervals:return target
    gaps=[];cursor=0.
    for a,b in intervals+[[duration,duration]]:
        if a-cursor>=min_silence:
            lo=max(lower,cursor+.05);hi=min(target,a-.05)
            if hi>=lo:gaps.append(min(hi,max(lo,(cursor+a)/2)))
        cursor=max(cursor,b)
    return max(gaps) if gaps else target


def overlap_matches(previous,current,max_shift=1.25):
    """Ordered exact lexical anchors, weighted toward native-time agreement.

    A single repeated word cannot align to a distant repetition. Punctuation is
    ignored for matching but preserved in the selected recognizer hypothesis.
    No fuzzy text replacements or language-specific vocabulary are introduced.
    """
    n=len(previous);m=len(current)
    scores=[[0.]*(m+1) for _ in range(n+1)]
    choices={}
    for i in range(1,n+1):
        p=previous[i-1];pn=normalized(p['text'])
        for j in range(1,m+1):
            c=current[j-1]
            value=scores[i-1][j];choice=0
            if scores[i][j-1]>value:value=scores[i][j-1];choice=1
            distance=abs(p['ownership_anchor']-c['ownership_anchor'])
            track_compatible=p.get('source_track_id')==c.get('source_track_id')
            if track_compatible and pn and pn==normalized(c['text']) and distance<=max_shift:
                matched=scores[i-1][j-1]+2-distance/(max_shift+1)
                if matched>value:value=matched;choice=2
            scores[i][j]=value;choices[i,j]=choice
    result=[];i=n;j=m
    while i and j:
        choice=choices[i,j]
        if choice==2:result.append((i-1,j-1));i-=1;j-=1
        elif choice==0:i-=1
        else:j-=1
    return list(reversed(result))


def lexical_core(result,start,end,previous=None,base=0.,*,retain_lexical=False):
    """Own whole lexical units; keep native timing instead of clipping words.

    `base` maps analyzer time onto the current upload timeline. The bounded tail
    is restart-safe when retained in normal chunk metadata by commit_window.
    """
    if retain_lexical:
        result.pop('_lexical_rejection',None)
        for segment in result['segments']:segment.pop('_lexical_evidence',None)
    # Mixed legacy/new adapters must never silently lose un-tokenized text.
    if any(not s.get('_units') for s in result['segments']):return None
    flattened=[]
    for segment in result['segments']:
        for u in segment.get('_units',[]):
            unit=dict(u)
            unit['_segment']=segment
            if segment.get('source_track_id') is not None:unit.setdefault('source_track_id',segment['source_track_id'])
            for k in ('start','end','ownership_anchor'):unit[k]+=base
            flattened.append(unit)
    if not flattened:return None
    lo=start+base;hi=end+base
    candidates=[u for u in flattened if lo-4<=u['ownership_anchor']<=lo+4][:256]
    prior=[u for u in previous or [] if lo-4<=u['ownership_anchor']<=lo+4][-256:]
    # Independent source branches can reorder in overlapping speech. Align and
    # choose a seam separately for each declared track, never across voices.
    matches=[];seams={}
    tracks={u.get('source_track_id') for u in prior+candidates}
    for track in tracks:
        pi=[i for i,u in enumerate(prior) if u.get('source_track_id')==track]
        ci=[i for i,u in enumerate(candidates) if u.get('source_track_id')==track]
        aligned=[(pi[i],ci[j]) for i,j in overlap_matches([prior[i] for i in pi],[candidates[j] for j in ci])]
        matches.extend(aligned)
        reliable=len(aligned)>=2 or (len(aligned)==1 and
            len(normalized(prior[aligned[0][0]]['text']))>=4 and
            abs(prior[aligned[0][0]]['ownership_anchor']-candidates[aligned[0][1]]['ownership_anchor'])<=.35)
        emitted=[j for i,j in aligned if prior[i].get('emitted')]
        if reliable and emitted:seams[track]=max(emitted)
    candidate_index={id(u):i for i,u in enumerate(candidates)}
    owned=[]
    for u in flattened:
        anchor=u['ownership_anchor']
        keep=lo<=anchor<hi
        idx=candidate_index.get(id(u))
        seam=seams.get(u.get('source_track_id'))
        if seam is not None and idx is not None and anchor<hi:
            keep=idx>seam
        already_committed=committed_punctuation(u,prior)
        if already_committed:keep=False
        # Carry committed status through another small core/restart-safe tail.
        u['emitted']=keep or already_committed
        if keep:owned.append(u)
    capture=None
    if retain_lexical:
        from .lexical_evidence import Capture
        capture=Capture(lo,hi,base)
    assembled=[]
    for u in owned:
        original=u['_segment']
        # No speaker labels propagate across independently diarized windows.
        if assembled and assembled[-1]['_source'] is original:
            s=assembled[-1]
            for key,value in [('text',u['text']),('raw_text',u.get('raw_text',u['text']))]:
                separator=' ' if u.get('unit_kind')=='native_phrase' and s[key] and value and not s[key][-1].isspace() and not value[0].isspace() else ''
                s[key]+=separator+value
            s['end']=max(s['end'],u['end']-lo)
        else:
            s={k:v for k,v in original.items() if k!='_units'}
            s.update(start=u['start']-lo,end=u['end']-lo,text=u['text'],
                     raw_text=u.get('raw_text',u['text']),_source=original)
            assembled.append(s)
        if type(original.get('_primary_parent_ordinal')) is int and type(u.get('_primary_unit_ordinal')) is int:
            s.setdefault('_primary_units', []).append([original['_primary_parent_ordinal'], u['_primary_unit_ordinal']])
        if capture is not None:capture.add(s,u)
    for s in assembled:
        s.pop('_source');s['text']=s['text'].strip();s['raw_text']=s['raw_text'].strip()
    tail=[]
    for u in flattened:
        if hi-4<=u['ownership_anchor']<=hi+4:
            tail.append({k:u[k] for k in ('start','end','text','ownership_anchor','emitted','source_track_id') if k in u})
    meta=result.setdefault('metadata',{})
    meta['boundary_tail']=tail[-256:]
    meta['boundary_alignment_matches']=len(matches)
    meta['boundary_alignment_used']=bool(seams)
    meta['boundary_policy']='whole lexical units; time-constrained overlap sequence alignment; native timing retained'
    assembled=[s for s in assembled if s['text']]
    if capture is not None:result['_lexical_rejection']=capture.finish(assembled)
    return assembled
