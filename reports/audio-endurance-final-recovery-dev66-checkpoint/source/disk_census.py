# PUBLIC SOURCE INSPECTION ONLY. This is not a runnable distribution.
"""Bounded descriptor-relative disk/log sampling; no deployment entry point."""

raise RuntimeError("Inspection-only transformed excerpt: do not execute or import; private dependencies and pins are intentionally omitted.")

import errno
import json
import os
from pathlib import Path
import stat
import threading
import time

MIB=1024**2
ENTRY_CAP=262144
DEPTH_CAP=64
FRESH_SECONDS=15
DIAGNOSTIC_CAP=8*MIB

class Budget:
    def __init__(self,root,plan,diagnostic_path=None,stop_type=RuntimeError,final_snapshot_reserve=0):
        self.root=Path(root).absolute();self.plan=plan;self.stop_type=stop_type
        self._require(type(final_snapshot_reserve) is int and final_snapshot_reserve in (0,512*MIB),
                      "Final snapshot reserve must be disabled or exactly512MiB")
        self.final_snapshot_reserve=final_snapshot_reserve;self.in_terminal=False;self.terminal_used=0;self._terminal_needs_census=False
        self._snapshot_state="held" if final_snapshot_reserve else "disabled"
        self._snapshot_target=None;self._snapshot_parent=None;self._snapshot_identity=None
        self._snapshot_credit=0;self._snapshot_credit_at=None;self._snapshot_failure=None
        self.lock=threading.RLock();self.last_time=None;self.last_bytes=0;self.reserved=0;self.peak=0
        self.log_bytes=0;self.ledger_bytes={};self._records={};self._episode=None
        self._anchors=[];self._diag=None;self._diag_bytes=0;self._diag_records=0
        self.gap_count=0;self.refusal_count=0;self.recovery_count=0;self.census_count=0
        self.last_diagnostic=None;self.diagnostic_write_error=None;self.observed_bytes=0;self.observed_log_bytes=0;self.read_context={'stage':'initial','path':''}
        try:
            parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
            self._anchors.append((parent,None,None,self._identity(os.fstat(parent))))
            for component in self.root.parts[1:]:
                self._require(len(self._anchors)<DEPTH_CAP,'Root ancestor depth cap')
                before=os.stat(component,dir_fd=parent,follow_symlinks=False)
                self._require(stat.S_ISDIR(before.st_mode),'Root ancestor is not a directory')
                child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
                try:self._require(self._identity(os.fstat(child))==self._identity(before),'Root ancestor changed while opening')
                except BaseException:os.close(child);raise
                self._anchors.append((child,parent,component,self._identity(before)));parent=child
            self.root_fd=parent;self.device=os.fstat(parent).st_dev
            if diagnostic_path is not None:
                path=Path(diagnostic_path).absolute()
                diagnostic_parent=self.root_fd
                if path.parent!=self.root:
                    self._require(path.parent.parent==self.root and path.parent.name.startswith('output'),'Diagnostic directory must be one output child below root')
                    before=os.stat(path.parent.name,dir_fd=self.root_fd,follow_symlinks=False)
                    self._require(stat.S_ISDIR(before.st_mode) and before.st_dev==self.device,'Diagnostic parent type/device')
                    diagnostic_parent=os.open(path.parent.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=self.root_fd)
                    try:self._require(self._identity(os.fstat(diagnostic_parent))==self._identity(before),'Diagnostic parent changed while opening')
                    except BaseException:os.close(diagnostic_parent);raise
                    self._anchors.append((diagnostic_parent,self.root_fd,path.parent.name,self._identity(before)))
                fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=diagnostic_parent)
                self._diag=(fd,path.name,self._identity(os.fstat(fd)),diagnostic_parent)
        except BaseException:
            self.close();raise
    @staticmethod
    def _identity(info):return (info.st_dev,info.st_ino,stat.S_IFMT(info.st_mode))
    def _require(self,condition,message):
        if not condition:raise self.stop_type(message)
    def _root_ok(self):
        for fd,parent,name,identity in self._anchors:
            self._require(self._identity(os.fstat(fd))==identity,'Held root identity changed')
            if parent is not None:
                self._require(self._identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==identity,'Root ancestor binding changed')
    def _free(self,growth=0):
        self._root_ok();fs=os.fstatvfs(self.root_fd)
        self._require(fs.f_bavail*fs.f_frsize>=self.plan.disk_floor+growth+self._snapshot_pending()+(64*MIB if self.final_snapshot_reserve and not self.in_terminal else 0),'Disk free-space floor')
    def _record(self,event,**fields):
        row=dict(event=event,at=time.monotonic(),last_clean=self.last_time,charged=self.last_bytes+self.reserved,
                 reserved=self.reserved,final_snapshot_pending=self._snapshot_pending(),final_snapshot_state=self._snapshot_state,gaps=self.gap_count,refusals=self.refusal_count,**fields)
        raw=(json.dumps(row,separators=(',',':'),sort_keys=True)+'\n').encode()
        self._require(len(raw)<=4096,'Disk diagnostic record cap');self.last_diagnostic=row
        if self._diag is None:return
        fd,name,identity,diagnostic_parent=self._diag
        self._require(self._identity(os.fstat(fd))==identity and self._identity(os.stat(name,dir_fd=diagnostic_parent,follow_symlinks=False))==identity,'Disk diagnostic identity changed')
        self._require(self._diag_bytes+len(raw)<=DIAGNOSTIC_CAP and self._diag_records<32768,'Disk diagnostic ledger cap')
        # This channel is bounded inside the existing64MiB terminal headroom.
        # It deliberately never calls admit(), avoiding census/ledger recursion.
        self._require(self.last_bytes+self.reserved+self._snapshot_pending()+(len(raw)+4096 if self.in_terminal else max(DIAGNOSTIC_CAP,len(raw),64*MIB if self.final_snapshot_reserve else 0))<=self.plan.disk_bytes,'Diagnostic reserved headroom ceiling')
        self._free(len(raw)+4096 if self.in_terminal else len(raw))
        if self.in_terminal:
            self._terminal_debit(len(raw)+4096);self.reserved+=len(raw)+4096
        view=memoryview(raw)
        while view:
            written=os.write(fd,view);self._require(written>0,'Disk diagnostic short write');view=view[written:]
        os.fsync(fd);self._diag_bytes+=len(raw);self._diag_records+=1
    def _binding(self,fd,parent,name,identity,relative,gaps):
        self._require(self._identity(os.fstat(fd))==identity,'Directory descriptor identity changed')
        if parent is None:return True
        try:current=os.stat(name,dir_fd=parent,follow_symlinks=False)
        except FileNotFoundError:
            try:os.stat(name,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:
                gaps.append(dict(path=relative,stage='directory-binding-absent'));return False
            raise self.stop_type('Directory disappeared then reappeared')
        self._require(self._identity(current)==identity,'Directory parent binding changed')
        return True
    def _walk(self):
        records={};gaps=[];entries=0;started=time.monotonic()
        def walk(fd,parent,name,identity,relative,depth):
            nonlocal entries
            self._require(depth<=DEPTH_CAP,'Run census depth cap')
            self._require(time.monotonic()-started<=5,'Run census duration cap')
            if not self._binding(fd,parent,name,identity,relative,gaps):return
            info=os.fstat(fd);key=(relative,*identity);old=records.get(key,(0,0,None));records[key]=(max(old[0],info.st_size,info.st_blocks*512),0,None)
            self.read_context={'stage':'scandir','path':relative}
            with os.scandir(fd) as scan:
                for entry in scan:
                    entries+=1;self._require(entries<=ENTRY_CAP,'Run census entry cap')
                    self._require(time.monotonic()-started<=5,'Run census duration cap')
                    self._require(entry.name not in ('.','..') and '/' not in entry.name,'Invalid census name')
                    rel=(relative+'/'+entry.name).lstrip('/');self.read_context={'stage':'entry-stat','path':rel}
                    try:before=os.stat(entry.name,dir_fd=fd,follow_symlinks=False)
                    except FileNotFoundError:
                        try:before=os.stat(entry.name,dir_fd=fd,follow_symlinks=False)
                        except FileNotFoundError:
                            gaps.append(dict(path=rel,stage='entry-absent'));continue
                        self._require(stat.S_ISREG(before.st_mode) and before.st_dev==self.device,'Reappeared entry type/device drift')
                        gaps.append(dict(path=rel,stage='regular-entry-reappeared'))
                    self._require(before.st_dev==self.device,'Run census device changed')
                    self._require(stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode),'Run-root symlink or special file refused')
                    key=(rel,*self._identity(before));allocated=max(before.st_size,before.st_blocks*512)
                    is_log=entry.name.endswith(('.stdout','.stderr'));log=before.st_size if is_log else 0
                    parts=rel.split('/');bucket=entry.name if len(parts)==2 and parts[0].startswith('output') and entry.name.endswith('.jsonl') else None
                    records[key]=(allocated,log,bucket)
                    if stat.S_ISDIR(before.st_mode):
                        try:child=os.open(entry.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
                        except FileNotFoundError:
                            try:os.stat(entry.name,dir_fd=fd,follow_symlinks=False)
                            except FileNotFoundError:gaps.append(dict(path=rel,stage='directory-open-absent'));continue
                            raise self.stop_type('Directory vanished then reappeared before open')
                        try:
                            self._require(self._identity(os.fstat(child))==self._identity(before),'Directory changed before open')
                            walk(child,fd,entry.name,self._identity(before),rel,depth+1)
                        finally:os.close(child)
                    else:
                        try:after=os.stat(entry.name,dir_fd=fd,follow_symlinks=False)
                        except FileNotFoundError:
                            try:after=os.stat(entry.name,dir_fd=fd,follow_symlinks=False)
                            except FileNotFoundError:gaps.append(dict(path=rel,stage='file-postread-absent'));continue
                            gaps.append(dict(path=rel,stage='regular-file-reappeared'))
                        self._require(stat.S_ISREG(after.st_mode) and after.st_dev==self.device,'File bracket type/device drift')
                        if self._identity(after)!=self._identity(before):
                            new_key=(rel,*self._identity(after));records[new_key]=(max(after.st_size,after.st_blocks*512),after.st_size if is_log else 0,bucket)
                            gaps.append(dict(path=rel,stage='regular-file-replaced'))
                        else:records[key]=(max(allocated,after.st_size,after.st_blocks*512),max(log,after.st_size if is_log else 0),bucket)
            self._binding(fd,parent,name,identity,relative,gaps)
        self._root_ok()
        try:walk(self.root_fd,None,None,self._identity(os.fstat(self.root_fd)),'',0)
        except BaseException as exc:
            self._partial=records;self._partial_gaps=gaps;raise
        self._partial=records;self._partial_gaps=gaps
        self._root_ok();self._require(time.monotonic()-started<=5,'Run census duration cap');return records,gaps
    @staticmethod
    def _totals(records):
        size=logs=0;buckets={}
        for allocated,log,bucket in records.values():
            size+=allocated;logs+=log
            if bucket:buckets[bucket]=buckets.get(bucket,0)+allocated
        return size,logs,buckets
    def _retain(self,records):
        if self._episode is None:
            self._episode={'base':self.last_bytes,'logs':self.log_bytes,'maxima':dict(self._records),'increments':0,'log_increments':0}
        e=self._episode
        for key,value in records.items():
            old=e['maxima'].get(key,(0,0,None));e['increments']+=max(0,value[0]-old[0]);e['log_increments']+=max(0,value[1]-old[1])
            e['maxima'][key]=(max(value[0],old[0]),max(value[1],old[1]),value[2])
        self._require(len(e['maxima'])<=ENTRY_CAP,'Retained disk identity cap')
        self.last_bytes=e['base']+e['increments'];self.log_bytes=e['logs']+e['log_increments']
    def census(self):
        with self.lock:
            self.census_count+=1;self._partial={};self._partial_gaps=[]
            try:
                records,gaps=self._walk();now=time.monotonic()
                self._snapshot_observe(records,clean=not gaps,now=now)
                size,logs,buckets=self._totals(records)
                self.observed_bytes=size;self.observed_log_bytes=logs
                if gaps or self._episode is not None:
                    self._retain(records)
                    self._require(self.last_time is not None,'Incomplete initial disk census')
                    self._require(now-self.last_time<FRESH_SECONDS,'Disk census stale after terminal gaps')
                    charge=max(size,self.last_bytes+self.reserved);charged_logs=max(logs,self.log_bytes)
                else:charge=max(size,self.last_bytes+self.reserved);charged_logs=logs
                self.peak=max(self.peak,charge)
                self._require(charge+self._snapshot_pending()+(0 if self.in_terminal else 64*MIB)<=self.plan.disk_bytes,'Run disk ceiling')
                self._require(charged_logs<=256*MIB,'Product log cap');self._free()
                if gaps:
                    self.gap_count+=len(gaps)
                    for gap in gaps[:16]:
                        current=[dict(device=k[1],inode=k[2],type=k[3],allocated=v[0],log_bytes=v[1]) for k,v in records.items() if k[0]==gap['path']]
                        prior=[dict(device=k[1],inode=k[2],type=k[3],allocated=v[0],log_bytes=v[1]) for k,v in self._records.items() if k[0]==gap['path']]
                        self._record('gap',**gap,readable_identities=current[:4] or None,prior_identities=prior[:4] or None,unmeasured=not current and not prior,identity_count=len(current)+len(prior))
                    if len(gaps)>16:self._record('gap-summary',additional=len(gaps)-16)
                    return charge
                recovered=self._episode is not None;reserved_before_record=self.reserved
                if recovered:self.recovery_count+=1;self._record('recovered',readable_bytes=size,retained_checked_bytes=charge)
                self.last_bytes=size;self.log_bytes=logs;self.ledger_bytes=buckets;self.last_time=now
                self.reserved=max(0,self.reserved-reserved_before_record);self._records=records;self._episode=None
                return size
            except BaseException as exc:
                self.refusal_count+=1;self._snapshot_credit=0;self._snapshot_credit_at=None
                try:
                    if self._partial:
                        self._retain(self._partial);self.observed_bytes,self.observed_log_bytes,_=self._totals(self._partial)
                    self._record('refused',exception_type=type(exc).__name__,reason=str(exc)[:512],errno=getattr(exc,'errno',None),read_context=self.read_context)
                except BaseException as diagnostic_exc:self.diagnostic_write_error=type(diagnostic_exc).__name__+': '+str(diagnostic_exc)[:512]
                raise
    def admit(self,growth,reserve=64*MIB):
        with self.lock:
            self._require(not self.in_terminal,'Ordinary admission forbidden in terminal mode')
            self._require(type(growth) is int and growth>=0 and type(reserve) is int and reserve>=0,'Invalid disk growth reservation')
            if self.last_time is None or time.monotonic()-self.last_time>=5:self.census()
            self._require(self.last_time is not None and time.monotonic()-self.last_time<FRESH_SECONDS,'Disk census stale')
            effective_reserve=max(reserve,DIAGNOSTIC_CAP if self._diag is not None else 0,64*MIB if self.final_snapshot_reserve else 0)
            self._require(self.last_bytes+self.reserved+growth+effective_reserve+self._snapshot_pending()<=self.plan.disk_bytes,'Disk admission ceiling')
            self._free(growth);self.reserved+=growth
    def begin_terminal(self):
        with self.lock:
            self._require(not self.in_terminal,'Terminal mode is one-way and one-shot')
            self.in_terminal=True;self._terminal_needs_census=True
    def _terminal_debit(self,growth):
        self._require(type(growth) is int and growth>=0,'Invalid terminal growth')
        self._require(self.terminal_used+growth<=64*MIB,'Terminal evidence spend cap')
        self.terminal_used+=growth
    def _terminal_capacity(self,growth):
        self._require(self.in_terminal,'Terminal admission requires terminal mode')
        self._require(type(growth) is int and growth>=0,'Invalid terminal growth')
        if self._terminal_needs_census or self.last_time is None or time.monotonic()-self.last_time>=5:
            self.census();self._terminal_needs_census=False
        self._require(self.last_time is not None and time.monotonic()-self.last_time<FRESH_SECONDS,'Terminal disk census stale')
        self._require(self.last_bytes+self.reserved+self._snapshot_pending()+growth<=self.plan.disk_bytes,'Terminal disk ceiling')
        self._free(growth)
    def admit_terminal_evidence(self,growth):
        with self.lock:
            self._terminal_capacity(growth)
            self._terminal_debit(growth);self.reserved+=growth
    def admit_evidence(self,growth):
        if self.in_terminal:return self.admit_terminal_evidence(growth)
        return self.admit(growth)
    def _snapshot_pending(self):
        if self._snapshot_state in ('disabled','completed'):return 0
        if self._snapshot_state=='held':return self.final_snapshot_reserve
        credit=self._snapshot_credit if (self._snapshot_credit_at is not None and
            time.monotonic()-self._snapshot_credit_at<5) else 0
        return self.final_snapshot_reserve-credit
    def _snapshot_observe(self,records,clean,now):
        if self._snapshot_state not in ('admitted','failed'):return
        self._snapshot_credit=0;self._snapshot_credit_at=None
        if not clean:return
        relative=self._snapshot_target;name=Path(relative).name
        matches=[(key,value) for key,value in records.items() if key[0]==relative]
        if not matches:
            self._require(self._snapshot_identity is None,'Observed final snapshot disappeared')
            return
        self._require(len(matches)==1,'Final snapshot identity ambiguous')
        key,value=matches[0]
        info=os.stat(name,dir_fd=self._snapshot_parent,follow_symlinks=False)
        identity=self._identity(info)
        self._require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_dev==self.device,
                      'Final snapshot type/link/device drift')
        self._require(tuple(key[1:])==identity,'Final snapshot changed during accounting')
        self._require(self._snapshot_identity in (None,identity),'Final snapshot identity replaced')
        self._require(info.st_size<=self.final_snapshot_reserve,'Final snapshot main-file cap')
        self._snapshot_identity=identity
        # Credit only bytes already present in this complete aggregate census.
        self._snapshot_credit=min(value[0],max(info.st_size,info.st_blocks*512),self.final_snapshot_reserve)
        self._snapshot_credit_at=now
    def admit_final_snapshot(self,path):
        with self.lock:
            self._require(self.in_terminal,'Final snapshot requires terminal mode')
            self._require(self._snapshot_state=='held' and self.final_snapshot_reserve==512*MIB,
                          'Final snapshot reservation conversion is one-shot')
            path=Path(path).absolute()
            self._require(path.name=='final.sqlite3' and path.parent.parent==self.root and
                          path.parent.name.startswith('output'),'Final snapshot target confinement')
            self._root_ok()
            parents=[fd for fd,parent,name,identity in self._anchors
                     if parent==self.root_fd and name==path.parent.name]
            self._require(len(parents)==1,'Final snapshot parent must be the held output directory')
            parent=parents[0]
            try:os.stat(path.name,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:pass
            else:raise self.stop_type('Final snapshot target must be absent')
            # Validate existing protection once; do not add a second512MiB charge.
            self._terminal_capacity(0)
            self._snapshot_target=str(path.relative_to(self.root));self._snapshot_parent=parent
            self._snapshot_state='admitted';self._snapshot_credit=0;self._snapshot_credit_at=None
    def complete_final_snapshot(self,positive_wait=False):
        with self.lock:
            self._require(self._snapshot_state=='admitted' and positive_wait is True,
                          'Final snapshot requires positive helper completion')
            self.census()
            self._require(self._episode is None and self._snapshot_identity is not None and
                          self._snapshot_credit>0,'Final snapshot requires fresh complete file accounting')
            self._snapshot_state='completed'
    def fail_final_snapshot(self,reason):
        with self.lock:
            if self._snapshot_state=='admitted':self._snapshot_state='failed'
            self._snapshot_failure=str(reason)[:512]
    def summary(self):
        return dict(terminal_mode=self.in_terminal,terminal_spent=self.terminal_used,terminal_cap=64*MIB,final_snapshot_reserve=self.final_snapshot_reserve,final_snapshot_state=self._snapshot_state,
                    final_snapshot_pending=self._snapshot_pending(),final_snapshot_accounted=self._snapshot_credit,
                    final_snapshot_target=self._snapshot_target,final_snapshot_failure=self._snapshot_failure,
                    censuses=self.census_count,gaps=self.gap_count,refusals=self.refusal_count,recoveries=self.recovery_count,
                    last_clean=self.last_time,last_bytes=self.last_bytes,reserved=self.reserved,peak=self.peak,
                    log_bytes=self.log_bytes,observed_bytes=self.observed_bytes,observed_log_bytes=self.observed_log_bytes,retained_charge=self.last_bytes+self.reserved,diagnostic_write_error=self.diagnostic_write_error,diagnostic_bytes=self._diag_bytes,diagnostic_records=self._diag_records,last_diagnostic=self.last_diagnostic)
    def close(self):
        if self._diag is not None:
            os.close(self._diag[0]);self._diag=None
        for fd,*_ in reversed(self._anchors):os.close(fd)
        self._anchors=[]
