#!/usr/bin/env python3
"""Portable model-free whole-excerpt CER from locally supplied references/outputs.

No recognizer imports, downloads, generation, token-to-word alignment or history.
"""
import argparse,json,unicodedata
from pathlib import Path
MAX_INPUT_BYTES=2*1024**2
MAX_TEXT_BYTES=65536
MAX_EDIT_CELLS=20000000

def characters(text):
    if not isinstance(text,str) or len(text.encode('utf8'))>MAX_TEXT_BYTES:
        raise ValueError('Text must be valid bounded UTF-8')
    return ''.join(c for c in unicodedata.normalize('NFKC',text).lower()
                   if not c.isspace() and not unicodedata.category(c).startswith(('P','S')))

def edit_counts(reference,hypothesis):
    if len(reference)*len(hypothesis)>MAX_EDIT_CELLS:
        raise ValueError('Edit workload exceeds bound; input is not truncated')
    previous=[(j,0,0,j) for j in range(len(hypothesis)+1)]
    for i,a in enumerate(reference,1):
        row=[(i,0,i,0)]
        for j,b in enumerate(hypothesis,1):
            x=previous[j-1];cost=int(a!=b);diagonal=(x[0]+cost,x[1]+cost,x[2],x[3])
            x=previous[j];deletion=(x[0]+1,x[1],x[2]+1,x[3])
            x=row[-1];insertion=(x[0]+1,x[1],x[2],x[3]+1)
            row.append(min((diagonal,deletion,insertion),key=lambda x:x[0]))
        previous=row
    errors,substitutions,deletions,insertions=previous[-1]
    return dict(errors=errors,substitutions=substitutions,deletions=deletions,
                insertions=insertions,reference_characters=len(reference))

def score(bundle):
    if bundle.get('schema')!='whole-excerpt-text-input-v1':raise ValueError('Input schema')
    excerpts=bundle.get('excerpts');records=bundle.get('records')
    if not isinstance(excerpts,list) or not 1<=len(excerpts)<=64 or not isinstance(records,list):raise ValueError('Bounded excerpts/records required')
    allowed=set();excerpt_ids=set()
    for e in excerpts:
        if e['id'] in excerpt_ids or not isinstance(e['window_ids'],list) or not e['window_ids']:raise ValueError('Excerpt identity/coverage')
        excerpt_ids.add(e['id'])
        for identity in e['window_ids']:
            if not isinstance(identity,str) or identity in allowed:raise ValueError('Duplicate/non-string window ID')
            allowed.add(identity)
        reference=characters(e['reference'])
        if not reference or type(e['reference_characters']) is not int or len(reference)!=e['reference_characters']:raise ValueError('Complete-reference denominator mismatch')
    if len(allowed)>64 or len(records)>len(allowed):raise ValueError('Window count bound')
    indexed={}
    for r in records:
        identity=r['window_id']
        if identity not in allowed or identity in indexed:raise ValueError('Foreign/duplicate output')
        if r['status'] not in ('success','truncated','failed','not_attempted','aborted'):raise ValueError('Output status')
        indexed[identity]=r
    meetings=[];ledger=[]
    for e in excerpts:
        parts=[];failed=0;truncated=0
        for identity in e['window_ids']:
            r=indexed.get(identity,dict(status='not_attempted'))
            usable=r['status'] in ('success','truncated')
            text=''
            if usable:
                segments=r.get('transcription')
                if not isinstance(segments,list) or len(segments)>4096:raise ValueError('Bounded native transcription array required')
                text=''.join(segment['text'] for segment in segments)
                characters(text)
            parts.append(text);failed+=not usable;truncated+=r['status']=='truncated'
            ledger.append(dict(excerpt=e['id'],window_id=identity,status=r['status'],scored_characters=len(characters(text))))
        reference=characters(e['reference']);hypothesis=characters(''.join(parts));counts=edit_counts(reference,hypothesis)
        counts.update(excerpt=e['id'],cer=counts['errors']/len(reference),hypothesis_characters=len(hypothesis),failed_or_missing_windows=failed,explicit_truncated_windows=truncated)
        meetings.append(counts)
    keys=('errors','substitutions','deletions','insertions','reference_characters','failed_or_missing_windows','explicit_truncated_windows')
    total={k:sum(e[k] for e in meetings) for k in keys};total['cer']=total['errors']/total['reference_characters']
    return dict(schema='whole-excerpt-text-counts-v1',overall=total,excerpts=meetings,ledger=ledger,
                complete=all(r['status']=='success' for r in ledger),
                normalization='NFKC/lowercase; remove Unicode punctuation, symbols and whitespace; no script conversion',
                edit_ties='diagonal, deletion, insertion',
                scope='Lexical counts only; does not verify runtime/input provenance, decoder completeness, timestamp accuracy or speaker identity')

def load(path):
    if path.stat().st_size>MAX_INPUT_BYTES:raise ValueError('Input byte cap')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('Duplicate JSON key')
            result[k]=v
        return result
    return json.loads(path.read_bytes().decode('utf8','surrogateescape'),object_pairs_hook=pairs,
                      parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if args.output.exists():raise ValueError('Refusing existing output')
    result=score(load(args.input))
    with args.output.open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
if __name__=='__main__':main()
