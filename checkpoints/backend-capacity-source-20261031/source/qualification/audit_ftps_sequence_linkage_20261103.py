"""Read-only linkage audit: saved real TLS receipts to existing native intake audit.

No sockets, inference, writes to originals, production activation or credentials.
Reports are local evidence and must not be uploaded with source.
"""
import argparse
import hashlib
import json
from pathlib import Path
from independent_exact_stop_audit_20261103 import audit, digest


def verify(output, repo, source, wire_report, expected_report_sha256):
    assert digest(wire_report) == expected_report_sha256
    wire = json.loads(wire_report.read_text())
    assert wire['exact_boundary_sequence'] and len(wire['scenarios']) == 3
    assert sorted(x['ordinal'] for x in wire['scenarios']) == [0, 1, 2]
    observed = []
    for scenario in sorted(wire['scenarios'], key=lambda x:x['ordinal']):
        ordinal = scenario['ordinal']; client = scenario['client']
        assert scenario['scenario'] == 'success' and client['status'] == 'verified'
        server = scenario['server']
        assert server['scenario'] == 'success' and server['sessions'] == 1
        assert server['control_tls'] == 1 and server['data_tls'] == 9
        assert server['all_closed'] and not server['errors'] and not server['fault']
        assert all(command['tls'] for command in server['commands'] if command['cmd'] != 'AUTH')
        assert client['all_raw_sockets_closed'] and client['local_unchanged']
        assert client['callback_registrations'] == client['callback_unregistrations']
        relative = Path(client['remote_directory'].lstrip('/'))
        assert '..' not in relative.parts and not relative.is_absolute()
        received = wire_report.parent / ('success-' + str(ordinal)) / 'remote' / relative
        assert received.name == client['attempt']
        assert set(server['remote_files']) == {str(relative/name) for name in ('audio.wav','segment.v1.bin','delivery.v1.txt')}
        assert server['data_bytes'] == 3 * sum(x['size'] for x in server['remote_files'].values())
        for name, key in [('audio.wav','wav_sha256'),('segment.v1.bin','metadata_sha256'),('delivery.v1.txt','marker_sha256')]:
            assert digest(received/name) == client['result'][key]
            saved = server['remote_files'][str(relative/name)]
            assert saved['sha256'] == client['result'][key] and saved['size'] == (received/name).stat().st_size
        receipt = json.loads((output/'intake/admissions'/str(ordinal)/'admission.json').read_text())
        assert Path(receipt['path']).resolve() == received.resolve()
        assert receipt['delivery']['source_id'] == client['source_id']
        assert receipt['delivery']['attempt'] == client['attempt']
        assert receipt['delivery']['wav_sha256'] == client['result']['wav_sha256']
        assert receipt['delivery']['metadata_sha256'] == client['result']['metadata_sha256']
        assert receipt['delivery']['sequence'] == ordinal
        observed.append(dict(ordinal=ordinal, source_id=client['source_id'], wav_sha256=client['result']['wav_sha256']))
    native = audit(output, repo, source)
    return dict(schema='ftps-sequence-native-linkage-v1', passed=True,
        wire_report_sha256=expected_report_sha256, actual_received_directories_bound=True,
        separate_loopback_server_sessions=True, host_api_fixtures=wire['host_api_fixtures'],
        production_receiver_deployed=False, device_qualified=False, physical_continuity_proven=False,
        sources=observed, native=native)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('output','repo','source','wire-report','report'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--wire-report-sha256', required=True)
    a = p.parse_args()
    result = verify(a.output.resolve(), a.repo.resolve(), a.source.resolve(), a.wire_report.resolve(), a.wire_report_sha256)
    a.report.write_text(json.dumps(result, sort_keys=True, indent=2)+'\n')
    print(json.dumps(result, sort_keys=True))
