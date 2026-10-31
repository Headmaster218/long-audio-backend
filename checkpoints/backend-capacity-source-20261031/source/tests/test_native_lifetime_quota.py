"""Finite historical metadata, no model/actor/runtime-qualification claims."""
import sqlite3
from types import SimpleNamespace
import pytest
from audio_backend import native_owned_cell_store as cells
from audio_backend import native_owned_core_seams as core
from audio_backend import strict_lifetime as life
from audio_backend.native_owned_cells import Unavailable


def database(count,limit=32768):
 db=sqlite3.connect(':memory:')
 db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,deleted INTEGER)')
 db.execute('CREATE TABLE chunks(id TEXT PRIMARY KEY,status TEXT)')
 core.init_schema(db)
 for sql in life.schema_statements(limit):db.execute(sql)
 life.initialize(db,limit)
 db.executemany("INSERT INTO native_core_commits VALUES(?, 's', 'c', 'scope', 1, X'00', NULL, 'none', 'none')",((f'{i:064x}',) for i in range(count)))
 db.commit();return db


@pytest.mark.parametrize('count,limit',[(2056,2056),(16464,32768),(65832,131072),(131072,131072)])
def test_retained_count_has_bounded_vm_steps(count,limit):
 db=database(count,limit);steps=[0]
 db.set_progress_handler(lambda:steps.__setitem__(0,steps[0]+1) or 0,1)
 budget=cells.Budget()
 with budget.phase():answer=core.retention_counts(db,budget)
 assert answer=={'occupied':count,'pending':0,'pending_bytes':0}
 assert steps[0]<200,steps
 db.close()


@pytest.mark.parametrize('states',[
 [('none',None)], [('pending',None)], [('none',b'x')],
 [('pending',b'x'),('none',b'yz'),('pending',None)],
 [('pending',None)]*4,[('none',b'x')]*4,
])
def test_pending_or_semantics_match_old_aggregate(states):
 db=database(len(states))
 for i,(state,body) in enumerate(states):db.execute('UPDATE native_core_commits SET state=?,pending=? WHERE slot=?',(state,body,f'{i:064x}'))
 expected=db.execute("SELECT COUNT(*),SUM(pending IS NOT NULL OR state='pending'),COALESCE(SUM(length(pending)),0) FROM native_core_commits").fetchone()
 budget=cells.Budget()
 with budget.phase():answer=core.retention_counts(db,budget)
 assert tuple(answer.values())==expected;db.close()


def test_pending_fifth_row_and_default_lifetime_refuse():
 db=database(2057,2056)
 with pytest.raises(Unavailable,match='retention_compact_capacity'):core.retention_counts(db,cells.Budget())
 db.close();db=database(5)
 db.execute("UPDATE native_core_commits SET state='pending'")
 with pytest.raises(Unavailable,match='retention_compact_capacity'):core.retention_counts(db,cells.Budget())
 db.close()


def test_native_quota_is_frozen_and_physical_envelope_unchanged(tmp_path):
 settings=SimpleNamespace(strict_primary_enabled=True)
 primary=SimpleNamespace(settings=settings,_strict_receipt_records=8192,_strict_lifetime_records=32768)
 owner=cells.CellStore(tmp_path,SimpleNamespace(store=primary),enabled=False)
 primary._strict_receipt_records=20000;primary._strict_lifetime_records=131072
 assert owner._receipt_limit==8192 and owner._compact_limit==32768
 assert owner.health()['receipt_gc']['source_receipt_limit']==8192
 assert cells.MAX_PAGE_COUNT*cells.PAGE_SIZE==cells.DATABASE_BYTES==24*1024**2
 assert cells.JOURNAL_BYTES==32*1024**2 and cells.WORKING_RESERVATION_BYTES==64*1024**2
 assert owner._thread is None and owner._db is None


def test_sources_reference_count_matches_old_expression():
 db=sqlite3.connect(':memory:');cells.CellStore._schema(db)
 for i in range(6):db.execute('INSERT INTO sources VALUES(?,?,?,?)',(str(i),'s','d',b'x'))
 # FK enforcement intentionally off: also verify orphan dependency doesn't count.
 for i,cid in enumerate(['0','0','2','5','missing']):db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)',('x',i,cid,'d','r',0,1,0,1))
 old=db.execute('SELECT COUNT(*),SUM(EXISTS(SELECT 1 FROM dependencies d WHERE d.cid=sources.cid)) FROM sources').fetchone()
 new=db.execute('SELECT (SELECT COUNT(*) FROM sources),(SELECT COUNT(DISTINCT d.cid) FROM dependencies d JOIN sources s ON s.cid=d.cid)').fetchone()
 assert old==new==(6,3);db.close()
