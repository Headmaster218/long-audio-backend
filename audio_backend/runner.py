"""Isolated sequential analyzer process; local parent-generated paths only."""
import json
import sys
import os
import threading
import time
from pathlib import Path


def write_result(analyzer,wav,result_path,diarization_path=None,analysis_options=None):
    path=Path(result_path)
    try:
        arguments={}
        if diarization_path:
            d=Path(diarization_path)
            if d.stat().st_size>1024**2:raise ValueError('External diarization exceeds1MiB')
            arguments['diarization']=json.loads(d.read_text())
        if analysis_options is not None:arguments['analysis_options']=analysis_options
        result=analyzer.analyze(wav,**arguments)
        encoded=json.dumps(result,allow_nan=False)
        if len(encoded)>8*1024**2: raise ValueError('Analyzer output exceeds 8 MiB window limit')
    except Exception as exc:
        encoded=json.dumps({'_error':f'{type(exc).__name__}: {exc}'[:2000]})
    tmp=path.with_suffix('.pending')
    tmp.write_text(encoded,encoding='utf-8');tmp.replace(path)


def watch_parent():
    import psutil
    pid=os.getenv('AUDIO_PARENT_PID');created=os.getenv('AUDIO_PARENT_CREATED')
    if not pid: return
    while True:
        try:
            parent=psutil.Process(int(pid))
            if abs(parent.create_time()-float(created))>.01: raise psutil.NoSuchProcess(int(pid))
            if parent.status()==psutil.STATUS_ZOMBIE: raise psutil.NoSuchProcess(int(pid))
        except psutil.Error:
            for child in psutil.Process().children(recursive=True):
                try: child.kill()
                except psutil.Error: pass
            os._exit(70)
        time.sleep(.5)


def main():
    threading.Thread(target=watch_parent,daemon=True).start()
    from audio_backend.inference import AudioAnalyzer
    if sys.argv[1]=='--serve':
        analyzer=AudioAnalyzer(json.loads(Path(sys.argv[2]).read_text()))
        try:
            for line in sys.stdin:
                if len(line)>16384: raise ValueError('Oversized request')
                request=json.loads(line)
                write_result(analyzer,request['wav'],request['output'],request.get('diarization'),request.get('analysis_options'))
        finally: analyzer.close()
    else:
        wav,result_path,config_path=sys.argv[1:]
        analyzer=AudioAnalyzer(json.loads(Path(config_path).read_text()))
        try: write_result(analyzer,wav,result_path)
        finally: analyzer.close()

if __name__=='__main__': main()
