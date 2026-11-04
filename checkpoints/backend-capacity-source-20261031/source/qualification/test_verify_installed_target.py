import hashlib,json
from pathlib import Path
import pytest
from qualification.verify_installed_target import verify

def fixture(tmp_path):
    root=tmp_path/'audio_backend';root.mkdir()
    files={'__init__.py':b'# package\n','native_owned_cell_store.py':b'# exact candidate\n'}
    for n,b in files.items():(root/n).write_bytes(b)
    receipt=dict(schema='isolated-target-materialization-receipt-v1',completed=True,
        production_profile_activated=False,service_started=False,
        candidate_files={'audio_backend/'+n:hashlib.sha256(b).hexdigest() for n,b in files.items()})
    path=tmp_path/'receipt.json';path.write_text(json.dumps(receipt));digest=hashlib.sha256(path.read_bytes()).hexdigest()
    return root,path,digest

def test_exact_package_and_cache(tmp_path):
    root,path,digest=fixture(tmp_path);cache=root/'__pycache__';cache.mkdir();(cache/'example.pyc').write_bytes(b'cache')
    with pytest.raises(ValueError,match='bytecode'):verify(root,path,digest)

@pytest.mark.parametrize('change',['extra','missing','modified','symlink','digest'])
def test_refuses_modified_install(tmp_path,change):
    root,path,digest=fixture(tmp_path)
    if change=='extra':(root/'other.py').write_text('extra')
    if change=='missing':(root/'__init__.py').unlink()
    if change=='modified':(root/'__init__.py').write_text('changed')
    if change=='symlink':(root/'alias').symlink_to(path)
    if change=='digest':digest='0'*64
    with pytest.raises(ValueError):verify(root,path,digest)

def test_exact_fresh_package(tmp_path):
    root,path,digest=fixture(tmp_path)
    assert verify(root,path,digest)['product_files']==2
