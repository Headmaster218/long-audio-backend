"""Offline whole-excerpt CER; never runs models or certifies execution."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('inherited_english_metric',
             Path(__file__).resolve().parents[1] / 'english' / 'score.py')
_metric = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_metric)
FAILURES = {'model_failure', 'infrastructure_failure', 'not_attempted', 'aborted', 'unknown'}
STATUSES = FAILURES | {'success', 'truncated'}

def score_excerpt(reference, records):
    """Records are already in fixed-window order; return no text, only counts.

    This small helper does not validate sample coverage, annotation provenance,
    repetition flags or a run's quality gate. It never converts a failed gate
    into a pass. Truncated text is scored; failed-call text is excluded.
    """
    if not isinstance(records, list) or not 1 <= len(records) <= 64:
        raise ValueError('Expected 1 through 64 ordered window records')
    parts = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {'status', 'text', 'truncated'}:
            raise ValueError('Exact status/text/truncated fields required')
        if record['status'] not in STATUSES or type(record['truncated']) is not bool:
            raise ValueError('Unknown status or non-boolean truncation')
        if ((record['status'] == 'success' and record['truncated']) or
            (record['status'] == 'truncated' and not record['truncated'])):
            raise ValueError('Inconsistent terminal status')
        _metric.normalize(record['text'])
        parts.append('' if record['status'] in FAILURES else record['text'])
    ref = ''.join(_metric.normalize(reference).split())
    hyp = ''.join(_metric.normalize(''.join(parts)).split())
    if len(ref) * len(hyp) > 20000000:
        raise ValueError('Edit workload bound exceeded; text is never truncated')
    counts = _metric.edits(ref, hyp)
    counts['reference_characters'] = counts.pop('reference_units')
    counts['cer'] = counts['errors'] / len(ref) if ref else None
    counts['failures'] = sum(r['status'] in FAILURES for r in records)
    counts['truncations'] = sum(r['truncated'] for r in records)
    return counts
