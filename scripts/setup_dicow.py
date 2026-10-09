#!/usr/bin/env python3
"""Pinned official download and bounded BF16 conversion. No remote model import."""
import argparse,gc,hashlib,json,os,shutil,sys,threading,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from audio_backend.dicow_assets import manifest,verify_model_directory

def verify_file(path,row):
    if not path.is_file() or path.stat().st_size!=row['bytes']:raise ValueError('Missing/wrong-size asset: '+str(path))
    with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    if digest!=row['sha256']:raise ValueError('SHA256 mismatch: '+str(path))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--model-dir',type=Path,default=ROOT/'models/dicow-bf16')
    ap.add_argument('--source-dir',type=Path)
    ap.add_argument('--prepared-dir',type=Path,help='Verify and copy/hardlink an already prepared identical BF16 directory')
    ap.add_argument('--verify-only',action='store_true')
    a=ap.parse_args();spec=manifest();dest=a.model_dir.expanduser().resolve()
    if a.verify_only:
        verify_model_directory(dest);print('Verified pinned SE-DiCoW BF16 files');return
    if dest.exists() and any(dest.iterdir()):raise ValueError('Destination must be empty; use --verify-only for existing model')
    dest.mkdir(parents=True,exist_ok=True)
    reserve=5*1024**3
    if shutil.disk_usage(dest).free<reserve:raise RuntimeError('At least5GiB disk reserve is required')
    if a.prepared_dir:
        source=a.prepared_dir.expanduser().resolve();verify_model_directory(source)
        for row in spec['runtime_files']:
            src=source/row['name'];target=dest/row['name']
            try:os.link(src,target)
            except OSError:
                if shutil.disk_usage(dest).free<reserve+row['bytes']:raise RuntimeError('Disk reserve reached')
                shutil.copyfile(src,target)
        verify_model_directory(dest);print('Prepared pinned BF16 model directory');return
    source=(a.source_dir.expanduser().resolve() if a.source_dir else dest.parent/'dicow-source-f32')
    source.mkdir(parents=True,exist_ok=True)
    for row in spec['source_files']:
        path=source/row['name']
        if not path.exists():
            if a.source_dir:raise ValueError('Missing source asset: '+row['name'])
            if shutil.disk_usage(source).free<reserve+row['bytes']+2203581456:raise RuntimeError('Insufficient disk for download, conversion, and reserve')
            with urllib.request.urlopen(spec['source_base_url']+row['name'],timeout=60) as reply, path.with_suffix(path.suffix+'.part').open('wb') as out:
                size=0
                while block:=reply.read(4*1024**2):
                    size+=len(block)
                    if size>row['bytes']:raise ValueError('Download larger than pinned asset')
                    out.write(block)
            pending=path.with_suffix(path.suffix+'.part');verify_file(pending,row);pending.replace(path)
        verify_file(path,row)
    # Import only reputable installed libraries, never downloaded custom model code.
    import psutil,torch,safetensors
    from safetensors import safe_open
    from safetensors.torch import save_file
    if torch.__version__!='2.6.0+cpu' or safetensors.__version__!='0.6.2':raise ValueError('Use pinned CPU Torch2.6.0+cpu and safetensors0.6.2')
    stop=threading.Event()
    def guard():
        while not stop.wait(.1):
            if psutil.Process().memory_info().rss>int(1.5*1024**3) or psutil.virtual_memory().available<int(1.5*1024**3):os._exit(75)
    threading.Thread(target=guard,daemon=True).start();torch.set_num_threads(2)
    if shutil.disk_usage(dest).free<reserve+2203581456:raise RuntimeError('Insufficient disk reserve for BF16 conversion')
    for row in spec['source_files']:
        if row['name']!='model.safetensors':shutil.copyfile(source/row['name'],dest/row['name'])
    config=json.loads((dest/'config.json').read_text());config['torch_dtype']='bfloat16'
    (dest/'config.json').write_text(json.dumps(config,indent=2))
    with safe_open(source/'model.safetensors',framework='pt',device='cpu') as f:keys=list(f.keys())
    mapping={};tensors={};size=0;total=0;number=0
    def flush():
        nonlocal tensors,size,number
        if not tensors:return
        number+=1;name=f'model-{number:05}.safetensors'
        save_file(tensors,dest/name,metadata={'format':'pt'})
        mapping.update({key:name for key in tensors});tensors={};size=0;gc.collect()
    for key in keys:
        with safe_open(source/'model.safetensors',framework='pt',device='cpu') as f:value=f.get_tensor(key).to(torch.bfloat16).clone()
        n=value.numel()*value.element_size()
        if size and size+n>100*1024**2:flush()
        tensors[key]=value;size+=n;total+=n;del value
    flush()
    (dest/'model.safetensors.index.json').write_text(json.dumps({'metadata':{'total_size':total},'weight_map':mapping},indent=2))
    verify_model_directory(dest);stop.set();print('Downloaded, converted, and verified pinned BF16 model')

if __name__=='__main__':main()
