"""ROOT-ONLY real process control; find_spec only, no service/model imports."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

BASE = Path('/workspace/scratch/12ec3121e93f')
PRODUCT = BASE/'audio-recovered-commit-diagnostics-rev01-20261023'
PYTHON = BASE/'audio-recovery-runtime-20261023b/bin/python'


@pytest.mark.parametrize('propagate', [False, True])
def test_real_grandchild_module_discovery_from_workspace(propagate):
    assert sys.executable == str(PYTHON), 'root must use lexical closed venv'
    runner = Path(__file__).resolve().parent
    probe = "import importlib.util,json,os,sys; s=importlib.util.find_spec('audio_backend'); print(json.dumps(dict(found=s is not None, origin=None if s is None else s.origin, executable=sys.executable, cwd=os.getcwd(), pythonpath=os.environ.get('PYTHONPATH'))))"
    # Simulate service-side sys.path insertion and then normal NativeProcess
    # grandchild launch. Only install_product_path crosses the process boundary.
    code = "\n".join([
        'import json, os, subprocess, sys',
        f'sys.path.insert(0, {str(runner)!r})',
        'from child_environment import install_product_path',
        f'sys.path.insert(0, {str(PRODUCT)!r})',
        f'propagate = {propagate!r}',
        f'if propagate: install_product_path(os.environ, {str(PRODUCT)!r})',
        f'child = subprocess.run([sys.executable, "-c", {probe!r}], env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)',
        'if child.returncode != 0: raise RuntimeError("model-free grandchild failed")',
        'print(child.stdout, end="")',
    ])
    env = {key: value for key, value in os.environ.items() if not key.startswith(('PYTHON', 'AUDIO_'))}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    parent = subprocess.run([str(PYTHON), '-B', '-c', code], cwd=BASE, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
    assert parent.returncode == 0
    result = json.loads(parent.stdout)
    assert result['found'] is propagate
    assert result['origin'] == (str(PRODUCT/'audio_backend/__init__.py') if propagate else None)
    assert result['executable'] == str(PYTHON)
    assert result['cwd'] == str(BASE)
    assert result['pythonpath'] == (str(PRODUCT) if propagate else None)
