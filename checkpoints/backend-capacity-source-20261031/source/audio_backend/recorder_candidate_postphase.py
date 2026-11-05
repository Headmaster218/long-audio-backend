"""Opt-in, finite post-plan candidate drain. Durable whole-attempt reservations.

Primary status is never changed. No inference retry, partial continuation, output
adoption, dependency installation, or control of the primary service is allowed.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from types import SimpleNamespace
import time

from . import recorder_intake as single
from . import recorder_inbox_runner as runner
from . import recorder_overlap_candidates as candidate
from . import recorder_ftps_receiver as receiver
from . import recording_uploader as u
from . import moss_handy_profile as handy
from .native_lexical_alternatives import SourceSupport
from .moss_handy_adapter import project

SCHEMA = 'recorder-finite-candidate-postphase-v1'
CONFIG_SCHEMA = 'recorder-finite-candidate-config-v1'
MiB = 1024**2
MAX_FILE = 2*MiB
MAX_FILES = 192


class KnownFailure(RuntimeError):
    pass
ROOT = Path(__file__).resolve().parents[1]
require = single.require


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(value):
    return digest(u._json(value))


def read(path, cap=MAX_FILE):
    return candidate.read(Path(path), cap)


def document(path, cap=MAX_FILE):
    return json.loads(read(path, cap), object_pairs_hook=receiver._unique_pairs)


def write(path,value):
    raw=u._json(value);require(len(raw)<=MAX_FILE,'candidate_ledger_file_bound')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb',closefd=False) as stream:
            stream.write(raw);stream.flush();os.fsync(fd)
    finally:os.close(fd)
    u._fsync_dir(path.parent)


def file_binding(path, cap=64*MiB, private=False):
    """No-follow streamed source/executable hash with immutable identity checks."""
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, 'candidate_file_alias')
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= cap,
            'candidate_file_kind_or_size')
    if private:single.control(path)
    else:require(info.st_mode & 0o022 == 0, 'candidate_code_writable')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    sha = hashlib.sha256(); count = 0
    try:
        while True:
            block = os.read(fd, 256*1024)
            if not block:break
            count += len(block); require(count <= cap, 'candidate_file_grew'); sha.update(block)
        require(u._signature(info) == u._signature(os.fstat(fd)) == u._signature(path.lstat()),
                'candidate_file_changed')
    finally:os.close(fd)
    return dict(bytes=count, sha256=sha.hexdigest())


def code_binding():
    paths = sorted((ROOT/'audio_backend').rglob('*.py'))
    paths += sorted((ROOT/'audio_backend/profiles').glob('*.json'))
    from .moss_td_cpp_assets import MANIFEST_PATH
    paths = sorted(set(paths + [MANIFEST_PATH]))
    require(1 <= len(paths) <= 1024, 'candidate_code_bound')
    return fingerprint({str(p.relative_to(ROOT)):file_binding(p, 2*MiB) for p in paths})


def policy(config_file, *, enabled, native_turns):
    require(type(enabled) is bool, 'candidate_opt_in_boolean')
    if not enabled:
        require(config_file is None, 'candidate_explicit_opt_in_required')
        return None
    require(native_turns is True and config_file is not None, 'candidate_native_export_and_config_required')
    path = runner._path(str(config_file)); raw = read(path, 16384)
    value = json.loads(raw, object_pairs_hook=receiver._unique_pairs)
    require(type(value) is dict and set(value) == {'schema','python','model','ledger_dir',
        'max_calls','max_wall_seconds','max_output_bytes'} and value['schema'] == CONFIG_SCHEMA,
        'candidate_config_fields')
    for key, lower, upper in (('max_calls', 0, 64), ('max_wall_seconds', 1, 86400),
                              ('max_output_bytes', MiB, 1024*MiB)):
        require(type(value[key]) is int and lower <= value[key] <= upper, 'candidate_budget_bound')
    require(type(value['python']) is str and 0<len(value['python'])<=4096,'candidate_python_required')
    python = Path(value['python'])
    require(python.is_absolute() and python == Path(os.path.abspath(python)) and
            python.parent.resolve(strict=True) == python.parent and os.access(python, os.X_OK),
            'candidate_python_required')
    # Lexical venv selection is retained, while its real executable and config
    # bytes are pinned. Existing runtime verifier forbids system-site fallback.
    executable = python.resolve(strict=True)
    model = runner._path(value['model']); ledger = runner._path(value['ledger_dir'])
    single.private_directory(ledger.parent); runner._outside_git(ledger)
    venv = python.parent.parent/'pyvenv.cfg'
    return dict(schema=SCHEMA, enabled=True, config_file=str(path), config_sha256=digest(raw),
        settings=value, python_executable=str(executable), python_binding=file_binding(executable),
        python_venv_binding=file_binding(venv, 8192) if os.path.lexists(venv) else None,
        profile_name=handy.NAME, profile_sha256=handy.profile(handy.NAME).sha256,
        model_sha256=handy.MODEL_SHA256, model_bytes=handy.MODEL_BYTES,
        runtime_manifest_sha256=handy.RUNTIME_SHA256, code_sha256=code_binding())


def verify_policy(expected):
    actual = policy(expected['config_file'], enabled=True, native_turns=True)
    require(actual == expected, 'candidate_policy_changed')
    handy.check_loader_context()


def validate_locations(policy, plan, config, templates, control, plan_file):
    if policy is None:return
    root = Path(policy['settings']['ledger_dir'])
    protected = [control, Path(plan['state_dir']), Path(config['job_dir']), Path(config['output_dir'])]
    for edge, template in zip(plan['edges'], templates):
        protected += [Path(edge['next_state_dir']), Path(template['job_dir']), Path(template['output_dir'])]
    for cfg in (config,*templates):
        protected.append(Path(cfg['inbox_dir']))
        publication=runner.receiver.validate_binding(cfg['receiver_publication'],cfg['inbox_dir'])
        protected.extend(Path(publication[k]) for k in ('state_dir','staging_dir','inbox_dir'))
    for path in protected:
        require(not root.is_relative_to(path) and not path.is_relative_to(root), 'candidate_ledger_overlap')
    for path in (Path(plan_file), Path(policy['config_file']), Path(policy['settings']['python']),
                 Path(policy['settings']['model']),*(Path(e['route_config']) for e in plan['edges'])):
        require(not path.is_relative_to(root), 'candidate_input_inside_ledger')


def initialize_ledger(policy, plan_intent, *, resumed):
    """Bind the only output root at opt-in, before primary work can begin."""
    root = Path(policy['settings']['ledger_dir'])
    marker = dict(schema=SCHEMA, plan_intent_sha256=fingerprint(plan_intent),
                  policy_sha256=fingerprint(policy), replay_allowed=False)
    if resumed:
        require(os.path.lexists(root), 'candidate_ledger_missing_no_recreate')
        single.private_directory(root)
        require(document(root/'owner.json') == marker, 'candidate_ledger_owner_changed')
        single.control(root/'ledger.lock'); single.control(root/'reservations.json')
    else:
        require(not os.path.lexists(root), 'candidate_ledger_existing_no_adoption')
        root.mkdir(mode=0o700); u._fsync_dir(root.parent)
        write(root/'owner.json', marker)
        write(root/'reservations.json',dict(schema=SCHEMA,attempts=[]))
        fd = os.open(root/'ledger.lock', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd); u._fsync_dir(root)
    return marker


@contextmanager
def source_locks(job):
    locks = []
    try:
        for path in (job/'intake.lock', job/'uploader/uploader.lock'):
            fd = runner.engine.held_lock(path); locks.append((fd, path, u._signature(os.fstat(fd))))
        yield lambda: require(all(u._signature(os.fstat(fd)) == pin == u._signature(path.lstat())
                                  for fd, path, pin in locks), 'candidate_source_lock_changed')
    finally:
        for fd, _, _ in reversed(locks):os.close(fd)


def prepare(job, baseline, policy, *, cancel, verify):
    """Run the candidate's exact local selector; count nonzero calls before launch."""
    for path in (job,baseline):single.private_directory(path)
    require({p.name for p in baseline.iterdir()}=={'complete.json','export.json','transcript.txt','native-turns.json'},
            'candidate_exact_native_export_required')
    end = time.monotonic()+120
    class Cancellation:
        def is_set(self):return bool(cancel())
    event = Cancellation()
    def check():
        require(not event.is_set() and time.monotonic() < end, 'candidate_preflight_cancelled_or_deadline')
        verify()
    with source_locks(job) as locks:
        check(); locks()
        binding, receipts, mapping, work, selection, chosen, pcms, chosen_profile, plan = candidate.prepare(
            job, baseline, None, event, check, auto_native_overlap=True)
        locks(); check()
    # Retain hashes and exact zero facts, never a second selection or PCM archive.
    slices = [dict(range=row, pcm16le_sha256=digest(pcm), frames=len(pcm)//2,
                   float32le_sha256=digest(candidate.float_bytes(candidate.samples_from_pcm(pcm))),
                   exact_zero=not any(pcm)) for row, pcm in zip(chosen, pcms)]
    calls = sum(not row['exact_zero'] for row in slices)
    return dict(plan=plan, source=binding, selection=selection, slices=slices,
        input_mapping=mapping,work_text_coverage=work,
        model_call_count=calls, selected_frames=sum(row['frames'] for row in slices),
        execution_deadline_seconds=(180+360*calls if calls else 0),
        reservation=dict(calls=calls, wall_seconds=(300+360*calls if calls else 0),
                         output_bytes=(16*MiB+6*MiB*len(slices) if calls else 16*MiB)),
        model=dict(path=policy['settings']['model'], sha256=policy['model_sha256'], bytes=policy['model_bytes']),
        profile_sha256=chosen_profile.sha256, runtime_manifest_sha256=policy['runtime_manifest_sha256'])


