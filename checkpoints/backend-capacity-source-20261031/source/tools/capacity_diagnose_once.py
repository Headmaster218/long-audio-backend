"""One explicitly authorized failed-chunk diagnostic retry, successor held via API."""
import argparse,fcntl,json,os,signal,sqlite3,subprocess,sys,time
from pathlib import Path
from capacity_run import REPO,save,source_pins,digest_file
from capacity_resume import ResumeGate


def main():
    p=argparse.ArgumentParser();p.add_argument('original',type=Path);p.add_argument('--attempt',required=True)
    p.add_argument('--chunk',required=True);p.add_argument('--successor',required=True);a=p.parse_args()
    original=a.original.resolve();out=original/a.attempt;assert out.parent==original;out.mkdir(exist_ok=False)
    data=original/'data';fd=os.open(data/'instance.lock',os.O_RDONLY|os.O_NOFOLLOW);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    state={};db=sqlite3.connect((data/'jobs.sqlite3').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    assert db.execute('pragma integrity_check').fetchone()[0]=='ok';assert db.execute('pragma foreign_key_check').fetchone() is None
    state['chunks']=[dict(r) for r in db.execute('select seq,id,status,next_offset,duration,timeline_start,error,digest,path from chunks order by seq')]
    target=next(r for r in state['chunks'] if r['id']==a.chunk);successor=next(r for r in state['chunks'] if r['id']==a.successor)
    assert target['status']=='failed' and target['error']=='ValueError: strict_provenance_required' and target['next_offset']==0
    assert successor['seq']==target['seq']+1 and successor['status']=='queued'
    streams=[dict(r) for r in db.execute('select stream_id,position,chunk_id from stream_chunks where chunk_id in (?,?) order by position',(a.chunk,a.successor))]
    assert len(streams)==2 and streams[0]['stream_id']==streams[1]['stream_id'] and streams[1]['position']==streams[0]['position']+1
    assert streams[0]['chunk_id']==a.chunk and streams[1]['chunk_id']==a.successor
    state['stream_boundary']=streams
    assert all(r['status']=='completed' for r in state['chunks'] if r['seq']<target['seq'])
    assert digest_file(Path(target['path']))==target['digest']
    state['control']=[dict(r) for r in db.execute('select * from strict_recording_control where cid in (?,?)',(a.chunk,a.successor))]
    assert next(r for r in state['control'] if r['cid']==a.chunk)['state']=='manual'
    with sqlite3.connect(out/'prestart-jobs.sqlite3') as dst:db.backup(dst)
    db.close();save(out/'PRESTART_STATE.json',state)
    old=json.loads((original/'SOURCE_PINS.json').read_text());changes=[]
    for pin in old:
        actual=digest_file(REPO/pin['path'])
        if actual!=pin['sha256']:
            assert pin['path']=='audio_backend/strict_recording.py','unreviewed_production_change'
            changes.append({'path':pin['path'],'before':pin['sha256'],'after':actual})
    assert len(changes)==1
    for asset in json.loads((original/'ASSET_VERIFIED.json').read_text())['files']:
        path=Path(asset['path']);assert path.stat().st_size==asset['bytes'] and digest_file(path)==asset['sha256']
    cfg=json.loads((original/'config.json').read_text());assert Path(cfg['data_dir'])==data
    save(out/'config.json',cfg);pins=source_pins();save(out/'SOURCE_PINS.json',pins)
    save(out/'OPERATOR_ACTION.json',{'kind':'one_explicit_diagnostic_retry','target':a.chunk,'hold_successor':a.successor,'source_changes':changes,'time':time.time(),'no_automatic_retry':True})
    cpus=sorted(os.sched_getaffinity(0));assert len(cpus)>=2;os.sched_setaffinity(0,set(cpus[:2]))
    gate=ResumeGate(out,original,{});gate.hold=fd;gate.deadline=time.monotonic()+300;gate.journal=(out/'http.jsonl').open('x')
    result={'schema':'one-shot-diagnostic-v1','started':time.time(),'target':a.chunk,'successor':a.successor}
    for s in (signal.SIGINT,signal.SIGTERM):signal.signal(s,lambda *_:setattr(gate,'stop_requested',True))
    try:
        gate.check()
        with (out/'service.stdout').open('xb') as stdout,(out/'service.stderr').open('xb') as stderr:
            gate.process=subprocess.Popen([sys.executable,'-I','-B','-X','pycache_prefix='+str(out/'absent-cache'),str(REPO/'tools/capacity_service.py'),str(out/'config.json'),str(fd),str(out)],pass_fds=(fd,),stdout=stdout,stderr=stderr,cwd=REPO,start_new_session=True)
        while not (out/'ready.json').exists():gate.check();time.sleep(.1)
        ready=json.loads((out/'ready.json').read_text());assert ready['strict'] and ready['worker_alive'] and ready['inherited_lock'];gate.port=ready['port']
        for cid,expected in ((a.chunk,'failed'),(a.successor,'queued')):
            row=gate.request(('GET',f'/chunks/{cid}',200,None,{}));assert row['status']==expected
        held=gate.request(('POST',f'/chunks/{a.successor}/pause',200,None,{}));assert held['status']=='failed'
        result['held_successor']=held['status']
        gate.request(('POST',f'/chunks/{a.chunk}/retry',200,None,{}))
        while True:
            row=gate.observe(('GET',f'/chunks/{a.chunk}',200,None,{}))
            if row and row['status'] in ('completed','failed','cancelled','paused'):
                result['target_result']={k:row[k] for k in ('id','status','error','next_offset','duration')};break
            time.sleep(.2)
    except BaseException as e:result['error']={'type':type(e).__name__,'message':str(e)[:4096]}
    finally:
        try:result['shutdown']=gate.stop()
        except BaseException as e:
            result['shutdown_error']=str(e)
            try:result['forced_cleanup']=gate.force_cleanup('diagnostic_shutdown')
            except BaseException as e:result['forced_cleanup_error']=str(e)
        gate.journal.close();result.update(ended=time.time(),source_unchanged=source_pins()==pins)
        save(out/'RESULT.json',result)
        if 'shutdown_error' not in result:os.close(fd)
    print(json.dumps(result))
    if 'error' in result or 'shutdown_error' in result:raise SystemExit(1)

if __name__=='__main__':main()
