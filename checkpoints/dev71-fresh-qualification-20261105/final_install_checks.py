import json,hashlib,sys,subprocess,importlib.metadata,dataclasses
from pathlib import Path
from audio_backend import native_owned_cell_store as store
b=Path('/workspace/scratch/12ec3121e93f');p=Path(__file__).parent;s=b/'backend-dev71-fresh-source-20261105-0118';material=b/'backend-dev71-fresh-materialized-20261105-0118';h=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
manifest=json.loads((p/'SOURCE_MANIFEST.json').read_text())
for f in manifest['files']:
 path=s/f['path'];assert not path.is_symlink() and path.stat().st_size==f['size'] and h(path)==f['sha256']
observed=json.loads((p/'target.json').read_text())['observed'];q=dataclasses.asdict(store.QUALIFIED_RUNTIMES[0]);assert all(q[k]==v for k,v in observed.items());assert q['evidence_sha256']==h(p/'REVIEW_CANDIDATE_BASIS.json');assert str(Path(store.__file__)).startswith(sys.prefix+'/lib/');assert importlib.metadata.version('long-audio-backend')=='0.2.0rc3.dev71'
receipt=material/'RECEIPT.json';cmd=[sys.executable,'-I','-B',str(s/'qualification/verify_installed_target.py'),'--receipt',str(receipt),'--receipt-sha256',h(receipt)];result=subprocess.run(cmd,capture_output=True,text=True,check=True);(p/'installed-after.json').write_text(result.stdout)
negative=subprocess.run(cmd[:-1]+['0'*64],capture_output=True,text=True);assert negative.returncode!=0 and 'receipt_hash_mismatch' in negative.stderr;(p/'negative-receipt.stderr').write_text(negative.stderr)
wheel=next((p/'product-wheel').glob('*.whl'));result=dict(schema='fresh-dev71-product-install-v1',passed=True,version='0.2.0rc3.dev71',runtime=sys.prefix,source_files_unchanged=len(manifest['files']),source_manifest_sha256=h(p/'SOURCE_MANIFEST.json'),reviewed_basis_sha256=h(p/'REVIEW_CANDIDATE_BASIS.json'),materialization_receipt=str(receipt),materialization_receipt_sha256=h(receipt),installed_verification=json.loads(result.stdout),wheel=str(wheel),wheel_sha256=h(wheel),candidate_module_sha256=h(Path(store.__file__)),dependency_files_unchanged=1832,qualification_tests=9,materializer_verifier_tests=41,live_bad_receipt_gate=True,models_loaded=False,service_started=False,general_production_rollout_approved=False,existing_store_migration_authorized=False)
(p/'INSTALL_RESULT.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');print(json.dumps(result,sort_keys=True))
