"""Inspected, pinned SE-DiCoW BF16 CPU runtime. English candidates only."""
import importlib,json,os,re,sys,types
from pathlib import Path
from ..dicow_assets import verify_model_directory

class DiCoWRecognizer:
    def __init__(self,config):
        os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
        import torch,transformers,safetensors
        if torch.__version__!='2.6.0+cpu' or transformers.__version__!='4.55.0' or safetensors.__version__!='0.6.2':
            raise ImportError('Use pinned CPU Torch2.6.0+cpu, Transformers4.55.0 and Safetensors0.6.2')
        self.torch=torch
        torch.set_num_threads(2);torch.set_num_interop_threads(1)
        directory=Path(config['dicow_model_dir']).expanduser().resolve()
        self.manifest=verify_model_directory(directory)
        # Create only a namespace package; do not execute an unchecked __init__.py.
        package='_audio_backend_pinned_dicow'
        module=types.ModuleType(package);module.__path__=[str(directory)];sys.modules[package]=module
        cls=importlib.import_module(package+'.modeling_dicow').DiCoWForConditionalGeneration
        from transformers import AutoFeatureExtractor,AutoTokenizer
        transformers.utils.logging.set_verbosity_error()
        self.model=cls.from_pretrained(directory,torch_dtype=torch.bfloat16,low_cpu_mem_usage=True,
            local_files_only=True,attn_implementation='sdpa').eval()
        self.extractor=AutoFeatureExtractor.from_pretrained(directory,local_files_only=True)
        self.tokenizer=AutoTokenizer.from_pretrained(directory,local_files_only=True)
        # Avoid the upstream set_tokenizer's training-only300MB soft-label matrix.
        self.model.tokenizer=self.tokenizer

    def _read(self,path):
        import soundfile as sf,numpy as np
        with sf.SoundFile(path) as f:
            if f.samplerate!=16000 or f.channels!=1 or not 0<len(f)<=30*16000:raise ValueError('Expected bounded16k mono audio')
            samples=f.read(dtype='float32')
        if not np.isfinite(samples).all():raise ValueError('Nonfinite DiCoW input')
        return samples

    def _mask(self,rows,target,duration):
        t=self.torch
        if not isinstance(rows,list) or len(rows)>4096:raise ValueError('Invalid conditioning intervals')
        ids=sorted({r['speaker'] for r in rows}|{target})
        if len(ids)>8:raise ValueError('DiCoW conditioning capacity exceeded')
        diar=t.zeros(len(ids),1500)
        for row in rows:
            lo,hi=float(row['start']),float(row['end'])
            if not 0<=lo<hi<=duration+.001:raise ValueError('Conditioning interval outside audio')
            diar[ids.index(row['speaker']),round(lo*50):min(1500,round(hi*50))]=1
        ti=ids.index(target);other=t.ones(len(ids),dtype=t.bool);other[ti]=False
        silence=(1-diar).prod(0);no_other=(1-diar[other]).prod(0)
        target_only=diar[ti]*no_other
        mask=t.stack([silence,target_only,(1-diar[ti])*(1-no_other),diar[ti]-target_only])
        if not t.all(mask.sum(0)==1):raise ValueError('Invalid STNO normalization')
        return mask[None].to(t.bfloat16)

    def transcribe(self,job,target):
        t=self.torch
        wav=self._read(job['path']);enrollment=self._read(target['enrollment_path'])
        duration=len(wav)/16000
        f=self.extractor(wav,sampling_rate=16000,return_tensors='pt',return_attention_mask=True)
        e=self.extractor(enrollment,sampling_rate=16000,return_tensors='pt',return_attention_mask=True)
        with t.inference_mode():
            tokens=self.model.generate(input_features=f.input_features.to(t.bfloat16),attention_mask=f.attention_mask,
                stno_mask=self._mask(job['intervals'],target['conditioning_local_id'],duration),
                enrollments={'input_features':e.input_features.to(t.bfloat16),'attention_mask':e.attention_mask,
                    'stno_mask':self._mask(target['enrollment_intervals'],target['conditioning_local_id'],len(enrollment)/16000)},
                language='en',task='transcribe',return_timestamps=True,num_beams=1,do_sample=False,
                max_new_tokens=160,condition_on_prev_tokens=False)
        seq=tokens['sequences'] if isinstance(tokens,dict) else tokens
        raw=self.tokenizer.batch_decode(seq,skip_special_tokens=False,decode_with_timestamps=True)[0]
        text=' '.join(re.sub(r'<\|[^|]+\|>',' ',raw).split())
        marks=list(re.finditer(r'<\|(\d+(?:\.\d+)?)\|>',raw));phrases=[]
        for first,last in zip(marks,marks[1:]):
            words=' '.join(re.sub(r'<\|[^|]+\|>',' ',raw[first.end():last.start()]).split())
            if not words:continue
            lo,hi=float(first.group(1)),float(last.group(1))
            if not 0<=lo<hi<=duration+.15:raise ValueError('Decoded native phrase timestamp outside input')
            phrases.append({'text':words,'raw_text':words,'start':lo,'end':min(hi,duration),
                            'ownership_anchor':lo,'unit_kind':'native_phrase','confidence':None})
        if text and not phrases:raise ValueError('Nonempty DiCoW text lacks usable native phrase timestamps')
        return {'text':text,'raw_text':text,'native_tokens':raw,'phrases':phrases,
                'quality_status':'unverified' if text else 'no_text'}
