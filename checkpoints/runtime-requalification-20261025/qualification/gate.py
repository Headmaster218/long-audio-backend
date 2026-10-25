"""SOURCE-ONLY independent finite physical gate. No profile activation.

Only root may execute after independent review. No models/user audio; only a
new synthetic namespace. A failed/inconclusive capture is never retried beyond
the two explicitly bounded distinct attempts. Original evidence is not resumed.
"""
import argparse,base64,hashlib,json,os,resource,shutil,signal,sqlite3,stat,struct,subprocess,sys,time
from pathlib import Path

PAGE=4096;PAGES=6144;DB_CAP=24*1024**2;JOURNAL_CAP=32*1024**2
SLACK_CAP=2*1024**2;ENVELOPE=58*1024**2;ARTIFACT_CAP=192*1024**2
RSS_CAP=256*1024**2;AS_CAP=1024**3;PHASE_SECONDS=120
MAGIC=bytes.fromhex('d9d505f920a163d7')
HERE=Path(__file__).resolve().parent
CHARGE_DISAPPEARANCES=0

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while block:=f.read(65536):h.update(block)
    return h.hexdigest()
def save(path,value):
    data=canonical(value)+b'\n'
    if len(data)>1024**2:raise RuntimeError('receipt bound')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
def read(path):return json.loads(Path(path).read_bytes())
def limits():
    resource.setrlimit(resource.RLIMIT_AS,(AS_CAP,AS_CAP))
    cpus=sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0,cpus[:2])
    os.nice(19)
def private(path):
    if path.is_symlink() or path.exists():raise RuntimeError('fresh output required')
    if path.parent.resolve()!=path.parent:raise RuntimeError('canonical parent required')
    if path.parent.stat().st_uid!=os.getuid():raise RuntimeError('owned output parent required')
    path.mkdir(mode=0o700)
def load_source(pins,manifest):
    source=Path(pins['source'])
    if source.resolve()!=source:raise RuntimeError('source path alias')
    expected={}
    until=time.monotonic()+120
    if manifest.stat().st_size>8*1024**2:raise RuntimeError("source manifest bound")
    for line in manifest.read_text().splitlines():
        digest,name=line[:64],line[66:]
        if len(digest)!=64 or not name.startswith('./'):raise RuntimeError('manifest form')
        rel=Path(name[2:])
        if rel.is_absolute() or '..' in rel.parts or str(rel) in expected:raise RuntimeError('manifest path')
        expected[str(rel)]=digest
        if len(expected)>10000:raise RuntimeError("source inventory count bound")
    actual=set()
    for root,dirs,files in os.walk(source,followlinks=False):
        if '__pycache__' in dirs and any((Path(root)/'__pycache__').iterdir()):raise RuntimeError('unpinned product bytecode')
        dirs[:]=[d for d in dirs if d not in ('.git','__pycache__')]
        for d in dirs:
            if (Path(root)/d).is_symlink():raise RuntimeError('source directory alias')
        for name in files:
            if name.endswith(('.pyc','.pyo')):raise RuntimeError('unpinned product bytecode')
            path=Path(root)/name;rel=str(path.relative_to(source))
            if path.is_symlink() or not path.is_file():raise RuntimeError('source file alias')
            if time.monotonic()>=until or path.stat().st_size>16*1024**2:raise RuntimeError("source preflight bound")
            actual.add(rel)
            if expected.get(rel)!=sha(path):raise RuntimeError('source fingerprint mismatch')
    if actual!=set(expected):raise RuntimeError('source inventory mismatch')
    sys.path.insert(0,str(source))
    from audio_backend import native_owned_cell_store as store
    if Path(store.__file__).resolve().parent.parent!=source:raise RuntimeError('wrong source module')
    if (store.PAGE_SIZE,store.MAX_PAGE_COUNT,store.DATABASE_BYTES,store.JOURNAL_BYTES,
        store.ANCILLARY_BYTES,store.RESERVED_ENVELOPE_BYTES)!=(PAGE,PAGES,DB_CAP,JOURNAL_CAP,SLACK_CAP,ENVELOPE):
        raise RuntimeError('storage bounds changed')
    return store

