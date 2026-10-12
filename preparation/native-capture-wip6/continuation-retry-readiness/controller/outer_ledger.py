# PORTABLE PREPARATION/RECOVERY SOURCE; not the exact executed artifact.
# OLD contains generic placeholders. Approved local contracts must bind matching roots.
"""Closed four-phase outer-record accounting; never excludes retained bytes."""
import importlib.util
import os
from pathlib import Path
import stat
from common import require, bounded_read, canonical, sha

LOGICAL = 64*1024
PHYSICAL = 256*1024
OLD = (('qualified_fixture', 'run-qualified-fixture-placeholder'), ('failed_continuation', 'run-failed-continuation-placeholder'))
COMMON = {'receipt.json', 'index.json', 'output.log', 'owner-original-wait.json'}
RESULT = {'qualified_fixture': 'continuation-fixtures.json',
          'accounting_fixture': 'accounting-fixtures.json'}

PACKAGE_FILES = {'continuation-readiness': ('PROFILE_AND_BUDGET.json', 'PROPOSED_GO_TRANSITION.json', 'READINESS_PLAN.json', 'SOURCE_FREEZE.json', 'TEST_IDS.json', 'UNEXECUTED_FREEZE_V1.json.zlib', 'approval.json', 'approval.template.json', 'continuation.outer.spec.json', 'continuation.spec.json', 'continuation.template.json', 'controller/bootstrap.py', 'controller/capture.py', 'controller/common.py', 'controller/continuation.py', 'controller/ownership.py', 'controller/server.py', 'fixtures.spec.json', 'test_continuation.py'), 'continuation-retry-readiness': ('ACCOUNTING_FIXTURE_ADMISSION.json', 'ACCOUNTING_FIXTURE_QUALIFICATION.json', 'FAILED_FIRST_CONTINUATION_SEAL.json', 'INDEPENDENT_REVIEW.json', 'LAUNCH_ADMISSION.json', 'MATERIALIZATION.json', 'OUTER_LEDGER.json', 'PROFILE_AND_BUDGET.json', 'PROPOSED_GO_TRANSITION.json', 'READINESS_PLAN.json', 'SOURCE_FREEZE.json', 'TERMINAL_SEAL.json', 'TEST_IDS.json', 'approval.json', 'continuation.outer.spec.json', 'continuation.spec.json', 'continuation.template.json', 'controller/bootstrap.py', 'controller/capture.py', 'controller/common.py', 'controller/continuation.py', 'controller/outer_ledger.py', 'controller/ownership.py', 'controller/server.py', 'fixtures.spec.json', 'test_accounting.py')}


def package_names(name, files, count):
    require(name in PACKAGE_FILES and set(files) <= set(PACKAGE_FILES[name]) and
            count == len(files)+2, "continuation_package_unknown_entry")
    if name == "continuation-readiness":
        require(set(files) == set(PACKAGE_FILES[name]), "immutable_v1_package_file_set")


def remaining(logical, physical):
    require(type(logical) is int and type(physical) is int and
            0 <= logical <= LOGICAL and 0 <= physical <= PHYSICAL, 'outer_ledger_capacity')
    return {'used_logical': logical, 'used_physical': physical,
            'remaining_logical': LOGICAL-logical, 'remaining_physical': PHYSICAL-physical}


def signature(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink,
            s.st_size, s.st_blocks, s.st_mtime_ns, s.st_ctime_ns)


def root_rows(root, role, pins, check):
    allowed = COMMON | ({RESULT[role]} if role in RESULT else set())
    require(role in ('qualified_fixture', 'failed_continuation', 'accounting_fixture', 'continuation'),
            'outer_ledger_role')
    before = root.lstat()
    require(stat.S_ISDIR(before.st_mode) and before.st_uid == os.geteuid() and
            not before.st_mode & 0o022 and root.resolve(strict=True) == root, 'outer_ledger_root_shape')
    names = []
    with os.scandir(root) as entries:
        for entry in entries:
            check()
            require(len(names) < 8 and entry.name in allowed | {'home', 'cache', 'tmp'},
                    'outer_ledger_unknown_file')
            names.append(entry.name)
    require({'home', 'cache', 'tmp', 'index.json', 'output.log'} <= set(names), 'outer_ledger_missing_file')
    if pins is not None:
        require(type(pins) is dict and set(pins) == set(names)-{'home', 'cache', 'tmp'} and
                COMMON <= set(pins) and (role not in RESULT or RESULT[role] in pins),
                'outer_ledger_closed_file_set')
    logical = 0
    physical = max(before.st_size, before.st_blocks*512)
    identities = [('.', signature(before))]
    for name in sorted(names):
        check()
        path = root/name
        old = path.lstat()
        require(old.st_uid == os.geteuid() and old.st_dev == before.st_dev, 'outer_ledger_owner_device')
        if name in ('home', 'cache', 'tmp'):
            require(stat.S_ISDIR(old.st_mode) and not old.st_mode & 0o022, 'outer_ledger_directory')
            if name == 'tmp':
                continue  # Whole tmp is separately charged; it earns no ledger credit.
            with os.scandir(path) as children:
                require(next(children, None) is None, 'outer_ledger_home_cache_not_empty')
        else:
            require(stat.S_ISREG(old.st_mode) and old.st_nlink == 1 and not old.st_mode & 0o111,
                    'outer_ledger_regular_file')
            cap = 12*1024 if name in ('receipt.json', 'index.json') else 4096 if name == 'output.log' else 1024 if name == 'owner-original-wait.json' else 2048
            raw = bounded_read(path, cap, check)
            if pins is not None:
                pin = pins[name]
                require(type(pin) is dict and set(pin) == {'bytes', 'sha256'} and len(raw) == pin['bytes'] and
                        sha(raw) == pin['sha256'], 'outer_ledger_sealed_pin')
            logical += len(raw)
        require(signature(old) == signature(path.lstat()), 'outer_ledger_changed_during_read')
        physical += max(old.st_size, old.st_blocks*512)
        identities.append((name, signature(old)))
    pair_bytes = sum((root/name).lstat().st_size for name in ('receipt.json', 'index.json') if name in names)
    require(pair_bytes <= 12*1024, 'outer_ledger_receipt_index_pair_cap')
    require(signature(before) == signature(root.lstat()), 'outer_ledger_root_changed')
    check()
    return logical, physical, identities


