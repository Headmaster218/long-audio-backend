#!/usr/bin/env python3
"""Private event-only seam experiment. Default proposal does not load a model."""
import argparse
import copy
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from audio_backend.fragment_core import BoundedPCM16Reader, build_pcm16_plan, inspect_pcm16_source
from audio_backend.fragment_matcher import Continuity
from audio_backend.fragment_runtime import LogicalPCMReader, LogicalRunner
from audio_backend.fragment_runtime_contracts import RATE, RuntimePolicy, NativeStatus, digest
from audio_backend.fragment_runtime_store import RuntimeStore
from audio_backend.fragment_evidence import EvidenceStore
from audio_backend.fragment_model_bridges import AnalyzerBridge, CallBudget
from audio_backend.fragment_model_protocol import FrozenFiles, imported_receipt, sha256
from audio_backend.fragment_model_guard import Identity, ResourceGuard
from audio_backend.fragment_model_compare import Watchdog, atomic_json, require_private_output
from audio_backend.events.detector import detect_events, window_starts

BASE = ROOT.parent/'audio-backend'
DEVELOPMENT = BASE/'reports/fresh-transport-benchmark-research/development-baseline-v1'
MODEL = BASE/'models/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15'
IDS = ('180174', '421591')
ARMS = ('AB-whole', 'AB-split', 'BA-whole', 'BA-split')
CONFIG = {'event_threshold': .25, 'event_release_ratio': 1., 'event_silence_peak': 1e-5}
LIMITS = {'analyzer': {'calls': 8, 'frames': 176*RATE}, 'event': {'calls': 64, 'frames': 320*RATE}}
CUTS = (0, 114000, 468000, 562000, 640000)  # 0, 7.125, 29.25, 35.125, 40 s
DUPLICATE = 20000  # Known 1.25 s duplicated prefix on second physical fragment only.
POLICY = 'exact-pcm16-v2q1p1'


def proposal():
    return {'schema': 'positive-event-seams-v1', 'status': 'requires independent review and root execution admission',
        'ids': list(IDS), 'arms': list(ARMS), 'source_selection': 'Previously exposed cached model-positive development data with at least 20 s available: greedily maximize distinct supported non-silence emitted labels, numeric ID tie-break; next source maximizes additional labels, numeric ID tie-break.',
        'arrangements': {'AB': [['180174', 0, 320000], ['421591', 0, 320000]], 'BA': [['421591', 0, 320000], ['180174', 0, 320000]]},
        'synthetic_join_frame': 320000, 'natural_continuity_claim': False, 'physical_cuts': list(CUTS),
        'second_fragment_duplicate_prefix_frames': DUPLICATE, 'matcher_policy': POLICY,
        'transport_provenance': 'Generated whole and split WAVs derive from identical synthetic canonical samples. Only second fragment has an added copy of immediately preceding 20000 samples. No intentional replay inside either canonical AB or BA arrangement.',
        'core_frames': 480000, 'context_frames': 32000, 'read_ranges': [[0,512000],[448000,640000]],
        'window_counts_per_arm': [12,4], 'maximum_calls': LIMITS, 'detector_config': CONFIG,
        'native': 'Synthetic immediate-frontier empty-turn stand-in, zero native inference',
        'analyzer': 'Event-only detector adapter through unchanged AnalyzerBridge and LogicalRunner. Empty lexical segments. No ASR, embeddings, MOSS or diarization compute.',
        'comparison': 'Exact paired read PCM, actual padded model inputs, raw events and details, confidence/source labels, projected/clipped event records, window IDs, evidence links. Counts are evidence segments, not physical occurrences. No cross-core event merge is added.',
        'positive_gate': 'Both fixtures must have nonempty published non-silence events, at least one event straddling a physical cut, and a common label with owned support ending and starting exactly at the 30 s logical boundary. Otherwise incomplete coverage, never vacuous success.',
        'limitations': ['selection on previously exposed predictions, not blind accuracy', 'weak whole-clip labels are not onset gold', 'rain not represented by selected cached positives', 'silence is a synthetic separate model-free shortcut test, not natural-silence accuracy', 'artificial 20 s join is not natural continuity', 'transport test is not a full pipeline or diarization test', 'confidence retained in raw evidence, absent by contract from logical published projection'],
        'resource_limits': {'implementation_rss_bytes': 256*1024**2, 'proposed_inference_rss_bytes': 512*1024**2,
            'aggregate_rss_bytes': 6*1024**3, 'artifact_bytes': 64*1024**2, 'host_available_bytes': 2*1024**3,
            'disk_free_bytes': int(5.5*1024**3), 'cpu_affinity': [7], 'threads': 1, 'outer_seconds': 300, 'operation_seconds': 45},
        'maximum_model_loads': 1, 'receipt_binding': 'Exact stage and absolute output directory; exclusive fsynced attempt marker before any model construction',
        'interrupted_accounting': 'No durable terminal record means conservatively charge the full approved call/frame ceiling plus one model load',
        'automatic_retries': False, 'downloads': False, 'confirmation_access': False}


