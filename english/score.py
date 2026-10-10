"""Offline lexical counts only; no execution-gate or recognizer imports."""
import unicodedata

def normalize(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 65536:
        raise ValueError('Expected a string of at most 65536 UTF-8 bytes')
    return ''.join(c for c in unicodedata.normalize('NFKC', text).lower()
                   if not unicodedata.category(c).startswith(('P', 'S')))

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
    return {'errors':n,'substitutions':s,'deletions':d,'insertions':i,'reference_units':len(reference)}

def score(reference, hypothesis):
    """Return WER/CER counts; callers retain control of local text and status.

    A failed/not-attempted call must be scored with an empty hypothesis while
    retaining its failure status separately. This helper cannot certify a run.
    """
    r, h = normalize(reference), normalize(hypothesis)
    rw, hw = r.split(), h.split()
    rc, hc = ''.join(rw), ''.join(hw)
    if max(len(rc), len(hc)) > 4096:
        raise ValueError('Offline helper limits each normalized side to 4096 characters')
    return {'words': edits(rw, hw), 'characters': edits(rc, hc)}
