import tarfile,json,hashlib,shutil,os,time
from pathlib import Path,PurePosixPath
root=Path(__file__).resolve().parent; dest=root/'expanded';dest.mkdir(exist_ok=False)
archives=[(root/'models/events.tar.bz2',dest/'events',300*1024**2),(root/'models/sensevoice/model.tar.bz2',dest/'sensevoice',350*1024**2),(root/'vendor/native-diarization/downloads/nemo-speech-0.2.0-linux-x86_64-cpu.tar.gz',dest/'native',256*1024**2)]
receipt=[]
for archive,out,cap in archives:
 out.mkdir();seen=set();links=[];total=0;start=time.monotonic()
 with tarfile.open(archive,'r|*') as tf:
  for m in tf:
   parts=PurePosixPath(m.name).parts
   assert parts and not m.name.startswith('/') and '..' not in parts and m.name not in seen
   seen.add(m.name);assert len(seen)<=20000
   assert time.monotonic()-start<600 and shutil.disk_usage(root).free>5.5*1024**3
   p=out.joinpath(*parts)
   if m.isdir():p.mkdir(parents=True,exist_ok=True);continue
   if m.issym():
    assert '/' not in m.linkname and m.linkname not in ('','..','.');links.append((p,p.parent/m.linkname));continue
   assert m.isfile();total+=m.size;assert total<=cap
   p.parent.mkdir(parents=True,exist_ok=True)
   with tf.extractfile(m) as i,p.open('xb') as o:shutil.copyfileobj(i,o,1024*1024)
 for _ in range(len(links)+1):
  pending=[]
  for p,t in links:
   if not t.is_file():pending.append((p,t));continue
   total+=t.stat().st_size;assert total<=cap
   shutil.copyfile(t,p)
  links=pending
  if not links:break
 assert not links
 files=[{'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in out.rglob('*') if p.is_file()]
 receipt.append({'archive':str(archive.relative_to(root)),'files':files,'symlinks_materialized_as_files':True,'total_written_bytes':total})
 (root/'expanded-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(archive.name,len(files),total,'expanded',flush=True)
