#!/usr/bin/env python3
"""Bounded helpers copied from the independently qualified receiver wire fixture.

No native inference, Android device lifecycle, deployment, or persistent secrets.
Run only with reviewed source hashes and an explicit reviewed-fixture flag.
"""
import argparse
from contextlib import contextmanager
import datetime
import ftplib
import hashlib
import importlib.metadata
import io
import ipaddress
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time

ANDROID_PIN = '2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9'
BACKEND_BASE = '90cbacbcea68e8944ad80e864719632f1aa3ac17'
FILES = ('audio.wav', 'segment.v1.bin', 'delivery.v1.txt')
HERE = Path(__file__).resolve().parent


def need(condition, label):
    if not condition:
        raise AssertionError(label)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def inventory(paths):
    answer = {}
    for root in paths:
        root = Path(root)
        if not root.exists():
            answer[str(root)] = None
            continue
        for path in [root, *sorted(root.rglob('*'))]:
            info = path.lstat()
            answer[str(path)] = dict(inode=info.st_ino, mode=info.st_mode,
                bytes=info.st_size if path.is_file() else None,
                sha256=digest(path) if path.is_file() else None)
    return answer


class Guard:
    def __init__(self, work):
        self.work = work
        self.started = time.monotonic()
        self.peak = 0
        self.owned = []

    def check(self):
        need(time.monotonic() - self.started < 600, '600-second suite deadline')
        need(shutil.disk_usage(self.work).free >= 5 * 1024**3, '5GiB disk floor')
        parents, rss = {}, {}
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                parents[int(entry.name)] = int(fields[1])
                rss[int(entry.name)] = int(fields[21]) * os.sysconf('SC_PAGE_SIZE')
            except (OSError, ValueError, IndexError):
                pass
        family = {os.getpid()}
        while True:
            more = {pid for pid, parent in parents.items() if parent in family} - family
            if not more:
                break
            family |= more
        self.peak = max(self.peak, sum(rss.get(pid, 0) for pid in family))
        need(self.peak <= 384 * 1024**2, '384MiB process-tree RSS')
        need(sum(p.stat().st_size for p in self.work.rglob('*') if p.is_file()) <= 8 * 1024**2, '8MiB output')

    def spawn(self, command, log, env=None):
        self.check()
        stream = Path(log).open('x')
        process = subprocess.Popen([str(x) for x in command], stdout=stream, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        self.owned.append((process, stream))
        return process

    def until(self, predicate, label, timeout=5, processes=()):
        end = time.monotonic() + timeout
        while not predicate():
            self.check()
            need(time.monotonic() <= end, 'wait deadline: ' + label)
            need(all(p.poll() is None for p in processes), 'process stopped before: ' + label)
            time.sleep(.02)
        self.check()

    def finish(self, process, timeout=30):
        self.until(lambda: process.poll() is not None, 'process completion', timeout)
        need(process.returncode == 0, 'subprocess exit: ' + str(process.returncode))

    def invoke(self, command, log, timeout=30, env=None):
        process = self.spawn(command, log, env)
        self.finish(process, timeout)
        return process

    def cleanup(self):
        result = []
        for process, stream in self.owned:
            try:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=2)
                result.append(dict(pid=process.pid, reaped=process.poll() is not None,
                    returncode=process.returncode))
            finally:
                stream.close()
        return result


def prepare_tls(keys):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic receiver fixture CA')])
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic loopback receiver')])

    def build(name, public, ca=False):
        builder = x509.CertificateBuilder().subject_name(name).issuer_name(ca_name).public_key(public)
        builder = builder.serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
        builder = builder.not_valid_after(now + datetime.timedelta(days=1))
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
        if not ca:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        return builder.sign(ca_key, hashes.SHA256())
    ca = build(ca_name, ca_key.public_key(), True)
    leaf = build(server_name, server_key.public_key())
    (keys / 'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (keys / 'server.pem').write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (keys / 'server.key').write_bytes(server_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return dict(ca_sha256=ca.fingerprint(hashes.SHA256()).hex(), server_sha256=leaf.fingerprint(hashes.SHA256()).hex())

