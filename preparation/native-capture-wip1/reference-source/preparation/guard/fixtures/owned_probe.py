"""Two finite non-model integration cases; no sleep or network."""
import os
import subprocess
import sys
from pathlib import Path
mode = sys.argv[1]
if mode == 'success':
    # Retain the original immediately. The outer supervisor owns discovery/reap.
    child = subprocess.Popen([sys.executable, '-I', '-B', '-S', '-c', 'raise SystemExit(0)'])
    code = child.wait(timeout=5)
    if code != 0:
        raise SystemExit(4)
    Path(os.environ['GUARD_RUN_DIRECTORY'], 'probe.txt').write_text('original_child_waited=0\n')
    raise SystemExit(0)
if mode == 'nonzero':
    raise SystemExit(3)
raise SystemExit(5)
