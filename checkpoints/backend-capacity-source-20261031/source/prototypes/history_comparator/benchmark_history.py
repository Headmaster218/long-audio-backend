"""One fixed dense model-free case:2880 cold rows then4 append rows =2884."""
import hashlib
import json
import math
import random
import resource
import struct
import time
from pathlib import Path
from compact_history import History, Observation, Work, canonical

ROOT=Path(__file__).resolve().parent
SEED=271828
BASE,APPEND=2880,4
started=time.perf_counter()
report={'schema':'bounded-history-prototype-benchmark-v1','seed':SEED,'cold_rows':BASE,'append_rows':APPEND,
        'dimension':512,'data':'dense Random.uniform(-1,1), binary64 normalization once, original float32 cast',
        'not_measured':['4096 rows','adversarial large multi-round completion','ongoing-arrival convergence',
                        'production SQLite latency','primary audio delay','model inference or acoustic accuracy'],
        'stages':{},'status':'running'}


def receipt():
    report['elapsed_seconds']=time.perf_counter()-started
    report['self_maxrss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
    (ROOT/'reports/benchmark-progress.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')


def run(stage,iterator,work):
    then=time.perf_counter();last=then
    while True:
        try:next(iterator)
        except StopIteration as done:
            report['stages'][stage]={'seconds':time.perf_counter()-then,'counters':dict(work.counts),
                                     'max_observed_quantum_seconds':work.max_quantum_seconds,
                                     'subphase_seconds':dict(work.phase_seconds)}
            receipt();print(stage,json.dumps(report['stages'][stage]),flush=True)
            if stage in ('cold_full_comparison','post_append_full_comparison') and time.perf_counter()-then>60:
                report['status']='capacity_failure_at_illustrative_60_second_cadence';receipt()
                raise RuntimeError('Completed comparison exceeded illustrative60-second interval')
            return done.value
        now=time.perf_counter()
        if stage in ('cold_full_comparison','post_append_full_comparison') and now-then>60:
            report['status']='capacity_failure_at_illustrative_60_second_cadence'
            report['active_stage']=stage
            report['partial_stage']={'seconds':now-then,'counters':dict(work.counts),'subphase_seconds':dict(work.phase_seconds)}
            receipt()
            raise RuntimeError('Full comparison exceeded illustrative60-second arrival interval; stopped without widening')
        if now-last>=2:
            report['active_stage']=stage
            report['partial_stage']={'seconds':now-then,'counters':dict(work.counts)}
            receipt();last=now


rng=random.Random(SEED)
rows=[]
generation=time.perf_counter()
vector_hash=hashlib.sha256()
for i in range(BASE+APPEND):
    values=[rng.uniform(-1.,1.) for _ in range(512)]
    norm=math.sqrt(math.fsum(v*v for v in values))
    values=list(struct.unpack('<512f',struct.pack('<512f',*(v/norm for v in values))))
    row=Observation.from_vector(f'n{i:05}',values,attributions=(('native',f'label{i}'),),
                                spans=(('one-source',100000000,i*32000,(i+1)*32000),),
                                stream='moss' if i%101==0 else 'primary')
    rows.append(row);vector_hash.update(row.vector)
report['generator_seconds']=time.perf_counter()-generation
report['input_vector_sha256']=vector_hash.hexdigest()
h=History();w=Work();run('cold_exact_scores',h.append(rows[:BASE],w),w)
assert len(h.scores)==BASE*(BASE-1)//2
cold_pairs=len(h.scores)
old_hash=hashlib.sha256(memoryview(h.scores).cast('B')).hexdigest()
w=Work();cold=run('cold_full_comparison',h.compare(w),w)
report['cold_clusters']=len(cold.clusters)
del cold
w=Work();run('incremental_exact_scores',h.append(rows[BASE:],w),w)
assert w.counts['dot_products']==APPEND*BASE+APPEND*(APPEND-1)//2
report['old_old_prefix_preserved']=hashlib.sha256(memoryview(h.scores).cast('B')[:cold_pairs*8]).hexdigest()==old_hash
assert report['old_old_prefix_preserved']
w=Work();decision=run('post_append_full_comparison',h.compare(w),w)
report['final_clusters']=len(decision.clusters)
report['raw_storage_bytes']={'vectors':sum(len(v) for v in h.vector_rows),'score_cache':len(h.scores)*8,
                             'two_dependency_bitsets':2*((len(h.scores)+7)//8)}
report['final_pair_count']=len(h.scores)
report['paging']={}
for name,method in [('annotation',h.annotation_page),('decision',h.decision_page)]:
    then=time.perf_counter();after=pages=rows_count=minority=maximum=0;page_hash=hashlib.sha256()
    while True:
        page=method(h.token(),after)
        raw=canonical(page);page_hash.update(raw)
        pages+=1;rows_count+=len(page['rows']);maximum=max(maximum,len(raw))
        minority+=sum(r.get('stream')=='moss' for r in page['rows'])
        after=page['next_after']
        if page['complete']:break
    report['paging'][name]={'seconds':time.perf_counter()-then,'pages':pages,'rows':rows_count,
                            'minority_stream_rows':minority,'max_encoded_page_bytes':maximum,
                            'pages_sha256':page_hash.hexdigest()}
assert report['paging']['annotation']['rows']==BASE+APPEND
assert report['paging']['annotation']['minority_stream_rows']==sum(r.stream=='moss' for r in rows)
report.pop('active_stage',None);report.pop('partial_stage',None)
report['status']='complete';receipt()
(ROOT/'reports/benchmark.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
print(json.dumps(report,sort_keys=True),flush=True)
