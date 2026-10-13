"""Source-only receipt amendment; no executable or installation admission.

Replaces the earlier draft's one-shot receipt arithmetic only after a later
reviewed integration. Four preserved assets and 34 new downloads remain separate
facts. The active download has not been accepted by this source preparation.
All functions below inspect supplied data only: no files, assets, packages,
network, databases, processes or guard state are touched. Callers must first
verify source/config/document pins through the later admitted read-only adapter.
Final successful owner-terminal binding is deliberately pending and refusing.
"""

FULL_COUNT = 38
FULL_BYTES = 5150756491
REUSED_COUNT = 4
REUSED_BYTES = 313636022
NEW_COUNT = 34
NEW_BYTES = 4837120469
ASSETS_SHA256 = '90edcc2ad8b791dd77239e739dfc1964503ab0a201379f11f531039df0da8e06'
ROW_KEYS = ('id', 'destination', 'bytes', 'sha256')
IDENTITY_KEYS = ('dev', 'ino', 'mode', 'mtime_ns', 'bytes', 'sha256')
ZERO_FIELDS = ('automatic_retries', 'archives_extracted', 'installs',
               'provider_imports', 'model_loads', 'model_calls')
CATEGORY_CAPS = {'acquisition': 7516192768, 'runtime': 536870912,
                 'proof': 536870912, 'actual': 570425344,
                 'source': 67108864, 'evidence': 67108864}
RESULT_KEYS = frozenset((
    'schema', 'status', 'failure', 'verified_artifacts', 'expected_artifacts',
    'network_bytes', 'attempts_per_missing_asset_this_phase', 'reused_artifacts',
    'reused_verified_bytes', 'newly_received_artifacts', 'newly_received_bytes',
    'final_verified_artifacts', 'final_verified_bytes', 'prior_attempt_preserved',
    'prior_failed_nemotron_request_preserved', 'prior_after_verified',
    'reconciliation', 'automatic_retries', 'ranges', 'archives_extracted',
    'installs', 'provider_imports', 'model_loads', 'model_calls',
    'outer_terminal_required',
))


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def integer(value, expected, reason):
    require(type(value) is int and value == expected, reason)


def closed(value, keys, reason):
    require(type(value) is dict and set(value) == set(keys), reason)


def digest(value):
    require(type(value) is str and len(value) == 64 and
            all(c in '0123456789abcdef' for c in value), 'sha256_shape')
    return value


def relative_name(value):
    require(type(value) is str and 0 < len(value) <= 4096 and
            not value.startswith('/') and '\\' not in value and '\x00' not in value and
            all(p not in ('', '.', '..') for p in value.split('/')), 'relative_name')
    return value


def public_row(value, exact=False):
    require(type(value) is dict, 'artifact_row_type')
    if exact:
        closed(value, ROW_KEYS, 'artifact_row_fields')
    require(type(value['id']) is str and 0 < len(value['id']) <= 96, 'artifact_id')
    relative_name(value['destination'])
    require(type(value['bytes']) is int and 0 < value['bytes'] < 4 * 2 ** 30,
            'artifact_byte_shape')
    digest(value['sha256'])
    return {k: value[k] for k in ROW_KEYS}


def exact_manifest_rows(manifest, contract_rows):
    """Manifest and contract hashes must already be pinned by the future caller."""
    require(manifest['schema'] == 'portable-recovery-assets-v1', 'asset_manifest_schema')
    rows = manifest['artifacts']
    require(type(rows) is list and len(rows) == FULL_COUNT, 'asset_manifest_count')
    expected = [public_row(x) for x in rows]
    require(expected == contract_rows and len({x['id'] for x in expected}) == FULL_COUNT and
            len({x['destination'] for x in expected}) == FULL_COUNT, 'exact_asset_manifest_order')
    integer(sum(x['bytes'] for x in expected), FULL_BYTES, 'full_asset_bytes')
    integer(sum(x['bytes'] for x in expected[:4]), REUSED_BYTES, 'reused_asset_bytes')
    integer(sum(x['bytes'] for x in expected[4:]), NEW_BYTES, 'new_asset_bytes')
    integer(manifest['total_download_bytes'], FULL_BYTES, 'manifest_total_bytes')
    return expected


