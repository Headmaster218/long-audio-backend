"""Real localhost transport controls; no models, product launch, or audio."""
import contextlib
import http.client
import socket
import threading
import time
import unittest
from http_transport import request


@contextlib.contextmanager
def peer(parts):
    listener=socket.socket()
    listener.bind(('127.0.0.1',0)); listener.listen(1); listener.settimeout(2)
    port=listener.getsockname()[1]; stop=threading.Event(); errors=[]
    def serve():
        try:
            with listener.accept()[0] as client:
                client.settimeout(1)
                data=b''
                while b'\r\n\r\n' not in data:
                    block=client.recv(4096)
                    if not block: return
                    data+=block
                    if len(data)>8192: raise AssertionError('Request cap')
                for delay,part in parts:
                    if stop.wait(delay): return
                    client.sendall(part)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception as exc: errors.append(exc)
    worker=threading.Thread(target=serve,daemon=True); worker.start()
    try: yield port
    finally:
        stop.set(); worker.join(3); listener.close()
        if worker.is_alive(): raise AssertionError('Synthetic peer survived')
        if errors: raise errors[0]


class HTTPControls(unittest.TestCase):
    def test_close_exact_content_length(self):
        with peer([(0,b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\n{}')]) as port:
            self.assertEqual(request(port,'GET','/',None),(200,b'{}'))
    def test_zero_content_length(self):
        with peer([(0,b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')]) as port:
            self.assertEqual(request(port,'GET','/',None),(200,b''))
    def test_eof_delimited(self):
        with peer([(0,b'HTTP/1.0 200 OK\r\n\r\n{}')]) as port:
            self.assertEqual(request(port,'GET','/',None),(200,b'{}'))
    def test_truncated_length(self):
        with peer([(0,b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 3\r\n\r\n{}')]) as port:
            with self.assertRaises(http.client.IncompleteRead): request(port,'GET','/',None)
    def test_chunked(self):
        with peer([(0,b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\n\r\n')]) as port:
            self.assertEqual(request(port,'GET','/',None),(200,b'{}'))
    def test_truncated_chunk(self):
        with peer([(0,b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\n{}')]) as port:
            with self.assertRaises(http.client.IncompleteRead): request(port,'GET','/',None)
    def test_body_cap(self):
        with peer([(0,b'HTTP/1.0 200 OK\r\n\r\n12345')]) as port:
            with self.assertRaisesRegex(ValueError,'body cap'): request(port,'GET','/',None,cap=4)
    def drip(self,parts):
        with peer(parts) as port:
            start=time.monotonic()
            with self.assertRaises(TimeoutError): request(port,'GET','/',None,seconds=.25)
            self.assertLess(time.monotonic()-start,1.25)
            self.assertGreaterEqual(time.monotonic()-start,.20)
    def test_slow_drip_headers(self):
        self.drip([(0,b'HTTP/1.1 200 OK\r\nX: ')]+[(.04,b'a')]*50)
    def test_slow_drip_body(self):
        self.drip([(0,b'HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n')]+[(.04,b'a')]*50)
    def test_no_watchdog_thread_leak(self):
        before=set(threading.enumerate())
        with peer([(0,b'HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}')]) as port:
            request(port,'GET','/',None)
        self.assertEqual(set(threading.enumerate()),before)

