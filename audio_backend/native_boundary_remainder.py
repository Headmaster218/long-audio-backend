"""Read-only proof binding for a separately approved P2 split-only remainder.

Never reruns the completed whole arm or edits its ledger/failure/raw evidence.
"""
import hashlib
import json
from pathlib import Path
import sqlite3

from .fragment_runtime_contracts import digest
from .fragment_runtime_store import RuntimeStore

EXPECTED_USED = {'native_reset': {'calls':2,'frames':0},
                 'native_push': {'calls':8,'frames':5990401},
                 'native_finish': {'calls':1,'frames':0},
                 'native_turns': {'calls':9,'frames':0}}
REMAINDER_LIMITS = {'native_reset': {'calls':2,'frames':0},
                    'native_push': {'calls':9,'frames':6099202},
                    'native_finish': {'calls':1,'frames':0},
                    'native_turns': {'calls':10,'frames':0}}


def verify_evidence(manifest):
    required = {'P1-attempt.json','P1-result.json','P2-attempt.json','P2-failure.json',
                'P2_whole.sqlite','P2-native-1.json','P2-native-2.json','P2-resource-summary.json'}
    expected = {str(Path(manifest['output'])/name) for name in required}
    if set(manifest['evidence_files']) != expected:
        raise ValueError('Remainder must pin every original attempt and whole evidence file')
    for name, pin in manifest['evidence_files'].items():
        path = Path(name)
        if path.resolve() != path or not path.is_file() or path.stat().st_size != pin['bytes']:
            raise ValueError('Original whole/P2 evidence path or size changed')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest() != pin['sha256']:
                raise ValueError('Original whole/P2 evidence bytes changed')


def validate_remainder(frozen):
    from .native_boundary_probe import LIMITS
    manifest=frozen['remainder']
    verify_evidence(manifest)
    output=Path(frozen['protocol']['real_execution']['output'])
    if manifest['output']!=str(output) or manifest['selected_arms']!=['P2_split_late_eos_restart']:
        raise ValueError('Remainder must use canonical output and only the unrun split arm')
    failure=json.loads((output/'P2-failure.json').read_text())
    original=json.loads((output/'P2-attempt.json').read_text())
    p1=json.loads((output/'P1-attempt.json').read_text())
    p1_result=json.loads((output/'P1-result.json').read_text())
    if (failure['budget_used']!=EXPECTED_USED or manifest['actual_consumed_p2']!=EXPECTED_USED
        or failure['error']!='Bounded envelope exceeded'
        or original['reserved']!=LIMITS['P2']
        or original['freeze_sha256']!=manifest['parent_freeze_sha256']
        or p1['freeze_sha256']!=manifest['parent_freeze_sha256']
        or p1_result['status']!='completed'
        or p1_result['budget_used']!=LIMITS['P1']):
        raise ValueError('Original attempts or actual remaining budget do not match')
    remaining={kind:{k:LIMITS['P2'][kind][k]-EXPECTED_USED[kind][k] for k in ('calls','frames')}
               for kind in EXPECTED_USED}
    if manifest['remaining_original_ceiling']!=remaining or manifest['requested_limits']!=REMAINDER_LIMITS:
        raise ValueError('Remainder ceiling changed or exceeded original P2 scope')
    if any(REMAINDER_LIMITS[k][n]>remaining[k][n] for k in remaining for n in ('calls','frames')):
        raise ValueError('Remainder attempts exceed unspent original ceiling')
    if (manifest['combined_deadline_unix']!=p1['began_unix']+1800
        or manifest['first_admission_unix']!=p1['began_unix']):
        raise ValueError('Original combined deadline reset')
    if (output/'P2_split_late_eos_restart.sqlite').exists():
        raise ValueError('Split arm already attempted; no retry or reuse')
    return REMAINDER_LIMITS, ['P2_split_late_eos_restart']


def reconstruct_whole(manifest):
    """Hash-verified read-only SQLite reconstruction; no model or file mutation."""
    verify_evidence(manifest)
    output=Path(manifest['output'])
    with sqlite3.connect('file:'+str(output/'P2_whole.sqlite')+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT * FROM fragment_runs WHERE run_id=?',('P2_whole',)).fetchone()
        expected=RuntimeStore._state_hash(*[row[k] for k in (
            'revision','plan_hash','policy_hash','next_frame','prefix_hash','tail','checkpoint','status')])
        if row['state_hash']!=expected or row['status']!='completed' or row['next_frame']!=5280001:
            raise ValueError('Original whole run not durably completed')
        control=dict(db.execute('SELECT * FROM fragment_controls WHERE run_id=?',('P2_whole',)).fetchone())
        if not control['acknowledged'] or digest(json.loads(control['response']))!=control['response_hash']:
            raise ValueError('Original whole EOS response corrupt/unacknowledged')
        commits=[]
        for saved in db.execute('SELECT payload,payload_hash FROM fragment_core_commits WHERE run_id=? ORDER BY core_start',('P2_whole',)):
            value=json.loads(saved['payload'])
            if digest(value)!=saved['payload_hash']:
                raise ValueError('Original whole committed core hash differs')
            commits.append(value)
    if [(p['window']['core_start'],p['window']['core_end']) for p in commits] != [
            (a,min(a+480000,5280001)) for a in range(0,5280001,480000)]:
        raise ValueError('Original whole ownership coverage differs')
    return {'arm':'P2_whole','status':'reconstructed_from_completed_whole_native_evidence',
            'native_rerun':False,'commits':commits,
            'snapshot':{'next_frame':row['next_frame'],'status':row['status'],'control':control},
            'source_evidence_files':manifest['evidence_files'],
            'quality_claim':'existing native mechanics only; original paired stage remains failed'}
