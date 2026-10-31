import json
import math
import shutil
import sys
import tempfile
import threading
import wave
from pathlib import Path
from .resources import run_bounded, check_resources, AnalyzerProcess
from .resident_analyzer import ResidentAnalyzer
from .boundaries import lexical_core, safe_cut
from .boundary_store import stream_info, context_parts, wait_for_input
from .native_worker import NativeCoordinator
from .storage import _ClaimDeferred
from .moss_automatic import _WINDOW_READ_DEFERRED
from .overlap_ownership import own_candidates,validate_candidates
from . import speaker_support, event_candidate_store
from .trial_sampling_census import window_observation, observe_active, observe_commit, observe_callback


def _validation_duration(strict, capture, chunk, native, extract_start, extract_end):
    """Use this strict window's already witnessed PCM, not float subtraction."""
    if not strict:return extract_end-extract_start
    from .native_owned_cells import RATE, CORE_FRAMES, exact_frame, integer, Unavailable
    if (capture is None or native is None or type(capture.analyzer) is not bytes
            or not capture.analyzer or len(capture.analyzer) % 2
            or native.get('extract_start') != extract_start or native.get('extract_end') != extract_end):
        raise Unavailable('strict_analyzer_duration_unavailable')
    origin = exact_frame(chunk['timeline_start'], origin=capture.scope_start)
    if origin != integer(capture.origin_frame):
        raise Unavailable('strict_analyzer_origin_mismatch')
    # The same exact signed local-frame transform used by observe_pcm. Never
    # round arbitrary producer timestamps or adopt result.metadata duration.
    local = tuple(exact_frame(abs(v)) * (-1 if v < 0 else 1)
                  for v in (extract_start, extract_end))
    start, end = (integer(origin + value) for value in local)
    frames = len(capture.analyzer) // 2
    if (capture.analyzer_start != start or not 0 < frames <= CORE_FRAMES
            or frames != end-start):
        raise Unavailable('strict_analyzer_frame_mismatch')
    return frames/RATE


def validate_result(result,duration):
    if not isinstance(result,dict): raise ValueError('Invalid analyzer result')
    if not isinstance(result.get('metadata',{}),dict): raise ValueError('Invalid analyzer metadata')
    for key in ('asr_backend','timestamp_semantics'):
        value=result.get('metadata',{}).get(key)
        if value is not None and (not isinstance(value,str) or len(value)>100000): raise ValueError(f'Invalid metadata {key}')
    segments=result.get('segments',[]); events=result.get('events',[]);turns=result.get('speaker_turns',[])
    if not isinstance(turns,list) or len(turns)>4096:raise ValueError('Invalid independent speaker turns')
    if not isinstance(segments,list) or not isinstance(events,list) or len(segments)>1000 or len(events)>1000:
        raise ValueError('Analyzer output item limit exceeded')
    for item in segments+events+turns:
        if not isinstance(item,dict): raise ValueError('Invalid analyzer item')
        for key in ('start','end'):
            v=item.get(key)
            if not isinstance(v,(int,float)) or not math.isfinite(v): raise ValueError('Invalid analyzer timestamp')
        if not 0<=item['start']<=item['end']<=duration+.15: raise ValueError('Analyzer timestamp outside window')
        item['start']=min(item['start'],duration)
        item['end']=min(item['end'],duration)
    for s in segments:
        if not isinstance(s.get('text',''),str) or len(s.get('text',''))>100000: raise ValueError('Invalid text')
        for optional_text in ('raw_text','asr_backend','timestamp_kind'):
            value=s.get(optional_text)
            if value is not None and (not isinstance(value,str) or len(value)>100000): raise ValueError(f'Invalid {optional_text}')
        units=s.get('_units',[])
        if not isinstance(units,list) or len(units)>4096:raise ValueError('Invalid lexical units')
        for u in units:
            if not isinstance(u,dict) or not isinstance(u.get('text'),str):raise ValueError('Invalid lexical unit')
            for name in ('start','end','ownership_anchor'):
                v=u.get(name)
                if not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=duration+.15:
                    raise ValueError('Invalid lexical timestamp')
            if u['end']<u['start']:raise ValueError('Reversed lexical interval')
        if s.get('speaker_local_id') is not None: s['speaker_local_id']=str(s['speaker_local_id'])[:128]
        confidence=s.get('confidence')
        if confidence is not None and (not isinstance(confidence,(int,float)) or not math.isfinite(confidence)): raise ValueError('Invalid confidence')
    for e in events:
        if not isinstance(e.get('label'),str) or len(e['label'])>512: raise ValueError('Invalid event label')
        if not isinstance(e.get('score'),(int,float)) or not math.isfinite(e['score']) or not 0<=e['score']<=1: raise ValueError('Invalid event score')
    result['segments']=[s for s in segments if s['end']>s['start']]
    result['events']=[e for e in events if e['end']>e['start']]
    result['speaker_turns']=[t for t in turns if t['end']>t['start']]
    for t in result['speaker_turns']:
        if t.get('speaker_local_id') is not None:t['speaker_local_id']=str(t['speaker_local_id'])[:128]
    if result.get('overlap_candidates') is not None:validate_candidates(result['overlap_candidates'],duration)
    # Ensure metadata is finite JSON and bounded alongside all other output.
    if len(json.dumps(result,allow_nan=False))>8*1024**2: raise ValueError('Analyzer result too large')
    return result


