# PUBLIC INSPECTION COPY. Do not execute or import.
"""Conservative visible-PID-namespace sampler; role attribution is not admission.

Source-only protocol revision. Original Popen ownership/stop code is inherited.
RSS is sampled, not an atomic namespace total or historical peak guarantee.
"""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import copy
import hashlib
import json
import os
from pathlib import Path
import resource
import time

MIB=1024**2
MAX_SCAN=4096
MAX_VISIBLE=128
MAX_ANCESTORS=32
MAX_RECORDS=131072
MAX_RECORD_BYTES=16384
FRESH_SECONDS=3.

class RssUnavailable(RuntimeError):
    def __init__(self,pid,cause):
        super().__init__(str(cause));self.pid=pid;self.cause=cause


def namespace_family(base, sink, limits):
    class NamespaceFamily(base):
        namespace_accounting=True
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.namespace_pin=self._namespace_pin()
            self.namespace_records=0
            self.namespace_active={}
            self.namespace_unreadable=None
            self.namespace_gap_count=0
            self.namespace_attribution_complete=False
            self.namespace_roles=set()  # Attribution not attempted; no cleanup authority comes from this map.
            self.namespace_bootstrap={}
            self.namespace_page_size=os.sysconf('SC_PAGE_SIZE')
            self._ns_require(type(self.namespace_page_size) is int and 0<self.namespace_page_size<=1024**2,'Invalid page size')
            self._capture_bootstrap()
            self._ns_record('protocol',namespace=self.namespace_pin,bootstrap=list(self.namespace_bootstrap.values()),
                memory_scope='All visible numeric PIDs; no comparator discount',
                limitation='Sampled RSS; exit gaps cannot establish historical peaks')
        def _ns_require(self,ok,reason):
            if not ok:raise RuntimeError(reason)
        def _namespace_pin(self):
            st=os.stat('/proc/self/ns/pid')
            return dict(device=st.st_dev,inode=st.st_ino,target=os.readlink('/proc/self/ns/pid'),
                boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        def _verify_namespace(self):
            self._ns_require(self._namespace_pin()==self.namespace_pin,'PID namespace or boot changed')
        def _ns_record(self,stage,**values):
            self._ns_require(self.namespace_records<MAX_RECORDS,'Namespace diagnostic record cap')
            row=dict(kind='namespace-sample-diagnostic',stage=stage,at=time.monotonic(),**values)
            raw=json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
            self._ns_require(len(raw)<=MAX_RECORD_BYTES,'Namespace diagnostic record byte cap')
            sink(json.loads(raw));self.namespace_records+=1
        def _read_bytes(self,path,cap):
            with open(path,'rb') as stream:raw=stream.read(cap+1)
            self._ns_require(len(raw)<=cap,'Namespace proc field cap')
            return raw
        def _raw_identity(self,pid):
            first=self._stat(pid)
            raw=self._read_bytes('/proc/'+str(pid)+'/status',65536)
            fields=[line.split() for line in raw.splitlines() if line.startswith(b'Uid:')]
            self._ns_require(len(fields)==1 and len(fields[0])==5,'Namespace UID parse')
            first['uid']=int(fields[0][2])
            last=self._stat(pid)
            self._ns_require((last['pid'],last['ticks'])==(first['pid'],first['ticks']),'PID birth changed within identity bracket')
            last['uid']=first['uid']
            return last
        def _same(self,a,b):
            self._ns_require(all(a[k]==b[k] for k in ('pid','ticks','uid')),'PID identity changed within RSS bracket')
        def _positive_absent(self,pid):
            # Exact PID disappearance only, never a missing ancestor or argv.
            try:self._stat(pid)
            except FileNotFoundError:
                if self.psutil.pid_exists(pid):return False
                try:self._stat(pid)
                except FileNotFoundError:return True
            return False
        def _fingerprint(self,pid):
            exe=os.readlink('/proc/'+str(pid)+'/exe')
            raw=self._read_bytes('/proc/'+str(pid)+'/cmdline',65536)
            self._ns_require(bool(exe) and bool(raw),'Bootstrap executable/argv unavailable')
            self._ns_require(len(os.fsencode(exe))<=8192,'Bootstrap executable path cap')
            return dict(exe=exe,argv_sha256=hashlib.sha256(raw).hexdigest())
        def _capture_bootstrap(self):
            pid=self.wrapper['pid'];seen=set()  # Bound timeout starts before taskset/nice; charge RSS but exempt its CPU metadata.
            while pid:
                self._ns_require(pid not in seen and len(seen)<MAX_ANCESTORS,'Bootstrap ancestry bound')
                seen.add(pid);row=self._raw_identity(pid);fingerprint=self._fingerprint(pid);again=self._raw_identity(pid)
                self._ns_require(all(row[k]==again[k] for k in self.STABLE),'Bootstrap identity changed during capture')
                self.namespace_bootstrap[pid]=dict(kernel=again,**fingerprint)
                pid=again['parent']
        def _verify_bootstrap(self,row):
            pin=self.namespace_bootstrap.get(row['pid'])
            if pin is None:return False
            self._ns_require(all(row[k]==pin['kernel'][k] for k in self.STABLE),'Bootstrap identity changed')
            self._ns_require(self._fingerprint(row['pid'])=={k:pin[k] for k in ('exe','argv_sha256')},'Bootstrap executable/argv changed')
            return True
        def _enumerate(self):
            pids=[];scanned=0
            with os.scandir('/proc') as entries:
                for entry in entries:
                    scanned+=1;self._ns_require(scanned<=MAX_SCAN,'Namespace enumeration cap')
                    if entry.name.isdigit():
                        pid=int(entry.name);self._ns_require(pid>0,'Invalid namespace PID');pids.append(pid)
                        self._ns_require(len(pids)<=MAX_VISIBLE,'Namespace visible process cap')
            self._ns_require(len(pids)==len(set(pids)),'Duplicate namespace PID')
            return sorted(pids)
        def _rss(self,pid):
            fields=self._read_bytes('/proc/'+str(pid)+'/statm',4096).split()
            self._ns_require(2<=len(fields)<=16 and fields[1].isdigit(),'Namespace statm RSS parse')
            pages=int(fields[1]);self._ns_require(0<=pages<=((1<<63)-1)//self.namespace_page_size,'Namespace RSS overflow')
            return pages*self.namespace_page_size
        def _envelope(self,pid,row):
            exempt=self._verify_bootstrap(row)
            if not exempt:
                q=self.psutil.Process(pid);affinity=q.cpu_affinity();nice=q.nice()
                self._ns_require(bool(affinity) and set(affinity)<=self._expected_cpus() and nice==19,'Visible workload CPU/nice envelope')
            return exempt
        def _measure(self,pid):
            first=self._raw_identity(pid)
            self.namespace_read_context=dict(pid=pid,stage='rss',kernel=dict(first),readable_rss=0,readable_cpu=0.)
            try:rss=self._rss(pid)
            except (FileNotFoundError,PermissionError) as exc:raise RssUnavailable(pid,exc) from exc
            self.namespace_read_context.update(stage='envelope',readable_rss=rss)
            q=self.psutil.Process(pid)
            exempt=self._envelope(pid,first)
            times=q.cpu_times();cpu=sum(getattr(times,k,0.) for k in ('user','system','children_user','children_system'))
            self._ns_require(cpu>=0,'Namespace negative CPU')
            self.namespace_read_context.update(stage='identity_recheck',readable_cpu=cpu)
            last=self._raw_identity(pid);self._same(first,last)
            return dict(kernel=last,rss=rss,cpu=cpu,bootstrap=exempt)
        def _charge(self,rss):
            self._ns_require(rss>=0,'Negative namespace RSS')
            # Same fixed reserve as the inherited sampler, with zero discounts.
            return rss+512*MIB+768*MIB+192*MIB
        def _gap(self,pid,old=None,stage='selected_exit',partial_rss=0):
            self._ns_require(self._positive_absent(pid),'Selected PID absence unconfirmed/reused')
            self._ns_require(type(partial_rss) is int and partial_rss>=0,'Invalid partial gap RSS')
            retained=max(partial_rss,0 if old is None else old.get('max_rss',old['rss']))
            context=copy.deepcopy(getattr(self,'namespace_read_context',{}))
            if stage!='selected_exit' or context.get('pid')!=pid:
                context=dict(pid=pid,stage=stage,readable_rss=0,readable_cpu=0.)
            self.namespace_gap_count+=1;self.namespace_attribution_complete=False
            self._ns_record('exit-gap',pid=pid,birth=None if old is None else old['kernel']['ticks'],
                last_rss=None if old is None else old['rss'],readable_gap_rss=retained,gap_stage=stage,positive_numeric_absence=True,
                role='unclassified',historical_peak_known=False,cleanup_authority=False,
                read_context=context)
            return retained
        def _retain_unreadable(self,pid,old,now,error):
            self._ns_require(old is not None,'New live PID RSS unreadable; no conservative prior measurement')
            self._ns_require(self.last_complete is not None,'Unreadable live PID before complete census')
            # A readable kernel identity is mandatory even during RSS tolerance.
            fresh=self._raw_identity(pid);self._same(old['kernel'],fresh)
            self._envelope(pid,fresh)  # Memory tolerance never exempts live CPU/nice validation.
            if self.namespace_unreadable is None:
                self.namespace_unreadable=dict(at=now,deadline=self.last_complete+FRESH_SECONDS,prior_charge=self.last_charge,members={})
            episode=self.namespace_unreadable
            self._ns_require(now<=episode['deadline'],'Unreadable namespace census freshness expired')
            key=(pid,fresh['ticks']);record=episode['members'].get(key)
            maximum=max(old['rss'],old.get('max_rss',old['rss']),0 if record is None else record['rss'])
            episode['members'][key]=dict(kernel=fresh,rss=maximum)
            self._ns_require(len(episode['members'])<=MAX_VISIBLE,'Unreadable identity cap')
            self._ns_record('live-unreadable-retained',pid=pid,birth=fresh['ticks'],rss=maximum,
                deadline=episode['deadline'],exception_type=type(error).__name__,reason=str(error))
            return dict(kernel=fresh,rss=maximum,max_rss=maximum,cpu=old.get('cpu',0),bootstrap=old.get('bootstrap',False),unreadable=True)
        def sample(self):
            try:return self._namespace_sample()
            except BaseException as exc:
                self._ns_record('census-refused',exception_type=type(exc).__name__,reason=str(exc),
                    last_complete=self.last_complete,last_charge=self.last_charge,pending_charge=self.pending_charge,
                    context=copy.deepcopy(getattr(self,'namespace_read_context',{})))
                raise
        def _namespace_sample(self):
            with self.lock:
                now=time.monotonic();self._verify_namespace()
                self.max_gap=max(self.max_gap,now-(self.samples[-1]['at'] if self.samples else now))
                self.sample_recheck_used=False
                if self.namespace_unreadable is not None:
                    self._ns_require(now<=self.namespace_unreadable['deadline'],'Unreadable namespace census freshness expired')
                # Original owner identity and handle provenance remain strict.
                for name,(p,q,argv,*_) in list(self.originals.items()):
                    if p.poll() is not None:self._original_exit(name)
                    else:self._original(name)
                selected=self._enumerate();previous=self.namespace_active;current={};gaps=0;gap_rss=0;gap_cpu=0.;unreadable=False
                # Missing retained identity must be positively absent; no reuse resolution.
                for pid,old in previous.items():
                    if pid not in selected:
                        gap_rss+=self._gap(pid,old,'absent_from_enumeration');gap_cpu+=old.get('cpu',0.);gaps+=1
                for pid in selected:
                    old=previous.get(pid)
                    self.namespace_read_context=dict(pid=pid,stage='measure',previous_birth=None if old is None else old['kernel']['ticks'],readable_rss=0,readable_cpu=0.)
                    try:
                        item=self._measure(pid)
                        if old is not None:self._same(old['kernel'],item['kernel'])
                        item['max_rss']=max(item['rss'],old.get('max_rss',old['rss']) if old is not None else 0)
                        current[pid]=item
                    except RssUnavailable as exc:
                        self._ns_require(exc.pid==pid,'Foreign RSS unavailable identity')
                        if self._positive_absent(pid):
                            gap_rss+=self._gap(pid,old,partial_rss=self.namespace_read_context.get('readable_rss',0));gap_cpu+=self.namespace_read_context.get('readable_cpu',0.);gaps+=1
                        else:
                            current[pid]=self._retain_unreadable(pid,old,time.monotonic(),exc);unreadable=True
                    except (FileNotFoundError,self.psutil.NoSuchProcess,ProcessLookupError) as exc:
                        if hasattr(exc,'pid'):self._ns_require(exc.pid==pid,'Foreign selected identity disappearance')
                        gap_rss+=self._gap(pid,old,partial_rss=self.namespace_read_context.get('readable_rss',0));gap_cpu+=self.namespace_read_context.get('readable_cpu',0.);gaps+=1
                # All captured infrastructure must remain the same process; not exempt from RSS.
                self._ns_require(set(self.namespace_bootstrap)<=set(current),'Bootstrap ancestor vanished/unreadable')
                self._verify_namespace();finished=time.monotonic()
                rss=16*MIB+gap_rss+sum(x['rss'] for x in current.values())
                cpu=gap_cpu+sum(x['cpu'] for x in current.values())
                charge=self._charge(rss)
                episode=self.namespace_unreadable
                if episode is not None:
                    self._ns_require(finished<=episode['deadline'],'Unreadable namespace census freshness expired')
                    retained=sum(x['rss'] for x in episode['members'].values())
                    charge=max(charge,episode['prior_charge']+retained,self.pending_charge)
                if gaps:charge=max(charge,self.last_charge)
                self.pending_charge=charge if unreadable else 0
                self.peak_rss=max(self.peak_rss,rss);self.peak_charge=max(self.peak_charge,charge)
                self._ns_require(charge<limits['charged_memory'],'Namespace sampled charged memory ceiling')
                if unreadable:
                    self.incomplete=dict(at=episode['at'],deadline=episode['deadline'],reason='namespace_read_unavailable',identities=len(episode['members']))
                else:
                    self.incomplete=None;self.namespace_unreadable=None
                    self.last_complete=finished;self.last_charge=charge
                    if self.shutting_down and not self.pre_stop_sampling:self.final_sample_complete=True
                self.namespace_active=current
                self.samples.append(dict(at=finished,rss=rss,comparator_rss=0,charged=charge,cpu=cpu,
                    complete=not unreadable,coverage='sampled_with_exit_gaps' if gaps else 'sampled_visible_namespace',
                    exit_gaps=gaps,visible=len(selected),unreadable_live=unreadable,historical_peak_guaranteed=False))
                if gaps or unreadable:self._ns_record('census-coverage',visible=len(selected),exit_gaps=gaps,
                    unreadable_live=unreadable,rss=rss,charged=charge,deadline=None if episode is None else episode['deadline'])
                if self.live_phase:
                    self.deadlines.live_check()
                    if self.budget.last_time is None or finished-self.budget.last_time>=5:
                        value=self.budget.census()
                        if value is not None:self._ns_require(value<limits['stop_output'],'Sampled live output stop')
                    self._ns_require(self.budget.last_time is not None and finished-self.budget.last_time<15,'Allocation census stale')
        def _retained_resolved(self):
            self._ns_require(self.incomplete is None and self.namespace_unreadable is None,'Unreadable namespace final cleanup')
            self._ns_require(not self.shutting_down or self.final_sample_complete,'Fresh namespace final census missing')
            # Original waits, never namespace membership, establish signal ownership.
            for name in self.originals:self._original_exit(name)
            return True
        def _final_inventory(self):
            self._verify_namespace()
            allowed={self.helper['pid']:self.helper,self.wrapper['pid']:self.wrapper}
            allowed.update({pid:pin['kernel'] for pid,pin in self.namespace_bootstrap.items()})
            selected=self._enumerate();seen=set()
            for pid in selected:
                try:row=self._raw_identity(pid)
                except FileNotFoundError:
                    self._gap(pid,self.namespace_active.get(pid),'final_inventory_exit');continue
                self._ns_require(pid in allowed,'Unowned visible process survives final cleanup')
                expected=allowed[pid]
                self._ns_require(all(row[k]==expected[k] for k in self.STABLE),'Final infrastructure identity changed')
                if pid in self.namespace_bootstrap:self._verify_bootstrap(row)
                seen.add(pid)
            self._ns_require(seen==set(allowed),'Final pinned infrastructure missing')
            return True
    return NamespaceFamily
