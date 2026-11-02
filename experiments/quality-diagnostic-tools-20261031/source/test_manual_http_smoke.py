"""Model-free exact lifecycle predicates; does not validate native execution."""
import unittest
from manual_http_smoke import terminal_ok,shutdown_ok

class Checks(unittest.TestCase):
    def test_terminal_requires_positive_cleanup(self):
        good=dict(state='returned_diagnostic',cleanup_pending=False,process_cleanup_verified=True,memory_cleanup_pending=False,storage_pending=False,result_available=True,raw_available=True)
        self.assertTrue(terminal_ok(good))
        for key in good:
            changed=dict(good);changed.pop(key);self.assertFalse(terminal_ok(changed),key)
        for key in ('cleanup_pending','memory_cleanup_pending','storage_pending'):
            changed=dict(good);changed[key]=True;self.assertFalse(terminal_ok(changed))
    def test_shutdown_unknown_is_not_success(self):
        good=dict(instance_lock_lifespan_started=True,instance_lock_cleanup_complete=True,shutdown_decision_diagnostics=dict(schema=1,observation='OBSERVED',predicates=dict(worker_stopped=True,jobs_stopped=True,cells_stopped=True,fragment_cleanup_clear=True,initializer_safe=True),exception_classes=dict(worker=None,jobs=None,cells=None,fragment=None)))
        self.assertTrue(shutdown_ok(good));self.assertFalse(shutdown_ok({}))
        for key in good['shutdown_decision_diagnostics']['predicates']:
            import copy
            changed=copy.deepcopy(good);changed['shutdown_decision_diagnostics']['predicates'][key]=None
            self.assertFalse(shutdown_ok(changed))
    def test_real_uvicorn_sigterm_writes_finally(self):
        import json,os,signal,subprocess,sys,tempfile,time
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            code="from pathlib import Path; from fastapi import FastAPI; from manual_http_smoke_server import serve; serve(FastAPI(),Path("+repr(folder)+"))"
            p=subprocess.Popen([sys.executable,'-B','-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+10
                while not (out/'endpoint.json').exists():
                    self.assertIsNone(p.poll());self.assertLess(time.monotonic(),deadline);time.sleep(.05)
                import http.client
                port=json.loads((out/'endpoint.json').read_text())['port']
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                conn.request('GET','/openapi.json');self.assertEqual(conn.getresponse().status,200);conn.close()
                p.send_signal(signal.SIGTERM);self.assertEqual(p.wait(timeout=10),0)
                self.assertTrue((out/'shutdown.json').exists())
                self.assertFalse(shutdown_ok(json.loads((out/'shutdown.json').read_text())))
            finally:
                if p.poll() is None:p.kill();p.wait()
if __name__=='__main__' :unittest.main()
