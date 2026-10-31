"""VM-step proof that completed fragment history does not dominate admission."""
from audio_backend.settings import Settings
from audio_backend.storage import Store,STREAM_PENDING_COUNT_SQL

def test_pending_count_status_first_10k_vs_100k(tmp_path):
    results=[]
    for count in (10000,100000):
        store=Store(tmp_path/f'{count}.sqlite',Settings(data_dir=tmp_path,start_worker=False));sid=store.create_session('scale')['id']
        with store.db() as db:
            rows=[(f'{i:032x}',sid,str(i),str(i),'/not-real.audio',1.,float(i),'completed' if i<count else ('waiting_input','queued','failed')[i-count],0.) for i in range(count+3)]
            db.executemany('INSERT INTO chunks(id,session_id,idempotency_key,digest,path,duration,timeline_start,status,created) VALUES(?,?,?,?,?,?,?,?,?)',rows)
            db.executemany('INSERT INTO stream_chunks(chunk_id,session_id,stream_id,position) VALUES(?,?,?,?)',((f'{i:032x}',sid,'s',i) for i in range(count+3)))
            steps=[0]
            def tick():steps[0]+=1;return 0
            db.set_progress_handler(tick,1)
            actual=db.execute(STREAM_PENDING_COUNT_SQL,(sid,'s')).fetchone()[0]
            db.set_progress_handler(None,0)
            plan=' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN '+STREAM_PENDING_COUNT_SQL,(sid,'s')))
            assert actual==3 and 'chunk_queue' in plan and 'chunk_id=?' in plan
            assert steps[0]<300
            results.append(steps[0])
        assert store.capacity(sid,'s')==1
    assert results[1]<=results[0]+20