def validate_continuation(continuation, expected):
    """Additional semantic checks after the pinned bootstrap.continuation gate."""
    require(continuation['schema'] == 'portable-fixed-four-reuse-v1' and
            continuation['assets_sha256'] == ASSETS_SHA256, 'continuation_identity')
    reused = continuation['reused']
    require(type(reused) is list and len(reused) == REUSED_COUNT and
            [public_row(x) for x in reused] == expected[:4], 'exact_reused_prefix')
    for row in reused:
        closed(row, ('id', 'destination', *IDENTITY_KEYS), 'reused_identity_fields')
        require(all(type(row[k]) is int and row[k] >= 0
                    for k in ('dev', 'ino', 'mode', 'mtime_ns', 'bytes')),
                'reused_identity_integer_types')
    require(continuation['remaining_ids'] == [x['id'] for x in expected[4:]],
            'remaining_asset_order')
    integer(continuation['remaining_bytes'], NEW_BYTES, 'continuation_new_bytes')
    integer(continuation['full_count'], FULL_COUNT, 'continuation_full_count')
    integer(continuation['full_bytes'], FULL_BYTES, 'continuation_full_bytes')
    part = continuation['preserved_part']
    integer(part['bytes'], 0, 'original_empty_partial_bytes')
    require(part['sha256'] == 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            'original_empty_partial_hash')
    return reused


def validate_new_result(result, expected):
    """Strict current v2 result shape, with no flattened network-byte total."""
    closed(result, RESULT_KEYS, 'download_v2_result_fields')
    require(result['schema'] == 'portable-download-result-v2' and
            result['status'] == 'VERIFIED_DOWNLOAD_ONLY' and result['failure'] is None,
            'successful_download_v2_result_required')
    require(type(result['verified_artifacts']) is list and
            [public_row(x, exact=True) for x in result['verified_artifacts']] == expected,
            'final_38_hash_size_order_closure')
    counts = {'reused_artifacts': REUSED_COUNT, 'reused_verified_bytes': REUSED_BYTES,
              'newly_received_artifacts': NEW_COUNT, 'newly_received_bytes': NEW_BYTES,
              'final_verified_artifacts': FULL_COUNT, 'final_verified_bytes': FULL_BYTES}
    for key, wanted in counts.items():
        integer(result[key], wanted, 'partition_' + key)
    closed(result['reconciliation'], counts, 'reconciliation_fields')
    for key, wanted in counts.items():
        integer(result['reconciliation'][key], wanted, 'reconciliation_' + key)
    integer(result['network_bytes'], NEW_BYTES, 'new_phase_network_bytes_only')
    integer(result['expected_artifacts'], FULL_COUNT, 'expected_artifact_count')
    integer(result['attempts_per_missing_asset_this_phase'], 1, 'one_new_attempt_per_missing')
    for key in ZERO_FIELDS:
        integer(result[key], 0, 'forbidden_action_' + key)
    require(result['ranges'] is False, 'ranges_not_permitted')
    for key in ('prior_attempt_preserved', 'prior_failed_nemotron_request_preserved',
                'prior_after_verified', 'outer_terminal_required'):
        require(result[key] is True, 'required_flag_' + key)
    return counts


def guard_scope(guard, successful):
    require(guard['status'] == ('PASS' if successful else 'FAIL_CLOSED') and
            guard['cleanup'] == 'CONFIRMED' and guard['original_handle_waited'] is True and
            guard['final_source_pin_verified'] is True and guard['errors'] == [] and
            guard['unresolved_owned'] == [] and guard['final_direct_child_pids'] == [],
            'guard_terminal_and_cleanup')
    integer(guard['exit_code'], 0 if successful else 1, 'guard_exit_code')
    require(guard['category_caps'] == CATEGORY_CAPS and
            all(type(v) is int for v in guard['category_caps'].values()), 'unchanged_category_caps')
    caps = guard['caps']
    work = caps['work_seconds']
    # Authentic retained receipts serialize 10740.0. Exact type excludes bool;
    # the explicit finite interval and equality reject NaN and infinities.
    require(type(work) in (int, float) and -float('inf') < work < float('inf') and
            work == 10740, 'unchanged_guard_work_seconds')
    for key, wanted in (('absolute_seconds', 10800),
                        ('owned_rss_bytes', 268435456), ('scratch_bytes', 12884901888)):
        integer(caps[key], wanted, 'unchanged_guard_' + key)
    require(caps['address_space_soft_hard'] == [1073741824, 1073741824] and
            all(type(x) is int for x in caps['address_space_soft_hard']), 'unchanged_address_space')
    digest(guard['spec_sha256'])


