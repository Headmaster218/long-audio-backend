# PUBLIC SOURCE INSPECTION ONLY. This is not a runnable distribution.
"""Same qualified dev66 product and exact previously issued qualified root artifact.

Source contract, not a package pass or launch authorization. No runtime/model,
quota, environment, inherited-lock, or ordinary startup exception is implied.
"""

raise RuntimeError("Inspection-only transformed excerpt: do not execute or import; private dependencies and pins are intentionally omitted.")

import hashlib,json
from pathlib import Path

FROM_VERSION='0.2.0rc3.dev65'
TO_VERSION='0.2.0rc3.dev66'
ALLOWED=frozenset({'audio_backend/native_root_reattestation.py',
    'audio_backend/app.py','audio_backend/__main__.py',
    'audio_backend/native_owned_cell_capture.py',
    'audio_backend/native_owned_cell_store.py','audio_backend/__init__.py'})
SOURCE_ALLOWED=ALLOWED|{'pyproject.toml','tests/test_native_root_reattestation.py','docs/NATIVE_ROOT_REATTESTATION.md'}
FLAGS=('--native-root-reattestation','--native-root-reattestation-sha256')

def require(ok,reason):
    if not ok: raise RuntimeError(reason)

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def pinned_json(release,path):
    require(type(path) is str and path in release['source_pins'],'Missing reviewed evidence pin')
    p=Path(path)
    require(p.is_absolute() and p.resolve()==p and not p.is_symlink(),'Evidence path confinement')
    data=p.read_bytes()
    require(hashlib.sha256(data).hexdigest()==release['source_pins'][path],'Reviewed evidence changed')
    return json.loads(data)

def mapped(value,old,new):
    if isinstance(value,str):return value.replace(old,new)
    if isinstance(value,list):return [mapped(x,old,new) for x in value]
    if isinstance(value,dict):return {k:mapped(v,old,new) for k,v in value.items()}
    return value

def inventory(root):
    root=Path(root);rows={}
    require(root.is_absolute() and root.resolve()==root and root.is_dir(),'Installed root')
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(),'Installed symlink')
        if path.is_file():rows[str(path.relative_to(root))]=digest(path)
        require(len(rows)<=512,'Installed file bound')
    return rows

def package_delta(before,after):
    changed={k for k in set(before)|set(after) if before.get(k)!=after.get(k)}
    code={k for k in changed if k.startswith('audio_backend/')}
    require(code==ALLOWED,'Unexpected product code delta')
    metadata=tuple('long_audio_backend-'+v+'.dist-info/' for v in (FROM_VERSION,TO_VERSION))
    require(all(k.startswith('audio_backend/') or k.startswith(metadata) for k in changed),'Non-package installed delta')
    require(all(k in after for k in before if k.startswith('audio_backend/')),'Removed product code')
    require('audio_backend/native_root_reattestation.py' not in before,'Reattestation module must be new')
    return {k:{'before':before.get(k),'after':after.get(k)} for k in sorted(changed)}

def require_source_delta(source,base):
    require(source['version']==TO_VERSION and base['version']==FROM_VERSION,'Source version mismatch')
    require(source['installed_audio_backend_delta']==sorted(ALLOWED)
            and source['change_allowlist']==sorted(SOURCE_ALLOWED),'Frozen source allowlist changed')
    before,after=base['files'],source['files']
    changed={key for key in set(before)|set(after) if before.get(key)!=after.get(key)}
    require(changed==SOURCE_ALLOWED,'Unexpected frozen nine-file source delta')
    require(set(before)<=set(after),'Removed source file')

def require_server_argv(release,old,a,b):
    target=release['product_transition'];proof=target['native_root_reattestation']
    require(set(proof)=={'path','sha256'},'Exact reattestation pin required')
    path,sha=proof['path'],proof['sha256']
    require(type(sha) is str and len(sha)==64 and all(x in '0123456789abcdef' for x in sha)
            and release['source_pins'].get(path)==sha,'Missing exact reattestation source pin')
    original=old['controls']['server_argv']
    require(not any(arg.split('=',1)[0] in FLAGS for arg in original),'Prior argv already has reattestation flags')
    expected=mapped(original,a,b)+[FLAGS[0],path,FLAGS[1],sha]
    require(release['controls']['server_argv']==expected,'Only exact paired reattestation flags may be appended')
    return proof

