"""Focused model-free target-context and authenticated failure closure contracts."""
from copy import deepcopy
import json
import threading

import pytest

from audio_backend import native_owned_core_seams as core, moss_automatic as automatic
from audio_backend import native_owned_cell_capture as capture
from audio_backend.native_owned_cells import RATE, Unavailable
from audio_backend.native_owned_cell_store import Budget
from test_bounded_core_support import synthetic_case, lexical_row, primary_row
from test_bounded_support_intents import (support_local_ledger_factory,
    _guarded_eof_handoff, _reference_snapshot, _insert_local_parent,
    bounded_app_factory, advance_to_waiter, intent_rows, c_target)
from test_moss_automatic_integration import automatic_app_factory, process_primary


def dense_case(count=130, crossing=False):
    case=synthetic_case([0, int(10.5*RATE), 21*RATE, int(31.5*RATE), 42*RATE],
        [(36*RATE,37*RATE)], origin_index=3)
    start=(16 if crossing else 1)*RATE
    units=[{'start':(start+i*1600)/RATE,'end':(start+i*1600+800)/RATE,'text':'词'} for i in range(count)]
    rows=[lexical_row(case,1,units),primary_row(2,30*RATE,33*RATE),
          primary_row(3,35*RATE,38*RATE)]
    from audio_backend.lexical_evidence import current_inventory
    from audio_backend.transcript_contract import primary_evidence
    from audio_backend.transcript_projection import lexical_groups
    sid=case['commits'][0]['scope']['session_id']
    inventory=current_inventory(None,sid,rows[0],source_lookup=case['sources'].get)
    assert inventory is not None
    assert len(lexical_groups(primary_evidence(sid,rows[0]),inventory))==count
    return case,rows


def scoped(case,rows):
    band=core.target_context(case['target'],case['commits'])
    return core.context_snapshot(case['commits'][0]['scope']['session_id'],rows,
        case['sources'],case['origin'],band,complete_frames=core.bounded_chain(case['commits']))


def test_unrelated_atoms_over_cap_do_not_block_complete_context_fixed_point():
    case,rows=dense_case()
    with pytest.raises(Unavailable,match='^lexical_atom_capacity$'):
        core.lexical_snapshot(case['commits'][0]['scope']['session_id'],rows,case['sources'],0.,
            scan_frames=[0,42*RATE],schema=core.COMPACT_INVENTORY_SCHEMA)
    snapshot=scoped(case,rows)
    assert snapshot['scan_frames']==[17*RATE,42*RATE]
    view=core.inventory_view(snapshot)
    assert [p['row']['id'] for p in view['parents']]==[2,3] and len(view['atoms'])==2
    support=core.select_support(case['target'],case['commits'],snapshot,17*RATE,42*RATE,preparation=True)
    assert support=={'start_frame':35*RATE,'end_frame':38*RATE}
    original=json.loads(case['target']['origins'][0]['proof'])['support']
    from audio_backend.transcript_projection import _legal
    assert not _legal(original['start_frame'],original['end_frame'],view['atoms'])
    assert [support['start_frame'],support['end_frame']]!=[original['start_frame'],original['end_frame']]
    local=core.localize_inventory(case['target'],case['commits'],snapshot,17*RATE,42*RATE,
        support,preparation=True)
    assert local['schema']==core.SUPPORT_LOCAL_INVENTORY_SCHEMA
    assert core.select_support(case['target'],case['commits'],local,17*RATE,42*RATE)==support
    assert core.MAX_PLAN_ATOMS==128


def test_intersecting_whole_parent_still_exceeds_cap_without_clipping():
    case,rows=dense_case(crossing=True)
    with pytest.raises(Unavailable,match='^lexical_atom_capacity$'):scoped(case,rows)


def test_preparation_context_equals_full_selection_and_cannot_be_persisted_as_broad():
    case,rows=dense_case(5)
    full=core.lexical_snapshot(case['commits'][0]['scope']['session_id'],rows,case['sources'],0.,
        scan_frames=[0,42*RATE],schema=core.COMPACT_INVENTORY_SCHEMA)
    snapshot=scoped(case,rows)
    expected=core.select_support(case['target'],case['commits'],full,0,42*RATE)
    assert core.select_support(case['target'],case['commits'],snapshot,0,42*RATE,preparation=True)==expected
    with pytest.raises(Unavailable,match='^bounded_plan_scope$'):
        core.select_support(case['target'],case['commits'],snapshot,0,42*RATE)
    with pytest.raises(Unavailable,match='^incomplete_primary$'):
        core.context_snapshot(case['commits'][0]['scope']['session_id'],rows,case['sources'],0.,
            [17*RATE,42*RATE],complete_frames=[18*RATE,42*RATE])
    incomplete=core.context_snapshot(case['commits'][0]['scope']['session_id'],rows,case['sources'],0.,
        [18*RATE,42*RATE],complete_frames=[0,42*RATE])
    with pytest.raises(Unavailable,match='^bounded_plan_scope$'):
        core.select_support(case['target'],case['commits'],incomplete,0,42*RATE,preparation=True)
    assert core.validate_inventory(full)==full


