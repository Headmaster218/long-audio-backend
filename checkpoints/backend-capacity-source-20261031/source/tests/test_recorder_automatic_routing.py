"""Bounded live admission rollover, with old in-flight upload and restart."""
from dataclasses import replace
import ftplib
import pytest
from test_recorder_ftps_receiver import configured, running, connect, upload
from test_recorder_receiver_routing import two,read
from test_recorder_receiver_completion_review import contents
from test_recorder_inbox import delivery
from audio_backend import recorder_ftps_receiver as receiver
from audio_backend import recorder_receiver_completion as completion
from audio_backend import recorder_receiver_publication as publication
from audio_backend.recorder_receiver_routing import Routes

def automatic(case,tmp_path,**kw):
 config,raw,ready,state=two(case,tmp_path)
 return replace(config,auto_advance_generations=True,**kw),raw,ready,state

def test_live_capacity_switch_preserves_inflight_and_readback_then_restart(configured,tmp_path):
 config,raw,ready,state=automatic(configured,tmp_path,max_attempts=1)
 first=configured['source'];second=delivery(tmp_path/'second/templates',1,close='ROTATION')
 with running(config) as (server,owner):
  a=connect(server,config);a.mkd(first.name)
  b=connect(server,config);b.mkd(second.name);upload(b,second)
  assert owner.active==1 and (ready/second.name).is_dir()
  upload(a,first);a.cwd(first.name);assert read(a,'audio.wav')==(first/'audio.wav').read_bytes()
  b.cwd('/'+first.name);assert read(b,'audio.wav')==(first/'audio.wav').read_bytes()
  a.quit();b.quit()
 before=(contents(configured['raw']),contents(raw),contents(configured['ready']),contents(ready))
 with running(config) as (server,owner):
  assert owner.active==1
  c=connect(server,config)
  c.cwd('/'+first.name);assert read(c,'audio.wav')==(first/'audio.wav').read_bytes()
  c.cwd('/')
  with pytest.raises(ftplib.error_perm):c.mkd(second.name[:-32]+'f'*32)
  c.quit()
 assert before==(contents(configured['raw']),contents(raw),contents(configured['ready']),contents(ready))

def test_full_reservations_advance_before_any_file_is_open(configured,tmp_path):
 config,raw,ready,state=automatic(configured,tmp_path,max_raw_bytes=sum(completion.LIMITS.values()))
 routes=Routes(config)
 try:
  first=configured['source'].name;second=first[:-32]+'f'*32
  a,_=routes.begin(first);b,_=routes.begin(second)
  assert a is routes.owners[0] and b is routes.owners[1]
  assert a._charged_bytes==b._charged_bytes==sum(completion.LIMITS.values())
  with pytest.raises(publication.Refused,match='all_generations_full'):routes.begin(first[:-32]+'e'*32)
 finally:routes.close()
 # Empty durable attempts still count and never regain write rights.
 routes=Routes(config)
 try:
  assert routes.active==1
  with pytest.raises(publication.Refused,match='existing_attempt'):routes.begin(first)
 finally:routes.close()

def test_uncertain_mkd_never_retries_or_advances(configured,tmp_path,monkeypatch):
 config,raw,ready,state=automatic(configured,tmp_path,max_attempts=1);routes=Routes(config)
 try:
  real=routes.owners[0].begin_attempt
  def uncertain(name):real(name);raise OSError('after mkdir')
  monkeypatch.setattr(routes.owners[0],'begin_attempt',uncertain)
  with pytest.raises(OSError):routes.begin(configured['source'].name)
  with pytest.raises(publication.Refused,match='admission_review_required'):
   routes.begin(configured['source'].name[:-32]+'f'*32)
  assert not list(raw.iterdir())
 finally:routes.close()

def test_pending_publication_is_ambiguity_not_capacity(configured,tmp_path):
 config,raw,ready,state=automatic(configured,tmp_path);routes=Routes(config)
 try:
  (configured['state']/'prepare'/('pending-'+'a'*32)).mkdir(mode=0o700)
  with pytest.raises(publication.Refused,match='pending_publication'):
   routes.begin(configured['source'].name)
  assert not list(raw.iterdir()) and not list(configured['raw'].iterdir())
 finally:routes.close()

@pytest.mark.parametrize('value',[1,'true',None,[],True])
def test_automatic_config_requires_boolean_and_routes(configured,value):
 data=dict(configured['data'],auto_advance_generations=value)
 with pytest.raises(receiver.ConfigurationError,match='automatic_routing'):
  receiver._validate_config(data)


def test_retired_pending_preparation_blocks_new_admission_but_not_readback(configured,tmp_path):
 config,raw,ready,state=automatic(configured,tmp_path,max_attempts=1)
 first=configured['source'];second=delivery(tmp_path/'second/templates',1,close='ROTATION')
 with running(config) as (server,routes):
  client=connect(server,config);client.mkd(first.name);upload(client,first)
  client.mkd(second.name);upload(client,second);client.quit()
 (configured['state']/'prepare'/('pending-'+'a'*32)).mkdir(mode=0o700)
 with running(config) as (server,routes):
  assert routes.active==1
  before=[(set(o._names),o._charged_bytes) for o in routes.owners]
  with pytest.raises(publication.Refused,match='pending_publication'):
   routes.begin(first.name[:-32]+'f'*32)
  assert before==[(set(o._names),o._charged_bytes) for o in routes.owners]
  client=connect(server,config);client.cwd(first.name)
  assert read(client,'audio.wav')==(first/'audio.wav').read_bytes();client.quit()