def identity(pins,store,directory):
    executable=Path(sys.executable).resolve()
    if str(executable)!=pins['python'] or executable.stat().st_size!=pins['python_bytes'] or sha(executable)!=pins['python_sha256']:
        raise RuntimeError('interpreter mismatch')
    if directory.stat().st_dev!=pins['filesystem_device']:raise RuntimeError('filesystem mismatch')
    # Existing fingerprint verifier, unchanged default budget. Never constructs
    # or installs a RuntimeQualification for the newly observed closure.
    fingerprints=store.runtime_fingerprints(store.Budget.for_owner())
    if fingerprints['sqlite3_package_sha256']!=pins['sqlite3_package_sha256']:raise RuntimeError('package mismatch')
    db=sqlite3.connect(':memory:')
    try:
        source=db.execute('SELECT sqlite_source_id()').fetchone()[0]
        options=sorted(r[0] for r in db.execute('PRAGMA compile_options'))
    finally:db.close()
    if source!=pins['sqlite_source_id'] or sqlite3.sqlite_version!=pins['sqlite_version'] or hashlib.sha256(canonical(options)).hexdigest()!=pins['compile_options_sha256']:
        raise RuntimeError('SQLite build mismatch')
    package=Path(sqlite3.__file__).resolve().parent
    inventory={}
    for folder,pattern in ((package,'*.py'),(package/'__pycache__','*.pyc')):
        if folder.exists():
            for path in sorted(folder.glob(pattern)):
                inventory[str(path.relative_to(package))]={'bytes':path.stat().st_size,'sha256':sha(path)}
    if hashlib.sha256(canonical(inventory)).hexdigest()!=fingerprints['sqlite3_package_sha256']:raise RuntimeError('package changed while observing')
    return {'fingerprints':fingerprints,'package_inventory':inventory,'sqlite_source_id':source,'sqlite_version':sqlite3.sqlite_version,
        'compile_options_sha256':hashlib.sha256(canonical(options)).hexdigest(),'filesystem_device':directory.stat().st_dev,
        'source_module_sha256':sha(Path(store.__file__)),'production_profile_activated':False}

def connect(path,store,*,fresh=False):
    db=sqlite3.connect(path.as_uri()+('?mode=rwc&vfs=unix' if fresh else '?mode=rw&vfs=unix'),uri=True,timeout=0,isolation_level=None)
    if db.execute('PRAGMA journal_mode').fetchone()[0].lower()!='delete':raise RuntimeError('journal mode')
    if fresh:db.execute('PRAGMA page_size=4096')
    expected={'page_size':PAGE,'max_page_count':PAGES,'synchronous':2,'temp_store':2,
        'busy_timeout':0,'cache_spill':0,'mmap_size':0,'foreign_keys':1,'trusted_schema':0,'auto_vacuum':0,'cache_size':-4096}
    for sql in ('PRAGMA max_page_count=6144','PRAGMA synchronous=FULL','PRAGMA temp_store=MEMORY','PRAGMA busy_timeout=0',
        'PRAGMA cache_spill=OFF','PRAGMA cache_size=-4096','PRAGMA mmap_size=0','PRAGMA foreign_keys=ON','PRAGMA trusted_schema=OFF'):
        db.execute(sql)
    for key,value in expected.items():
        if db.execute('PRAGMA '+key).fetchone()[0]!=value:raise RuntimeError('PRAGMA readback')
    db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,store.HANDOFF_BYTES)
    db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH,store.SQL_PROJECTION_BYTES)
    db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED,0)
    if (db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)!=store.HANDOFF_BYTES or
        db.getlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH)!=store.SQL_PROJECTION_BYTES or
        db.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED)!=0):raise RuntimeError("SQLite limit readback")
    return db

def oracle(db):
    tables=('sqlite_master','store_meta','sources','cells','dependencies','sqlite_sequence');result={}
    def typed(value):
        if value is None:return ['null']
        if type(value) is bytes:return ['blob',base64.b64encode(value).decode('ascii')]
        if type(value) is int:return ['integer',str(value)]
        if type(value) is float:return ['real',value.hex()]
        if type(value) is str:return ['text',value]
        raise RuntimeError('unexpected SQL type')
    for table in tables:
        h=hashlib.sha256();count=0
        cursor=db.execute('SELECT * FROM '+table+' ORDER BY rowid')
        columns=[item[0] for item in cursor.description]
        for row in cursor:
            encoded=canonical([typed(v) for v in row]);h.update(struct.pack('>I',len(encoded)));h.update(encoded);count+=1
        result[table]={'columns':columns,'rows':count,'typed_rows_sha256':h.hexdigest()}
    return result

