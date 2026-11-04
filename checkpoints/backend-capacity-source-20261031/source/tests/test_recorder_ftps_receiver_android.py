"""Explicit optional host-client smoke, never an Android-device qualification.

Set ANDROID_RECEIVER_SOURCE to an existing recorder source checkout. All actual
FtpsTransfer/FtpsNetwork production sources are compiled unchanged. The copied
host-only WireClient profile is changed from /fixture to the receiver's / root.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_recorder_ftps_receiver import configured, running
from dataclasses import replace


def test_actual_android_production_transport_on_host(configured, tmp_path):
    root = os.environ.get('ANDROID_RECEIVER_SOURCE')
    if not root:
        pytest.skip('set ANDROID_RECEIVER_SOURCE for explicit Android host transport smoke')
    root = Path(root).resolve()
    assert shutil.which('java') and shutil.which('keytool')
    fixtures = root/'app/src/testFixtures/ftps-wire'
    core = root/'app/src/main/java/io/github/headmaster218/recorder/core'
    network = root/'app/src/main/java/io/github/headmaster218/recorder/android/FtpsNetwork.java'
    sources = sorted(fixtures.rglob('*.java')) + sorted(core.glob('*.java')) + [network]
    copied = tmp_path/'WireClient.java'
    client_source = next(p for p in sources if p.name == 'WireClient.java')
    original = client_source.read_text()
    before = 'port, "synthetic", "/fixture"'
    assert original.count(before) == 1
    copied.write_text(original.replace(before, 'port, "synthetic", "/"'))
    sources[sources.index(client_source)] = copied
    pins = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sources if p != copied}
    classes = tmp_path/'classes'; classes.mkdir()
    java = ['java', '-Xmx64m', '-XX:MaxMetaspaceSize=48m', '-XX:ReservedCodeCacheSize=8m',
            '-XX:+UseSerialGC', '-XX:ActiveProcessorCount=1', '-Xss256k']
    result = subprocess.run(java+['-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '8', '-target', '8',
                                 '-Xlint:all,-options', '-Werror', '-d', str(classes), *map(str, sources)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    trust = tmp_path/'trust.p12'
    result = subprocess.run(['keytool', '-importcert', '-noprompt', '-storetype', 'PKCS12',
                             '-keystore', str(trust), '-storepass', 'synthetic', '-alias', 'fixture',
                             '-file', str(configured['config'].cert_file)], capture_output=True, timeout=15)
    assert result.returncode == 0
    config = replace(configured['config'], username='synthetic', password='synthetic')
    with running(config) as (server, owner):
        work = tmp_path/'client'
        command = java+['-Djavax.net.ssl.trustStore='+str(trust), '-Djavax.net.ssl.trustStorePassword=synthetic',
                        '-Djavax.net.ssl.trustStoreType=PKCS12', '-cp', str(classes),
                        'io.github.headmaster218.recorder.android.WireClient', 'success', str(work),
                        str(server.socket.getsockname()[1])]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        report = json.loads(result.stdout)
        assert report['status'] == 'verified' and report['local_unchanged'] and report['all_raw_sockets_closed']
        assert not server.receiver_fault
        attempt = next(iter(owner._attempts.values()))
        assert owner.status(attempt)['status'] == 'published'
        assert (configured['ready']/report['attempt']/'audio.wav').is_file()
        # The original harness receipt hardcodes /fixture; actual configured
        # profile and product FS root are /. Never report that label as evidence.
        report['remote_directory'] = '/'+report['attempt']
        report['fixture_profile_only_adapted'] = True
        report['production_sources_sha256'] = pins
        (tmp_path/'android-receiver-smoke.json').write_text(json.dumps(report, indent=2)+'\n')
