import sys, zipfile, stat
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from setup_native_diarization import safe_zip_extract

@pytest.mark.parametrize('name',['../escape.dll','/absolute.dll','C:/escape.dll','..\\escape.dll'])
def test_zip_rejects_traversal(tmp_path,name):
    archive=tmp_path/'bad.zip';dest=tmp_path/'out';dest.mkdir()
    with zipfile.ZipFile(archive,'w') as z:z.writestr(name,b'bad')
    with pytest.raises(RuntimeError):safe_zip_extract(archive,dest)
    assert not (tmp_path/'escape.dll').exists()

def test_zip_rejects_symlink(tmp_path):
    archive=tmp_path/'bad.zip';dest=tmp_path/'out';dest.mkdir()
    member=zipfile.ZipInfo('link');member.external_attr=(stat.S_IFLNK|0o777)<<16
    with zipfile.ZipFile(archive,'w') as z:z.writestr(member,'/outside')
    with pytest.raises(RuntimeError):safe_zip_extract(archive,dest)

def test_zip_retains_notices(tmp_path):
    archive=tmp_path/'good.zip';dest=tmp_path/'out';dest.mkdir()
    with zipfile.ZipFile(archive,'w') as z:z.writestr('runtime/LICENSE.txt','copyright notice')
    safe_zip_extract(archive,dest)
    assert (dest/'runtime/LICENSE.txt').read_text()=='copyright notice'