@pytest.mark.parametrize('change',['insert','mutate','retention'])
def test_transaction_scoped_revalidation_rejects_intersecting_changes(support_local_ledger_factory,change):
    f=support_local_ledger_factory(waiting=True);before=_reference_snapshot(f)
    budget=Budget.for_bounded(1.0)
    handoff={'waiters':[f.row],'batch':f.context,'reserved':True,'budget':budget,
        'origins':[],'plans':[{**f.final,'snapshot':f.snapshot,'preparation':True}],
        'context':{'origin':f.snapshot['origin'],'scan_frames':f.snapshot['scan_frames']},'draft_ids':[]}
    with f.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if change=='insert':_insert_local_parent(db,primary_row(99,18,20,cid='source-1'))
        elif change=='mutate':db.execute("UPDATE segments SET text='changed',raw_text='changed' WHERE id=1")
        else:handoff['draft_ids']=[123]
        notice=automatic.enqueue_bounded(db,[],{'commit':f.case['commits'][-1]},handoff,
            {'session_id':f.row['sid']},[], 'refused' if change=='retention' else None)
        assert notice['reason']==('bounded_inventory_not_retained' if change=='retention' else 'bounded_inventory_changed')
        assert dict(db.execute(f'SELECT * FROM {automatic.TABLE} WHERE seq=?',(f.row['seq'],)).fetchone())==f.row
        db.rollback()
    assert _reference_snapshot(f)==before


def failure_handoff(f,budget):
    h=_guarded_eof_handoff(f,budget)
    # This path never binds or creates PCM: the authentic existing query is
    # retained only to authenticate the final frontier and original owner.
    h['origins']=[]
    h['frontier']={k:f.final[k] for k in ('commits','query','evidence','support')}
    h['plans']=[{'target':f.case['target'],'reason':'lexical_atom_capacity'}]
    h['reason']='lexical_atom_capacity'
    return h


@pytest.mark.parametrize('state',['authentic','absent_frontier','forged_native','changed_row','cancelled'])
def test_preparation_failure_eof_authentication_and_exact_owner(support_local_ledger_factory,monkeypatch,state):
    f=support_local_ledger_factory(waiting=True);budget=Budget.for_bounded(1.0)
    h=failure_handoff(f,budget)
    if state=='absent_frontier':h.pop('frontier')
    elif state=='forged_native':
        h['frontier']=deepcopy(h['frontier']);h['frontier']['query']['native']['state']['finished']=False
    read=automatic._read_v2
    if state=='changed_row':
        def changed(*args,**kwargs):
            row=read(*args,**kwargs)
            return dict(row,updated=row['updated']+1) if kwargs.get('expected') is not None and row else row
        monkeypatch.setattr(automatic,'_read_v2',changed)
    with f.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if state=='cancelled':automatic._terminal(db,f.row,'cancelled','cancelled',budget=budget)
        before=[tuple(r) for r in db.execute('SELECT * FROM segments ORDER BY id')]
        notice=automatic.enqueue_bounded(db,[],{'commit':f.case['commits'][-1]},h,
            {'session_id':f.row['sid']},[],None)
        row=dict(db.execute(f'SELECT * FROM {automatic.TABLE} WHERE seq=?',(f.row['seq'],)).fetchone())
        assert [tuple(r) for r in db.execute('SELECT * FROM segments ORDER BY id')]==before
        assert notice['reason']=='lexical_atom_capacity'
        if state=='authentic':
            assert row['state']=='no_result' and row['reason']=='lexical_atom_capacity'
            assert notice['status']=='no_result' and notice['eof_finalization']=='closed'
            assert row['plan_revision']==f.row['plan_revision']+1 and row['job_id'] is None
            again=automatic.enqueue_bounded(db,[],{'commit':f.case['commits'][-1]},h,
                {'session_id':f.row['sid']},[],None)
            assert dict(db.execute(f'SELECT * FROM {automatic.TABLE} WHERE seq=?',(f.row['seq'],)).fetchone())==row
            assert again['bound']==0
        elif state=='cancelled':assert row['state']=='cancelled'
        else:assert row==f.row


