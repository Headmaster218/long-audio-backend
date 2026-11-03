"""Read-only bounded audit of an existing incremental-native smoke result.

Copies SQLite and its sidecars before opening the copy. Does not start a service,
replay requests, modify original recordings, or qualify long-duration/accuracy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import wave


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(65536),b''):h.update(block)
    return h.hexdigest()


def pcm(path):
    with wave.open(str(path),'rb') as f:
        assert (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getcomptype())==(1,2,16000,'NONE')
        frames=f.getnframes();assert 0<frames<=480000
        data=f.readframes(frames+1);assert len(data)==frames*2
        return data


def read(path):return json.loads(Path(path).read_text())


def chain(directory,kind,count,head,job):
    previous='0'*64;records=[]
    for number in range(1,count+1):
        path=directory/f'{kind}-{number:04d}.json';raw=path.read_bytes();value=json.loads(raw)
        assert json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()==raw
        assert value['kind']==kind and value['ordinal']==number and value['job']==job
        assert value['previous']==previous and value['policy']=='immutable-files-contiguous-mono16k-v3'
        previous=hashlib.sha256(raw).hexdigest();records.append(value['payload'])
    assert previous==head
    return records


def audit(out,repo,source):
    result=read(out/'RESULT.json');terminal=read(out/'terminal.json')
    assert result['passed'] is True and result['input_kind']=='explicit_recorder_incremental'
    assert not result['accuracy_qualified'] and not result['recorder_provenance_qualified']
    assert result['source_unchanged'] and result['input_unchanged'] and result['external_batch_unchanged']
    assert result['runtime_binary_differences']=={}
    stages=result['incremental_stages'];assert len(stages)==3
    assert [r['frames'] for r in stages]==[64000,128000,192000]
    for n,stage in enumerate(stages,1):
        assert stage['inputs']==stage['sources']==n and stage['published_frames']==0
        assert stage['staged_frames']==stage['frames'] and not stage['capture_finalized']
        assert stage['status']=='delivery_staged_without_eos' and stage['max_hours']==12
        assert not stage['physical_continuity_proven'] and not stage['analysis_completion_verified']
    final=result['upload'];assert final==result['upload_reconciled_resume']
    assert not final['physical_continuity_proven'] and final['hardware_frame_mapping']=='UNESTABLISHED'
    assert final['capture_finalized'] and final['frames']==final['published_frames']==192000 and final['staged_frames']==0
    assert result['injected_lost_published_seal_responses']==1
    assert result['shutdown']['exit_code']==0 and result['shutdown']['terminal']==terminal
    assert terminal['error'] is None and terminal['service_cleanup_error'] is None
    assert terminal['lock_closed'] and terminal['permit_clear'] and terminal['store_safe']
    assert not terminal['worker_alive'] and terminal['shutdown_errors']=={} and terminal['committed_claim_late_count']==0
    assert terminal['shutdown']['observation']=='OBSERVED'
    assert all(terminal['shutdown']['predicates'].values())
    assert all(v is None for v in terminal['shutdown']['exception_classes'].values())
    assert not any(k.endswith('error') for k in result)

    pins=read(out/'SOURCE_PINS.json');actual=[];differences=[]
    for folder in ('audio_backend','tools'):
        for path in sorted((repo/folder).rglob('*.py')):
            if '__pycache__' not in path.parts:
                actual.append(dict(path=str(path.relative_to(repo)),bytes=path.stat().st_size,sha256=digest(path)))
    assert actual==pins
    for row in pins:
        baseline=source/row['path']
        assert baseline.is_file()
        if digest(baseline)!=row['sha256']:differences.append(row['path'])
    assert differences==['audio_backend/native_owned_cell_store.py']
    harness=(repo/'tools/strict_uploader_smoke.py').read_text()
    assert 'if repeated != stages[-1]:' in harness
    assert "raise RuntimeError('incremental_latest_retry_changed_result')" in harness

    sys.path.insert(0,str(repo))
    from audio_backend.recorder_delivery import inspect_delivery
    from audio_backend.recorder_snapshot import inspect_snapshot
    from audio_backend.recorder_sequence import reconcile_snapshots
    from audio_backend.recorder_metadata import decode
    from audio_backend.recorder_delivery import parse_envelope
    receipts=[read(out/f'intake/admissions/{n}/admission.json') for n in range(3)]
    originals=[];snapshots=[]
    for n,r in enumerate(receipts):
        assert r['ordinal']==n and r['delivery']['sequence']==n
        assert inspect_delivery(r['path'],validate_metadata=True)==r['delivery']
        assert inspect_snapshot(r['snapshot_path'])['delivery']==r['delivery']
        a=Path(r['path'])/'audio.wav';b=Path(r['snapshot_path'])/'audio.wav'
        assert digest(a)==digest(b)==r['delivery']['wav_sha256']
        originals.append(pcm(a));snapshots.append(pcm(b))
    assert [r['delivery']['close_reason'] for r in receipts]==['ROTATION','ROTATION','STOP']
    assert [r['delivery']['frames'] for r in receipts]==[64000]*3
    for r in receipts:
        meta=decode(parse_envelope((Path(r['path'])/'segment.v1.bin').read_bytes(),r['delivery'],return_local=True))
        assert meta['target_frames']==meta['frames']==64000
    sequence=reconcile_snapshots([r['snapshot_path'] for r in receipts])
    assert not sequence['physical_continuity_proven']
    assert all(e['relation']=='declared_adjacent' and not e['physical_continuity_proven'] and e['hardware_frame_mapping']=='UNESTABLISHED' for e in sequence['captures'][0]['edges'])
    original_pcm=b''.join(originals);assert len(original_pcm)==384000
    assert original_pcm==b''.join(snapshots)==pcm(out/'synthetic.wav')
    original_hashes={str(Path(r['path'])/name):digest(Path(r['path'])/name) for r in receipts for name in ('audio.wav','segment.v1.bin','delivery.v1.txt')}

    checkpoint=read(out/'intake/uploader/uploader.json');header=read(out/'intake/capture.json')
    assert checkpoint['policy']=='immutable-files-contiguous-mono16k-v3'
    assert checkpoint['caps']==dict(input_bytes=8*1024**3,max_inputs=64,output_frames=12*3600*16000)
    assert header['max_hours']==12 and header['max_inputs']==64
    assert checkpoint['complete'] and checkpoint['finalize_requested']
    assert checkpoint['inputs_done']==checkpoint['input_count']==3 and checkpoint['pages_done']==checkpoint['page_count']==1
    assert checkpoint['source_count']==3 and checkpoint['emitted_frames']==checkpoint['published_frames']==192000
    assert checkpoint['active_input'] is None and checkpoint['active_page'] is None
    assert checkpoint['archive_intent'] is None and checkpoint['retirement'] is None and checkpoint['spool']==[]
    assert not list((out/'intake/uploader').glob('*.wav*'))
    history=out/'intake/uploader/uploader.history'
    inputs=chain(history,'input',3,checkpoint['inputs_head'],checkpoint['job'])
    pages=chain(history,'page',1,checkpoint['pages_head'],checkpoint['job']);page=pages[0]
    for entry,r in zip(inputs,receipts):
        assert entry['clean_eof'] and entry['id']==r['delivery']['source_id']
        assert entry['frames']==r['delivery']['frames'] and entry['normalized_start']==r['delivery']['frame_start']
        assert entry['identity']['sha256']==r['delivery']['wav_sha256']
    assert page['released'] and page['seal']['eos'] and page['seal']['end_frame']==192000

    db=out/'data/jobs.sqlite3';db_hash_before=digest(db)
    with tempfile.TemporaryDirectory(prefix='incremental-smoke-audit-') as temp:
        copy=Path(temp)/'jobs.sqlite3'
        for suffix in ('','-wal','-shm'):
            p=Path(str(db)+suffix)
            if p.exists():shutil.copyfile(p,Path(str(copy)+suffix))
        conn=sqlite3.connect(str(copy));conn.row_factory=sqlite3.Row
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        sessions=[dict(r) for r in conn.execute('SELECT * FROM sessions')]
        batches=[dict(r) for r in conn.execute('SELECT * FROM recording_batches')]
        sources=[dict(r) for r in conn.execute('SELECT * FROM recording_sources ORDER BY source_start_frame')]
        work=[dict(r) for r in conn.execute('SELECT w.*,c.path,c.status,c.duration,c.digest,c.metadata FROM recording_work w JOIN chunks c ON c.id=w.chunk_id ORDER BY w.position')]
        ranges=[dict(r) for r in conn.execute('SELECT * FROM recording_ranges ORDER BY capture_start_frame')]
        remote_pages=[dict(r) for r in conn.execute('SELECT * FROM recording_pages')]
        stream=[dict(r) for r in conn.execute('SELECT * FROM stream_chunks ORDER BY position')]
        assert len(sessions)==len(batches)==len(remote_pages)==1 and len(sources)==3 and len(work)==2 and len(ranges)==4
        assert sessions[0]['timeline_end']==12.0
        assert sessions[0]['id']==result['session_id'] and batches[0]['capture_origin_frame']==0 and batches[0]['capture_end_frame']==192000 and batches[0]['eos']==1
        assert remote_pages[0]['state']=='published' and remote_pages[0]['released']==remote_pages[0]['eos']==1
        assert remote_pages[0]['seal_key']==page['seal']['idempotency_key']
        frozen=json.loads(remote_pages[0]['seal_request'])
        assert frozen['end_frame']==page['seal']['end_frame'] and frozen['expected_revision']==page['seal']['expected_revision'] and frozen['eos']==page['seal']['eos']
        assert [s['source_start_frame'] for s in sources]==[0,64000,128000]
        assert [s['frames'] for s in sources]==[64000,64000,64000]
        for s,p,r in zip(sources,page['pieces'],receipts):
            assert s['id']==p['server_id'] and s['idempotency_key']==p['key']
            assert s['digest']==p['digest']==r['delivery']['wav_sha256']==digest(s['path'])
        assert b''.join(pcm(s['path']) for s in sources)==original_pcm
        assert [w['capture_start_frame'] for w in work]==[0,168000] and [w['frames'] for w in work]==[168000,24000]
        assert all(w['status']=='completed' and w['receipt_state']=='durable' and w['native_receipt_status']=='receipt_retained' for w in work)
        assert all(digest(w['path'])==w['digest']==json.loads(w['native_receipt'])['digest'] for w in work)
        assert b''.join(pcm(w['path']) for w in work)==original_pcm
        assert not any(original_pcm)
        for w in work:
            meta=json.loads(w['metadata'])
            assert meta['diarization_backend']=='nemotron3-native' and meta['silence_fast_path']
            assert meta['models_executed']==['Nemotron-3']
            assert meta['native_diarization']['finished'] and meta['native_diarization']['processed_until']==12.0
        assert sum(r['frames'] for r in ranges)==192000 and all(r['relation']=='owner' for r in ranges)
        cursor=0
        for r in ranges:
            assert r['capture_start_frame']==cursor
            s=next(x for x in sources if x['id']==r['source_id']);w=next(x for x in work if x['chunk_id']==r['chunk_id'])
            assert r['source_start_frame']+s['source_start_frame']==r['capture_start_frame']
            assert r['work_start_frame']+w['capture_start_frame']==r['capture_start_frame']
            cursor+=r['frames']
        assert [r['stream_final'] for r in stream]==[0,1] and [r['upload_stream_final'] for r in stream]==[0,1]
        conn.close()
    assert digest(db)==db_hash_before
    assert all(digest(path)==value for path,value in original_hashes.items())
    return dict(schema='exact-stop-incremental-native-smoke-independent-audit-v1',passed=True,
        duration_seconds=12,frames=192000,full_stop_frames=64000,rotation_target_frames=64000,append_calls=3,stage_frames=[s['frames'] for s in stages],
        stage_eos=False,stage_published_frames=[s['published_frames'] for s in stages],
        latest_retry_evidence='Pinned harness enforced equality; separate retry result not retained',
        explicit_finalization=True,finalize_replay_equal=True,lost_published_seal_responses=1,
        lost_response_evidence='Pinned harness and RESULT counter; uploader per-attempt HTTP transcript not retained',
        source_starts=[s['source_start_frame'] for s in sources],source_frames=[s['frames'] for s in sources],
        native_chunk_frames=[w['frames'] for w in work],native_chunks_completed=2,
        concatenated_pcm_equal_across='Originals, owned snapshots, combined fixture, server sources and derived work chunks',
        concatenated_pcm_sha256=hashlib.sha256(original_pcm).hexdigest(),
        source_pins_verified=len(pins),candidate_source_difference=differences,
        logical_duration_cap_hours=12,physical_test_duration_hours=12/3600,
        sqlite_integrity='ok',original_database_sha256=db_hash_before,originals_unchanged=True,
        uploader_spool_empty=True,shutdown_exit_code=0,all_shutdown_predicates=True,
        peak_sampled_rss_bytes=result['peak_sampled_rss'],
        long_duration_ingestion_qualified=False,accuracy_qualified=False,hardware_continuity_proven=False,
        artifact_hashes={name:digest(out/name) for name in ('RESULT.json','terminal.json','SOURCE_PINS.json','ASSET_VERIFIED.json')})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path);parser.add_argument('--repo',required=True,type=Path)
    parser.add_argument('--source',required=True,type=Path);parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args();value=audit(args.output.resolve(),args.repo.resolve(),args.source.resolve())
    args.report.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
    print(json.dumps(value,sort_keys=True))