def cleanup_verified(value):
    return (type(value) is dict and value.get('cleanup_verified') is True and
            value.get('original_handle_waited_and_reaped') is True and value.get('remaining_live_owned') == [] and
            not value.get('cleanup_failure'))


def inventory(root):
    """Exact private, bounded tree: includes owned raw/native evidence, no aliases."""
    single.private_directory(root); result = {}; dirs = []
    for path in sorted(root.rglob('*')):
        require(len(result)+len(dirs) < MAX_FILES, 'candidate_artifact_count')
        relative = str(path.relative_to(root)); info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            single.private_directory(path); dirs.append(relative)
        else:
            raw = read(path); result[relative] = dict(bytes=len(raw), sha256=digest(raw))
    return dict(files=result, directories=dirs)


def completed_result(output, attempt):
    """Validate full successful CLI evidence, never just its summary's status."""
    expected = attempt['preflight']; plan = expected['plan']; slices = expected['slices']
    require(document(output/'plan.json') == plan and document(output/'selection.json') == expected['selection'],
            'candidate_completed_plan_or_selection')
    result = document(output/'complete.json')
    require(result.get('schema') == candidate.SCHEMA and result.get('source') == expected['source'] and
            result.get('automatic_selection') == expected['selection'] and result.get('selected_ranges') == plan['ranges'] and
            result.get('candidate_count') == len(slices) and result.get('model_call_count') == expected['model_call_count'] and
            result.get('model_invoked') is (expected['model_call_count'] > 0) and
            result.get('exact_zero_ranges_skipped') == sum(row['exact_zero'] for row in slices) and
            result.get('status') == ('no_detected_native_overlap' if not slices else 'candidates_recorded') and
            all(result.get(k) is False for k in ('complete_content','speaker_identity_valid','accuracy_verified',
                                                'waveform_separation','replay_allowed')) and
            result.get('primary_baseline') == 'unchanged' and result.get('unselected_audio') == 'not_processed',
            'candidate_completed_summary')
    names = {'plan.json','selection.json','complete.json'}
    plan_sha = digest(u._json(plan))
    missing = 0
    for index, row in enumerate(slices):
        suffix = '%02d' % index
        if row['exact_zero']:
            name = 'skipped-'+suffix+'.json'; names.add(name); item = document(output/name)
            require(item == dict(range=row['range'],state='skipped_exact_zero_pcm',complete_content=False,
                speaker_identity_valid=False,transcripts=[],parse_errors=[],raw_label_spans=None,
                model_invoked=False,model_call_count=0,reason='all_selected_pcm16le_bytes_are_zero',
                source_binding=dict(schema='original-pcm-slice-v1',pcm16le_sha256=row['pcm16le_sha256'],
                    frames=row['frames'],input_mapping=expected['input_mapping'],native_core_attested=False,
                    native_owned_capture_authority=False)), 'candidate_completed_zero')
            continue
        names.update({'call-'+suffix+'.intent.json','call-'+suffix+'.status.json','call-'+suffix+'.raw.txt',
                      'candidate-'+suffix+'.json','owned-'+suffix})
        intent = document(output/('call-'+suffix+'.intent.json'))
        observed = document(output/('call-'+suffix+'.status.json'),262144)
        item = document(output/('candidate-'+suffix+'.json'))
        raw = read(output/('call-'+suffix+'.raw.txt'), 131072)
        span = row['range']; support = SourceSupport(expected['source']['export_sha256'], plan_sha,
            expected['runtime_manifest_sha256'], row['pcm16le_sha256'], expected['source']['frames'],
            span['start_frame'],span['end_frame'],span['start_frame'],span['end_frame'],
            row['pcm16le_sha256'],row['float32le_sha256'])
        intersections=[dict(chunk_id=w['chunk_id'],span_start_frame=w['span_start_frame'],span_end_frame=w['span_end_frame'])
            for w in expected['work_text_coverage'] if w['span_start_frame']<span['end_frame'] and w['span_end_frame']>span['start_frame']]
        require(intent.get('source_binding') == dict(schema='original-pcm-slice-v1',
            support_container_semantics='caller-declared-original-slice-not-native-core',
            native_runtime_field='fixed-MOSS-runtime-profile-identity-not-primary-native-attestation',
            native_owned_capture_authority=False,native_core_attested=False,
            export_declared_primary_work=intersections,input_mapping=expected['input_mapping']),
            'candidate_completed_intent_source')
        require(intent.get('support') == asdict(support) and intent.get('invocation_may_have_started') is True and
            intent.get('replay_allowed') is False and type(intent.get('job_id')) is str and
            re.fullmatch('[a-f0-9]{32}',intent['job_id']) is not None, 'candidate_completed_intent')
        require(observed.get('job_id') == intent['job_id'] and observed.get('state') == 'returned_diagnostic' and
            observed.get('publication_valid') is True and observed.get('invocation_possible') is True and
            observed.get('error_type') is None and observed.get('failure') is None and cleanup_verified(observed.get('cleanup')),
            'candidate_completed_status_or_cleanup')
        tracks, errors = candidate.voice_tracks(raw, support.support_id)
        require(item.get('range') == span and item.get('state') == observed['state'] and
            item.get('diagnostic') == observed.get('result') and item.get('transcripts') == tracks and
            item.get('parse_errors') == errors and item.get('raw_label_spans') == candidate.raw_label_spans(raw) and
            item.get('complete_content') is False and item.get('speaker_identity_valid') is False,
            'candidate_completed_raw_projection')
        require(item.get('source_binding') == dict(schema='original-pcm-slice-v1',input_mapping=expected['input_mapping'],
            export_declared_primary_work=intersections,native_core_attested=False,native_owned_capture_authority=False,
            core_fields_mean='original-slice-bounds-and-PCM-hash',
            native_runtime_field='fixed-MOSS-runtime-profile-identity-not-primary-native-attestation'),
            'candidate_completed_projection_source')
        missing += not tracks or bool(errors)
        owned = output/('owned-'+suffix); single.private_directory(owned)
        allowed = {'request.json','support.wav','raw.bin','result.json'}
        require({p.name for p in owned.iterdir()} == allowed, 'candidate_completed_owned_files')
        request = document(owned/'request.json',16384)
        require(request == dict(job_id=intent['job_id'],support=support.to_dict(),profile_name=handy.NAME,
            profile_sha256=expected['profile_sha256'],model_path=expected['model']['path']), 'candidate_completed_model_profile')
        require(read(owned/'raw.bin',131072) == raw, 'candidate_completed_exact_raw')
        native = document(owned/'result.json',8192)
        require(observed.get('receipt_validated') is True and observed.get('native_receipt') == native,
                'candidate_completed_native_receipt')
        candidate.MossController._validate_return(SimpleNamespace(
            _job=dict(job_id=intent['job_id'],support=support),profile=handy.profile(handy.NAME)),native,raw)
        if native['aborted'] or native['truncated'] or native['error_type'] or native['close_errors']:
            raise KnownFailure('candidate_native_failed_or_incomplete')
        if errors or not tracks or (observed.get('result') or {}).get('schema_errors'):
            raise KnownFailure('candidate_parse_failed_or_missing')
        require(observed.get('result')==project(raw,support=support,profile_name=handy.NAME,
            outcome='returned',truncated=False,aborted=False),'candidate_completed_projection')
        wave = read(owned/'support.wav',640044)
        require(wave[:44] == u.LegacyAppendUploader._header(row['frames']) and
            digest(wave[44:]) == row['pcm16le_sha256'], 'candidate_completed_pcm')
    require({p.name for p in output.iterdir()} == names, 'candidate_completed_exact_files')
    require(result.get('missing_or_unparsed_candidates') == missing, 'candidate_completed_missing_count')
    return result


