"""Isolated CLI and child-launch contract; no Store, model or service execution."""
import ast
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'tools' / 'capacity_resume.py'


@pytest.mark.parametrize('option,code', [('--help', 0), ('--unknown-cli-option', 2)])
def test_direct_isolated_cli_outside_repo(tmp_path, option, code):
    # A caller-controlled cwd/module must not supply capacity_run under -I.
    poison = tmp_path / 'capacity_run.py'
    poison.write_text("raise RuntimeError('caller module must not load')\n")
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    before = set(tmp_path.iterdir())
    result = subprocess.run([sys.executable, '-I', '-B', str(SCRIPT), option],
                            cwd=tmp_path, env=env, capture_output=True, text=True,
                            timeout=15)
    assert result.returncode == code, result.stderr
    assert 'ModuleNotFoundError' not in result.stderr
    assert 'caller module' not in result.stderr
    assert set(tmp_path.iterdir()) == before
    if code == 0:
        assert '--from-clean-pause' in result.stdout
        assert '--retry-plan' in result.stdout
        assert '--attempt' in result.stdout


def test_actual_child_launch_expression_keeps_repo_cwd(tmp_path):
    # Evaluate only the original Popen expression with a recording constructor.
    # This is a narrow launch contract, not a simulated recovery/Store test.
    tree = ast.parse(SCRIPT.read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == 'Popen'
             and isinstance(node.func.value, ast.Name)
             and node.func.value.id == 'subprocess']
    assert len(calls) == 1
    captured = {}
    def record(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
    out = tmp_path / 'attempt'
    namespace = dict(subprocess=SimpleNamespace(Popen=record), sys=sys,
                     REPO=REPO, out=out, fd=123, stdout=object(), stderr=object())
    expr = ast.fix_missing_locations(ast.Expression(calls[0]))
    eval(compile(expr, str(SCRIPT), 'eval'), namespace)
    args, kw = captured['args'], captured['kwargs']
    assert args[0] == [sys.executable, '-I', '-B',
                       '-X', 'pycache_prefix='+str(out/'absent-cache'),
                       str(REPO/'tools/capacity_service.py'),
                       str(out/'config.json'), '123', str(out)]
    assert kw['cwd'] == REPO
    assert kw['pass_fds'] == (123,)
    assert kw['start_new_session'] is True
