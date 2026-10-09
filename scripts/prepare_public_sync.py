#!/usr/bin/env python3
"""Prepare a small, offline, review-only source update. Never edit the source or Git."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from check_public_tree import ROOT, MAX_BYTES, content_errors


def digest(data):return hashlib.sha256(data).hexdigest()


def prepare(source,output,source_ref=None):
    if any(p.is_symlink() for p in (source,*source.parents)):
        raise ValueError('Symlink source roots are not accepted')
    source=source.resolve(strict=True)
    if source==ROOT or source in ROOT.parents or ROOT in source.parents:
        raise ValueError('Use a separate source tree, not this public repository or its ancestor')
    if output.exists():raise ValueError('Output must be a new directory')
    resolved_output=output.resolve()
    if source==resolved_output or source in resolved_output.parents or ROOT==resolved_output or ROOT in resolved_output.parents:
        raise ValueError('Output must be outside both source and public repository')
    policy=json.loads((ROOT/'PUBLIC_POLICY.json').read_text())
    candidates={};observed={};rows=[];missing=[];source_commit=None
    if source_ref is not None:
        def git(*args):return subprocess.check_output(['git','-C',str(source),*args])
        if Path(git('rev-parse','--show-toplevel').decode().strip()).resolve()!=source:
            raise ValueError('Source must be the Git worktree root')
        source_commit=git('rev-parse','--verify','--end-of-options',source_ref+'^{commit}').decode().strip()
    for name in policy['sync_allowlist']:
        path=source/name
        # Follow no symlinks, including intermediate components.
        if any(p.is_symlink() for p in (path,*path.parents)):
            raise ValueError(f'Symlink rejected: {name}')
        if source_commit is not None:
            entries=git('ls-tree','-z',source_commit,'--',name).split(b'\x00')
            if not entries[0]:missing.append(name);continue
            header,found=entries[0].split(b'\t',1)
            mode,kind,oid=header.decode().split()
            if found.decode()!=name or kind!='blob' or mode not in {'100644','100755'}:
                raise ValueError('Non-regular committed source: '+name)
            if int(git('cat-file','-s',oid))>MAX_BYTES:raise ValueError('Oversized committed source: '+name)
            data=git('cat-file','blob',oid)
        else:
            if not path.is_file():missing.append(name);continue
            if path.stat().st_size > MAX_BYTES:raise ValueError(f'Oversized source file: {name}')
            data=path.read_bytes();observed[name]=digest(data)
        errors=content_errors(name,data)
        if errors:raise ValueError(f'{name}: '+', '.join(errors))
        old=(ROOT/name).read_bytes() if (ROOT/name).is_file() else None
        if old!=data:
            candidates[name]=data
            rows.append({'path':name,'old_sha256':digest(old) if old is not None else None,
                         'new_sha256':digest(data),'bytes':len(data)})
    # A concurrent edit invalidates the proposal before anything is written.
    for name,expected in observed.items():
        if digest((source/name).read_bytes())!=expected:
            raise ValueError(f'Source changed during capture: {name}; retry later')
    if sum(map(len,candidates.values()))>policy['maximum_total_bytes']:
        raise ValueError('Proposal exceeds reviewed source budget')
    output.mkdir(parents=True,exist_ok=False)
    for name,data in candidates.items():
        path=output/'files'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    plan={'schema_version':1,'mode':'proposal_only_not_applied','source_commit':source_commit,'changed_files':rows,'missing_source_files':missing,
          'review_required':'Inspect every diff, approve privacy and scope, then copy selected files into a focused branch. No deletion or version changes are proposed.',
          'exclusions':policy['excluded_categories']}
    (output/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    return {'changed_files':len(rows),'bytes':sum(map(len,candidates.values())),'applied':False,'source_commit':source_commit,'missing_source_files':missing}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--source-ref',help='Read an immutable commit instead of advancing working files');a=p.parse_args()
    print(json.dumps(prepare(a.source_dir,a.output_dir,a.source_ref),indent=2))
if __name__=='__main__':main()
