#!/usr/bin/env python3
"""Download pinned, hash-verified public models; optionally build whisper.cpp.
Usage: python scripts/setup_models.py --build [--vulkan]
Requires Python >=3.12; CMake and a C/C++ compiler for --build.
"""
from pathlib import Path
import argparse, hashlib, json, os, shutil, subprocess, tarfile, tempfile, time, urllib.request
ROOT=Path(__file__).resolve().parents[1]

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def download(item):
    dest=ROOT/item['destination'];dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.is_file() and sha(dest)==item['sha256']:return dest
    reserve=2*1024**3
    limit=int(item['bytes'])
    if shutil.disk_usage(dest.parent).free < limit+reserve:
        raise RuntimeError('Insufficient disk: download requires a 2 GiB reserve')
    temp=dest.with_suffix(dest.suffix+'.partial')
    for attempt in range(3):
        try:
            req=urllib.request.Request(item['url'],headers={'User-Agent':'OfflineAudioBackend/1.0'})
            with urllib.request.urlopen(req,timeout=90) as src,temp.open('wb') as dst:
                received=0
                while True:
                    block=src.read(1024*1024)
                    if not block: break
                    received+=len(block)
                    if received > limit: raise RuntimeError('Download exceeds pinned manifest size')
                    if shutil.disk_usage(dest.parent).free < reserve+len(block): raise RuntimeError('Disk reserve reached')
                    dst.write(block)
            if sha(temp)!=item['sha256']:raise RuntimeError('SHA256 mismatch for '+item['id'])
            temp.replace(dest);return dest
        except Exception:
            if attempt==2:raise
            time.sleep(2**attempt)

def safe_extract(path,dest,item):
    with tarfile.open(path) as tar:
        members=tar.getmembers()
        size=sum(m.size for m in members if m.isfile())
        limit=int(item.get('max_extracted_bytes', 1024**3))
        if size>limit or len(members)>20000: raise RuntimeError('Archive expansion exceeds manifest limit')
        if shutil.disk_usage(dest).free < size*(2 if item.get('strip_root') else 1)+2*1024**3: raise RuntimeError('Extraction would violate 2 GiB disk reserve')
        tar.extractall(dest,members=members,filter='data')

def extract(path,item):
    dest=ROOT/item['extract_to'];dest.mkdir(parents=True,exist_ok=True)
    # Only public source/model archives. Python data filter prevents traversal and unsafe links.
    if item.get('strip_root'):
        with tempfile.TemporaryDirectory(dir=dest.parent) as td:
            safe_extract(path,td,item)
            roots=list(Path(td).iterdir())
            if len(roots)!=1 or not roots[0].is_dir():raise RuntimeError('Expected single archive root')
            shutil.copytree(roots[0],dest,dirs_exist_ok=True)
    else:
        safe_extract(path,dest,item)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--build',action='store_true');parser.add_argument('--vulkan',action='store_true');parser.add_argument('--verify-only',action='store_true');parser.add_argument('--quality',action='store_true');parser.add_argument('--turbo',action='store_true');parser.add_argument('--sensevoice',action='store_true');args=parser.parse_args()
    if args.sensevoice:
        print('OPTIONAL SenseVoice weights use the custom FunASR Model Open Source License Agreement v1.1, not MIT/Apache. Review https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE before use or redistribution. Explicit --sensevoice selects this optional download.',flush=True)
    manifest=json.loads((ROOT/'model-manifest.json').read_text())
    for item in manifest['downloads']:
        if item.get('optional_quality_model') and not args.quality: continue
        if item.get('optional_turbo_model') and not args.turbo: continue
        if item.get('optional_sensevoice_model') and not args.sensevoice: continue
        if args.verify_only:
            path=ROOT/item['destination']
            if not path.is_file() or sha(path)!=item['sha256']:raise RuntimeError('Missing or corrupt '+str(path))
        else:
            print('Preparing',item['id'],flush=True);path=download(item)
            if 'extract_to'in item:extract(path,item)
    for item in manifest['runtime_files']:
        if item.get('optional_sensevoice_model') and not args.sensevoice: continue
        path=ROOT/item['path']
        if not path.is_file() or path.stat().st_size!=item['bytes'] or sha(path)!=item['sha256']:
            raise RuntimeError('Missing or corrupt extracted runtime model: '+str(path))
    if args.build:
        src=ROOT/'vendor/whisper.cpp';build=src/'build'
        subprocess.run(['cmake','-S',str(src),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release',
                        '-DGGML_VULKAN='+('ON'if args.vulkan else'OFF'),'-DGGML_NATIVE=OFF','-DWHISPER_BUILD_TESTS=OFF'],check=True)
        subprocess.run(['cmake','--build',str(build),'--config','Release','--target','whisper-cli','-j','2'],check=True)
    print('Model archives and extracted runtime weights verified. Build whisper-cli separately if not built. Inference is offline.')
if __name__=='__main__':main()
