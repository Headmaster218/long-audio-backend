"""Optional offline two-source separator; imports Torch only in isolated worker."""
from __future__ import annotations
import sys
import hashlib
from pathlib import Path
import numpy as np
from ..overlap import official_input_normalization, asr_safe_source, source_permutation
ROOT=Path(__file__).resolve().parents[2]
MODEL_REVISION='407cb030cd66340918ebb6c8cc63b18f8592cdbe'
SOURCE_REVISION='6b3774dc79c46ae8bed2a4fa5f706f0ac8c75c61'


class MossFormerSeparator:
    def __init__(self,config):
        import torch
        threads=min(2,max(1,int(config.get('overlap_threads',2))))
        torch.set_num_threads(threads);torch.set_num_interop_threads(1)
        source=Path(config.get('overlap_source_dir') or ROOT/'vendor/clearvoice-research/source/clearvoice/clearvoice/models')
        sys.path.insert(0,str(source))
        from mossformer2_ss.mossformer2 import MossFormer
        self.model=MossFormer(in_channels=512,out_channels=512,num_blocks=24,kernel_size=16,num_spks=2)
        weights=Path(config.get('model_dir',ROOT/'models'))/'mossformer2/model-weights-only.pt'
        digest=hashlib.sha256()
        with weights.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        self.weights_sha256=digest.hexdigest()
        state=torch.load(weights,map_location='cpu',weights_only=True,mmap=True)
        self.model.load_state_dict(state,strict=True,assign=True);self.model.eval()
        self.mode=config.get('overlap_window_mode','official_2s')
        if self.mode not in ('official_2s','direct_bounded'):raise ValueError('Invalid overlap window mode')

    def separate(self,samples):
        """Return scope-local waveforms. Ambiguous seams create fresh scopes."""
        import torch
        x,gain=official_input_normalization(samples);length=len(x);sr=16000
        if not 0<length<=12*sr:raise ValueError('Separator requires <=12s per request')
        if self.mode=='direct_bounded':window=length;hop=length
        else:window=32000;hop=24000
        shared=window-hop;trim=shared//2
        starts=list(range(0,max(1,length-window+hop),hop))
        if starts[-1]+window<length:starts.append(starts[-1]+hop)
        tracks={};windows=[];previous=None;scope=-1
        for index,start in enumerate(starts):
            batch=np.zeros(window,dtype=np.float32);crop=x[start:start+window];batch[:len(crop)]=crop
            with torch.inference_mode():outputs=self.model(torch.from_numpy(batch[None]))
            y=np.stack([a[0].numpy() for a in outputs])
            if y.shape!=(2,window) or not np.isfinite(y).all():raise ValueError('Invalid separator result')
            match=(source_permutation(previous[:,-shared:],y[:,:shared]) if previous is not None and shared
                   else {'status':'new_scope','permutation':None,'identity_claim':False})
            if match['status']=='matched':y=y[match['permutation']]
            else:scope+=1
            previous=y
            lo=0 if index==0 else trim
            hi=min(window if index==len(starts)-1 else window-trim,length-start)
            windows.append({'start':start/sr,'end':min(length,start+window)/sr,
                            'core_start':(start+lo)/sr,'core_end':(start+hi)/sr,
                            'scope_id':f'scope-{scope}','continuity':match})
            for slot,row in enumerate(y):
                key=(scope,slot)
                if key not in tracks:tracks[key]={'samples':np.zeros(length,dtype=np.float32),'start':(start+lo)/sr,'end':(start+hi)/sr,'scope_id':f'scope-{scope}','source_slot':slot}
                tracks[key]['samples'][start+lo:start+hi]=row[lo:hi]
                tracks[key]['end']=(start+hi)/sr
        target_rms=float(np.sqrt(np.mean(x*x)))
        for track in tracks.values():
            lo,hi=round(track['start']*sr),round(track['end']*sr)
            track['samples'],track['asr_scale']=asr_safe_source(track['samples'][lo:hi],target_rms)
        return {'tracks':list(tracks.values()),'windows':windows,'input_gain':gain,
                'scope_count':scope+1,'all_seams_matched':scope==0,
                'provenance':{'model_id':'alibabasglab/MossFormer2_SS_16K','model_revision':MODEL_REVISION,
                'source_revision':SOURCE_REVISION,'weights_sha256':self.weights_sha256,'license':'Apache-2.0','sample_rate':16000,
                'capacity':2,'device':'cpu','window_mode':self.mode,'source_identity_semantics':'scope-local neural source; not a person',
                'output_normalization':'ASR-only RMS normalization and .95 peak guard; scaling is not activity evidence'}}
