"""ROOT-ONLY fresh synthetic FULL database reproduction; author did not run.

Five independent ordinary copies, never original gate run01. Test stores all
case receipts; SQL variants are diagnostic, not acceptance budget relaxation.
"""
import json,shutil,time
from pathlib import Path
import gate


def test_full_database_record_growth_vs_fixed_blob(tmp_path):
    pins=gate.read(gate.HERE/'pins.json');store=gate.load_source(pins,gate.HERE/'product.sha256')
    gate.identity(pins,store,tmp_path)
    root=tmp_path/'new-synthetic-full';root.mkdir(mode=0o700)
    filled=root/'fill';filled.mkdir(mode=0o700)
    expected=gate.fill(filled,store,time.monotonic()+60)
    baseline=filled/'namespace'/'cells.sqlite3'
    assert expected['page_count']==6144 and expected['sqlite_full_observed']
    assert expected['oracle']['cells']['rows']==42
    assert expected['oracle']['dependencies']['rows']==42
    assert expected['oracle']['sources']['rows']==128
    results={}
    cases=('old_combined','revision_only','pcm_sql_only','revision_rowwise','fixed_blob')
    for case in cases:
        directory=root/case;directory.mkdir(mode=0o700)
        path=directory/'cells.sqlite3';shutil.copyfile(baseline,path)
        db=gate.connect(path,store);row={'case':case};mutation=None;attempted_rowid=None
        before_rows=db.execute('SELECT seq,length(pcm),revision FROM cells ORDER BY seq').fetchall()
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                if case=='old_combined':db.execute('UPDATE cells SET pcm=zeroblob(length(pcm)),revision=revision+1')
                elif case=='revision_only':db.execute('UPDATE cells SET revision=revision+1')
                elif case=='pcm_sql_only':db.execute('UPDATE cells SET pcm=zeroblob(length(pcm))')
                elif case=='revision_rowwise':
                    for rowid,length,revision in before_rows:
                        attempted_rowid=rowid
                        db.execute('UPDATE cells SET revision=revision+1 WHERE seq=?',(rowid,))
                else:
                    mutation=gate.overwrite_existing_pcm(db,time.monotonic()+30)
                    assert db.execute('SELECT seq,length(pcm),revision FROM cells ORDER BY seq').fetchall()==before_rows
                    for rowid,pcm in db.execute('SELECT seq,pcm FROM cells ORDER BY seq'):
                        assert pcm==b'\0'*len(pcm)
                row.update(result='MUTATED_UNCOMMITTED',page_count=db.execute('PRAGMA page_count').fetchone()[0],mutation=mutation)
            except gate.sqlite3.Error as exc:
                row.update(result='SQLITE_ERROR',sqlite_code=getattr(exc,'sqlite_errorcode',None),
                    attempted_rowid=attempted_rowid)
            finally:
                if db.in_transaction:db.execute('ROLLBACK')
            assert gate.oracle(db)==expected['oracle']
            assert db.execute('PRAGMA page_count').fetchone()[0]==6144
        finally:db.close()
        if row['result']=='SQLITE_ERROR':assert row['sqlite_code']==gate.sqlite3.SQLITE_FULL
        row['rollback_byte_equal']=gate.sha(path)==expected['database_sha256']
        assert row['rollback_byte_equal']
        results[case]=row
        gate.save(directory/'result.json',row)
        assert gate.charged_tree(root)<gate.ARTIFACT_CAP
    gate.save(root/'mutation-comparison.json',results)
    assert results['old_combined']['result']=='SQLITE_ERROR'
    assert results['old_combined']['sqlite_code']==gate.sqlite3.SQLITE_FULL
    assert results['fixed_blob']['result']=='MUTATED_UNCOMMITTED'
    assert results['fixed_blob']['mutation']['payload_bytes_written']>0
    assert results['fixed_blob']['mutation']['page_count_before']==6144
    assert results['fixed_blob']['mutation']['page_count_after']==6144
