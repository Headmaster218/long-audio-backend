"""Local experiment refusal/validation tests; no model or runtime qualification."""
import importlib.util
import json
from pathlib import Path
import pytest
from test_recorder_metadata import semantic_delivery


def module():
    path = Path(__file__).resolve().parents[1] / 'tools/recorder_delivery_smoke.py'
    spec = importlib.util.spec_from_file_location('recorder_smoke_test', path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_valid_local_producer_declaration(tmp_path):
    semantic_delivery(tmp_path)
    report = module().validate_delivery(tmp_path)
    assert report['frames'] == 3 and not report['strict_import_ready']


def test_corrupt_delivery_never_starts_service(tmp_path, monkeypatch):
    source = tmp_path / 'input'; source.mkdir(); semantic_delivery(source)
    (source / 'audio.wav').write_bytes(b'bad')
    m = module()
    def forbidden(): raise AssertionError('must not start service')
    monkeypatch.setattr(m.smoke, 'main', forbidden)
    output = tmp_path / 'output'
    assert m.main([str(source), str(output), '--assets', str(tmp_path / 'unused')]) == 1
    assert not (output / 'service').exists() and not (output / 'snapshot').exists()
    assert not json.loads((output / 'RESULT.json').read_text())['passed']


@pytest.mark.parametrize('key,value', [('sequence',1),('epoch',1),('frame_start',1),('close_reason','ROTATION'),('channels',2),('frames',480001)])
def test_unsupported_mapping_refused(tmp_path, monkeypatch, key, value):
    semantic_delivery(tmp_path)
    m = module(); report = m.inspect_delivery(tmp_path, validate_metadata=True)
    report[key] = value
    monkeypatch.setattr(m, 'inspect_delivery', lambda *a, **kw: report)
    with pytest.raises(m.Refused): m.validate_delivery(tmp_path)


def test_durable_mapping_checks_saved_bytes(tmp_path):
    import sqlite3
    import hashlib
    m = module(); audio = tmp_path / 'source.wav'; audio.write_bytes(b'synthetic')
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    path = tmp_path / 'jobs.sqlite3'; db = sqlite3.connect(path)
    db.executescript('''
      CREATE TABLE recording_sources(id TEXT,batch_id TEXT,page_id TEXT,digest TEXT,frames INT,bytes INT,source_start_frame INT,path TEXT,idempotency_key TEXT);
      CREATE TABLE recording_batches(id TEXT,session_id TEXT,capture_id TEXT,capture_origin_frame INT,capture_end_frame INT,eos INT);
      CREATE TABLE recording_pages(id TEXT,batch_id TEXT,page_number INT,start_frame INT,end_frame INT,state TEXT,released INT,eos INT);
    ''')
    db.execute('INSERT INTO recording_sources VALUES(?,?,?,?,?,?,?,?,?)', ('source','batch','page',digest,3,9,0,str(audio),'key'))
    db.execute('INSERT INTO recording_batches VALUES(?,?,?,?,?,?)',('batch','session','synthetic-smoke',0,3,1))
    db.execute('INSERT INTO recording_pages VALUES(?,?,?,?,?,?,?,?)',('page','batch',1,0,3,'published',1,1))
    db.commit(); db.close()
    expected = dict(wav_sha256=digest,frames=3,wav_bytes=9)
    assert m.durable_mapping(path,expected,'session')['backend_source_id']=='source'
    with pytest.raises(m.Refused):m.durable_mapping(path,expected,'different-session')
    audio.write_bytes(b'changed')
    with pytest.raises(m.Refused):m.durable_mapping(path,expected,'session')