def observe(ledger, prep, current, check):
    require(type(ledger) is dict and set(ledger) == {'schema', 'logical_cap', 'physical_cap', 'closed_roots'} and
            ledger['schema'] == 1 and ledger['logical_cap'] == LOGICAL and ledger['physical_cap'] == PHYSICAL,
            'outer_ledger_contract')
    closed = ledger['closed_roots']
    require(type(closed) is list and len(closed) in (2, 3), 'outer_ledger_root_count')
    prep, current = Path(prep), Path(current)
    require(prep.resolve(strict=True) == prep and current.parent == prep/'runs' and
            current.name.startswith('run-') and len(current.name) <= 40 and
            all(x in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for x in current.name), 'outer_ledger_current_root')
    names, rows, all_identities = set(), [], []
    logical = physical = 0
    for index, item in enumerate(closed + [{'role': 'continuation', 'path': str(current), 'files': None}]):
        require(type(item) is dict and set(item) == {'role', 'path', 'files'}, 'outer_ledger_root_contract')
        require(type(item['files']) is dict if index < len(closed) else item['files'] is None,
                'outer_ledger_closed_pins_required')
        root = Path(item['path'])
        require(root.parent == prep/'runs' and root.name.startswith('run-') and len(root.name) <= 40 and
                all(x in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for x in root.name) and root not in names,
                'outer_ledger_duplicate_or_unrelated_root')
        if index < 2:
            role, basename = OLD[index]
            require(item['role'] == role and root == prep/'runs'/basename, 'outer_ledger_fixed_prior_root')
        elif index < len(closed):
            require(item['role'] == 'accounting_fixture' and root != current, 'outer_ledger_accounting_root')
        else:
            require(root == current and item['files'] is None, 'outer_ledger_current_separation')
        names.add(root)
        size, charge, identities = root_rows(root, item['role'], item['files'], check)
        logical += size
        physical += charge
        rows.append({'path': str(root), 'role': item['role'], 'logical': size, 'physical': charge})
        all_identities.append((str(root), identities))
    result = remaining(logical, physical)
    result.update(roots=rows, identity_sha256=sha(canonical(all_identities)))
    return result


def unchanged(before, after):
    require(before == after, 'outer_ledger_before_after_growth')


def admission(spec, check):
    contract = spec['_continuation']
    prep = Path(contract['prep_root'])
    run = Path(os.environ['GUARD_RUN_DIRECTORY'])
    ledger = contract['outer_ledger']
    before = observe(ledger, prep, run, check)
    desc = importlib.util.spec_from_file_location('continuation_geometry', prep/'storage-proof/geometry.py')
    geometry = importlib.util.module_from_spec(desc)
    desc.loader.exec_module(geometry)
    c = geometry.c
    result = geometry.category(check)
    rows, physical, entries = c.scan(run/'tmp', check)
    package_rows, package_physical, package_entries = {}, 0, 0
    for name in ('continuation-readiness', 'continuation-retry-readiness'):
        require((prep/'build'/name/'controller').is_dir(), 'package_controller_directory')
        files, charge, count = c.scan(prep/'build'/name, check)
        package_names(name, files, count)
        package_rows.update({name+'/'+key: value for key, value in files.items()})
        package_physical += charge
        package_entries += count
    require(physical <= 3*2**20 and entries <= 64, 'continuation_output_envelope')
    require(package_physical <= 2**20 and package_entries <= 49, 'continuation_package_envelope')
    after = observe(ledger, prep, run, check)
    unchanged(before, after)
    require(result['logical_groups']['temp']+3*2**20-sum(rows.values()) <= 16*2**20,
            'continuation_temp_reserve')
    require(result['logical_groups']['build']+2**20-sum(package_rows.values()) <= 15*2**20,
            'continuation_build_reserve')
    require(result['logical_groups']['bookkeeping']+98304+76795+after['remaining_logical'] <= 7*2**20,
            'continuation_bookkeeping_reserve')
    remaining_physical = 3*2**20-physical+2**20-package_physical+after['remaining_physical']+1048576
    require(result['charged_bytes']+result['reserve_bytes']+result['remaining_proof_reservation']+
            remaining_physical <= result['cap_bytes'], 'continuation_category_reserve')
    result.update(deleted_unpinned_cache_logical_extra=76795, deleted_unpinned_cache_physical_extra=1048576,
                  deleted_bytes_reconstructable=False, outer_ledger=after,
                  whole_output_bytes=3*2**20, current_output_physical=physical,
                  aggregate_package_physical=package_physical, remaining_additional_physical=remaining_physical)
    return result
