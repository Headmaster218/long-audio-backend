"""Harness-only fail-closed observer tests, no audio or model execution."""
import ast
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import traceback
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).with_name('receiver_resource_workload.py')

def functions(root):
    tree=ast.parse(SCRIPT.read_text())
    selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('monitor','guard','emit','stat')]
    namespace=dict(os=os,Path=Path,ROOT=root,stop=threading.Event(),monitor_errors=[],started=time.monotonic(),time=time,json=json,shutil=shutil,traceback=traceback)
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(SCRIPT),'exec'),namespace)
    return namespace

class ObserverTests(unittest.TestCase):
    def test_scan_failure_is_visible_to_main_guard(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);n=functions(root)
            with patch.object(Path,'rglob',side_effect=OSError('synthetic scan failure')):n['monitor']()
            self.assertIn('synthetic scan failure',n['monitor_errors'][0])
            self.assertTrue((root/'monitor_errors.jsonl').is_file())
            with self.assertRaisesRegex(AssertionError,'resource observer failed'):n['guard']()

    def test_failure_to_write_error_does_not_hide_observer_failure(self):
        with tempfile.TemporaryDirectory() as d:
            n=functions(Path(d));n['emit']=lambda *a:(_ for _ in ()).throw(OSError('synthetic full disk'))
            with patch.object(Path,'rglob',side_effect=OSError('synthetic scan failure')):n['monitor']()
            with self.assertRaisesRegex(AssertionError,'resource observer failed'):n['guard']()

    def test_terminal_block_rejects_failure_after_last_operation(self):
        tree=ast.parse(SCRIPT.read_text());statement=tree.body[-1]
        self.assertIsInstance(statement,ast.Try)
        n=dict(stop=threading.Event(),thread=type('Thread',(),{'join':lambda self:None})(),monitor_errors=['synthetic late failure'])
        with self.assertRaisesRegex(RuntimeError,'resource observer failed'):
            exec(compile(ast.Module(body=statement.finalbody,type_ignores=[]),str(SCRIPT),'exec'),n)

if __name__=='__main__':unittest.main()
