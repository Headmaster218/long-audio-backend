"""Synthetic metadata controls only; no actual qualification approval or launch."""
from dataclasses import asdict
import json
from pathlib import Path
import pytest
from qualification import materialize_target_candidate as tool
from qualification.target_runtime_observation import FIELDS, SCHEMA
from audio_backend import native_owned_cell_store as product


def write(path, value):
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return tool.sha(raw)


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path/'original';(source/'audio_backend').mkdir(parents=True)
    raw = Path(product.__file__).read_bytes()
    (source/tool.MODULE).write_bytes(raw)
    (source/'README.md').write_text('synthetic materializer test only\n')
    manifest = {'schema':'standalone-source-checkpoint-v1', 'files':[
        {'path':str(p.relative_to(source)), 'size':p.stat().st_size, 'sha256':tool.sha(p.read_bytes())}
        for p in sorted(source.rglob('*')) if p.is_file()]}
    q = asdict(product.QUALIFIED_RUNTIMES[0]);observed = {k:q[k] for k in FIELDS}
    observed['interpreter_realpath_sha256'] = '1'*64
    target = dict(schema=SCHEMA, production_profile_activated=False, observed=observed)
    paths = {k:tmp_path/(k+'.json') for k in ('manifest','target','physical','audit','candidate_tests','basis')}
    hashes = {'source_manifest_sha256':write(paths['manifest'],manifest),
        'source_module_sha256':tool.sha(raw), 'target_sha256':write(paths['target'],target)}
    build = {'platform','vfs','filesystem_device','sqlite_source_id','sqlite_version','compile_options_sha256'}
    identity = {k:observed[k] for k in build}
    identity.update(fingerprints={k:v for k,v in observed.items() if k not in build},
                    target_observation_sha256=hashes['target_sha256'])
    physical = dict(passed=True, production_profile_activated=False, power_loss_qualified=False,
        source_manifest={e['path']:e['sha256'] for e in manifest['files']},
        identity=identity, identity_after=identity, source_module_sha256=tool.sha(raw))
    hashes['physical_result_sha256'] = write(paths['physical'],physical)
    audit = dict(status='accepted_production_schema_saved_byte_evidence', production_activation_approved=False,
        exact_recovery=True,evidence_unchanged=True,source_runtime_inventory_matches=True,
        lifecycle_receipts_consistent=True,evidence_inventory={'RESULT.json':{'sha256':hashes['physical_result_sha256']}})
    hashes['audit_sha256'] = write(paths['audit'],audit)
    tests = dict(schema='target-candidate-regression-receipt-v1', tests_passed=9, exit_code=0,
        production_profile_activated=False, **{k:hashes[k] for k in ('target_sha256','audit_sha256',
            'physical_result_sha256','source_module_sha256','source_manifest_sha256')})
    hashes['candidate_tests_sha256'] = write(paths['candidate_tests'],tests)
    basis = dict(schema=tool.BASIS_SCHEMA, accepted_for_fresh_bounded_smoke=True,
        general_production_rollout_approved=False, existing_store_migration_authorized=False, **hashes)
    return dict(source=source, output=tmp_path/'candidate', basis_sha256=write(paths['basis'],basis), **paths)


def alter_basis(inputs, **changes):
    basis = json.loads(inputs['basis'].read_text());basis.update(changes)
    inputs['basis_sha256'] = write(inputs['basis'],basis)


def test_materializes_only_reviewed_identity_literals(inputs):
    original = (inputs['source']/tool.MODULE).read_bytes()
    receipt = tool.materialize(**inputs)
    assert receipt['completed'] is True and receipt['production_profile_activated'] is False
    assert receipt['service_started'] is False
    assert (inputs['source']/tool.MODULE).read_bytes() == original
    assert (inputs['output']/'source/README.md').read_bytes() == (inputs['source']/'README.md').read_bytes()
    updated = (inputs['output']/'source'/tool.MODULE).read_bytes()
    expected = original.replace(product.QUALIFIED_RUNTIMES[0].interpreter_realpath_sha256.encode(),b'1'*64)
    expected = expected.replace(product.QUALIFIED_RUNTIMES[0].evidence_sha256.encode(),inputs['basis_sha256'].encode())
    assert updated == expected
    assert tool.sha(updated) == receipt['candidate_module_sha256']
    assert (inputs['output']/'candidate.patch').exists()


@pytest.mark.parametrize('change', [
    {'schema':'observation'}, {'accepted_for_fresh_bounded_smoke':False},
    {'general_production_rollout_approved':True}, {'existing_store_migration_authorized':True},
    {'unexpected':'field'}, {'source_module_sha256':'0'*64}, {'target_sha256':'0'*64},
    {'audit_sha256':'0'*64}, {'candidate_tests_sha256':'0'*64}, {'source_manifest_sha256':'0'*64}])
def test_unreviewed_or_mismatched_basis_refused_before_output(inputs, change):
    alter_basis(inputs, **change)
    with pytest.raises((ValueError,KeyError)):tool.materialize(**inputs)
    assert not inputs['output'].exists()


