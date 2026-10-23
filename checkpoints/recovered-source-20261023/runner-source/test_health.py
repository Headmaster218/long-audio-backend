import copy
import pytest
from health import EXPECTED, HealthDiagnostics, health_blockers, wait_operational_idle


def idle():
    result = {}
    for dotted, value in EXPECTED.items():
        current = result
        parts = dotted.split('.')
        for part in parts[:-1]: current = current.setdefault(part, {})
        current[parts[-1]] = value
    result['native_owned_cells']['core_seam_pending'] = True
    return result


def test_coarse_pending_is_not_active_work_or_zero_backlog_proof():
    value = idle()
    assert health_blockers(value) == []
    diagnostics = HealthDiagnostics(lambda value: None, clock=lambda: 0)
    diagnostics.observe(value)
    assert diagnostics.last_snapshot['maintenance_backlog'] == 'UNKNOWN'
    assert diagnostics.last_snapshot['durable_pending_zero_proven'] is False


@pytest.mark.parametrize('field', list(EXPECTED))
def test_each_required_actual_field_rejects_missing_and_wrong(field):
    value = idle(); parts = field.split('.'); parent = value
    for part in parts[:-1]: parent = parent[part]
    parent.pop(parts[-1])
    assert field in health_blockers(value)
    parent[parts[-1]] = object()
    assert field in health_blockers(value)


@pytest.mark.parametrize('path', [('native_owned_cells',), ('native_owned_cells','retention'),
                                ('native_owned_cells','retention','roots'), ('moss_jobs',)])
def test_malformed_after_healthy_has_no_stale_blockers(path):
    events = []; diagnostics = HealthDiagnostics(events.append, clock=lambda: 1)
    diagnostics.observe(idle())
    malformed = idle(); parent = malformed
    for part in path[:-1]: parent = parent[part]
    parent[path[-1]] = None
    diagnostics.observe(malformed)
    assert diagnostics.last_blockers == health_blockers(malformed)
    assert diagnostics.last_blockers
    diagnostics.failure()
    assert events[-1]['current_health'] == 'UNKNOWN'


def test_late_final_healthy_response_is_diagnostic_not_success():
    now = [0.0]; events = []
    diagnostics = HealthDiagnostics(events.append, clock=lambda: now[0])
    def get(until, observe):
        now[0] = until
        observe(idle(), late=True)
        return idle()
    with pytest.raises(TimeoutError):
        wait_operational_idle(get, overall_deadline=300, diagnostics=diagnostics,
                              clock=lambda: now[0], sleep=lambda _: None)
    assert events[-1]['current_health'] == 'UNKNOWN'
    assert events[-1]['last_response_late'] is True
