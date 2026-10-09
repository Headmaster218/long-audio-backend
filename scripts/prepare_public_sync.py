#!/usr/bin/env python3
"""Prepare a small, offline, review-only source update. Never edit the source or Git."""
import argparse
import hashlib
import json
from pathlib import Path
from check_public_tree import ROOT, MAX_BYTES, content_errors


def digest(data):return hashlib.sha256(data).hexdigest()


def prepare(source,output):
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
    candidates={};observed={};rows=[]
    for name in policy['sync_allowlist']:
        path=source/name
        # Follow no symlinks, including intermediate components.
        if any(p.is_symlink() for p in (path,*path.parents)):
            raise ValueError(f'Symlink rejected: {name}')
        if not path.is_file():continue
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
    plan={'schema_version':1,'mode':'proposal_only_not_applied','changed_files':rows,
          'review_required':'Inspect every diff, approve privacy and scope, then copy selected files into a focused branch. No deletion or version changes are proposed.',
          'exclusions':policy['excluded_categories']}
    (output/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    return {'changed_files':len(rows),'bytes':sum(map(len,candidates.values())),'applied':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    print(json.dumps(prepare(a.source_dir,a.output_dir),indent=2))
if __name__=='__main__':main()
