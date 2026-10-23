"""Model-free temporary-file binding tests; no real assets or runtime import."""
import hashlib
import json
import time
from pathlib import Path
import pytest
from bindings import runtime_inventory, verify_import_paths, verify_manifest


def row(path):
    data = path.read_bytes()
    return dict(path=str(path), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def fixture(tmp_path):
    runtime = tmp_path/'runtime'; runtime.mkdir()
    (runtime/'bin').mkdir(); (runtime/'lib').mkdir()
    binary = tmp_path/'binary'; binary.write_bytes(b'not executable; only hashed')
    interpreter = runtime/'bin/python'; interpreter.symlink_to(binary)
    cfg = runtime/'pyvenv.cfg'; cfg.write_text('include-system-site-packages = false\n')
    module = runtime/'lib/package.py'; module.write_text('value = 1\n')
    source = tmp_path/'product'; source.mkdir()
    (source/'module.py').write_text('value = 2\n')
    files, links = runtime_inventory(runtime)
    source_files, source_links = runtime_inventory(source)
    value = dict(schema=1, ready=True, complete_transitive_runtime_pinned=False,
                 python_executable_lexical=str(interpreter), binary=row(binary), pyvenv_cfg=row(cfg),
                 runtime_root=str(runtime), runtime_files=files, runtime_links=links,
                 product_source=str(source), source_files=source_files, source_links=source_links,
                 files=[row(module), row(source/'module.py')])
    manifest = tmp_path/'manifest.json'; manifest.write_text(json.dumps(value))
    return manifest, value, interpreter


def verify(manifest, interpreter):
    return verify_manifest(manifest, row(manifest)['sha256'], deadline=time.monotonic()+10,
                           executable=str(interpreter))


def test_valid_closed_fixture_and_lexical_alias_rejected(tmp_path):
    manifest, value, interpreter = fixture(tmp_path)
    assert verify(manifest, interpreter)['ready'] is True
    with pytest.raises(ValueError, match='lexical'):
        verify(manifest, interpreter.resolve())


def test_content_hash_not_just_inventory(tmp_path):
    manifest, value, interpreter = fixture(tmp_path)
    (Path(value['runtime_root'])/'lib/package.py').write_text('value = 9\n')
    with pytest.raises(ValueError, match='hash'): verify(manifest, interpreter)


def test_unpinned_extra_product_file_rejected(tmp_path):
    manifest, value, interpreter = fixture(tmp_path)
    (Path(value['product_source'])/'extra.py').write_text('unexpected')
    with pytest.raises(ValueError, match='inventory'): verify(manifest, interpreter)


def test_closed_cfg_must_not_enable_system_site(tmp_path):
    manifest, value, interpreter = fixture(tmp_path)
    cfg = Path(value['pyvenv_cfg']['path']); cfg.write_text('include-system-site-packages = true\n')
    value['pyvenv_cfg'] = row(cfg); manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='ambient'): verify(manifest, interpreter)


def test_import_path_traversal_and_symlink_escape(tmp_path):
    permitted = tmp_path/'stdlib'; permitted.mkdir()
    ambient = tmp_path/'ambient'; ambient.mkdir()
    escape = str(permitted/'..'/'ambient')
    with pytest.raises(ValueError): verify_import_paths([escape], allowed=[escape])
    link = permitted/'link'; link.symlink_to(ambient)
    with pytest.raises(ValueError): verify_import_paths([str(link)], allowed=[str(link)])
    dangling = permitted/'dangling'; dangling.symlink_to(tmp_path/'missing')
    with pytest.raises(ValueError): verify_import_paths([str(dangling)], allowed=[str(dangling)])
    absent_zip = permitted/'python312.zip'
    verify_import_paths([str(absent_zip)], allowed=[str(absent_zip)])


def test_manifest_parses_only_hashed_read_not_second_path_read(tmp_path, monkeypatch):
    manifest, value, interpreter = fixture(tmp_path)
    original = Path.read_text
    def forbidden(path, *args, **kwargs):
        if path == manifest:
            raise AssertionError('second pathname manifest read forbidden')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    assert verify(manifest, interpreter)['ready'] is True
