import os,sys,json,hashlib,subprocess,resource,signal
from pathlib import Path
os.umask(0o077);os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]))
b=Path('/workspace/scratch/12ec3121e93f');p=b/'backend-dev71-fresh-proof-20261105-0118';s=b/'backend-dev71-fresh-source-20261105-0118';py=b/'backend-dev71-fresh-runtime-20261105-0118/bin/python'
h=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
assert h(p/'SOURCE_MANIFEST.json')=='a073aed6b9281af15965f0829106a7aeb1bb21047d6551a6fdcc727b84481272'
m=json.loads((p/'SOURCE_MANIFEST.json').read_text())
def verify():
 for f in m['files']:
  path=s/f['path'];assert not path.is_symlink() and path.stat().st_size==f['size'] and h(path)==f['sha256']
verify();(p/'target-data').mkdir(mode=0o700)
commands=[]
def run(name,args,timeout):
 command=[str(py),'-I','-B',*map(str,args)];commands.append(command)
 (p/'qualification-commands.json').write_text(json.dumps(commands,indent=2)+'\n')
 with (p/(name+'.stdout')).open('w') as out,(p/(name+'.stderr')).open('w') as err:
  result=subprocess.run(command,stdout=out,stderr=err,timeout=timeout,cwd=s)
 assert result.returncode==0,(name,result.returncode)
run('target-observation',[s/'qualification/target_runtime_observation.py','--data-dir',p/'target-data','--output',p/'target.json'],30)
target_hash=h(p/'target.json')
run('physical',[s/'qualification/cellstore_crash_probe.py',p/'cellstore','--target-observation',p/'target.json','--target-sha256',target_hash],180)
run('audit',[s/'qualification/audit_target_cellstore_evidence.py','--evidence',p/'cellstore','--source',s,'--report',p/'cellstore-audit.json','--target-observation',p/'target.json','--target-sha256',target_hash],120)
run('candidate-tests',[p/'run_candidate_tests.py'],130)
receipt=dict(schema='target-candidate-regression-receipt-v1',tests_passed=9,exit_code=0,production_profile_activated=False,
 target_sha256=target_hash,audit_sha256=h(p/'cellstore-audit.json'),physical_result_sha256=h(p/'cellstore/RESULT.json'),
 source_module_sha256=h(s/'audio_backend/native_owned_cell_store.py'),source_manifest_sha256=h(p/'SOURCE_MANIFEST.json'),
 source_test_sha256=h(s/'qualification/test_target_candidate_cellstore.py'),junit_sha256=h(p/'candidate-tests.xml'),
 limits='2 CPUs, 1 GiB AS, CPU90/100 sec, alarm120 sec')
import xml.etree.ElementTree as ET
root=ET.parse(p/'candidate-tests.xml').getroot();suites=list(root.iter('testsuite'))
assert sum(int(x.attrib.get('tests',0)) for x in suites)==9
assert all(int(x.attrib.get(k,0))==0 for x in suites for k in ('errors','failures','skipped'))
verify();(p/'candidate-tests-receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
print(json.dumps(dict(passed=True,awaiting_reviewed_basis=True,hashes={name:h(p/name) for name in ['target.json','cellstore/RESULT.json','cellstore-audit.json','candidate-tests-receipt.json','SOURCE_MANIFEST.json']}),sort_keys=True))
