import json
import math
import sys
import tempfile
import threading
import wave
from pathlib import Path
from .resources import run_bounded, check_resources, AnalyzerProcess
from .boundaries import lexical_core, safe_cut
from .boundary_store import stream_info, context_parts, wait_for_input
from .native_worker import NativeCoordinator
from .overlap_ownership import own_candidates,validate_candidates


def validate_result(result,duration):
    if not isinstance(result,dict): raise ValueError('Invalid analyzer result')
    if not isinstance(result.get('metadata',{}),dict): raise ValueError('Invalid analyzer metadata')
    for key in ('asr_backend','timestamp_semantics'):
        value=result.get('metadata',{}).get(key)
        if value is not None and (not isinstance(value,str) or len(value)>100000): raise ValueError(f'Invalid metadata {key}')
    segments=result.get('segments',[]); events=result.get('events',[]);turns=result.get('speaker_turns',[])
    if not isinstance(turns,list) or len(turns)>4096:raise ValueError('Invalid independent speaker turns')
    if not isinstance(segments,list) or not isinstance(events,list) or len(segments)>1000 or len(events)>1000:
        raise ValueError('Analyzer output item limit exceeded')
    for item in segments+events+turns:
        if not isinstance(item,dict): raise ValueError('Invalid analyzer item')
        for key in ('start','end'):
            v=item.get(key)
            if not isinstance(v,(int,float)) or not math.isfinite(v): raise ValueError('Invalid analyzer timestamp')
        if not 0<=item['start']<=item['end']<=duration+.15: raise ValueError('Analyzer timestamp outside window')
        item['start']=min(item['start'],duration)
        item['end']=min(item['end'],duration)
    for s in segments:
        if not isinstance(s.get('text',''),str) or len(s.get('text',''))>100000: raise ValueError('Invalid text')
        for optional_text in ('raw_text','asr_backend','timestamp_kind'):
            value=s.get(optional_text)
            if value is not None and (not isinstance(value,str) or len(value)>100000): raise ValueError(f'Invalid {optional_text}')
        units=s.get('_units',[])
        if not isinstance(units,list) or len(units)>4096:raise ValueError('Invalid lexical units')
        for u in units:
            if not isinstance(u,dict) or not isinstance(u.get('text'),str):raise ValueError('Invalid lexical unit')
            for name in ('start','end','ownership_anchor'):
                v=u.get(name)
                if not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=duration+.15:
                    raise ValueError('Invalid lexical timestamp')
            if u['end']<u['start']:raise ValueError('Reversed lexical interval')
        if s.get('speaker_local_id') is not None: s['speaker_local_id']=str(s['speaker_local_id'])[:128]
        confidence=s.get('confidence')
        if confidence is not None and (not isinstance(confidence,(int,float)) or not math.isfinite(confidence)): raise ValueError('Invalid confidence')
    for e in events:
        if not isinstance(e.get('label'),str) or len(e['label'])>512: raise ValueError('Invalid event label')
        if not isinstance(e.get('score'),(int,float)) or not math.isfinite(e['score']) or not 0<=e['score']<=1: raise ValueError('Invalid event score')
    result['segments']=[s for s in segments if s['end']>s['start']]
    result['events']=[e for e in events if e['end']>e['start']]
    result['speaker_turns']=[t for t in turns if t['end']>t['start']]
    for t in result['speaker_turns']:
        if t.get('speaker_local_id') is not None:t['speaker_local_id']=str(t['speaker_local_id'])[:128]
    if result.get('overlap_candidates') is not None:validate_candidates(result['overlap_candidates'],duration)
    # Ensure metadata is finite JSON and bounded alongside all other output.
    if len(json.dumps(result,allow_nan=False))>8*1024**2: raise ValueError('Analyzer result too large')
    return result


