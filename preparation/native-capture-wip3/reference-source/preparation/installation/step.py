"""Fixed offline substeps, always launched with the copied Python -I -B -S.
No arbitrary module, argument, pip requirement, or build frontend is accepted.
"""
import importlib.util
from pathlib import Path
import sys
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('native_install_common', HERE/'common.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
import importlib.metadata
import json
import os
import zipfile


def no_external_actions(event, args):
    if event.startswith('socket.'):
        raise PermissionError('offline substep denies socket action')
    if event.startswith(( 'subprocess.', 'os.exec', 'os.spawn')) or event in ('os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty', 'pty.spawn'):
        raise c.Refused('offline substep denied external action: '+event)


def installed_metadata():
    expected = {x['name'].lower().replace('_','-'):x['version'] for x in c.packages()}
    expected['long-audio-backend'] = '0.2.0rc3.dev3'
    actual = {}
    for dist in importlib.metadata.distributions(path=[str(c.staged_site())]):
        name = dist.metadata['Name'].lower().replace('_','-')
        c.require(name not in actual and name in expected and dist.version == expected[name], 'installed distribution mismatch')
        actual[name] = {'version':dist.version, 'metadata_path':str(dist._path),
                        'license_expression':dist.metadata.get('License-Expression'),
                        'license':dist.metadata.get('License'),
                        'requires_dist':dist.metadata.get_all('Requires-Dist') or [],
                        'license_files':[str(p) for p in (dist.files or ())
                            if 'license' in str(p).lower() or 'notice' in str(p).lower()]}
    c.require(set(actual) == set(expected), 'installed distribution closure')
    return actual


FIXED_USER_AGENT = 'pip/26.2.1 (isolated-offline-preparation)'

def override_user_agent(session, pin):
    """Replace this pinned process-local symbol without calling its system probe."""
    import functools
    import types
    path=Path(pin['path'])
    c.require(str(path).endswith('/pip/_internal/network/session.py') and
              str(Path(session.__file__))==str(path),'pip session origin')
    c.verify_expected(path,pin,c.check)
    original=session.user_agent
    c.require(type(original) is functools._lru_cache_wrapper,'pip user-agent wrapper')
    f=original.__wrapped__
    c.require(type(f) is types.FunctionType and f.__module__=='pip._internal.network.session' and
              f.__name__==f.__qualname__=='user_agent' and f.__code__.co_filename==str(path) and
              f.__code__.co_firstlineno==83 and f.__globals__ is session.__dict__ and
              original.cache_parameters()=={'maxsize':1,'typed':False} and
              session.PipSession.__init__.__globals__ is session.__dict__ and
              'user_agent' in session.PipSession.__init__.__code__.co_names,
              'pip user-agent original identity')
    def fixed_user_agent():return FIXED_USER_AGENT
    session.user_agent=fixed_user_agent
    c.require(session.PipSession.__init__.__globals__['user_agent'] is fixed_user_agent,
              'pip user-agent replacement identity')
    return {'value':FIXED_USER_AGENT,'original_file':pin,'original_called':False,'process_local':True}

def install_wheels(manifest, backend=False):
    # Load only the exact host pip package, never its site-packages parent.
    frontend = manifest['frontend']
    s = importlib.util.spec_from_file_location('pip', frontend['module'],
                   submodule_search_locations=[frontend['package_root']])
    pip = importlib.util.module_from_spec(s); sys.modules['pip'] = pip; s.loader.exec_module(pip)
    c.require(pip.__version__ == '26.2.1', 'frontend version')
    from pip._internal.network import session
    pins=[x for x in manifest['frontend_files'] if x['path']==str(Path(frontend['package_root'])/'_internal/network/session.py')]
    c.require(len(pins)==1 and pins[0]['sha256']=='d8aa8cbf91eefcf1516119030614a321ba9bb01a74957bf8b6aa0b2a73362baa', 'pip session pin')
    user_agent=override_user_agent(session,pins[0])
    from pip._internal.cli.main import main as pip_main
    if backend:
        receipt = c.read_json(c.PREP/'build/backend-wheel.json', c.check)
        wheel = c.safe_path(c.PREP/'build/wheels', receipt['filename'])
        c.verify_expected(wheel, receipt, c.check)
        paths = [wheel]
    else:
        paths = [c.PREP/'wheels'/row['filename'] for row in c.packages()]
    for wheel in paths:
        c.check()
        args = ['--isolated', '--disable-pip-version-check', '--no-input', 'install',
                '--no-index', '--no-deps', '--no-compile', '--no-cache-dir',
                '--no-build-isolation', '--ignore-installed', '--no-warn-script-location',
                '--prefix', str(c.PREP/'environment'), str(wheel)]
        c.require(pip_main(args) == 0, 'offline pip failed: '+wheel.name)
        c.category()
    c.write_json(c.PREP/'build'/('backend-install.json' if backend else 'wheels-install.json'),
        {'status':'VERIFIED', 'frontend_version':pip.__version__, 'frontend_file':pip.__file__,
         'wheels':[x.name for x in paths], 'process_local_user_agent':user_agent, 'sys_path':sys.path, 'dependency_resolution':False,
         'bytecode_compilation':False, 'network':False, 'cache':False})


def build_backend():
    src = Path(os.environ['GUARD_RUN_DIRECTORY'])/'tmp/source'
    expected = c.source_verify(src)
    pyproject = c.regular(src/'pyproject.toml', maximum=65536, contents=True, checker=c.check)
    import tomllib
    cfg = tomllib.loads(pyproject.decode())
    c.require(cfg['build-system'] == {'requires':['setuptools>=75','wheel'], 'build-backend':'setuptools.build_meta'},
              'build system changed')
    os.chdir(src)
    from packaging.requirements import Requirement
    from packaging.version import Version
    import setuptools.build_meta as backend
    rows = {x['name']:x['version'] for x in c.packages()}
    dynamic = backend.get_requires_for_build_wheel(config_settings=None)
    c.require(isinstance(dynamic,list) and len(dynamic)<=16, 'dynamic build requirements bound')
    for raw in dynamic:
        req = Requirement(raw)
        c.require(req.url is None and not req.extras and req.name in ('packaging','setuptools','wheel'),
                  'unapproved dynamic build requirement')
        if req.marker is None or req.marker.evaluate({'extra':''}):
            c.require(Version(rows[req.name]) in req.specifier, 'dynamic build requirement unresolved')
    out = c.PREP/'build/wheels'; out.mkdir()
    name = backend.build_wheel(str(out), config_settings=None, metadata_directory=None)
    c.require(name == 'long_audio_backend-0.2.0rc3.dev3-py3-none-any.whl', 'backend wheel identity')
    path = c.safe_path(out,name)
    c.require(path.stat().st_size <= 8*2**20, 'backend wheel bound')
    # Bounded by our qualified pure-Python source. Reject unexpected wheel members.
    with zipfile.ZipFile(path) as z:
        members = z.infolist()
        c.require(len(members)<=512 and sum(x.file_size for x in members)<=16*2**20, 'backend wheel expansion')
        names = [x.filename for x in members]
        c.require(len(names)==len(set(names)), 'duplicate backend wheel member')
        expected_package = {k:v for k,v in expected.items() if k.startswith('audio_backend/')}
        actual_package = {x for x in names if x.startswith('audio_backend/') and not x.endswith('/')}
        c.require(set(expected_package)==actual_package, 'backend wheel package membership')
        for name in actual_package:
            row=expected_package[name]; c.require(z.getinfo(name).file_size==row['bytes'], 'backend member size')
            with z.open(name) as f:
                import hashlib
                h=hashlib.sha256(); n=0
                while block:=f.read(65536): c.check(); n+=len(block);h.update(block)
            c.require(n==row['bytes'] and h.hexdigest()==row['sha256'], 'backend wheel source mismatch')
        metadata = z.read('long_audio_backend-0.2.0rc3.dev3.dist-info/METADATA')
        c.require(len(metadata)<=65536 and b'Version: 0.2.0rc3.dev3\n' in metadata, 'backend version')
    c.write_json(c.PREP/'build/backend-wheel.json', dict(c.regular(path,checker=c.check),
        filename=path.name, dynamic_build_requirements=dynamic, backend='setuptools.build_meta',
        source_commit='2c841d84a9b3204e510628f4aef17fe90334e76c', source_files=311,
        copied_source_path=str(src), pep517_subprocesses=0))


def verify_installed():
    expected = c.source_verify(c.SOURCE)
    package = c.staged_site()/'audio_backend'
    actual, _, _ = c.scan(package, c.check)
    wanted={k.removeprefix('audio_backend/'):v for k,v in expected.items() if k.startswith('audio_backend/')}
    c.require(set(actual)==set(wanted), 'installed audio_backend membership mismatch')
    for rel,row in wanted.items(): c.verify_expected(package/rel,row,c.check)
    metadata = installed_metadata()
    # Compare every unchanged wheel payload byte with installation. Standard generated
    # RECORD/console scripts are inventoried separately; no .data scheme is guessed.
    wheel_rows = []
    rows = c.packages()
    backend=c.read_json(c.PREP/'build/backend-wheel.json',c.check)
    for wheel in [c.PREP/'wheels'/r['filename'] for r in rows]+[c.PREP/'build/wheels'/backend['filename']]:
        c.check()
        with zipfile.ZipFile(wheel) as z:
            members=z.infolist(); c.require(len(members)<=8192,'wheel entry bound')
            checked=0
            for item in members:
                if item.is_dir():continue
                c.require('.data/' not in item.filename,'unreviewed wheel data scheme')
                path=c.safe_path(c.staged_site(),item.filename)
                if item.filename.endswith('.dist-info/RECORD'):continue
                c.require(item.file_size<=128*2**20,'wheel member bound')
                import hashlib
                h=hashlib.sha256();n=0
                with z.open(item) as f:
                    while block:=f.read(65536):c.check();n+=len(block);h.update(block)
                c.verify_expected(path,{'bytes':n,'sha256':h.hexdigest()},c.check);checked+=1
            wheel_rows.append({'wheel':wheel.name,'unchanged_payload_members_verified':checked})
    c.write_json(c.PREP/'build/installed-verification.json',
        {'status':'VERIFIED', 'package_file_count':len(wanted), 'distributions':metadata,
         'wheel_payloads':wheel_rows, 'backend_source_byte_exact':True, 'backend_version':'0.2.0rc3.dev3',
         'environment_inventory':c.inventory(c.PREP/'environment'), 'build_inventory':c.inventory(c.PREP/'build'),
         'models_loaded':0, 'model_calls':0, 'imports_smoke_performed':False,
         'unknown_dynamic_dependencies':'No native wheel import or C-ABI load in installation phase.'})


def main():
    c.require(len(sys.argv)==2 and sys.argv[1] in ('install','build','backend','verify'), 'substep not allowed')
    c.check(); manifest=c.verify_base(); c.isolated_path(manifest)
    sys.addaudithook(no_external_actions)
    if sys.argv[1]=='install':install_wheels(manifest)
    elif sys.argv[1]=='build':build_backend()
    elif sys.argv[1]=='backend':install_wheels(manifest,backend=True)
    else:verify_installed()

if __name__=='__main__':raise SystemExit(c.cli_boundary(main))
