"""Fixed-denominator scoring, using the qualified backend lexical contract.
Missing predictions are scored as failures, never dropped. Original historical
execution audit is not recreated by this simplified portable scoring command.
"""
import argparse, hashlib, json
from pathlib import Path
from fractions import Fraction
from common import HERE, read, metrics as load_metrics, external_output
metrics = None
MAX_TEXT_BYTES=65536
FAILURES={'model_failure','infrastructure_failure','not_attempted','aborted','unknown'}
STATUSES=FAILURES|{'success','truncated'}
def chars(text):
    if not isinstance(text,str) or len(text.encode('utf-8'))>MAX_TEXT_BYTES:
        raise ValueError('Unbounded/non-string prediction')
    return ''.join(metrics.normalize(text).split())
def edits(reference,hypothesis):
    """Levenshtein unit costs; ties prefer diagonal, deletion, insertion.
    Two rows; tuple stores distance, substitutions, deletions, insertions.
    """
    prev=[(j,0,0,j) for j in range(len(hypothesis)+1)]
    for i,a in enumerate(reference,1):
        row=[(i,0,i,0)]
        for j,b in enumerate(hypothesis,1):
            q=prev[j-1]; cost=int(a!=b)
            diag=(q[0]+cost,q[1]+cost,q[2],q[3])
            q=prev[j]; delete=(q[0]+1,q[1],q[2]+1,q[3])
            q=row[-1]; insert=(q[0]+1,q[1],q[2],q[3]+1)
            row.append(min((diag,delete,insert),key=lambda x:x[0]))
        prev=row
    n,s,d,i=prev[-1]
    return {'errors':n,'substitutions':s,'deletions':d,'insertions':i,'reference_characters':len(reference)}
