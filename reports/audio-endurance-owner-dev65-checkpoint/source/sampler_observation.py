# PUBLIC INSPECTION COPY. Do not execute or import.
"""One narrow known-child terminal-only recovery plus durable bounded evidence.

No deadline extension, no role inference, and no live missing-metadata admission.
The inherited terminal grace remains the sole policy implementation.
"""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import copy,json,time,traceback


def observation_family(base, sink):
    class Observed(base):
        def _diagnostic(self,stage,**values):
            count=getattr(self,'diagnostic_count',0)
            if count>=32768:raise RuntimeError('Sampler diagnostic event cap')
            pending=getattr(self,'incomplete',None)
            now=time.monotonic()
            row=dict(kind='sampler-diagnostic',stage=stage,at=now,
                pending=copy.deepcopy(pending),
                pending_age=None if pending is None else max(0,now-pending['at']),
                last_complete=getattr(self,'last_complete',None),
                last_charge=getattr(self,'last_charge',0),
                pending_charge=getattr(self,'pending_charge',0),
                read_context=copy.deepcopy(getattr(self,'read_context',{})),**values)
            raw=json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
            if len(raw)>16384:raise RuntimeError('Sampler diagnostic record cap')
            sink(json.loads(raw));self.diagnostic_count=count+1

        def authenticate(self,q,argv=None,expected=None,allow_pending=False):
            try:return super().authenticate(q,argv,expected,allow_pending)
            except self.PendingMetadata as pending:
                key=(pending.pid,pending.ticks);previous=self.authenticated.get(key)
                self._diagnostic('authentication-pending',pid=pending.pid,birth=pending.ticks,
                    prior_authenticated=previous is not None,native_reads=pending.fields.get('native_reads'),
                    triggering_kernel=pending.fields.get('kernel'))
                # Do not consume or bypass the inherited known-exit recovery.
                # Unknown, shutdown, second-recheck and already-pending cases
                # retain exactly the inherited refusal/pending behavior.
                eligible=(allow_pending and previous is not None and not self.shutting_down
                    and not self.sample_recheck_used and self.incomplete is None
                    and previous.get('kind') in ('analyzer','native_diarizer','primary_speaker_probe','comparator','decoder','decoder_guard')
                    and type(previous.get('argv')) is list and bool(previous['argv'])
                    and type(previous.get('exe')) is str and bool(previous['exe'])
                    and all(k in previous for k in ('parent','uid','kernel')))
                if not eligible:raise
                self.sample_recheck_used=True
                record=dict(pid=q.pid,start_ticks=pending.ticks,stage='selected_known_metadata',
                    trigger='known selected child metadata incomplete',at=time.monotonic(),outcome='attempted')
                self.live_rechecks.append(record)
                try:
                    result=self._terminal_metadata_grace(q,previous,record,pending)
                    if result is not None:raise RuntimeError('Terminal-only grace returned live admission')
                    return None
                except BaseException as exc:
                    record.update(outcome='failed_closed',exception_type=type(exc).__name__,reason=str(exc))
                    raise
                finally:
                    grace=record.get('terminal_grace',{})
                    self._diagnostic('known-terminal-grace',pid=q.pid,birth=pending.ticks,
                        outcome=record.get('outcome'),exception_type=record.get('exception_type'),reason=record.get('reason'),maximum_reads=grace.get('maximum_reads'),
                        maximum_seconds=grace.get('maximum_seconds'),deadline=grace.get('deadline'),
                        retained_charge=grace.get('retained_charge'),reads=copy.deepcopy(grace.get('reads',[])))

        def _mark_incomplete(self,exc,now,partial_charge):
            self._diagnostic('incomplete-enter',pid=exc.pid,birth=getattr(exc,'ticks',None),
                exception_type=type(exc).__name__,partial_charge=partial_charge)
            result=super()._mark_incomplete(exc,now,partial_charge)
            self._diagnostic('incomplete-retained',pid=exc.pid,birth=getattr(exc,'ticks',None))
            return result

        def _finish_sample(self,now,rss,comparison_rss,cpu):
            previous=copy.deepcopy(self.incomplete)
            result=super()._finish_sample(now,rss,comparison_rss,cpu)
            if previous is not None:
                self._diagnostic('incomplete-resolved',previous_pending=previous,
                    rss=rss,comparison_rss=comparison_rss,charged=self.last_charge)
            return result

        def child_diagnostic(self,q,fields,stage,error):
            try:return super().child_diagnostic(q,fields,stage,error)
            finally:
                self._diagnostic('child-refusal',pid=q.pid,refusal_stage=stage,
                    exception_type=type(error).__name__,reason=str(error),
                    triggering_kernel=fields.get('kernel'),native_reads=fields.get('native_reads'),
                    refusal=copy.deepcopy(self.child_refusals[-1]) if self.child_refusals else None)

        def sample(self):
            try:return super().sample()
            except BaseException as exc:
                frames=traceback.extract_tb(exc.__traceback__)[-8:]
                self._diagnostic('sample-failed',exception_type=type(exc).__name__,reason=str(exc),
                    failure_sites=[dict(file=f.filename.rsplit('/',1)[-1],line=f.lineno,function=f.name) for f in frames])
                raise
    return Observed
