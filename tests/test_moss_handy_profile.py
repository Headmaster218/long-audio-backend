"""Standard-library profile verification fixtures; no model/runtime loading."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from audio_backend import moss_handy_profile as p


def retained_directory(label):
    root = Path(os.environ['GUARD_RUN_DIRECTORY']) / 'tmp' if os.environ.get('GUARD_RUN_DIRECTORY') else Path(tempfile.mkdtemp(prefix='moss-fixtures-'))
    target = root / (label + '-' + uuid.uuid4().hex)
    target.mkdir(parents=True, mode=0o700)
    return target


class ProfileTests(unittest.TestCase):
    def test_closed_packaged_profile(self):
        chosen = p.profile(p.NAME)
        self.assertEqual(chosen.payload()['model_bytes'], 986899616)
        self.assertEqual(chosen.payload()['model_sha256'], p.MODEL_SHA256)
        self.assertEqual(chosen.sha256, hashlib.sha256(p.canonical(chosen.payload())).hexdigest())
        changed = chosen.payload(); changed['backend'] = 'vulkan'
        self.assertEqual(chosen.payload()['backend'], 'cpu')

    def test_explicit_name_only(self):
        for value in (None, '', 'default', 'MOSS-Transcribe-Diarize-Q8_0.gguf', True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.profile(value)

    def test_profile_edits_rejected(self):
        for key, value in [('backend', 'vulkan'), ('model_sha256', '0'*64), ('maximum_calls', 2), ('experimental', 1)]:
            data = p.profile(p.NAME).payload(); data[key] = value
            with self.subTest(key=key), patch.object(Path, 'read_bytes', return_value=json.dumps(data).encode()), self.assertRaises(ValueError):
                p.profile(p.NAME)

    def test_model_size_then_hash(self):
        path = retained_directory('profile') / 'synthetic.fixture'
        path.write_bytes(b'abc')
        called = []
        with patch.object(p, 'MODEL_BYTES', 3), patch.object(p, 'MODEL_SHA256', hashlib.sha256(b'abc').hexdigest()):
            self.assertEqual(p.verify_model(path, check=lambda: called.append(True)), path)
        self.assertTrue(called)
        with self.assertRaises(ValueError):
            p.verify_model(path)

    def test_wrong_hash_rejected(self):
        path = retained_directory('bad-hash') / 'synthetic.fixture'; path.write_bytes(b'abc')
        with patch.object(p, 'MODEL_BYTES', 3), self.assertRaises(ValueError): p.verify_model(path)

    def test_symlink_and_link_count_faults(self):
        path = retained_directory('path-fault') / 'synthetic.fixture'; path.write_bytes(b'a')
        with patch.object(Path, 'is_symlink', return_value=True), self.assertRaises(ValueError): p.regular_path(path)
        info = path.stat()
        from types import SimpleNamespace
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=info.st_mode, st_nlink=2)), self.assertRaises(ValueError):
            p.regular_path(path)

    def test_relative_path_rejected(self):
        with self.assertRaises(ValueError): p.regular_path(Path('synthetic.fixture'))

    def test_unsupported_platform_before_runtime_import(self):
        with patch.object(p.sys, 'platform', 'win32'), self.assertRaises(ValueError): p.verify_runtime(p.NAME)

    def test_runtime_manifest_and_wheel_reuse(self):
        from audio_backend import moss_td_cpp_assets as assets
        with patch.object(p.sys, 'platform', 'linux'), patch.object(p.platform, 'machine', return_value='x86_64'), \
                patch.object(p, 'runtime_site_paths', return_value=(Path(__file__).resolve().parent,)), \
                patch.object(p, '_selected_runtime_metadata'), \
                patch.object(assets, 'verify_installed_runtime', return_value=({'fixture': 'path'}, {'fixture': 'hash'})) as verify:
            chosen, paths, hashes = p.verify_runtime(p.NAME)
        self.assertEqual(chosen.name, p.NAME); verify.assert_called_once_with()
        self.assertEqual(paths, {'fixture': 'path'}); self.assertEqual(hashes, {'fixture': 'hash'})

    def test_changed_runtime_manifest_rejected(self):
        from audio_backend import moss_td_cpp_assets as assets
        with patch.object(p.sys, 'platform', 'linux'), patch.object(p.platform, 'machine', return_value='x86_64'), \
                patch.object(assets, 'sha256', return_value='0'*64), patch.object(assets, 'verify_installed_runtime') as verify, self.assertRaises(ValueError):
            p.verify_runtime(p.NAME)
        verify.assert_not_called()


    def test_loader_overrides_and_preload_file_refused(self):
        for name in ('LD_PRELOAD','LD_LIBRARY_PATH','LD_AUDIT','GLIBC_TUNABLES','TRANSCRIBE_LIBRARY'):
            with self.subTest(name=name), patch.dict(os.environ,{name:'synthetic'}), self.assertRaises(ValueError):
                p.check_loader_context()
        with patch.object(Path,'exists',return_value=True), self.assertRaises(ValueError):
            p.check_loader_context()

    def venv(self, config='home = /synthetic-base\ninclude-system-site-packages = false\n', *, sites=True):
        directory = retained_directory('venv-layout')
        executable = directory/'bin'/'python'; executable.parent.mkdir(); executable.write_bytes(b'synthetic, never executed')
        if config is not None:
            (directory/'pyvenv.cfg').write_text(config)
        roots = tuple(dict.fromkeys(Path(p.sysconfig.get_path(key, scheme='posix_prefix',
            vars={'base':str(directory),'platbase':str(directory)})) for key in ('purelib','platlib')))
        if sites:
            for root in roots: root.mkdir(parents=True)
        return executable, roots

    def test_minus_s_venv_resolution_ignores_base_prefix(self):
        executable, roots = self.venv()
        with patch.object(p.sys,'prefix','/synthetic-base'), patch.object(p.sys,'base_prefix','/synthetic-base'):
            self.assertEqual(p.runtime_site_paths(executable),roots)

    def test_venv_missing_sites_never_falls_back(self):
        executable, _ = self.venv(sites=False)
        with self.assertRaises(ValueError): p.runtime_site_paths(executable)

    def test_venv_missing_config_never_falls_back(self):
        executable, _ = self.venv(config=None)
        with self.assertRaises(ValueError): p.runtime_site_paths(executable)

    def test_venv_malformed_and_system_site_configs_rejected(self):
        for config in ('', 'not a key-value configuration', 'home = x\ninclude-system-site-packages = true\n',
                       'home = x\ninclude-system-site-packages = false\ninclude-system-site-packages = false\n',
                       'home = x\ninclude-system-site-packages = maybe\n', 'home = x\n'):
            executable, _ = self.venv(config)
            with self.subTest(config=config), self.assertRaises(ValueError): p.runtime_site_paths(executable)

    def test_venv_alias_fault_rejected_without_links(self):
        executable, _ = self.venv()
        with patch.object(Path,'is_symlink',lambda path:path.name=='pyvenv.cfg'), self.assertRaises(ValueError):
            p.runtime_site_paths(executable)

    def test_selected_metadata_identity_duplicate_and_provider_closure(self):
        from audio_backend import moss_td_cpp_assets as assets
        root=retained_directory('runtime-metadata')
        distributions=[]
        for name in ('transcribe_cpp','transcribe_cpp_native'):
            directory=root/(name+'-0.2.3.dist-info'); directory.mkdir()
            (directory/'METADATA').write_text('Name: '+name+'\nVersion: 0.2.3\n')
            if name=='transcribe_cpp_native':
                (directory/'entry_points.txt').write_text('[transcribe_cpp.native]\ndefault = transcribe_cpp_native:descriptor\n')
            distributions.append(p.importlib.metadata.PathDistribution(directory))
        rows=[{'package':'transcribe-cpp'},{'package':'transcribe-cpp-native'}]
        lookup=lambda name:distributions[0 if name=='transcribe-cpp' else 1]
        with patch.object(assets,'selected_wheels',return_value=rows), \
                patch.object(p.importlib.metadata,'distributions',return_value=distributions) as discovery, \
                patch.object(p.importlib.metadata,'distribution',side_effect=lookup):
            p._selected_runtime_metadata(assets,(root,))
            discovery.assert_called_once_with(path=[str(root)])
        for selected in (distributions+[distributions[0]], distributions[:1], distributions+[distributions[1]]):
            with patch.object(assets,'selected_wheels',return_value=rows), \
                    patch.object(p.importlib.metadata,'distributions',return_value=selected), self.assertRaises(ValueError):
                p._selected_runtime_metadata(assets,(root,))
        outside=retained_directory('outside-metadata')/'transcribe_cpp-0.2.3.dist-info';outside.mkdir()
        with patch.object(assets,'selected_wheels',return_value=rows), \
                patch.object(p.importlib.metadata,'distributions',return_value=distributions), \
                patch.object(p.importlib.metadata,'distribution',return_value=p.importlib.metadata.PathDistribution(outside)), self.assertRaises(ValueError):
            p._selected_runtime_metadata(assets,(root,))


if __name__ == '__main__':
    unittest.main()
