import hashlib,os,resource,signal,sys
from pathlib import Path
b=Path('/workspace/scratch/12ec3121e93f');p=b/'backend-dev73-fresh-proof-20261105-0214';s=b/'backend-dev73-fresh-source-20261105-0214'
h=lambda x:hashlib.sha256(x.read_bytes()).hexdigest()
os.environ.update(TARGET_OBSERVATION=str(p/'target.json'),TARGET_SHA256=h(p/'target.json'),TARGET_AUDIT=str(p/'cellstore-audit.json'),TARGET_AUDIT_SHA256=h(p/'cellstore-audit.json'),TARGET_EVIDENCE=str(p/'cellstore'),TARGET_RESULT_SHA256=h(p/'cellstore/RESULT.json'))
os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]));resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3));resource.setrlimit(resource.RLIMIT_CPU,(90,100));signal.alarm(120)
sys.path[:0]=[str(b/'audio-recovery-test-tools-20261023b'),str(s),str(s/'tests')]
import pytest
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider',str(s/'qualification/test_target_candidate_cellstore.py'),'--basetemp',str(p/'tests01'),'--junitxml',str(p/'candidate-tests.xml')]))
