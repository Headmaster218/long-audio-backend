# PUBLIC INSPECTION COPY. Do not execute or import.
"""Bounded recheck of already authenticated retained original owners only."""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import hashlib
import json
import time

READS=5
SECONDS=.1
STABLE=('pid','ticks','parent','uid','pgid','sid')

class DiagnosticFailure(OSError):
    """A sink failure must never be mistaken for disappearing proc metadata."""

def fingerprint(value):
    raw=json.dumps(value,separators=(',',':'),ensure_ascii=True).encode()
    return dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())

def recheck(family,name,record):
    """Return an exact live/Z row, or raise Vanished only after positive exit proof.

    A same-birth zombie remains the inherited contract, not a positive wait.
    Callers must not signal or retire on empty metadata or on an old row.
    """
    p,q,argv,*_=family.originals[name]
    old=family.root_identities[name]
    def require(ok,reason):
        if not ok:raise RuntimeError(reason)
    require(all(k in old for k in STABLE) and old['pid']==p.pid and isinstance(old.get('exe'),str) and bool(old['exe']),
            'Original retained binding incomplete')
    require(old.get('boot')==family.boot,'Original retained boot changed')
    phase='cleanup' if family.shutting_down else 'normal'
    remaining=family.deadlines.cleanup_remaining() if family.shutting_down else family.deadlines.remaining('normal')
    started=time.monotonic();end=min(started+SECONDS,started+remaining)
    previous=getattr(family,'terminal_grace_deadline',None)
    if previous is not None:end=min(end,previous)
    family.terminal_grace_deadline=end
    context=dict(kind='original-recheck',name=name,pid=p.pid,birth=old['ticks'],phase=phase,
                 started=started,deadline=end,attempt=0,stage='begin',kernel=None,
                 exe=None,argv=None,exe_outcome='not_read',argv_outcome='not_read',
                 positive_original_wait=False,numeric_pid_absent=False)
    family.original_recheck_context=context
    counters=getattr(family,'original_recheck_counters',None)
    if counters is None:
        counters=dict(calls=0,unavailable=0,resolved_live=0,resolved_exit=0,zombie=0,refusals=0)
        family.original_recheck_counters=counters
    counters['calls']+=1
    anomalous=False
    def evidence(outcome,**extra):
        key={'metadata-unavailable':'unavailable','resolved-live-exact':'resolved_live','resolved-exit':'resolved_exit','same-birth-zombie':'zombie','failed-closed':'refusals'}.get(outcome)
        if key:counters[key]+=1
        row=dict(context,outcome=outcome,at=time.monotonic(),**extra)
        require(len(json.dumps(row,separators=(',',':')).encode())<=4096,'Original recheck diagnostic cap')
        try:record(row)
        except Exception as exc:
            context['diagnostic_write_error']=type(exc).__name__
            raise DiagnosticFailure('Original recheck diagnostic write failed') from exc
        if outcome in ('resolved-live-exact','resolved-exit','same-birth-zombie'):within()
    def within():require(time.monotonic()<end,'Original recheck fixed deadline exhausted')
    def kernel():
        within();family.kernel_stage='original-recheck'
        try:row=family._kernel(p.pid,old)
        except BaseException:
            raw=getattr(family,'read_context',{})
            context['kernel_read_stage']=raw.get('stage')
            context['kernel']={k:raw.get('kernel',{}).get(k) for k in (*STABLE,'state')}
            raise
        context['kernel']={k:row.get(k) for k in (*STABLE,'state')}
        require(all(row.get(k)==old[k] for k in STABLE),'Original kernel identity changed')
        within();return row
    def exited():
        context['stage']='original-handle-poll'
        within();code=p.poll();within();context['poll_returncode']=code
        if code is None:return False
        context['stage']='original-handle-wait';result=p.wait(timeout=0)
        within();context['positive_original_wait']=True;context['wait_returncode']=result
        require(result==code,'Original wait status changed')
        context['stage']='numeric-absence';absent=family._absent(p.pid)
        within();context['numeric_pid_absent']=bool(absent)
        require(absent,'Original PID present or reused after positive wait')
        proof=dict(name=name,pid=p.pid,start_ticks=old['ticks'],wait_returncode=result,
                   positive_original_wait=True,numeric_pid_absent=True,at=time.monotonic())
        old['original_recheck_exit_confirmation']=proof
        evidence('resolved-exit')
        exc=family.Vanished(p.pid,old['ticks'])
        exc.original_recheck_exit_confirmation=proof
        exc.original_recheck_owner=p
        raise exc
    try:
        for attempt in range(READS):
            context.update(attempt=attempt+1,stage='kernel-before',kernel=None,exe=None,argv=None,
                           exe_outcome='not_read',argv_outcome='not_read',read_exception=None)
            within()
            try:
                row=kernel()
                if row['state']=='Z':
                    if anomalous:evidence('same-birth-zombie')
                    return row
                # Fresh wrapper avoids cached executable metadata from a prior read.
                context['stage']='process-wrapper';q=family.psutil.Process(p.pid)
                context['stage']='executable';context['exe_outcome']='reading';exe=q.exe();context['exe']=fingerprint(exe)
                context['exe_outcome']='exact' if exe==old['exe'] else 'empty' if not exe else 'mismatch'
                require(not exe or exe==old['exe'],'Original executable changed')
                context['stage']='argv';context['argv_outcome']='reading';command=q.cmdline();context['argv']=fingerprint(command)
                context['argv_outcome']='exact' if command==argv else 'empty' if not command else 'mismatch'
                require(not command or command==argv,'Original argv changed')
                context['stage']='kernel-after';row=kernel()
                if row['state']=='Z':
                    if anomalous or not exe or not command:evidence('same-birth-zombie')
                    return row
                if exe and command:
                    if anomalous:evidence('resolved-live-exact')
                    return row
                anomalous=True;context['stage']='empty-metadata';evidence('metadata-unavailable')
            except family.psutil.ZombieProcess as exc:
                require(exc.pid==p.pid,'Original metadata foreign zombie')
                anomalous=True;context['read_exception']='ZombieProcess'
                if context.get('exe_outcome')=='reading':context['exe_outcome']='exception'
                if context.get('argv_outcome')=='reading':context['argv_outcome']='exception'
                evidence('metadata-unavailable')
            except family.psutil.NoSuchProcess as exc:
                require(exc.pid==p.pid,'Original metadata foreign disappearance')
                anomalous=True;context['read_exception']='NoSuchProcess'
                if context.get('exe_outcome')=='reading':context['exe_outcome']='exception'
                if context.get('argv_outcome')=='reading':context['argv_outcome']='exception'
                evidence('metadata-unavailable')
            except FileNotFoundError:
                anomalous=True;context['read_exception']='FileNotFoundError'
                if context.get('exe_outcome')=='reading':context['exe_outcome']='exception'
                if context.get('argv_outcome')=='reading':context['argv_outcome']='exception'
                evidence('metadata-unavailable')
            except family.Vanished as exc:
                require(exc.pid==p.pid and exc.ticks in (None,old['ticks']),'Original kernel foreign disappearance')
                anomalous=True;context['read_exception']='Vanished'
                if context.get('exe_outcome')=='reading':context['exe_outcome']='exception'
                if context.get('argv_outcome')=='reading':context['argv_outcome']='exception'
                evidence('metadata-unavailable')
            # Missing metadata is not terminal evidence. Only the retained handle
            # and strict numeric absence can resolve an exit between observations.
            exited()
            if attempt+1<READS:
                within();time.sleep(min(.02,max(0,end-time.monotonic())))
        raise RuntimeError('Original metadata unavailable within fixed recheck bounds')
    except BaseException as exc:
        if context.get('exe_outcome')=='reading':context['exe_outcome']='exception'
        if context.get('argv_outcome')=='reading':context['argv_outcome']='exception'
        if isinstance(exc,DiagnosticFailure):
            counters['refusals']+=1
        elif not getattr(exc,'original_recheck_exit_confirmation',None):
            evidence('failed-closed',exception_type=type(exc).__name__,reason=str(exc)[:512])
        raise
    finally:
        if previous is None:
            try:del family.terminal_grace_deadline
            except AttributeError:pass
        else:family.terminal_grace_deadline=previous
