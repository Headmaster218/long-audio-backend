"""Finite synthetic cell/real owner/controller seams; no native model invocation.

The store qualification in these 24 cases is deliberately simulated. The
separately selected exact-A driver is a different, genuine-verifier gate.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from audio_backend import native_owned_cell_store as cell_store
from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_cell_jobs as adapter
from audio_backend import moss_handy_jobs as jobs
from audio_backend.native_execution_permit import NativeExecutionPermit, AdmissionError
from audio_backend.native_owned_support_cache import ReadError
from test_native_owned_support_cache import CacheFixture
from test_native_owned_cell_store import SyntheticAuthority, simulated_profile, wait_operation
from test_native_owned_cells import synthetic_cell
from test_native_owned_seam_cells import synthetic_composite
from test_native_execution_permit import sample
from test_moss_handy_controller import FakeDriver, clean
from test_moss_handy_jobs import ManualThread, retain_outcome, prepare_history


class CellJobsFixture(CacheFixture):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        with patch('test_native_owned_support_cache.tempfile.TemporaryDirectory', return_value=temporary):
            super().setUp()
        self.store.settings.moss_jobs_enabled = True
        self.store.settings.native_owned_cells_enabled = True
        self.store.settings.moss_jobs_model_path = '/synthetic-model.fixture'
        self.store.settings.data_dir = self.root
        self.store.settings.disk_reserve_bytes = 0
        self.store.settings.process_memory_limit_bytes = 6 * 1024**3
        self.chunk()
        self.authority = SyntheticAuthority()
        self.cell_owner = cell_store.CellStore(self.root / 'native-owned-cells', self.authority,
            enabled=True, qualification=simulated_profile(self.root), instance_lock=object())
        self.authority.owner = self.cell_owner
        self.cell_owner.start()
        self.assertTrue(self.cell_owner._ready.wait(3.), self.cell_owner.health())
        self.source = adapter.CellJobsSource(self.cell_owner, self.store)
        self.permit = NativeExecutionPermit(self.store.settings, sampler=lambda _:sample())
        self.drivers = []
        def factory(directory, sha):
            driver = FakeDriver(directory, sha); self.drivers.append(driver); return driver
        results = prepare_history(self)
        self.owner = jobs.MossJobs(self.store, self.cache, self.permit, clock=self.clock,
            _driver_factory=factory, _thread_factory=ManualThread, cell_source=self.source,
            result_store=results)

    def tearDown(self):
        retain_outcome(self.root, self.owner)
        self.owner.close_admission()
        record = self.owner._live
        if record is not None:
            thread = record['thread']
            if thread is not None and thread.is_alive():thread.run()
        for pin in tuple(self.cell_owner._pins.values()):pin.release()
        self.owner.stop()
        self.cell_owner.close(.5)
        if self.cell_owner._thread:self.cell_owner._thread.join(3.)
        self.assertFalse(self.cell_owner._thread and self.cell_owner._thread.is_alive())
        super().tearDown()

    def operation(self, kind, payload):
        return wait_operation(self, self.cell_owner.submit(kind, payload))

    def ready_cell(self, index=0, frames=1600):
        cell = synthetic_cell(index, frames)
        result = self.operation('publish', (cell,))
        self.assertEqual(result['status'], 'durable', result)
        detail = self.source.detail('session', cell.cell_id)
        body = {'jobs_epoch':self.owner.jobs_epoch, 'expected_cell_sequence':detail['sequence'],
            'expected_cell_revision':detail['revision'], 'expected_proof_sha256':hashlib.sha256(cell.proof).hexdigest(),
            'support_id':cell.support.support_id, 'profile':jobs.NAME}
        return cell, body

    def ready_composite(self, *, frames=16):
        if not self.cell_owner.seam_enabled:
            self.assertTrue(self.cell_owner.close())
            self.cell_owner = cell_store.CellStore(self.root / 'seam-owned-cells', self.authority,
                enabled=True, seam_enabled=True, qualification=simulated_profile(self.root), instance_lock=object())
            self.authority.owner = self.cell_owner; self.cell_owner.start()
            self.assertTrue(self.cell_owner._ready.wait(3.), self.cell_owner.health())
            self.source = adapter.CellJobsSource(self.cell_owner, self.store)
            self.owner.cell_source = self.source
        cell = synthetic_composite(frames=frames)
        result = self.operation('publish', (cell,))
        self.assertEqual(result['status'], 'durable', result)
        detail = self.source.detail('session', cell.cell_id)
        return cell, {'jobs_epoch':self.owner.jobs_epoch, 'expected_cell_sequence':detail['sequence'],
            'expected_cell_revision':detail['revision'], 'expected_proof_sha256':hashlib.sha256(cell.proof).hexdigest(),
            'support_id':cell.support.support_id, 'profile':jobs.NAME,
            'coverage_intent':'overlapping_diagnostic'}

    def submit_cell(self, cell, body, key='cell', **kwargs):
        return self.owner.submit_cell(cell.session_id, cell.cell_id, body, key, **kwargs)

    def finish(self):
        record = self.owner._live; record['thread'].run(); return record

    def dispatch_operation(self, cell, body):
        return self.cell_owner.submit('dispatch', ('session',cell.cell_id,body['expected_cell_sequence'],
            body['expected_cell_revision'],body['expected_proof_sha256'],body['support_id'],self.cell_owner._epoch))

    def await_drained(self, op):
        self.assertTrue(op.drained.wait(1.5), 'original delivery owner did not drain')
        self.assertTrue(op.slot_released.wait(.5))
        self.assertEqual(self.cell_owner.health()['pins'], 0)


class CellJobsTests(CellJobsFixture):
    def test_nonzero_absolute_support_passes_unchanged_to_existing_controller(self):
        # A >20-second synthetic core with a nonzero interior cell. This is
        # geometry/controller evidence, never a genuine producer observation.
        original = synthetic_cell(25, 1600)
        doc = json.loads(original.proof)
        doc['commit_evidence']['start'] = 0; doc['commit_evidence']['end'] = 400000
        doc['commit']['start'] = 0; doc['commit']['end'] = 400000
        doc['commit']['sha256'] = cells.sha(doc['commit_evidence'])
        support = replace(original.support, source_frames=400000, core_start_frame=0, core_end_frame=400000,
            core_sha256=cells.sha({'start':0,'end':400000,'commit':doc['commit']['sha256'],'native':doc['native']}))
        doc['support'] = support.to_dict()
        cell = replace(original, support=support, proof=cells.canonical(doc), commit_start=0,
            commit_end=400000, commit_sha256=doc['commit']['sha256'])
        self.assertEqual(self.operation('publish',(cell,))['status'],'durable')
        detail = self.source.detail('session',cell.cell_id)
        body = dict(jobs_epoch=self.owner.jobs_epoch,expected_cell_sequence=detail['sequence'],
            expected_cell_revision=detail['revision'],expected_proof_sha256=hashlib.sha256(cell.proof).hexdigest(),
            support_id=support.support_id,profile=jobs.NAME)
        before = tuple(self.db.execute('SELECT status,next_offset,metadata FROM chunks').fetchone())
        self.submit_cell(cell,body); record=self.finish()
        self.assertEqual(record['token'].support,support)
        request=json.loads((self.owner.directory/'request.json').read_bytes())
        self.assertEqual(request['support'],support.to_dict())
        self.assertGreater(support.start_frame,0);self.assertGreater(support.core_end_frame-support.core_start_frame,320000)
        self.assertEqual(before,tuple(self.db.execute('SELECT status,next_offset,metadata FROM chunks').fetchone()))
        self.assertEqual(sum(d.starts for d in self.drivers),1)

    def test_cell_identity_binds_session_sequence_revision_proof_and_lineage(self):
        cell, body=self.ready_cell()
        cases=(('expected_cell_sequence',body['expected_cell_sequence']+1),
               ('expected_cell_revision',body['expected_cell_revision']+1),
               ('expected_proof_sha256','0'*64),('support_id','0'*64))
        for field,value in cases:
            with self.subTest(field=field),self.assertRaises(ReadError):self.submit_cell(cell,{**body,field:value})
        lease=self.source.acquire_dispatch('session',cell.cell_id,body)
        for change in ({'adapter_epoch':'0'*32},{'generation':object()},{'owner':object()},
                       {'primary_identity':'forged'},{'profile':'forged'},{'model_sha256':'0'*64},
                       {'runtime_sha256':'0'*64},{'proof':b'{}'},
                       {'support':replace(lease.token.support,native_runtime_sha256='0'*64)}):
            with self.subTest(change=tuple(change)),self.assertRaises(ReadError):
                self.source.revalidate_dispatch(replace(lease.token,**change))
        self.assertTrue(self.source.abandon(lease));self.assertFalse(self.drivers)
        code,_=self.submit_cell(cell,body);self.assertEqual(code,202)
        self.assertEqual(len(self.owner._records),1)

    def test_same_content_reinsert_cannot_satisfy_old_sequence(self):
        cell,body=self.ready_cell();self.assertEqual(self.operation('delete',cell.cell_id)['status'],'deleted')
        cell,new=self.ready_cell();self.assertGreater(new['expected_cell_sequence'],body['expected_cell_sequence'])
        with self.assertRaises(ReadError):self.submit_cell(cell,body)
        self.assertFalse(self.owner._records)

    def test_cross_origin_same_key_conflicts_without_second_acquisition(self):
        cell,body=self.ready_cell();self.submit_cell(cell,body)
        chunk,pcm,_,artifact=self.capture()
        rolling=dict(jobs_epoch=self.owner.jobs_epoch,expected_capture_epoch=self.cache.epoch,
            expected_revision=self.cache._entries[('session',chunk['id'])].revision,
            support_id=artifact.support.support_id,profile=jobs.NAME)
        with patch.object(self.cache,'acquire_dispatch',side_effect=AssertionError('no second acquisition')):
            with self.assertRaises(ReadError) as caught:self.owner.submit('session',chunk['id'],rolling,'cell')
        self.assertEqual(caught.exception.reason,'idempotency_conflict');self.assertEqual(len(self.owner._records),1)

    def test_repeat_retirement_and_epoch_ledger_never_replay(self):
        cell,body=self.ready_cell()
        for index in range(64):
            code,first=self.submit_cell(cell,body,str(index));self.assertEqual(code,202)
            self.assertEqual(self.submit_cell(cell,body,str(index))[1]['job_id'],first['job_id']);self.finish()
        with self.assertRaises(ReadError) as caught:self.submit_cell(cell,body,'65')
        self.assertEqual(caught.exception.status_code,429)
        with self.assertRaises(ReadError) as caught:self.submit_cell(cell,body,'0')
        self.assertEqual(caught.exception.status_code,410)
        with self.assertRaises(ReadError):self.submit_cell(cell,{**body,'jobs_epoch':'0'*32},'fresh')
        self.assertEqual(sum(d.starts for d in self.drivers),64)

    def test_only_one_cell_pin_uses_existing_two_pin_budget(self):
        cell,body=self.ready_cell();self.submit_cell(cell,body)
        ordinary=self.operation('read',cell.cell_id)['pin']
        try:
            self.assertEqual(self.cell_owner.health()['pins'],2)
            self.assertEqual(self.operation('read',cell.cell_id)['reason'],'read_pin_capacity')
            self.assertIsNone(self.cache._dispatch)
        finally:ordinary.release()

    def test_busy_receipt_or_publication_slot_rejects_dispatch_without_queue(self):
        cell,body=self.ready_cell();entered=threading.Event();release=threading.Event()
        original=self.authority.cell_valid
        def blocked(*args,**kwargs):entered.set();release.wait(.4);return original(*args,**kwargs)
        with patch.object(self.authority,'cell_valid',side_effect=blocked):
            op=self.cell_owner.submit('detail',cell.cell_id);self.assertTrue(entered.wait(.5))
            try:
                with self.assertRaises(ReadError):self.submit_cell(cell,body)
                self.assertIs(self.cell_owner._slot,op);self.assertFalse(self.owner._records)
            finally:release.set()
        self.assertTrue(op.done.wait(1.))

    def test_abandon_before_owner_read_cannot_copy_pin(self):
        cell,body=self.ready_cell()
        entered=threading.Event();release=threading.Event();begin=cell_store.Operation._begin_dispatch
        def blocked(operation):
            entered.set();release.wait(.3);return begin(operation)
        with patch.object(cell_store.Operation,'_begin_dispatch',blocked), \
             patch.object(self.cell_owner,'_read',wraps=self.cell_owner._read) as read:
            op=self.dispatch_operation(cell,body);self.assertTrue(entered.wait(.5))
            op.abandon('response_closed');release.set();self.await_drained(op)
            self.assertFalse(any(call.args[0] is op for call in read.call_args_list))
        self.assertFalse(self.owner._records)

    def test_abandon_after_copy_before_delivery_drains_exact_pin(self):
        cell,body=self.ready_cell();original=self.cell_owner._read;copied=threading.Event();release=threading.Event()
        def blocked(op,budget):
            value=original(op,budget)
            if op.kind=='dispatch':copied.set();release.wait(.3)
            return value
        with patch.object(self.cell_owner,'_read',side_effect=blocked):
            op=self.dispatch_operation(cell,body);self.assertTrue(copied.wait(.5));op.abandon('disconnect');release.set()
            self.await_drained(op)
        self.assertFalse(self.owner._records);self.assertFalse(self.drivers)

    def test_claim_abandon_race_has_exactly_one_cleanup_owner(self):
        cell,body=self.ready_cell()
        for winner in ('claim','abandon'):
            op=self.dispatch_operation(cell,body);op.wait(.5)
            if winner=='claim':
                value=op.claim(admission_open=True);self.assertEqual(value['status'],'available')
                op.abandon('late');self.assertFalse(value['pin'].release_confirmed)
                self.assertTrue(op.slot_released.wait(.5));value['pin'].release();self.assertTrue(value['pin'].release_done.wait(.5))
            else:
                op.abandon('early');self.assertNotIn('pin',op.claim(admission_open=True));self.await_drained(op)
            self.assertEqual(self.cell_owner.health()['pins'],0)

    def test_ready_result_deadline_and_contended_release_keep_slot_charged(self):
        cell,body=self.ready_cell();op=self.dispatch_operation(cell,body);op.wait(.5)
        self.assertIs(self.cell_owner._slot,op)
        self.assertEqual(self.cell_owner.submit('detail',cell.cell_id)['status'],'busy')
        op.deadline=time.monotonic()-1
        self.cell_owner._wake.set();self.await_drained(op)
        self.assertFalse(self.owner._records)

    def test_preacceptance_failure_retains_pending_acquisition_until_ack(self):
        cell,body=self.ready_cell();original=self.source.release
        with patch.object(self.permit,'reserve_optional',side_effect=AdmissionError('primary_or_optional_busy')), \
             patch.object(self.source,'release',return_value=False):
            with self.assertRaises(ReadError):self.submit_cell(cell,body)
        self.assertTrue(self.source.cleanup_pending);self.assertFalse(self.owner._records)
        pending=self.source._pending;self.assertTrue(original(pending));self.assertFalse(self.source.cleanup_pending)
        snapshots=[]
        def admission():snapshots.append(True);return len(snapshots)==1
        def failed_thread(**kwargs):raise RuntimeError('synthetic thread preparation failure')
        with patch.object(self.source,'release',return_value=False), \
             patch.object(self.owner,'_thread_factory',side_effect=failed_thread):
            with self.assertRaises(ReadError):self.submit_cell(cell,body,_admission_open=admission)
        rejected=self.owner._pending_record
        self.assertIsNotNone(rejected);self.assertIs(self.permit._optional,rejected)
        self.assertIsNone(rejected['controller']);self.assertFalse(self.owner._keys)
        self.assertTrue(self.source.abandon(rejected['lease']))
        self.owner.health();self.assertIsNone(self.owner._pending_record);self.assertIsNone(self.permit._optional)
        self.assertFalse(self.drivers)

    def test_pin_ack_requires_registry_removal_not_local_release(self):
        cell,body=self.ready_cell();lease=self.source.acquire_dispatch('session',cell.cell_id,body);pin=lease.pin
        with patch.object(self.cell_owner,'_release_pin',return_value=False):
            pin._pcm=b'';pin._released=True  # Fault injection: local clearing is not registry removal.
            pin.release();self.assertEqual(pin._pcm,b'');self.assertFalse(pin.release_confirmed)
            self.assertIs(self.cell_owner._pins[id(pin)],pin);self.assertFalse(lease.released)
        self.cell_owner._wake.set();self.assertTrue(pin.release_done.wait(.5));self.assertTrue(lease.released)
        self.assertNotIn(id(pin),self.cell_owner._pins);self.assertFalse(self.source.cleanup_pending)

    def test_normal_pin_release_does_not_cancel_successful_job(self):
        cell,body=self.ready_cell();self.submit_cell(cell,body);record=self.finish()
        self.assertTrue(record['lease'].released);self.assertTrue(record['lease'].cancelled.is_set())
        self.assertFalse(record['cancelled'].is_set());self.assertEqual(record['state'],'returned_diagnostic')
        self.assertEqual(self.owner.content('session',record['job_id'],'raw'),b'[0][S1]synthetic[1]')

    def test_deletion_at_every_dispatch_gate_suppresses_start(self):
        cell,body=self.ready_cell()
        for phase in ('before_input','before_spawn','before_admission'):
            self.submit_cell(cell,body,phase);record=self.owner._live;gate=record['controller']._service_gate;original=gate.check
            def revoked(current):
                if current==phase:self.cell_owner.revoked()
                return original(current)
            if phase=='before_admission':
                factory=record['controller']._factory
                def guarded(directory,sha):
                    driver=factory(directory,sha);start=driver.start
                    def admission(cancelled):driver._service_gate.check('before_admission');return start(cancelled)
                    driver.start=admission;return driver
                record['controller']._factory=guarded
            with patch.object(gate,'check',side_effect=revoked):self.finish()
            self.assertIsNone(record['raw']);self.assertTrue(record['cancelled'].is_set() or record['invalidated'])
            # Fresh explicit binding uses the new generation; no replay.
            self.assertEqual(sum(d.starts for d in self.drivers),0)

    def test_revocation_before_publication_suppresses_late_raw_and_result(self):
        cell,body=self.ready_cell();self.submit_cell(cell,body);record=self.owner._live
        factory=record['controller']._factory
        def changed(directory,sha):
            driver=factory(directory,sha);driver.callback=self.cell_owner.revoked;return driver
        record['controller']._factory=changed;self.finish()
        self.assertIsNone(record['raw']);self.assertIsNone(record['result']);self.assertIsNone(self.permit._optional)

    def test_source_replacement_and_expiry_fail_fresh_authority(self):
        cell,body=self.ready_cell();self.submit_cell(cell,body);record=self.finish()
        self.authority.valid=False
        with self.assertRaises(ReadError):self.owner.content('session',record['job_id'],'raw')
        self.authority.valid=True;self.assertEqual(self.operation('expire',cell.cell_id)['status'],'expired')
        with self.assertRaises(ReadError):self.owner.content('session',record['job_id'],'result')
        self.assertEqual(sum(d.starts for d in self.drivers),1)

    def test_durable_retention_expiry_and_job_ttl_are_distinct(self):
        cell,body=self.ready_cell();self.clock.now+=901;self.submit_cell(cell,body)
        self.assertEqual(self.operation('expire',cell.cell_id)['reason'],'cell_pinned')
        self.finish();self.clock.now+=901
        with self.assertRaises(ReadError) as caught:self.submit_cell(cell,body)
        self.assertEqual(caught.exception.status_code,410)
        self.assertEqual(self.source.detail('session',cell.cell_id)['state'],'retained')

    def test_process_cleanup_blocked_retains_all_ownership(self):
        cell,body=self.ready_cell();self.submit_cell(cell,body);record=self.owner._live;factory=record['controller']._factory
        def blocked(directory,sha):
            driver=factory(directory,sha);driver.cleanup_value=RuntimeError('synthetic');return driver
        record['controller']._factory=blocked;self.finish()
        self.assertFalse(record['process_cleanup_verified']);self.assertFalse(record['lease'].released)
        self.assertIs(self.permit._optional,record);self.assertTrue((self.owner.directory/'owner.json').exists())
        self.drivers[0].cleanup_value=clean();self.owner.cancel('session',record['job_id'],self.owner.jobs_epoch);record['thread'].run()
        self.assertTrue(record['cleaned']);self.assertEqual(self.drivers[0].starts,1)

    def test_memory_cleanup_pending_preserves_positive_process_receipt(self):
        cell,body=self.ready_cell();self.submit_cell(cell,body);record=self.owner._live
        released=threading.Event();original=self.cell_owner._release_pin
        def delayed(pin):
            if not released.is_set():return False
            return original(pin)
        thread=threading.Thread(target=record['thread'].run,daemon=True)
        with patch.object(self.cell_owner,'_release_pin',side_effect=delayed):
            thread.start()
            deadline=time.monotonic()+2.
            while not record['process_cleanup_verified'] and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(record['process_cleanup_verified']);self.assertFalse(record['cleaned'])
            self.assertIs(self.permit._optional,record)
            # The session can disappear while only the original cleanup owner
            # remains. No deleted-session API is needed for positive drainage.
            self.db.execute("UPDATE sessions SET deleted=1 WHERE id='session'");self.db.commit()
            self.owner.invalidate_session('session');self.cell_owner.revoked()
            released.set();self.cell_owner._wake.set();thread.join(2.)
        self.assertFalse(thread.is_alive());self.assertTrue(record['cleaned'])
        self.assertIsNone(self.permit._optional);self.assertFalse(self.cell_owner._pins)
        self.assertEqual(sum(d.starts for d in self.drivers),1)

    def test_primary_intent_cancels_and_waits_for_both_cleanup_barriers(self):
        cell,body=self.ready_cell();self.submit_cell(cell,body);record=self.owner._live
        with self.permit.claim_probe():self.assertFalse(record['cancelled'].is_set())
        intent=self.permit.primary_intent('worker')
        try:
            self.assertTrue(record['cancelled'].is_set());self.assertIs(self.permit._optional,record)
            with patch.object(self.cell_owner,'_release_pin',return_value=False):
                record['cleanup_deadline']=time.monotonic()
                self.finish()
            self.assertTrue(record['process_cleanup_verified']);self.assertFalse(record['cleaned'])
            self.cell_owner._wake.set();self.assertTrue(record['lease'].pin.release_done.wait(.5))
            self.owner._retry_cleanup(record);intent.wait(lambda:False);self.assertTrue(intent.entered)
        finally:intent.release()
        self.assertFalse(self.drivers)

    def test_exact_raw_failure_and_full_primary_evidence_are_preserved(self):
        cell,body=self.ready_cell();before=tuple(self.db.execute('SELECT status,next_offset,metadata FROM chunks').fetchone())
        for index,raw in enumerate((b'',b' \n[0][S1]synthetic[1]\t',' 合成 '.encode(),None)):
            self.submit_cell(cell,body,str(index));record=self.owner._live;factory=record['controller']._factory
            def configure(directory,sha,value=raw):
                driver=factory(directory,sha);driver.raw=value;return driver
            record['controller']._factory=configure;self.finish()
            if raw is None:
                self.assertIsNone(record['raw']);self.assertEqual(record['raw_reason'],'raw_text_oversize')
            else:self.assertEqual(self.owner.content('session',record['job_id'],'raw'),raw)
            self.assertEqual(self.source.detail('session',cell.cell_id)['detail'],cell.proof)
        self.assertEqual(before,tuple(self.db.execute('SELECT status,next_offset,metadata FROM chunks').fetchone()))

    def test_maximum_material_stays_inside_existing_reservations(self):
        original=synthetic_cell(frames=320000)
        def padded(count):
            doc=json.loads(original.proof);doc['commit_evidence']['padding']='x'*count
            commit=cells.sha(doc['commit_evidence']);doc['commit']['sha256']=commit
            support=replace(original.support,core_sha256=cells.sha({'start':0,'end':320000,
                'commit':commit,'native':doc['native']}))
            doc['support']=support.to_dict()
            return replace(original,support=support,proof=cells.canonical(doc),commit_sha256=commit)
        initial=padded(0);cell=padded(65536-len(initial.proof))
        self.assertEqual(len(cell.proof),65536);self.assertEqual(len(cell.pcm),640000)
        self.assertEqual(self.operation('publish',(cell,))['status'],'durable')
        detail=self.source.detail('session',cell.cell_id)
        body=dict(jobs_epoch=self.owner.jobs_epoch,expected_cell_sequence=detail['sequence'],
            expected_cell_revision=detail['revision'],expected_proof_sha256=hashlib.sha256(cell.proof).hexdigest(),
            support_id=cell.support.support_id,profile=jobs.NAME)
        # Acquisition keeps the real 500 ms owner deadline and full PCM checks.
        # Its first failed run did not retain enough evidence to establish why
        # it failed; do not attribute that failure to tracing as a proven cause.
        observed=[];submit=self.cell_owner.submit
        def observe_dispatch(kind,payload):
            result=submit(kind,payload)
            if kind == 'dispatch' and isinstance(result,cell_store.Operation):observed.append(result)
            return result
        with patch.object(self.cell_owner,'submit',side_effect=observe_dispatch):
            try:lease=self.source.acquire_dispatch('session',cell.cell_id,body)
            except ReadError as exc:
                op=observed[-1] if observed else None
                self.fail({'acquisition_error':exc.reason,'owner':self.cell_owner.health(),
                    'delivery_state':op.state if op is not None else None,
                    'operation_reason':str(op.result.get('reason',''))[:160] if op is not None else None})
        # This measures only postclaim projection/proof validation transients,
        # not acquisition allocations, native memory, or a hard-RSS envelope.
        tracemalloc.start()
        try:
            lease.token.projection();cells.validate_document(lease.token.proof)
            self.assertTrue(self.source.revalidate_dispatch(lease.token))
            peak=tracemalloc.get_traced_memory()[1]
        finally:tracemalloc.stop()
        self.assertLess(peak,16*1024**2,'postclaim-only Python projection/proof validation peak')
        self.assertIs(lease.pcm,lease.pin._pcm)
        self.assertLessEqual(len(lease.pcm)+len(cell.proof)+4096,cell_store.PIN_RESERVATION_BYTES)
        self.assertTrue(self.source.abandon(lease))
        self.submit_cell(cell,body);record=self.owner._live;factory=record['controller']._factory
        def maximum_raw(directory,sha):
            driver=factory(directory,sha);driver.raw=b'x'*jobs.MAX_RAW_BYTES;return driver
        record['controller']._factory=maximum_raw;self.finish()
        self.assertEqual(len(self.owner.content('session',record['job_id'],'raw')),131072)
        self.assertLessEqual(len(jobs.encoded(record['token'].projection())),4096)
        self.assertLessEqual(len(record['result'] or b''),jobs.MAX_RESULT_BYTES)
        self.assertEqual(cell_store.MAX_PINS,2);self.assertEqual(jobs.MAX_RETAINED_BYTES,1024*1024)
        with self.assertRaises(cells.Unavailable):padded(65537-len(initial.proof))

    def test_restart_and_shutdown_drain_without_replay_or_second_owner(self):
        cell,body=self.ready_cell();lease=self.source.acquire_dispatch('session',cell.cell_id,body)
        original_owner_thread=self.cell_owner._thread
        # Admission close correctly releases an unaccepted lease. Hold only the
        # positive registry acknowledgement, then wake the same cleanup owner.
        with patch.object(self.cell_owner,'_release_pin',return_value=False):
            self.source.close_admission();self.assertFalse(self.cell_owner.close(.005))
            self.assertFalse(lease.released);self.assertIs(self.cell_owner._pins[id(lease.pin)],lease.pin)
            self.assertIsNotNone(self.cell_owner.instance_lock);self.assertTrue(original_owner_thread.is_alive())
        self.cell_owner._wake.set();self.assertTrue(lease.pin.release_done.wait(.5))
        self.assertIs(self.cell_owner._thread,original_owner_thread)
        self.assertNotIn(id(lease.pin),self.cell_owner._pins);self.assertEqual(lease.pcm,b'')
        self.assertTrue(self.source.abandon(lease));self.assertTrue(self.cell_owner.close(.5))
        fresh=cell_store.CellStore(self.cell_owner.directory,self.authority,enabled=True,
            qualification=self.cell_owner.qualification,instance_lock=object())
        self.cell_owner=fresh;self.authority.owner=fresh;fresh.start();self.assertTrue(fresh._ready.wait(3.))
        self.assertEqual(self.operation('detail',cell.cell_id)['detail'],cell.proof)
        self.assertFalse(self.owner._records);self.assertFalse(self.drivers)


    def test_seam_intent_is_version_scoped_and_rejection_positively_releases_pin(self):
        composite, body = self.ready_composite()
        for value in (None, 'new_primary_audio', True, {}):
            request = dict(body)
            if value is None:request.pop('coverage_intent')
            else:request['coverage_intent'] = value
            with self.subTest(value=value), self.assertRaises(ReadError) as caught:
                self.submit_cell(composite, request)
            self.assertEqual(caught.exception.reason, 'coverage_intent_required')
            self.assertFalse(self.source.cleanup_pending); self.assertFalse(self.cell_owner._pins)
            self.assertFalse(self.owner._records); self.assertFalse(self.owner._keys); self.assertFalse(self.drivers)
        ordinary, legacy = self.ready_cell()
        with self.assertRaises(ReadError) as caught:
            self.submit_cell(ordinary, {**legacy, 'coverage_intent':'overlapping_diagnostic'})
        self.assertEqual(caught.exception.reason, 'coverage_intent_not_allowed')
        self.assertFalse(self.source.cleanup_pending); self.assertFalse(self.cell_owner._pins)
        self.assertFalse(self.owner._records); self.assertFalse(self.drivers)
        self.assertEqual(self.submit_cell(composite, body)[0], 202)

    def test_seam_intent_rejection_keeps_contended_pin_charged_until_ack(self):
        cell, body = self.ready_composite(); request = dict(body); request.pop('coverage_intent')
        with patch.object(self.cell_owner, '_release_pin', return_value=False):
            with self.assertRaises(ReadError):self.submit_cell(cell, request)
            self.assertTrue(self.source.cleanup_pending); self.assertEqual(len(self.cell_owner._pins), 1)
            pending = self.source._pending; self.assertFalse(pending.released)
            self.assertFalse(self.owner._records); self.assertFalse(self.drivers)
        self.cell_owner._wake.set(); self.assertTrue(pending.pin.release_done.wait(.5))
        self.assertNotIn(id(pending.pin), self.cell_owner._pins); self.assertFalse(self.source.cleanup_pending)
        self.assertEqual(self.submit_cell(cell, body)[0], 202)

    def test_seam_token_summary_and_result_carry_exact_alternative_coverage(self):
        cell, body = self.ready_composite(); expected = json.loads(cell.proof)['coverage']
        code, summary = self.submit_cell(cell, body)
        self.assertEqual(code, 202); self.assertEqual(summary['cell_binding']['coverage'], expected)
        record = self.owner._live; token = record['token']
        self.assertEqual(token.coverage, expected); self.assertEqual(token.coverage_intent, 'overlapping_diagnostic')
        self.assertEqual(token.projection()['coverage'], expected)
        self.assertEqual(token.support.core_end_frame, 32)
        self.assertFalse(expected['adds_primary_audio']); self.assertFalse(expected['automatic_dispatch'])
        self.assertLessEqual(len(cells.canonical(token.projection())), 4096)
        self.assertTrue(self.source.revalidate_dispatch(token))
        with self.assertRaises(ReadError):self.source.revalidate_dispatch(replace(token, coverage_intent=None))
        record = self.finish()
        result = json.loads(self.owner.content('session', record['job_id'], 'result'))
        self.assertEqual(result['cell_binding']['coverage'], expected)
        self.assertEqual(self.owner.content('session', record['job_id'], 'raw'), b'[0][S1]synthetic[1]')
        self.assertFalse(self.cell_owner._pins); self.assertEqual(sum(driver.starts for driver in self.drivers), 1)

    def test_seam_same_key_exact_repeat_does_not_reacquire_or_dispatch(self):
        cell, body = self.ready_composite(); code, first = self.submit_cell(cell, body)
        self.assertEqual(code, 202)
        with patch.object(self.source, 'acquire_dispatch', side_effect=AssertionError('no second acquisition')):
            self.assertEqual(self.submit_cell(cell, body)[1]['job_id'], first['job_id'])
            missing = dict(body); missing.pop('coverage_intent')
            with self.assertRaises(ReadError) as caught:self.submit_cell(cell, missing)
            self.assertEqual(caught.exception.reason, 'idempotency_conflict')
            with self.assertRaises(ReadError) as caught:
                self.submit_cell(cell, {**body, 'coverage_intent':'new_primary_audio'})
            self.assertEqual(caught.exception.reason, 'idempotency_conflict')
        self.finish()
        ordinary, legacy = self.ready_cell()
        with patch.object(self.source, 'acquire_dispatch', side_effect=AssertionError('no cross-target acquisition')):
            with self.assertRaises(ReadError) as caught:self.submit_cell(ordinary, legacy)
        self.assertEqual(caught.exception.reason, 'idempotency_conflict')
        self.assertEqual(len(self.owner._records), 1); self.assertEqual(sum(driver.starts for driver in self.drivers), 1)

    def test_seam_either_dependency_revokes_later_raw_and_result(self):
        cell, body = self.ready_composite(); self.submit_cell(cell, body); record = self.finish()
        # Synthetic authority denial models each dependency independently; the
        # primary-row/receipt mechanics have separate Capture authority fixtures.
        deadline = time.monotonic() + 1.5
        self.cell_owner._wake.set()
        while self.cell_owner._reconcile.is_set() and time.monotonic() < deadline:time.sleep(.005)
        self.assertFalse(self.cell_owner._reconcile.is_set())
        original = self.authority.cell_valid
        for cid in ('left', 'right'):
            def denied(doc, budget, *, publishing):
                if any(part['chunk_id'] == cid for part in doc['dependencies']):return False
                return original(doc, budget, publishing=publishing)
            with self.subTest(cid=cid), patch.object(self.authority, 'cell_valid', side_effect=denied):
                for kind in ('raw', 'result'):
                    with self.assertRaises(ReadError) as caught:self.owner.content('session', record['job_id'], kind)
                    self.assertEqual(caught.exception.reason, 'cell_revoked')
        self.assertEqual(sum(driver.starts for driver in self.drivers), 1); self.assertFalse(self.cell_owner._pins)

    def test_seam_production_off_keeps_retained_explicit_diagnostic_available(self):
        cell, body = self.ready_composite(); namespace = self.cell_owner.directory
        self.assertTrue(self.cell_owner.close())
        self.cell_owner = cell_store.CellStore(namespace, self.authority, enabled=True,
            seam_enabled=False, qualification=simulated_profile(self.root), instance_lock=object())
        self.authority.owner = self.cell_owner; self.cell_owner.start()
        self.assertTrue(self.cell_owner._ready.wait(3.))
        self.source = adapter.CellJobsSource(self.cell_owner, self.store); self.owner.cell_source = self.source
        self.assertEqual(self.source.detail('session', cell.cell_id)['detail'], cell.proof)
        self.assertEqual(self.submit_cell(cell, body)[0], 202); self.finish()
        self.assertEqual(sum(driver.starts for driver in self.drivers), 1)
        with patch.object(self.store.settings, 'native_owned_cells_enabled', False):
            with self.assertRaises(ReadError) as caught:self.source.detail('session', cell.cell_id)
        self.assertEqual(caught.exception.reason, 'feature_disabled')