def validate_initial_transition(release,old):
    target=release['product_transition']
    require(target['approved_exact_product_transition'] is True,'Product transition not approved')
    require(target['from_version']==FROM_VERSION and target['to_version']==TO_VERSION,'Version transition')
    require(old['controls']['package_version']==FROM_VERSION and release['controls']['package_version']==TO_VERSION,'Installed package version transition')
    a=target['old_installed'];b=target['new_installed']
    require(a==old['controls']['environment']['PYTHONPATH'] and b==release['controls']['environment']['PYTHONPATH'] and a!=b,'Installed transition target')
    delta=pinned_json(release,target['delta_manifest'])
    require(delta['old_installed']==a and delta['new_installed']==b,'Delta target mismatch')
    require(delta['status']=='passed' and delta['source_wheel_installed_equal'] is True,'Package audit not qualified')
    before=inventory(a);after=inventory(b)
    require(package_delta(before,after)==delta['changed_files'],'Package delta differs from reviewed manifest')
    source=pinned_json(release,target['source_manifest'])
    base_pin=source['base_manifest']
    require(set(base_pin)=={'path','sha256'} and release['source_pins'].get(base_pin['path'])==base_pin['sha256'],'Base source manifest provenance changed')
    base_source=pinned_json(release,base_pin['path'])
    require_source_delta(source,base_source)
    base_product={k:v['sha256'] for k,v in base_source['files'].items() if k.startswith('audio_backend/')}
    require(base_product=={k:v for k,v in before.items() if k.startswith('audio_backend/')},'Prior installed product differs from frozen source')
    source_product={k:v['sha256'] for k,v in source['files'].items() if k.startswith('audio_backend/')}
    require(source_product=={k:v for k,v in after.items() if k.startswith('audio_backend/')},'Installed product differs from frozen source')
    receipts=target['acceptance_receipts']
    require(set(receipts)=={'controls','package','package_wait'},'Product acceptance kinds')
    for kind,path in receipts.items():
        receipt=pinned_json(release,path)
        require(delta['acceptance_pins'].get(path)==release['source_pins'][path],'Package acceptance provenance changed')
        if kind=='package':
            stages=receipt['stages']
            require(receipt['status']=='passed' and len(stages)==3 and {x['name'] for x in stages}=={'wheel','install','smoke'}
                    and all(type(x['exit_code']) is int and x['exit_code']==0 and x['positive_original_wait'] is True for x in stages),'Installed acceptance failed')
        else:
            require(type(receipt['returncode']) is int and receipt['returncode']==0 and receipt['positive_original_wait'] is True,'Product acceptance failed')
    require(delta['acceptance_pins'].get(target['source_manifest'])==release['source_pins'][target['source_manifest']],'Source acceptance provenance changed')
    proof=require_server_argv(release,old,a,b)
    artifact=pinned_json(release,proof['path'])
    require(artifact['schema']=='native-root-reattestation-v1','Reattestation schema')
    require(artifact['baseline']=={'path':release['resume']['baseline_receipt'],'sha256':release['source_pins'][release['resume']['baseline_receipt']]},'Reattestation baseline mismatch')
    data=release['controls']['data']
    require(artifact['data_path']==data and artifact['primary_path']==release['database']
            and artifact['native_path']==str(Path(data)/'native-owned-cells-bounded-v1/cells.sqlite3')
            and artifact['lock_path']==str(Path(data)/'instance.lock'),'Reattestation target changed')
    runtime=json.loads(Path(release['runtime_manifest']['path']).read_bytes())
    require(digest(release['runtime_manifest']['path'])==release['runtime_manifest']['sha256'],'New runtime manifest changed')
    product={str(Path(k).relative_to(b)):v for k,v in runtime['files'].items() if Path(k).is_relative_to(b)}
    require(set(product)==set(after),'Incomplete installed runtime coverage')
    require(all(v['sha256']==after[k] and v['bytes']==(Path(b)/k).stat().st_size and v['resolved']==str(Path(b)/k) and v['hold'] is True for k,v in product.items()),'Installed runtime pin mismatch')
    prior_runtime=json.loads(Path(old['runtime_manifest']['path']).read_bytes())
    require(digest(old['runtime_manifest']['path'])==old['runtime_manifest']['sha256'],'Prior runtime manifest changed')
    require({k:v for k,v in prior_runtime.items() if k not in ('files','symlinks','scope')}=={k:v for k,v in runtime.items() if k not in ('files','symlinks','scope')},'Runtime manifest policy changed')
    for key in ('files','symlinks'):
        prior=prior_runtime.get(key,{});current=runtime.get(key,{})
        external_old={k:v for k,v in prior.items() if not Path(k).is_relative_to(Path(a))}
        external_new={k:v for k,v in current.items() if not Path(k).is_relative_to(Path(b))}
        require(external_old==external_new,'Runtime/model/decoder manifest changed outside product')
        if key=='symlinks':require(not any(Path(k).is_relative_to(b) for k in current),'Installed runtime symlinks prohibited')
    return a,b


