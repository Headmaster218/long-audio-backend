# PUBLIC SOURCE INSPECTION ONLY. This is not a runnable distribution.
# Prior role-admission/eviction tests remain preserved in the frozen earlier runner; replaced by namespace accounting controls here.
"""Finite adapter controls authored for review; NOT executed in preparation."""

raise RuntimeError("Inspection-only transformed excerpt: do not execute or import; private dependencies and pins are intentionally omitted.")

import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from adapter import Ledger, Operations, bounded_family, encoded
from endurance import Plan, Stop, FRAMES, MIB
from passive import compare


class ListLedger(list):
    pass


class BareFamily:
    class Vanished(Exception):
        def __init__(self,pid): self.pid=pid
    def sample(self): pass


def fake_family():
    lifetime=dict(exits=ListLedger(),details=ListLedger(),samples=ListLedger(),peak_charge=0,peak_rss=0,max_gap=0,roles=set())
    cls=bounded_family(SimpleNamespace(Family=BareFamily),{},lifetime)
    f=cls.__new__(cls); f.lock=threading.RLock(); f.pid_index={1:(1,10)}
    f.authenticated={(1,10):dict(process=object(),kind='analyzer',kernel=dict(pid=1,ticks=10))}
    f.observed={(1,10):object()}; f.exit_ledger=lifetime['exits']
    f.peak_charge=f.peak_rss=f.max_gap=0
    f.confirm_child_exit=lambda q,ticks:dict(numeric_pid_absent=True,original_parent_reap=False)
    return f,lifetime


class AdapterControls(unittest.TestCase):
    def test_compact_status_full_24h_envelope(self):
        value=dict(pages=147,released=147,end=691300003,state='sealed',eos=True,
                   primary_queued=64,primary_executing=1,comparison_state='idle',
                   charged_peak=6*1024**3,disk_bytes=8*1024**3)
        row=encoded(dict(kind='status',elapsed=86400,value=value))+b'\n'
        self.assertLessEqual(len(encoded(value)),1024)
        self.assertLess(len(row)*2880+2*MIB,8*MIB)
    def test_ledger_cap_never_discards_old_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            budget=SimpleNamespace(admit_evidence=lambda size:None)
            ledger=Ledger(Path(d)/'rows',32,budget)
            ledger.append(dict(a=1)); before=(Path(d)/'rows').read_bytes()
            with self.assertRaises(Stop): ledger.append(dict(long='x'*100))
            ledger.close()
            self.assertEqual((Path(d)/'rows').read_bytes(),before)
    def test_completed_manifest_conservation_allows_new_only(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'a'; b=Path(d)/'b'
            a.write_text(json.dumps(dict(id='b',sha256='one'))+'\n')
            b.write_text('\n'.join(json.dumps(x) for x in [dict(id='a',sha256='new'),dict(id='b',sha256='one')])+'\n')
            self.assertEqual(compare(a,b),1)
            b.write_text(json.dumps(dict(id='b',sha256='changed'))+'\n')
            with self.assertRaises(RuntimeError): compare(a,b)
    def test_final_sweep_exact_147_pages_and_4115_chunks(self):
        ops=Operations.__new__(Operations)
        ops.plan=Plan('/python','http://127.0.0.1:1','sid','cap','/journal')
        ops.last_complete=False; ops.final_sweep_after=0
        ops.final_cursor=1; ops.final_position=ops.plan.origin; ops.final_work_count=0; ops.final_verification=False
        ops.observe=lambda:None; ops.elapsed=lambda:43200.
        ops.proof=lambda label:('manifest',dict(completed=4115))
        seen=[]
        def http(path):
            if '/pages/' not in path:
                return 200,dict(eos=True,released_pages=147,page_count=147,capture_end_frame=ops.plan.origin+90*FRAMES)
            number=int(path.rsplit('/',1)[1]);seen.append(number)
            first=(number-1)*28; last=min(number*28,4115)
            work=[dict(capture_start_frame=ops.plan.origin+i*168000,
                       frames=min(168000,90*FRAMES-i*168000),status='completed') for i in range(first,last)]
            return 200,dict(page_number=number,start_frame=work[0]['capture_start_frame'],
                            end_frame=work[-1]['capture_start_frame']+work[-1]['frames'],
                            capture_origin_frame=ops.plan.origin,released=True,state='published',
                            eos=number==147,work_chunks=work)
        ops.http=http
        self.assertTrue(ops.primary_complete())
        self.assertEqual(seen,list(range(1,148))*2)
        self.assertEqual(ops.final_work_count,4115)
    def test_partial_readback_never_passes(self):
        # The production gate explicitly requires both exact coverage and the
        # conserved final-live manifest. A live-only success is insufficient.
        source=Path(__file__).with_name('adapter.py').read_text()
        self.assertIn("report.get('final_completed_results_preserved')==4115",source)
        self.assertIn("final['end']==self.plan.origin+90*FRAMES",source)
        self.assertIn("preserved=compare(self.final_live[0],final_path)",source)

# Integration controls still required after review: launch canonical short owned
# Python originals under the exact timeout wrapper, positive retire/reuse, sample
# and stop; synthetic append-v3 journal corruption and durable boundary cases;
# SQLite restart/final proof mismatch; every observer/deadline/disk resource cap.
# No module auto-runs this suite.
