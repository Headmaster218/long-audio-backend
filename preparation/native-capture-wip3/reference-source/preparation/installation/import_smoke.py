"""Separately reviewed import-only smoke. No app, provider, model or native C ABI."""
import importlib.util
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('native_install_common',HERE/'common.py')
c=importlib.util.module_from_spec(s);s.loader.exec_module(c)
import importlib
import os

IMPORTS=('numpy','soundfile','sherpa_onnx','fastapi','uvicorn','python_multipart',
         'pydantic_settings','psutil','opencc','audio_backend','audio_backend.app',
         'audio_backend.worker','audio_backend.inference','audio_backend.inference.native_diarization')


def no_actions(event,args):
    if event.startswith(('socket.','subprocess.','os.exec','os.spawn')) or event in ('os.system','os.posix_spawn','os.fork','os.forkpty'):
        raise c.Refused('import smoke external action: '+event)
    if event in ('open','ctypes.dlopen') and args and isinstance(args[0],(str,bytes)):
        path=Path(os.fsdecode(args[0])).absolute()
        c.require(not any(path.is_relative_to(c.PREP/x) for x in ('models','vendor','corpus')),
                  'import smoke model/input/native artifact access')


def no_constructors(frame,event,arg):
    if event=='call':
        code=frame.f_code;filename=code.co_filename;name=code.co_qualname
        if '/audio_backend/' in filename:
            forbidden=('AudioAnalyzer.__init__','NativeDiarizer.__init__','Worker.__init__',
                       'AnalyzerProcess.__init__','NativeCoordinator.__init__','create_app','main')
            c.require(name not in forbidden,'model/service constructor attempted: '+name)
        if '/sherpa_onnx/' in filename:
            c.require(code.co_name!='__init__','Sherpa constructor attempted: '+name)


def mapped_paths(raw):
    paths=[]
    for line in raw.decode().splitlines():
        fields=line.split(maxsplit=5)
        if len(fields)==6 and fields[5].startswith('/'):
            c.require(not fields[5].endswith(' (deleted)'),'deleted mapped file')
            if fields[5] not in paths:
                c.require(len(paths)<256,'mapped file count')
                paths.append(fields[5])
    return paths

def main():
    c.check();host=c.verify_base();receipt=c.read_json(c.PREP/'build/installed-verification.json',c.check)
    c.require(receipt['status']=='VERIFIED' and receipt['backend_source_byte_exact'],'installation not verified')
    for rel,pin in receipt['environment_inventory']['files'].items():
        c.verify_expected(c.safe_path(c.PREP/'environment',rel),pin,c.check)
    exe=c.PREP/'environment/bin/python3.12'
    if sys.argv[1:]==[]:
        c.require(Path(sys.executable).resolve()!=exe,'invalid host smoke entry')
        env=dict(os.environ)
        for key in ('PYTHONHOME','PYTHONPATH','LD_PRELOAD','LD_LIBRARY_PATH','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE'):env.pop(key,None)
        os.execve(exe,[str(exe),'-I','-B','-S',str(HERE/'import_smoke.py'),'--installed'],env)
    c.require(sys.argv[1:]==['--installed'] and Path(sys.executable).resolve()==exe,'installed smoke origin')
    c.isolated_path(host)
    sys.addaudithook(no_actions);sys.setprofile(no_constructors)
    modules={}
    try:
        for name in IMPORTS:
            c.check();mod=importlib.import_module(name);origin=Path(mod.__file__).resolve()
            c.require(origin.is_relative_to(c.staged_site()),'unexpected import origin: '+name)
            modules[name]={'origin':str(origin),**c.regular(origin,checker=c.check)}
        import audio_backend
        c.require(audio_backend.__version__=='0.2.0rc3.dev3','backend import version')
    finally:sys.setprofile(None)
    all_origins={}
    for name,mod in sorted(sys.modules.copy().items()):
        origin=getattr(mod,'__file__',None)
        if not origin:continue
        path=Path(origin).resolve()
        allowed=path.is_relative_to(c.staged_site()) or path.is_relative_to(Path(host['stdlib_root'])) or path.is_relative_to(HERE)
        c.require(allowed,'ambient loaded module: '+name)
        # Parent of the stdlib includes site-packages; explicitly reject that host path.
        c.require(not path.is_relative_to(Path(host['stdlib_root'])/'site-packages'),'global host package leaked: '+name)
        all_origins[name]={'origin':str(path),**c.regular(path,checker=c.check)}
    mapped=[]
    raw=c.regular(Path('/proc/self/maps'),maximum=256*2**10,contents=True,checker=c.check)
    for name in mapped_paths(raw):
        path=Path(name);mapped.append({'path':name,**c.regular(path,checker=c.check)})
    c.write_json(c.PREP/'build/import-smoke.json',{'status':'VERIFIED_IMPORT_ONLY','imports':modules,
        'all_loaded_module_origins':all_origins,'mapped_file_pins':mapped,'sys_path':sys.path,
        'python_executable':sys.executable,'constructors_called_by_probe':0,'model_calls':0,
        'actual_app_started':False,'native_nemo_c_abi_loaded':False,
        'limits':['Imports load NumPy/Soundfile/Sherpa extension code; no neural instance was requested.',
                  'Mapped files are the observed process closure only; unused/lazy dlopen dependencies remain unknown.',
                  'This does not establish model readiness, inference success or model working-set memory.']})
    c.category()

if __name__=='__main__':raise SystemExit(c.cli_boundary(main))
