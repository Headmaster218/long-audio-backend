"""Installed-symbol compatibility only; no provider or stream construction."""
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
PREP=HERE.parent
s=importlib.util.spec_from_file_location('sdk_install_common',PREP/'installation/common.py')
c=importlib.util.module_from_spec(s);s.loader.exec_module(c)

def main():
    c.require(not sys.argv[1:],'fixed compatibility probe')
    target=PREP/'build/sdk-compatibility.json'
    c.require(not target.exists() and not target.is_symlink(),'fresh SDK inventory output required')
    c.check();host=c.verify_base();installed=c.read_json(PREP/'build/installed-verification.json',c.check)
    c.require(installed['status']=='VERIFIED' and installed['backend_source_byte_exact'],'installation unavailable')
    inventory=installed['environment_inventory']['files']
    for rel,pin in inventory.items():c.verify_expected(c.safe_path(PREP/'environment',rel),pin,c.check)
    c.isolated_path(host)
    s=importlib.util.spec_from_file_location('sdk_import_guards',PREP/'installation/import_smoke.py');guards=importlib.util.module_from_spec(s);s.loader.exec_module(guards)
    sys.addaudithook(guards.no_actions);sys.setprofile(guards.no_constructors)
    controller=PREP/'controller';sys.path.insert(0,str(controller))
    from instrumentation import Instrumentation
    import sherpa_onnx as sdk
    originals=[];symbols=[]
    class Deadline:
        check=staticmethod(c.check)
    class Journal:
        def emit(self,event,**fields):
            c.check();c.require(event=='bound_original_symbol','unexpected provider action')
            symbols.append({'event':event,**fields});c.require(len(symbols)<=32,'symbol count cap')
    class Probe(Instrumentation):
        def bind(self,owner,name,replacement):
            raw=inspect.getattr_static(owner,name);originals.append((owner,name,raw,name in vars(owner)))
            return super().bind(owner,name,replacement)
    profile={'output_dir':str(Path(os.environ['GUARD_RUN_DIRECTORY'])),'site_packages':str(c.staged_site()),
             'installed_files':{str(PREP/'environment'/rel):pin for rel,pin in inventory.items()}}
    probe=Probe(profile,Deadline(),Journal(),'analyzer');streams=[];restored=False
    try:
        probe.install_sdk()
        for name in ('OfflineStream','OnlineStream'):
            c.check();cls=getattr(sdk,name);raw=inspect.getattr_static(cls,'accept_waveform');originals.append((cls,'accept_waveform',raw,'accept_waveform' in vars(cls)))
            doc=getattr(raw,'__doc__',None);c.require(doc is None or type(doc) is str and len(doc.encode())<=16384,'signature/doc byte cap')
            def never_call(*args,**kwargs):raise RuntimeError('probe must never construct or submit audio')
            probe.bind(cls,'accept_waveform',never_call)
            streams.append({'exported_name':name,'module':getattr(cls,'__module__',None),'descriptor_type':type(raw).__name__,'doc':doc,'actual_returned_type_observed':False})
        for counts in (probe.lifecycle.counts,probe.sdk.counts,probe.abi.counts):
            c.require(all(all(value==0 for value in row.values()) for row in counts.values()),'provider counter changed')
        c.require(not probe.streams and probe.streams_created==0 and not probe.clean_receipts,'stream action occurred')
    finally:
        for owner,name,raw,had_own in reversed(originals):
            if had_own:setattr(owner,name,raw)
            elif name in vars(owner):delattr(owner,name)
        for owner,name,raw,had_own in originals:
            c.require((name in vars(owner))==had_own,'descriptor membership restoration failed')
            # Repeated stream records include the same original descriptor only.
            c.require(inspect.getattr_static(owner,name) is raw,'descriptor restoration failed')
        restored=True;sys.setprofile(None)
    observed={}
    for name,module in sorted(sys.modules.copy().items()):
        origin=getattr(module,'__file__',None)
        if not origin:continue
        path=Path(origin).resolve()
        c.require(path.is_relative_to(c.staged_site()) or path.is_relative_to(Path(host['stdlib_root'])) or path.is_relative_to(PREP/'installation') or path.is_relative_to(controller) or path.is_relative_to(HERE),'ambient module origin')
        c.require(not path.is_relative_to(Path(host['stdlib_root'])/'site-packages'),'host site package leaked')
        observed[name]={'path':str(path),**c.regular(path,checker=c.check)}
    maps=c.regular('/proc/self/maps',maximum=256*2**10,contents=True,checker=c.check).decode();mapped={}
    for line in maps.splitlines():
        c.check();fields=line.split(maxsplit=5)
        if len(fields)==6 and fields[5].startswith('/'):
            c.require(not fields[5].endswith(' (deleted)'),'deleted mapped file');path=Path(fields[5]).resolve()
            if str(path) not in mapped:
                c.require(len(mapped)<256,'mapped file count');mapped[str(path)]=c.regular(path,checker=c.check)
    for rel,pin in inventory.items():c.verify_expected(c.safe_path(PREP/'environment',rel),pin,c.check)
    c.source_verify(c.SOURCE);c.check()
    c.write_json(target,{'status':'PASS_INSTALLED_SYMBOL_COMPATIBILITY_ONLY','symbols':symbols,'stream_classes':streams,'observed_module_origins':observed,'observed_mapped_files':mapped,'descriptors_restored':restored,'provider_constructor_calls':0,'stream_constructor_calls':0,'compute_decode_calls':0,'model_asset_opens':0,'limits':['No actual returned stream type, model working set, inference, or native C ABI qualification.']})
    c.category()
if __name__=='__main__':raise SystemExit(c.cli_boundary(main))