def validate_entry(entry,prepared):
    names={'preflight.json'}
    if prepared['model_call_count']:
        names.update({'supervisor.json','supervisor.stdout.txt','supervisor.stderr.txt','result'})
    require({p.name for p in entry.iterdir()}==names and document(entry/'preflight.json')==prepared,
            'candidate_entry_exact_files')
    require(sum(v['bytes'] for v in inventory(entry)['files'].values())<=prepared['reservation']['output_bytes'],
            'candidate_artifact_reservation_exceeded')


def known_failure(output):
    """Only positively observed terminal failed calls are known, never exit codes."""
    try:
        if not output.is_dir() or not os.path.lexists(output/'failure.json'):return False
        intents=sorted(output.glob('call-*.intent.json'))
        if not intents:return False
        failed=False
        for path in intents:
            observed=document(output/path.name.replace('.intent.json','.status.json'))
            intent=document(path)
            if observed.get('job_id')!=intent.get('job_id') or not cleanup_verified(observed.get('cleanup')):return False
            if observed.get('state') not in ('returned_diagnostic','failed','cancelled'):return False
            native=observed.get('native_receipt')
            if observed.get('receipt_validated') is not True or type(native) is not dict:return False
            raw=read(output/path.name.replace('.intent.json','.raw.txt'),131072)
            support=SourceSupport(**intent['support'])
            candidate.MossController._validate_return(SimpleNamespace(_job=dict(job_id=intent['job_id'],support=support),
                profile=handy.profile(handy.NAME)),native,raw)
            failed |= observed['state'] in ('failed','cancelled')
        return failed
    except Exception:return False


