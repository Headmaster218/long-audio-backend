"""Synthetic local listener for finite ownership controls. NO model or audio."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import signal
import sys

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        raw=json.dumps({'moss_jobs':{'enabled':False}}).encode()
        self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def log_message(self,*args): pass

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);args=p.parse_args()
    server=HTTPServer(('127.0.0.1',args.port),Handler);server.timeout=.1
    stopping=[False]
    signal.signal(signal.SIGTERM,lambda *_:stopping.__setitem__(0,True))
    print(f'Started server process [{os.getpid()}]',file=sys.stderr,flush=True)
    while not stopping[0]:server.handle_request()
    print('Shutting down',file=sys.stderr,flush=True)
    print('Waiting for application shutdown.',file=sys.stderr,flush=True)
    server.server_close()
    print('Application shutdown complete.',file=sys.stderr,flush=True)
    print(f'Finished server process [{os.getpid()}]',file=sys.stderr,flush=True)
