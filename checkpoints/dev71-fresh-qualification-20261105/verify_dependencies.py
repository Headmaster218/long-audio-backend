import os,sys,json,hashlib,importlib,importlib.metadata as m,importlib.util,re,sqlite3,zipfile,email
from pathlib import Path
os.umask(0o077)
b=Path('/workspace/scratch/12ec3121e93f');p=Path(__file__).parent
norm=lambda n:re.sub(r'[-_.]+','-',n).lower()
h=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
lock=p/'Linux-py313-runtime-and-receiver.lock.txt'
expected={norm(x.split('==')[0]):x.split('==')[1].split()[0] for x in lock.read_text().splitlines() if x and not x.startswith('#')}
actual={norm(d.metadata['Name']):d.version for d in m.distributions()}
assert actual==expected,(actual,expected)
modules=['annotated_doc','annotated_types','anyio','cffi','_cffi_backend','click','fastapi','h11','idna','numpy','opencc','opentelemetry.trace','psutil','pycparser','pydantic','pydantic_core','pydantic_settings','dotenv','python_multipart','sherpa_onnx','soundfile','starlette','transcribe_cpp','typing_extensions','typing_inspection','uvicorn','pyftpdlib','pyftpdlib.handlers','pyftpdlib.servers','OpenSSL','cryptography','asyncore','asynchat']
for name in modules:importlib.import_module(name)
from OpenSSL import SSL
SSL.Context(SSL.TLS_SERVER_METHOD)
assert importlib.util.find_spec('audio_backend') is None
wheels=[]
for directory in [b/'backend-dynamic-proof-20261104/wheels',b/'backend-operational-proof-20261104/receiver-wheels']:
 for f in sorted(directory.glob('*.whl')):
  with zipfile.ZipFile(f) as z:
   names=[x for x in z.namelist() if x.endswith('.dist-info/METADATA')];assert len(names)==1
   meta=email.message_from_bytes(z.read(names[0]))
  name=norm(meta['Name']);version=meta['Version']
  if expected.get(name)==version:wheels.append(dict(name=name,version=version,path=str(f),sha256=h(f),bytes=f.stat().st_size))
unique={}
for row in wheels:
 key=row['name']
 if key in unique:assert unique[key]['sha256']==row['sha256']
 else:unique[key]=row
assert len(unique)==len(expected)==32
wheels=list(unique.values())
result=dict(schema='fresh-final-dependency-environment-v1',passed=True,environment=str(Path(sys.prefix)),python=sys.version,sqlite_version=sqlite3.sqlite_version,combined_lock_sha256=h(lock),packages=actual,imports_passed=modules,selected_wheels=wheels,product_installed=False,models_loaded=False,service_started=False,network_used=False,secondary_metadata_recheck='historically not completed; no new network verification claimed',pyftpdlib_origin='previous locally built wheel from hash-verified official sdist; see copied provenance')
(p/'DEPENDENCY_RESULT.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
print(json.dumps(dict(passed=True,packages=len(actual),imports=len(modules),python=sys.version.split()[0],sqlite=sqlite3.sqlite_version,product_installed=False)))