def write_wav(path, pcm):
    with wave.open(str(path), 'wb') as w:
        w.setparams((1,2,RATE,0,'NONE','not compressed'))
        w.writeframes(pcm)


def source_rows():
    rows = json.loads((DEVELOPMENT/'PREPROCESSED.json').read_text())['rows']
    # Inspect all already-exposed development cached scores only, never confirmation.
    assert all(r['project_split']=='development' for r in rows)
    selected, covered = [], set()
    pool = []
    for r in rows:
        score = json.loads((DEVELOPMENT/'scores'/f"{r['id']}.json").read_text())
        if r['converted']['samples']>=320000: pool.append((r, set(score['labels'])-{'silence'}))
    for _ in range(2):
        best = min(pool, key=lambda v: (-len(v[1]-covered), int(v[0]['id'])))
        selected.append(best[0]['id']); covered |= best[1]; pool.remove(best)
    if tuple(selected)!=IDS or max(r['converted']['seconds'] for r in rows)>=30:
        raise ValueError('Frozen selection criterion/natural-duration premise changed')
    return [next(r for r in rows if r['id']==id) for id in IDS]


def pcm_sources(rows):
    output = {}
    for r in rows:
        path = Path(r['converted_path'])
        if path.resolve()!=(DEVELOPMENT/'pcm'/f"{r['id']}.wav").resolve() or sha256(path)!=r['converted']['file_sha256']:
            raise ValueError('Selected development PCM changed')
        with wave.open(str(path),'rb') as w:
            if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,RATE):
                raise ValueError('Unexpected canonical PCM')
            output[r['id']] = w.readframes(320000)
        if len(output[r['id']])!=640000: raise ValueError('Short source')
    return output


def prepare(directory, arrangement, mode, source_pcm):
    ids = IDS if arrangement=='AB' else IDS[::-1]
    pcm = b''.join(source_pcm[id] for id in ids)
    chunks = [(0,640000)] if mode=='whole' else [
        (CUTS[i]-(DUPLICATE if i==1 else 0),CUTS[i+1]) for i in range(4)]
    paths = {}
    for i,(a,b) in enumerate(chunks):
        path = directory/f'part-{i}.wav';write_wav(path,pcm[a*2:b*2]);paths[str(i)]=path
    sources = tuple(inspect_pcm16_source(key,path) for key,path in paths.items())
    reader = BoundedPCM16Reader(sources,paths)
    declarations = [Continuity('continuous',same_capture=True,replay_possible=False)]*(len(sources)-1)
    plan = build_pcm16_plan(sources,declarations,reader,final=True,policy=POLICY)
    if plan.end_frame!=640000 or LogicalPCMReader(plan,paths).read(0,640000).pcm!=pcm:
        raise ValueError('Matcher plan does not preserve exact canonical waveform')
    expected = [DUPLICATE,0,0] if mode=='split' else []
    if [s.evidence.trim_frames for s in plan.seams]!=expected:
        raise ValueError('Unexpected physical duplicate admission')
    return plan,paths


