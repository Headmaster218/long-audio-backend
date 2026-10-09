#!/usr/bin/env python3
"""Private, read-only checkpoint observer. Never changes the service database."""
import argparse, hashlib, json, os, sqlite3, tempfile, time, contextlib, shutil
from pathlib import Path

def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()

def capture(run, output):
 run=Path(run); output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
 if output.exists() and output.stat().st_size>128*1024**2: raise RuntimeError('Private cache 128 MiB limit reached')
 os.chmod(output.parent,0o700)
 source=run/'checkpoint/jobs.sqlite3'
 if shutil.disk_usage(output.parent).free < 5*1024**3+source.stat().st_size+16*1024**2: raise RuntimeError('Observer disk reserve would be crossed')
 with tempfile.TemporaryDirectory(dir=output.parent) as td:
  snapshot=Path(td)/'snapshot.sqlite3'
  with contextlib.closing(sqlite3.connect(f'file:{source}?mode=ro',uri=True)) as src, contextlib.closing(sqlite3.connect(snapshot)) as dst: src.backup(dst)
  digest=sha(snapshot)
  with contextlib.closing(sqlite3.connect(f'file:{snapshot}?mode=ro',uri=True)) as src, contextlib.closing(sqlite3.connect(output)) as out:
   os.chmod(output,0o600)
   page_size=out.execute('pragma page_size').fetchone()[0]
   out.execute('pragma max_page_count='+str(128*1024**2//page_size))
   src.row_factory=sqlite3.Row
   if src.execute('pragma quick_check').fetchone()[0]!='ok': raise RuntimeError('Checkpoint integrity failed')
   out.executescript('CREATE TABLE IF NOT EXISTS snapshots(sha TEXT PRIMARY KEY, captured REAL, provenance TEXT); CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, payload TEXT NOT NULL, first_snapshot TEXT NOT NULL);')
   previous=out.execute('select provenance from snapshots where sha=?',(digest,)).fetchone()
   if previous and json.loads(previous[0]).get('script_sha256')==sha(Path(__file__)): return {'unchanged':True,'snapshot_sha256':digest}
   if out.execute('select count(*) from observations').fetchone()[0]>100000: raise RuntimeError('Private cache 100000 row limit reached')
   out.execute('CREATE TABLE IF NOT EXISTS query_scopes(chunk_id TEXT, window_start REAL, local_id TEXT, statuses TEXT, captured INTEGER, first_snapshot TEXT, PRIMARY KEY(chunk_id,window_start,local_id))')
   existing_count=out.execute('select count(*) from observations').fetchone()[0]
   added=0
   for row in src.execute('select * from speaker_observations order by window_start,id'):
    payload=json.dumps(dict(row),sort_keys=True,separators=(',',':'),ensure_ascii=False)
    old=out.execute('select payload from observations where id=?',(row['id'],)).fetchone()
    if old and old[0]!=payload: raise RuntimeError('Previously captured observation changed: '+row['id'])
    if not old:
     if existing_count+added>=100000: raise RuntimeError('Private cache 100000 row limit reached')
     out.execute('insert into observations values(?,?,?)',(row['id'],payload,digest));added+=1
   captured_scopes=set()
   for (payload,) in out.execute('select payload from observations'):
    r=json.loads(payload);captured_scopes.add((r['chunk_id'],r['window_start'],r['local_speaker_id']))
   scope_counts={}
   for r in src.execute('select chunk_id,speaker_window_start,speaker_local_id,group_concat(distinct speaker_status) statuses from speaker_turns group by chunk_id,speaker_window_start,speaker_local_id'):
    key=(r[0],r[1],r[2]); found=key in captured_scopes
    storage_key=(r[0] if r[0] is not None else '__NULL__',r[1] if r[1] is not None else -1,r[2] if r[2] is not None else '__NULL__')
    out.execute('insert into query_scopes values(?,?,?,?,?,?) ON CONFLICT(chunk_id,window_start,local_id) DO UPDATE SET statuses=excluded.statuses,captured=excluded.captured',(*storage_key,r[3],int(found),digest))
    status=r[3] or 'null'; scope_counts.setdefault(status,{'total':0,'captured':0});scope_counts[status]['total']+=1;scope_counts[status]['captured']+=int(found)
   missing_pointers=0
   for (oid,) in src.execute('select distinct candidate_observation_id from segments where candidate_observation_id is not null'):
    missing_pointers+=int(out.execute('select 1 from observations where id=?',(oid,)).fetchone() is None)
   provenance={'durable_query_scopes_by_status':scope_counts,'missing_durable_observation_pointers':missing_pointers,'coverage_note':'Missing scopes may never have produced an eligible embedding; pointers prove retained observations. Zero missing pointers is not completeness proof: scopes without ASR pointers may be uncaptured. No claim of all query vectors.', 'source_checkpoint':str(source.absolute()),'run_sha256':sha(run/'run.json'),'effective_settings_sha256':sha(run/'effective-settings.json'),'checkpoint_rows':src.execute('select count(*) from speaker_observations').fetchone()[0],'checkpoint_matched_rows':src.execute('select count(*) from speaker_observations where matched_speaker_id is not null').fetchone()[0],'media_offset':src.execute('select sum(next_offset) from chunks').fetchone()[0],'script_sha256':sha(Path(__file__)),'private_biometric_cache':True,'cap_bytes':128*1024**2,'cap_rows':100000}
   out.execute('insert or replace into snapshots values(?,?,?)',(digest,time.time(),json.dumps(provenance,sort_keys=True)))
   total=out.execute('select count(*) from observations').fetchone()[0]
   out.commit()
   return {'snapshot_sha256':digest,'added':added,'total':total,**provenance}

def main():
 p=argparse.ArgumentParser();p.add_argument('--run-dir',required=True);p.add_argument('--output',required=True);p.add_argument('--watch',action='store_true');a=p.parse_args()
 while True:
  result=capture(a.run_dir,a.output);print(json.dumps(result,sort_keys=True),flush=True)
  if not a.watch: break
  time.sleep(60)
if __name__=='__main__':main()