def own_core(result,start,end,previous=None,base=0.,*,retain_lexical=False):
    """Own lexical units when available, with legacy adapter fallback disclosed."""
    owned=(lexical_core(result,start,end,previous,base,retain_lexical=True)
           if retain_lexical else lexical_core(result,start,end,previous,base))
    if owned is None:
        if retain_lexical:result['_lexical_rejection']='unsupported_adapter'
        owned=[]
        for segment in result['segments']:
            midpoint=(segment['start']+segment['end'])/2
            if start<=midpoint<end:
                segment=dict(segment)
                segment['start']=max(start,segment['start'])-start
                segment['end']=min(end,segment['end'])-start
                owned.append(segment)
        result.setdefault('metadata',{})['boundary_policy']='legacy adapter without lexical units: approximate segment midpoint ownership'
    result['segments']=owned
    events=[]
    for event in result['events']:
        lo=max(start,event['start']);hi=min(end,event['end'])
        if hi>lo:
            event=dict(event);event['start']=lo-start;event['end']=hi-start;events.append(event)
    result['events']=events
    turns=[]
    for turn in result.get('speaker_turns',[]):
        lo=max(start,turn['start']);hi=min(end,turn['end'])
        if hi>lo:turns.append(dict(turn,start=lo-start,end=hi-start))
    result['speaker_turns']=turns
    result['event_candidates']=event_candidate_store.own_candidates(result.setdefault('metadata',{}),start,end)
    if result.get('overlap_candidates') is not None:
        result['overlap_candidates']=own_candidates(result['overlap_candidates'],start,end,base)
    overlap=result.get('metadata',{}).get('overlapping_speech')
    if overlap:
        regions=[]
        for region in overlap.get('regions',[]):
            lo=max(start,region['start']);hi=min(end,region['end'])
            if hi>lo:regions.append(dict(region,start=lo+base,end=hi+base))
        overlap=dict(overlap,regions=regions,time_axis='chunk_relative',
                     status='unresolved_mixture_asr' if regions else 'none_detected')
        result['metadata']['overlapping_speech']=overlap
    return result