class SyntheticFrontier:
    """Scheduling stand-in, never a diarization provider or real native result."""
    def __init__(self, fingerprint):
        self.fingerprint=fingerprint
        self.provenance={'provider':'synthetic-empty-frontier-no-neural-compute'}
        self.finished=False
    def reset(self,key,start): self.end=start;self.key=key
    def push(self,pcm):
        if self.finished or pcm.start!=self.end: raise ValueError('Noncontiguous stand-in push')
        self.end=pcm.end
    def status(self): return NativeStatus(self.end,self.end,self.end,self.finished,())
    def finish(self,control): self.finished=True;return self.status()
    def evidence(self): return {'synthetic_stand_in':True,'status':asdict(self.status())}


class MeteredTagger:
    def __init__(self, tagger, budget, guard, verify):
        self.tagger,self.budget,self.guard,self.verify=tagger,budget,guard,verify
        self.context=None; self.inputs=[]
    def create_stream(self):
        outer=self
        class Stream:
            def accept_waveform(self, *, sample_rate, waveform):
                import numpy as np
                if sample_rate!=RATE or len(waveform)!=80000: raise ValueError('Unapproved model support')
                self.samples=np.asarray(waveform,dtype='<f4').copy()
        return Stream()
    def compute(self,stream):
        self.guard.check();self.verify()
        receipt={'context':self.context,'samples':len(stream.samples),'float32_sha256':hashlib.sha256(stream.samples.tobytes()).hexdigest()}
        ticket=self.budget.reserve('event',len(stream.samples),context=receipt)
        self.inputs.append(receipt)
        native=self.tagger.create_stream();native.accept_waveform(sample_rate=RATE,waveform=stream.samples)
        result=self.tagger.compute(native)
        self.budget.complete(ticket);self.guard.check();self.verify()
        return result


class EventOnlyAnalyzer:
    def __init__(self,tagger): self.tagger=tagger;self.primary_error=None
    def analyze(self,path, *, diarization,analysis_options):
        self.primary_error=None
        try:return self._analyze(path,diarization=diarization,analysis_options=analysis_options)
        except BaseException as error:
            self.primary_error=error
            raise
    def _analyze(self,path, *, diarization,analysis_options):
        import numpy as np
        with wave.open(path,'rb') as w:
            pcm=w.readframes(w.getnframes())
        values=np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768
        begin=len(self.tagger.inputs)
        self.tagger.context={'read_float32_sha256':hashlib.sha256(values.tobytes()).hexdigest()}
        events,details=detect_events(values,None,None,threads=1,config=CONFIG,tagger=self.tagger)
        receipts=self.tagger.inputs[begin:]
        executed=[(index,row) for index,row in enumerate(details) if row['model_inference_executed']]
        if len(receipts)!=len(executed):raise ValueError('Actual calls differ from detector execution schedule')
        for receipt,(index,row) in zip(receipts,executed):
            receipt['detector_window']={'index':index,'read_relative_start_frame':round(row['start']*RATE),
                'read_relative_end_frame':round(row['end']*RATE),'right_padding_frames':80000-round((row['end']-row['start'])*RATE)}
        return {'segments':[],'events':events,'details':details,'actual_inputs':receipts,
                'event_only':True,'native_stand_in':True,'read_float32_sha256':hashlib.sha256(values.tobytes()).hexdigest()}
    def close(self): pass


