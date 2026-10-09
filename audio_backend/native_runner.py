"""Isolated opt-in native diarization process; parent-generated paths only."""
import json
import os
import sys
import threading
from pathlib import Path
from .runner import watch_parent


def main():
    config=json.loads(Path(sys.argv[1]).read_text())
    threads=min(2,max(1,int(config.get('native_diarization_threads',2))))
    os.environ['OMP_NUM_THREADS']=str(threads)
    os.environ['OMP_THREAD_LIMIT']=str(threads)
    os.environ['OPENBLAS_NUM_THREADS']=str(threads)
    affinity_enforced=False
    if hasattr(os,'sched_getaffinity'):
        os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:threads]);affinity_enforced=True
    else:
        import psutil
        try:
            process=psutil.Process();process.cpu_affinity(process.cpu_affinity()[:threads]);affinity_enforced=True
        except (AttributeError,NotImplementedError,psutil.Error):pass
    threading.Thread(target=watch_parent,daemon=True).start()
    from .inference.native_diarization import NativeDiarizer
    provider=None
    try:
        for line in sys.stdin:
            if len(line)>16384:raise ValueError('Oversized native request')
            request=json.loads(line);out=Path(request['output'])
            try:
                if provider is None:provider=NativeDiarizer(config)
                action=request['action']
                if action=='reset':
                    value=provider.reset(request['epoch_id'])
                    provenance=provider.provenance()
                    provenance['diarization_wrapper_cpu_limit']=threads
                    provenance['diarization_cpu_affinity_enforced']=affinity_enforced
                    if not affinity_enforced:provenance['diarization_cpu_cap_warning']='Affinity unavailable: only OpenMP limits set; native CPU cap unverified on this platform.'
                    result={'state':value,'provenance':provenance}
                elif action=='push':result=provider.push(request['wav'])
                elif action=='finish':result=provider.finish()
                elif action=='turns':result={'turns':provider.turns(request['start'],request['end'],allow_provisional=True)}
                else:raise ValueError('Unsupported native command')
                encoded=json.dumps(result,allow_nan=False)
                if len(encoded)>8*1024**2:raise ValueError('Native response exceeds8MiB')
            except Exception as exc:encoded=json.dumps({'_error':f'{type(exc).__name__}: {exc}'[:2000]})
            pending=out.with_suffix('.pending');pending.write_text(encoded);pending.replace(out)
    finally:
        if provider:provider.close()

if __name__=='__main__':main()
