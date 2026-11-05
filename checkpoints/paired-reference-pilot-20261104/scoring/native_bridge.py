"""Model-free paired-study preparation and full-native-output gate. Never runs inference."""
from pathlib import Path
import argparse
import copy
import hashlib
import sys
import wave
import paired_scoring as p

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / 'backend-calibration-pilot-20261104'
sys.path.insert(0, str(OLD))
import native_runner as runner
import orchestrator as original

CANDIDATES = ROOT / 'fresh-ami-feasibility-20261104/prospective_annotation_candidates.json'
CANDIDATES_SHA = 'fc297e67fbee68481809c600068ffc159df0742c395f00a1b51f767a13a16e52'


def bridge_sources():
    return {name: p.pin(path) for name, path in {'bridge': __file__, 'native_runner': runner.__file__,
            'native_gate': original.__file__, 'gate_scoring': original.s.__file__,
            'native_helper': runner.HELPER_PATH}.items()}


def prepare(crop_path, crop_sha, output):
    """Crop manifest rows must preserve all frozen candidate fields verbatim."""
    import numpy as np
    cp = p.pin(crop_path)
    if cp['sha256'] != crop_sha or p.pin(CANDIDATES)['sha256'] != CANDIDATES_SHA:
        raise ValueError('crop/candidate externally retained SHA mismatch')
    crops = p.read_pinned(cp)
    candidates = p.read_json(CANDIDATES)
    base = Path(__file__).resolve().parents[1]
    upstream = {k: p.pin(base / path) for k, path in {
        'source_receipt_sha256': 'acquired-recovery-02/RECOVERY_RECEIPT.json',
        'metadata_audit_sha256': 'metadata-audit/metadata_audit.json',
        'materializer_sha256': 'materialize_crops.py'}.items()}
    if (crops['status'] != 'complete_fixed_coordinates' or crops['candidate_sha256'] != CANDIDATES_SHA
            or any(crops[k] != pin['sha256'] for k, pin in upstream.items())):
        raise ValueError('crop upstream seals mismatch')
    receipt = p.read_pinned(upstream['source_receipt_sha256'])
    if (receipt['status'] != 'complete_recovered_not_original_single_attempt'
            or not receipt['no_substitutions'] or receipt['combined_transfer_upper_bound'] > receipt['cap_bytes']
            or p.read_pinned(upstream['metadata_audit_sha256'])['status'] != 'PASS_WITH_EXPLICIT_LIMITATIONS'):
        raise ValueError('acquisition/metadata incomplete')
    sources = {Path(r['path']).name: r for r in receipt['files']}
    for source in sources.values():
        if p.pin(source['path']) != {k: source[k] for k in ('path','bytes','sha256')}:
            raise ValueError('source WAV seal mismatch')
    rows = crops['rows']
    if len(rows) != 36 or len(rows) != len(candidates['rows']):
        raise ValueError('all 36 original frozen slots required')
    out = Path(output).resolve(); out.mkdir(exist_ok=False)
    samples = {'C': [], 'T': []}; records = []
    roster = {(r['split'], r['person_id']): r for r in candidates['roster']}
    for row, frozen in zip(rows, candidates['rows']):
        if any(row.get(k) != v for k, v in frozen.items()):
            raise ValueError('crop rows differ from original frozen order/metadata')
        identity = f"{row['split']}-{row['person_id']}-{row['crop_role']}-{row['slot']}"
        if row['id'] != identity:
            raise ValueError('opaque ID differs from deterministic frozen slot')
        wav = p.pin(row['path'])
        if wav['sha256'] != row['sha256']:
            raise ValueError('crop WAV seal mismatch')
        with wave.open(wav['path'], 'rb') as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes(), audio.getcomptype()) != (1, 2, 16000, 32000, 'NONE'):
                raise ValueError('crop format mismatch')
            pcm = audio.readframes(32000)
        source = sources[row['meeting']+'.Mix-Headset.wav']
        with wave.open(source['path'], 'rb') as original_audio:
            original_audio.setpos(row['start_frame'])
            original_pcm = original_audio.readframes(row['end_frame']-row['start_frame'])
        if pcm != original_pcm or row['source_sha256'] != source['sha256']:
            raise ValueError('crop not exact frozen source coordinates')
        if hashlib.sha256(pcm).hexdigest() != row['pcm_sha256']:
            raise ValueError('crop PCM hash mismatch')
        floats = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / np.float32(32768)
        sample = dict(id=identity, wav=wav, pcm_sha256=row['pcm_sha256'],
                      float32_sha256=hashlib.sha256(floats.astype('<f4', copy=False).tobytes()).hexdigest())
        if sample['float32_sha256'] != row['float32_sha256']: raise ValueError('float32 hash mismatch')
        runner.waveform(sample)
        samples[row['split']].append(sample)
        person = roster[row['split'], row['person_id']]
        records.append(dict(id=identity, split=row['split'], role='reference' if row['crop_role']=='reference' else 'query',
            kind=row['person_role'], person_id=row['person_id'] if row['person_role']=='known' else None,
            speaker_id=row['person_id'], session_id=row['meeting'], recording_day_label=person['date'],
            meeting_phase=row['meeting'][-1], turn_id=row['segment_id']))
    plans = {}; predictors = {}; truths = {}
    for split in ('C', 'T'):
        local = [r for r in rows if r['split'] == split]
        gallery = [dict(person_id=r['person_id'], reference_ids=[x['id'] for x in local if x['person_id']==r['person_id'] and x['crop_role']=='reference'])
                   for r in candidates['roster'] if r['split']==split and r['role']=='known']
        queries = [r['id'] for r in local if r['crop_role'] != 'reference']
        primary = [r['id'] for r in local if r['crop_role'] != 'reference' and r['slot']==1]
        pred = dict(split=split, gallery=gallery, queries=queries, primary_queries=primary)
        truth = {r['id']: {k:r[k] for k in ('kind','person_id','speaker_id','session_id')} for r in records if r['split']==split and r['role']=='query'}
        p.validate_predictor(pred); p.validate_truth(pred, truth)
        predictors[split]=p.seal(out/(split+'.predictor.json'),pred)
        truths[split]=p.seal(out/(split+'.truth.json'),truth)
        # First reference and first query, selected before inference, two repeats each.
        plan=runner.make_plan_template(split,samples[split],[gallery[0]['reference_ids'][0], queries[0]])
        runner.verify_environment(plan)
        plans[split]=p.seal(out/(split+'.plan.json'),plan)
    evidence=dict(schema=1,records=records,crop_manifest=cp,candidates=p.pin(CANDIDATES),native_plans=plans,
                  bridge_sources=bridge_sources(),upstream=upstream,historical_exposure='unknown',scope='English same-day cross-meeting mechanism study; not final acceptance')
    ep=p.seal(out/'source-evidence.json',evidence)
    pp=p.freeze_protocol(predictors['C']['path'],truths['C']['path'],predictors['T']['path'],truths['T']['path'],ep['path'],out/'PROTOCOL.json')
    manifest=copy.deepcopy(p.read_pinned(plans['C'])); manifest['protocol_sha256']=pp['sha256']
    runner.validate_manifest(manifest)
    return dict(protocol=pp,C_manifest=p.seal(out/'C_MANIFEST.json',manifest),plans=plans)


