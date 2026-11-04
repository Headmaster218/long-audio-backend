"""Finite parser/refusal controls and opt-in-existing Linux runtime observations."""
from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest
from audio_backend import sqlite_dynamic_identity as dynamic
from audio_backend import native_owned_cell_store as store
from qualification import target_runtime_observation as target


def elf(*, relocation_kind=7, symbol_kind=2, symbol_binding=1, version=None):
    raw=bytearray(8192)
    names=['sqlite3_open_v2','sqlite3_prepare_v2','sqlite3_libversion']
    strings=b'\0'+b'\0'.join(n.encode() for n in names)+b'\0'
    tags=[(5,0x500),(6,0x600),(10,len(strings)),(7,0x800),(8,72),(9,24),
          (23,0x900),(2,0),(20,7),(11,24)]
    if version is not None:tags.append((0x6ffffff0,0x700))
    tags.append((0,0))
    ident=b'\x7fELF\x02\x01\x01'+b'\0'*9
    struct.pack_into('<16sHHIQQQIHHHHHH',raw,0,ident,3,62,1,0,64,0,0,64,56,2,0,0,0)
    struct.pack_into('<IIQQQQQQ',raw,64,1,7,0,0,0,len(raw),len(raw),4096)
    struct.pack_into('<IIQQQQQQ',raw,120,2,6,0x200,0x200,0,len(tags)*16,len(tags)*16,8)
    for i,entry in enumerate(tags):struct.pack_into('<qQ',raw,0x200+i*16,*entry)
    raw[0x500:0x500+len(strings)]=strings
    offset=1
    for i,name in enumerate(names,1):
        struct.pack_into('<IBBHQQ',raw,0x600+i*24,offset,(symbol_binding<<4)|symbol_kind,0,0,0,0)
        struct.pack_into('<QQq',raw,0x800+(i-1)*24,0x1000+i*8,(i<<32)|relocation_kind,0)
        if version is not None:struct.pack_into('<H',raw,0x700+i*2,version)
        offset+=len(name)+1
    return raw


def test_dynamic_table_enumerates_every_sqlite_slot():
    assert dynamic.elf_sqlite_relocations(elf())==[(0x1008,'sqlite3_open_v2'),
        (0x1010,'sqlite3_prepare_v2'),(0x1018,'sqlite3_libversion')]
    assert len(dynamic.elf_sqlite_relocations(elf(relocation_kind=6)))==3


@pytest.mark.parametrize('kwargs',[{'relocation_kind':5},{'relocation_kind':37},
    {'symbol_kind':10},{'symbol_kind':1},{'symbol_binding':0},{'symbol_binding':2},
    {'symbol_binding':10},{'version':2},{'version':0x8001}])
def test_unsupported_actual_sqlite_relocation_refused(kwargs):
    with pytest.raises(store.Unavailable,match='sqlite_relocation'):
        dynamic.elf_sqlite_relocations(elf(**kwargs))


@pytest.mark.parametrize('change',[
    lambda b:b.__setitem__(4,1),lambda b:b.__setitem__(5,2),
    lambda b:struct.pack_into('<H',b,18,183),
    lambda b:struct.pack_into('<H',b,56,65535),
    lambda b:struct.pack_into('<Q',b,112,3),
    lambda b:struct.pack_into('<Q',b,0x800,2**63),
    lambda b:struct.pack_into('<Q',b,0x808,(5000<<32)|7),
    lambda b:struct.pack_into('<q',b,0x810,1),
    lambda b:struct.pack_into('<qQ',b,0x200+16,5,0x500),
])
def test_malformed_elf_refused(change):
    raw=elf();change(raw)
    with pytest.raises(store.Unavailable):dynamic.elf_sqlite_relocations(raw)


@pytest.mark.parametrize('raw',[b'bad',b'2000-1000 r-xp 0000 00:01 2 /x',
    b'1000-2000 rwxp 0000 00 2 /x',
    b'1000-2000 r-xp 0000 00:01 2 /x\n1800-3000 r-xp 0000 00:01 2 /x'])
def test_malformed_maps_refused(raw):
    with pytest.raises(store.Unavailable):dynamic.parse_maps(raw)


def test_mapping_identity_and_deleted_alias_refused(tmp_path):
    path=tmp_path/'object.so';path.write_bytes(b'object');info=path.stat()
    mapping=dynamic.Mapping(4096,8192,'r-xp',0,(os.major(info.st_dev),os.minor(info.st_dev)),info.st_ino,str(path))
    assert dynamic.file_group([mapping],path,info)==(mapping,)
    with pytest.raises(store.Unavailable,match='mapping_alias'):
        dynamic.file_group([replace(mapping,path=str(path)+' (deleted)')],path,info)
    with pytest.raises(store.Unavailable,match='mapping_missing'):
        dynamic.file_group([replace(mapping,inode=info.st_ino+1)],path,info)
    with pytest.raises(store.Unavailable,match='mapping_ambiguous'):
        dynamic.at_address([mapping],8190,8,'r')


