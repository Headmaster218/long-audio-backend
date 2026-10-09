"""Conservative source-separation plumbing; source slots are never identities.

No model is imported here. The optional separator runs in a bounded subprocess.
All intervals are half-open, and original mixtures remain authoritative evidence.
"""
from __future__ import annotations

import itertools
import math
import numpy as np


def official_input_normalization(samples):
    """Pinned ClearerVoice two-stage -25 dB normalization, with silent guards."""
    x = np.asarray(samples, dtype=np.float32).copy()
    if x.ndim != 1 or not len(x) or not np.isfinite(x).all():
        raise ValueError('Require finite nonempty mono samples')
    if float(np.max(np.abs(x))) <= 1e-9:
        return x, 1.0
    first = 10 ** (-25 / 20) / (float(np.sqrt(np.mean(x * x))) + 1e-6)
    x *= first
    power = x * x
    strong = power[power > power.mean()]
    rms = float(np.sqrt(strong.mean() if len(strong) else power.mean()))
    second = 10 ** (-25 / 20) / (rms + 1e-6)
    return x * second, first * second


def asr_safe_source(samples, target_rms, peak_limit=.95):
    """Explicit ASR-only gain; raw outputs and original mixture stay unchanged.

    This matches upstream per-source RMS normalization except for a peak guard.
    Scaling is never evidence for activity/identity: it can amplify leakage.
    """
    x = np.asarray(samples, dtype=np.float32)
    if x.ndim != 1 or not np.isfinite(x).all() or not len(x):
        raise ValueError('Invalid separated samples')
    rms = float(np.sqrt(np.mean(x * x)))
    peak = float(np.max(np.abs(x)))
    gain = min(float(target_rms) / max(rms, 1e-12), peak_limit / max(peak, 1e-12))
    if not math.isfinite(gain) or gain < 0:
        raise ValueError('Invalid normalization target')
    return x * gain, {'gain': gain, 'raw_rms': rms, 'raw_peak': peak,
                      'target_rms': target_rms, 'peak_limit': peak_limit,
                      'gain_is_activity_evidence': False}


def overlapping_regions(turns, duration, capacity=2):
    """Retain simultaneous IDs, including explicit 3+ source capacity failures."""
    edges = {0.0, float(duration)}
    clipped = []
    for t in turns:
        a, b = max(0., float(t['start'])), min(duration, float(t['end']))
        if b > a:
            clipped.append((a, b, t['speaker_local_id']))
            edges.update((a, b))
    regions = []
    points = sorted(edges)
    for a, b in zip(points, points[1:]):
        ids = sorted({sid for lo, hi, sid in clipped if lo < b and hi > a})
        if len(ids) < 2:
            continue
        status = 'capacity_exceeded' if len(ids) > capacity else 'pending_separation'
        if regions and regions[-1]['end'] == a and regions[-1]['speaker_local_ids'] == ids:
            regions[-1]['end'] = b
        else:
            regions.append({'start': a, 'end': b, 'speaker_local_ids': ids,
                            'detected_source_count': len(ids), 'capacity': capacity,
                            'status': status, 'original_mixture_retained': True})
    return regions


