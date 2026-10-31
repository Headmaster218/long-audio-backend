"""Stopped, streaming integrity census; no model, no unbounded BLOB fetch.
File hashes cover registered original/canonical/shared/unit files. SQLite files
are checked separately. This is capacity integrity, not transcription accuracy.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import sys
import time

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))


def hash_file(path,expected_size,expected_hash):
    before=path.lstat()
    assert path.is_file() and not path.is_symlink() and before.st_size==expected_size
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):digest.update(block)
    assert digest.hexdigest()==expected_hash
    after=path.lstat();assert all(getattr(before,key)==getattr(after,key) for key in ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns'))
    return before.st_blocks*512


def inspect(data,config,pages):
    until=time.monotonic()+1800
    data=Path(data).resolve();database=data/'jobs.sqlite3'
    db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5);db.row_factory=sqlite3.Row
    db.set_progress_handler(lambda:int(time.monotonic()>until),10000)
    report={'schema':'streaming-stopped-integrity-v1','files':0,'allocated_registered':0}
    seen=set();roster=hashlib.sha256();cold_ids=[];shared_refs={};shared_descriptions={}
    def verify(path,size,digest):
        path=Path(path).absolute();assert path.is_relative_to(data) and path not in seen
        seen.add(path);report['allocated_registered']+=hash_file(path,size,digest);report['files']+=1
        roster.update(json.dumps([str(path.relative_to(data)),size,digest],separators=(',',':')).encode()+b'\n')
        if time.monotonic()>until:raise TimeoutError('integrity_deadline')
    try:
        assert list(db.execute('PRAGMA integrity_check'))[0][0]=='ok'
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None
        report['optional_identity_success_claimed']=False
        report['optional_history_scopes']=[dict(row) for row in db.execute('SELECT scope_seq,state,authority_blocked,history_revision,observation_count,vector_count FROM recording_history_scopes_v1 WHERE scope_seq>0 LIMIT 65')]
        report['optional_attempt_counts']=dict(db.execute('SELECT state,count(*) FROM recording_history_attempts_v1 GROUP BY state'))
        reasons={}
        for row in db.execute('SELECT terminal FROM recording_history_attempts_v1 WHERE terminal IS NOT NULL'):
            value=json.loads(row[0]);reason=str(value.get('reason',value.get('state','unspecified')))
            reasons[reason]=reasons.get(reason,0)+1
        report['optional_terminal_reasons']=reasons
        stats=dict(db.execute('SELECT status,count(*) FROM chunks GROUP BY status'))
        assert stats=={'completed':pages*28},stats
        assert db.execute('SELECT count(*) FROM chunks WHERE next_offset!=duration OR duration!=10.5').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM recording_batches').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM recording_pages WHERE released=1').fetchone()[0]==pages
        assert db.execute('SELECT count(*) FROM recording_pages WHERE eos=1 AND page_number=?',(pages,)).fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM recording_sources').fetchone()[0]==pages*3
        for row in db.execute('SELECT path,bytes,digest FROM recording_sources ORDER BY seq'):
            verify(row['path'],row['bytes'],row['digest'])
        for row in db.execute('SELECT path,digest FROM chunks ORDER BY seq'):
            verify(row['path'],44+168000*2,row['digest'])
        units=0
        for row in db.execute('SELECT * FROM strict_payload_unit ORDER BY sequence'):
            assert row['state']=='committed';units+=1
            path=data/'strict-payload-v2'/f"{row['bucket']:08x}"/(row['unit_id']+'.unit')
            verify(path,row['bytes'],row['file_sha256'])
            with path.open('rb') as stream:
                assert stream.read(6)==b'SPV2\x00\x01'
                length=struct.unpack('>I',stream.read(4))[0]
                assert length==row['manifest_bytes'] and 0<length<=4*1024**2
                raw=stream.read(length);assert hashlib.sha256(raw).hexdigest()==row['manifest_sha256']
                manifest=json.loads(raw)
                for obj in manifest['objects']:
                    ref=obj.get('shared_source')
                    if ref is not None:
                        key=ref['body_id']
                        if key in shared_descriptions:assert shared_descriptions[key]==ref
                        else:shared_descriptions[key]=ref
                        shared_refs[key]=shared_refs.get(key,0)+1
        assert units>0,'no_natural_archive'
        bodies=0;allocated_shared=0;published_ids=set()
        for row in db.execute('SELECT * FROM strict_shared_source_body ORDER BY body_id'):
            assert row['state']=='published';bodies+=1;published_ids.add(row['body_id'])
            reference=shared_descriptions[row['body_id']]
            assert reference=={'body_id':row['body_id'],'identity':json.loads(row['identity']),
                'bytes':row['bytes'],'sha256':row['sha256'],'frames':row['frames']}
            path=data/'strict-shared-sources-v1'/row['body_id'][:4]/(row['body_id']+'.body')
            verify(path,row['bytes'],row['sha256']);allocated_shared+=path.stat().st_blocks*512
            assert path.stat().st_blocks*512==row['allocated'] and shared_refs.get(row['body_id'],0)>0
        assert set(shared_refs)==published_ids,'missing_shared_body'
        assert any(count>1 for count in shared_refs.values()),'shared_reuse_not_exercised'
        usage=db.execute('SELECT * FROM strict_shared_source_usage').fetchone()
        assert usage['bodies']==bodies and usage['reserved']==0 and usage['allocated']==allocated_shared
        for root in (data/'strict-payload-v2',data/'strict-shared-sources-v1'):
            for path in root.rglob('*'):
                assert not path.is_symlink()
                if path.is_file():assert path.absolute() in seen,'unregistered_archive_file'
        cold_ids=[tuple(row) for row in db.execute("SELECT s.session_id,s.id,o.sha256,o.bytes FROM speaker_support s JOIN strict_payload_object o ON o.kind='pcm' AND o.owner_id=s.id ORDER BY s.rowid LIMIT 3")]
        assert cold_ids,'no_cold_pcm'
        report.update(chunks=pages*28,archives=units,shared_bodies=bodies,shared_reused_bodies=sum(n>1 for n in shared_refs.values()),
            shared_references=sum(shared_refs.values()),registered_roster_sha256=roster.hexdigest())
    finally:db.close()
    private_database=data/'native-owned-cells-bounded-v1'/'cells.sqlite3'
    assert private_database.is_file() and not private_database.is_symlink(),'native_sidecar_missing'
    report['sqlite_files']=[str(database.relative_to(data))]
    for path in data.rglob('*'):
        if path==database or path.suffix not in ('.sqlite','.sqlite3'):continue
        other=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
        try:
            other.set_progress_handler(lambda:int(time.monotonic()>until),10000)
            assert other.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert other.execute('PRAGMA foreign_key_check').fetchone() is None
            report['sqlite_files'].append(str(path.relative_to(data)))
        finally:other.close()
    # New original Store open and exact production cold API: bounded three records.
    from audio_backend.settings import Settings
    from audio_backend.storage import Store
    from audio_backend import speaker_support
    store=Store(database,Settings(**config));cold=[]
    for sid,support_id,digest,size in cold_ids:
        wav=speaker_support.audio(store,sid,support_id)
        # Production audio() emits fixed float-WAV header followed by exact PCM.
        pcm=wav[-size:];assert len(pcm)==size and hashlib.sha256(pcm).hexdigest()==digest
        cold.append({'id':support_id,'bytes':size,'sha256':digest})
    assert store.strict_cleanup_confirmed()
    report['production_cold_reads']=cold
    report['reopen_cleanup_confirmed']=True
    return report
