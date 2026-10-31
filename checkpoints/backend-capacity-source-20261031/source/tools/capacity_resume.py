"""Explicit operator continuation of a preserved capacity run, never an auto retry.
Original receipts stay untouched. Each invocation creates its own attempt directory.
Unknown/partial publication stops; only fully sealed known pages are adopted.
"""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import subprocess
import sys
import time
import wave
from capacity_run import Gate, REPO, DATA_CAP, FLOOR, allocated, digest_file, save, source_pins
import capacity_requests as plan


def inventory(data, retry_plan=()):
    with sqlite3.connect((data/'jobs.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None
        sessions=[dict(r) for r in db.execute('SELECT id FROM sessions WHERE deleted=0')]
        assert len(sessions)==1
        pages=[dict(r) for r in db.execute('SELECT page_number,state,released,revision,seal_key,start_frame,end_frame,eos FROM recording_pages ORDER BY page_number')]
        assert pages and [p['page_number'] for p in pages]==list(range(1,len(pages)+1))
        assert all(p['state'] in ('published','pending_receipts') for p in pages),'partial_publication_requires_review'
        chunks=[dict(r) for r in db.execute('SELECT seq,id,status,next_offset,duration,timeline_start,error FROM chunks ORDER BY seq')]
        assert len(chunks)==len(pages)*28
        approved={r['id']:r for r in retry_plan}
        assert len(approved)==len(retry_plan)
        assert set(approved)<=set(c['id'] for c in chunks)
        for i,c in enumerate(chunks):
            assert c['duration']==10.5 and c['timeline_start']==i*10.5
            if c['id'] in approved:
                a=approved[c['id']]
                assert c['status']=='failed' and c['error']==a['error'] and c['next_offset']==a['next_offset'],'retry_plan_state_changed'
            else:
                assert c['status'] in ('completed','running','queued','staging'),'preexisting_failure_requires_review'
        return {'session_id':sessions[0]['id'],'pages':pages,'chunks':chunks,
            'sources':[dict(r) for r in db.execute('SELECT idempotency_key,digest,source_start_frame,frames FROM recording_sources ORDER BY source_start_frame')]}


class ResumeGate(Gate):
    def __init__(self,out,original,state,retry_plan=()):
        super().__init__(out,147,16*3600)
        self.original=original;self.data=original/'data';self.state=state;self.total_disk_at=0;self.retry_plan={r['id']:r for r in retry_plan}
    def check(self):
        super().check()
        if (self.original/'STOP_REQUESTED').exists():raise RuntimeError('manual_stop_requested')
        now=time.monotonic()
        if now-self.total_disk_at>=10:
            size,_=allocated(self.original);self.peak_data=max(self.peak_data,size);self.total_disk_at=now
            if size>DATA_CAP:raise RuntimeError('total_preserved_data_capacity')
    def create_page(self,sid,number):
        return self.create_page_reconciled(sid,number,reconcile_first=True)
    def stream(self,sample):
        sid=self.state['session_id'];pending=[];all_hash=hashlib.sha256();cursor=0
        for prior in self.state['pages']:
            page=self.request(plan.inspect_page(sid,'fixture',prior['page_number']))
            ids=plan.check_page_geometry(page,prior['page_number'],self.pages)
            pending.extend(ids)
            for cid in ids:all_hash.update((cid+'\n').encode())
        assert pending==[r['id'] for r in self.state['chunks']]
        self.published=len(self.state['pages'])
        # Only the exact previously running chunk is eligible for this explicit
        # operator retry, and only after startup has durably marked interruption.
        for old in self.state['chunks']:
            if old['status']!='running' and old['id'] not in self.retry_plan:continue
            current=self.request(('GET',f"/chunks/{old['id']}",200,None,{}))
            expected_error=self.retry_plan[old['id']]['error'] if old['id'] in self.retry_plan else 'Strict processing interrupted; manual retry required'
            assert current['status']=='failed' and current['error']==expected_error,current
            assert current['next_offset']==old['next_offset']
            self.request(('POST',f"/chunks/{old['id']}/retry",200,None,{}))
        while self.completed<self.pages*28:
            observation=self.observe(plan.chunk_progress(sid,cursor))
            if observation is None:time.sleep(.5);continue
            for row in observation['chunks']:
                if row['status'] in ('failed','paused','cancelled'):raise RuntimeError('chunk_refused:'+json.dumps(row))
                if row['status']!='completed':break
                assert pending and row['id']==pending[0] and row['next_offset']==row['duration']==10.5
                pending.pop(0);cursor=row['cursor'];self.completed+=1
            if self.published<self.pages and len(pending)<=28:
                self.wait_page(sid,self.published)
                ids=self.publish(sid,self.published+1,sample);pending.extend(ids)
                for cid in ids:all_hash.update((cid+'\n').encode())
            time.sleep(.5)
        self.wait_page(sid,self.pages)
        return {'session_id':sid,'chunks':self.completed,'cid_order_sha256':all_hash.hexdigest()}


def main():
    p=argparse.ArgumentParser();p.add_argument('original',type=Path);p.add_argument('--attempt',required=True)
    p.add_argument('--retry-plan',type=Path);p.add_argument('--reviewed-source-commit')
    args=p.parse_args();original=args.original.resolve();out=original/args.attempt
    retry_plan=json.loads(args.retry_plan.read_text()) if args.retry_plan else []
    assert not retry_plan or args.reviewed_source_commit,'reviewed_commit_required'
    assert out.parent==original and not out.exists()
    out.mkdir();data=original/'data'
    fd=os.open(data/'instance.lock',os.O_RDONLY|os.O_NOFOLLOW)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    lock=os.fstat(fd)
    original_pins=json.loads((original/'SOURCE_PINS.json').read_text())
    changed=[]
    if args.reviewed_source_commit:
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
        assert head==args.reviewed_source_commit,'source_commit_changed'
        assert not subprocess.check_output(['git','status','--porcelain'],cwd=REPO,text=True).strip(),'uncommitted_sources'
    for pin in original_pins:
        actual=digest_file(REPO/pin['path'])
        if actual!=pin['sha256']:
            assert args.reviewed_source_commit,pin['path']
            changed.append({'path':pin['path'],'before':pin['sha256'],'after':actual})
    assets=json.loads((original/'ASSET_VERIFIED.json').read_text())
    for asset in assets['files']:
        path=Path(asset['path']);assert path.stat().st_size==asset['bytes'] and digest_file(path)==asset['sha256'],str(path)
    state=inventory(data,retry_plan);save(out/'PRESTART_STATE.json',state)
    # Preserve the pre-start DB state without checkpointing or mutating it.
    with sqlite3.connect((data/'jobs.sqlite3').as_uri()+'?mode=ro',uri=True) as src:
        with sqlite3.connect(out/'prestart-jobs.sqlite3') as dst:src.backup(dst)
    cfg=json.loads((original/'config.json').read_text());assert Path(cfg['data_dir'])==data
    save(out/'config.json',cfg);pins=source_pins();save(out/'SOURCE_PINS.json',pins)
    save(out/'OPERATOR_ACTION.json',{'kind':'explicit_resume_preserved_capacity','time':time.time(),
        'page_create_policy':'exact_read_reconciliation_before_same_request;60s;20POST;no_inference_upload_seal_retry',
        'original_source_pins_sha256':digest_file(original/'SOURCE_PINS.json'),
        'inactive_instance_lock':{'device':lock.st_dev,'inode':lock.st_ino},
        'old_cleanup':'unconfirmed_environment_interruption','continuous_wall_pass':False,
        'retry_candidates':[r['id'] for r in state['chunks'] if r['status']=='running'],
        'reviewed_source_commit':args.reviewed_source_commit,'source_changes':changed,'explicit_retry_plan':retry_plan})
    sample_path=REPO.parent/'audio-model-recovery-20261023b/expanded/sensevoice/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/test_wavs/en.wav'
    assert digest_file(sample_path)=='eb1eb008904465b74c304aad8342e8c7d3c6e61ffe9f66adcaca9cf0f76a93f4'
    with wave.open(str(sample_path),'rb') as wav:sample=wav.readframes(wav.getnframes())
    # Prove deterministic continuation uses the same original audio and keys.
    assert len(state['sources'])==len(state['pages'])*3
    for index,source in enumerate(state['sources']):
        number=index//3+1;part=index%3;start=(number-1)*plan.PAGE_FRAMES+sum(plan.SOURCE_FRAMES[:part])
        assert source['idempotency_key']==f'fixture.page.{number}.source.{part+1}'
        assert source['source_start_frame']==start and source['frames']==plan.SOURCE_FRAMES[part]
        rotated=sample[(start*2)%len(sample):]+sample[:(start*2)%len(sample)]
        length=source['frames']*2;pcm=(rotated*(length//len(rotated)+1))[:length]
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm)
        assert hashlib.sha256(buffer.getvalue()).hexdigest()==source['digest'],'source_continuity'
    cpus=sorted(os.sched_getaffinity(0));assert len(cpus)>=2;os.sched_setaffinity(0,set(cpus[:2]))
    gate=ResumeGate(out,original,state,retry_plan);gate.hold=fd;gate.journal=(out/'http.jsonl').open('x')
    result={'schema':'resumed-real-worker-capacity-v1','started':time.time(),'audio_seconds':43218,
        'wall_12h_qualified':False,'accuracy_qualified':False,'repeated_speech':True,
        'initial_completed':sum(c['status']=='completed' for c in state['chunks'])}
    for s in (signal.SIGINT,signal.SIGTERM):signal.signal(s,lambda *_:setattr(gate,'stop_requested',True))
    try:
        gate.check()
        with (out/'service.stdout').open('xb') as stdout,(out/'service.stderr').open('xb') as stderr:
            gate.process=subprocess.Popen([sys.executable,'-I','-B','-X','pycache_prefix='+str(out/'absent-cache'),str(REPO/'tools/capacity_service.py'),str(out/'config.json'),str(fd),str(out)],pass_fds=(fd,),stdout=stdout,stderr=stderr,cwd=REPO,start_new_session=True)
        while not (out/'ready.json').exists():gate.check();time.sleep(.1)
        ready=json.loads((out/'ready.json').read_text());assert ready['strict'] and ready['inherited_lock'] and ready['worker_alive']
        gate.port=ready['port'];gate.set_phase('processing');result['stream']=gate.stream(sample)
    except BaseException as exc:result['error']={'type':type(exc).__name__,'message':str(exc)[:4096]}
    finally:
        gate.set_phase('stopping')
        try:result['shutdown']=gate.stop()
        except BaseException as exc:
            result['shutdown_error']=str(exc)
            try:result['forced_cleanup']=gate.force_cleanup('resume_shutdown_failed')
            except BaseException as error:result['forced_cleanup_error']=str(error)
        gate.journal.close()
        if not any(k in result for k in ('error','shutdown_error')):
            try:
                gate.set_phase('integrity')
                from capacity_integrity import inspect
                result['integrity']=inspect(data,cfg,147)
            except BaseException as exc:result['integrity_error']=str(exc)
        result.update(ended=time.time(),peak_rss=gate.peak_rss,peak_data=gate.peak_data,http_requests=gate.http_count)
        size,count=allocated(original);free=shutil.disk_usage(original).free
        result.update(final_allocated=size,final_files=count,final_free=free)
        if size>DATA_CAP or free<FLOOR:result['resource_error']='final_disk_bound'
        result['source_unchanged']=source_pins()==pins
        gate.set_phase('terminal')
        if gate.phase_observation_error is not None:result['monitoring_error']=gate.phase_observation_error
        result['passed']=result['source_unchanged'] and not any(k.endswith('error') for k in result)
        save(out/'RESULT.json',result)
        if 'shutdown_error' not in result:os.close(fd)
    if not result['passed']:raise SystemExit(1)

if __name__=='__main__':main()
