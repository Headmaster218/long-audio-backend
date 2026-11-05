"""Prepare fresh, reviewable receiver/finite-analysis configuration; never launch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from . import recorder_capacity_preflight as capacity
from . import recorder_ftps_receiver as receiver
from . import recorder_receiver_publication as publication
from . import recorder_receiver_routing as routing
from . import recorder_analysis_plan as plan
from . import recorder_intake as single
from . import recording_uploader as u

SCHEMA = 'recorder-fresh-deployment-bundle-v1'
require = single.require


def _write(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    u._fsync_dir(path.parent)


def _snapshot(template):
    raw = receiver._private_file(template,16384)
    data = json.loads(raw,object_pairs_hook=receiver._unique_pairs)
    config = receiver._validate_config(data)
    require(config.generations is None, 'bundle_single_generation_template_required')
    files = {str(template): raw}
    for field, bound in (('key_file',65536),('password_file',4096)):
        files[data[field]] = receiver._private_file(Path(data[field]),bound)
    cert = Path(data['cert_file'])
    # The certificate is public, but pin its bytes and identity too.
    fd = os.open(cert,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= 1024*1024,
                'bundle_certificate_bound')
        cert_raw = os.read(fd,1024*1024+1)
        stamp = publication.snapshot._stamp
        require(len(cert_raw) == before.st_size and stamp(os.fstat(fd)) == stamp(before)
                and stamp(cert.stat()) == stamp(before),'bundle_certificate_changed')
    finally:os.close(fd)
    files[str(cert)] = cert_raw
    return data, files


def prepare(*, bundle_dir, endpoint_config, api, generations=3, segment_seconds=300,
            max_attempts=144,max_raw_bytes=8*1024**3,max_hours=12,max_inputs=192,
            phase_max_actions=16,phase_max_polls=192,max_total_actions=32768,
            max_total_polls=4096,max_requests=4096,max_network_writes=1024,
            max_network_bytes=256*1024**2,acknowledge_fresh_only=False,native_turns=False):
    plan.runner.native_export_options(native_turns)
    require(acknowledge_fresh_only is True,'bundle_fresh_acknowledgment_required')
    root = publication._path(str(bundle_dir)); template = publication._path(str(endpoint_config))
    publication._private(root.parent)
    require(not os.path.lexists(root),'bundle_fresh_destination_required')
    require(type(generations) is int and 2 <= generations <= 8,'bundle_generation_bound')
    # Validate API syntax without contacting it or manufacturing a session.
    single.target(api,'operator-selected-unverified','bundle-validation','bundle-capture',0)
    settings = dict(segment_seconds=segment_seconds,max_attempts=max_attempts,max_raw_bytes=max_raw_bytes,
        max_hours=max_hours,max_inputs=max_inputs,generations=generations,
        phase_max_actions=phase_max_actions,phase_max_polls=phase_max_polls,
        max_total_actions=max_total_actions,max_total_polls=max_total_polls,max_requests=max_requests,
        max_network_writes=max_network_writes,max_network_bytes=max_network_bytes)
    report = capacity.profile(**settings)
    if native_turns:settings['native_turns'] = True
    require(report['conditional_profile_fits'],'bundle_profile_does_not_fit')
    data, snapshot = _snapshot(template)
    old = publication.validate_binding(data['binding'],data['inbox_dir'])
    require(not any(root.is_relative_to(Path(old[k])) or Path(old[k]).is_relative_to(root)
        for k in ('state_dir','staging_dir','inbox_dir')),'bundle_existing_storage_overlap')
    require(not any(Path(p).is_relative_to(root) for p in snapshot),'bundle_input_inside_destination')
    # Configure supports fresh default ancillary endpoint limits only. Do not silently lose customization.
    defaults = receiver.Config.__dataclass_fields__
    for key in ('max_connections','max_connections_per_ip','max_readback_bytes','idle_seconds',
                'session_seconds','max_commands','publication_seconds'):
        require(key not in data or data[key] == defaults[key].default,'bundle_custom_endpoint_policy_unsupported')
    def unchanged():
        current, observed = _snapshot(template)
        require(current == data and observed == snapshot,'bundle_template_or_credentials_changed')
    unchanged()
    # Exclusive creation is the mutation boundary. Any later failure preserves this directory.
    root.mkdir(mode=0o700); u._fsync_dir(root.parent)
    configs=[]; records=[]
    for index in range(generations):
        unchanged()
        base=root/('generation-%02d'%index);base.mkdir(mode=0o700)
        raw=base/'raw';ready=base/'inbox';state=base/'publication'
        raw.mkdir(mode=0o700);ready.mkdir(mode=0o700)
        publication.configure(state_dir=state,staging_dir=raw,inbox_dir=ready,exclusive_receiver=True)
        config=root/('receiver-%02d.json'%index)
        args={key:data[key] for key in ('cert_file','key_file','username','password_file')}
        args.update({key:data[key] for key in ('bind_host','port','passive_ports','allow_non_loopback',
            'acknowledge_data_peer_ip_only') if key in data})
        receiver.configure(config_path=config,state_dir=state,inbox_dir=ready,
            max_attempts=max_attempts,max_raw_bytes=max_raw_bytes,**args)
        configs.append(str(config));records.append(dict(inbox=str(ready),publication=str(state),
            runner=str(base/'runner'),job=str(base/'job'),output=str(base/'export')))
    routes=[]
    for index in range(generations):
        route=root/('route-%02d.json'%index)
        routing.configure(endpoint_config=configs[0],generation_configs=configs,active_generation=index,
            output=str(route),auto_advance_generations=True)
        routes.append(str(route))
    control=root/'control';control.mkdir(mode=0o700)
    finite=dict(schema=plan.SCHEMA,state_dir=records[0]['runner'],edges=[dict(route_config=routes[i],
        next_state_dir=records[i]['runner'],next_job_dir=records[i]['job'],next_output_dir=records[i]['output'],
        next_capture_id='capture-%02d'%i,next_session_name='Recorder generation %02d'%i)
        for i in range(1,generations)])
    _write(root/'plan.json',finite);_write(root/'capacity.json',report)
    initial=['python','-I','-B','-m','audio_backend.recorder_inbox_runner','initialize',
        '--state-dir',records[0]['runner'],'--inbox-dir',records[0]['inbox'],'--job-dir',records[0]['job'],
        '--output-dir',records[0]['output'],'--receiver-state-dir',records[0]['publication'],
        '--api',api,'--capture-id','capture-00','--max-hours',str(max_hours),'--max-inputs',str(max_inputs),
        '--exclusive-writer','--acknowledge-span-local-closure']
    budgets={k:settings[k] for k in ('phase_max_actions','phase_max_polls','max_total_actions','max_total_polls',
        'max_requests','max_network_writes','max_network_bytes')}
    commands=dict(launch_requires='Genuinely fresh destination with no outstanding historical attempts; not an in-place replacement of a used endpoint. Preserve existing service/routing for historical readback. Changing producer destination may rescan originals.',check=['python','-I','-B','-m','audio_backend.recorder_ftps_receiver','check','--config',routes[0]],
        serve=['python','-I','-B','-m','audio_backend.recorder_ftps_receiver','serve','--config',routes[0]],
        initialize_prefix=initial,initialize_requires=['--first-delivery','ABSOLUTE_VERIFIED_PUBLISHED_ATTEMPT'],
        analyze=['python','-I','-B','-m','audio_backend.recorder_analysis_plan','--plan-file',str(root/'plan.json'),
            '--control-dir',str(control),'--acknowledge-finite-plan','--exclusive-writer','--acknowledge-span-local-closure',
            *[part for key,value in budgets.items() for part in ('--'+key.replace('_','-'),str(value))]])
    if native_turns:commands['analyze'].append('--native-turns')
    _write(root/'commands.json',commands)
    unchanged()
    for config in configs+routes:receiver.load_config(config)
    manifest=dict(schema=SCHEMA,status='prepared_not_started',settings=settings,commands=commands,
        endpoint_template_sha256=hashlib.sha256(snapshot[str(template)]).hexdigest(),
        files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(root.rglob('*.json'))},network_requests=0,service_started=False,
        sessions_created=False,credentials_copied=False,existing_storage_adopted=False,
        runtime_qualified=False,automatic_retry=False)
    _write(root/'COMPLETE.json',manifest)
    return manifest


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('bundle-dir','endpoint-config','api'):p.add_argument('--'+name,required=True)
    p.add_argument('--native-turns',action='store_true')
    p.add_argument('--acknowledge-fresh-only',action='store_true',required=True)
    for name,default in (('generations',3),('segment-seconds',300),('max-attempts',144),('max-raw-bytes',8*1024**3),
        ('max-hours',12),('max-inputs',192),('phase-max-actions',16),('phase-max-polls',192),
        ('max-total-actions',32768),('max-total-polls',4096),('max-requests',4096),('max-network-writes',1024),
        ('max-network-bytes',256*1024**2)):p.add_argument('--'+name,type=int,default=default)
    try: result=prepare(**vars(p.parse_args(argv)))
    except (Exception,KeyboardInterrupt) as error:
        result=dict(schema=SCHEMA,status='refused',reason='bundle_validation_or_preparation_failed',preserve_partial_bundle=True,automatic_retry=False)
        print(json.dumps(result,sort_keys=True));return 2
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
