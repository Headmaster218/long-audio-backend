import os,sys,time,json,hashlib,struct,threading,shutil,traceback
from pathlib import Path
ROOT=Path(os.environ['RESOURCE_TRENDS_OUTPUT']).resolve()
SOURCE=Path(os.environ['RESOURCE_TRENDS_SOURCE']).resolve()
sys.path.insert(0,str(SOURCE))
from audio_backend import recorder_receiver_completion as c, recorder_receiver_publication as p
from audio_backend import recorder_inbox as inbox

def emit(name,x):
    with (ROOT/name).open('a') as f:f.write(json.dumps(x,sort_keys=True)+'\n')
def stat():
    d={}
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith(('VmRSS:','VmHWM:')):d[line.split(':')[0]]=int(line.split()[1])*1024
    d.update(time=time.time(),pid=os.getpid(),fd=len(list(Path('/proc/self/fd').iterdir())))
    return d
stop=threading.Event()
monitor_errors=[]
def monitor():
    try:
        while not stop.is_set():
            x=stat(); x['free']=shutil.disk_usage(ROOT).free
            x['allocated']=sum(f.stat().st_blocks*512 for f in ROOT.rglob('*') if f.is_file())
            emit('timeseries.jsonl',x)
            if x['VmRSS']>2*1024**3 or x['free']<10*1024**3 or x['allocated']>6*1024**3:
                (ROOT/'SAFETY_STOP').write_text(json.dumps(x))
            stop.wait(1)
    except BaseException:
        monitor_errors.append(traceback.format_exc())
        try:emit('monitor_errors.jsonl', {'error':monitor_errors[-1]})
        except OSError:pass
def guard():
    assert not monitor_errors,'resource observer failed'
    assert not (ROOT/'SAFETY_STOP').exists(),'resource safety bound'
    assert time.monotonic()-started<1800,'wall deadline'
def utf(x):
    b=x.encode('ascii');return struct.pack('>H',len(b))+b
def q(x):return struct.pack('>q',x)
def i(x):return struct.pack('>i',x)
def data(seq):
    frames=120*16000; pcm=b'\x00\x01'*frames
    wav=struct.pack('<4sI8sIHHIIHH4sI',b'RIFF',36+len(pcm),b'WAVEfmt ',16,1,1,16000,32000,2,16,b'data',len(pcm))+pcm
    wh=hashlib.sha256(wav).hexdigest()
    local=b'PCM1'+i(1)+utf('synthetic-resource-study')+utf('synthetic-run')+q(0)+i(16000)+i(1)
    local+=utf('phone')+utf('phone')+utf('START')+q(seq)+q(seq*frames)+q(frames)+q(frames)
    local+=utf(hashlib.sha256(pcm).hexdigest())+utf(wh)+utf('STOP' if seq==255 else 'ROTATION')+bytes([seq>0])
    local+=hashlib.sha256(local).digest()
    source='segment-'+hashlib.sha256(b'synthetic-resource-study').hexdigest()+'-'+str(seq)+'.ready'
    env=b'RFT1'+utf('resource-study-v1')+utf(source)+i(len(local))+local
    bind=hashlib.sha256((source+'\n'+wh+'\nresource-study-v1').encode()).hexdigest();name='r-'+bind+'-'+'0'*32
    marker='\n'.join(('RECORDER-DELIVERY-V1',name,'resource-study-v1',source,str(len(wav)),wh,str(len(env)),hashlib.sha256(env).hexdigest(),''))
    return name,{'audio.wav':wav,'segment.v1.bin':env,'delivery.v1.txt':marker.encode()}

def run(start,end):
    raw=ROOT/'raw';ready=ROOT/'ready';state=ROOT/'publisher'
    if start==0:
        raw.mkdir(mode=0o700);ready.mkdir(mode=0o700)
        p.configure(state_dir=state,staging_dir=raw,inbox_dir=ready,exclusive_receiver=True)
    t=time.monotonic();owner=c.ReceiverCompletion(binding=p.bind(state),inbox_dir=ready,exclusive_receiver=True)
    emit('operations.jsonl',dict(op='open_process',count=start,seconds=time.monotonic()-t,**stat()))
    with owner:
        for seq in range(start,end):
            guard();name,files=data(seq); t=time.monotonic();attempt=owner.begin_attempt(name)
            for key in p.FILES:
                w=owner.open_writer(attempt,key);w.stream.write(files[key]);w.stream.close();result=owner.writer_closed(w,successful=True)
            assert result['status']=='published'; publish=time.monotonic()-t
            t=time.monotonic();inv=p.inventory(p.bind(state),ready)
            assert len(inv['deliveries'])==seq+1 and all(x['status']=='observed_valid' for x in inv['deliveries'])
            emit('operations.jsonl',dict(op='segment',count=seq+1,audio_seconds=(seq+1)*120,publish_seconds=publish,inventory_seconds=time.monotonic()-t,**stat()))
        if end==256:
            name,_=data(256);t=time.monotonic()
            try:owner.begin_attempt(name)
            except p.Refused as exc:
                assert str(exc)=='completion_raw_attempt_capacity',str(exc)
                emit('operations.jsonl',dict(op='expected_refusal',reason=str(exc),seconds=time.monotonic()-t,**stat()))
            else:raise AssertionError('257th attempt admitted')
            for j in range(32):
                guard();t=time.monotonic();inv=p.inventory(p.bind(state),ready)
                assert len(inv['deliveries'])==256
                emit('operations.jsonl',dict(op='fixed_inventory',iteration=j,seconds=time.monotonic()-t,**stat()))
    emit('operations.jsonl',dict(op='closed_process',count=end,**stat()))

started=time.monotonic();thread=threading.Thread(target=monitor,daemon=True);thread.start()
try:run(int(sys.argv[1]),int(sys.argv[2]))
except BaseException:
    emit('errors.jsonl',dict(error=traceback.format_exc(),**stat()));raise
finally:
    stop.set();thread.join()
    if monitor_errors:raise RuntimeError('resource observer failed')
    emit('operations.jsonl',dict(op='terminal',wall_seconds=time.monotonic()-started,**stat()))
