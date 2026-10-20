# SOURCE ARCHIVE ONLY: this sanitized file is not the executable tested bundle.
# ${WORKSPACE_ROOT} is an explicit deployment placeholder, not a literal path.
# Restore local configuration and regenerate all pins/releases before execution.
"""Only GET and the exact previously frozen page2 seal. No audio/upload/session creation."""
def verify_remote_pages(observer,controls):
 journal=controls['original_journal'];pages={}
 for saved in journal['pages']:
  path='/sessions/'+controls['resume_sid']+'/recordings/'+controls['capture']+'/pages/'+str(saved['number'])
  st,remote=observer.request(path,131072)
  if st!=200 or type(remote) is not dict:raise RuntimeError('resume page GET unavailable')
  if (remote.get('id'),remote.get('page_number'),remote.get('capture_id'),remote.get('capture_origin_frame'))!=(saved['server_id'],saved['number'],controls['capture'],100003):raise RuntimeError('resume remote page identity mismatch')
  expected={(p['server_id'],p['key'],p['digest'],p['frames'],p['bytes'],p['capture_start']) for p in saved['pieces']}
  actual={(p['id'],p['idempotency_key'],p['digest'],p['frames'],p['bytes'],p['source_start_frame']) for p in remote.get('sources',[])}
  if actual!=expected or len(remote['sources'])!=len(expected):raise RuntimeError('resume source identity/digest/geometry changed')
  if remote.get('state') not in ('pending_receipts','published','preparing','sealed'):raise RuntimeError('resume remote page not sealed')
  if saved['released'] and (remote.get('released') is not True or remote.get('state')!='published'):raise RuntimeError('released page regressed')
  if (remote.get('end_frame'),remote.get('eos'))!=(saved['seal']['end_frame'],saved['seal']['eos']):raise RuntimeError('resume frozen end/eos changed')
  pages[saved['number']]=remote
 return pages

def complete_pending_seal(observer,controls):
 pages=verify_remote_pages(observer,controls)
 if pages[2].get('released'):return pages[2]
 if pages[2].get('state')=='preparing':return pages[2]
 st,value=observer.request(controls['pending_page_seal_path'],131072,method='POST',payload=controls['pending_page_seal'],attempts=1)
 if st in (200,202) and type(value) is dict:return value
 # An uncertain response is checked before any future exact-idempotency retry.
 checked=verify_remote_pages(observer,controls)
 if checked[2].get('released'):return checked[2]
 if st is None or st in (429,500,502,503,504):return checked[2]
 raise RuntimeError('existing page2 seal refused: '+str(st))
