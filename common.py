"""Small shared helpers; no network, installation, or recognizer imports."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent

def read(path):
    with Path(path).open('rb') as f:
        data = f.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024: raise ValueError('Metadata byte bound exceeded')
    return json.loads(data)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()

def verify(path, pin):
    p = Path(path)
    if not p.is_file() or p.is_symlink() or p.stat().st_size != pin['bytes'] or sha(p) != pin['sha256']:
        raise ValueError('Asset identity mismatch: ' + str(p))

def backend(root):
    root = Path(root).resolve()
    pins = read(HERE / 'backend-source.json')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()
    if git('rev-parse', 'HEAD') != pins['commit'] or git('rev-parse', 'HEAD^{tree}') != pins['tree']:
        raise ValueError('Required qualified backend commit/tree differs')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Backend checkout must be clean, with no untracked files')
    for row in pins['files']:
        verify(root / row['path'], row)
    return root

def metrics(root):
    p = backend(root) / 'scripts/benchmarks/score_predictions.py'
    spec = importlib.util.spec_from_file_location('qualified_metrics', p)
    module = importlib.util.module_from_spec(spec)
    exec(compile(p.read_bytes(), str(p), 'exec'), module.__dict__)
    return module

def external_output(path, *protected_roots):
    p = Path(path).resolve()
    for root in (HERE, *(Path(r).resolve() for r in protected_roots)):
        if p == root or root in p.parents:
            raise ValueError('Generated data must be outside both Git checkouts')
    return p
