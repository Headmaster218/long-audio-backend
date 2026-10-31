"""Read-only post-run whole-excerpt diagnostic with externally pinned v2 scorer.
Gold accessed ONLY here, never by the inference process. Private JSON output.
"""
import collections,hashlib,importlib.util,json,sys
from pathlib import Path

def load(path):return json.loads(Path(path).read_text())
def validate_run(execution, receipt, raw):
 if execution['returncode']!=0 or execution['reason'] is not None or not execution['original_handle_reaped'] or execution['unexpected_live_descendants']:raise ValueError('invalid execution/cleanup')
 if not receipt['model_closed'] or not receipt['session_closed'] or receipt['truncated'] or receipt['aborted'] or receipt['exception'] is not None:raise ValueError('invalid native receipt')
 if receipt['attempts']!=1 or receipt['raw_bytes']!=len(raw) or hashlib.sha256(raw).hexdigest()!=receipt['raw_sha256']:raise ValueError('raw changed')

def main(spec_path,run_path,output):
 s=load(spec_path);run=Path(run_path)
 for path,digest in s['pins'].items():
  assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest
 sys.path.insert(0,s['scorer_dir']);from score_overlap_transcripts import score
 sys.path.insert(0,s['repo']);from audio_backend.moss_handy_adapter import _PHRASE
 # Reuse exact frozen grammar, but never claim/construct the closed20s profile.
 raw=(run/'exact-native-raw.utf8').read_bytes();text=raw.decode('utf-8');tail=0;phrases=[];adjusted=0
 from decimal import Decimal
 for m in _PHRASE.finditer(text):
  assert not text[tail:m.start()].strip();tail=m.end();assert len(phrases)<256
  assert len(m[1])<=128 and len(m[4])<=128
  a,b=Decimal(m[1]),Decimal(m[4]);end=Decimal('45.728')
  assert 0<=a<b
  if b>end:
   assert a<end<b<=end+Decimal('.010');b=end;adjusted+=1
  phrases.append(dict(speaker_local_id=m[2],text=m[3],start=float(a),end=float(b)))
 assert not text[tail:].strip() and phrases and len({p['speaker_local_id'] for p in phrases})<=8
 validate_run(load(run/'execution.json'),load(run/'receipt.json'),raw)
 g=load(s['gold']);refs=[dict(id=sid,text=' '.join(w['text'] for w in g['words'] if w['speaker']==sid)) for sid in sorted(g['people'].values())]
 tracks=collections.defaultdict(list)
 for p in phrases:tracks[p['speaker_local_id'] or '__unknown__'].append(p['text'])
 metric=score(dict(language='en',speakers=refs),dict(transcripts=[dict(channel=k,text=' '.join(v)) for k,v in tracks.items()]))
 assert metric['reference_units']==125
 result=dict(metric=metric,phrases=phrases,complete_content=False,eos=None,generated_token_count=None,timestamp_adjusted_phrase_count=adjusted,baseline_errors=80,improvement=80-metric['errors'],research_screen_pass=80-metric['errors']>=10,production_promotion=False)
 with open(output,'x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
if __name__=='__main__':main(*sys.argv[1:])