def validate_prior_documents(continuation, documents, expected):
    """Preserve current verify_prior predicates, never its pre-download census.

    This function does not reconstitute the original failed run or certify file
    preservation. The separate later read/hash adapter must retain fixed origin
    map validation, exact identity/byte rehashes and all source/run/asset pins.
    """
    owner = documents['prior_owner']
    guard = documents['prior_guard']
    result = documents['prior_result']
    require(owner['schema'] == 'portable-owner-download-terminal-v1' and
            owner['status'] == 'FAILED_DOWNLOAD_PRESERVED' and
            owner['full38_acquisition_complete'] is False, 'accepted_original_failure')
    integer(owner['verified_asset_bytes'], REUSED_BYTES, 'original_four_verified_bytes')
    require(type(owner['original_tools']['launch']['session_id']) is int,
            'original_launch_handle_record')
    integer(owner['original_tools']['wait']['exit_code'], 1, 'original_failure_owner_reap')
    require(owner['guard_receipt'] == guard and owner['download_result'] == result,
            'original_owner_embedded_evidence')
    guard_scope(guard, successful=False)
    require(result['schema'] == 'portable-download-result-v1' and result['status'] == 'FAILED' and
            result['verified_artifacts'] == expected[:4], 'original_failed_four_result')
    integer(result['network_bytes'], REUSED_BYTES, 'original_network_bytes')
    integer(result['expected_artifacts'], FULL_COUNT, 'original_expected_count')
    for key in ZERO_FIELDS:
        integer(result[key], 0, 'original_no_execution_' + key)
    require(result['ranges'] is False and result['outer_terminal_required'] is True,
            'original_result_scope')
    require(guard['spec_sha256'] == continuation['prior_spec']['sha256'] and
            documents['prior_claim'] == {'schema': 'portable-single-dispatch-v1',
                                        'phase': 'download', 'attempts': 1},
            'original_spent_attempt_bound')
    head = documents['head_diagnostic']
    observation = head['reported_result']
    require(head['schema'] == 'portable-redirect-head-diagnostic-v1' and
            head['failed_download_terminal_sha256'] == continuation['prior_owner']['sha256'] and
            observation['method'] == 'HEAD' and observation['redirect_hostname'] == 'us.aws.cdn.hf.co' and
            observation['redirect_followed'] is False, 'head_diagnostic_is_separate')
    integer(observation['status'], 302, 'head_status')
    integer(observation['payload_read_bytes'], 0, 'head_no_payload')
    receipts = documents['prior_download_receipts']
    require(type(receipts) is list and len(receipts) == 5 and
            [r['artifact_id'] for r in receipts] == [r['id'] for r in expected[:5]],
            'five_original_attempt_receipts')
    for receipt, artifact in zip(receipts[:4], expected[:4]):
        require(receipt['status'] == 'VERIFIED' and receipt['retained_hash_complete'] is True and
                receipt['final_present'] is True and
                receipt['received_sha256'] == receipt['retained_sha256'] == artifact['sha256'],
                'original_success_receipt')
        integer(receipt['received_bytes'], artifact['bytes'], 'original_received_bytes')
        integer(receipt['retained_bytes'], artifact['bytes'], 'original_retained_bytes')
    failed = receipts[4]
    require(failed['status'] == 'FAILED' and failed['final_present'] is False and
            failed['part_retained'] is True, 'original_zero_byte_failure_preserved')
    integer(failed['received_bytes'], 0, 'original_failure_network_zero')
    integer(failed['retained_bytes'], 0, 'original_failure_retained_zero')


def validate_new_guard(guard, exact_spec_sha):
    guard_scope(guard, successful=True)
    require(guard['spec_sha256'] == digest(exact_spec_sha), 'new_guard_exact_spec')
    # Run/config/claim/launcher identity and mutual pins belong to the pending
    # accepted owner terminal. A child PASS does not establish owner acceptance.


def require_accepted_new_owner_terminal(*args, **kwargs):
    """No success branch until the actual new terminal schema is reviewed.

    Later amendment must bind the exact successful owner terminal, its original
    launch/wait/reap evidence, source/config/spec/result/guard pins, preserved
    prior provenance and all 38 fresh post-reap byte/hash observations. It must
    reject missing/pending/failed/unknown cleanup. No old success is inherited.
    """
    raise Refused('NEW_OWNER_TERMINAL_BINDING_PENDING_NOT_ADMITTED')


def require_install_acquisition(manifest, contract_rows, continuation, prior_documents,
                                new_result, new_guard, exact_new_spec_sha,
                                new_owner_terminal):
    expected = exact_manifest_rows(manifest, contract_rows)
    validate_continuation(continuation, expected)
    validate_prior_documents(continuation, prior_documents, expected)
    validate_new_result(new_result, expected)
    validate_new_guard(new_guard, exact_new_spec_sha)
    require_accepted_new_owner_terminal(new_owner_terminal)
    # Unreachable in this source-only revision. No install authority is returned.


def main():
    raise Refused('SOURCE_ONLY_DRAFT_UNADMITTED')


if __name__ == '__main__':
    main()