class Worker:
    def __init__(self,store,settings,analyzer_factory=None,*,trial_observer=None):
        self.trial_observer=trial_observer
        self.store=store; self.settings=settings; self.analyzer_factory=analyzer_factory
        self.stop_event=threading.Event(); self.wake=threading.Event(); self.current_chunk=None
        self._terminal_error_class=None
        self.native=None
        self._strict_active=False; self._strict_cleanup_failed=False
        self._strict_analyzer_owner=None; self._strict_native_owner=None
        self._strict_intent_owner=None; self._strict_first_error=None
        self._strict_cohort_finished=False
        self._strict_wait_pending=None
        self._payload_frontier_owner=None
        self._payload_maintenance_error=None
        self._payload_cleanup_observer_error=None
        self.resident_analyzer=None
        self.native_owned_support_cache=None
        self.native_owned_cells=None
        self.recordings=None
        self.moss_automatic=None;self._fair_core_handoff=False
        self._native_capture=None;self._native_capture_token=None
        self._native_capture_observation=None
        self.support_fingerprints=speaker_support.SourceFingerprints()
        self.thread=threading.Thread(target=self.loop,name='audio-worker',daemon=True)
    def start(self): self.thread.start()
    def stop(self):
        if self.native_owned_support_cache is not None:
            try:self.native_owned_support_cache.close()
            except Exception:pass
        self.stop_event.set(); self.wake.set()
        if self.thread.is_alive(): self.thread.join(timeout=15)
        if getattr(self.settings, 'strict_primary_enabled', False):
            if self.thread.is_alive():return False
            return self._strict_retire() and self.store.strict_cleanup_confirmed()
        if not self.thread.is_alive() and self.native:self.native.close();self.native=None
        return not self.thread.is_alive()
    @staticmethod
    def _strict_cancellation(error):
        # Only these exact producers are intentional cancellation, not OS EINTR.
        return (type(error) is InterruptedError and error.errno is None
                and error.args in ((), ('Worker stopping or chunk paused/cancelled',),
                    ('Native diarization interrupted',), ('Primary wait interrupted',)))

    def _strict_unknown(self, error):
        self._strict_cleanup_failed=True
        if self._strict_first_error is None:self._strict_first_error=error
        self.store.strict_cleanup_unknown(self, kind='worker', error=error)

    def _strict_classify(self, error):
        if isinstance(error, OSError) and not self._strict_cancellation(error):
            self._strict_unknown(error)

    def _strict_permit_closed(self):
        permit=getattr(self.settings, '_native_execution_permit', None)
        if permit is None:return True
        with permit._condition:
            return (not permit._handles and permit._unavailable is None
                and permit._primary_observation_failure is None)

    def _strict_retire(self):
        # Ordinary concurrent DB/publication work is not unknown ownership.
        # Sticky uncertainty, however, forbids re-close/re-census of this cohort.
        if self._strict_cleanup_failed:return False
        if self.store.strict_cleanup_uncertain():
            self._strict_cleanup_failed=True
            return False
        if self.resident_analyzer is not None:
            try:self.resident_analyzer.retire()
            except BaseException as exc:
                self._strict_unknown(exc);return False
        native=self.native
        if native is not None:
            process=native.process
            self._strict_native_owner=process
            try:
                native.close()
                if process is not None:
                    if process.proc.poll() is None or process.proc.wait(timeout=0) is None:
                        raise RuntimeError('Strict native original wait unconfirmed')
                    permit=getattr(self.settings, '_native_execution_permit', None)
                    if permit is not None:permit.finish_primary(process)
                self.native=None
            except BaseException as exc:
                self._strict_unknown(exc);return False
        permit=getattr(self.settings, '_native_execution_permit', None)
        if permit is not None:
            with permit._condition:
                sticky=permit._unavailable is not None or permit._primary_observation_failure is not None
            if sticky:
                self._strict_unknown(RuntimeError('Strict native ownership unconfirmed'));return False
            if not self._strict_cohort_finished:
                self._strict_cohort_finished=True
                try:permit.finish_cohort(normal=False)
                except BaseException as exc:
                    self._strict_unknown(exc);return False
        if (self._strict_analyzer_owner is not None or not self._strict_permit_closed()
                or self.resident_analyzer is not None and self.resident_analyzer.uncertain):
            self._strict_unknown(RuntimeError('Strict native ownership unconfirmed'))
        positive=not self._strict_cleanup_failed and not self.store.strict_cleanup_uncertain()
        if positive:self._strict_native_owner=None
        return positive

    def _strict_analyzer_process_factory(self, *args, **kwargs):
        try:return AnalyzerProcess(*args, **kwargs)
        except BaseException as exc:
            self._strict_unknown(exc)
            raise

    def _strict_test_analyze(self, wav, arguments):
        # Test injection has the same one-shot ownership, but no subprocess claim.
        try:analyzer=self.analyzer_factory(self.settings.inference_config())
        except BaseException as exc:
            self._strict_unknown(exc); raise
        self._strict_analyzer_owner=analyzer
        original=None
        try:return analyzer.analyze(str(wav), **arguments)
        except BaseException as exc:
            original=exc; raise
        finally:
            try:analyzer.close()
            except BaseException as cleanup:
                self._strict_unknown(cleanup)
                if original is None:raise
            else:self._strict_analyzer_owner=None

    def _run_strict_chunk(self, chunk, intent, permit, fair, watermark):
        self.current_chunk=chunk['id']; self._strict_active=True
        self._strict_intent_owner=intent
        self._strict_cohort_finished=False
        self._strict_wait_pending=None
        normal=False; error=None; requeue=False
        def cleanup(call):
            nonlocal error
            try:call()
            except BaseException as exc:
                if error is None:error=exc
                self._strict_unknown(exc)
        try:
            if self.store.strict_cleanup_uncertain():
                raise RuntimeError('Strict cleanup is uncertain')
            if intent is not None and not intent.entered:intent.wait(lambda:self.interrupted(chunk['id']))
            self._begin_native_support(chunk,watermark)
            self._fair_core_handoff=fair
            yielded=self.process(chunk)
            requeue=fair and yielded is True
            normal=True
        except BaseException as exc:
            error=exc; self._strict_classify(exc)
        finally:
            self._fair_core_handoff=False
            cleanup(self._finish_native_support)
            if self.store.strict_cleanup_uncertain():self._strict_cleanup_failed=True
            if not normal:cleanup(self._strict_retire)
            if intent is not None:
                if intent.entered and not self._strict_cleanup_failed and not self._strict_cohort_finished:
                    self._strict_cohort_finished=True
                    cleanup(lambda:permit.finish_cohort(normal=normal))
                # finish_cohort deliberately swallows observation errors. Its
                # sticky flags, and empty handles after failure, are authority.
                if permit is not None and not self._strict_cleanup_failed:
                    def reconcile():
                        with permit._condition:
                            healthy=(permit._unavailable is None and permit._primary_observation_failure is None
                                and (normal or not permit._handles))
                        if not healthy:raise RuntimeError('Strict permit retirement unconfirmed')
                    cleanup(reconcile)
                if not self._strict_cleanup_failed:
                    cleanup(intent.release)
                    if not self._strict_cleanup_failed:self._strict_intent_owner=None
            self.current_chunk=None; self._strict_active=False
        # Durable ordinary continuation is published only after original
        # support/cohort/intent cleanup is known positive. On unknown, active
        # remains durable, so restart recovery requires explicit manual retry.
        if error is None and normal and not self._strict_cleanup_failed and not self.store.strict_cleanup_uncertain():
            try:
                if self._strict_wait_pending is not None:
                    observed_end,observed_final,acknowledged_offset=self._strict_wait_pending
                    # Only this continuation gets the acknowledged cursor. The
                    # original claim remains unchanged for Capture/requeue/pause.
                    self.store.strict_wait_for_input(dict(chunk,next_offset=acknowledged_offset),
                        observed_end,observed_final)
                elif requeue:self.store.requeue_core(chunk)
                self._strict_wait_pending=None
                self._maintain_payloads(chunk)
            except BaseException as exc:
                error=exc; self._strict_classify(exc)
                cleanup(self._strict_retire)
        if error is not None:
            try:self.store.strict_pause(chunk, f'{type(error).__name__}: {error}'[:512],
                cleanup_confirmed=not self._strict_cleanup_failed and not self.store.strict_cleanup_uncertain())
            except BaseException:
                raise error.with_traceback(error.__traceback__)
            if not isinstance(error, Exception):raise error
            # A completed chunk remains a completed accepted prefix.  Its
            # post-cleanup storage refusal must nevertheless stop this Worker,
            # not disappear through strict_pause's completed-row no-op.
            if self._payload_maintenance_error is error:raise error.with_traceback(error.__traceback__)
        if self._strict_cleanup_failed or self.store.strict_cleanup_uncertain():
            # Original work failure remains primary, with retained cleanup owner.
            if error is not None:raise error.with_traceback(error.__traceback__)
            raise RuntimeError('Strict worker requires process restart after uncertain cleanup')

    def _payload_note_unknown(self, error):
        """Retain an observer fault without replacing the original cleanup."""
        self._strict_cleanup_failed=True
        if self._strict_first_error is None:self._strict_first_error=error
        try:self._strict_unknown(error)
        except BaseException as observer_error:
            if self._payload_cleanup_observer_error is None:
                self._payload_cleanup_observer_error=observer_error

    def _maintain_payloads(self, chunk):
        """One fresh live-v2 unit after original attempt cleanup/continuation.

        This independent storage housekeeping phase never reuses or resets the
        native proof Budget.  Store owns the one cumulative archive Budget;
        known short ownership contention merely defers to a later ordinary
        opportunity, while every other exception keeps the strict fail path.
        """
        from .settings import payload_live_enabled
        if not payload_live_enabled(self.settings):return None
        if self._payload_maintenance_error is not None:raise self._payload_maintenance_error
        from .strict_payload_v2 import PayloadBudget, PayloadAdmissionBusy, PayloadBeginBusy
        from .native_owned_cell_capture import PayloadFrontierBusy
        budget = PayloadBudget()
        witness = None; original = None
        try:
            budget.check()
            if self.native_owned_cells is None:raise RuntimeError('Live payload Capture unavailable')
            witness = self.native_owned_cells.payload_frontier(self, chunk)
            if witness is None:return None
            return self.store.maintain_payloads(witness, budget)
        except BaseException as exc:
            if (type(exc) in (PayloadFrontierBusy, PayloadAdmissionBusy, PayloadBeginBusy)
                    and not self._strict_cleanup_failed and not self.store.strict_cleanup_uncertain()):
                # These exact producers prove ordinary contention.  A saved
                # busy exception accompanied by unknown cleanup is not defer.
                # No new claim, archive retry or maintenance call is triggered.
                return None
            original = exc
            if self._payload_maintenance_error is None:self._payload_maintenance_error = exc
            raise
        finally:
            if witness is not None:
                try:witness.close()
                except BaseException as cleanup:
                    self._payload_note_unknown(cleanup)
                    if original is None:raise cleanup

    def notify(self): self.wake.set()
    def interrupted(self,cid):
        if self.stop_event.is_set(): return True
        try: return self.store.chunk(cid)['status']!='running'
        except KeyError: return True
    def loop(self):
        try:
            try:self._run_loop()
            finally:
                try:
                    if getattr(self.settings, 'strict_primary_enabled', False):self._strict_retire()
                    elif self.resident_analyzer is not None:self.resident_analyzer.close()
                finally:
                    permit=getattr(self.settings, '_native_execution_permit', None)
                    if permit is not None:permit.abandon_automatic_turn()
        except BaseException as exc:
            # Bounded terminal metadata only; retain the original propagation and
            # cleanup semantics. Never publish an exception message or traceback.
            name=type(exc).__name__
            self._terminal_error_class=(name if type(name) is str and 1<=len(name)<=64
                and name.isascii() and name.isidentifier() else 'UnclassifiedError')
            raise

    def _run_loop(self):
        while not self.stop_event.is_set():
            if (self.moss_automatic is not None
                    and getattr(self.settings,'native_owned_bounded_core_support_enabled',False)):
                # One metadata-only expiry/startup turn before claiming any
                # primary/model permit, including when no input will arrive.
                try:self.moss_automatic.maintain_bounded_waiters(stop=self.stop_event.is_set)
                except Exception as exc:
                    # Optional metadata maintenance cannot kill the primary
                    # worker. Its failed or uncertain transaction is not an
                    # acknowledged closure: the next turn rereads durable state.
                    from .native_owned_cells import Unavailable
                    from .native_owned_support_cache import ReadError
                    from .native_owned_cell_store import _diagnostic
                    reason=(exc.reason if isinstance(exc,ReadError) else str(exc)
                            if isinstance(exc,Unavailable) else 'bounded_waiter_maintenance_unavailable')
                    if (type(reason) is not str or not 1<=len(reason)<=80
                            or any(c not in 'abcdefghijklmnopqrstuvwxyz_' for c in reason)):
                        reason='bounded_waiter_maintenance_unavailable'
                    self.moss_automatic.last_reason=reason
                    _diagnostic('bounded_waiter_maintenance_refused reason=%s',reason)
            fragments=getattr(self.store,'fragment_http',None)
            permit=getattr(self.settings, '_native_execution_permit', None)
            if permit is not None and fragments and fragments.factory is not None:
                permit.close_admission()
            if fragments and fragments.cleanup_error is not None:
                self.wake.wait(1); self.wake.clear(); continue
            if permit is None and fragments and fragments.process_next(self):
                continue
            if self.recordings is not None:
                # Bounded model-free outbox maintenance; pending recording work
                # stays staged while unrelated ordinary jobs remain claimable.
                self.recordings.advance(self.native_owned_cells, stop=self.stop_event.is_set)
            if not (self.moss_automatic is not None and getattr(self.moss_automatic, 'primary_only', False)):
                self._cell_call('drain_core_seams')
            watermark=None
            if getattr(self.settings,'native_owned_support_enabled',False) and self.native_owned_support_cache is not None:
                try:
                    self.native_owned_support_cache.drain()
                    watermark=self.native_owned_support_cache.preclaim()
                except Exception:
                    # Optional observation never takes authority over primary work.
                    pass
            from .moss_automatic import enabled
            fair=permit is not None and self.moss_automatic is not None and (
                enabled(self.settings) or getattr(self.moss_automatic, 'primary_only', False))
            intent=None;chunk=None
            try:
                if permit is None:
                    chunk=self.store.claim()
                else:
                    with permit.claim_probe():
                        chunk=self.store.claim(probe=True) if fair else self.store.claim()
                        if chunk is not None:
                            intent=permit.primary_intent('worker')
                    if fair and intent is not None:
                        # The row stays queued throughout the optional wait.
                        intent.wait(self.stop_event.is_set, handoff=self._automatic_handoff)
                        with permit.claim_probe():chunk=self.store.claim()
            except _ClaimDeferred:
                if intent is not None:intent.release()
                # An unknown queue is not an idle queue. Release the probe,
                # wait interruptibly, then recheck primary work before MOSS.
                self.stop_event.wait(.1)
                continue
            except InterruptedError:
                if intent is not None:intent.release()
                if self.stop_event.is_set():break
                continue
            except BaseException:
                if intent is not None:intent.release()
                raise
            if chunk is None:
                if intent is not None:intent.release()
                if fair:self._automatic_handoff(request=True)
                elif self.moss_automatic is not None:self.moss_automatic.step()
                self.wake.wait(1); self.wake.clear(); continue
            if chunk.get('_strict_primary') is True:
                self._run_strict_chunk(chunk,intent,permit,fair,watermark)
                continue
            self.current_chunk=chunk['id']
            normal=False;preflight_deferred=False
            try:
                if intent is not None and not intent.entered:intent.wait(lambda:self.interrupted(chunk['id']))
                self._begin_native_support(chunk,watermark)
                self._fair_core_handoff=fair
                yielded=self.process(chunk)
                normal=True
                preflight_deferred=yielded is _WINDOW_READ_DEFERRED
                if fair and yielded is True:self.store.requeue_core(chunk)
            except InterruptedError:
                if self.stop_event.is_set():
                    with self.store.db() as db:
                        db.execute("UPDATE chunks SET status='queued' WHERE id=? AND status='running'",(chunk['id'],))
            except Exception as exc: self.store.fail(chunk['id'],f'{type(exc).__name__}: {exc}')
            finally:
                try:
                    if not normal and self.resident_analyzer is not None:
                        self.resident_analyzer.retire()
                finally:
                    self._fair_core_handoff=False
                    self._finish_native_support()
                    if intent is not None:
                        if intent.entered:permit.finish_cohort(normal=normal)
                        intent.release()
                    self.current_chunk=None
            if preflight_deferred:
                # Unknown preflight is not an idle optional opportunity. The
                # exact offset remains queued after ordinary process cleanup.
                self.stop_event.wait(.1)
                continue
            if fair:self._automatic_handoff(request=True)

    def _automatic_handoff(self, *, request=False):
        permit=getattr(self.settings, '_native_execution_permit', None)
        automatic=self.moss_automatic
        if permit is None or automatic is None:return
        if self.stop_event.is_set():permit.abandon_automatic_turn();return
        if permit.sampling_recovery_pending:permit.recover_primary_sampling()
        automatic.maintain_text_storage(stop=self.stop_event.is_set)
        if automatic.jobs is not None:
            try:automatic.jobs.maintain_history_comparison()
            except Exception:pass
        if request and automatic.primary_yield_due():permit.request_automatic_turn()
        with permit.automatic_turn() as granted:
            if granted:automatic.step(stop=self.stop_event.is_set)
        if automatic.jobs is not None:
            try:automatic.jobs.maintain_history_comparison()
            except Exception:pass

    def _begin_native_support(self,chunk,watermark):
        cache=self.native_owned_support_cache
        if not getattr(self.settings,'native_owned_support_enabled',False) or cache is None or watermark is None:
            return
        try:
            if self.settings.diarization_backend!='nemotron':return
            token=cache.begin(chunk,watermark)
            if token is None:return
            self._native_capture_token=token
            from .native_owned_support import SourceMaterial,NativeWitness
            self._native_capture=NativeWitness(SourceMaterial.inspect(chunk))
        except Exception:
            self._finish_native_support()

    def _finish_native_support(self):
        token=self._native_capture_token
        self._native_capture=None;self._native_capture_token=None
        self._native_capture_observation=None
        if token is not None:
            try:self.native_owned_support_cache.fail(token,'capture_unavailable')
            except Exception:pass

    def _cell_call(self,method,*args):
        capture=self.native_owned_cells
        if capture is None:return None
        try:return getattr(capture,method)(*args)
        except Exception:
            # No optional hook replaces a primary return value or exception.
            return None

    def _native_response(self,response):
        self._cell_call('observe_response',response)
        observation=self._native_capture_observation
        if observation is not None:
            token,witness=observation
            if token is not self._native_capture_token or witness is not self._native_capture:return
            try:witness.observe(response)
            except Exception:
                try:witness.fail('response_observation_unavailable')
                except Exception:self._native_capture=None

    def _native_process_factory(self,settings):
        from .native_worker import NativeProcess
        try:process=NativeProcess(settings)
        except BaseException as exc:
            if self._strict_active:self._strict_unknown(exc)
            raise
        # Retain before binding/observer code can throw or silently latch.
        if self._strict_active:self._strict_native_owner=process
        permit=getattr(settings, '_native_execution_permit', None)
        binding=True
        try:
            if permit is not None:
                permit.bind_primary(process, resident=lambda: self.native is not None
                    and self.native.process is process and self.native.key is not None
                    and self.native.state is not None and not process.busy)
                if self._strict_active:
                    with permit._condition:
                        if permit._unavailable is not None or permit._primary_observation_failure is not None:
                            raise RuntimeError('Strict native ownership binding unavailable')
            binding=False
            prior=getattr(process,'response_observer',None)
            def observe(response):
                if prior is not None:prior(response)
                self._native_response(response)
            process.response_observer=observe
        except BaseException as exc:
            if self._strict_active:
                self._strict_unknown(exc); raise
            if binding or not isinstance(exc, Exception):raise
            self._finish_native_support()
        return process

    def _observe_native_pcm(self,kind,wav,start,end,*,analyzer_local=None):
        if analyzer_local is None:self._cell_call('observe_pcm',kind,wav,start,end)
        else:self._cell_call('observe_pcm',kind,wav,start,end,analyzer_local)
        witness=self._native_capture
        if witness is not None:
            try:witness.observe_pcm(kind,wav,start,end)
            except Exception:
                try:witness.fail('material_observation_unavailable')
                except Exception:self._native_capture=None

    def _native_extract(self,chunk,start,end,wav,tmp,stop,**kwargs):
        result=self.extract_pcm(chunk,start,end,wav,tmp,stop,**kwargs)
        self._observe_native_pcm('native',wav,start,end)
        return result

    def _publish_native_support(self,native,offset,end):
        witness=self._native_capture;token=self._native_capture_token
        if witness is None or token is None:return
        try:
            artifact=witness.finalize(native,offset,end)
            self.native_owned_support_cache.publish(token,artifact)
        except Exception:pass
        finally:self._finish_native_support()
    def process(self,chunk):
        strict=chunk.get('_strict_primary') is True
        previous_active=self._strict_active
        if strict:self._strict_active=True
        try:
            if not self.analyzer_factory and self.resident_analyzer is None:
                self.resident_analyzer=ResidentAnalyzer(self.settings,
                    factory=self._strict_analyzer_process_factory if strict else AnalyzerProcess)
            process=None if self.analyzer_factory else self.resident_analyzer
            try:return self._process_windows(chunk,process)
            except BaseException as exc:
                if strict:
                    self._strict_classify(exc)
                    try:self._strict_retire()
                    except BaseException as cleanup:self._strict_unknown(cleanup)
                    raise
                try:
                    if process is not None:process.retire()
                finally:
                    if self.native:self.native.close();self.native=None
                raise
        finally:self._strict_active=previous_active

    def extract_pcm(self,chunk,start,end,wav,tmp,stop,*,capture_support=False,capture_lexical=False):
        parts=context_parts(self.store,chunk,start,end)
        lineage={'status':'complete','parts':[],'analyzer_frame_count':0}
        lexical_sources=None
        if capture_lexical:
            from .lexical_evidence import snapshot_parts
            with self.store.db() as db:
                lexical_sources=snapshot_parts(db,parts,chunk['session_id'])
        if capture_support:
            try:
                if len(parts)>speaker_support.MAX_PARTS:
                    raise ValueError('Too many source parts for review')
                executable=shutil.which(self.settings.ffmpeg)
                if not executable:raise ValueError('Missing decoder')
                lineage['decoder_sha256']=self.support_fingerprints.get(executable,stop)[0]
            except (OSError,ValueError,RuntimeError) as exc:
                if chunk.get('_strict_primary') is True and isinstance(exc, OSError):raise
                lineage['status']='decoder_or_parts_unavailable'
        with wave.open(str(wav),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
            for index,part in enumerate(parts):
                source_digest=signature=None
                if capture_support and lineage['status']=='complete':
                    try:
                        source_digest,signature=self.support_fingerprints.get(part['path'],stop)
                        with self.store.db() as db:
                            row=db.execute('SELECT digest,session_id FROM chunks WHERE id=?',(part['id'],)).fetchone()
                        if not row or row['digest']!=source_digest or row['session_id']!=chunk['session_id']:
                            lineage['status']='source_content_mismatch'
                    except (OSError,ValueError,RuntimeError) as exc:
                        if chunk.get('_strict_primary') is True and isinstance(exc, OSError):raise
                        lineage['status']='source_lineage_unavailable'
                pcm=tmp/f'part-{index}.wav'
                requested_frames=round(part['read_duration']*16000)
                # Native scheduling requires an exact canonical frame count.
                # Legacy non-native windows retain measured resampled ranges.
                # Only the declared end of an entire source gets an EOF check.
                # One extra requested sample exposes an underestimated duration;
                # a short clean decode exposes an overestimate. Neither is padded
                # or silently clipped to the immutable declared source timeline.
                source_end=round((part['read_start']+part['read_duration'])*16000)>=round(part['duration']*16000)
                verify_source_end=source_end and self.settings.diarization_backend=='nemotron'
                decode_duration=(requested_frames+1)/16000 if verify_source_end else part['read_duration']
                # FFmpeg duration syntax rejects Python's scientific notation
                # for short intervals such as the first 1/16000-second sample.
                run_bounded([self.settings.ffmpeg,'-nostdin','-v','error','-xerror','-protocol_whitelist','file,pipe',
                    '-format_whitelist','wav,flac,ogg,mp3,mov,matroska,webm,aac','-ss',f"{part['read_start']:.9f}",
                    '-i',part['path'],'-t',f'{decode_duration:.9f}','-map','0:a:0','-vn','-ac','1','-ar','16000',
                    '-c:a','pcm_s16le','-threads','1','-y',pcm],self.settings,timeout=180,stop=stop)
                frames=0
                with wave.open(str(pcm),'rb') as source:
                    while block:=source.readframes(16000):
                        output.writeframesraw(block)
                        frames+=len(block)//2
                pcm.unlink()
                if verify_source_end and frames!=requested_frames:
                    raise RuntimeError(f'Source duration does not match decoded PCM: expected {requested_frames} frames, got {frames}; source timeline was not changed')
                first=lineage['analyzer_frame_count'];lineage['analyzer_frame_count']+=frames
                if lexical_sources is not None:
                    lexical_sources[index]['analyzer_frames']=[first,first+frames]
                if capture_support and lineage['status']=='complete':
                    try:
                        if self.support_fingerprints.signature(part['path'])!=signature:
                            raise ValueError('Source changed while decoding')
                        lineage['parts'].append({'chunk_id':part['id'],'source_sha256':source_digest,
                            'seek_seconds':part['read_start'],'requested_seconds':part['read_duration'],
                            'analyzer_frames':[first,first+frames]})
                    except (OSError,ValueError,RuntimeError) as exc:
                        if chunk.get('_strict_primary') is True and isinstance(exc, OSError):raise
                        lineage['status']='source_changed_during_decode'
        if capture_lexical:lineage['_lexical_sources']=lexical_sources
        return lineage if capture_support or capture_lexical else None

    def _process_windows(self,chunk,process):
        strict=chunk.get('_strict_primary') is True
        if strict and (self.native_owned_cells is None or self.settings.diarization_backend != 'nemotron'):
            raise RuntimeError('Strict native capture required')
        offset=chunk['next_offset']
        metadata=chunk.get('metadata') or {}
        if isinstance(metadata,str):metadata=json.loads(metadata)
        previous=metadata.get('boundary_tail',[])
        info=stream_info(self.store,chunk)
        if offset==0 and info and info['previous']:
            predecessor=info['previous'];saved=predecessor.get('metadata') or '{}'
            if isinstance(saved,str):saved=json.loads(saved)
            delta=predecessor['timeline_start']-chunk['timeline_start']
            previous=[dict(u,**{k:u[k]+delta for k in ('start','end','ownership_anchor')})
                      for u in saved.get('boundary_tail',[])]
        while offset<chunk['duration']-1e-5:
            if self.interrupted(chunk['id']): raise InterruptedError()
            automatic=False
            primary_only=self.moss_automatic is not None and getattr(self.moss_automatic, 'primary_only', False)
            primary_recording=False
            if self.moss_automatic is not None:
                from .moss_automatic import enabled,recording
                with self.store.db() as db:
                    is_recording=(enabled(self.settings) or primary_only) and recording(db,chunk['id'])
                    automatic=not primary_only and enabled(self.settings) and is_recording
                    primary_recording=primary_only and is_recording
                preflight=self.moss_automatic.before_window(chunk) if automatic else True
                if preflight is _WINDOW_READ_DEFERRED or not preflight:
                    # Yield before native preparation. The unchanged offset is
                    # durable; process/permit cleanup runs on the ordinary exit.
                    with self.store.db() as db:
                        db.execute("UPDATE chunks SET status='queued',error=? WHERE id=? AND status='running'",
                            (self.moss_automatic.last_reason,chunk['id']))
                    return _WINDOW_READ_DEFERRED if preflight is _WINDOW_READ_DEFERRED else None
            check_resources(self.settings, 4*1024**2)
            info=stream_info(self.store,chunk)
            origin=chunk['timeline_start']
            limit=chunk['duration']
            if info and not info['final']:
                limit=min(limit,max(0.,info['end']-origin-self.settings.context_seconds))
            if limit<=offset+1e-5:
                if strict:self._strict_wait_pending=(info['end'],info['final'],offset)
                else:wait_for_input(self.store,chunk['id'],info['end'],info['final'])
                return
            end=min(limit,offset+self.settings.window_seconds)
            extract_start=max(info['start']-origin if info else 0.,offset-self.settings.context_seconds)
            extract_end=min(info['end']-origin if info else chunk['duration'],end+self.settings.context_seconds)
            native=None
            if self.settings.diarization_backend=='nemotron':
                if self.native is None:
                    if ((getattr(self.settings,'native_owned_support_enabled',False) and self.native_owned_support_cache is not None)
                            or self.native_owned_cells is not None):
                        self.native=NativeCoordinator(self.settings,self._native_extract,process_factory=self._native_process_factory)
                    else:self.native=NativeCoordinator(self.settings,self.extract_pcm)
                if self._native_capture is not None:
                    self._native_capture_observation=(self._native_capture_token,self._native_capture)
                self._cell_call('begin_prepare',chunk,info)
                if strict:self.native_owned_cells.require_strict_ready(beginning=True)
                try:native=self.native.prepare(chunk,offset,end,info,lambda:self.interrupted(chunk['id']))
                finally:
                    self._native_capture_observation=None
                    self._cell_call('end_prepare')
                if strict:self.native_owned_cells.require_strict_ready()
                if native is None:
                    if not info:raise RuntimeError('Finalized native source did not produce a stable core')
                    if strict:
                        self.native_owned_cells.require_strict_wait(self.native)
                        self._strict_wait_pending=(info['end'],info['final'],offset)
                    else:wait_for_input(self.store,chunk['id'],info['end'],info['final'])
                    return
                end=native['end'];extract_start=native['extract_start'];extract_end=native['extract_end']
            with tempfile.TemporaryDirectory(prefix='window-',dir=self.settings.data_dir/'tmp') as tmp:
                tmp=Path(tmp); wav=tmp/'audio.wav'
                from .verified_identity import is_verified
                with self.store.db() as db:verified_query=is_verified(db,chunk['session_id'])
                capture_support=self.settings.speaker_support_enabled and (self.settings.speaker_support_max_records>0 or verified_query)
                expected_generation=speaker_support.capture_generation(self.store,chunk) if capture_support else None
                retain_lexical=getattr(self.settings,'lexical_evidence_enabled',False)
                lineage=self.extract_pcm(chunk,extract_start+origin,extract_end+origin,wav,tmp,lambda:self.interrupted(chunk['id']),capture_support=capture_support,**({'capture_lexical':True} if retain_lexical else {}))
                self._observe_native_pcm('analyzer',wav,extract_start+origin,extract_end+origin,
                    analyzer_local=(origin,extract_start,extract_end) if native is not None else None)
                options=({'candidate_core_start':offset-extract_start,'candidate_core_end':end-extract_start,
                          'candidate_core_apply_safe_cut':end<chunk['duration']-1e-5,
                          'candidate_core_cut_lower':max(offset-extract_start+.5,end-extract_start-min(3.,self.settings.window_seconds*.25))}
                         if self.settings.overlap_backend!='none' else None)
                if self.analyzer_factory:
                    # Test-only dependency injection. Production uses monitored subprocesses.
                    if not strict:analyzer=self.analyzer_factory(self.settings.inference_config())
                    arguments={}
                    if native:arguments['diarization']=native
                    if options is not None:arguments['analysis_options']=options
                    if strict:result=self._strict_test_analyze(wav,arguments)
                    else:
                        try:result=analyzer.analyze(str(wav),**arguments)
                        finally:analyzer.close()
                else:
                    arguments={}
                    if native:
                        diarization_path=tmp/'diarization.json'
                        diarization_path.write_text(json.dumps({'turns':native['turns'],'provenance':native['provenance']}))
                        arguments['diarization_path']=diarization_path
                    if options is not None:arguments['analysis_options']=options
                    result=process.analyze(wav,tmp/'result.json',lambda:self.interrupted(chunk['id']),**arguments)
                from .recording_primary_provenance import enabled as primary_provenance_enabled, observe, validate_observation
                if strict:
                    result.pop('_strict_capture', None)
                    self.native_owned_cells.require_strict_ready()
                primary_snapshot = observe(result, native) if (strict or automatic or primary_recording) and primary_provenance_enabled(self.settings) else None
                result=validate_result(result,_validation_duration(strict, self.native_owned_cells,
                    chunk, native, extract_start, extract_end))
                if primary_snapshot is not None:validate_observation(result, primary_snapshot)
                primary_snapshot = None
                if native:
                    result.setdefault('metadata',{}).update(native['provenance'])
                    result['metadata']['native_diarization']=native['state']
                    if result['metadata'].get('silence_fast_path'):
                        result['metadata']['model_execution']='native_diarization_only_asr_skipped'
                        result['metadata']['models_executed']=['Nemotron-3']
                        result['metadata']['model_execution_note']='Stateful native diarization ran; silence bypassed ASR, embeddings and event model.'
                nominal_end=end
                if end<chunk['duration']-1e-5:
                    local=safe_cut(result.get('metadata',{}).get('diarization_turns',[]),
                                   end-extract_start,
                                   max(offset-extract_start+.5,end-extract_start-min(3.,self.settings.window_seconds*.25)),
                                   extract_end-extract_start)
                    end=extract_start+local
                    if native and self.native_owned_cells is not None:
                        args=(chunk,native,offset,nominal_end,result,self.settings.window_seconds,end)
                        if strict or automatic:end=self.native_owned_cells.native_core_cut(*args)
                        else:
                            # The legacy optional hook still cannot suppress a
                            # primary result when its witness is unavailable.
                            selected=self._cell_call('native_core_cut',*args)
                            if selected is not None:end=selected
                try:
                    prepared=(speaker_support.prepare_window(result,wav,lineage,expected_generation,
                        core_start=offset-extract_start,analyzer_start=extract_start,settings=self.settings) if capture_support else {})
                except (ValueError,TypeError,OSError,RuntimeError,ImportError) as exc:
                    if strict and isinstance(exc, OSError):raise
                    prepared={}
                result=own_core(result,offset-extract_start,end-extract_start,previous,extract_start,**({'retain_lexical':True} if retain_lexical else {}))
                if retain_lexical:result['_lexical_sources']=lineage.get('_lexical_sources') if lineage else None
                result['_speaker_support']=prepared
                result.setdefault('metadata',{})['core_cut']={'nominal':nominal_end,'actual':end,'reason':'neural_speech_gap' if end<nominal_end else 'fixed_or_final'}
                previous=result['metadata'].get('boundary_tail',[])
                primary_jobs=getattr(self.moss_automatic,'jobs',None)
                with window_observation(getattr(self,'trial_observer',None),chunk,offset,end,self.native_owned_cells):
                    try:
                        if strict:
                            self.native_owned_cells.start_bounded(result)
                            selected_cells=self.native_owned_cells.prepare(chunk,native,offset,end,result,required=True)
                        elif automatic:
                            if self.native_owned_cells is None:raise RuntimeError('automatic_capture_unavailable')
                            if getattr(self.settings,'native_owned_bounded_core_support_enabled',False):
                                self.native_owned_cells.start_bounded(result)
                            selected_cells=self.native_owned_cells.prepare(chunk,native,offset,end,result,required=True)
                            result['_native_automatic_cells']=selected_cells
                            if getattr(self.settings,'native_owned_bounded_core_support_enabled',False):
                                # The result packet is the sole handoff owner; bounded
                                # preparation releases ordinary PCM after freezing origins.
                                selected_cells=()
                        else:
                            if primary_recording:self._cell_call('start_bounded',result)
                            selected_cells=(self.native_owned_cells.prepare(chunk,native,offset,end,result)
                                if primary_recording and self.native_owned_cells is not None
                                else self._cell_call('prepare',chunk,native,offset,end,result))
                        observe_active('PREPARE_RESULT', {'component':'worker_prepare', 'outcome':'returned',
                                       'reason':'no_capture' if self.native_owned_cells is None else 'none'})
                        if strict:
                            self.native_owned_cells.prepare_core_seam(chunk,native,offset,end,result,self.native,
                                lambda:self.interrupted(chunk['id']),required=True)
                            from .strict_recording import require_prepared
                            with result['_native_bounded_support']['budget'].phase():
                                require_prepared(chunk,offset,end,result)
                        elif automatic:
                            self.native_owned_cells.prepare_core_seam(chunk,native,offset,end,result,self.native,
                                lambda:self.interrupted(chunk['id']),required=True)
                            # Disk headroom includes the same primary DB/WAL and existing
                            # write/scratch allowance immediately before enqueue.
                            self.moss_automatic.results._headroom()
                        elif primary_recording and self.native_owned_cells is not None:
                            self.native_owned_cells.prepare_core_seam(chunk,native,offset,end,result,self.native,
                                lambda:self.interrupted(chunk['id']))
                        else:self._cell_call('prepare_core_seam',chunk,native,offset,end,result,self.native,lambda:self.interrupted(chunk['id']))
                        if self.interrupted(chunk['id']): raise InterruptedError()
                        if observe_commit(lambda:self.store.commit_window(chunk,offset,end,result)) is not True:
                            if strict:raise RuntimeError('Strict primary commit not acknowledged')
                            if isinstance(process,ResidentAnalyzer):process.retire()
                            return
                        # A positively returned primary acknowledgement alone transfers ownership.
                        if primary_jobs is not None:
                            try:observe_callback(lambda:primary_jobs.primary_committed(result.get('_primary_probe_handle')))
                            except Exception:pass
                        else:observe_active('CALLBACK_RESULT', {'outcome':'absent', 'reason':'no_jobs'})
                        self._publish_native_support(native,offset,end)
                        if not automatic and not primary_only:
                            self._cell_call('committed',selected_cells)
                            self._cell_call('drain_core_seams',result.get('_native_core_seam_status'))
                    finally:
                        # This also aborts after seam/headroom/cancel/unknown-ack failures.
                        handle=result.pop('_primary_probe_handle',None)
                        if primary_jobs is not None:
                            try:primary_jobs.abort_primary_copy(handle)
                            except Exception:pass
            offset=end
            if self._fair_core_handoff and self.moss_automatic is not None and self.moss_automatic.primary_yield_due():
                permit=getattr(self.settings, '_native_execution_permit', None)
                if permit is not None and permit.request_automatic_turn():return True if offset < chunk['duration'] - 1e-5 else None
