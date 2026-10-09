#!/usr/bin/env python3
"""Optional pinned official downloads for research-only overlap experiments.

Use a separate CPU Torch environment. This script never installs software,
changes default backend settings, or uploads recordings.
"""
import argparse,json,tempfile
from pathlib import Path
from setup_models import ROOT,download,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--model',choices=['mossformer','sepformer','both'],default='mossformer');p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    manifest=json.loads((ROOT/'overlap-model-manifest.json').read_text())
    for item in manifest['downloads']:
        if args.model!='both' and item['group']!=args.model:continue
        if args.verify_only:
            path=ROOT/item['destination']
            if not path.is_file() or path.stat().st_size!=item['bytes'] or sha(path)!=item['sha256']:raise RuntimeError('Missing/corrupt '+str(path))
        else:download(item)
    if args.model in ('mossformer','both'):
        import torch
        root=ROOT/'models/mossformer2';original=root/'last_best_checkpoint.pt';derived=root/'model-weights-only.pt'
        original_state=torch.load(original,map_location='cpu',weights_only=True,mmap=True)['model']
        if not args.verify_only:
            with tempfile.TemporaryDirectory(dir=root) as td:
                path=Path(td)/derived.name;torch.save(original_state,path);path.replace(derived)
        if not derived.is_file():raise RuntimeError('Missing model-only checkpoint; rerun without --verify-only')
        state=torch.load(derived,map_location='cpu',weights_only=True,mmap=True)
        if state.keys()!=original_state.keys() or not all(torch.equal(v,original_state[k]) for k,v in state.items()):raise RuntimeError('Derived model weights differ from pinned original')
        info={'official_model':'https://huggingface.co/alibabasglab/MossFormer2_SS_16K','revision':manifest['models']['mossformer']['revision'],'original_sha256':sha(original),'derived':'model state only, verified bit-exact tensor equality','derived_sha256':sha(derived),'license':'Apache-2.0','source_revision':manifest['models']['mossformer']['source_revision'],'capacity':'two sources, never identities','sample_rate':16000}
        if not args.verify_only:(root/'provenance.json').write_text(json.dumps(info,indent=2))
    print('Pinned optional overlap model/source files verified; default backend unchanged.')
if __name__=='__main__':main()
