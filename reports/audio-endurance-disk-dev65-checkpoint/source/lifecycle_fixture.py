"""Synthetic inherited-lock listener/helper. No models, audio, Store or journal."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import signal
import sys
import time
from audio_backend.resources import InstanceLock

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        raw=json.dumps({'moss_jobs':{'enabled':False}}).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def log_message(self,*args):pass

