# PUBLIC INSPECTION COPY. Do not execute or import.
"""Bounded loopback HTTP transaction, including slow-drip headers and bodies."""

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")

import http.client
import socket
import threading
import time


def request(port, method, path, payload, seconds=5., cap=512*1024):
    if not 0 < seconds <= 5 or not 0 < cap <= 512*1024:
        raise ValueError('Invalid HTTP bounds')
    end = time.monotonic() + seconds
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=seconds)
    guard = response = timer = None
    expired = threading.Event()
    def interrupt():
        expired.set()
        try: guard.shutdown(socket.SHUT_RDWR)
        except OSError: pass
    def remaining():
        value = end - time.monotonic()
        if expired.is_set() or value <= 0: raise TimeoutError('Whole HTTP request deadline')
        return value
    try:
        connection.connect()
        # A duplicate owns the same endpoint even when getresponse()/read1()
        # close http.client's reference. It is never used to read or write.
        guard = connection.sock.dup()
        timer = threading.Timer(remaining(), interrupt)
        timer.daemon = True
        timer.start()
        connection.sock.settimeout(remaining())
        connection.request(method, path, body=payload,
                           headers={'Content-Type': 'application/json', 'Connection': 'close'})
        remaining()
        response = connection.getresponse()
        remaining()
        expected = response.length
        body = bytearray()
        # isclosed detects exact-length completion before touching an expired
        # response socket. The watchdog, not per-recv timeouts, bounds readline.
        while not response.isclosed():
            remaining()
            block = response.read1(min(8192, cap + 1 - len(body)))
            remaining()
            if not block: break
            body.extend(block)
            if len(body) > cap: raise ValueError('HTTP individual body cap')
        if expected is not None and len(body) != expected:
            raise http.client.IncompleteRead(bytes(body), expected - len(body))
        remaining()
        return response.status, bytes(body)
    except Exception:
        if expired.is_set() or time.monotonic() >= end:
            raise TimeoutError('Whole HTTP request deadline') from None
        raise
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()  # interrupt() only performs one nonblocking shutdown
        if response is not None: response.close()
        connection.close()
        if guard is not None: guard.close()
