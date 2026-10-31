"""Verify the exact source-only checkpoint without external inputs or inference."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parent
manifest=json.loads((root/'SOURCE_MANIFEST.json').read_text())
expected={row['path'] for row in manifest['files']}
actual={str(p.relative_to(root/'source')) for p in (root/'source').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
assert actual==expected,(actual-expected,expected-actual)
for row in manifest['files']:
 data=(root/'source'/row['path']).read_bytes()
 assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'],row['path']
 assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==row['git_blob'],row['path']
print('PASS:',len(expected),'source files; SHA256, Git blob SHA1 and size verified')
