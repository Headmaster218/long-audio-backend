"""Finite exact-product environment controls, no process or model execution."""
import ast
from pathlib import Path
import pytest

from child_environment import install_product_path


def test_installs_only_exact_product_and_preserves_unrelated_values(tmp_path):
    environment = {'PATH': '/fixed', 'PYTHONDONTWRITEBYTECODE': '1'}
    install_product_path(environment, tmp_path)
    assert environment == {'PATH': '/fixed', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(tmp_path)}


@pytest.mark.parametrize('present', ['', '/other', '/other:/test', '/test-target'])
def test_refuses_ambient_or_multiple_roots(tmp_path, present):
    environment = {'PYTHONPATH': present}
    with pytest.raises(ValueError): install_product_path(environment, tmp_path)
    assert environment == {'PYTHONPATH': present}


def test_refuses_home_and_symlink_root(tmp_path):
    with pytest.raises(ValueError): install_product_path({'PYTHONHOME': '/other'}, tmp_path)
    link = tmp_path/'link'; link.symlink_to(tmp_path)
    with pytest.raises(ValueError): install_product_path({}, link)


def test_install_is_after_full_prepare_and_before_app_import():
    text = (Path(__file__).parent/'service_cli.py').read_text()
    tree = ast.parse(text)
    calls = {node.func.id: node.lineno for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id in ('prepare', 'install_product_path', 'run_service')}
    app_import = next(node.lineno for node in ast.walk(tree)
                      if isinstance(node, ast.ImportFrom) and node.module == 'audio_backend.app')
    assert calls['prepare'] < calls['install_product_path'] < app_import < calls['run_service']


def test_initial_import_and_parent_environment_policies_unchanged():
    root = Path(__file__).parent
    old = root.parent/'audio-recovery-runner-rev04-20261023'
    # Rev06 deliberately adds A type dispatch in launch/manifest/parent;
    # the closed verifier and original short uploader/geometry remain unchanged.
    for name in ('bindings.py', 'uploader_cli.py', 'short_input.py'):
        assert (root/name).read_bytes() == (old/name).read_bytes()


def test_authorized_A_dispatch_keeps_exact_existing_modes_and_work_limits():
    root = Path(__file__).parent
    def dictionaries(name):
        values = []
        for node in ast.walk(ast.parse((root/name).read_text())):
            if isinstance(node, ast.Dict):
                try: values.append(ast.literal_eval(node))
                except (ValueError, TypeError): pass
        return values
    qualifications = {'short': 'short-operational', 'A': 'A-operational', 'lifecycle': 'lifecycle-only'}
    assert qualifications in dictionaries('lifecycle_cli.py')
    assert qualifications in dictionaries('prepare_manifest.py')
    assert {'short': 300, 'A': 1800, 'lifecycle': 60} in dictionaries('lifecycle_cli.py')
    tree = ast.parse((root/'launch_binding.py').read_text())
    modes = [ast.literal_eval(node) for node in ast.walk(tree)
             if isinstance(node, ast.Tuple) and all(isinstance(item, ast.Constant) for item in node.elts)]
    assert ('lifecycle-only', 'short-operational', 'A-operational') in modes