def command(policy, job, baseline, output, deadline, expected_plan_sha256=None):
    # Both interpreter and complete source closure are checked again immediately
    # before dispatch. The bootstrap contains only fixed trusted module calls.
    bootstrap = ('import sys; sys.pycache_prefix='+repr(str(output.parent/'unused-bytecode'))+'; '
        'sys.path.insert(0, '+repr(str(ROOT))+'); '
        'from audio_backend.moss_handy_profile import NAME, activate_runtime_sites, verify_runtime, verify_model; '
        'sites=activate_runtime_sites('+repr(str(ROOT))+'); '
        'verify_runtime(NAME,sites=sites); verify_model(sys.argv[1]); '
        'from audio_backend.recorder_overlap_candidates import main; '
        'raise SystemExit(main(sys.argv[2:]))')
    return [policy['settings']['python'],'-I','-B','-S','-c',bootstrap,policy['settings']['model'],
        '--job-dir',str(job),'--export-dir',str(baseline),'--model',policy['settings']['model'],
        '--output-dir',str(output),'--auto-native-overlap','--exclusive-inputs','--aggregate-budget-managed',
        '--deadline-seconds',str(min(3600, deadline))] + (
        ['--expected-plan-sha256',expected_plan_sha256] if expected_plan_sha256 is not None else [])