def repetition(text):
    """First normalized span with minimal period 1..8, >=8 copies, >=32 chars.
    Coordinates are zero-based, end-exclusive NORMALIZED Unicode characters.
    This is suspicion, not a conclusion that a model looped.
    """
    text=chars(text)
    for start in range(len(text)):
        for period in range(1,9):
            count=max(8,(32+period-1)//period)
            if start+period*count>len(text): continue
            block=text[start:start+period]
            # Reject nonprimitive blocks, retaining the minimal period.
            if any(period%d==0 and block==block[:d]*(period//d) for d in range(1,period)): continue
            if text[start:start+period*count] != block*count: continue
            end=start+period*count
            while text[end:end+period]==block: end+=period
            return {'flagged':True,'start':start,'end':end,'period':period,
                    'repetitions':(end-start)//period,'block':block,'span':text[start:end],
                    'coordinates':'normalized_unicode_characters'}
    return {'flagged':False}
def row_score(example,record):
    if set(record) != {'id','arm','status','text','raw','truncated'}: raise ValueError('Exact terminal record fields required')
    if record['status'] not in STATUSES: raise ValueError('Unknown terminal status')
    if type(record['truncated']) is not bool: raise ValueError('Explicit truncation boolean required')
    if record['status']=='success' and record['truncated']: raise ValueError('Inconsistent success/truncation')
    if record['status']=='truncated' and not record['truncated']: raise ValueError('Inconsistent truncation')
    chars(record['text'])
    hypothesis='' if record['status'] in FAILURES else record['text']
    reference=chars(example['reference']); norm=chars(hypothesis)
    counts=edits(reference,norm)
    existing=metrics.lexical(example['reference'],hypothesis)
    if counts['errors']!=existing['character_errors'] or len(reference)!=existing['reference_characters']:
        raise ValueError('Independent edit count differs from qualified lexical')
    return dict(id=example['id'],session=example['session'],stratum=example['stratum'],
        status=record['status'],truncated=record['truncated'],**counts,
        repetition_suspicion=repetition(record['text']),
        diagnostic_partial_errors=(edits(reference,chars(record['text'])) if record['status'] in FAILURES and record['text'] else None))
def aggregate(rows):
    result={k:sum(x[k] for x in rows) for k in ('errors','substitutions','deletions','insertions','reference_characters')}
    result.update(examples=len(rows),cer=result['errors']/result['reference_characters'] if result['reference_characters'] else None,
        failures=sum(x['status'] in FAILURES for x in rows),truncations=sum(x['truncated'] for x in rows),
        repetition_suspicions=sum(x['repetition_suspicion']['flagged'] for x in rows))
    return result

def score(examples,records,meetings):
    ids={x['id'] for x in examples}
    if len(ids)!=len(examples) or not 1<=len(ids)<=64: raise ValueError('Unique bounded cohort required')
    if set(x['session'] for x in examples)!=set(meetings) or len(set(meetings))!=8: raise ValueError('Exactly eight meeting coverage required')
    indexed={}
    for row in records:
        key=(row['arm'],row['id'])
        if row['arm'] not in ('sensevoice','qwen') or row['id'] not in ids or key in indexed: raise ValueError('Duplicate or extra prediction')
        indexed[key]=row
    arms={}
    for arm in ('sensevoice','qwen'):
        rows=[]
        for ex in examples:
            rec=indexed.get((arm,ex['id']),dict(id=ex['id'],arm=arm,status='not_attempted',text='',raw=None,truncated=False))
            rows.append(row_score(ex,rec))
        arms[arm]={'overall':aggregate(rows),'strata':{s:aggregate([x for x in rows if x['stratum']==s]) for s in ('quiet','ordinary')},
                   'meetings':{s:aggregate([x for x in rows if x['session']==s]) for s in meetings},'rows':rows}
    def rate(x): return Fraction(x['errors'],x['reference_characters']) if x['reference_characters'] else None
    b,q=arms['sensevoice'],arms['qwen']; bq,qq=rate(b['strata']['quiet']),rate(q['strata']['quiet'])
    improved=sum(rate(q['meetings'][m]) < rate(b['meetings'][m]) for m in meetings)
    checks={'all_examples_successful_untruncated':all(x['status']=='success' and not x['truncated'] for a in arms.values() for x in a['rows']),
        'quiet_nonzero_baseline':bq is not None and bq>0,
        'quiet_relative_reduction_at_least_15_percent':bq is not None and bq>0 and qq is not None and qq<=bq*Fraction(85,100),
        'quiet_absolute_reduction_at_least_2_points':bq is not None and qq is not None and bq-qq>=Fraction(2,100),
        'overall_no_regression':rate(q['overall'])<=rate(b['overall']),
        'strict_overall_improvement_at_least_6_of_8_meetings':improved>=6,
        'qwen_no_repetition_suspicions':q['overall']['repetition_suspicions']==0}
    return {'schema':'farfield-direct-asr-scores-v1','normalization':'qualified score_predictions.lexical: NFKC, lowercase, remove Unicode P/S, remove split whitespace for CER',
        'edit_ties':'diagonal then deletion then insertion','arms':arms,'gate':{'checks':checks,'passes':all(checks.values()),'improved_meetings':improved,
        'permits_only':'larger evaluation; no default promotion'},'scope':'oracle-boundary direct recognizer quality, prospectively frozen validation; not proven unseen; no diarization, English, or overlap claim'}

def main():
    global metrics
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--backend', type=Path, required=True)
    ap.add_argument('--references', type=Path, required=True)
    ap.add_argument('--predictions', type=Path, nargs='+', required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = external_output(args.output, args.backend)
    if out.exists(): raise ValueError('Output exists')
    metrics = load_metrics(args.backend)
    examples = read(args.references); pins = read(HERE/'selection.json')
    if len(examples) != len(pins): raise ValueError('Exact cohort required')
    for ex,pin in zip(examples,pins):
        if {k:ex[k] for k in pin} != pin or hashlib.sha256(ex['reference'].encode()).hexdigest() != pin['reference_utf8_sha256']:
            raise ValueError('Reference identity mismatch')
        if hashlib.sha256(chars(ex['reference']).encode()).hexdigest() != pin['normalized_reference_sha256']:
            raise ValueError('Normalization differs')
    records = []
    for p in args.predictions:
        with p.open('rb') as f:
            while True:
                line = f.readline(262145)
                if not line: break
                if len(line) > 262144 or len(records) >= 112 or not line.endswith(b'\n'): raise ValueError('Prediction bound exceeded/incomplete line')
                records.append(json.loads(line))
    result = score(examples,records,sorted({r['session'] for r in examples}))
    result['reproduction_scope'] = 'Portable scoring only; no assertion of original controller audit or resource equivalence'
    with out.open('x') as f: json.dump(result,f,ensure_ascii=False,indent=2); f.write('\n')
    print(json.dumps({arm:result['arms'][arm]['overall'] for arm in result['arms']}))

if __name__ == '__main__': main()
