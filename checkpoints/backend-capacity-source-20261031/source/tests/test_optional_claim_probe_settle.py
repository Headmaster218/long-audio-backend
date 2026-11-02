"""Deterministic finite empty-claim/census interleavings, no model or subprocess."""
import threading
import time
from unittest.mock import patch
import pytest
from audio_backend import native_execution_permit as mod
from test_native_execution_permit import settings, sample


def fixture():
    permit = mod.NativeExecutionPermit(settings(), sampler=lambda _: sample())
    token, cancelled, callbacks = object(), threading.Event(), []
    permit.reserve_optional(token, cancelled, lambda: callbacks.append(cancelled.is_set()))
    return permit, token, cancelled, callbacks


class Check:
    def __init__(self, permit, token, **kwargs):
        self.done = threading.Event(); self.error = None
        def run():
            try: permit.check_optional(token, **kwargs)
            except BaseException as error: self.error = error
            finally: self.done.set()
        self.thread = threading.Thread(target=run)
    def start(self): self.thread.start()
    def join(self):
        self.thread.join(1.)
        assert not self.thread.is_alive()


@pytest.mark.parametrize('before', [True, False])
def test_empty_probe_settles_at_either_fingerprint_without_cancel(before):
    p, token, cancelled, callbacks = fixture()
    sampled, release = threading.Event(), threading.Event()
    calls = []
    def census(_):
        calls.append(1); sampled.set()
        assert release.wait(1.)
        return sample()
    p._sampler = census
    check = Check(p, token)
    try:
        if before:
            with p.claim_probe():
                check.start()
                assert not sampled.wait(.02)
                assert not check.done.is_set()
            assert sampled.wait(1.); release.set()
        else:
            check.start(); assert sampled.wait(1.)
            with p.claim_probe():
                release.set()
                assert not check.done.wait(.02)
                assert not cancelled.is_set()
        check.join()
        assert check.error is None and calls == [1]
        assert not cancelled.is_set() and not callbacks
        assert p._optional is token and not p._primary
    finally:
        release.set()
        if check.thread.ident is not None: check.join()
        p.release_optional(token, cleaned=True)


@pytest.mark.parametrize('raise_in_probe', [False, True])
def test_probe_completes_inside_census_and_exception_retains_existing_contract(raise_in_probe):
    p, token, cancelled, _ = fixture()
    entered, release = threading.Event(), threading.Event()
    def census(_): entered.set(); assert release.wait(1.); return sample()
    p._sampler = census; check = Check(p, token); check.start()
    try:
        assert entered.wait(1.)
        try:
            with p.claim_probe():
                if raise_in_probe: raise ValueError('synthetic claim refusal')
        except ValueError: pass
        release.set(); check.join()
        assert check.error is None and not cancelled.is_set()
    finally:
        release.set(); check.join(); p.release_optional(token, cleaned=True)


@pytest.mark.parametrize('action', ['intent', 'shutdown', 'cancel', 'configuration', 'unavailable'])
@pytest.mark.parametrize('before', [True, False])
def test_real_veto_preempts_while_probe_remains_held(action, before):
    p, token, cancelled, callbacks = fixture()
    entered, release = threading.Event(), threading.Event()
    def census(_): entered.set(); assert release.wait(1.); return sample()
    p._sampler = census; check = Check(p, token); intent = None
    try:
        if not before: check.start(); assert entered.wait(1.)
        with p.claim_probe():
            if before: check.start()
            else: release.set()
            if action == 'intent': intent = p.primary_intent('worker')
            elif action == 'shutdown': p.close_admission()
            elif action == 'cancel': cancelled.set()
            elif action == 'configuration': p._configuration_current = lambda: False
            else: p.mark_unavailable()
            assert check.done.wait(.15)
            check.join()
            assert isinstance(check.error, mod.AdmissionError)
            assert cancelled.is_set() and p._optional is token
            if intent is not None:
                assert callbacks == [True] and not intent.granted
        p.release_optional(token, cleaned=False)
        assert p._optional is token
        p.release_optional(token, cleaned=True)
        if intent is not None:
            intent.wait(); assert intent.entered; intent.release()
    finally:
        release.set()
        if check.thread.ident is not None: check.join()
        p.release_optional(token, cleaned=True)
        if intent is not None: intent.release()


def test_shared_absolute_budget_includes_both_barriers_and_one_census():
    p, token, cancelled, _ = fixture(); now = [100.]; waits = []; calls = []
    def wait(seconds):
        waits.append(seconds); now[0] += seconds
        if now[0] >= 100.08 and not calls: p._probe = False
    def census(_):
        calls.append(1); now[0] += .08; p._probe = True
        return sample()
    p._sampler = census; p._probe = True
    with patch.object(mod.time, 'monotonic', side_effect=lambda: now[0]), patch.object(p._condition, 'wait', side_effect=wait):
        with pytest.raises(mod.AdmissionError, match='optional_claim_check_deadline'):
            p.check_optional(token)
    assert 100.25 <= now[0] < 100.251
    assert calls == [1] and cancelled.is_set() and p._optional is token
    p._probe = False; p.release_optional(token, cleaned=True)


def test_original_short_remaining_budget_is_not_renewed_by_notifications():
    p, token, cancelled, _ = fixture(); now = [100.]; calls = []
    p._sampler = lambda _: (calls.append(1), sample())[1]
    def wait(seconds): now[0] += min(seconds, .003)
    with patch.object(mod.time, 'monotonic', side_effect=lambda: now[0]), patch.object(p._condition, 'wait', side_effect=wait):
        with p.claim_probe():
            with pytest.raises(mod.AdmissionError, match='optional_claim_check_deadline'):
                p.check_optional(token, ownership_remaining=lambda: 100.04-now[0])
    assert now[0] < 100.041 and not calls and cancelled.is_set()
    p.release_optional(token, cleaned=True)


def test_returned_census_cannot_outlive_total_freshness_envelope():
    p, token, cancelled, _ = fixture(); now=[100.]
    def census(_): now[0] += .251; return sample()
    p._sampler=census
    with patch.object(mod.time, 'monotonic', side_effect=lambda:now[0]):
        with pytest.raises(mod.AdmissionError, match='optional_claim_check_deadline'):p.check_optional(token)
    assert cancelled.is_set();p.release_optional(token,cleaned=True)


def test_reentrant_cancel_callback_and_primary_grant_are_not_starved():
    p = mod.NativeExecutionPermit(settings(), sampler=lambda _:sample())
    token, cancel = object(), threading.Event(); nested=[]
    def callback():
        def inspect():
            with p._condition:nested.append(p._optional is token)
        t=threading.Thread(target=inspect);t.start();t.join(.15)
        assert not t.is_alive()
    p.reserve_optional(token,cancel,callback)
    with p.claim_probe():
        check=Check(p,token);check.start()
        intent=p.primary_intent('worker')
        assert check.done.wait(.15);check.join()
        assert nested==[True] and cancel.is_set() and not intent.granted
    p.release_optional(token,cleaned=True);intent.wait();assert intent.entered;intent.release()
