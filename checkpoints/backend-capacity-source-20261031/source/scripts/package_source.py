#!/usr/bin/env python3
"""Build a source+evidence archive without models, audio, databases, or environments."""
from pathlib import Path
import argparse
import gzip
import hashlib
import io
import json
import zipfile
import zlib
ROOT=Path(__file__).resolve().parents[1]


# Both the on-disk input and expanded JSON are bounded before parsing. Existing
# evidence includes a 22 MiB plain JSON report; compressed reports are under 2 MiB
# when expanded. Never use gzip.decompress() on an unbounded report.
MAX_STRUCTURED_BYTES = 64 * 1024 * 1024
EVIDENCE_ROOTS = {'reports', 'validation', 'benchmarks'}

# Intentionally reviewed source paths, not paths loaded from a writable manifest.
# Keep in sync with validation/eres2net_v2_identity/public/packaging-manifest.json.
OWNED_IDENTITY_HELPERS = (
    'validation/eres2net_v2_identity/study.py',
    'validation/eres2net_v2_identity/runner.py',
    'validation/eres2net_v2_identity/acquire_model.py',
    'validation/eres2net_v2_identity/write_report.py',
    'validation/eres2net_v2_identity/test_study.py',
    'validation/eres2net_v2_identity/finalize.py',
    'validation/eres2net_v2_identity/parity/acquire.py',
    'validation/eres2net_v2_identity/parity/worker.py',
    'validation/eres2net_v2_identity/parity/run_guarded.py',
    'validation/eres2net_v2_identity/parity/diagnose_frontend.py',
    'validation/eres2net_v2_identity/parity/diagnose_frontend_torch.py',
    'validation/eres2net_v2_identity/author_frontend/corrected.py',
    'validation/eres2net_v2_identity/v1_author_control/control.py',
)
PUBLIC_GZIP_REPORTS = (
    'validation/eres2net_v2_identity/author_frontend/calibration-frozen.json.gz',
    'validation/eres2net_v2_identity/author_frontend/replay.json.gz',
    'validation/eres2net_v2_identity/v1_author_control/calibration-frozen.json.gz',
    'validation/eres2net_v2_identity/v1_author_control/replay.json.gz',
)


def private_path(parts):
    return any(part == 'private' or part.startswith('private_') for part in parts)


