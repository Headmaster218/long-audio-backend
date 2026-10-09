"""Small, explicit, model-bound speaker policy profiles (no biometric vectors)."""
import hashlib
import json
import math
from pathlib import Path

BUILTIN='eres2net-ami20-v1'

def load_profile(value):
    path=Path(__file__).parent/'profiles'/f'{BUILTIN}.json' if str(value)==BUILTIN else Path(value).expanduser().resolve()
    if path.stat().st_size>65536:raise ValueError('Speaker calibration profile exceeds64KiB')
    raw=path.read_bytes();profile=json.loads(raw)
    if profile.get('schema_version')!=1:raise ValueError('Unsupported speaker calibration schema')
    model=profile.get('model',{});policy=profile.get('policy',{})
    version=model.get('embedding_version');sha=model.get('sha256');dim=model.get('dimension')
    if not isinstance(version,str) or not version or len(version)>200:raise ValueError('Invalid calibrated embedding version')
    if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('Invalid calibrated model fingerprint')
    if not isinstance(dim,int) or not 8<=dim<=2048:raise ValueError('Invalid calibrated embedding dimension')
    for name in ('single_threshold','verified_centroid_threshold','novelty_threshold','ambiguity_margin'):
        v=policy.get(name)
        if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or not 0<=v<=1:raise ValueError('Invalid calibrated '+name)
    if policy['novelty_threshold']>min(policy['single_threshold'],policy['verified_centroid_threshold']):raise ValueError('Novelty threshold exceeds matching threshold')
    for name in ('minimum_new_enrollment_seconds','minimum_matching_query_seconds'):
        v=policy.get(name)
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not .5<=v<=20:raise ValueError('Invalid calibrated '+name)
    if policy['minimum_matching_query_seconds']>policy['minimum_new_enrollment_seconds']:raise ValueError('Query duration exceeds enrollment duration')
    if policy.get('automatic_template_updates') is not False:raise ValueError('Automatic template adaptation is not supported')
    if policy.get('profile_strategy')!='verified_centroid':raise ValueError('Unsupported calibrated profile scoring strategy')
    if not isinstance(profile.get('profile_id'),str) or not 1<=len(profile['profile_id'])<=200:raise ValueError('Invalid speaker policy ID')
    return {**profile,'artifact_sha256':hashlib.sha256(raw).hexdigest()}
