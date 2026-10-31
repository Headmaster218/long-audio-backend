#!/usr/bin/env python3
"""Offline public-source gate. Heuristic checks complement required human review."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'PUBLIC_SOURCE_MANIFEST.json'
POLICY = 'PUBLIC_POLICY.json'
MAX_BYTES = 250_000
PATTERNS = {
    'credential-shaped value': r'(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)',
    'operational absolute path': r'/(?:workspace|home|root|mnt)/[^\s\"\x27`<>]+',
    'private service identifier': r'(?:library[_]file[_]id|library[-]file[:]//|sediment[:]//|file_[0-9a-f]{12,}|file-[A-Za-z0-9]{20,})',
    'subject-level dataset identifier': r'\b(?:MEE|FEE|MEO|FEO)\d{3}\b',
}
DENIED_ROOTS = {'reports','benchmarks','validation','reproductions','models','vendor','data','audio-data','.env','.github'}
VECTOR_KEYS = {'embedding','embeddings','speaker_embedding','speaker_embeddings','voice_embedding','voice_embeddings','centroid','vector','vectors','voiceprint','voiceprints','anchor_vector','observation_vector'}


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def content_errors(name, data):
    errors = []
    parts = PurePosixPath(name).parts
    if not parts or name.startswith('/') or '..' in parts or parts[0] in DENIED_ROOTS:
        errors.append('forbidden path')
    if len(data) > MAX_BYTES:
        errors.append('file exceeds reviewed small-source limit')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return errors + ['binary content']
    if '\x00' in text:
        errors.append('NUL content')
    for reason, pattern in PATTERNS.items():
        if re.search(pattern, text):
            errors.append(reason)
    if name.endswith('.json'):
        try:
            value = json.loads(text)
        except ValueError:
            errors.append('invalid JSON')
        else:
            def has_numeric_list(v):
                if isinstance(v,list):
                    return (bool(v) and all(isinstance(x,(int,float)) for x in v)) or any(has_numeric_list(x) for x in v)
                if isinstance(v,dict):
                    return any(has_numeric_list(x) for x in v.values())
                return False
            def walk(v):
                if isinstance(v,dict):
                    for key,item in v.items():
                        if key.lower() in VECTOR_KEYS and has_numeric_list(item):
                            errors.append('vector values in structured data')
                        walk(item)
                elif isinstance(v,list):
                    for item in v: walk(item)
            walk(value)
    return errors


def make_manifest(files):
    return {
        'schema_version': 1,
        'status': 'work_in_progress_not_accepted',
        'scope': 'Public source files only. This manifest excludes itself to avoid a recursive digest.',
        'files': [{'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
                  for name,data in sorted(files.items()) if name != MANIFEST],
    }


def read_worktree(policy):
    names = git('ls-files','--cached','--others','--exclude-standard','-z').decode().split('\x00')
    names = sorted(set(x for x in names if x))
    files = {}
    for name in names:
        path = ROOT/name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Non-regular or missing source file: {name}')
        if path.stat().st_size > MAX_BYTES: raise ValueError(f'Oversized source file: {name}')
        files[name] = path.read_bytes()
    # New policy-listed files must be audited before staging as well.
    for name in policy['allowed_files']:
        path = ROOT/name
        if name not in files and path.is_file() and not path.is_symlink():
            files[name] = path.read_bytes()
    return files


def read_git_tree(ref):
    if ref == ':':
        rows = git('ls-files','--stage','-z').split(b'\x00')
        values = []
        for row in rows:
            if not row: continue
            header,name = row.split(b'\t',1)
            mode,oid,stage = header.decode().split()
            if stage != '0': raise ValueError('Unmerged index')
            values.append((mode,oid,name.decode()))
    else:
        rows = git('ls-tree','-r','-z',ref).split(b'\x00')
        values=[]
        for row in rows:
            if not row: continue
            header,name=row.split(b'\t',1)
            mode,kind,oid=header.decode().split()
            if kind!='blob': raise ValueError('Submodules or non-blobs are prohibited')
            values.append((mode,oid,name.decode()))
    files={}
    for mode,oid,name in values:
        if mode not in {'100644','100755'}: raise ValueError(f'Non-regular tracked path: {name}')
        if int(git('cat-file','-s',oid)) > MAX_BYTES: raise ValueError(f'Oversized Git object: {name}')
        files[name]=git('cat-file','blob',oid)
    return files


def validate(files, policy, require_manifest=True):
    errors=[]
    allowed=set(policy['allowed_files'])
    for name,data in files.items():
        if name not in allowed: errors.append(f'{name}: not explicitly allowlisted')
        errors.extend(f'{name}: {error}' for error in content_errors(name,data))
    if len(files) > policy['maximum_files'] or sum(map(len,files.values())) > policy['maximum_total_bytes']:
        errors.append('Repository exceeds reviewed source budget')
    if require_manifest:
        if MANIFEST not in files:
            errors.append('Public manifest missing')
        else:
            try:
                if json.loads(files[MANIFEST]) != make_manifest(files): errors.append('Public manifest is stale')
            except ValueError: errors.append('Public manifest is invalid')
        missing=allowed-set(files)
        if missing: errors.append('Allowlisted files missing: '+', '.join(sorted(missing)))
    try:
        import tomllib
        version=tomllib.loads(files['pyproject.toml'].decode())['project']['version']
        module=files['audio_backend/__init__.py'].decode()
        found=re.search(r'__version__\s*=\s*[\"\x27]([^\"\x27]+)',module)
        if not found or found[1]!=version: errors.append('Package version fields disagree')
    except (KeyError,ValueError): errors.append('Version metadata missing or invalid')
    if errors: raise ValueError('\n'.join(errors))
    return {'files':len(files),'bytes':sum(map(len,files.values())),'findings':0,
            'scope':'allowlist, text/binary, private identifiers, secret patterns, structured vectors, manifest and versions',
            'limitation':'Heuristic scan is not a proof of privacy; inspect staged diffs and imported source before sharing.'}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    group=ap.add_mutually_exclusive_group()
    group.add_argument('--staged',action='store_true')
    group.add_argument('--ref')
    group.add_argument('--history',action='store_true')
    group.add_argument('--write-manifest',action='store_true')
    args=ap.parse_args()
    policy=json.loads((ROOT/POLICY).read_text())
    if args.history:
        commits=git('rev-list','--all').decode().splitlines()
        summaries=[]
        for ref in commits:
            files=read_git_tree(ref)
            local_policy=json.loads(files[POLICY]) if POLICY in files else policy
            result=validate(files,local_policy,require_manifest=POLICY in files)
            summaries.append({'commit':ref,**result})
        print(json.dumps({'commits':summaries},indent=2));return
    files=read_git_tree(':' if args.staged else args.ref) if args.staged or args.ref else read_worktree(policy)
    if args.write_manifest:
        validate(files,policy,require_manifest=False)
        files[MANIFEST]=(json.dumps(make_manifest(files),ensure_ascii=False,indent=2)+'\n').encode()
        (ROOT/MANIFEST).write_bytes(files[MANIFEST])
    local_policy=json.loads(files[POLICY]) if POLICY in files else policy
    print(json.dumps(validate(files,local_policy),indent=2))

if __name__=='__main__':
    try:main()
    except (ValueError,subprocess.CalledProcessError) as exc:
        print(f'Public source check failed: {exc}',file=sys.stderr);raise SystemExit(1)
