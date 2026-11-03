"""Independent saved-byte audit; never opens interrupted database with SQLite."""
import hashlib,json,os,sqlite3,struct,sys
from pathlib import Path
BASE=Path('/workspace/scratch/12ec3121e93f')
E=BASE/'backend-cellstore-physical-evidence-20261103'
R=BASE/'backend-recorder-intake-20261103'
O=Path(__file__).resolve().parent
h=lambda b:hashlib.sha256(b).hexdigest()
def read(p,limit=32*1024**2):
 with p.open('rb') as f:b=f.read(limit+1)
 assert len(b)<=limit,str(p)
 return b
def inventory():
 return {str(p.relative_to(E)):{'sha256':h(read(p)), 'bytes':p.stat().st_size} for p in sorted(E.rglob('*')) if p.is_file()}
before=inventory();r=json.loads(read(E/'RESULT.json'))
assert r['passed'] is True and 'error' not in r
assert not r['production_profile_activated'] and not r['power_loss_qualified']
baseline=read(E/'baseline/store/cells.sqlite3');damaged=read(E/'attempt-1/store/cells.sqlite3');journal=read(E/'attempt-1/store/cells.sqlite3-journal')
assert len(baseline)==len(damaged)==6144*4096
assert baseline[:16]==b'SQLite format 3\0' and int.from_bytes(baseline[16:18],'big')==4096
assert damaged==read(E/'captured-database.bin') and journal==read(E/'captured-journal.bin')
assert journal[:8]==bytes.fromhex('d9d505f920a163d7')
nrec,seed,npages,sector,page=struct.unpack_from('>IIIII',journal,8)
assert npages==6144 and page==4096 and 0<nrec<=npages
assert 512<=sector<=65536 and sector&(sector-1)==0
end=sector+nrec*(page+8)
assert 0<=len(journal)-end<sector
assert not any(journal[28:sector]) and not any(journal[end:])
seen=set();restored=bytearray(damaged)
for k in range(nrec):
 off=sector+k*(page+8);pn=int.from_bytes(journal[off:off+4],'big');payload=journal[off+4:off+4+page]
 assert 1<=pn<=npages and pn not in seen
 seen.add(pn)
 checksum=seed;idx=page-200
 while idx>=0:
  checksum=(checksum+payload[idx])%(1<<32);idx-=200
 assert checksum==int.from_bytes(journal[off+4+page:off+8+page],'big')
 assert payload==baseline[(pn-1)*page:pn*page]
 restored[(pn-1)*page:pn*page]=payload
