"""Fail-closed CLI controls, separate from actual saved-byte proof."""
from pathlib import Path
import subprocess
import sys
import pytest

SCRIPT = Path(__file__).with_name('audit_target_cellstore_evidence.py')
SOURCE = SCRIPT.parent.parent

def run(evidence, report, *flags):
    return subprocess.run([sys.executable, *flags, '-B', str(SCRIPT), '--evidence', str(evidence),
                           '--source', str(SOURCE), '--report', str(report),
                           '--target-observation', str(evidence/'target.json'), '--target-sha256', '0'*64],
                          capture_output=True, text=True, timeout=10)

def test_optimized_audit_refused(tmp_path):
    report = tmp_path/'report.json'
    result = run(tmp_path, report, '-O')
    assert result.returncode != 0 and 'audit_requires_assertions' in result.stderr
    assert not report.exists()

def test_existing_report_preserved(tmp_path):
    evidence = tmp_path/'evidence'; evidence.mkdir()
    report = tmp_path/'report.json'; report.write_text('existing')
    result = run(evidence, report)
    assert result.returncode != 0
    assert report.read_text() == 'existing'

def test_report_inside_evidence_refused(tmp_path):
    report = tmp_path/'report.json'
    result = run(tmp_path, report)
    assert result.returncode != 0 and 'outside_evidence' in result.stderr
    assert not report.exists()

@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'too_many_files'])
def test_unsafe_inventory_refused(tmp_path, kind):
    evidence = tmp_path/'evidence'; evidence.mkdir()
    report = tmp_path/'report.json'
    if kind == 'symlink':
        (evidence/'alias').symlink_to(tmp_path/'missing')
    elif kind == 'fifo':
        import os
        os.mkfifo(evidence/'fifo')
    else:
        for i in range(129): (evidence/str(i)).write_bytes(b'')
    result = run(evidence, report)
    assert result.returncode != 0
    assert not report.exists()