@pytest.mark.parametrize('fail',[False,True])
def test_noload_handle_always_closed(monkeypatch,fail):
    opened=[];closed=[]
    def create(path,mode):
        opened.append((path,mode));return SimpleNamespace(_handle=1234)
    monkeypatch.setattr(dynamic.ctypes,'CDLL',create)
    monkeypatch.setattr(dynamic._ctypes,'dlclose',closed.append)
    try:
        with dynamic.loaded_object('/already-loaded.so'):
            if fail:raise RuntimeError('test')
    except RuntimeError:pass
    assert opened==[('/already-loaded.so',os.RTLD_NOLOAD|os.RTLD_LAZY)]
    assert closed==[1234]


def test_binding_schema_preserves_builtin_and_requires_dynamic_hashes():
    original=store.QUALIFIED_RUNTIMES[0]
    assert original.sqlite3_binding=='observed-built-in-no-separate-sqlite-mapping-v1'
    assert original.evidence_sha256=='d1eafbcf6bde0071d301f488ba82e69fa3bbadc446c62775276e4998efbdbe67'
    fields=('sqlite3_extension_sha256','sqlite3_library_sha256','sqlite3_library_realpath_sha256')
    dynamic_q=replace(original,sqlite3_binding=dynamic.BINDING,**{k:'a'*64 for k in fields})
    observed={k:asdict(dynamic_q)[k] for k in target.FIELDS}
    value=dict(schema=target.SCHEMA,production_profile_activated=False,observed=observed)
    assert target.validate(value)==observed
    for key in fields:
        with pytest.raises((store.Unavailable,ValueError,TypeError)):replace(dynamic_q,**{key:None})
        changed=dict(value,observed=dict(observed,**{key:None}))
        with pytest.raises(ValueError,match='binding'):target.validate(changed)


def test_existing_dynamic_interpreter_real_repeated_observation():
    executable=Path('/usr/bin/python3.13')
    if not executable.is_file():pytest.skip('existing independent dynamic interpreter unavailable')
    root=str(Path(__file__).resolve().parents[1])
    code='''
import sys,os,json,signal,resource
signal.alarm(30)
resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3))
sys.path.insert(0,ROOT)
from audio_backend import native_owned_cell_store as store
from audio_backend import sqlite_dynamic_identity as dynamic
first=store.runtime_fingerprints()
assert first['sqlite3_binding']==dynamic.BINDING
baseline=len(os.listdir('/proc/self/fd'))
original=dynamic._ctypes.dlclose;closed=[]
def close(handle):closed.append(handle);return original(handle)
dynamic._ctypes.dlclose=close
for _ in range(20):assert store.runtime_fingerprints()==first
assert len(closed)==40
assert len(os.listdir('/proc/self/fd'))==baseline
print(json.dumps({'repeat_calls':20,'balanced_noload_closes':len(closed),'fingerprints':first}))
'''.replace('ROOT',repr(root))
    completed=subprocess.run([str(executable),'-I','-B','-c',code],capture_output=True,text=True,timeout=35)
    assert completed.returncode==0,completed.stderr
    result=json.loads(completed.stdout)
    assert result['repeat_calls']==20 and result['balanced_noload_closes']==40


def test_actual_preload_interposition_refused(tmp_path):
    import shutil
    executable=Path('/usr/bin/python3.13');compiler=shutil.which('gcc')
    if not executable.is_file() or compiler is None:pytest.skip('existing dynamic runtime/compiler unavailable')
    source=tmp_path/'interposer.c';library=tmp_path/'interposer.so'
    # Deliberately never invoked: the verifier must refuse the actual resolved slot.
    source.write_text('int sqlite3_open_v2(const char *a, void **b, int c, const char *d) { return 21; }\n')
    build=subprocess.run([compiler,'-shared','-fPIC','-O2','-o',str(library),str(source)],
        capture_output=True,text=True,timeout=30)
    assert build.returncode==0,build.stderr
    root=str(Path(__file__).resolve().parents[1])
    code='''
import sys,signal,resource
signal.alarm(20)
resource.setrlimit(resource.RLIMIT_AS,(1024**3,1024**3))
sys.path.insert(0,ROOT)
from audio_backend.native_owned_cell_store import runtime_fingerprints,Unavailable
try:runtime_fingerprints()
except Unavailable as error:
 assert str(error) in ('runtime_sqlite_library_ambiguous','runtime_sqlite_symbol_interposed'),str(error)
 print(str(error))
else:raise AssertionError('interposed SQLite relocation was accepted')
'''.replace('ROOT',repr(root))
    env=dict(os.environ,LD_PRELOAD=str(library))
    completed=subprocess.run([str(executable),'-I','-B','-c',code],env=env,
        capture_output=True,text=True,timeout=25)
    assert completed.returncode==0,completed.stderr
    assert 'runtime_sqlite_' in completed.stdout