def own_core(result,start,end,previous=None,base=0.):
    """Own lexical units when available, with legacy adapter fallback disclosed."""
    owned=lexical_core(result,start,end,previous,base)
    if owned is None:
        owned=[]
        for segment in result['segments']:
            midpoint=(segment['start']+segment['end'])/2
            if start<=midpoint<end:
                segment=dict(segment)
                segment['start']=max(start,segment['start'])-start
                segment['end']=min(end,segment['end'])-start
                owned.append(segment)
        result.setdefault('metadata',{})['boundary_policy']='legacy adapter without lexical units: approximate segment midpoint ownership'
    result['segments']=owned
    events=[]
    for event in result['events']:
        lo=max(start,event['start']);hi=min(end,event['end'])
        if hi>lo:
            event=dict(event);event['start']=lo-start;event['end']=hi-start;events.append(event)
    result['events']=events
    turns=[]
    for turn in result.get('speaker_turns',[]):
        lo=max(start,turn['start']);hi=min(end,turn['end'])
        if hi>lo:turns.append(dict(turn,start=lo-start,end=hi-start))
    result['speaker_turns']=turns
    candidates=[]
    for candidate in result.get('metadata',{}).get('event_candidates',[]):
        lo=max(start,candidate['start']);hi=min(end,candidate['end'])
        if hi>lo:
            candidates.append(dict(candidate,start=lo-start,end=hi-start,
                support_start=candidate['start']-start,support_end=candidate['end']-start,
                time_axis='core_relative',ownership='core_intersection',
                support_semantics='original model window; not verified event onset/end'))
    result['event_candidates']=candidates
    if result.get('overlap_candidates') is not None:
        result['overlap_candidates']=own_candidates(result['overlap_candidates'],start,end,base)
    overlap=result.get('metadata',{}).get('overlapping_speech')
    if overlap:
        regions=[]
        for region in overlap.get('regions',[]):
            lo=max(start,region['start']);hi=min(end,region['end'])
            if hi>lo:regions.append(dict(region,start=lo+base,end=hi+base))
        overlap=dict(overlap,regions=regions,time_axis='chunk_relative',
                     status='unresolved_mixture_asr' if regions else 'none_detected')
        result['metadata']['overlapping_speech']=overlap
    return result