def normalized(rows,evidence):
    result=[]
    for row in rows:
        ev=evidence.read(row['evidence_ref'])
        raw=ev['raw_analyzer_result']
        result.append({'window_id':row['window_id'],'window':row['window'],'pcm_hash':row['pcm_hash'],
            'events':row['events'],'raw':raw,'projection_index':ev['projection_index'],
            'model_calls':[{k:v for k,v in item.items() if k!='context'} for item in raw['actual_inputs']]})
        # Every retained confidence/provenance belongs to the exact unmodified raw event.
        for index in ev['projection_index']['events']:
            original=raw['events'][index['raw_event']]
            assert index['score']==original['score'] and index['source_label']==original['source_label']
        expected=[]
        for ei,event in enumerate(raw['events']):
            native_a=row['window']['read_start']+round(event['start']*RATE)
            native_b=row['window']['read_start']+round(event['end']*RATE)
            a=max(row['window']['core_start'],native_a);b=min(row['window']['core_end'],native_b)
            if a<b:expected.append((a,b,event['label'],native_a,native_b,digest([row['window_id'],'event',ei,event])))
        actual=[(e['start_frame'],e['end_frame'],e['event'],e['native_start_frame'],e['native_end_frame'],e['instance_id']) for e in row['events']]
        if actual!=expected:raise ValueError('Raw event retention/count/core clipping differs from independent projection oracle')
        for item in row['events']:
            assert item['scope']==row['window_id']
            assert row['window']['core_start']<=item['start_frame']<item['end_frame']<=row['window']['core_end']
    return result


def positive_coverage(rows):
    events=[e for row in rows for e in row['events'] if e['event']!='silence']
    left={e['event'] for e in events if e['end_frame']==480000}
    right={e['event'] for e in events if e['start_frame']==480000}
    crossed=[cut for cut in CUTS[1:-1] if any(e['start_frame']<cut<e['end_frame'] for e in events)]
    return {'nonempty_events':len(events),'physical_cuts_crossed':crossed,'logical_boundary_labels':sorted(left&right),
            'positive_coverage_pass':bool(events and crossed and left&right),
            'per_core_event_counts':[len(r['events']) for r in rows],
            'representation':'Core-clipped model evidence segments. No cross-core merge, no occurrence count.'}


def execute(output,source_pcm,tagger,budget,guard,verify,watchdog,fingerprint):
    policy=RuntimePolicy(native_fingerprint='synthetic-empty-frontier-v1',analyzer_fingerprint=fingerprint)
    outputs={};coverage={};comparison={}
    for arm in ARMS:
        arrangement,mode=arm.split('-');armdir=output/arm;armdir.mkdir()
        with tempfile.TemporaryDirectory(dir=armdir) as tmp, EvidenceStore(armdir/'evidence',reserve=guard.check) as evidence:
            plan,paths=prepare(Path(tmp),arrangement,mode,source_pcm)
            store=RuntimeStore(armdir/'runtime.sqlite',evidence_store=evidence)
            # Same fixture-scoped run ID across transport arms makes window identity comparable.
            run_id=arrangement
            store.create(run_id,plan,policy)
            metered=MeteredTagger(tagger,budget,guard,verify)
            adapter=EventOnlyAnalyzer(metered)
            bridge=AnalyzerBridge(adapter,fingerprint=fingerprint,evidence_store=evidence,
                temp_root=armdir/'tmp',verify=verify,budget=budget,guard=guard,
                provenance={'experiment':'event-only','synthetic_native':True},source_bindings={'arrangement':arrangement,'mode':mode})
            runner=LogicalRunner(store,run_id,paths,bridge,lambda:SyntheticFrontier(policy.native_fingerprint))
            try:
                for _ in range(2):
                    with watchdog.operation(45): status=runner.step()
            except BaseException as reporting:
                # The unchanged bridge can fail while writing evidence in its except
                # block. Keep the event adapter's original provider error primary.
                primary=adapter.primary_error
                if primary is not None and primary is not reporting:
                    primary.add_note('Bridge failure-evidence reporting also failed: '+str(reporting)[:1000])
                    primary._event_seam_secondary_errors=[{'role':'bridge_failure_evidence_persistence',
                        'type':type(reporting).__name__,'message':str(reporting)[:2000]}]
                    raise primary from reporting
                raise
            if status!='completed':raise ValueError('Did not complete exactly two logical cores')
            rows=store.commits(run_id)
            comparison[arm]=normalized(rows,evidence);coverage[arm]=positive_coverage(rows)
            atomic_json(armdir/'plan.json',json.loads(plan.to_json()),guard)
            atomic_json(armdir/'commits.json',rows,guard)
            outputs[arm]={'cores':len(rows),'coverage':coverage[arm]}
    for arrangement in ('AB','BA'):
        if comparison[arrangement+'-whole']!=comparison[arrangement+'-split']:
            raise ValueError('Whole/split exact events, confidence, model inputs or window provenance differ')
    return {'transport_exact_pass':True,'positive_coverage_pass':all(v['positive_coverage_pass'] for v in coverage.values()),
            'arms':outputs,'budget':budget.used,'claims':'Event-only selected-development synthetic transport seam evidence; no accuracy, diarization or full-pipeline claim.'}


