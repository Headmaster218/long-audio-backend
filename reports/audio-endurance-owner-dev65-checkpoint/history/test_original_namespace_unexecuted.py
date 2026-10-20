# PUBLIC INSPECTION COPY. Do not execute or import.
"""Finite actual namespace-sample integration for retained original exit proof."""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_namespace_sampling as namespace_fixture

class Vanished(Exception):
    def __init__(self,pid,ticks=None):self.pid=pid;self.ticks=ticks

class OriginalNamespaceControls(unittest.TestCase):
    def setup_family(self):
        helper=namespace_fixture.NamespaceControls();f,records,_=helper.family()
        p=SimpleNamespace(pid=7075,poll=lambda:None)
        f.originals={'uploader':(p,None,[],None,None)};f.root_identities={'uploader':namespace_fixture.row(7075)}
        f.Vanished=Vanished;calls=[];f._original_exit=lambda name:calls.append(name)
        f.namespace_read_context={'stage':'stale','pid':999}
        return helper,f,records,p,calls
    def exit_exception(self,f,p,owner=None):
        proof=dict(name='uploader',pid=p.pid,start_ticks=f.root_identities['uploader']['ticks'],wait_returncode=0,positive_original_wait=True,numeric_pid_absent=True)
        f.root_identities['uploader']['original_recheck_exit_confirmation']=proof
        exc=Vanished(p.pid,proof['start_ticks']);exc.original_recheck_exit_confirmation=proof;exc.original_recheck_owner=p if owner is None else owner
        return exc
    def test_verified_exit_rechecks_original_handle_then_completes_sample(self):
        helper,f,records,p,calls=self.setup_family();exc=self.exit_exception(f,p)
        f._original=lambda name:(_ for _ in ()).throw(exc)
        helper.sample(f);self.assertEqual(calls,['uploader']);self.assertTrue(f.samples[-1]['complete'])
    def test_raw_vanished_is_not_accepted(self):
        helper,f,records,p,calls=self.setup_family()
        f._original=lambda name:(_ for _ in ()).throw(Vanished(p.pid,f.root_identities[name]['ticks']))
        with self.assertRaisesRegex(RuntimeError,'exit proof'):helper.sample(f)
        self.assertEqual(calls,[]);self.assertEqual(records[-1]['context_scope'],'original-owner-recheck');self.assertNotEqual(records[-1]['context'].get('pid'),999)
    def test_other_original_handle_marker_refused(self):
        helper,f,records,p,calls=self.setup_family();exc=self.exit_exception(f,p,owner=object())
        f._original=lambda name:(_ for _ in ()).throw(exc)
        with self.assertRaisesRegex(RuntimeError,'exit proof'):helper.sample(f)
        self.assertEqual(calls,[])
    def test_positive_proof_does_not_skip_fresh_absence_check(self):
        helper,f,records,p,calls=self.setup_family();exc=self.exit_exception(f,p)
        f._original=lambda name:(_ for _ in ()).throw(exc)
        f._original_exit=lambda name:(_ for _ in ()).throw(RuntimeError('PID reused after wait'))
        with self.assertRaisesRegex(RuntimeError,'PID reused'):helper.sample(f)
    def test_fresh_original_failure_context_replaces_stale_resource_context(self):
        helper,f,records,p,calls=self.setup_family()
        def fail(name):
            f.original_recheck_context={'name':name,'pid':p.pid,'stage':'executable','exe_outcome':'empty','kernel':row(p.pid)}
            raise RuntimeError('original failure')
        f._original=fail
        with self.assertRaisesRegex(RuntimeError,'original failure'):helper.sample(f)
        self.assertEqual(records[-1]['context']['stage'],'executable');self.assertEqual(records[-1]['context_scope'],'original-owner-recheck')
    def test_already_exited_original_uses_existing_wait_contract(self):
        helper,f,records,p,calls=self.setup_family();p.poll=lambda:0
        f._original=lambda name:(_ for _ in ()).throw(AssertionError('not live'))
        helper.sample(f);self.assertEqual(calls,['uploader'])
    def test_enumeration_failure_does_not_reuse_original_context(self):
        helper,f,records,p,calls=self.setup_family();f._original=lambda name:row(p.pid)
        f._enumerate=lambda:(_ for _ in ()).throw(RuntimeError('enumeration failed'))
        with self.assertRaisesRegex(RuntimeError,'enumeration failed'):helper.sample(f)
        self.assertEqual(records[-1]['context_scope'],'namespace-resource-read');self.assertEqual(records[-1]['context']['stage'],'namespace-enumerate')