def require_continued_artifact(release,old):
    """A progressed checkpoint is not authority to issue or retarget an artifact."""
    proof=release['product_transition']['native_root_reattestation']
    require(proof==old['product_transition']['native_root_reattestation']
            and proof['sha256']=='REDACTED_PRIVATE_EVIDENCE_SHA256'
            and release['source_pins'].get(proof['path'])==proof['sha256'],
            'Continuation must retain the exact qualified qualified root artifact')
    artifact=pinned_json(release,proof['path'])
    anchor_pin=artifact['baseline']
    require(anchor_pin==dict(path=old['resume']['baseline_receipt'],
                sha256='REDACTED_PRIVATE_EVIDENCE_SHA256')
            and release['source_pins'].get(anchor_pin['path'])==anchor_pin['sha256'],
            'Artifact baseline14 bytes cannot be replaced by the progressed baseline')
    anchor=pinned_json(release,anchor_pin['path'])
    current=pinned_json(release,release['resume']['baseline_receipt'])
    require(release['resume']['baseline_receipt']!=anchor_pin['path'],
            'Current lost-run checkpoint must be distinct from artifact issuance baseline')
    require(current['anchor_baseline']==dict(**anchor_pin,required_current_root=[REDACTED_ROOT_DEVICE, REDACTED_ROOT_INODE],
                shared_anchors_unchanged=True,source_bytes_may_have_progressed=True)
            and current['retained_root_artifact']==proof,'Current baseline anchor linkage changed')
    require(current['native_binding']==anchor['native_binding']
            and current['native_ancestry_before']==current['native_ancestry_after']
            ==anchor['native_ancestry_before']==anchor['native_ancestry_after']
            and current['ownership_locks']==anchor['ownership_locks'],
            'Reused artifact binding, ancestry or ownership identity changed')
    names=('schema','policy','primary_identity','qualification','namespace_identity','database_identity')
    binding=[current['native_binding']['stored'][key] for key in names]
    require(binding==artifact['old_binding'] and hashlib.sha256(
                json.dumps(binding,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')).hexdigest()
            ==artifact['old_binding_sha256'],'Artifact stored binding changed')
    ancestry=current['native_ancestry_before'];root=ancestry['ancestors'][0]
    require(root['path']=='/' and [root['device'],root['inode']]==artifact['new_root']==[REDACTED_ROOT_DEVICE, REDACTED_ROOT_INODE]
            and current['ownership_locks']['instance'][:2]==artifact['lock_identity'],
            'Exact qualified root or inherited instance lock changed')
    data=release['controls']['data']
    require(artifact['data_path']==data and artifact['primary_path']==release['database']
            and artifact['native_path']==str(Path(data)/'native-owned-cells-bounded-v1/cells.sqlite3')
            and artifact['lock_path']==str(Path(data)/'instance.lock'),'Retargeted reused artifact')
    evidence=current['source_evidence'];root_path=Path(release['run_root'])
    identities={kind:evidence[str(Path(artifact[kind+'_path']).relative_to(root_path))]['identity'][:2]
                for kind in ('primary','native','lock')}
    require(identities['primary']==[ancestry['primary']['device'],ancestry['primary']['inode']]
            and identities['native']==json.loads(current['native_binding']['stored']['database_identity'])
            and identities['lock']==artifact['lock_identity'],
            'Progressed source identities differ from reused artifact anchors')
    return artifact


def validate_previous_continuation(release,old):
    """No new product transition: re-prove and retain run20's qualification."""
    require(old['controls']['package_version']==release['controls']['package_version']==TO_VERSION,
            'Same qualified dev66 product required')
    require(release['product_transition']==old['product_transition'],
            'Historical product qualification cannot change on continuation')
    require(release['runtime_manifest']==old['runtime_manifest'],
            'Same-product continuation runtime manifest changed')
    require(release['controls']['server_argv']==old['controls']['server_argv'],
            'Same-product continuation server argv changed')
    for path,sha in old['source_pins'].items():
        require(release['source_pins'].get(path)==sha,'Prior qualified source pin lost or changed')
    predecessor=pinned_json(release,old['resume']['previous_release'])
    _,installed=validate_initial_transition(old,predecessor)
    require(release['controls']['environment']['PYTHONPATH']==installed,
            'Same-product installed path changed')
    require_continued_artifact(release,old)
    return installed,installed


def validate_loss23_continuation(release,old):
    """Exact run23 -> run20 -> original dev65 qualification, never recursion."""
    require(Path(old['output']).name=='prior-segment-output-E'
            and old['controls']['package_version']==release['controls']['package_version']==TO_VERSION,
            'Latest qualified loss23 predecessor required')
    require(release['product_transition']==old['product_transition']
            and release['runtime_manifest']==old['runtime_manifest']
            and release['controls']['server_argv']==old['controls']['server_argv'],
            'Same-product loss23 continuation qualification/runtime/argv changed')
    for path,sha in old['source_pins'].items():
        require(release['source_pins'].get(path)==sha,'Prior qualified source pin lost or changed')
    run20=pinned_json(release,old['resume']['previous_release'])
    require(Path(run20['output']).name=='prior-segment-output-D','Fixed product qualification lineage changed')
    installed,_=validate_previous_continuation(old,run20)
    require(release['controls']['environment']['PYTHONPATH']==installed,
            'Same-product installed path changed')
    # The issuance baseline belongs to run20, not to the immediate run23
    # predecessor. Neither a newer readback nor a lost executor reissues it.
    require_continued_artifact(release,run20)
    return installed,installed


def validate(release,old):
    """Exact run25 -> run23 -> run20 -> original dev65 qualification."""
    require(Path(old['output']).name=='prior-segment-output-F'
            and old['controls']['package_version']==release['controls']['package_version']==TO_VERSION,
            'Latest qualified loss25 predecessor required')
    require(release['product_transition']==old['product_transition']
            and release['runtime_manifest']==old['runtime_manifest']
            and release['controls']['server_argv']==old['controls']['server_argv'],
            'Same-product loss25 continuation qualification/runtime/argv changed')
    for path,sha in old['source_pins'].items():
        require(release['source_pins'].get(path)==sha,'Prior qualified source pin lost or changed')
    run23=pinned_json(release,old['resume']['previous_release'])
    require(Path(run23['output']).name=='prior-segment-output-E','Fixed product qualification lineage changed')
    installed,_=validate_loss23_continuation(old,run23)
    require(release['controls']['environment']['PYTHONPATH']==installed,'Same-product installed path changed')
    run20=pinned_json(release,run23['resume']['previous_release'])
    require(Path(run20['output']).name=='prior-segment-output-D','Fixed artifact qualification lineage changed')
    require_continued_artifact(release,run20)
    return installed,installed
