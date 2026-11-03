"""Independent copied-byte SQLite audit, never opens interrupted originals."""
import hashlib,json,sqlite3,shutil,sys
from pathlib import Path
B=Path('/workspace/scratch/12ec3121e93f');E=B/'backend-session-atomic-init-physical-20261103';C=B/'backend-session-atomic-init-candidate-20261103';O=Path(__file__).resolve().parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def inventory(p):return {str(x.relative_to(p)):{'bytes':x.stat().st_size,'sha256':h(x)} for x in sorted(p.rglob('*')) if x.is_file()}
before=inventory(E);r=json.loads((E/'RESULT.json').read_text());assert r['passed'] and 'error' not in r and not r['native_activation'] and not r['power_loss_qualified']
phases=['initializer-before','recover-initializer-before','initializer-after','recover-initializer-after']
assert [x['phase'] for x in r['runs']]==phases
for row in r['runs']:
 stopped=not row['phase'].startswith('recover-');assert row['stop_acknowledged']==stopped and row['exit_code']==(-9 if stopped else 0)
assert sum(x['seconds'] for x in r['runs'])<60
pins=json.loads((E/'SOURCE_PINS.json').read_text());assert all(h(C/n)==v for n,v in pins.items())
identities=[json.loads((E/p/'runtime.json').read_text()) for p in phases];assert all(x==identities[0] for x in identities)
sys.path.insert(0,str(C));from audio_backend.native_owned_cell_store import runtime_fingerprints,Budget
assert runtime_fingerprints(Budget())==identities[0]['fingerprints']
observed={}
for phase in phases:
 if not phase.startswith('recover-'):
  assert inventory(E/phase)==json.loads((E/(phase+'-preserved.json')).read_text())
  assert json.loads((E/phase/'barrier.json').read_text())==dict(stage=phase,transaction_active=True)
 else:
  assert json.loads((E/phase/'child-result.json').read_text())==dict(passed=True,application_recovery=False,expected_refusal='strict_archive_fresh_only')
 copy=O/('copy-v2-'+phase)
 if copy.exists():assert inventory(copy)==inventory(E/phase/'data')
 else:shutil.copytree(E/phase/'data',copy)
 wal=copy/'jobs.sqlite3-wal'
 # WAL contains base-phase committed frames: use SQLite WAL-aware read-only opening of the COPY, not immutable mode.
 db=sqlite3.connect((copy/'jobs.sqlite3').as_uri()+'?mode=ro',uri=True)
 try:
  assert db.execute('pragma integrity_check').fetchall()==[('ok',)] and not db.execute('pragma foreign_key_check').fetchall()
  names={x[0] for x in db.execute('select name from sqlite_master')}
  assert 'sessions' in names and not any(n.startswith(('strict_','session_requests_')) for n in names)
  assert db.execute('select count(*) from sessions').fetchone()[0]==0
  observed[phase]={'base_sessions_present':True,'sessions':0,'strict_namespace_absent':True,'request_namespace_absent':True,'database_sha256':h(copy/'jobs.sqlite3'),'wal_bytes':wal.stat().st_size if wal.exists() else None}
 finally:db.close()
assert inventory(E)==before
report={'decision':'accepted_narrow_atomic_namespace_initialization_fix','observations':observed,'source_runtime_pins_match':True,'original_evidence_unchanged':True,'production_activation_approved':False,'whole_store_initialization_recovery':False,'limits':['Both checkpoints are before enclosing COMMIT, not midcommit or power-loss tests','Earlier base-schema phase remains durable; strict reopen correctly refuses and no adoption/migration was attempted','No native model/service activated; SQLite inspection used separate WAL-aware read-only audit copies only','Historical stop/kill/wait events are consistent receipts, not independently observed retrospectively','This closes the additional feature split-transaction gap only; stop qualification expansion and use explicit known-session intake'],'evidence_inventory':before,'audit_source_sha256':h(Path(__file__))}
(O/'REPORT.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps({'decision':report['decision'],'report_sha256':h(O/'REPORT.json'),'audit_sha256':h(Path(__file__))},sort_keys=True))
