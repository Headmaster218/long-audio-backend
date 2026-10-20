# PUBLIC INSPECTION COPY. Do not execute or import.
"""Exact package-only dev64 -> dev65 transition; no model/runtime/quota widening."""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import hashlib,json
from pathlib import Path

ALLOWED = {'audio_backend/resources.py','audio_backend/app.py','audio_backend/__main__.py','audio_backend/recording_uploader.py','audio_backend/recording_uploader_history.py','audio_backend/__init__.py'}

def require(ok,reason):
    if not ok: raise RuntimeError(reason)

def digest(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()

def mapped(value,old,new):
    if isinstance(value,str):return value.replace(old,new)
    if isinstance(value,list):return [mapped(x,old,new) for x in value]
    if isinstance(value,dict):return {k:mapped(v,old,new) for k,v in value.items()}
    return value

def inventory(root):
    root=Path(root); rows={}
    require(root.is_absolute() and root.resolve()==root and root.is_dir(),'Installed root')
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(),'Installed symlink')
        if path.is_file(): rows[str(path.relative_to(root))]=digest(path)
        require(len(rows)<=512,'Installed file bound')
    return rows

def package_delta(before,after):
    changed={k for k in set(before)|set(after) if before.get(k)!=after.get(k)}
    code={k for k in changed if k.startswith('audio_backend/')}
    require(code==ALLOWED,'Unexpected product code delta')
    require(all(k.startswith('audio_backend/') or '.dist-info/' in k for k in changed),'Non-package installed delta')
    require(all(k in after for k in before if k.startswith('audio_backend/')),'Removed product code')
    return {k:{'before':before.get(k),'after':after.get(k)} for k in sorted(changed)}

def validate(release,old):
    target=release['product_transition']
    require(target['approved_exact_product_transition'] is True,'Product transition not approved')
    require(target['from_version']=='0.2.0rc3.dev64' and target['to_version']=='0.2.0rc3.dev65','Version transition')
    a=target['old_installed']; b=target['new_installed']
    require(a==old['controls']['environment']['PYTHONPATH'] and b==release['controls']['environment']['PYTHONPATH'] and a!=b,'Installed transition target')
    require(target['delta_manifest'] in release['source_pins'],'Missing reviewed delta pin')
    delta=json.loads(Path(target['delta_manifest']).read_bytes())
    require(delta['old_installed']==a and delta['new_installed']==b,'Delta target mismatch')
    before=inventory(a);after=inventory(b)
    require(package_delta(before,after)==delta['changed_files'],'Package delta differs from reviewed manifest')
    receipts=target['acceptance_receipts']
    require(set(receipts)=={'controls','package','package_wait'},'Product acceptance kinds')
    for kind,path in receipts.items():
        require(path in release['source_pins'],'Missing product acceptance receipt pin')
        receipt=json.loads(Path(path).read_bytes())
        if kind=='package':
            require(receipt['status']=='passed' and {x['name'] for x in receipt['stages']}=={'wheel','install','smoke'} and all(x['exit_code']==0 and x['positive_original_wait'] is True for x in receipt['stages']),'Installed acceptance failed')
        else:
            require(receipt['returncode']==0 and receipt['positive_original_wait'] is True,'Product acceptance failed')
    runtime=json.loads(Path(release['runtime_manifest']['path']).read_bytes())
    product={str(Path(k).relative_to(b)):v for k,v in runtime['files'].items() if Path(k).is_relative_to(b)}
    require(set(product)==set(after),'Incomplete installed runtime coverage')
    require(all(v['sha256']==after[k] and v['bytes']==(Path(b)/k).stat().st_size and v['resolved']==str(Path(b)/k) and v['hold'] is True for k,v in product.items()),'Installed runtime pin mismatch')
    for key in ('files','symlinks'):
        prior=json.loads(Path(old['runtime_manifest']['path']).read_bytes()).get(key,{})
        current=json.loads(Path(release['runtime_manifest']['path']).read_bytes()).get(key,{})
        # New product is allowed; every external path/value must be identical.
        external_old={k:v for k,v in prior.items() if not Path(k).is_relative_to(Path(a))}
        external_new={k:v for k,v in current.items() if not Path(k).is_relative_to(Path(b))}
        require(external_old==external_new,'Runtime/model/decoder manifest changed outside product')
    return a,b