def test_wrong_basis_pin_and_existing_output(inputs):
    correct = inputs['basis_sha256'];inputs['basis_sha256']='0'*64
    with pytest.raises(ValueError,match='hash_mismatch'):tool.materialize(**inputs)
    inputs['basis_sha256']=correct;inputs['output'].mkdir()
    marker=inputs['output']/'keep';marker.write_text('keep')
    with pytest.raises(ValueError,match='output_must_be_new'):tool.materialize(**inputs)
    assert marker.read_text()=='keep'


@pytest.mark.parametrize('target', ['basis', 'target', 'manifest', 'source'])
def test_symlinks_refused(inputs, tmp_path, target):
    link=tmp_path/('alias-'+target);link.symlink_to(inputs[target],target_is_directory=target=='source')
    inputs[target]=link
    with pytest.raises(ValueError,match='symlink_refused'):tool.materialize(**inputs)
    assert not inputs['output'].exists()


@pytest.mark.parametrize('name', ['../escape.py', '/absolute.py', '.git/config', 'data.sqlite3',
    'audio.wav', 'x//y.py', 'cache/__pycache__/x.py', 'x\\y.py'])
def test_source_paths_refused(inputs, name):
    manifest=json.loads(inputs['manifest'].read_text());manifest['files'][0]['path']=name
    manifest_hash=write(inputs['manifest'],manifest)
    tests=json.loads(inputs['candidate_tests'].read_text());tests['source_manifest_sha256']=manifest_hash
    alter_basis(inputs,source_manifest_sha256=manifest_hash,candidate_tests_sha256=write(inputs['candidate_tests'],tests))
    with pytest.raises(ValueError,match='source_only_relative'):tool.materialize(**inputs)
    assert not inputs['output'].exists()


def test_changed_source_refused(inputs):
    (inputs['source']/'README.md').write_text('changed')
    with pytest.raises(ValueError,match='source_manifest_mismatch'):tool.materialize(**inputs)
    assert not inputs['output'].exists()


def test_test_receipt_not_success_refused(inputs):
    tests=json.loads(inputs['candidate_tests'].read_text());tests['exit_code']=1
    alter_basis(inputs,candidate_tests_sha256=write(inputs['candidate_tests'],tests))
    with pytest.raises(ValueError,match='candidate_tests_binding'):tool.materialize(**inputs)
    assert not inputs['output'].exists()


def test_bounds_and_multiple_profiles_refused(inputs):
    raw=(inputs['source']/tool.MODULE).read_bytes()
    observed=json.loads(inputs['target'].read_text())['observed']
    with pytest.raises(ValueError,match='reviewed_bounds_changed'):
        tool.patch_profile(raw.replace(b'sector_bytes_max=65536',b'sector_bytes_max=32768'),observed,'1'*64)
    with pytest.raises(ValueError,match='single_runtime'):
        tool.patch_profile(raw.replace(b'sqlite3_library_realpath_sha256=None),)',
            b'sqlite3_library_realpath_sha256=None), None)'),observed,'1'*64)


def test_physical_source_changes_cannot_reuse_review(inputs):
    path=inputs['source']/'README.md';path.write_text('updated source')
    manifest=json.loads(inputs['manifest'].read_text())
    for entry in manifest['files']:
        if entry['path']=='README.md':entry.update(size=path.stat().st_size,sha256=tool.sha(path.read_bytes()))
    manifest_hash=write(inputs['manifest'],manifest)
    tests=json.loads(inputs['candidate_tests'].read_text());tests['source_manifest_sha256']=manifest_hash
    alter_basis(inputs,source_manifest_sha256=manifest_hash,candidate_tests_sha256=write(inputs['candidate_tests'],tests))
    with pytest.raises(ValueError,match='physical_source_manifest_mismatch'):tool.materialize(**inputs)
    assert not inputs['output'].exists()


@pytest.mark.parametrize('changed_field,refused', [('st_atime_ns',False),('st_mtime_ns',True),
    ('st_ctime_ns',True),('st_ino',True),('st_mode',True),('st_nlink',True)])
def test_read_stable_identity_ignores_atime_only(tmp_path,monkeypatch,changed_field,refused):
    from types import SimpleNamespace
    path=tmp_path/'input';path.write_bytes(b'input')
    original=tool.os.fstat;calls=[]
    def observed(fd):
        real=original(fd)
        values={key:getattr(real,key) for key in ('st_dev','st_ino','st_mode','st_nlink',
            'st_uid','st_gid','st_size','st_mtime_ns','st_ctime_ns','st_atime_ns')}
        if calls:values[changed_field]+=1
        calls.append(fd)
        return SimpleNamespace(**values)
    monkeypatch.setattr(tool.os,'fstat',observed)
    if refused:
        with pytest.raises(ValueError,match='input_changed'):tool.read(path)
    else:assert tool.read(path)==b'input'
