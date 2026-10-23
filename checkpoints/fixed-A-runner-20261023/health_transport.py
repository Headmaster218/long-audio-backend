"""Fixed loopback GETs, bounded decoding, no body logging or redirects."""
import http.client
import json

from http_input import request


def get_health(port, route, *, deadline, ledger, observe=None, connection=http.client.HTTPConnection):
    paths = {'health': '/health', 'observer_health': '/trial-observer-health'}
    path = paths[route]
    def operation(timeout):
        client = connection('127.0.0.1', port, timeout=timeout)
        try:
            client.request('GET', path, headers={'Connection': 'close'})
            response = client.getresponse()
            data = response.read(1024*1024+1)
            if len(data) > 1024*1024:
                raise ValueError('health body bound exceeded')
            return response.status, json.loads(data)
        finally:
            client.close()
    return request('GET', route, operation, deadline=deadline, ledger=ledger, observe=observe)
