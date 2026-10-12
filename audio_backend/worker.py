import json
import math
import shutil
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
from . import speaker_support, event_candidate_store


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
    result['event_candidates']=event_candidate_store.own_candidates(result.setdefault('metadata',{}),start,end)
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
        self.native_owned_support_cache=None
        self._native_capture=None;self._native_capture_token=None
        self._native_capture_observation=None
        self.support_fingerprints=speaker_support.SourceFingerprints()
        self.thread=threading.Thread(target=self.loop,name='audio-worker',daemon=True)
    def start(self): self.thread.start()
    def stop(self):
        if self.native_owned_support_cache is not None:
            try:self.native_owned_support_cache.close()
            except Exception:pass
        self.stop_event.set(); self.wake.set()
        if self.thread.is_alive(): self.thread.join(timeout=15)
        if not self.thread.is_alive() and self.native:self.native.close();self.native=None
        return not self.thread.is_alive()
    def notify(self): self.wake.set()
    def interrupted(self,cid):
        if self.stop_event.is_set(): return True
        try: return self.store.chunk(cid)['status']!='running'
        except KeyError: return True
    def loop(self):
        while not self.stop_event.is_set():
            fragments=getattr(self.store,'fragment_http',None)
            permit=getattr(self.settings, '_native_execution_permit', None)
            if permit is not None and fragments and fragments.factory is not None:
                permit.close_admission()
            if fragments and fragments.cleanup_error is not None:
                self.wake.wait(1); self.wake.clear(); continue
            if permit is None and fragments and fragments.process_next(self):
                continue
            watermark=None
            if getattr(self.settings,'native_owned_support_enabled',False) and self.native_owned_support_cache is not None:
                try:
                    self.native_owned_support_cache.drain()
                    watermark=self.native_owned_support_cache.preclaim()
                except Exception:
                    # Optional observation never takes authority over primary work.
                    pass
            intent=None
            if permit is None:
                chunk=self.store.claim()
            else:
                with permit.claim_probe():
                    chunk=self.store.claim()
                    if chunk is not None:
                        self.current_chunk=chunk['id']
                        intent=permit.primary_intent('worker')
            if chunk is None:
                self.wake.wait(1); self.wake.clear(); continue
            self.current_chunk=chunk['id']
            normal=False
            try:
                if intent is not None:intent.wait(lambda:self.interrupted(chunk['id']))
                self._begin_native_support(chunk,watermark)
                self.process(chunk)
                normal=True
            except InterruptedError:
                if self.stop_event.is_set():
                    with self.store.db() as db:
                        db.execute("UPDATE chunks SET status='queued' WHERE id=? AND status='running'",(chunk['id'],))
            except Exception as exc: self.store.fail(chunk['id'],f'{type(exc).__name__}: {exc}')
            finally:
                self._finish_native_support()
                if intent is not None:
                    if intent.entered:permit.finish_cohort(normal=normal)
                    intent.release()
                self.current_chunk=None

    def _begin_native_support(self,chunk,watermark):
        cache=self.native_owned_support_cache
        if not getattr(self.settings,'native_owned_support_enabled',False) or cache is None or watermark is None:
            return
        try:
            if self.settings.diarization_backend!='nemotron':return
            token=cache.begin(chunk,watermark)
            if token is None:return
            self._native_capture_token=token
            from .native_owned_support import SourceMaterial,NativeWitness
            self._native_capture=NativeWitness(SourceMaterial.inspect(chunk))
        except Exception:
            self._finish_native_support()

    def _finish_native_support(self):
        token=self._native_capture_token
        self._native_capture=None;self._native_capture_token=None
        self._native_capture_observation=None
        if token is not None:
            try:self.native_owned_support_cache.fail(token,'capture_unavailable')
            except Exception:pass

    def _native_response(self,response):
        observation=self._native_capture_observation
        if observation is not None:
            token,witness=observation
            if token is not self._native_capture_token or witness is not self._native_capture:return
            try:witness.observe(response)
            except Exception:
                try:witness.fail('response_observation_unavailable')
                except Exception:self._native_capture=None

    def _native_process_factory(self,settings):
        from .native_worker import NativeProcess
        process=NativeProcess(settings)
        permit=getattr(settings, '_native_execution_permit', None)
        if permit is not None:
            permit.bind_primary(process, resident=lambda: self.native is not None
                and self.native.process is process and self.native.key is not None
                and self.native.state is not None and not process.busy)
        # Construction is never retried if optional attachment fails.
        try:
            prior=getattr(process,'response_observer',None)
            def observe(response):
                if prior is not None:prior(response)
                self._native_response(response)
            process.response_observer=observe
        except Exception:
            self._finish_native_support()
        return process

    def _observe_native_pcm(self,kind,wav,start,end):
        witness=self._native_capture
        if witness is not None:
            try:witness.observe_pcm(kind,wav,start,end)
            except Exception:
                try:witness.fail('material_observation_unavailable')
                except Exception:self._native_capture=None

    def _native_extract(self,chunk,start,end,wav,tmp,stop,**kwargs):
        result=self.extract_pcm(chunk,start,end,wav,tmp,stop,**kwargs)
        self._observe_native_pcm('native',wav,start,end)
        return result

    def _publish_native_support(self,native,offset,end):
        witness=self._native_capture;token=self._native_capture_token
        if witness is None or token is None:return
        try:
            artifact=witness.finalize(native,offset,end)
            self.native_owned_support_cache.publish(token,artifact)
        except Exception:pass
        finally:self._finish_native_support()
    def process(self,chunk):
        with tempfile.TemporaryDirectory(prefix='analyzer-',dir=self.settings.data_dir/'tmp') as tmp:
            config=Path(tmp)/'config.json'
            config.write_text(json.dumps(self.settings.inference_config()))
            process=None if self.analyzer_factory else AnalyzerProcess(self.settings,config)
            permit=getattr(self.settings, '_native_execution_permit', None)
            if process and permit is not None:permit.bind_primary(process)
            try: self._process_windows(chunk,process)
            except BaseException:
                if self.native:self.native.close();self.native=None
                raise
            finally:
                if process:
                    try:process.close()
                    finally:
                        if permit is not None:permit.finish_primary(process)

    def extract_pcm(self,chunk,start,end,wav,tmp,stop,*,capture_support=False):
        parts=context_parts(self.store,chunk,start,end)
        lineage={'status':'complete','parts':[],'analyzer_frame_count':0}
        if capture_support:
            try:
                if len(parts)>speaker_support.MAX_PARTS:
                    raise ValueError('Too many source parts for review')
                executable=shutil.which(self.settings.ffmpeg)
                if not executable:raise ValueError('Missing decoder')
                lineage['decoder_sha256']=self.support_fingerprints.get(executable,stop)[0]
            except (OSError,ValueError,RuntimeError):
                lineage['status']='decoder_or_parts_unavailable'
        with wave.open(str(wav),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
            for index,part in enumerate(parts):
                source_digest=signature=None
                if capture_support and lineage['status']=='complete':
                    try:
                        source_digest,signature=self.support_fingerprints.get(part['path'],stop)
                        with self.store.db() as db:
                            row=db.execute('SELECT digest,session_id FROM chunks WHERE id=?',(part['id'],)).fetchone()
                        if not row or row['digest']!=source_digest or row['session_id']!=chunk['session_id']:
                            lineage['status']='source_content_mismatch'
                    except (OSError,ValueError,RuntimeError):
                        lineage['status']='source_lineage_unavailable'
                pcm=tmp/f'part-{index}.wav'
                requested_frames=round(part['read_duration']*16000)
                # Native scheduling requires an exact canonical frame count.
                # Legacy non-native windows retain measured resampled ranges.
                # Only the declared end of an entire source gets an EOF check.
                # One extra requested sample exposes an underestimated duration;
                # a short clean decode exposes an overestimate. Neither is padded
                # or silently clipped to the immutable declared source timeline.
                source_end=round((part['read_start']+part['read_duration'])*16000)>=round(part['duration']*16000)
                verify_source_end=source_end and self.settings.diarization_backend=='nemotron'
                decode_duration=(requested_frames+1)/16000 if verify_source_end else part['read_duration']
                # FFmpeg duration syntax rejects Python's scientific notation
                # for short intervals such as the first 1/16000-second sample.
                run_bounded([self.settings.ffmpeg,'-nostdin','-v','error','-xerror','-protocol_whitelist','file,pipe',
                    '-format_whitelist','wav,flac,ogg,mp3,mov,matroska,webm,aac','-ss',f"{part['read_start']:.9f}",
                    '-i',part['path'],'-t',f'{decode_duration:.9f}','-map','0:a:0','-vn','-ac','1','-ar','16000',
                    '-c:a','pcm_s16le','-threads','1','-y',pcm],self.settings,timeout=180,stop=stop)
                frames=0
                with wave.open(str(pcm),'rb') as source:
                    while block:=source.readframes(16000):
                        output.writeframesraw(block)
                        frames+=len(block)//2
                pcm.unlink()
                if verify_source_end and frames!=requested_frames:
                    raise RuntimeError(f'Source duration does not match decoded PCM: expected {requested_frames} frames, got {frames}; source timeline was not changed')
                first=lineage['analyzer_frame_count'];lineage['analyzer_frame_count']+=frames
                if capture_support and lineage['status']=='complete':
                    try:
                        if self.support_fingerprints.signature(part['path'])!=signature:
                            raise ValueError('Source changed while decoding')
                        lineage['parts'].append({'chunk_id':part['id'],'source_sha256':source_digest,
                            'seek_seconds':part['read_start'],'requested_seconds':part['read_duration'],
                            'analyzer_frames':[first,first+frames]})
                    except (OSError,ValueError,RuntimeError):
                        lineage['status']='source_changed_during_decode'
        return lineage if capture_support else None

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
                if self.native is None:
                    if getattr(self.settings,'native_owned_support_enabled',False) and self.native_owned_support_cache is not None:
                        self.native=NativeCoordinator(self.settings,self._native_extract,process_factory=self._native_process_factory)
                    else:self.native=NativeCoordinator(self.settings,self.extract_pcm)
                if self._native_capture is not None:
                    self._native_capture_observation=(self._native_capture_token,self._native_capture)
                try:native=self.native.prepare(chunk,offset,end,info,lambda:self.interrupted(chunk['id']))
                finally:self._native_capture_observation=None
                if native is None:
                    if not info:raise RuntimeError('Finalized native source did not produce a stable core')
                    wait_for_input(self.store,chunk['id'],info['end'],info['final']);return
                end=native['end'];extract_start=native['extract_start'];extract_end=native['extract_end']
            with tempfile.TemporaryDirectory(prefix='window-',dir=self.settings.data_dir/'tmp') as tmp:
                tmp=Path(tmp); wav=tmp/'audio.wav'
                from .verified_identity import is_verified
                with self.store.db() as db:verified_query=is_verified(db,chunk['session_id'])
                capture_support=self.settings.speaker_support_enabled and (self.settings.speaker_support_max_records>0 or verified_query)
                expected_generation=speaker_support.capture_generation(self.store,chunk) if capture_support else None
                lineage=self.extract_pcm(chunk,extract_start+origin,extract_end+origin,wav,tmp,lambda:self.interrupted(chunk['id']),capture_support=capture_support)
                self._observe_native_pcm('analyzer',wav,extract_start+origin,extract_end+origin)
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
                try:
                    prepared=(speaker_support.prepare_window(result,wav,lineage,expected_generation,
                        core_start=offset-extract_start,analyzer_start=extract_start,settings=self.settings) if capture_support else {})
                except (ValueError,TypeError,OSError,RuntimeError,ImportError):
                    prepared={}
                result=own_core(result,offset-extract_start,end-extract_start,previous,extract_start)
                result['_speaker_support']=prepared
                result.setdefault('metadata',{})['core_cut']={'nominal':nominal_end,'actual':end,'reason':'neural_speech_gap' if end<nominal_end else 'fixed_or_final'}
                previous=result['metadata'].get('boundary_tail',[])
                if self.interrupted(chunk['id']): raise InterruptedError()
                if not self.store.commit_window(chunk,offset,end,result): return
                self._publish_native_support(native,offset,end)
            offset=end
