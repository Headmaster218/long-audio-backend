"""Real Automatic discovery deferral with synthetic producer/model edges only."""
from copy import deepcopy
import json
import threading
import time

import pytest

from audio_backend import recording_speaker_history as history
from audio_backend import recording_speaker_history_process as comparison_process
from audio_backend.recording_speaker_probe_runner import ProbeOperation, WORK_SECONDS
from test_moss_handy_jobs import ManualThread
from test_primary_only_pipeline import (
    primary_app_factory, _tripwires, _assert_primary_packet, RATE,
    ingest_recording, _cohort, _counts, _observations, _primary_rows,
)


@pytest.mark.parametrize('outcome', ['success', 'source_revoked'])
def test_auto_discovery_pending_preserves_attempt_once(primary_app_factory, monkeypatch, record_property, outcome):
    with primary_app_factory() as h:
        _tripwires(h, monkeypatch)
        jobs, permit, automatic = h.jobs, h.jobs.permit, h.automatic
        forbidden_comparators = []
        def no_comparator(*args, **kwargs):
            forbidden_comparators.append(True)
            raise AssertionError('No comparator construction in this three-node gate')
        monkeypatch.setattr(comparison_process, 'ComparisonProcess', no_comparator)
        census = []
        sample = permit._sampler
        def counted_sample(settings):
            census.append(threading.get_ident())
            return sample(settings)
        monkeypatch.setattr(permit, '_sampler', counted_sample)
        generation, observed, sampled_at = permit._sample_generation, deepcopy(permit._observed), permit._last_sample_at
        status = permit.status()
        comparison_keys = [key for key in status if key.startswith('comparison_')]
        empty_records = []
        for cycle in range(2):
            if cycle:
                time.sleep(max(0., jobs._history_compare_after-time.monotonic()))
            h.worker._automatic_handoff(request=False)
            empty = jobs._history_discovery
            assert empty is not None and isinstance(empty['thread'], ManualThread)
            empty['thread'].run()
            assert empty['completed'].is_set() and empty['state']=='idle'
            assert empty['snapshot'] is None and empty['driver'] is None
            assert jobs.maintain_history_comparison()
            assert jobs._history_discovery is None and jobs._history_comparison is None
            assert permit._comparison is None and permit._sample_generation is generation
            assert permit._observed==observed and permit._last_sample_at==sampled_at and not census
            assert {key:permit.status()[key] for key in comparison_keys}=={key:status[key] for key in comparison_keys}
            empty_records.append(empty)
        assert empty_records[0] is not empty_records[1]
        cids = ingest_recording(h, frames=20*RATE)
        assert len(cids)==2 and _cohort(h)==cids[0]
        first = jobs._primary_pending
        assert first is not None and first['state']=='pending' and first['thread'] is None
        first_token = first['reservation']['token']
        assert jobs._stop_primary() and jobs._primary_pending is None and not h.probes
        assert _counts(h)==dict(observations=1, vectors=0, pending=0, candidate_missing=1, attempts=1)
        first_observation = deepcopy(_observations(h)[0])
        assert first_observation['attempt']==first_token and first_observation['evidence_kind']=='candidate_missing'
        available_closed, release_available = threading.Event(), threading.Event()
        coordinator_bound, release_coordinator = threading.Event(), threading.Event()
        threads, joins, phases, begin_results = [], [], [], []
        fixture_deadline = time.monotonic()+10.
        def remaining():return max(0., fixture_deadline-time.monotonic())
        class Coordinator(threading.Thread):
            def join(self, timeout=None):
                assert timeout is not None
                joins.append((self, timeout))
                return super().join(min(timeout, remaining()))
        def thread_factory(**kwargs):
            if kwargs.get('name')=='recording-history-compare' and not threads:
                thread=Coordinator(**kwargs);threads.append(thread);return thread
            return ManualThread(**kwargs)
        monkeypatch.setattr(jobs, '_thread_factory', thread_factory)
        available = jobs.history.comparison_work_available
        def checked_available():
            result=available()  # Real bounded SQL and positive close precede the barrier.
            if threading.current_thread() in threads:
                assert result is True and not jobs._lock._is_owned() and not permit._condition._is_owned()
                phases.append('availability_true_closed');available_closed.set()
                assert release_available.wait(remaining())
            return result
        monkeypatch.setattr(jobs.history, 'comparison_work_available', checked_available)
        bind = permit.bind_comparison_thread
        def held_bind(token, thread, completed):
            result=bind(token, thread, completed)
            if thread in threads:
                assert not jobs._lock._is_owned() and not permit._condition._is_owned()
                assert permit._comparison['thread'] is thread and permit._comparison['owner'] is None
                phases.append('original_coordinator_bound');coordinator_bound.set()
                assert release_coordinator.wait(remaining())
            return result
        monkeypatch.setattr(permit, 'bind_comparison_thread', held_bind)
        begin = jobs.history.begin_comparison
        def checked_begin(**kwargs):
            result=begin(**kwargs);begin_results.append(result);return result
        monkeypatch.setattr(jobs.history, 'begin_comparison', checked_begin)
        calls = {'step':[], 'submit':[], 'reserve':[], 'dispatch':[], 'append':[]}
        step, submit, reserve = automatic.step, jobs.submit_primary, permit.reserve_optional
        dispatch, append = jobs.history.dispatch_current, jobs.history.append_primary
        def checked_step(**kwargs):
            calls['step'].append(time.monotonic());return step(**kwargs)
        def checked_submit():
            probe=jobs._primary_pending
            calls['submit'].append((probe, time.monotonic()))
            return submit()
        def checked_reserve(token, cancelled, callback, **kwargs):
            calls['reserve'].append((token, cancelled, kwargs['work_deadline']))
            return reserve(token, cancelled, callback, **kwargs)
        def checked_dispatch(reservation, **kwargs):
            try:result=dispatch(reservation, **kwargs)
            except BaseException as error:
                calls['dispatch'].append(getattr(error, 'reason', type(error).__name__));raise
            calls['dispatch'].append('current');return result
        def checked_append(*args, **kwargs):
            calls['append'].append(args);return append(*args, **kwargs)
        monkeypatch.setattr(automatic, 'step', checked_step)
        monkeypatch.setattr(jobs, 'submit_primary', checked_submit)
        monkeypatch.setattr(permit, 'reserve_optional', checked_reserve)
        monkeypatch.setattr(jobs.history, 'dispatch_current', checked_dispatch)
        monkeypatch.setattr(jobs.history, 'append_primary', checked_append)
        try:
            h.worker._automatic_handoff(request=False)
            assert available_closed.wait(remaining())
            discovery=jobs._history_discovery
            assert discovery is not None and discovery['thread'] is threads[0]
            original = {key:discovery[key] for key in ('thread','grant','generation','cancelled','completed','admission_start','deadline')}
            assert jobs._history_comparison is None and permit._comparison is None
            assert _cohort(h)==cids[1]
            probe=jobs._primary_pending
            assert probe is not None and probe is not first and probe['state']=='pending'
            identities={key:probe[key] for key in ('reservation','crop','node','cancelled','completed','fence')}
            values={key:deepcopy(probe[key]) for key in ('reservation','node','invocation_id','attempt_id','identity',
                'chunk_id','session_id','recording_id','source_start_frame','start','end','saved_header','saved_packet_bytes','receipt_bytes')}
            assert probe['chunk_id']==cids[1] and probe['work_deadline'] is None
            assert _counts(h)==dict(observations=2, vectors=0, pending=1, candidate_missing=1, attempts=2)
            release_available.set()
            assert coordinator_bound.wait(remaining())
            assert jobs._history_discovery is None and jobs._history_comparison is discovery
            assert all(discovery[key] is value for key,value in original.items() if key not in ('admission_start','deadline'))
            assert all(discovery[key]==original[key] for key in ('admission_start','deadline'))
            assert permit._comparison['token'] is discovery['grant'] and permit._comparison['owner'] is None
            before_census=len(census)
            before_step, before_submit = len(calls['step']), len(calls['submit'])
            h.worker._automatic_handoff(request=True)
            assert len(calls['step'])==before_step+1 and len(calls['submit'])==before_submit+1
            assert calls['submit'][-1][0] is probe and calls['dispatch']==['current']
            deadline=probe['work_deadline'];next_attempt=automatic.next_attempt
            assert WORK_SECONDS==10. and 0.<deadline-time.monotonic()<=WORK_SECONDS
            assert calls['submit'][-1][1]+WORK_SECONDS<=deadline
            binding=probe['dispatch_binding']
            def same_pending():
                assert jobs._primary_pending is probe and probe['state']=='pending' and probe['admitted']
                assert all(probe[key] is value for key,value in identities.items())
                assert all(probe[key]==value for key,value in values.items())
                assert probe['work_deadline']==deadline and probe['dispatch_binding'] is binding
                assert not probe['cancelled'].is_set() and not probe['completed'].is_set()
                assert probe['thread'] is None and not probe['thread_start_attempted'] and probe['operation'] is None
                assert permit._optional is None and permit._primary_probe_admission is None and not h.probes
            same_pending()
            assert len(census)==before_census and len(calls['reserve'])==1 and not calls['append']
            assert calls['reserve'][0]==(probe, probe['cancelled'], deadline)
            assert not begin_results and not discovery['completed'].is_set()
            release_coordinator.set();threads[0].join(remaining())
            assert not threads[0].is_alive() and discovery['completed'].is_set()
            assert begin_results==[None] and discovery['snapshot'] is None and discovery['driver'] is None
            assert discovery['state']=='idle' and jobs.maintain_history_comparison()
            assert jobs._history_comparison is None and permit._comparison is None
            assert any(thread is threads[0] and timeout==0 for thread,timeout in joins)
            phases.append('original_coordinator_joined_and_grant_released')
            same_pending()
            assert automatic.next_attempt==next_attempt and _observations(h)[0]==first_observation
            if outcome=='source_revoked':
                assert history.notify_source_mismatch(h.store, [cids[1]]) is True
            time.sleep(max(0., next_attempt-time.monotonic()))
            assert automatic.next_attempt==next_attempt and probe['work_deadline']==deadline
            assert time.monotonic()<deadline
            h.worker._automatic_handoff(request=True)
            assert len(calls['submit'])==before_submit+2 and calls['submit'][-1][0] is probe
            assert probe['work_deadline']==deadline
            if outcome=='success':
                assert jobs._primary_pending is probe and probe['state']=='running'
                assert isinstance(probe['thread'], ManualThread) and probe['thread'].is_alive()
                assert len(calls['reserve'])==2 and permit._optional is probe
                incidental=jobs._history_discovery
                if incidental is not None and incidental['thread'].is_alive():
                    assert isinstance(incidental['thread'], ManualThread)
                    incidental['thread'].run()  # Second durable attempt is still reserved: real query is false.
                    assert incidental['state']=='idle' and incidental['completed'].is_set()
                probe['thread'].run()
                assert probe['completed'].is_set() and not probe['thread'].is_alive()
                assert type(probe['operation']) is ProbeOperation and probe['operation'].work_deadline==deadline
                assert probe['operation'].started and probe['operation'].cleaned
                assert len(h.probes)==1 and h.probes[0].starts==h.probes[0].cleanups==1
                assert len(calls['append'])==1 and jobs._maintain_primary()
                assert _counts(h)==dict(observations=2, vectors=1, pending=0, candidate_missing=1, attempts=2)
            else:
                assert jobs._primary_pending is None and not h.probes and not calls['append']
                assert len(calls['reserve'])==1 and calls['dispatch']==['current','history_authority_changed']
                assert probe['operation'] is None and probe['thread'] is None and not probe['thread_start_attempted']
                assert _counts(h)==dict(observations=2, vectors=0, pending=0, candidate_missing=2, attempts=2)
                with h.store.db() as db:
                    scope=db.execute(f'SELECT state,authority_blocked,current_slot FROM {history.SCOPES} WHERE scope_seq>0').fetchone()
                    assert tuple(scope)==('pending','source_changed',None)
                    assert {row[0] for row in db.execute(f'SELECT state FROM {history.SOURCES} WHERE chunk_id=?',(cids[1],))}=={'revoked'}
            assert _observations(h)[0]==first_observation  # Original unknown rival is retained.
            for cid in cids:_assert_primary_packet(h,cid)
            assert [row['text'] for row in _primary_rows(h)]==['synthetic','synthetic']
            with h.store.db() as db:
                assert len(list(db.execute(f'SELECT token FROM {history.ATTEMPTS}')))==2
                assert all(tuple(row)==(0,0) for row in db.execute(f'SELECT reserved_bytes,staging_bytes FROM {history.ATTEMPTS}'))
            assert jobs._primary_pending is None and jobs._probe_staging_bytes==0 and permit._optional is None
            record_property('integration_evidence', json.dumps({'outcome':outcome,'empty_cycles':2,
                'real_coordinator_threads':len(threads),'same_attempt_retried':True,'original_work_seconds':WORK_SECONDS,
                'pending_census_delta':0,'phases':phases,'begin_none_count':len(begin_results),
                'probe_driver_starts':sum(item.starts for item in h.probes),'append_calls':len(calls['append']),
                'counts':_counts(h),'identity_publication_claimed':False},sort_keys=True))
        finally:
            release_available.set();release_coordinator.set()
            for thread in threads:
                if thread.ident is not None:thread.join(remaining())
            assert all(not thread.is_alive() for thread in threads)
            for task in (jobs._history_discovery,jobs._history_comparison):
                if task is not None:
                    task['cancelled'].set()
                    if isinstance(task['thread'],ManualThread) and task['thread'].is_alive():task['thread'].run()
            assert jobs._stop_history_comparison()
            assert jobs._history_discovery is None and jobs._history_comparison is None and permit._comparison is None
            assert not forbidden_comparators
