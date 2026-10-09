"""One bounded separator job batch, supervised by the normal service watchdog."""
import json,os,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    config_path=Path(sys.argv[1])
    if config_path.stat().st_size>1024**2:raise ValueError('Oversized overlap request')
    request=json.loads(config_path.read_text());config=request['config'];out=Path(request['output'])
    threads=min(2,max(1,int(config.get('overlap_threads',2))))
    os.environ['OMP_NUM_THREADS']=str(threads);os.environ['OMP_THREAD_LIMIT']=str(threads);os.environ['OPENBLAS_NUM_THREADS']='1'
    from audio_backend.runner import watch_parent
    threading.Thread(target=watch_parent,daemon=True).start()
    import psutil
    process=psutil.Process();affinity=False
    try:
        if hasattr(os,'sched_getaffinity'):
            os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:threads]);affinity=True
        else:
            process.cpu_affinity(process.cpu_affinity()[:threads]);affinity=True
    except (AttributeError,NotImplementedError,psutil.Error):pass
    stats={'peak_rss_bytes':0,'cpu_threads':threads,'cpu_affinity_enforced':affinity};stop=threading.Event()
    def guard():
        while not stop.wait(.1):
            rss=process.memory_info().rss;stats['peak_rss_bytes']=max(rss,stats['peak_rss_bytes'])
            if rss>min(int(config.get('overlap_memory_limit_bytes',int(2.5*1024**3))),int(2.5*1024**3)) or psutil.virtual_memory().available<768*1024**2:os._exit(75)
    threading.Thread(target=guard,daemon=True).start()
    import soundfile as sf,numpy as np
    from audio_backend.inference.mossformer_separation import MossFormerSeparator
    jobs=request['jobs']
    if not 0<len(jobs)<=16:raise ValueError('Invalid overlap job count')
    checked=[];seconds=0
    for job in jobs:
        info=sf.info(job['path'])
        if info.channels!=1 or info.samplerate!=16000 or not 0<info.duration<=12:raise ValueError('Invalid bounded separator audio')
        seconds+=info.duration;checked.append(job)
    if seconds>float(config.get('overlap_max_seconds',12))+.001:raise ValueError('Overlap batch duration limit exceeded')
    started=time.monotonic();separator=MossFormerSeparator(config);results=[]
    for index,job in enumerate(checked):
        samples,sr=sf.read(job['path'],dtype='float32');result=separator.separate(samples)
        for n,track in enumerate(result['tracks']):
            path=out.parent/f'source-{index}-{n}.wav';sf.write(path,track.pop('samples'),sr,subtype='FLOAT');track['path']=str(path)
        result['overlap_group_id']=job['overlap_group_id'];results.append(result)
    stop.set();payload={'groups':results,'metadata':{**stats,'seconds':time.monotonic()-started}}
    encoded=json.dumps(payload,allow_nan=False)
    if len(encoded)>1024**2:raise ValueError('Overlap response exceeds1MiB')
    pending=out.with_suffix('.pending');pending.write_text(encoded);pending.replace(out)
if __name__=='__main__':
    try:main()
    except Exception as exc:
        code=('missing_runtime_dependency' if isinstance(exc,ModuleNotFoundError) else
              'runtime_dependency_error' if isinstance(exc,ImportError) else
              'missing_model_or_source' if isinstance(exc,FileNotFoundError) else
              'invalid_model_or_runtime' if isinstance(exc,RuntimeError) else 'separator_failed')
        try:
            request=json.loads(Path(sys.argv[1]).read_text());out=Path(request['output'])
            pending=out.with_suffix('.pending');pending.write_text(json.dumps({'error':{'code':code,'detail':f'{type(exc).__name__}: {exc}'[:500]}}));pending.replace(out)
        except Exception:raise exc