def structured_json(data):
    # Duplicate keys could hide an earlier vector while the parsed object appears
    # unchanged, which would otherwise preserve the unsafe original bytes.
    def unique_object(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON object key')
            result[key]=value
        return result

    def invalid_constant(value):
        raise ValueError('Non-finite JSON constant')

    return json.loads(data,object_pairs_hook=unique_object,parse_constant=invalid_constant)


PRIVATE_VECTOR_KEYS = {'speaker_embedding','embedding','embeddings','speaker_embeddings','voice_embedding','voice_embeddings','centroid','speaker_vector','speaker_vectors','voiceprint','voiceprints',
                       'vector','vectors','anchor_vector','anchor_vectors','observation_vector','observation_vectors',
                       'automatic_vector','automatic_vectors','negative_vector','negative_vectors','learned_vector','learned_vectors'}

def contains_vector(value):
    if isinstance(value,str) and value.lstrip().startswith(('[','{')):
        try:return contains_vector(structured_json(value))
        except json.JSONDecodeError:return False
    if isinstance(value,list):
        if value and all(isinstance(x,(int,float)) and not isinstance(x,bool) for x in value):return True
        return any(contains_vector(x) for x in value)
    if isinstance(value,dict):return any(contains_vector(x) for x in value.values())
    return False

def sanitized_json(value):
    """Reports retain scores/provenance, never biometric vector values."""
    if isinstance(value,dict):
        result={}
        for k,v in value.items():
            if k.lower() in PRIVATE_VECTOR_KEYS and contains_vector(v):continue
            # SQLite exports sometimes carry structured JSON as TEXT. Preserve
            # its string type, but sanitize nested private vectors as well.
            if isinstance(v,str) and (k.endswith('_json') or k in {'metadata','detail','provenance','state','payload'}):
                try:
                    parsed=structured_json(v);safe=sanitized_json(parsed)
                    if safe!=parsed:v=json.dumps(safe,ensure_ascii=False)
                except json.JSONDecodeError:pass
            result[k]=sanitized_json(v)
        return result
    if isinstance(value,list):return [sanitized_json(v) for v in value]
    return value

def packaged_bytes(path):
    relative=path.relative_to(ROOT)
    compressed=path.name.endswith('.json.gz')
    if relative.parts[0] not in EVIDENCE_ROOTS or not (path.suffix=='.json' or compressed):
        return path.read_bytes(),False
    try:
        with path.open('rb') as source:
            data=source.read(MAX_STRUCTURED_BYTES+1)
        if len(data)>MAX_STRUCTURED_BYTES:
            raise ValueError('Structured evidence input exceeds byte limit')
        expanded=data
        if compressed:
            with gzip.GzipFile(fileobj=io.BytesIO(data),mode='rb') as source:
                expanded=source.read(MAX_STRUCTURED_BYTES+1)
            if len(expanded)>MAX_STRUCTURED_BYTES:
                raise ValueError('Expanded structured evidence exceeds byte limit')
        original=structured_json(expanded)
        safe=sanitized_json(original)
        if safe!=original:
            sanitized=(json.dumps(safe,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf8')
            if len(sanitized)>MAX_STRUCTURED_BYTES:
                raise ValueError('Sanitized structured evidence exceeds byte limit')
            if compressed:
                output=io.BytesIO()
                # Empty filename and fixed mtime avoid leaking a source filename
                # or producing a different report every time it is packaged.
                with gzip.GzipFile(filename='',mode='wb',fileobj=output,compresslevel=9,mtime=0) as target:
                    target.write(sanitized)
                sanitized=output.getvalue()
            return sanitized,True
    except (ValueError,RecursionError,OSError,EOFError,zlib.error) as exc:
        raise ValueError(f'Invalid structured evidence, refusing to package: {relative}: {exc}') from exc
    # Preserve existing evidence hashes, gzip headers and JSON formatting exactly.
    return data,False

def selected_files():
    roots=['README.md','pyproject.toml','.gitignore','model-manifest.json','native-runtime-manifest.json','overlap-model-manifest.json','dicow-model-manifest.json','moss-td-model-manifest.json','moss-td-cpp-model-manifest.json','requirements-moss-td-cpp.txt','requirements-moss-td.txt','requirements-dicow.txt','requirements-metrics-lock.txt','requirements-overlap.txt','requirements-inference.txt','requirements-lock-linux-py312.txt']
    # The optional namespaced MIT source closure must ship its original notice.
    roots.append('audio_backend/events/_vendor/framemn/LICENSE')
    selected=[ROOT/x for x in roots if (ROOT/x).is_file()]
    # Preserve exact measured, project-owned study entrypoints at their original
    # relative paths; downloaded third-party Python is deliberately not included.
    for name in (*OWNED_IDENTITY_HELPERS,*PUBLIC_GZIP_REPORTS):
        path=ROOT/name
        if path.is_file():selected.append(path)
    for folder in ['audio_backend','scripts','docs','tests','licenses']:
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.md','.json','.txt','.yaml','.yml','.toml'}:
                selected.append(p)
    # Structured test evidence, never retained inputs or operational state.
    for folder in ['reports','validation']:
        for p in (ROOT/folder).rglob('*'):
            parts=p.relative_to(ROOT/folder).parts
            if private_path(parts):
                continue  # Never ship private caches, including redacted unusable stubs.
            if folder=='validation' and parts[0]=='moss-td-runtime':
                continue
            if folder=='validation' and len(parts)>1 and parts[0]=='dicow-research' and parts[1] in {'hub','bf16'}:
                continue  # Downloaded/converted model bundle, not project evidence.
            if folder=='validation' and len(parts)>1 and parts[0]=='moss-td-pilot' and parts[1] in {'model','official-source'}:
                continue  # Pinned upstream assets are fetched, not redistributed here.
            if p.is_file() and (p.suffix in {'.json','.md'} or p.name in {'pytest-final.txt','archive-pytest.txt','pip-check.txt','models-verified.txt'}) and not any(x in {'fixtures','data','checkpoint','snapshot','runtime','source','__pycache__','efficient-sed-research'} or x.endswith('-data') for x in parts):
                selected.append(p)
    # Lightweight source manifests support reproducibility; exclude third-party media,
    # raw gold corpora, private embeddings and licensed research checkpoint trees.
    for p in (ROOT/'benchmarks').rglob('*'):
        parts=p.relative_to(ROOT/'benchmarks').parts
        if p.is_file() and (p.name.endswith('manifest.json') or p.suffix=='.md' or p.name in {'evaluation.json','provenance.json','requirements-lock.txt'}) and not private_path(parts) and 'efficient-sed-research' not in parts:selected.append(p)
    fixtures=ROOT/'validation/fixtures'
    for name in ['manifest.json','cases.json','stress-manifest.json','soak-recipe.json','additional-manifest.json','additional-cases.json']:
        if (fixtures/name).is_file():selected.append(fixtures/name)
    return sorted({p for p in selected if not private_path(p.relative_to(ROOT).parts)},key=lambda p:p.relative_to(ROOT).as_posix())


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=ROOT/'dist/audio-backend-v0.2.0rc1.zip')
    ap.add_argument('--checkpoint',action='store_true',help='Explicit ongoing-work snapshot; not final acceptance')
    args=ap.parse_args()
    if not args.checkpoint and not (ROOT/'reports/FINAL_REPORT.md').is_file():
        ap.error('Final report not present; do not package incomplete evidence')
    files=selected_files()
    manifest={'release_status':'work_in_progress_not_accepted' if args.checkpoint else 'delivery_artifact_see_goal_report','files':[],
              'excludes':'Model binaries, third-party recordings, venv, compiled code, operational databases, credentials, and logs are not included.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    stage=args.output.with_suffix(args.output.suffix+'.partial')
    with zipfile.ZipFile(stage,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in files:
            data,sanitized=packaged_bytes(p);relative=p.relative_to(ROOT).as_posix()
            manifest['files'].append({'path':relative,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'biometric_vector_fields_removed':sanitized})
            z.writestr('audio-backend/'+relative,data)
        z.writestr('audio-backend/BUILD_STATUS.json',json.dumps({'status':manifest['release_status'],'note':'Historical v0.1 reports are intermediate evidence, not acceptance of current goals.'}))
        z.writestr('audio-backend/SOURCE_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(stage) as z:
        bad=z.testzip()
        if bad:raise RuntimeError('Archive verification failed: '+bad)
        for item in manifest['files']:
            if hashlib.sha256(z.read('audio-backend/'+item['path'])).hexdigest()!=item['sha256']:raise RuntimeError('Archive content mismatch')
    stage.replace(args.output)
    result={'path':str(args.output.resolve()),'files':len(files),'bytes':args.output.stat().st_size,'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}
    print(json.dumps(result))
if __name__=='__main__':main()
