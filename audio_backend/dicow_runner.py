"""Isolated bounded English target-speaker ASR. Never writes profile state."""
import json,os,sys,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
ERROR_PATH=None
STAGE='request_validation'

def fail_report(reason,message):
    if ERROR_PATH is not None:
        try:ERROR_PATH.write_text(json.dumps({'reason_code':reason,'message':str(message)[:2000]}))
        except OSError:pass

def main():
    global ERROR_PATH,STAGE
    path=Path(sys.argv[1])
    if path.stat().st_size>1024**2:raise ValueError('Oversized DiCoW request')
    request=json.loads(path.read_text());config=request['config'];out=Path(request['output'])
    ERROR_PATH=out.with_suffix('.error.json')
    jobs=request['jobs']
    if not 0<len(jobs)<=4:raise ValueError('DiCoW batch limited to4 bounded regions')
    os.environ.update(OMP_NUM_THREADS='2',OMP_THREAD_LIMIT='2',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='2')
    from audio_backend.runner import watch_parent
    threading.Thread(target=watch_parent,daemon=True).start()
    import psutil,soundfile as sf
    process=psutil.Process();stats={'peak_rss_bytes':0,'cpu_threads':2};stop=threading.Event()
    try:
        if hasattr(os,'sched_getaffinity'):os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:2])
        else:process.cpu_affinity(process.cpu_affinity()[:2])
        stats['cpu_affinity_enforced']=True
    except (AttributeError,NotImplementedError,psutil.Error):stats['cpu_affinity_enforced']=False
    cap=min(int(config.get('dicow_memory_limit_bytes',int(3.5*1024**3))),int(4.5*1024**3))
    def guard():
        while not stop.wait(.1):
            rss=process.memory_info().rss;stats['peak_rss_bytes']=max(stats['peak_rss_bytes'],rss)
            if rss>cap:
                fail_report('memory_guard',f'DiCoW child RSS exceeded {cap} bytes');os._exit(75)
            if psutil.virtual_memory().available<int(1.5*1024**3):
                fail_report('host_memory_reserve','Host available memory below1.5GiB reserve');os._exit(75)
    threading.Thread(target=guard,daemon=True).start()
    seconds=0
    for job in jobs:
        info=sf.info(job['path'])
        if info.channels!=1 or info.samplerate!=16000 or not 0<info.duration<=12:raise ValueError('Invalid DiCoW crop')
        if len(job['targets'])!=2:raise ValueError('DiCoW candidate region requires2 detectedtargets')
        seconds+=info.duration
    if seconds>float(config.get('overlap_max_seconds',12))+.001:raise ValueError('DiCoW audio budget exceeded')
    from audio_backend.inference.dicow_asr import DiCoWRecognizer
    STAGE='model_load'
    began=time.monotonic();recognizer=DiCoWRecognizer(config);groups=[]
    STAGE='inference'
    for job in jobs:
        tracks=[]
        for target in job['targets']:
            tracks.append({'conditioning_local_id':target['conditioning_local_id'],
                           'source_slot':target['source_slot'],**recognizer.transcribe(job,target)})
        groups.append({'overlap_group_id':job['overlap_group_id'],'tracks':tracks})
    stop.set();encoded=json.dumps({'groups':groups,'metadata':{**stats,'seconds':time.monotonic()-began}},allow_nan=False)
    if len(encoded)>1024**2:raise ValueError('DiCoW response exceeds1MiB')
    pending=out.with_suffix('.pending');pending.write_text(encoded);pending.replace(out)

if __name__=='__main__':
    try:main()
    except (ModuleNotFoundError,ImportError) as exc:
        fail_report('dependency_error',f'{type(exc).__name__}: {exc}');raise
    except Exception as exc:
        fail_report('model_load_error' if STAGE=='model_load' else 'inference_error',f'{type(exc).__name__}: {exc}');raise
