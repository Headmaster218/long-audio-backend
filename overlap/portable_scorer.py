"""Portable production-derived oracle lexical scorer. No model dependencies.

Per-input permutation is a lexical diagnostic, never persistent identity accuracy.
"""

import unicodedata

_levenshtein = None

def normalize(s):return ''.join(c for c in unicodedata.normalize('NFKC',s).lower() if not unicodedata.category(c).startswith(('P','S')))

def edit(a,b):
 if a==b:return 0
 if _levenshtein is not None:return _levenshtein.distance(a,b)
 # Strip shared edges without changing edit distance.
 lo=0
 while lo<min(len(a),len(b)) and a[lo]==b[lo]:lo+=1
 a=a[lo:];b=b[lo:]
 hi=0
 while hi<min(len(a),len(b)) and a[-hi-1]==b[-hi-1]:hi+=1
 if hi:a=a[:-hi];b=b[:-hi]
 prev=list(range(len(b)+1))
 for i,x in enumerate(a,1):
  cur=[i]
  for j,y in enumerate(b,1):cur.append(min(cur[-1]+1,prev[j]+1,prev[j-1]+(x!=y)))
  prev=cur
 return prev[-1]

def tokens(text,language):
    s=normalize(text)
    return list(''.join(s.split())) if language=='zh' else s.split()

def score(gold,prediction):
    lang=gold['language']
    if lang not in ('zh','en'):raise ValueError('Explicit language required')
    if 'per_speaker_gold' in gold:
        if gold.get('whole_clip_lexical_scoring_eligible') is not True:
            raise ValueError('Partial-boundary gold is diagnostic only, not lexical-score eligible')
        refs=[{'id':sid,'text':row['text']} for sid,row in gold['per_speaker_gold'].items()]
    else:
        if gold.get('whole_clip_lexical_scoring_eligible') is False:
            raise ValueError('Partial-boundary gold cannot be scored')
        refs=gold['speakers']
    if prediction.get('whole_clip_per_speaker_scoring_eligible') is False:
        raise ValueError('Uncertain independent source scopes cannot be scored as whole-clip persistent speaker tracks')
    hyps=prediction['transcripts']
    if len(refs)>8 or len(hyps)>1024:raise ValueError('Excerpt diagnostic supports <=8reference people and <=1024hypothesis tracks')
    rt=[tokens(x['text'],lang) for x in refs]
    ht=[tokens(x['text'],lang) for x in hyps]
    costs=[[edit(r,h) for h in ht] for r in rt]
    # Baseline deletes every reference and inserts every hypothesis. A match
    # replaces exactly those two costs. Reference-subset DP is exact, bounded
    # by reference people, and penalizes extra output tracks as insertions.
    states={0:(sum(map(len,rt))+sum(map(len,ht)),{})}
    for j,h in enumerate(ht):
        following=dict(states)
        for mask,(value,mapping) in states.items():
            for i,r in enumerate(rt):
                if mask&(1<<i):continue
                new=mask|(1<<i);candidate=value+costs[i][j]-len(r)-len(h)
                if new not in following or candidate<following[new][0]:
                    following[new]=(candidate,mapping|{i:j})
        states=following
    errors,mapping=min(states.values(),key=lambda x:x[0])
    assignments=[];used=set(mapping.values())
    for i,r in enumerate(rt):
        j=mapping.get(i);n=costs[i][j] if j is not None else len(r)
        assignments.append({'reference_id':refs[i]['id'],'hypothesis_channel':hyps[j]['channel'] if j is not None else None,'errors':n,'reference_units':len(r),'error_rate':n/len(r) if r else None,'reference_text':refs[i]['text'],'hypothesis_text':hyps[j]['text'] if j is not None else None})
    for j,h in enumerate(ht):
        if j not in used:
            assignments.append({'reference_id':None,'hypothesis_channel':hyps[j]['channel'],'errors':len(h),'reference_units':0,'error_rate':None,'reference_text':None,'hypothesis_text':hyps[j]['text']})
    den=sum(map(len,rt))
    return {'metric':'excerpt_permutation_invariant_CER' if lang=='zh' else 'excerpt_permutation_invariant_WER','errors':errors,'reference_units':den,'error_rate':errors/den if den else None,'reference_speakers':len(refs),'hypothesis_tracks':len(hyps),'oracle_excerpt_assignment':assignments,'identity_claim':False,'assignment_algorithm':'exact reference-subset dynamic programming','limitations':['Best excerpt permutation is an oracle diagnostic, not persistent speaker identity accuracy.','Only fully annotated transcript excerpts are valid; partial-turn gold is not silently scored.','Independent uncertain source scopes require time-scoped recovery evaluation, not fabricated whole-clip identities.','Separate signal-level/source-quality and overlap timing tests are required.']}

def score_population(inputs, predictions, expected_reference_units):
    """Score every frozen input, including absent/failed predictions, with fixed denominator."""
    ids = [item['id'] for item in inputs]
    if len(ids) != len(set(ids)) or any(key not in ids for key in predictions):
        raise ValueError('Duplicate reference input or unexpected prediction')
    rows = []
    for item in inputs:
        prediction = predictions.get(item['id'])
        failure = None
        if prediction is None or (isinstance(prediction, dict) and prediction.get('failure')):
            failure = 'missing' if prediction is None else prediction['failure']
            prediction = {'transcripts': []}
        if not isinstance(prediction, dict) or not isinstance(prediction.get('transcripts'), list):
            raise ValueError('Malformed prediction must be explicitly recorded as a failure')
        row = score(item, prediction)
        rows.append({'input_id': item['id'], 'failure': failure, 'score': row})
    denominator = sum(row['score']['reference_units'] for row in rows)
    if denominator != expected_reference_units:
        raise ValueError('Frozen reference denominator changed')
    errors = sum(row['score']['errors'] for row in rows)
    return {'errors': errors, 'reference_units': denominator,
            'error_rate': errors / denominator if denominator else None,
            'identity_claim': False, 'inputs': rows}