def fill(directory,store,deadline):
    (directory/"namespace").mkdir(mode=0o700)
    path=directory/'namespace'/'cells.sqlite3';db=connect(path,store,fresh=True)
    attempts=[];size=640000;full=False
    try:
        store.CellStore._schema(db)
        db.execute('INSERT INTO store_meta VALUES(1,?,?,?,?,?,?)',('synthetic-gate','synthetic-gate','synthetic-gate','synthetic-gate','synthetic-gate','synthetic-gate'))
        db.execute('BEGIN IMMEDIATE')
        for i in range(128):db.execute('INSERT INTO sources VALUES(?,?,?,?)',(f'c{i}','synthetic','0'*64,b'x'*1024))
        db.execute('COMMIT')
        for attempt in range(128):
            if time.monotonic()>=deadline:raise TimeoutError('fill deadline')
            try:
                db.execute('BEGIN IMMEDIATE')
                cid=f'cell-{attempt}'
                db.execute('INSERT INTO cells(cell_id,ownership,sid,cid,proof,pcm,state,revision) VALUES(?,?,?,?,?,?,?,?)',
                    (cid,cid,'synthetic','c0',b'{}',b'Z'*size,'retained',1))
                db.execute('INSERT INTO dependencies VALUES(?,0,?,?,?,?,?,?,?)',(cid,'c0','0'*64,'0'*64,0,16,0,16))
                db.execute('COMMIT');attempts.append({'pcm_bytes':size,'result':'COMMITTED'})
            except sqlite3.Error as exc:
                if db.in_transaction:db.execute('ROLLBACK')
                code=getattr(exc,'sqlite_errorcode',None)
                attempts.append({'pcm_bytes':size,'sqlite_code':code})
                if code!=sqlite3.SQLITE_FULL:raise
                full=True
                if db.execute('PRAGMA page_count').fetchone()[0]==PAGES:break
                size=max(1,size//2)
        pages=db.execute('PRAGMA page_count').fetchone()[0]
        if not full or pages!=PAGES:raise RuntimeError('finite fill did not establish full page ceiling')
        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('fill integrity')
        value={'page_count':pages,'sqlite_full_observed':full,'attempts':attempts,'oracle':oracle(db)}
    finally:db.close()
    value['database_sha256']=sha(path);save(directory/'fill.json',value);return value

def overwrite_existing_pcm(db,deadline):
    """Existing payload only: no record-size/index/revision/header growth.

    Real SQLite incremental BLOB API journals modified pages normally. This is
    a physical-storage synthetic operation, not a product owner/API mutation.
    """
    before=db.execute('PRAGMA page_count').fetchone()[0]
    rows=db.execute('SELECT seq,length(pcm) FROM cells ORDER BY seq').fetchall()
    if before!=PAGES or not 1<=len(rows)<=1024:raise RuntimeError('fixed-size workload shape')
    written=0
    for rowid,length in rows:
        if type(length) is not int or not 1<=length<=640000:raise RuntimeError('existing PCM extent')
        with db.blobopen('cells','pcm',rowid,readonly=False) as blob:
            if len(blob)!=length:raise RuntimeError('BLOB size changed')
            remaining=length
            while remaining:
                if time.monotonic()>=deadline:raise TimeoutError('BLOB overwrite deadline')
                count=min(65536,remaining);blob.write(b'\0'*count);written+=count;remaining-=count
            if len(blob)!=length:raise RuntimeError('BLOB grew')
    after=db.execute('PRAGMA page_count').fetchone()[0]
    if after!=before:raise RuntimeError('fixed-size update changed page count')
    return {'mutation_kind':'existing_pcm_incremental_blob','mutated_rows':len(rows),
        'payload_bytes_written':written,'page_count_before':before,'page_count_after':after}

def crash_child(directory,store,deadline):
    # Child owns its one SQLite connection. Parent owns signals and originalwait.
    db=connect(directory/'namespace'/'cells.sqlite3',store)
    try:
        db.execute('BEGIN IMMEDIATE')
        mutation=overwrite_existing_pcm(db,deadline)
        save(directory/'before-commit.json',{'stage':'before_real_COMMIT','pid':os.getpid(),**mutation})
        while not (directory/'commit-go').exists():
            if time.monotonic()>=deadline:raise TimeoutError('commit start deadline')
            time.sleep(.001)
        db.execute('COMMIT')
        save(directory/'commit-returned.json',{'stage':'COMMIT_returned'})
    finally:db.close()

def audit_journal(original,baseline,check=lambda:None):
    database=original/'namespace'/'cells.sqlite3';journal=original/'namespace'/'cells.sqlite3-journal'
    raw=journal.read_bytes();before=baseline.read_bytes();after=database.read_bytes()
    check()
    if len(raw)>JOURNAL_CAP or len(after)>DB_CAP or len(before)!=len(after):raise RuntimeError('artifact extent')
    if len(raw)<28 or raw[:8]!=MAGIC:raise RuntimeError('no valid hot journal magic')
    count,nonce,pages,sector,page=struct.unpack('>5I',raw[8:28])
    if not 1<=count<=PAGES or pages!=PAGES or page!=PAGE or sector<512 or sector>65536 or sector&(sector-1):raise RuntimeError('journal header bounds')
    end=sector+count*(PAGE+8)
    if end>len(raw) or len(raw)-end>=sector or any(raw[end:]):raise RuntimeError('not one bounded complete header segment')
    seen=set()
    for i in range(count):
        if i%64==0:check()
        offset=sector+i*(PAGE+8);number=struct.unpack('>I',raw[offset:offset+4])[0];body=raw[offset+4:offset+4+PAGE]
        if number in seen or not 1<=number<=PAGES:raise RuntimeError('journal page duplicate/bounds')
        seen.add(number)
        checksum=nonce
        for j in range(PAGE-200,0,-200):checksum=(checksum+body[j])&0xffffffff
        if checksum!=struct.unpack('>I',raw[offset+4+PAGE:offset+8+PAGE])[0]:raise RuntimeError('journal checksum')
        if body!=before[(number-1)*PAGE:number*PAGE]:raise RuntimeError('journal page is not baseline preimage')
    changed=[i+1 for i in range(PAGES) if before[i*PAGE:(i+1)*PAGE]!=after[i*PAGE:(i+1)*PAGE]]
    if not changed or not set(changed)<=seen:raise RuntimeError('no journal-covered interrupted database changes')
    return {'records':count,'changed_pages':len(changed),'headers':1,'sector_bytes':sector,'journal_bytes':len(raw),
        'database_sha256':sha(database),'journal_sha256':sha(journal)}

def recover(directory,original,baseline,expected,store):
    (directory/"namespace").mkdir(mode=0o700)
    shutil.copyfile(original/'namespace'/'cells.sqlite3',directory/'namespace'/'cells.sqlite3')
    shutil.copyfile(original/'namespace'/'cells.sqlite3-journal',directory/'namespace'/'cells.sqlite3-journal')
    before={'database':sha(original/'namespace'/'cells.sqlite3'),'journal':sha(original/'namespace'/'cells.sqlite3-journal')}
    db=connect(directory/'namespace'/'cells.sqlite3',store)
    try:
        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('recovery integrity')
        actual=oracle(db)
        if actual!=expected['oracle']:raise RuntimeError('logical oracle mismatch')
    finally:db.close()
    if (directory/'namespace'/'cells.sqlite3-journal').exists():raise RuntimeError('recovery journal retained')
    if sha(directory/'namespace'/'cells.sqlite3')!=expected['database_sha256']:raise RuntimeError('recovery byte mismatch')
    after={'database':sha(original/'namespace'/'cells.sqlite3'),'journal':sha(original/'namespace'/'cells.sqlite3-journal')}
    if before!=after:raise RuntimeError('original pair changed')
    return {'byte_equal':True,'logical_oracle_equal':True,'integrity':'ok','foreign_keys':'ok','original_pair_unchanged':True}

def charged_tree(root):
    global CHARGE_DISAPPEARANCES
    total=0
    for directory,dirs,files in os.walk(root,followlinks=False):
        for path in [Path(directory)]+[Path(directory)/name for name in files]:
            try:info=path.lstat()
            except FileNotFoundError:
                if path.name!='cells.sqlite3-journal' or path.parent.name!='namespace':raise
                CHARGE_DISAPPEARANCES+=1
                continue
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise RuntimeError('unexpected artifact type')
            total+=max(info.st_size,info.st_blocks*512)
        for name in dirs:
            if (Path(directory)/name).is_symlink():raise RuntimeError('artifact directory alias')
    return total

def namespace_charge(directory):
    names={'cells.sqlite3','cells.sqlite3-journal'};logical=0;charge=0
    if set(p.name for p in directory.iterdir())-names:raise RuntimeError('unexpected namespace entry')
    root=directory.stat();charge+=max(root.st_size,root.st_blocks*512)
    for name in names:
        path=directory/name
        if path.exists():
            try:info=path.lstat()
            except FileNotFoundError:continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.getuid() or info.st_dev!=root.st_dev:raise RuntimeError('unsafe namespace file')
            logical+=info.st_size;charge+=max(info.st_size,info.st_blocks*512)
            if info.st_size>(DB_CAP if name=='cells.sqlite3' else JOURNAL_CAP):raise RuntimeError('namespace file bound')
    if charge>ENVELOPE or charge-logical>SLACK_CAP:raise RuntimeError('namespace allocation envelope')
    return {'charged_bytes':charge,'slack_bytes':charge-logical}

def rss(pid):
    try:
        for line in Path('/proc/'+str(pid)+'/status').read_text().splitlines():
            if line.startswith('VmRSS:'):return int(line.split()[1])*1024
    except FileNotFoundError:return 0
    return 0

class Supervisor:
    def __init__(self,args,out):
        self.args=args;self.out=out;self.live=None;self.waits=[];self.peak_rss=0;self.peak_bytes=0
        self.peak_namespace_charge=0;self.peak_namespace_slack=0
    def check(self,deadline):
        if time.monotonic()>=deadline:raise TimeoutError('phase deadline')
        self.peak_rss=max(self.peak_rss,rss(os.getpid())+(rss(self.live.pid) if self.live else 0))
        self.peak_bytes=max(self.peak_bytes,charged_tree(self.out))
        for entry in self.out.iterdir():
            namespace=entry/"namespace"
            if entry.is_dir() and namespace.is_dir():
                sample=namespace_charge(namespace)
                self.peak_namespace_charge=max(self.peak_namespace_charge,sample["charged_bytes"])
                self.peak_namespace_slack=max(self.peak_namespace_slack,sample["slack_bytes"])
        if self.peak_rss>RSS_CAP:raise RuntimeError('aggregate sampled RSS cap')
        if self.peak_bytes>ARTIFACT_CAP-2*1024**2:raise RuntimeError('artifact cap')
        if os.statvfs(self.out).f_bavail*os.statvfs(self.out).f_frsize<256*1024**2:raise RuntimeError('free disk floor')
    def launch(self,phase,directory,deadline,extra=()):
        if self.live is not None:raise RuntimeError('one owned child only')
        command=[sys.executable,'-B',str(HERE/'gate.py'),'--child',phase,'--output',str(self.out),
            '--directory',str(directory),'--deadline',str(deadline),'--pins',str(self.args.pins),
            '--pins-sha256',self.args.pins_sha256,'--manifest',str(self.args.manifest),
            '--manifest-sha256',self.args.manifest_sha256,'--harness-sha256',self.args.harness_sha256,*extra]
        stream=(directory/'process.log').open('xb')
        previous=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGALRM,signal.SIGTERM,signal.SIGINT})
        try:
            self.live=subprocess.Popen(command,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True,
                preexec_fn=lambda:signal.pthread_sigmask(signal.SIG_SETMASK,previous))
        finally:
            stream.close()
            signal.pthread_sigmask(signal.SIG_SETMASK,previous)
        return self.live
    def wait(self,phase,deadline,*,expected=0):
        proc=self.live
        while proc.poll() is None:self.check(deadline);time.sleep(.01)
        self.check(deadline)
        code=proc.wait();self.waits.append({'phase':phase,'pid':proc.pid,'returncode':code,'original_wait':True});self.live=None
        if code!=expected:raise RuntimeError('owned child phase failed')
    def cleanup(self):
        if self.live is None:return
        proc=self.live
        if proc.poll() is None:
            try:os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        code=proc.wait(timeout=5)
        self.waits.append({'phase':'cleanup','pid':proc.pid,'returncode':code,'original_wait':True});self.live=None

