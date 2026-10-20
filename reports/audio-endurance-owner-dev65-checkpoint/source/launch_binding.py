# PUBLIC INSPECTION COPY. Do not execute or import.
"""Bounded original binding. No mismatched process is ever admitted."""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import hashlib,json,time
from pathlib import Path

def fingerprint(value):
    raw=json.dumps(value,separators=(',',':'),ensure_ascii=True).encode()
    return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}

def bind(family,process,argv,record,seconds=.1,reads=5):
    if seconds!=.1 or reads!=5:raise RuntimeError('Fixed binding bounds')
    end=min(time.monotonic()+seconds,time.monotonic()+family.deadlines.remaining('normal'))
    expected=family._kernel(process.pid)
    if expected['parent']!=family.helper['pid']:raise RuntimeError('Original parent changed')
    wanted=str(Path(argv[0]).resolve())
    for attempt in range(reads):
        if time.monotonic()>=end:break
        row=family._kernel(process.pid,expected)
        if row['state']=='Z' or process.poll() is not None:raise RuntimeError('Original exited before binding')
        # Fresh Process avoids cached exe evidence surviving a subsequent read.
        q=family.psutil.Process(process.pid)
        command=q.cmdline();exe=q.exe()
        final=family._kernel(process.pid,expected)
        observation=dict(kind='original-bind',attempt=attempt+1,pid=process.pid,birth=expected['ticks'],state=final['state'],argv=fingerprint(command),exe=fingerprint(exe),match=command==argv and exe==wanted)
        record(observation)
        if time.monotonic()>=end:break
        if observation['match'] and final['state']!='Z' and process.poll() is None:
            final.update(exe=exe,boot=family.boot)
            # All binding fields are proven again after full metadata observation.
            final.update(family._kernel(process.pid,final))
            if time.monotonic()>=end or final['state']=='Z' or process.poll() is not None:break
            return q,final
        if attempt+1<reads:time.sleep(min(.02,max(0,end-time.monotonic())))
    raise RuntimeError('Original launch identity not proven within bounded handshake')