def source_permutation(previous, current, min_correlation=.65, min_margin=.15,
                       min_rms=1e-5):
    """Match the SAME acoustic overlap across windows, abstain on weak evidence.

    previous/current are [source,samples] over exactly the same time interval.
    Returns previous-slot -> current-slot; this is acoustic continuity only.
    It must not be used to infer a person's global identity.
    """
    a, b = np.asarray(previous, dtype=np.float64), np.asarray(current, dtype=np.float64)
    if a.shape != b.shape or a.ndim != 2 or not 1 <= a.shape[0] <= 4 or a.shape[1] < 2:
        raise ValueError('Require equal source x shared-sample arrays')
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('Nonfinite continuity evidence')
    a, b = a-a.mean(axis=1, keepdims=True), b-b.mean(axis=1, keepdims=True)
    if np.any(np.sqrt(np.mean(a*a, axis=1)) < min_rms) or np.any(np.sqrt(np.mean(b*b, axis=1)) < min_rms):
        return {'status': 'uncertain', 'permutation': None, 'reason': 'insufficient_active_overlap'}
    corr = np.abs(a @ b.T / np.maximum(np.linalg.norm(a, axis=1)[:, None] * np.linalg.norm(b, axis=1)[None, :], 1e-12))
    choices = sorted(((float(np.mean([corr[i,j] for i,j in enumerate(p)])), p)
                      for p in itertools.permutations(range(len(a)))), reverse=True)
    best, perm = choices[0]
    margin = best - choices[1][0] if len(choices)>1 else 1.
    weakest = min(float(corr[i,j]) for i,j in enumerate(perm))
    accepted = weakest >= min_correlation and margin >= min_margin
    return {'status': 'matched' if accepted else 'uncertain',
            'permutation': list(perm) if accepted else None, 'mean_correlation': best,
            'weakest_correlation': weakest, 'margin': margin,
            'reason': None if accepted else 'ambiguous_source_continuity',
            'identity_claim': False}


def match_clean_identities(source_embeddings, clean_embeddings, min_cosine=.7,
                           min_margin=.15, evidence_seconds=None, min_seconds=2.):
    """One-to-one tentative matching against clean ERes evidence, never enrollment.

    Thresholds are conservative experiment settings, NOT calibrated confidence.
    Colliding best identities abstain rather than force the second-best person.
    """
    def norm(x):
        x=np.asarray(x,dtype=np.float64)
        if x.ndim!=1 or not len(x) or not np.isfinite(x).all() or np.linalg.norm(x)<1e-8:
            return None
        return x/np.linalg.norm(x)
    clean={sid:norm(e) for sid,e in clean_embeddings.items()}
    clean={sid:e for sid,e in clean.items() if e is not None and (evidence_seconds is None or evidence_seconds.get(sid,0)>=min_seconds)}
    results=[]
    for slot,emb in enumerate(source_embeddings):
        e=norm(emb) if emb is not None else None
        scores=sorted(((float(e@v),sid) for sid,v in clean.items() if e is not None and e.shape==v.shape), reverse=True)
        candidates=[{'speaker_local_id':sid,'cosine':score} for score,sid in scores]
        margin=scores[0][0]-scores[1][0] if len(scores)>1 else None
        # A lone candidate does not establish discrimination from another voice.
        good=len(scores)>=2 and scores[0][0]>=min_cosine and margin>=min_margin
        results.append({'source_slot':slot,'speaker_local_id':scores[0][1] if good else None,
                        'identity_status':'tentative_clean_match' if good else 'uncertain',
                        'identity_candidates':candidates, 'identity_margin':margin,
                        'profile_update_eligible':False, 'confidence':None})
    selected=[r['speaker_local_id'] for r in results if r['speaker_local_id'] is not None]
    for r in results:
        if r['speaker_local_id'] is not None and selected.count(r['speaker_local_id'])>1:
            r.update(speaker_local_id=None,identity_status='uncertain_collision')
    return results


def leakage_diagnostics(sources, transcripts=None):
    """Flag suspicious duplicate source evidence; never delete another voice."""
    x=np.asarray(sources,dtype=np.float64)
    if x.ndim!=2 or not np.isfinite(x).all(): raise ValueError('Invalid source array')
    flags=[]; maximum=0.
    for i,j in itertools.combinations(range(len(x)),2):
        a,b=x[i]-x[i].mean(),x[j]-x[j].mean()
        c=abs(float(a@b/max(np.linalg.norm(a)*np.linalg.norm(b),1e-12)))
        maximum=max(maximum,c)
        if c>.9: flags.append({'sources':[i,j],'reason':'high_waveform_correlation','correlation':c})
        if transcripts and transcripts[i].strip() and transcripts[i].strip()==transcripts[j].strip():
            flags.append({'sources':[i,j],'reason':'duplicate_transcript_requires_review'})
    return {'flags':flags,'maximum_pair_correlation':maximum,
            'quality_status':'suspected_leakage' if flags else 'unverified',
            'tracks_deleted':False,'profile_update_eligible':False}
