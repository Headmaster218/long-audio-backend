"""Parser rejection fixtures only; never physical qualification evidence."""
import importlib.util
from pathlib import Path
import struct

import pytest

spec = importlib.util.spec_from_file_location('physical_probe_fixture',
    Path(__file__).resolve().parents[1]/'qualification/sqlite_crash_probe.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def fixture():
    baseline = b'A'*p.PAGE + b'C'*p.PAGE
    database = b'B'*p.PAGE + b'C'*p.PAGE
    nonce = 17
    check = nonce + sum(baseline[index] for index in range(p.PAGE-200, -1, -200))
    journal = p.MAGIC + struct.pack('>5I', 1, nonce, 2, 512, p.PAGE)
    journal += b'\0'*(512-len(journal))
    journal += struct.pack('>I', 1) + baseline[:p.PAGE] + struct.pack('>I', check)
    return baseline, database, journal


def test_valid_single_header_reconstructs_every_changed_page():
    result = p.audit(*fixture())
    assert result['changed_pages'] == 1 and result['journal_headers'] == 1
    assert result['baseline_sha256'] == result['reconstructed_sha256']


@pytest.mark.parametrize('kind', ['unchanged','uncovered','checksum','truncated','zero_count','unbounded_count','extra_header','wrong_original','duplicate_page','out_of_range','oversized_zero_tail'])
def test_bad_capture_rejected(kind):
    baseline, database, journal = fixture()
    if kind == 'unchanged':database = baseline
    if kind == 'uncovered':database = database[:p.PAGE] + b'D'*p.PAGE
    if kind == 'checksum':journal = journal[:-1] + bytes([journal[-1]^1])
    if kind == 'truncated':journal = journal[:-1]
    if kind == 'zero_count':journal = journal[:8] + struct.pack('>I',0) + journal[12:]
    if kind == 'unbounded_count':journal = journal[:8] + struct.pack('>I',0xffffffff) + journal[12:]
    if kind == 'extra_header':journal += p.MAGIC
    if kind == 'wrong_original':baseline = b'Z'*p.PAGE + baseline[p.PAGE:]
    if kind == 'duplicate_page':
        journal = journal[:8]+struct.pack('>I',2)+journal[12:] + journal[512:]
    if kind == 'out_of_range':journal = journal[:512]+struct.pack('>I',3)+journal[516:]
    if kind == 'oversized_zero_tail':journal += b'\0'*512
    with pytest.raises(ValueError):p.audit(baseline, database, journal)
