"""Read-only evidence audit; SQLite accesses copies, never interrupted originals."""
import hashlib,json,sqlite3,shutil,sys,os
from pathlib import Path
B=Path('/workspace/scratch/12ec3121e93f');E=B/'backend-session-request-physical-20261103';C=B/'backend-session-idempotency-candidate-20261103';O=Path(__file__).resolve().parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def inventory(p):return {str(x.relative_to(p)):{'bytes':x.stat().st_size,'sha256':h(x)} for x in sorted(p.rglob('*')) if x.is_file()}
before=inventory(E);r=json.loads((E/'RESULT.json').read_text());assert r['passed'] and not r['native_activation'] and not r['power_loss_qualified'] and 'error' not in r
phases=['initializer','recover-initializer','precommit','recover-precommit','committed','recover-committed','allocation-disabled','allocation-enabled']
assert [x['phase'] for x in r['runs']]==phases
for x in r['runs']:
 stopped=x['phase'] in ('initializer','precommit','committed');assert x['stop_acknowledged']==stopped and x['exit_code']==(-9 if stopped else 0)
assert sum(x['seconds'] for x in r['runs'])<600 and r['peak_sampled_child_rss']<512*1024**2 and r['peak_sampled_artifact_allocation']<512*1024**2
pins=json.loads((E/'SOURCE_PINS.json').read_text());assert all(h(C/n)==v for n,v in pins.items())
identities=[json.loads((E/phase/'runtime.json').read_text()) for phase in phases];assert all(x==identities[0] for x in identities)
sys.path.insert(0,str(C));from audio_backend.native_owned_cell_store import runtime_fingerprints,Budget
assert runtime_fingerprints(Budget())==identities[0]['fingerprints'] and E.stat().st_dev==identities[0]['filesystem_device']
for phase in ('initializer','precommit','committed'):assert inventory(E/phase)==json.loads((E/(phase+'-preserved.json')).read_text())
observations={}
for phase in phases:
 original=E/phase/'data';copy=O/('copy-'+phase)
 # Each independent run writes new audit-only copies; never recover an original.
 shutil.copytree(original,copy)
 wal=copy/'jobs.sqlite3-wal';assert not wal.exists() or wal.stat().st_size==0
 db=sqlite3.connect((copy/'jobs.sqlite3').as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
 try:
  assert [tuple(x) for x in db.execute('pragma integrity_check')]==[('ok',)] and not db.execute('pragma foreign_key_check').fetchall()
  names={x[0] for x in db.execute("select name from sqlite_master where type='table'")}
  obs={t:db.execute('select count(*) from '+t).fetchone()[0] for t in ('sessions','strict_recording_policy')}
  obs['session_requests_v1']=db.execute('select count(*) from session_requests_v1').fetchone()[0] if 'session_requests_v1' in names else None
  if phase in ('initializer','recover-initializer'):
   assert obs=={'sessions':0,'strict_recording_policy':0,'session_requests_v1':None}
  elif phase=='precommit':assert set(obs.values())=={0}
  elif phase in ('committed','recover-committed','recover-precommit'):
   assert set(obs.values())=={1}
   row=dict(db.execute('select * from session_requests_v1').fetchone());session=dict(db.execute('select * from sessions').fetchone())
   expected=json.loads((E/('committed/barrier.json' if phase=='committed' else phase+'/child-result.json')).read_text())
   if phase!='committed':expected=expected['receipt']
   assert row['request_key']==expected['request_key']=='a'*32 and row['sid']==expected['session_id']==session['id'] and row['request_sha256']==expected['request_sha256']
   assert session['deleted']==0 and session['name']=='synthetic'
   canonical=json.dumps(dict(name='synthetic',identity_group='default',identity_mode=None,processing_mode='legacy'),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
   assert row['request_sha256']==hashlib.sha256(canonical).hexdigest()
  else:
   enabled=phase.endswith('enabled');assert obs['sessions']==obs['strict_recording_policy']==1000 and obs['session_requests_v1']==(1000 if enabled else None)
   assert db.execute('select count(*) from sessions where deleted=0').fetchone()[0]==1
   assert db.execute('select count(*) from sessions where deleted=1').fetchone()[0]==999
   assert db.execute('select name from sessions where deleted=0').fetchone()[0]=='😀'*200
   if enabled:
    assert db.execute('select count(*) from session_requests_v1 where request_sha256 is null').fetchone()[0]==999
    assert db.execute('select count(*) from session_requests_v1 r join sessions s on s.id=r.sid where (s.deleted=1)!=(r.request_sha256 is null)').fetchone()[0]==0
   child=json.loads((E/phase/'child-result.json').read_text());assert child['passed'] and child['created']==1000 and child['active']==1 and child['retained_policies']==1000 and child['refusal']=='strict_policy_capacity'
   for stage in ('initial','full_active','after_999_deletions','final'):
    measure=child[stage];assert measure['logical']==sum(x['logical'] for x in measure['entries']) and measure['allocated']==sum(x['allocated'] for x in measure['entries'])
   for item in child['final']['entries']:
    p=original/item['path'];assert p.stat().st_blocks*512==item['allocated'] and (p.stat().st_size if p.is_file() else 0)==item['logical']
   obs['allocation']= {stage:{k:child[stage][k] for k in ('logical','allocated')} for stage in ('initial','full_active','after_999_deletions','final')}
  observations[phase]=obs
 finally:db.close()
init=json.loads((E/'recover-initializer/child-result.json').read_text());assert init=={'passed':True,'application_recovery':False,'expected_refusal':'session_requests_fresh_store_only'}
pre=json.loads((E/'recover-precommit/child-result.json').read_text());assert set(pre['before'].values())=={0}
committed=json.loads((E/'recover-committed/child-result.json').read_text());assert set(committed['before'].values())=={1} and committed['receipt']==json.loads((E/'committed/barrier.json').read_text())
assert inventory(E)==before
comparison={stage:{k:observations['allocation-enabled']['allocation'][stage][k]-observations['allocation-disabled']['allocation'][stage][k] for k in ('logical','allocated')} for stage in ('initial','full_active','after_999_deletions','final')}
report={'decision':'accepted_bounded_session_request_transaction_evidence_not_activation','observations':observations,'enabled_minus_disabled_bytes':comparison,'source_runtime_pins_match':True,'originals_and_evidence_unchanged':True,'historical_stop_kill_wait_receipts_consistent':True,'initializer_application_recovery':False,'production_activation_approved':False,'limits':['Checkpoints are before COMMIT and after COMMIT before response; no midcommit or power-loss recovery evidence','Interrupted originals never opened by SQLite; independent immutable audit copies have absent or zero-byte WAL','Effective strict-policy lifetime cap1000 remains unchanged;4096 receipt ceiling not reached through API','Allocation comparison is this exact workload and sampled/retained file state, not a universal primary-store bound or performance comparison','999 deleted request digests are logically NULL; no secure erasure claim','No application lifespan/native model/service activation; pending initializer recovery design remains unresolved','Child-only RSS sampling, not hard aggregate memory guarantee'],'evidence_inventory':before,'audit_source_sha256':h(Path(__file__))}
(O/'REPORT.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps({'decision':report['decision'],'delta':comparison,'report_sha256':h(O/'REPORT.json'),'audit_sha256':h(Path(__file__))},sort_keys=True))
