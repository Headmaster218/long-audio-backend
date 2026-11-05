"""Real import/metadata fixtures only; no runtime, model, service or native load."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from audio_backend import moss_handy_profile as profile


@pytest.mark.parametrize('case', [
    'separate', 'same-root', 'wrong-root', 'wrong-file', 'wrong-path',
    'preloaded-runtime', 'missing-distribution', 'duplicate-distribution',
    'duplicate-provider', 'unrelated-provider',
])
def test_pinned_product_and_selected_metadata_only(tmp_path, case):
    product = tmp_path / 'product'
    selected = product if case == 'same-root' else tmp_path / 'selected'
    package = product / 'audio_backend'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('ORIGIN = "pinned-product"\n')
    (package / 'marker.py').write_text('ORIGIN = "pinned-product-submodule"\n')
    (package / 'moss_handy_profile.py').write_bytes(Path(profile.__file__).read_bytes())
    if selected != product:
        (selected / 'audio_backend').mkdir(parents=True)
        (selected / 'audio_backend/__init__.py').write_text('raise RuntimeError("wrong product imported")\n')
        (selected / 'audio_backend/marker.py').write_text('raise RuntimeError("wrong submodule imported")\n')

    def distribution(root, directory, name, provider=False):
        path = root / directory
        path.mkdir()
        (path / 'METADATA').write_text('Name: ' + name + '\nVersion: 0.2.3\n')
        if provider:
            (path / 'entry_points.txt').write_text(
                '[transcribe_cpp.native]\ndefault = transcribe_cpp_native:descriptor\n')
    for root in dict.fromkeys((product, selected)):
        for name in ('transcribe_cpp', 'transcribe_cpp_native'):
            if root == selected and case == 'missing-distribution' and name == 'transcribe_cpp':
                continue
            distribution(root, name + '-0.2.3.dist-info', name, name.endswith('_native'))
    if case == 'duplicate-distribution':
        distribution(selected, 'transcribe_cpp-9.9.dist-info', 'transcribe_cpp')
    if case in ('duplicate-provider', 'unrelated-provider'):
        distribution(selected, 'other-0.0.dist-info', 'other', True)
        if case == 'unrelated-provider':
            (selected / 'other-0.0.dist-info/entry_points.txt').write_text(
                '[transcribe_cpp.native]\nunrelated = untrusted:descriptor\n')
    script = r'''
import sys
sys.pycache_prefix = sys.argv[4]
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from types import SimpleNamespace
import audio_backend
from audio_backend import moss_handy_profile as p
product, selected, case = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
assets = SimpleNamespace(selected_wheels=lambda: [
    {'package': 'transcribe-cpp'}, {'package': 'transcribe-cpp-native'}])
p.runtime_site_paths = lambda: (selected,)
if case == 'separate':
    assert Path(p.importlib.metadata.distribution('transcribe-cpp')._path).parent == product
    try:
        p._selected_runtime_metadata(assets, (selected,))
    except ValueError as exc:
        assert 'escaped the selected roots' in str(exc)
    else:
        raise AssertionError('old mixed-root lookup unexpectedly passed')
if case == 'wrong-file': audio_backend.__file__ = str(selected / 'audio_backend/__init__.py')
if case == 'wrong-path': audio_backend.__path__.append(str(selected / 'audio_backend'))
if case == 'preloaded-runtime': sys.modules['transcribe_cpp'] = SimpleNamespace()
before = list(sys.path)
try:
    sites = p.activate_runtime_sites(selected if case == 'wrong-root' else product)
except ValueError:
    assert case in {'wrong-root', 'wrong-file', 'wrong-path', 'preloaded-runtime'}
    assert sys.path == before
    print('expected-origin-refusal')
else:
    assert case not in {'wrong-root', 'wrong-file', 'wrong-path', 'preloaded-runtime'}
    assert sites == (selected,)
    assert sys.path == [entry for entry in before if entry != str(product)] + [str(selected)]
    assert audio_backend.__file__ == str(product / 'audio_backend/__init__.py')
    assert audio_backend.__path__ == [str(product / 'audio_backend')]
    from audio_backend import marker
    assert marker.ORIGIN == 'pinned-product-submodule'
    assert marker.__file__ == str(product / 'audio_backend/marker.py')
    try:
        p._selected_runtime_metadata(assets, sites)
    except ValueError:
        assert case in {'missing-distribution', 'duplicate-distribution', 'duplicate-provider', 'unrelated-provider'}
        print('expected-selected-closure-refusal')
    else:
        assert case in {'separate', 'same-root'}
        for name in ('transcribe-cpp', 'transcribe-cpp-native'):
            assert Path(p.importlib.metadata.distribution(name)._path).parent == selected
        assert len(list(p.importlib.metadata.entry_points(group='transcribe_cpp.native'))) == 1
        assert not any(name.split('.')[0] in {'transcribe_cpp', 'transcribe_cpp_native'} for name in sys.modules)
        print('pinned-product-selected-metadata')
'''
    command = [os.environ.get('CANDIDATE_TEST_OPTIONAL_PYTHON', sys.executable),
               '-I', '-B', '-S', '-c', script, str(product), str(selected), case,
               str(tmp_path / 'unused-bytecode')]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {'expected-origin-refusal', 'expected-selected-closure-refusal',
                                    'pinned-product-selected-metadata'}


def test_three_entrypoints_activate_before_runtime_verification():
    from audio_backend import recorder_candidate_postphase as post
    from audio_backend import recorder_candidate_resources as resources
    from audio_backend import moss_handy_runner as runner
    # Check the actual fixed factory and both source call sites, without dispatch.
    argv = post.command({'settings': {'python': '/fixture/python', 'model': '/fixture/model'}},
                        Path('/job'), Path('/baseline'), Path('/output/result'), 540)
    bootstrap = argv[5]
    assert 'activate_runtime_sites(' + repr(str(post.ROOT)) + ')' in bootstrap
    assert bootstrap.index('activate_runtime_sites(') < bootstrap.index('verify_runtime(NAME')
    verifier = Path(resources.__file__).read_text()
    assert "'sites = activate_runtime_sites(' + repr(str(postphase.ROOT))" in verifier
    assert "'chosen, paths, hashes = verify_runtime(NAME, sites=sites)" in verifier
    tree = ast.parse(Path(runner.__file__).read_text())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    calls = sorted((node.lineno, node.func.id) for node in ast.walk(main)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {'activate_runtime_sites', 'verify_runtime'})
    assert [name for _, name in calls] == ['activate_runtime_sites', 'verify_runtime']
