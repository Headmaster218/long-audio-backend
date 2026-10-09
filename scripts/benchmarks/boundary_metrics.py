"""Edit alignment restricted to a predeclared gold-time boundary neighborhood.

Unlike cropping predicted segments, this compares exactly the same gold words.
Insertions attach to the next reference item (or final item at utterance end).
It does not claim word timing accuracy for ASR output.
"""
from scripts.benchmarks.score_predictions import normalize


def aligned_errors(reference,hypothesis,selected):
    n=len(reference);m=len(hypothesis)
    rows=[list(range(m+1))];back={}
    for i,r in enumerate(reference,1):
        row=[i]
        for j,h in enumerate(hypothesis,1):
            candidates=[(rows[-1][j-1]+int(r!=h),'match' if r==h else 'substitution'),
                        (rows[-1][j]+1,'deletion'),(row[-1]+1,'insertion')]
            value,op=min(candidates,key=lambda x:x[0]);row.append(value);back[i,j]=op
        rows.append(row)
    edits=[];i=n;j=m
    while i or j:
        op=back[i,j] if i and j else ('deletion' if i else 'insertion')
        if op=='insertion':edits.append((min(i,n-1),op,hypothesis[j-1]));j-=1
        elif op=='deletion':edits.append((i-1,op,reference[i-1]));i-=1
        else:edits.append((i-1,op,(reference[i-1],hypothesis[j-1])));i-=1;j-=1
    counts={k:0 for k in ('match','substitution','deletion','insertion')}
    detail=[]
    for index,op,value in reversed(edits):
        if index in selected:counts[op]+=1;detail.append({'reference_index':index,'operation':op,'value':value})
    den=len(selected);errors=sum(counts[k]for k in ('substitution','deletion','insertion'))
    return {'error_rate':errors/den if den else None,'reference_items':den,'errors':errors,
            'lexical_coverage':counts['match']/den if den else None,'counts':counts,'edits':detail}


def boundary_score(words,hypothesis,start=25,end=35):
    refs=[];chars=[];word_selected=set();char_selected=set()
    for w in words:
        include=start<=(w['start']+w['end'])/2<end
        normalized=normalize(w['text']).strip()
        for token in normalized.split():
            if include:word_selected.add(len(refs))
            refs.append(token)
        for c in ''.join(normalized.split()):
            if include:char_selected.add(len(chars))
            chars.append(c)
    hyp=normalize(hypothesis)
    return {'gold_interval':[start,end],'wer':aligned_errors(refs,hyp.split(),word_selected),
            'cer':aligned_errors(chars,list(''.join(hyp.split())),char_selected),
            'method':'Whole-excerpt minimum-edit alignment restricted to source-gold word midpoint interval; insertions assigned to next reference item. No predicted time interpolation.'}