def status(name, **extra):
    return dict(schema=SCHEMA,status=name,primary_baseline='unchanged',complete_content=False,
                accuracy_verified=False,speaker_identity_valid=False,automatic_retry=False,**extra)


def drain(*, policy, plan_intent, generations, api_origin, cancel=None, verify=lambda:None,
          _preflight=None, _supervise=None):
    """A serial finite drain. Every failed/unknown attempt stops all later work."""
    cancel = cancel or (lambda:False)
    root = Path(policy['settings']['ledger_dir']); reports=[]
    try:
        initialize_ledger(policy,plan_intent,resumed=True)
        with _held(root/'ledger.lock') as ledger_lock:
            identity=(root.stat().st_dev,root.stat().st_ino)
            lock_signature=u._signature(os.fstat(ledger_lock))
            anchor=document(root/'reservations.json')
            require(type(anchor) is dict and set(anchor)=={'schema','attempts'} and anchor['schema']==SCHEMA and
                type(anchor['attempts']) is list and len(anchor['attempts'])<=len(generations),'candidate_reservation_anchor')
            def check():
                require(not cancel(), 'candidate_cancelled'); verify(); single.private_directory(root)
                require((root.stat().st_dev,root.stat().st_ino)==identity,'candidate_ledger_replaced')
                require(lock_signature==u._signature(os.fstat(ledger_lock))==u._signature((root/'ledger.lock').lstat()),
                        'candidate_ledger_lock_changed')
                require(document(root/'reservations.json')==anchor,'candidate_reservation_anchor_changed')
                require(document(root/'owner.json') == dict(schema=SCHEMA,plan_intent_sha256=fingerprint(plan_intent),
                    policy_sha256=fingerprint(policy),replay_allowed=False),'candidate_ledger_owner_changed')
            check(); verify_policy(policy)
            allowed={'owner.json','ledger.lock','reservations.json'}
            for i in range(len(generations)):
                allowed.update({'generation-%02d.intent.json'%i,'generation-%02d.complete.json'%i,
                                'generation-%02d.failure.json'%i})
            names={p.name for p in root.iterdir()}
            require(all(n in allowed or re.fullmatch(r'generation-[0-7][0-9]-[a-f0-9]{64}',n) for n in names),
                    'candidate_unknown_ledger_file')
            usage=dict(calls=0,wall_seconds=0,output_bytes=0)
            settings=policy['settings']
            limits=dict(calls=settings['max_calls'],wall_seconds=settings['max_wall_seconds'],output_bytes=settings['max_output_bytes'])
            # A monotonic reservation anchor is committed BEFORE each attempt.
            # Missing tail artifacts cannot turn consumed allowance into fresh work.
            attempts={}; missing_intents=[]
            for i,row in enumerate(anchor['attempts']):
                require(type(row) is dict and set(row)=={'ordinal','attempt_sha256','reservation','output_dir'} and
                    row['ordinal']==i and type(row['attempt_sha256']) is str and
                    re.fullmatch('[a-f0-9]{64}',row['attempt_sha256']),'candidate_reservation_anchor_row')
                charge=row['reservation']
                require(type(charge) is dict and set(charge)==set(usage) and
                    all(type(v) is int and v>=0 for v in charge.values()),'candidate_reservation')
                for key in usage:usage[key]+=charge[key]
                intent_path=root/('generation-%02d.intent.json'%i)
                if not os.path.lexists(intent_path):missing_intents.append(i);continue
                attempt=document(intent_path);attempts[i]=attempt
                require(fingerprint(attempt)==row['attempt_sha256'] and attempt.get('reservation')==charge and
                    attempt.get('output_dir')==row['output_dir'] and attempt.get('schema')==SCHEMA and attempt.get('ordinal')==i and
                    attempt.get('policy_sha256')==fingerprint(policy) and attempt.get('plan_intent_sha256')==fingerprint(plan_intent),
                    'candidate_attempt_owner_or_anchor')
            if missing_intents:
                return status('candidate_unknown',generations=reports,stop_ordinal=missing_intents[0],
                    reserved=usage,replay_allowed=False,reason='reserved_attempt_files_missing')
            owned_names={'owner.json','ledger.lock','reservations.json'}
            for i,attempt in attempts.items():
                prefix='generation-%02d'%i
                entry_path=Path(attempt.get('output_dir',''))
                require(entry_path.parent==root and re.fullmatch(prefix+'-[a-f0-9]{64}',entry_path.name),
                        'candidate_attempt_output')
                owned_names.update({prefix+'.intent.json',prefix+'.complete.json',prefix+'.failure.json',entry_path.name})
                require(not (os.path.lexists(root/(prefix+'.complete.json')) and
                    os.path.lexists(root/(prefix+'.failure.json'))),'candidate_ambiguous_terminal_receipts')
            require(names<=owned_names,'candidate_unowned_ledger_output')
            require(all(usage[k]<=limits[k] for k in usage),'candidate_ledger_budget_changed')
            for i,(job,baseline) in enumerate(generations):
                check(); prefix='generation-%02d'%i
                attempt=attempts.get(i)
                if attempt is not None:
                    if os.path.lexists(root/(prefix+'.failure.json')):
                        failure=document(root/(prefix+'.failure.json'))
                        require(failure==dict(schema=SCHEMA,status='failed_no_retry',
                            attempt_sha256=fingerprint(attempt),replay_allowed=False),'candidate_failure_receipt')
                        return status('candidate_failed',generations=reports,stop_ordinal=i,reserved=usage,replay_allowed=False)
                    if not os.path.lexists(root/(prefix+'.complete.json')):
                        return status('candidate_unknown',generations=reports,stop_ordinal=i,reserved=usage,replay_allowed=False)
                prepared=prepare(Path(job),Path(baseline),policy,cancel=cancel,verify=check)
                key=fingerprint(dict(source=prepared['source'],selection=prepared['selection'],
                    profile=prepared['profile_sha256'],model=prepared['model'],runtime=prepared['runtime_manifest_sha256']))
                entry=root/(prefix+'-'+key)
                expected=dict(schema=SCHEMA,ordinal=i,plan_intent_sha256=fingerprint(plan_intent),
                    policy_sha256=fingerprint(policy),job_dir=str(job),export_dir=str(baseline),
                    output_dir=str(entry),preflight=prepared,reservation=prepared['reservation'],replay_allowed=False)
                if attempt is not None:
                    require(attempt==expected,'candidate_attempt_source_or_selection_changed')
                    completion=document(root/(prefix+'.complete.json'))
                    expected_status='candidates_recorded' if prepared['model_call_count'] else 'no_call'
                    require(completion.get('schema')==SCHEMA and completion.get('status')==expected_status and
                            completion.get('replay_allowed') is False and
                            completion.get('model_call_count')==prepared['model_call_count'] and
                            completion.get('attempt_sha256')==fingerprint(attempt) and
                            completion.get('artifacts')==inventory(entry),'candidate_completion_manifest')
                    validate_entry(entry,prepared)
                    if prepared['model_call_count']:
                        observed=document(entry/'supervisor.json')
                        require(observed.get('status')=='completed' and observed.get('exit_code')==0 and
                                cleanup_verified(observed.get('cleanup')),'candidate_supervisor_cleanup')
                        completed_result(entry/'result',attempt)
                    else:require(completion.get('status')=='no_call','candidate_no_call_receipt')
                    reports.append(dict(ordinal=i,status=completion['status'],reused=True,model_calls=prepared['model_call_count']))
                    continue
                require(not any(j>i for j in attempts),'candidate_ledger_gap')
                # No output created or adopted unless its unique reservation is ours.
                require(not os.path.lexists(entry),'candidate_existing_output_no_adoption')
                if any(usage[k]+prepared['reservation'][k]>limits[k] for k in usage):
                    return status('candidate_budget_exhausted',generations=reports,reserved=usage,next_reservation=prepared['reservation'])
                require(len(u._json(expected))<=MAX_FILE,'candidate_attempt_size')
                gate=None
                if prepared['model_call_count']:
                    if _preflight is None:
                        from .recorder_candidate_resources import preflight as resource_preflight
                    else:resource_preflight=_preflight
                    try:gate=resource_preflight(api_origin, runtime_profile=policy, cancel=cancel)
                    except Exception as exc:
                        return status('candidate_resource_blocked',generations=reports,reserved=usage,
                            reason=getattr(exc,'reason','resource_proof_unavailable'),
                            prerequisite=getattr(exc,'prerequisite','Verify local primary and common bounded CPU-only execution environment.'))
                    verify_policy(policy); check()
                check()
                updated=dict(schema=SCHEMA,attempts=[*anchor['attempts'],dict(ordinal=i,attempt_sha256=fingerprint(expected),
                    reservation=prepared['reservation'],output_dir=str(entry))])
                require(i==len(anchor['attempts']),'candidate_reservation_order')
                write(root/'reservations.next.json',updated)
                os.replace(root/'reservations.next.json',root/'reservations.json');u._fsync_dir(root)
                anchor=updated
                for k in usage:usage[k]+=prepared['reservation'][k]
                write(root/(prefix+'.intent.json'),expected)
                entry.mkdir(mode=0o700);u._fsync_dir(root)
                write(entry/'preflight.json',prepared)
                try:
                    if prepared['model_call_count']:
                        if _supervise is None:
                            from .recorder_candidate_resources import supervise as run_owned
                        else:run_owned=_supervise
                        observation=run_owned(command(policy,job,baseline,entry/'result',prepared['execution_deadline_seconds'],fingerprint(prepared['plan'])),
                            gate=gate,output_dir=entry,deadline_seconds=prepared['execution_deadline_seconds'],
                            cancel=cancel,artifact_cap=prepared['reservation']['output_bytes'])
                        write(entry/'supervisor.json',observation)
                        require(observation.get('status')=='completed' and observation.get('exit_code')==0 and
                            cleanup_verified(observation.get('cleanup')),'candidate_execution_or_cleanup_failed')
                        completed_result(entry/'result',expected)
                    check(); verify_policy(policy)
                    require(prepare(Path(job),Path(baseline),policy,cancel=cancel,verify=check)==prepared,
                            'candidate_source_changed_after_attempt')
                    terminal='candidates_recorded' if prepared['model_call_count'] else 'no_call'
                    validate_entry(entry,prepared)
                    completion=dict(schema=SCHEMA,status=terminal,attempt_sha256=fingerprint(expected),
                        artifacts=inventory(entry),model_call_count=prepared['model_call_count'],replay_allowed=False,
                        no_call_reason=(None if prepared['model_call_count'] else
                            'no_detected_native_overlap' if not prepared['slices'] else 'all_selected_pcm_exact_zero'))
                    # Include the retained root intent/completion and a bounded
                    # monotonic-anchor allowance, not merely the entry subtree.
                    require(len(u._json(completion))<=65536,'candidate_completion_manifest_bound')
                    retained=sum(v['bytes'] for v in completion['artifacts']['files'].values())
                    retained+=len(u._json(expected))+len(u._json(completion))+65536
                    require(retained<=prepared['reservation']['output_bytes'],'candidate_retained_reservation_exceeded')
                    write(root/(prefix+'.complete.json'),completion)
                    reports.append(dict(ordinal=i,status=terminal,reused=False,model_calls=prepared['model_call_count']))
                except BaseException as exc:
                    known=isinstance(exc,KnownFailure) or known_failure(entry/'result')
                    if known:
                        write(root/(prefix+'.failure.json'),dict(schema=SCHEMA,status='failed_no_retry',
                            attempt_sha256=fingerprint(expected),replay_allowed=False))
                    return status('candidate_failed' if known else 'candidate_unknown',generations=reports,
                        stop_ordinal=i,reserved=usage,replay_allowed=False)
            return status('candidate_drain_complete',generations=reports,reserved=usage,
                          model_call_count=sum(r['model_calls'] for r in reports),replay_allowed=False)
    except (Exception,KeyboardInterrupt) as exc:
        return status('candidate_review_required',generations=reports,reason=single.failure_report(exc),replay_allowed=False)