class Worker:
    def __init__(self,store,settings,analyzer_factory=None):
        self.store=store; self.settings=settings; self.analyzer_factory=analyzer_factory
        self.stop_event=threading.Event(); self.wake=threading.Event(); self.current_chunk=None
        self.native=None
        self.thread=threading.Thread(target=self.loop,name='audio-worker',daemon=True)
    def start(self): self.thread.start()
    def stop(self):
        self.stop_event.set(); self.wake.set()
        if self.thread.is_alive(): self.thread.join(timeout=15)
        if not self.thread.is_alive() and self.native:self.native.close();self.native=None
    def notify(self): self.wake.set()
    def interrupted(self,cid):
        if self.stop_event.is_set(): return True
        try: return self.store.chunk(cid)['status']!='running'
        except KeyError: return True
    def loop(self):
        while not self.stop_event.is_set():
            chunk=self.store.claim()
            if chunk is None:
                self.wake.wait(1); self.wake.clear(); continue
            self.current_chunk=chunk['id']
            try: self.process(chunk)
            except InterruptedError:
                if self.stop_event.is_set():
                    with self.store.db() as db:
                        db.execute("UPDATE chunks SET status='queued' WHERE id=? AND status='running'",(chunk['id'],))
            except Exception as exc: self.store.fail(chunk['id'],f'{type(exc).__name__}: {exc}')
            finally: self.current_chunk=None
    def process(self,chunk):
        with tempfile.TemporaryDirectory(prefix='analyzer-',dir=self.settings.data_dir/'tmp') as tmp:
            config=Path(tmp)/'config.json'
            config.write_text(json.dumps(self.settings.inference_config()))
            process=None if self.analyzer_factory else AnalyzerProcess(self.settings,config)
            try: self._process_windows(chunk,process)
            except BaseException:
                if self.native:self.native.close();self.native=None
                raise
            finally:
                if process: process.close()

    def extract_pcm(self,chunk,start,end,wav,tmp,stop):
        parts=context_parts(self.store,chunk,start,end)
        with wave.open(str(wav),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
            for index,part in enumerate(parts):
                pcm=tmp/f'part-{index}.wav'
                run_bounded([self.settings.ffmpeg,'-nostdin','-v','error','-protocol_whitelist','file,pipe',
                    '-format_whitelist','wav,flac,ogg,mp3,mov,matroska,webm,aac','-ss',str(part['read_start']),
                    '-i',part['path'],'-t',str(part['read_duration']),'-map','0:a:0','-vn','-ac','1','-ar','16000',
                    '-c:a','pcm_s16le','-threads','1','-y',pcm],self.settings,timeout=180,stop=stop)
                with wave.open(str(pcm),'rb') as source:
                    while block:=source.readframes(16000):output.writeframesraw(block)
                pcm.unlink()

    def _process_windows(self,chunk,process):
        offset=chunk['next_offset']
        metadata=chunk.get('metadata') or {}
        if isinstance(metadata,str):metadata=json.loads(metadata)
        previous=metadata.get('boundary_tail',[])
        info=stream_info(self.store,chunk)
        if offset==0 and info and info['previous']:
            predecessor=info['previous'];saved=predecessor.get('metadata') or '{}'
            if isinstance(saved,str):saved=json.loads(saved)
            delta=predecessor['timeline_start']-chunk['timeline_start']
            previous=[dict(u,**{k:u[k]+delta for k in ('start','end','ownership_anchor')})
                      for u in saved.get('boundary_tail',[])]
        while offset<chunk['duration']-1e-5:
            if self.interrupted(chunk['id']): raise InterruptedError()
            check_resources(self.settings, 4*1024**2)
            info=stream_info(self.store,chunk)
            origin=chunk['timeline_start']
            limit=chunk['duration']
            if info and not info['final']:
                limit=min(limit,max(0.,info['end']-origin-self.settings.context_seconds))
            if limit<=offset+1e-5:
                wait_for_input(self.store,chunk['id'],info['end'],info['final'])
                return
            end=min(limit,offset+self.settings.window_seconds)
            extract_start=max(info['start']-origin if info else 0.,offset-self.settings.context_seconds)
            extract_end=min(info['end']-origin if info else chunk['duration'],end+self.settings.context_seconds)
            native=None
            if self.settings.diarization_backend=='nemotron':
                if self.native is None:self.native=NativeCoordinator(self.settings,self.extract_pcm)
                native=self.native.prepare(chunk,offset,end,info,lambda:self.interrupted(chunk['id']))
                if native is None:
                    if not info:raise RuntimeError('Finalized native source did not produce a stable core')
                    wait_for_input(self.store,chunk['id'],info['end'],info['final']);return
                end=native['end'];extract_start=native['extract_start'];extract_end=native['extract_end']
            with tempfile.TemporaryDirectory(prefix='window-',dir=self.settings.data_dir/'tmp') as tmp:
                tmp=Path(tmp); wav=tmp/'audio.wav'
                self.extract_pcm(chunk,extract_start+origin,extract_end+origin,wav,tmp,lambda:self.interrupted(chunk['id']))
                options=({'candidate_core_start':offset-extract_start,'candidate_core_end':end-extract_start,
                          'candidate_core_apply_safe_cut':end<chunk['duration']-1e-5,
                          'candidate_core_cut_lower':max(offset-extract_start+.5,end-extract_start-min(3.,self.settings.window_seconds*.25))}
                         if self.settings.overlap_backend!='none' else None)
                if self.analyzer_factory:
                    # Test-only dependency injection. Production uses monitored subprocesses.
                    analyzer=self.analyzer_factory(self.settings.inference_config())
                    arguments={}
                    if native:arguments['diarization']=native
                    if options is not None:arguments['analysis_options']=options
                    try:result=analyzer.analyze(str(wav),**arguments)
                    finally:analyzer.close()
                else:
                    arguments={}
                    if native:
                        diarization_path=tmp/'diarization.json'
                        diarization_path.write_text(json.dumps({'turns':native['turns'],'provenance':native['provenance']}))
                        arguments['diarization_path']=diarization_path
                    if options is not None:arguments['analysis_options']=options
                    result=process.analyze(wav,tmp/'result.json',lambda:self.interrupted(chunk['id']),**arguments)
                result=validate_result(result,extract_end-extract_start)
                if native:
                    result.setdefault('metadata',{}).update(native['provenance'])
                    result['metadata']['native_diarization']=native['state']
                    if result['metadata'].get('silence_fast_path'):
                        result['metadata']['model_execution']='native_diarization_only_asr_skipped'
                        result['metadata']['models_executed']=['Nemotron-3']
                        result['metadata']['model_execution_note']='Stateful native diarization ran; silence bypassed ASR, embeddings and event model.'
                nominal_end=end
                if end<chunk['duration']-1e-5:
                    local=safe_cut(result.get('metadata',{}).get('diarization_turns',[]),
                                   end-extract_start,
                                   max(offset-extract_start+.5,end-extract_start-min(3.,self.settings.window_seconds*.25)),
                                   extract_end-extract_start)
                    end=extract_start+local
                result=own_core(result,offset-extract_start,end-extract_start,previous,extract_start)
                result.setdefault('metadata',{})['core_cut']={'nominal':nominal_end,'actual':end,'reason':'neural_speech_gap' if end<nominal_end else 'fixed_or_final'}
                previous=result['metadata'].get('boundary_tail',[])
                if self.interrupted(chunk['id']): raise InterruptedError()
                if not self.store.commit_window(chunk,offset,end,result): return
            offset=end