def test_empty_preparation_plans_keep_specific_notice(support_local_ledger_factory):
    f=support_local_ledger_factory(waiting=True);h=failure_handoff(f,Budget.for_bounded(1.0))
    h['plans']=[];h['true_eof']=False
    with f.store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        notice=automatic.enqueue_bounded(db,[],{'commit':f.case['commits'][-1]},h,
            {'session_id':f.row['sid']},[],None)
        assert notice['reason']=='lexical_atom_capacity' and notice['bound']==0
        assert dict(db.execute(f'SELECT * FROM {automatic.TABLE} WHERE seq=?',(f.row['seq'],)).fetchone())==f.row


def test_real_primary_preparation_capacity_closes_at_authenticated_eof(bounded_app_factory,monkeypatch):
    with bounded_app_factory(support_seconds=1.0) as h:
        chunks,waiting=advance_to_waiter(h)
        calls=[]
        def capacity(*args,**kwargs):
            calls.append(1);raise Unavailable('lexical_atom_capacity')
        monkeypatch.setattr(core,'context_snapshot',capacity)
        assert process_primary(h)==chunks[-1]['id']
        closed=c_target(h);last=h.store.chunk(chunks[-1]['id'])
        assert calls and closed['state']=='no_result' and closed['reason']=='lexical_atom_capacity'
        assert closed['job_id'] is None and closed['plan_revision']==waiting['plan_revision']+1
        assert last['status']=='completed' and last['next_offset']==last['duration']
        notice=last['metadata']['bounded_core_support']
        assert notice['reason']=='lexical_atom_capacity' and notice['eof_finalization']=='closed'


def test_crossing_parent_keeps_outside_atoms_and_raw_parent_cap():
    case,rows=dense_case(20,crossing=True)
    snapshot=scoped(case,rows);view=core.inventory_view(snapshot)
    parent=next(p for p in view['parents'] if p['row']['id']==1)
    assert len(parent['atoms'])==20
    assert parent['atoms'][0]['start_frame']<snapshot['scan_frames'][0]
    with pytest.raises(Unavailable,match='^bounded_primary_capacity$'):
        scoped(case,[*rows,*[dict(rows[-1],id=i) for i in range(4,131)]])


@pytest.mark.parametrize('schema',[core.INVENTORY_SCHEMA,core.COMPACT_INVENTORY_SCHEMA])
def test_legacy_narrowed_snapshot_still_rejected(schema):
    case,rows=dense_case(5);band=core.target_context(case['target'],case['commits'])
    snapshot=core.lexical_snapshot(case['commits'][0]['scope']['session_id'],rows[1:],
        case['sources'],0.,scan_frames=band,schema=schema)
    with pytest.raises(Unavailable,match='^bounded_plan_scope$'):
        core.select_support(case['target'],case['commits'],snapshot,0,42*RATE)


def test_multiple_contexts_share_original_cumulative_budget(bounded_app_factory,monkeypatch):
    with bounded_app_factory(support_seconds=1.0) as h:
        chunks,waiting=advance_to_waiter(h)
        prepare=capture.Capture.bounded_prepare;expand=core.context_snapshot
        active=[];seen=[];init=Budget.__init__;thread=[None]
        def no_reset(budget):
            assert not active or threading.get_ident()!=thread[0], 'a second Budget was constructed inside target preparation'
            init(budget)
        def prepare_once(self,*args,**kwargs):
            budget=args[-1];active.append(budget);thread[0]=threading.get_ident()
            try:return prepare(self,*args,**kwargs)
            finally:active.pop()
        def charge(*args,**kwargs):
            assert len(active)==1;budget=active[0]
            seen.append((budget,budget.sql_bytes))
            if len(seen)==1:
                # Declared consumed SQL at the unchanged planning ceiling.
                # A second target must inherit it; this is not a cap increase.
                budget.sql_bytes=128*1024
            else:
                assert budget is seen[0][0] and budget.sql_bytes>=128*1024
                budget.projection(1)
            return expand(*args,**kwargs)
        monkeypatch.setattr(Budget,'__init__',no_reset)
        monkeypatch.setattr(capture.Capture,'bounded_prepare',prepare_once)
        monkeypatch.setattr(core,'context_snapshot',charge)
        assert process_primary(h)==chunks[-1]['id']
        assert len(seen)>=2 and all(budget is seen[0][0] for budget,_ in seen)
        assert seen[0][0].sql_bytes>=128*1024 and seen[0][0].seconds==1.0
        assert not any(p['has_pcm'] for p in h.planning[-1]['plans'])
        last=h.store.chunk(chunks[-1]['id'])
        assert last['status']=='completed' and last['next_offset']==last['duration']