def capture(supervisor,baseline,directory,deadline):
    (directory/"namespace").mkdir(mode=0o700)
    shutil.copyfile(baseline,directory/'namespace'/'cells.sqlite3')
    before=baseline.read_bytes()[:256*PAGE]
    proc=supervisor.launch('crash',directory,deadline)
    while not (directory/'before-commit.json').exists():
        supervisor.check(deadline)
        if proc.poll() is not None:supervisor.wait('crash-precommit',deadline);raise RuntimeError('child exited before commit')
        time.sleep(.002)
    save(directory/'commit-go',{'go':True})
    stopped=False
    while proc.poll() is None:
        supervisor.check(deadline)
        journal=directory/'namespace'/'cells.sqlite3-journal'
        try:
            with journal.open('rb') as f:header=f.read(28)
            valid=(len(header)==28 and header[:8]==MAGIC and 0<struct.unpack('>I',header[8:12])[0]<=PAGES)
            if valid:
                with (directory/'namespace'/'cells.sqlite3').open('rb') as f:changed=f.read(len(before))!=before
                if changed:
                    os.kill(proc.pid,signal.SIGSTOP)
                    stop_deadline=min(deadline,time.monotonic()+2)
                    while time.monotonic()<stop_deadline:
                        # WNOWAIT acknowledges STOP without consuming exit status;
                        # the original Popen.wait below owns the terminal reap.
                        observed=os.waitid(os.P_PID,proc.pid,os.WSTOPPED|os.WNOHANG|os.WNOWAIT)
                        if observed is not None and observed.si_code==os.CLD_STOPPED:
                            stopped=True;break
                        supervisor.check(deadline);time.sleep(.001)
                    break
        except FileNotFoundError:pass
        time.sleep(.001)
    if not stopped:
        if proc.poll() is None:os.killpg(proc.pid,signal.SIGKILL)
        expected=proc.wait(timeout=5)
        supervisor.waits.append({'phase':'crash-missed','pid':proc.pid,'returncode':expected,'original_wait':True,'stop_acknowledged':False})
        supervisor.live=None
        return {'captured':False,'reason':'commit_window_not_observed','returncode':expected}
    os.killpg(proc.pid,signal.SIGKILL)
    supervisor.wait('crash-captured',deadline,expected=-signal.SIGKILL)
    audit=audit_journal(directory,baseline,lambda:supervisor.check(deadline))
    return {'captured':True,'stop_acknowledged':True,'kill_signal':signal.SIGKILL,'audit':audit,'allocation':namespace_charge(directory/"namespace")}

