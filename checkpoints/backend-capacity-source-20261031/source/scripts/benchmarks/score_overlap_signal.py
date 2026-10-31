#!/usr/bin/env python3
"""Supplementary permutation-invariant SI-SDRi using exact source references."""
import argparse
import itertools
import json
from pathlib import Path
import numpy as np
import soundfile as sf


def si_sdr(reference, estimate, mask=None):
    r,e=np.asarray(reference,dtype=np.float64),np.asarray(estimate,dtype=np.float64)
    if r.shape!=e.shape or r.ndim!=1: raise ValueError('Reference/output shape mismatch')
    if mask is not None:r,e=r[mask],e[mask]
    if len(r)<2 or not np.isfinite(r).all() or not np.isfinite(e).all():raise ValueError('Invalid score samples')
    r,e=r-r.mean(),e-e.mean()
    energy=float(r@r)
    if energy<1e-12:return None
    target=(float(e@r)/energy)*r
    noise=e-target
    return 10*np.log10(max(float(target@target),1e-12)/max(float(noise@noise),1e-12))


def score(mixture, references, estimates, mask=None):
    if len(references)!=len(estimates) or not 1<=len(references)<=4:raise ValueError('Equal bounded source counts required')
    matrix=[[si_sdr(r,e,mask) for e in estimates] for r in references]
    if any(v is None for row in matrix for v in row):raise ValueError('Silent reference cannot be SI-SDR scored')
    best,perm=max((sum(matrix[i][j] for i,j in enumerate(p))/len(p),p) for p in itertools.permutations(range(len(references))))
    rows=[]
    for i,j in enumerate(perm):
        base=si_sdr(references[i],mixture,mask)
        rows.append({'reference_index':i,'estimate_index':j,'mixture_si_sdr_db':base,
                     'output_si_sdr_db':matrix[i][j],'improvement_db':matrix[i][j]-base})
    return {'metric':'oracle_permutation_SI_SDRi','mean_improvement_db':float(np.mean([r['improvement_db'] for r in rows])),
            'per_source':rows,'identity_claim':False,
            'caveat':'Synthetic mixture of real microphone crops; signal metric cannot establish real overlap lexical recovery.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('mixture');p.add_argument('--references',nargs=2,required=True);p.add_argument('--estimates',nargs=2,required=True);p.add_argument('--out');a=p.parse_args()
    def read(path):
        x,sr=sf.read(path,dtype='float32');assert sr==16000 and x.ndim==1;return x
    mixture=read(a.mixture);refs=[read(x) for x in a.references];ests=[read(x) for x in a.estimates]
    result=score(mixture,refs,ests)
    # Evaluate only intervals with nontrivial energy in both reference tracks.
    frame=320;active=[]
    for r in refs:
        rms=np.array([np.sqrt(np.mean(r[i:i+frame]**2)) for i in range(0,len(r),frame)])
        threshold=max(float(rms.max())*.02,1e-6)
        active.append(np.repeat(rms>threshold,frame)[:len(r)])
    mask=np.logical_and.reduce(active)
    if mask.sum()>=frame:result['both_sources_active']=score(mixture,refs,ests,mask)
    text=json.dumps(result,indent=2);print(text)
    if a.out:Path(a.out).write_text(text)
if __name__=='__main__':main()
