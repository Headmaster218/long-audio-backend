import hashlib,os,resource,signal,sys
from pathlib import Path
b=Path('/workspace/scratch/12ec3121e93f');p=b/'backend-dev73-fresh-proof-20261105-0214';s=b/'backend-dev73-fresh-source-20261105-0214'
h=lambda x:hashlib.sha256(x.read_bytes()).hexdigest()
os.sched_setaffinity(0,set(sorted(os.sched_getaffinity(0))[:2]));resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3));resource.setrlimit(resource.RLIMIT_CPU,(90,100));signal.alarm(120)
sys.path[:0]=[str(b/'audio-recovery-test-tools-20261023b'),str(s),str(s/'tests')]
import pytest
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider',str(s/'qualification/test_materialize_target_candidate.py'),str(s/'qualification/test_verify_installed_target.py'),'--basetemp',str(p/'negative-tests01'),'--junitxml',str(p/'negative-tests.xml')]))