def parse():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--pins',type=Path,required=True)
    p.add_argument('--pins-sha256',required=True);p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--manifest-sha256',required=True);p.add_argument('--harness-sha256',required=True)
    p.add_argument('--child',choices=('identity','fill','crash','recover'))
    p.add_argument('--directory',type=Path);p.add_argument('--deadline',type=float)
    p.add_argument('--original',type=Path)
    return p.parse_args()

def main():
    setup_started=time.monotonic()
    args=parse();limits()
    if not sys.dont_write_bytecode:raise RuntimeError("root launcher must use -B")
    def phase_expired(signum,frame):raise TimeoutError("physical phase alarm")
    signal.signal(signal.SIGALRM,phase_expired)
    signal.setitimer(signal.ITIMER_REAL,max(.001,args.deadline-time.monotonic()) if args.child and args.deadline else PHASE_SECONDS)
    if sha(args.pins)!=args.pins_sha256 or sha(args.manifest)!=args.manifest_sha256 or sha(HERE/'gate.py')!=args.harness_sha256:
        raise RuntimeError('reviewed input pin mismatch')
    pins=read(args.pins)
    if args.output.resolve()!=args.output:raise RuntimeError('canonical output required')
    store=load_source(pins,args.manifest)
    if args.child:
        if args.directory is None or args.directory.parent!=args.output:raise RuntimeError('child namespace outside owned output')
        if args.deadline is None or not time.monotonic()<args.deadline<=time.monotonic()+PHASE_SECONDS:raise RuntimeError('child deadline')
        observed=identity(pins,store,args.directory)
        save(args.directory/'identity.json',observed)
        if args.child=='identity':return
        if args.child=='fill':fill(args.directory,store,args.deadline);return
        if args.child=='crash':crash_child(args.directory,store,args.deadline);return
        if args.child=='recover':
            if args.original is None or args.original.parent!=args.output or args.original.name not in ('crash-1','crash-2'):raise RuntimeError('recovery original namespace')
            result=recover(args.directory,args.original,args.output/'fill'/'namespace'/'cells.sqlite3',read(args.output/'fill'/'fill.json'),store)
            save(args.directory/'recover.json',result);return
    def interrupted(signum,frame):raise InterruptedError("supervisor interrupted")
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    private(args.output)
    supervisor=Supervisor(args,args.output);start=time.monotonic()
    terminal={'status':'INCONCLUSIVE','new_physical_evidence':True,'production_profile_activated':False,
        'synthetic_storage_not_acoustic_evidence':True,'complete_process_census':False}
    try:
        for phase in ('identity','fill'):
            directory=args.output/phase;directory.mkdir(mode=0o700)
            deadline=(setup_started+PHASE_SECONDS if phase=="identity" else time.monotonic()+PHASE_SECONDS)
            signal.setitimer(signal.ITIMER_REAL,max(.001,deadline-time.monotonic()))
            supervisor.launch(phase,directory,deadline);supervisor.wait(phase,deadline)
            if phase=='fill':terminal['fill_allocation']=namespace_charge(directory/"namespace")
        deadline=time.monotonic()+PHASE_SECONDS;captured=None;attempts=[]
        signal.setitimer(signal.ITIMER_REAL,PHASE_SECONDS)
        for number in (1,2):
            directory=args.output/f'crash-{number}';directory.mkdir(mode=0o700)
            result=capture(supervisor,args.output/'fill'/'namespace'/'cells.sqlite3',directory,deadline)
            attempts.append(result);save(directory/'capture.json',result)
            if result['captured']:captured=directory;break
        terminal['crash_attempts']=attempts
        if captured is None:raise RuntimeError('no qualified interrupted pair captured')
        directory=args.output/'recovery';directory.mkdir(mode=0o700);deadline=time.monotonic()+PHASE_SECONDS
        signal.setitimer(signal.ITIMER_REAL,PHASE_SECONDS)
        supervisor.launch('recover',directory,deadline,('--original',str(captured)))
        supervisor.wait('recover',deadline);terminal['recovery_allocation']=namespace_charge(directory/"namespace")
        load_source(pins,args.manifest)
        if sha(args.pins)!=args.pins_sha256 or sha(args.manifest)!=args.manifest_sha256 or sha(HERE/'gate.py')!=args.harness_sha256:raise RuntimeError('final input drift')
        final=identity(pins,store,args.output)
        if final!=read(args.output/'identity'/'identity.json'):raise RuntimeError('runtime/source identity changed')
        supervisor.check(deadline)
        terminal.update(status='PHYSICAL_GATE_COMPLETE_REVIEW_REQUIRED',captured_attempt=captured.name,
            identity=final,source_manifest_sha256=args.manifest_sha256,pins_sha256=args.pins_sha256,harness_sha256=args.harness_sha256)
    except BaseException as exc:
        terminal.update(error_class=type(exc).__name__)
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        signal.signal(signal.SIGINT,signal.SIG_IGN)
        try:supervisor.cleanup()
        except BaseException as exc:terminal.update(cleanup_error_class=type(exc).__name__,status='CLEANUP_UNKNOWN')
        terminal.update(original_waits=supervisor.waits,all_original_children_waited=supervisor.live is None,
            elapsed=time.monotonic()-start,peak_sampled_aggregate_rss=supervisor.peak_rss,
            peak_sampled_namespace_charge=supervisor.peak_namespace_charge,peak_sampled_namespace_slack=supervisor.peak_namespace_slack,
            peak_charged_artifacts=supervisor.peak_bytes,cpu_affinity=sorted(os.sched_getaffinity(0)),
            maximum_phase_seconds=PHASE_SECONDS,per_process_address_space=AS_CAP,transient_deleted_journal_samples=CHARGE_DISAPPEARANCES)
        save(args.output/'terminal.json',terminal)
        final_charge=charged_tree(args.output)
        if final_charge>ARTIFACT_CAP-65536:raise RuntimeError('terminal artifact charge bound')
        save(args.output/'terminal-accounting.json',{'charged_bytes_including_terminal':final_charge,
            'closing_receipt_reserved_bytes':65536,'artifact_cap':ARTIFACT_CAP})
        if charged_tree(args.output)>ARTIFACT_CAP:raise RuntimeError('final artifact charge bound')
if __name__=='__main__':main()