@contextmanager
def _held(path):
    fd=runner.engine.held_lock(path)
    try:yield fd
    finally:
        try:
            if pending_cleanup():
                try:
                    cleanup_notice(status('candidate_cleanup_incomplete',primary_status='plan_complete',
                        requires_operator_attention=True,original_owner_retained=True,new_work_allowed=False,
                        detail='Cleanup remains unverified. The original owner retains plan and ledger locks and only retries owned-child cleanup; no primary control or inference retry occurs.'))
                finally:
                    # Output failure must never release a live owner's locks.
                    retain_cleanup()
                cleanup_notice(status('candidate_cleanup_complete',primary_status='plan_complete',
                    new_work_allowed=False,automatic_inference_retry=False))
        finally:os.close(fd)


def cleanup_notice(value):
    """Best-effort reporting is never a prerequisite for positive cleanup."""
    try:print(json.dumps(value),file=sys.stderr,flush=True)
    except BaseException:pass


def pending_cleanup():
    """Do not import optional supervision just to read a completed/no-call result."""
    module=sys.modules.get(__package__+'.recorder_candidate_resources')
    return bool(module is not None and module.pending_cleanup())


def retain_cleanup(*, _module=None, _sleep=time.sleep):
    """Exceptional ownership retention only: no new job, model or primary action.

    Normal inference work remains bounded. Unknown owned-child cleanup can keep
    this original CLI owner alive until intervention restores positive cleanup.
    SIGINT/TERM cannot turn unresolved ownership into an apparently clean exit.
    """
    module=_module or sys.modules.get(__package__+'.recorder_candidate_resources')
    if module is None or not module.pending_cleanup():return False
    # The original Popen owner retains its nonthrowing signal handlers from
    # spawn through pending cleanup, and restores them only after positive reap.
    # Do not replace those handlers or re-install an obsolete snapshot here.
    while module.pending_cleanup():
        try:
            if module.retry_pending_cleanup():break
        except BaseException:pass
        try:_sleep(5)
        except BaseException:pass
    return True
