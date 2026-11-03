"""Durable-intent bootstrap and partial-file recovery via real subprocess exits."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from audio_backend import recorder_capture_intake as capture
from audio_backend import recorder_intake as single
from test_recorder_capture_intake import options
from test_recording_session_contract import handler_app,session,HEADERS
from test_strict_uploader_admission import StrictRoutes

CHILD='''
import json,os,sys
from pathlib import Path
from audio_backend import recorder_capture_intake as c
from audio_backend import recorder_initial_intent as i
from audio_backend import recorder_intake as single
from audio_backend import recording_uploader as u
args=json.loads(sys.argv[1]);contract=json.loads(sys.argv[2]);stage=sys.argv[3]
class ReadOnly:
 def request(self,method,path,**kw):
  assert method=='GET' and path.endswith('/recording-contract')
  return contract
write=single.manifest_write
complete=i.complete_file
scaffold=i.complete_scaffold
snapshot=c.create_snapshot
def partial(path,raw):
 fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 os.write(fd,raw[:max(1,len(raw)//2)]);os.fsync(fd);os.close(fd);os._exit(74)
def patched_write(path,value):
 if stage=='admission_prefix' and path.name=='admission.json':partial(path,single._json(value))
 write(path,value)
 if stage=='intent' and path.name==i.NAME:os._exit(74)
def patched_complete(path,raw,remaining):
 if stage=='header_prefix' and path.name=='capture.json':partial(path,raw)
 return complete(path,raw,remaining)
def patched_scaffold(*a):
 result=scaffold(*a)
 if stage=='scaffold':os._exit(74)
 return result
def patched_snapshot(source,destination,**kw):
 if stage=='snapshot_prefix':
  destination.mkdir(mode=0o700)
  partial(destination/'audio.wav',(source/'audio.wav').read_bytes())
 result=snapshot(source,destination,**kw)
 if stage=='snapshot_complete':os._exit(74)
 return result
single.manifest_write=patched_write;i.complete_file=patched_complete
i.complete_scaffold=patched_scaffold;c.create_snapshot=patched_snapshot
c.run(**args,transport=ReadOnly(),limits=u.Limits(disk_floor=0,deadline_seconds=30))
'''


def interrupted(tmp_path,client,stage):
 paths,args=options(tmp_path);args.update(session_id=session(client),transport=StrictRoutes(client))
 wire={k:str(v) if isinstance(v,Path) else v for k,v in args.items() if k not in ('limits','transport')}
 wire.update(delivery=str(paths[0]),start=True)
 contract=client.get('/sessions/'+args['session_id']+'/recording-contract',headers=HEADERS).json()
 result=subprocess.run([sys.executable,'-c',CHILD,json.dumps(wire),json.dumps(contract),stage],capture_output=True,timeout=40)
 assert result.returncode==74,result.stderr.decode()
 return paths,args


@pytest.mark.parametrize('stage',['intent','header_prefix','scaffold','snapshot_prefix','snapshot_complete','admission_prefix'])
def test_exact_prefix_initial_recovery(stage,tmp_path):
 with handler_app(tmp_path/'server') as (client,app):
  paths,args=interrupted(tmp_path,client,stage)
  before={str(p):(p.stat().st_ino,p.read_bytes()) for p in args['job_dir'].rglob('*') if p.is_file()}
  result=capture.run(**args,delivery=paths[0],recover_initial=True)
  assert result['frames']==4 and result['inputs']==1 and not result['capture_finalized']
  for name,(inode,data) in before.items():
   assert Path(name).stat().st_ino==inode and Path(name).read_bytes().startswith(data)
  assert all(m=='GET' for m,_,_ in args['transport'].calls)
  assert capture.run(**args,delivery=paths[0])['frames']==4
  assert capture.run(**args,delivery=paths[1])['frames']==8
  assert not app.state.worker.thread.is_alive()


@pytest.mark.parametrize('kind',['header','snapshot','admission','intent','target','caps','remote'])
def test_ambiguous_initial_state_preserved_before_repair(tmp_path,kind):
 with handler_app(tmp_path/'server') as (client,app):
  stage={'header':'header_prefix','snapshot':'snapshot_prefix','admission':'admission_prefix'}.get(kind,'intent')
  paths,args=interrupted(tmp_path,client,stage)
  if kind in ('header','snapshot','admission','intent'):
   relative={'header':'capture.json','snapshot':'admissions/0/snapshot/audio.wav','admission':'admissions/0/admission.json','intent':'initial-intent.json'}[kind]
   p=args['job_dir']/relative;b=bytearray(p.read_bytes());b[0]^=1;p.write_bytes(b)
  elif kind=='target':args['capture_id']='different'
  elif kind=='caps':args['max_inputs']=127
  else:
   routes=args['transport']
   class Nonempty:
    def request(self,*a,**kw):
     value=routes.request(*a,**kw);value['has_chunks']=True;return value
   args['transport']=Nonempty()
  before={str(p):p.read_bytes() for p in args['job_dir'].rglob('*') if p.is_file()}
  with pytest.raises((capture.Refused,ValueError)):
   capture.run(**args,delivery=paths[0],recover_initial=True)
  assert before=={str(p):p.read_bytes() for p in args['job_dir'].rglob('*') if p.is_file()}
  assert not (args['job_dir']/'uploader/uploader.json').exists()


def test_legacy_partial_without_intent_never_reconstructed(tmp_path):
 with handler_app(tmp_path/'server') as (client,app):
  paths,args=interrupted(tmp_path,client,'snapshot_prefix')
  p=args['job_dir']/'capture.json';header=single.manifest_read(p);header.pop('initial_intent');p.write_bytes(single._json(header))
  (args['job_dir']/'initial-intent.json').unlink()
  with pytest.raises(capture.Refused,match='partial_admission'):
   capture.run(**args,delivery=paths[0],recover_initial=True)
  assert not args['transport'].calls


def test_hard_exit_during_repair_reconciles_again(tmp_path):
 with handler_app(tmp_path/'server') as (client,app):
  paths,args=interrupted(tmp_path,client,'snapshot_prefix')
  wav=args['job_dir']/'admissions/0/snapshot/audio.wav';before=wav.read_bytes();inode=wav.stat().st_ino
  wire={k:str(v) if isinstance(v,Path) else v for k,v in args.items() if k not in ('limits','transport')}
  wire.update(delivery=str(paths[0]),recover_initial=True)
  contract=client.get('/sessions/'+args['session_id']+'/recording-contract',headers=HEADERS).json()
  code='''
import os,json,sys
from audio_backend import recorder_capture_intake as c
from audio_backend import recording_uploader as u
args=json.loads(sys.argv[1]);contract=json.loads(sys.argv[2]);write=os.write
class ReadOnly:
 def request(self,method,path,**kw):
  assert method=='GET' and path.endswith('/recording-contract')
  return contract
def interrupted(fd,data):
 if os.readlink('/proc/self/fd/'+str(fd)).endswith('/snapshot/audio.wav'):
  write(fd,data[:1]);os.fsync(fd);os._exit(75)
 return write(fd,data)
os.write=interrupted
c.run(**args,transport=ReadOnly(),limits=u.Limits(disk_floor=0,deadline_seconds=30))
'''
  child=subprocess.run([sys.executable,'-c',code,json.dumps(wire),json.dumps(contract)],capture_output=True,timeout=40)
  assert child.returncode==75,child.stderr.decode()
  assert wav.stat().st_ino==inode and wav.read_bytes().startswith(before) and wav.stat().st_size==len(before)+1
  assert not (args['job_dir']/'uploader/uploader.json').exists()
  assert capture.run(**args,delivery=paths[0],recover_initial=True)['frames']==4


def test_incomplete_intent_or_missing_lock_is_not_guessed(tmp_path):
 with handler_app(tmp_path/'server') as (client,app):
  paths,args=interrupted(tmp_path,client,'intent')
  intent=args['job_dir']/'initial-intent.json';original=intent.read_bytes();intent.write_bytes(original[:20])
  with pytest.raises(ValueError):capture.run(**args,delivery=paths[0],recover_initial=True)
  assert not args['transport'].calls and not (args['job_dir']/'capture.json').exists()
  intent.write_bytes(original);(args['job_dir']/'intake.lock').unlink()
  with pytest.raises(FileNotFoundError):capture.run(**args,delivery=paths[0],recover_initial=True)
  assert not args['transport'].calls
