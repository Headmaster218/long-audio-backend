"""Model-free telemetry and cache-integrity contracts for the v2 continuation."""
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
import os
import pytest
from scripts.benchmarks import events_residual_resume as m


def write_owner(tmp_path,*,wall=True,partial=False,age=0):
    current=1000.
    row=dict(rss_bytes=100,host_available_memory_bytes=3*m.frozen.GIB,
             disk_free_bytes=6*m.frozen.GIB,processes=[],wall_seconds=40.)
    metrics=tmp_path/'metrics.jsonl';metrics.write_text(json.dumps(row)+'\n'+('{"rss_bytes":999'if partial else''))
    os.utime(metrics,(current-age,current-age))
    progress=dict(utc=dt.datetime.fromtimestamp(current-age,dt.timezone.utc).isoformat())
    if wall:progress['wall_seconds_this_invocation']=40.
    (tmp_path/'progress.json').write_text(json.dumps(progress))
    return row


class Clock:
    def __init__(self):self.t=0
    def mono(self):return self.t
    def sleep(self,n):self.t+=n


def test_legal_owner_first_phase_does_not_require_optional_wall(tmp_path):
    expected=write_owner(tmp_path,wall=False)
    row,progress,age,identity=m.owner_snapshot(tmp_path,now=lambda:1000.,retry_seconds=0)
    assert row==expected and age==0
    assert 'wall_seconds_this_invocation'not in progress


def test_partial_tail_never_reuses_old_complete_row(tmp_path):
    write_owner(tmp_path,partial=True)
    clock=Clock()
    with pytest.raises(RuntimeError,match='incomplete'):
        m.owner_snapshot(tmp_path,now=lambda:1000.,monotonic=clock.mono,sleep=clock.sleep)
    assert clock.t==pytest.approx(1.)


@pytest.mark.parametrize('age',[11,-1])
def test_stale_or_future_owner_snapshot_fails_closed(tmp_path,age):
    write_owner(tmp_path,age=age)
    with pytest.raises(RuntimeError,match='stale or future'):
        m.owner_snapshot(tmp_path,now=lambda:1000.,retry_seconds=0)


def test_missing_progress_time_is_not_silently_accepted(tmp_path):
    write_owner(tmp_path);(tmp_path/'progress.json').write_text('{}')
    with pytest.raises(RuntimeError,match='bounded reread'):
        m.owner_snapshot(tmp_path,now=lambda:1000.,retry_seconds=0)


def test_append_race_retries_and_reads_only_new_complete_tail(tmp_path,monkeypatch):
    write_owner(tmp_path,partial=True);clock=Clock()
    def sleep(seconds):
        clock.sleep(seconds);write_owner(tmp_path,wall=False)
    row,_,_,_=m.owner_snapshot(tmp_path,now=lambda:1000.,monotonic=clock.mono,sleep=sleep)
    assert row['rss_bytes']==100 and clock.t==pytest.approx(.05)


def test_metrics_mutation_during_read_is_rejected(tmp_path,monkeypatch):
    write_owner(tmp_path);original=m.tail_read
    def change(path):
        data=original(path)
        with path.open('ab')as f:f.write(b' ')  # changed size and incomplete tail
        return data
    monkeypatch.setattr(m,'tail_read',change)
    with pytest.raises(RuntimeError,match='changed during read'):m.metrics_snapshot(tmp_path/'metrics.jsonl')


def test_scope_rejects_completed_replay_and_missing_or_reordered_rows():
    rows=[{'id':str(x)}for x in range(5)]
    assert m.resume_scope(rows,{'0','1'},['2','3','4'])==['2','3','4']
    for request in [['1','2','3','4'],['2','3'],['3','2','4']]:
        with pytest.raises(RuntimeError):m.resume_scope(rows,{'0','1'},request)


def test_frozen_scientific_functions_are_unmodified():
    assert m.frozen.digest(m.frozen.SELF)==m.SCIENTIFIC_SHA
    assert m.Guard.run is m.frozen.Guard.run
    assert m.EXTRA_BUDGET_SECONDS==600


def test_cumulative_time_is_not_reset_in_summary(monkeypatch):
    guard=object.__new__(m.Guard);guard.started=10.;guard.prior_active_seconds=1227.5
    guard.peak_own=1;guard.peak_aggregate=2;guard.min_available=3;guard.min_free=4
    monkeypatch.setattr(m.time,'monotonic',lambda:30.)
    summary=guard.summary()
    assert summary['active_seconds']==20.
    assert summary['v2_cumulative_active_seconds']==1247.5


def test_duplicate_completed_case_cannot_be_replaced(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'ROOT',tmp_path)
    case={'id':'one'};path=tmp_path/'case.json';path.write_text(json.dumps(case))
    plan={'completed_cases':[{'id':'one','path':'case.json','sha256':m.frozen.digest(path)}]}
    with pytest.raises(RuntimeError,match='replace completed'):
        m.combined_cases(plan,{'one':case})


def test_bound_evidence_mutation_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'ROOT',tmp_path);path=tmp_path/'cache.json';path.write_text('{}')
    expected=m.frozen.digest(path);path.write_text('{"changed":true}')
    with pytest.raises(RuntimeError,match='Bound evidence changed'):
        m.verify_bound_files({'bound_files':{'cache.json':expected}})
