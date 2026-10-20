# SOURCE ARCHIVE ONLY: this sanitized file is not the executable tested bundle.
# ${WORKSPACE_ROOT} is an explicit deployment placeholder, not a literal path.
# Restore local configuration and regenerate all pins/releases before execution.
"""Exact preserved scope/work/certificate/terminal attempt guard; no product imports."""
from pathlib import Path
import hashlib

def verify_summary(summary,frozen,before):
 def need(c,m):
  if not c:raise RuntimeError(m)
 need(summary['session_id']==frozen['sid'] and summary['batch']['id']==frozen['rid'],'resume scope mismatch')
 work={w['id']:w for w in summary['work_chunks']};obs={o['observation_id']:o for o in summary['history']['observations']};attempts={a['token']:a for a in summary['history']['attempts']}
 need(set(work)==set(frozen['work_ids']),'resume work set changed')
 need({s['id'] for s in summary['sources']}==set(frozen['source_ids']),'resume source set changed')
 for old in frozen['completed']:need(all(work[old['id']][k]==v for k,v in old.items()),'completed work changed')
 for old in frozen['observations']:need(old['observation_id'] in obs and all(obs[old['observation_id']][k]==v for k,v in old.items()),'retained certificate/vector changed')
 for old in frozen['attempts']:need(attempts.get(old['token'])==old,'prior terminal attempt changed, including failure')
 if before:
  need(len(obs)==len(frozen['observations']) and len(attempts)==len(frozen['attempts']),'initial observation/attempt set changed')
  for key,state in frozen['initial_work_states'].items():need([work[key]['status'],work[key]['next_offset']]==state,'initial work state changed')
 for raw,old in frozen['files'].items():
  s=Path(raw).stat();need([s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns]==old,'retained PCM identity changed')
 need(hashlib.sha256(Path(frozen['prior_receipt']).read_bytes()).hexdigest()==frozen['prior_receipt_sha256'],'original receipt changed')
 return True
