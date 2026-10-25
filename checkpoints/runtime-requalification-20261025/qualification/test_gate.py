"""Source-only root-executed controls; fabricated journal is unit data ONLY."""
import struct
import pytest
import gate


def fake_pair(tmp_path,monkeypatch):
    monkeypatch.setattr(gate,'PAGES',2)
    original=tmp_path/'interrupted';original.mkdir()
    (original/"namespace").mkdir()
    baseline=tmp_path/'baseline';baseline.write_bytes(b'A'*8192)
    (original/'namespace'/'cells.sqlite3').write_bytes(b'B'*4096+b'A'*4096)
    nonce=123;body=b'A'*4096
    checksum=(nonce+sum(body[j] for j in range(4096-200,0,-200)))&0xffffffff
    header=gate.MAGIC+struct.pack('>5I',1,nonce,2,512,4096)
    raw=header+b'\0'*(512-len(header))+struct.pack('>I',1)+body+struct.pack('>I',checksum)
    (original/'namespace'/'cells.sqlite3-journal').write_bytes(raw)
    return original,baseline


def test_exact_typed_header_checksum_preimage_and_changed_page(tmp_path,monkeypatch):
    original,baseline=fake_pair(tmp_path,monkeypatch)
    result=gate.audit_journal(original,baseline)
    assert result['records']==1 and result['changed_pages']==1 and result['headers']==1

@pytest.mark.parametrize('offset',[0,8,16,20,24,516,4615])
def test_corrupt_journal_refuses(tmp_path,monkeypatch,offset):
    original,baseline=fake_pair(tmp_path,monkeypatch)
    path=original/'namespace'/'cells.sqlite3-journal';raw=bytearray(path.read_bytes());raw[offset]^=1;path.write_bytes(raw)
    with pytest.raises(RuntimeError):gate.audit_journal(original,baseline)


def test_no_interrupted_database_change_refuses(tmp_path,monkeypatch):
    original,baseline=fake_pair(tmp_path,monkeypatch)
    (original/'namespace'/'cells.sqlite3').write_bytes(baseline.read_bytes())
    with pytest.raises(RuntimeError):gate.audit_journal(original,baseline)


def test_uncovered_database_change_refuses(tmp_path,monkeypatch):
    original,baseline=fake_pair(tmp_path,monkeypatch)
    (original/'namespace'/'cells.sqlite3').write_bytes(b'B'*8192)
    with pytest.raises(RuntimeError):gate.audit_journal(original,baseline)


def test_fresh_output_not_overwrite(tmp_path):
    with pytest.raises(RuntimeError):gate.private(tmp_path)
    target=tmp_path/'fresh';gate.private(target)
    assert target.stat().st_mode&0o777==0o700
    with pytest.raises(RuntimeError):gate.private(target)


def test_storage_bounds_match_published_contract():
    assert gate.PAGE==4096 and gate.PAGES==6144
    assert (gate.DB_CAP,gate.JOURNAL_CAP,gate.SLACK_CAP,gate.ENVELOPE)==tuple(n*1024**2 for n in (24,32,2,58))
    assert gate.ARTIFACT_CAP==192*1024**2 and gate.RSS_CAP==256*1024**2
    assert gate.AS_CAP==1024**3 and gate.PHASE_SECONDS==120
