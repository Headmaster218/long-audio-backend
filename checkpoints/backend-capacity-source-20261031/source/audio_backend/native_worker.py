"""Stateful diarizer scheduling independent of ASR upload/core boundaries.

One native child is live. Process loss or scope changes replay at most one
300-second owned epoch plus21.2seconds preroll and one emitted-batch lookahead.
No opaque native state, complete waveform, or whole-recording posterior is saved.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave
import psutil
from .resources import check_resources,kill_tree


class NativeProcess:
    def __init__(self,settings,*,managed_transport=None):
        self.managed_transport=managed_transport
        if managed_transport is not None:
            self.proc=managed_transport.proc
            return
        self.settings=settings;self.counter=0;self.busy=False
        self.temp=tempfile.TemporaryDirectory(prefix='native-diar-',dir=settings.data_dir/'tmp')
        self.root=Path(self.temp.name);config=self.root/'config.json'
        config.write_text(json.dumps(settings.inference_config()))
        self.proc=subprocess.Popen([sys.executable,'-m','audio_backend.native_runner',str(config)],
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True,bufsize=1,
            env={**os.environ,'AUDIO_PARENT_PID':str(os.getpid()),
                 'AUDIO_PARENT_CREATED':str(psutil.Process().create_time()),
                 'OMP_NUM_THREADS':'2','OMP_THREAD_LIMIT':'2','OPENBLAS_NUM_THREADS':'2'})
    def request(self,action,stop,**kwargs):
        if getattr(self, 'managed_transport', None) is not None:
            result=self.managed_transport.request(action,stop,**kwargs)
            observer=getattr(self,'response_observer',None)
            if observer is not None:observer(result)
            return result
        if psutil.virtual_memory().available<1536*1024**2:raise RuntimeError('Native diarization requires1.5GiB free host memory safety reserve')
        if self.proc.poll() is not None:raise RuntimeError('Native diarizer process exited')
        self.counter+=1;output=self.root/f'{self.counter}.json';self.busy=True
        self.proc.stdin.write(json.dumps({'action':action,'output':str(output),**kwargs})+'\n');self.proc.stdin.flush()
        began=time.monotonic()
        while not output.exists():
            if stop():raise InterruptedError('Native diarization interrupted')
            if self.proc.poll() is not None:raise RuntimeError(f'Native diarizer exited with code {self.proc.returncode}')
            if time.monotonic()-began>1200:raise TimeoutError('Native request exceeded watchdog')
            check_resources(self.settings)
            if psutil.virtual_memory().available<1536*1024**2:raise RuntimeError('Native diarization host memory safety reserve breached')
            time.sleep(.1)
        if output.stat().st_size>8*1024**2:raise RuntimeError('Native result exceeds8MiB')
        result=json.loads(output.read_text())
        observer=getattr(self,'response_observer',None)
        if observer is not None:observer(result)
        output.unlink();self.busy=False
        if '_error' in result:raise RuntimeError(result['_error'])
        return result
    def close(self):
        if getattr(self, 'managed_transport', None) is not None:
            return self.managed_transport.close()
        try:
            if self.busy:kill_tree(self.proc)
            if self.proc.stdin:self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except (BrokenPipeError,subprocess.TimeoutExpired):kill_tree(self.proc)
        self.temp.cleanup()


class NativeCoordinator:
    EPOCH_SECONDS=300.
    PREROLL_SECONDS=21.2
    PUSH_SECONDS=21.2
    def __init__(self,settings,extract,process_factory=NativeProcess):
        self.settings=settings;self.extract=extract;self.factory=process_factory
        self.process=None;self.key=None;self.state=None;self.provenance={}
    def close(self):
        if self.process:self.process.close()
        self.process=None;self.key=None;self.state=None
    def prepare(self,chunk,offset,end,info,stop):
        """Return a core and context backed by stable native activity, or None.

        `offset/end` are current-upload-relative. `extract` reads a bounded
        absolute source interval from this explicit stream or isolated upload.
        """
        origin=chunk['timeline_start'];context=round(self.settings.context_seconds*16000)
        scope_start=info['start'] if info else origin
        # All scheduling bounds are integer frames relative to this source/stream.
        # Absolute timeline origins need not lie on the 16 kHz sample grid, and
        # old six-decimal duration receipts may fall just below a sample edge.
        origin_frame=round((origin-scope_start)*16000)
        available_end=round((info['end']-scope_start)*16000) if info else round(chunk['duration']*16000)
        final=info['final'] if info else True
        scope=('stream',chunk['session_id'],info['stream_id']) if info else ('upload',chunk['session_id'],chunk['id'])
        absolute_offset=origin_frame+round(offset*16000)
        epoch_index=absolute_offset//round(self.EPOCH_SECONDS*16000)
        owned_start=epoch_index*round(self.EPOCH_SECONDS*16000)
        owned_end=owned_start+round(self.EPOCH_SECONDS*16000)
        replay_start=max(0,owned_start-round(self.PREROLL_SECONDS*16000))
        desired_end=min(origin_frame+round(end*16000),owned_end)
        epoch_key=hashlib.sha256(json.dumps([scope,epoch_index]).encode()).hexdigest()[:32]
        if self.key!=epoch_key:
            if self.process is None:self.process=self.factory(self.settings)
            response=self.process.request('reset',stop,epoch_id=epoch_key)
            self.provenance=response['provenance'];self.key=epoch_key
            self.state=response.get('state') or {'input_seconds':0.,'processed_seconds':0.,'stable_seconds':0.,'finished':False}
            # Fail clearly rather than replay under changed weights/settings.
            saved=chunk.get('metadata') or (info.get('previous',{}).get('metadata') if info and info.get('previous') else None) or {}
            if isinstance(saved,str):saved=json.loads(saved)
            prior=saved.get('native_diarization',{}).get('provenance',{})
            if not prior.get('diarization_fingerprint') and info and info.get('previous'):
                prior_metadata=info['previous'].get('metadata') or {}
                if isinstance(prior_metadata,str):prior_metadata=json.loads(prior_metadata)
                prior=prior_metadata.get('native_diarization',{}).get('provenance',{})
            if prior.get('diarization_fingerprint') and prior['diarization_fingerprint']!=self.provenance.get('diarization_fingerprint'):
                raise RuntimeError('Native diarization fingerprint changed; resume requires original model/runtime')
        target_context_end=min(available_end,desired_end+context)
        while (replay_start+round(self.state['processed_seconds']*16000)<target_context_end or
               replay_start+round(self.state['stable_seconds']*16000)<desired_end):
            input_end=replay_start+round(self.state['input_seconds']*16000)
            if input_end>=available_end:
                if final and not self.state.get('finished'):
                    self.state=self.process.request('finish',stop)
                break
            push_end=min(available_end,input_end+round(self.PUSH_SECONDS*16000))
            if push_end-replay_start>360*16000:raise RuntimeError('Native epoch replay exceeded360seconds')
            with tempfile.TemporaryDirectory(prefix='native-pcm-',dir=self.settings.data_dir/'tmp') as td:
                wav=Path(td)/'audio.wav'
                self.extract(chunk,scope_start+input_end/16000,scope_start+push_end/16000,wav,Path(td),stop)
                with wave.open(str(wav),'rb') as source:
                    if (source.getframerate(),source.getnchannels(),source.getsampwidth())!=(16000,1,2):
                        raise RuntimeError('Native input must be canonical 16 kHz mono PCM16')
                    frames=source.getnframes()
                if frames!=push_end-input_end:
                    raise RuntimeError(f'Source duration does not match decoded PCM: expected {push_end-input_end} native frames, got {frames}')
                self.state=self.process.request('push',stop,wav=str(wav))
                if replay_start+round(self.state['input_seconds']*16000)!=push_end:
                    raise RuntimeError('Native input did not advance by decoded frame count')
        processed=replay_start+round(self.state['processed_seconds']*16000)
        stable=replay_start+round(self.state['stable_seconds']*16000)
        # At true EOS all actual samples are finalized; no fictional right context.
        eos=bool(self.state.get('finished'))
        core_end=min(desired_end,stable,processed if eos else processed-context)
        if core_end<=absolute_offset:return None
        extract_start=max(0,absolute_offset-context)
        extract_end=min(available_end,core_end+context,processed)
        response=self.process.request('turns',stop,start=max(0.,(extract_start-replay_start)/16000),end=(extract_end-replay_start)/16000)
        turns=[]
        for t in response['turns']:
            a=max(extract_start/16000,replay_start/16000+t['start']);b=min(extract_end/16000,replay_start/16000+t['end'])
            if b>a:turns.append(dict(t,start=a-extract_start/16000,end=b-extract_start/16000))
        return {'end':(core_end-origin_frame)/16000,'extract_start':(extract_start-origin_frame)/16000,'extract_end':(extract_end-origin_frame)/16000,
                'turns':turns,'provenance':self.provenance,
                'state':{'epoch_id':epoch_key,'epoch_index':epoch_index,'owned_start':scope_start+owned_start/16000,
                    'owned_end':scope_start+owned_end/16000,'replay_start':scope_start+replay_start/16000,
                    'input_until':scope_start+replay_start/16000+self.state['input_seconds'],
                    'processed_until':scope_start+processed/16000,'stable_until':scope_start+stable/16000,'finished':eos,
                    'provenance':self.provenance}}
