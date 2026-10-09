#!/usr/bin/env python3
"""Explicit opt-in, pinned local native diarization installation (Python>=3.12).
Default runtime is CPU. Vulkan archives are migration artifacts only: the current
backend provider intentionally uses CPU and no GPU measurement is claimed.
"""
from pathlib import Path, PurePosixPath
import argparse, hashlib, json, platform, shutil, stat, tempfile, zipfile
from setup_models import download, safe_extract, sha, ROOT


def safe_zip_extract(path, destination):
    destination=Path(destination).resolve()
    with zipfile.ZipFile(path) as archive:
        members=archive.infolist()
        size=sum(x.file_size for x in members)
        if len(members)>20000 or size>256*1024**2:raise RuntimeError('Native archive expansion limit')
        if shutil.disk_usage(destination).free<size+2*1024**3:raise RuntimeError('Disk reserve would be exceeded')
        for member in members:
            name=member.filename
            normalized=name.replace('\\','/')
            p=PurePosixPath(normalized)
            kind=(member.external_attr>>16)&0o170000
            if p.is_absolute() or '..'in p.parts or any(':'in part for part in p.parts) or kind not in (0,stat.S_IFREG,stat.S_IFDIR):
                raise RuntimeError('Unsafe native archive member: '+name)
            target=(destination/normalized).resolve()
            if not target.is_relative_to(destination):raise RuntimeError('Unsafe native archive traversal')
            if member.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(member) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst,1024*1024)


def validate_receipt(receipt):
    for item in receipt['files']:
        path=ROOT/item['path']
        if not path.is_file() or sha(path)!=item['sha256']:raise RuntimeError('Missing/corrupt native runtime file: '+str(path))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--platform',choices=['linux-x86_64','windows-x86_64'])
    ap.add_argument('--runtime',choices=['cpu','vulkan'],default='cpu')
    ap.add_argument('--verify-only',action='store_true')
    a=ap.parse_args()
    if a.platform is None:
        if platform.machine().lower() not in ('x86_64','amd64'):raise RuntimeError('This pinned setup supports x86_64 only')
        a.platform='windows-x86_64' if platform.system()=='Windows' else 'linux-x86_64' if platform.system()=='Linux' else None
        if a.platform is None:raise RuntimeError('Select a supported target platform explicitly')
    manifest=json.loads((ROOT/'native-runtime-manifest.json').read_text())
    key=a.platform+'-'+a.runtime
    item=manifest['runtimes'][key];model=manifest['model']
    license_path=ROOT/'licenses/native-diarization/OpenMDW-1.1.txt'
    if not license_path.is_file() or sha(license_path)!=manifest['model_license_sha256']:raise RuntimeError('Missing/corrupt model license notice')
    print('Model uses OpenMDW-1.1; retain license/origin notices when distributing. '+manifest['model_license_url'],flush=True)
    receipt_path=ROOT/'vendor/native-diarization'/key/'installation.json'
    if a.verify_only:
        if not receipt_path.is_file():raise RuntimeError('No installed runtime receipt; run setup first')
        receipt=json.loads(receipt_path.read_text());validate_receipt(receipt)
        if sha(ROOT/model['destination'])!=model['sha256']:raise RuntimeError('Missing/corrupt model')
        print(json.dumps(receipt['settings'],indent=2));return
    archive=download(item);model_path=download(model)
    dest=receipt_path.parent;dest.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as td:
        if archive.suffix=='.zip':safe_zip_extract(archive,td)
        else:safe_extract(archive,td,{'max_extracted_bytes':256*1024**2})
        shutil.copytree(td,dest,dirs_exist_ok=True)
    libraries=list(dest.rglob('nemo_speech_asr_c.dll')) if a.platform.startswith('windows') else list(dest.rglob('libnemo_speech_asr_c.so'))
    if len(libraries)!=1:
        # Upstream package uses libnemo_speech_asr on some platforms/releases.
        pattern='*nemo*asr_c*.dll' if a.platform.startswith('windows') else '*nemo*asr_c*.so'
        libraries=list(dest.rglob(pattern))
    if len(libraries)!=1:raise RuntimeError('Expected one native C ABI library, found '+str(libraries))
    settings={'diarization_backend':'nemotron','native_diarization_model':str(model_path),'native_diarization_library':str(libraries[0]),'native_diarization_threads':2}
    receipt={'runtime':key,'version':manifest['release'],'settings':settings,'gpu_status':manifest['gpu_status'],'files':[{'path':str(p.relative_to(ROOT)),'sha256':sha(p)} for p in sorted(dest.rglob('*')) if p.is_file() and p!=receipt_path]}
    receipt_path.write_text(json.dumps(receipt,indent=2));validate_receipt(receipt)
    print(json.dumps(settings,indent=2))
    if a.runtime=='vulkan':print('Vulkan archive downloaded only. Current provider still forces CPU; do not claim GPU execution.')
    print('Runtime/model verified. Windows execution remains untested from this Linux host.')

if __name__=='__main__':main()
