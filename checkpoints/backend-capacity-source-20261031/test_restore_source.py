"""Source-only tests: python -m unittest discover -s CHECKPOINT -p test_restore_source.py."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from restore_source import restore, Refused


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkpoint = self.root / 'checkpoint'
        self.source = self.checkpoint / 'source'
        self.source.mkdir(parents=True)
        data = b'print("source only")\n'
        (self.source / 'app.py').write_bytes(data)
        self.row = dict(path='app.py', size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                        sha=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest())
        self.manifest([self.row])
        self.out = self.root / 'restored'

    def manifest(self, rows):
        (self.checkpoint / 'SOURCE_MANIFEST.json').write_text(json.dumps({'files': rows}))

    def test_verify_has_no_output(self):
        self.assertFalse(restore(self.checkpoint)['restored'])
        self.assertFalse(self.out.exists())

    def test_copy_exact_bytes(self):
        self.assertTrue(restore(self.checkpoint, self.out)['restored'])
        self.assertEqual((self.out/'app.py').read_bytes(), (self.source/'app.py').read_bytes())

    def test_existing_output_untouched(self):
        self.out.mkdir()
        (self.out/'keep').write_text('untouched')
        with self.assertRaises(FileExistsError): restore(self.checkpoint, self.out)
        self.assertEqual(list(self.out.iterdir()), [self.out/'keep'])

    def test_bad_hash_before_output(self):
        (self.source/'app.py').write_text('changed')
        with self.assertRaises(Refused): restore(self.checkpoint, self.out)
        self.assertFalse(self.out.exists())

    def test_extra_file(self):
        (self.source/'secret').write_text('synthetic')
        with self.assertRaises(Refused): restore(self.checkpoint, self.out)
        self.assertFalse(self.out.exists())

    def test_source_symlink(self):
        (self.source/'link').symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(Refused): restore(self.checkpoint, self.out)

    def test_output_symlink(self):
        self.out.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(FileExistsError): restore(self.checkpoint, self.out)

    def test_duplicate_path(self):
        self.manifest([self.row, self.row])
        with self.assertRaises(Refused): restore(self.checkpoint, self.out)

    def test_traversal(self):
        self.manifest([{**self.row, 'path':'../escape'}])
        with self.assertRaises(Refused): restore(self.checkpoint, self.out)

    def test_nested_output(self):
        with self.assertRaises(Refused): restore(self.checkpoint, self.source/'nested')

    def test_output_parent_link_into_source(self):
        alias = self.root/'source-alias'
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(Refused): restore(self.checkpoint, alias/'nested')
        self.assertFalse((self.source/'nested').exists())

    def test_missing_source_file(self):
        (self.source/'app.py').unlink()
        with self.assertRaises(Refused): restore(self.checkpoint, self.out)


if __name__ == '__main__': unittest.main()