changed={i+1 for i in range(npages) if baseline[i*page:(i+1)*page]!=damaged[i*page:(i+1)*page]}
assert changed and changed<=seen and restored==baseline
assert r['audit']==dict(baseline_sha256=h(baseline),database_sha256=h(damaged),journal_sha256=h(journal),reconstructed_sha256=h(restored),changed_pages=len(changed),journal_records=nrec,journal_headers=1,sector_bytes=sector)
recovered=E/'recovery/store/cells.sqlite3'
assert not Path(str(recovered)+'-journal').exists() and read(recovered)==baseline
expected=json.loads(read(E/'baseline/expected_rows.json'))
assert expected==r['full_capacity'] and expected['pages']==6144 and expected['sqlite_full'] is True
assert expected['cells']==55 and expected['sources']==2048 and expected['full_events']==5
# SQLite opens only clean recovered bytes, immutable/read-only. No harness import.
db=sqlite3.connect(recovered.as_uri()+'?mode=ro&immutable=1',uri=True)
try:
 assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
 assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
 assert db.execute('PRAGMA page_count').fetchone()[0]==6144
 sys.path.insert(0,str(R))
 from audio_backend.native_owned_cell_store import CellStore, Receipt
 scratch=sqlite3.connect(':memory:',isolation_level=None)
 try:
  CellStore._schema(scratch)
  schema=db.execute('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name').fetchall()
  assert schema==scratch.execute('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name').fetchall()
 finally:scratch.close()
 source_receipts={}
 for cid,sid,digest,raw in db.execute('SELECT * FROM sources'):
  receipt=Receipt.decode(raw)
  assert receipt.chunk_id==cid and receipt.session_id==sid=='synthetic' and receipt.digest==digest=='a'*64
  assert receipt.frames==320000 and receipt.data_offset==44 and receipt.size==640044
  assert receipt.signature[0]==27 and 1<=receipt.signature[1]<=2048
  index=receipt.signature[1]-1
  assert cid==h(('source-%04d'%index).encode())
  assert receipt.signature==(27,index+1,640044,1,1) and receipt.timeline_start==0. and receipt.stream_id is None
  source_receipts[cid]=h(raw)
 assert len(source_receipts)==2048
 cells={};count=0
 for seq,cell,owner,sid,cid,proof,pcm,state,revision in db.execute('SELECT * FROM cells ORDER BY seq'):
  assert seq==count+1 and cell==h(('cell-%04d'%count).encode()) and owner==h(('ownership-%04d'%count).encode())
  assert sid=='synthetic' and cid==h(('source-%04d'%count).encode()) and state=='retained' and revision==1
  assert len(pcm) in (640000,65536,4096,256,2) and pcm==b'A'*len(pcm)
  assert proof==b'{"synthetic":true,"pad":"'+b'A'*min(len(pcm),65500)+b'"}' and len(proof)<=65536
  cells[cell]=(cid,len(pcm)//2);count+=1
 assert count==55
 deps=0
 for cell,pos,cid,digest,receipt_id,ss,se,ls,le in db.execute('SELECT * FROM dependencies'):
  assert pos==0 and cells[cell]==(cid,se) and ss==ls==0 and se==le and 0<se<=320000
  assert digest=='a'*64 and receipt_id==source_receipts[cid];deps+=1
 assert deps==55 and db.execute('SELECT * FROM sqlite_sequence').fetchall()==[('cells',55)]
 # Independent canonical typed streaming implementation, matching published oracle format.
 orders={'store_meta':'id','sources':'cid','cells':'seq','dependencies':'cell_id,position','sqlite_sequence':'name','sqlite_master':'type,name'}
 oracle={'schema_sha256':h(json.dumps(schema,separators=(',',':')).encode()),'tables':{}}
 for table,order in orders.items():
  cur=db.execute('SELECT * FROM '+table+' ORDER BY '+order)
  columns=[v[0] for v in cur.description];digest=hashlib.sha256();n=0
  for row in cur:
   values=[]
   for v in row:
    if v is None:encoded=['null']
    elif isinstance(v,bytes):encoded=['blob',len(v),h(v)]
    elif isinstance(v,str):encoded=['text',v]
    elif isinstance(v,int):encoded=['integer',str(v)]
    elif isinstance(v,float):encoded=['real',v.hex()]
    else:raise AssertionError(type(v))
    values.append(encoded)
   digest.update(json.dumps(values,separators=(',',':')).encode());digest.update(bytes([10]));n+=1
  oracle['tables'][table]={'columns':columns,'rows':n,'typed_sha256':digest.hexdigest()}
 assert oracle==r['typed_oracle']==json.loads(read(E/'baseline/oracle.json'))==json.loads(read(E/'recovery/oracle.json'))
 source=db.execute('SELECT sqlite_source_id()').fetchone()[0]
 options=sorted(x[0] for x in db.execute('PRAGMA compile_options'))
finally:db.close()
ident=r['identity'];assert ident==r['identity_after']
assert source==ident['sqlite_source_id'] and sqlite3.sqlite_version==ident['sqlite_version']
assert h(json.dumps(options,separators=(',',':'),ensure_ascii=False).encode())==ident['compile_options_sha256']
package=Path(sqlite3.__file__).resolve().parent
leaves=list(package.glob('*.py'))+list((package/'__pycache__').glob('*.pyc'))
actual={str(p.relative_to(package)):{'bytes':p.stat().st_size,'sha256':h(read(p,256*1024))} for p in leaves}
assert actual==ident['package_inventory']
fp=ident['fingerprints'];assert h(json.dumps(actual,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())==fp['sqlite3_package_sha256']
import _sqlite3
from importlib.machinery import BuiltinImporter
assert sys.platform=='linux' and _sqlite3.__spec__.origin=='built-in' and _sqlite3.__spec__.loader is BuiltinImporter
assert getattr(_sqlite3,'__file__',None) is None and b'libsqlite3' not in read(Path('/proc/self/maps'),256*1024)
assert fp['sqlite3_binding']=='observed-built-in-no-separate-sqlite-mapping-v1'
assert all(fp[key] is None for key in ('sqlite3_extension_sha256','sqlite3_library_sha256','sqlite3_library_realpath_sha256'))
exe=Path(sys.executable).resolve();assert h(read(exe))==fp['interpreter_sha256'] and exe.stat().st_size==fp['interpreter_bytes']
assert h(os.fsencode(exe))==fp['interpreter_realpath_sha256']
assert E.stat().st_dev==ident['filesystem_device']
assert all(h(read(R/name))==digest for name,digest in r['source_manifest'].items())
assert h(read(R/'qualification/cellstore_crash_probe.py'))==r['harness_sha256']
assert h(read(R/'audio_backend/native_owned_cell_store.py'))==r['source_module_sha256']
for name in ('baseline/fill.json','recovery/recover.json'):
 child=json.loads(read(E/name));assert child['closed'] is True and child['identity']==ident
assert json.loads(read(E/'attempt-1/before_commit.json'))=={'identity':ident,'stage':'before_commit'}
assert not (E/'attempt-1/missed_commit.json').exists()
waits=r['original_waits'];assert [(x['phase'],x['returncode'],x['original_wait']) for x in waits]==[('fill',0,True),('crash-captured',-9,True),('recover',0,True)]
assert len({x['pid'] for x in waits})==3
namespace=E/'attempt-1/store'
assert {x.name for x in namespace.iterdir()}=={'cells.sqlite3','cells.sqlite3-journal'}
allocated=max(namespace.stat().st_size,namespace.stat().st_blocks*512)+sum(max(p.stat().st_size,p.stat().st_blocks*512) for p in namespace.iterdir())
slack=max(0,allocated-len(damaged)-len(journal));assert allocated<=58*1024**2 and slack<=2*1024**2
assert allocated==r['crash_allocation']['charged_bytes'] and slack==r['crash_allocation']['slack_bytes']
assert sum(p.stat().st_blocks*512 for p in E.rglob('*') if p.is_file())<=256*1024**2
assert inventory()==before
report=dict(status='accepted_production_schema_saved_byte_evidence',production_activation_approved=False,
 changed_pages=len(changed),journal_records=nrec,sector_bytes=sector,allocated_pair_bytes=allocated,slack_bytes=slack,
 baseline_sha256=h(baseline),runtime_package_sha256=fp['sqlite3_package_sha256'],exact_recovery=True,
 all_rows_checked=count,evidence_unchanged=True,source_runtime_inventory_matches=True,
 lifecycle_receipts_consistent=True,lifecycle_independently_reobserved=False,
 limitations=['Production DDL with synthetic opaque proofs, not recorder provenance or primary authority','Process crash, not power-loss durability','Sampled allocation does not prove instantaneous peak','Original child stop/wait events are historical receipts, not independently re-observed','No native model/service or profile activation'],
 audit_script_sha256=h(read(Path(__file__))),evidence_inventory=before)
(O/'REPORT.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='evidence_inventory'},sort_keys=True))