def expected(protocol_path, protocol_sha, split, lock=None):
    pp, protocol, predictors=p.load_protocol(protocol_path,protocol_sha)
    evidence=p.read_pinned(protocol['source_evidence'])
    if evidence['bridge_sources'] != bridge_sources():
        raise ValueError('bridge/native gate source changed')
    plan=p.read_pinned(evidence['native_plans'][split]); runner.validate_plan_template(plan)
    ids=[s['id'] for s in plan['samples']]
    _,refs=p.validate_predictor(predictors[split])
    if len(ids)!=len(set(ids)) or set(ids)!=set(refs+predictors[split]['queries']):
        raise ValueError('native plan differs from entire paired population')
    plan=copy.deepcopy(plan); plan['protocol_sha256']=pp['sha256']
    if split=='T':
        if lock is None: raise ValueError('paired C lock required before T release')
        p.read_lock(lock,pp,protocol,predictors); plan['release_pin']=lock
    runner.validate_manifest(plan)
    return pp,plan


def release_t(protocol_path,protocol_sha,lock,output):
    _,plan=expected(protocol_path,protocol_sha,'T',lock)
    return p.seal(output,plan)


def gate(protocol_path,protocol_sha,split,manifest_pin,terminal_pin,output,lock=None):
    pp,plan=expected(protocol_path,protocol_sha,split,lock)
    runner.verify_environment(plan)
    _,seals,terminal=original._sealed_run(pp,manifest_pin,terminal_pin,plan)
    return p.seal(output,dict(status='complete_native_run_gate_passed',protocol=pp,manifest=manifest_pin,
        terminal=terminal_pin,artifacts=seals,bridge_sources=bridge_sources(),partition=split,
        positive_original_wait=True,planned_calls=terminal['planned_calls']))


def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare')
    for key in ('crop-manifest','crop-sha','output'): prep.add_argument('--'+key,required=True)
    for cmd in ('release-t','gate'):
        parser=sub.add_parser(cmd)
        for key in ('protocol','protocol-sha','output'): parser.add_argument('--'+key,required=True)
        parser.add_argument('--lock');parser.add_argument('--lock-sha')
        if cmd=='gate':
            parser.add_argument('--split',choices=('C','T'),required=True)
            for key in ('manifest','manifest-sha','terminal','terminal-sha'): parser.add_argument('--'+key,required=True)
    a=ap.parse_args()
    def retained(path,sha):
        value=p.pin(path)
        if value['sha256'] != sha:raise ValueError('external input SHA mismatch')
        return value
    if a.command=='prepare': result=prepare(a.crop_manifest,a.crop_sha,a.output)
    else:
        lock=retained(a.lock,a.lock_sha) if a.lock else None
        if a.command=='release-t': result=release_t(a.protocol,a.protocol_sha,lock,a.output)
        else:result=gate(a.protocol,a.protocol_sha,a.split,retained(a.manifest,a.manifest_sha),retained(a.terminal,a.terminal_sha),a.output,lock)
    print(p.canonical(result).decode())

if __name__=='__main__':main()
