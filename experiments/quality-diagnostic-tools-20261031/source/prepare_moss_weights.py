"""Pinned weights-only extraction, no model construction or inference."""
import hashlib,importlib.metadata,json,os,resource,sys
from pathlib import Path
EXPECTED='00a3a48bda492db1e829b85dd443f8f43a43039a3e90f1a24962ea9caf14a11a'
def main(source,destination,receipt):
 assert importlib.metadata.version('torch')=='2.10.0+cpu'
 src=Path(source);out=Path(destination);assert not out.exists() and src.stat().st_size==670353271
 with src.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==EXPECTED
 os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:2]);resource.setrlimit(resource.RLIMIT_AS,(6*1024**3,6*1024**3))
 import torch
 assert torch.version.cuda is None
 torch.set_num_threads(2);torch.set_num_interop_threads(1)
 obj=torch.load(src,map_location='cpu',weights_only=True,mmap=True)
 state=obj['model'];assert isinstance(state,dict) and state and all(isinstance(k,str) and isinstance(v,torch.Tensor) for k,v in state.items())
 torch.save(state,out)
 restored=torch.load(out,map_location='cpu',weights_only=True,mmap=True)
 assert state.keys()==restored.keys() and all(torch.equal(v,restored[k]) for k,v in state.items())
 with out.open('rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()
 Path(receipt).write_text(json.dumps(dict(original_sha256=EXPECTED,derived_sha256=h,derived_bytes=out.stat().st_size,tensors=len(state),parameters=sum(v.numel() for v in state.values()),tensor_equal=True,torch_version=torch.__version__,no_model_constructed=True,no_inference=True),indent=2))
if __name__=='__main__':main(*sys.argv[1:])