def freeze(output,guard):
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,text=True).strip():
        raise ValueError('Commit source changes before freeze')
    rows=source_rows(); selected=pcm_sources(rows)
    paths=set(ROOT/p for p in subprocess.check_output(['git','ls-files'],cwd=ROOT,text=True).splitlines())
    paths|={Path(sys.executable).resolve(),DEVELOPMENT/'PREPROCESSED.json',DEVELOPMENT/'INPUTS.json',DEVELOPMENT/'PROTOCOL.json',MODEL/'model.onnx',MODEL/'class_labels_indices.csv'}
    paths.add(DEVELOPMENT.parent/'candidate-inventory.json')
    paths.update((DEVELOPMENT/'scores').glob('*.json'))
    for r in rows: paths|={Path(r['converted_path']),Path(r['original_path']),DEVELOPMENT/'scores'/f"{r['id']}.json"}
    # Freeze installed distribution payload without importing Sherpa or loading a model.
    for name in ('numpy','sherpa-onnx','sherpa-onnx-core','psutil'):
        distribution=importlib.metadata.distribution(name)
        for entry in distribution.files or ():
            path=Path(distribution.locate_file(entry)).resolve()
            if path.is_file() and path.suffix!='.pyc': paths.add(path)
    files={}
    for path in sorted(paths):
        path=path.resolve();guard.check()
        files[str(path)]={'sha256':sha256(path),'bytes':path.stat().st_size}
    # Verify inherited unchanged model/detector contract against existing baseline.
    baseline=json.loads((DEVELOPMENT/'PROTOCOL.json').read_text())
    for rel,entry in baseline['artifact_hashes'].items():
        target=ROOT/rel if rel.startswith('audio_backend/') else BASE/rel
        if files[str(target.resolve())]!=entry:raise ValueError('Original detector/model changed')
    inventory=json.loads((DEVELOPMENT.parent/'candidate-inventory.json').read_text())
    licensing=[{k:r[k] for k in ('id','license','source_page','uploader') if k in r} for r in inventory if r['id'] in IDS]
    prepared={}
    for arm in ARMS:
        with tempfile.TemporaryDirectory(dir=output) as tmp:
            a,m=arm.split('-');plan,paths=prepare(Path(tmp),a,m,selected)
            prepared[arm]={'plan':json.loads(plan.to_json()),'canonical_sha256':LogicalPCMReader(plan,paths).read(0,640000).pcm_sha256}
    result={'proposal':proposal(),'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'bridge_base':'957255700a8493f82750f821b65c8a2797326396','files':files,'selected_rows':rows,'licensing':licensing,'prepared':prepared}
    atomic_json(output/'freeze.json',result,guard)
    return {'freeze_sha256':digest(result),'models_executed':0,'files':len(files)}


def validate_receipt(receipt,frozen,output):
    if receipt.get('output_directory')!=str(Path(output).resolve()) or receipt.get('maximum_model_loads')!=1:
        raise ValueError('Receipt must bind exact absolute output directory and one model load')
    if receipt.get('freeze_sha256')!=digest(frozen) or receipt.get('arms')!=list(ARMS) or receipt.get('maximum_calls')!=LIMITS:
        raise ValueError('Receipt does not bind exact freeze, arms and call limits')
    if not receipt.get('authorization_reference') or receipt.get('stage')!='event-only-positive-seams':
        raise ValueError('Explicit root authorization reference required, not provided by this program')
    if receipt.get('cpu_affinity')!=[7] or receipt.get('own_rss_cap_bytes')!=512*1024**2:
        raise ValueError('Unapproved CPU/RSS budget')
    if not receipt.get('project_roots') and not receipt.get('external_feeds'):
        raise ValueError('Current reviewed concurrent-project roster required')
    if receipt.get('external_feeds') and receipt.get('asynchronous_resource_contract_approved') is not True:
        raise ValueError('External feed estimate contract requires explicit approval')


class AttemptJournal:
    """A durable nonterminal attempt always implies full-ceiling interruption charge."""
    def __init__(self,output,frozen,receipt,guard):
        self.output,self.guard=Path(output),guard
        self.binding={'freeze_sha256':digest(frozen),'authorization_reference':receipt['authorization_reference'],
            'approval_sha256':digest(receipt),'stage':receipt['stage'],'output_directory':str(self.output.resolve()),
            'interruption_charge':{'budget':copy.deepcopy(LIMITS),'model_loads':1},
            'interruption_rule':'No verified durable terminal attempt-state means unknown interruption; conservatively charge full ceiling.'}
        self.model_load_status='not_attempted'
        self.budget=None

    def start(self):
        marker=self.output/'EVENT_INFERENCE_ATTEMPTED.json'
        payload=(json.dumps(self.binding,sort_keys=True,separators=(',',':'))+'\n').encode()
        self.guard.check(len(payload))
        # Exclusive marker is never overwritten, even if file/directory fsync fails.
        # A failure here occurs before any model and leaves retry fenced.
        fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(payload);stream.flush();os.fsync(stream.fileno())
        directory=os.open(self.output,os.O_DIRECTORY|os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)

    def write(self,status='active',error=None):
        value={**self.binding,'status':status,'model_load_status':self.model_load_status,
            'known_budget_used':copy.deepcopy(self.budget.used if self.budget else {k:{'calls':0,'frames':0} for k in LIMITS}),
            'known_call_records':copy.deepcopy(self.budget.records if self.budget else [])}
        if error is not None:
            value['primary_error']={'type':type(error).__name__,'message':str(error)[:2000]}
            value['secondary_errors']=copy.deepcopy(getattr(error,'_event_seam_secondary_errors',[]))
        atomic_json(self.output/'attempt-state.json',value,self.guard)


class DurableBudget(CallBudget):
    def __init__(self,limits,journal):
        super().__init__(limits);self.journal=journal;journal.budget=self
    def reserve(self,*args,**kwargs):
        ticket=super().reserve(*args,**kwargs)
        self.journal.write()  # No actual call may begin without its durable reservation.
        return ticket
    def complete(self,ticket):
        super().complete(ticket);self.journal.write()


def load_actual_tagger(verify):
    import numpy
    import sherpa_onnx as sherpa
    verify()
    return sherpa.AudioTagging(sherpa.AudioTaggingConfig(model=sherpa.AudioTaggingModelConfig(
        zipformer=sherpa.OfflineZipformerAudioTaggingModelConfig(model=str(MODEL/'model.onnx')),
        num_threads=1,provider='cpu'),labels=str(MODEL/'class_labels_indices.csv'),top_k=527))


def execute_admitted(output,frozen,receipt,guard,watchdog,loader=load_actual_tagger):
    validate_receipt(receipt,frozen,output)
    files=FrozenFiles(frozen['files']);files.verify(full=True)
    journal=AttemptJournal(output,frozen,receipt,guard)
    journal.start()  # A repeated authorization/output is refused before any model import/load.
    budget=DurableBudget(LIMITS,journal)
    try:
        journal.write()
        os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
        def verify():
            files.verify();return imported_receipt(frozen['files'])
        journal.model_load_status='attempted';journal.write()
        with watchdog.operation(45):tagger=loader(verify)
        journal.model_load_status='completed';journal.write()
        result=execute(output,pcm_sources(frozen['selected_rows']),tagger,budget,guard,verify,watchdog,digest(frozen))
        files.verify(full=True)
        atomic_json(output/'result.json',result,guard)
        journal.write('completed')
        return result
    except BaseException as primary:
        try:journal.write('failed',primary)
        except BaseException as reporting:
            # Preserve the original exception even when disk/resource failure prevents reporting.
            primary.add_note('Could not persist terminal failure; use full-ceiling interrupted accounting: '+str(reporting)[:1000])
        raise


def guarded_session(guard,watchdog,work):
    """Always attempt both owned cleanup and watcher close; never mask primary failure."""
    primary=None
    try:
        guard.check();watchdog.start()
        return work()
    except BaseException as error:
        primary=error
        raise
    finally:
        cleanup=[]
        for operation in (watchdog.close,guard.terminate_owned):
            try:operation()
            except BaseException as error:cleanup.append(error)
        if cleanup:
            if primary is not None:
                for error in cleanup:primary.add_note('Owned-session cleanup error: '+str(error)[:1000])
            else:
                first=cleanup[0]
                for error in cleanup[1:]:first.add_note('Additional cleanup error: '+str(error)[:1000])
                raise first


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['proposal','freeze','run'],nargs='?',default='proposal')
    parser.add_argument('--output',type=Path);parser.add_argument('--freeze',type=Path);parser.add_argument('--approval',type=Path)
    args=parser.parse_args()
    if args.mode=='proposal':print(json.dumps(proposal(),indent=2));return
    if not args.output:parser.error('--output required')
    output=require_private_output(args.output);output.mkdir(parents=True,exist_ok=True)
    if os.sched_getaffinity(0)!={7}:raise ValueError('Launch with CPU7 affinity')
    for name in ('OMP_NUM_THREADS','OMP_THREAD_LIMIT','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMBA_NUM_THREADS'):
        if os.environ.get(name)!='1':raise ValueError('Launch with all math thread limits=1')
    cap=256*1024**2;project=();feeds=[]
    if args.mode=='run':
        if not args.freeze or not args.approval:parser.error('Separate frozen protocol and approved root receipt required')
        frozen=json.loads(args.freeze.read_text());receipt=json.loads(args.approval.read_text());validate_receipt(receipt,frozen,output)
        if frozen['proposal']!=proposal():raise ValueError('Protocol drift')
        cap=receipt['own_rss_cap_bytes'];project=tuple(Identity(**r) for r in receipt.get('project_roots',[]))
        if receipt.get('external_feeds'):
            from audio_backend.fragment_resource_feed import EnduranceFeed
            feeds=[EnduranceFeed(**entry) for entry in receipt['external_feeds']]
    guard=ResourceGuard(ROOT,(Identity.bind(os.getpid()),),project,own_cap=cap,deadline=300,external_feeds=feeds)
    watchdog=Watchdog(guard,output/'watchdog-failure.json')
    def work():
        if args.mode=='freeze':result=freeze(output,guard)
        else:result=execute_admitted(output,frozen,receipt,guard,watchdog)
        guard.check()
        atomic_json(output/'resources.json',{'peak_own_rss':guard.peak_own,'peak_aggregate':guard.peak_aggregate,
            'minimum_host_available':guard.minimum_host,'minimum_free_disk':guard.minimum_disk,
            'sampled_not_kernel_hard':True,'external_observation':'asynchronous_estimate' if feeds else None},guard)
        print(json.dumps(result,indent=2))
    guarded_session(guard,watchdog,work)


if __name__=='__main__':main()
