"""Verify the complete standalone source snapshot without importing product code."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
entries = json.loads((root / "SOURCE_MANIFEST.json").read_text())["files"]
expected = {e["path"] for e in entries}
actual = {str(p.relative_to(root / "source")) for p in (root / "source").rglob("*") if p.is_file()}
if actual != expected:
    raise SystemExit(f"File set mismatch: missing={expected - actual}, extra={actual - expected}")
for entry in entries:
    data = (root / "source" / entry["path"]).read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"] or blob != entry["sha"]:
        raise SystemExit(f"Content mismatch: {entry['path']}")
print(f"Verified {len(entries)} files, {sum(e['size'] for e in entries)} bytes")
