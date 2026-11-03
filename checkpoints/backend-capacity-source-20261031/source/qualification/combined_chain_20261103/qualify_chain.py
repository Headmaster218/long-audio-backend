"""Reviewed bounded FTPS callback -> native watch -> private export qualification.
No deployment, microphone, device, accuracy or whole-capture claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import wave
import stat

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'qualification'), str(ROOT)]
from capacity_run import Gate, save, digest_file, source_pins
import qualify_local_inbox_loopback as baseline
import qualify_local_inbox_watch as watchbase
from strict_incremental_span_probe import inventory, need, verify_status


def load(path): return json.loads(Path(path).read_bytes())

def check_pins(pins):
    need(type(pins) is list and bool(pins), 'pins_empty')
    need(len({row['path'] for row in pins}) == len(pins), 'pins_duplicate')
    for row in pins:
        path = Path(row['path'])
        need(path.is_file() and not path.is_symlink() and path.stat().st_size == row['bytes']
             and digest_file(path) == row['sha256'], 'pin_mismatch:'+str(path))



def check_inventories(spec):
    roles = {'source', 'runtime', 'classes', 'dependencies', 'test_tools'}
    need(set(spec['inventories']) == roles, 'inventory_roles')
    roots = {}
    for role, binding in spec['inventories'].items():
        manifest = Path(binding['path'])
        need(manifest.is_file() and not manifest.is_symlink() and digest_file(manifest) == binding['sha256'], 'inventory_manifest:'+role)
        root = Path(binding['root'])
        need(root.is_dir() and not root.is_symlink() and root.resolve() == root, 'inventory_root:'+role)
        roots[role] = root
        record = load(manifest); rows = record['files']
        need(bool(rows), 'inventory_empty:'+role)
        expected = {}
        for row in rows:
            relative = Path(row['path'])
            need(not relative.is_absolute() and '..' not in relative.parts and str(relative) not in expected, 'inventory_relative:'+role)
            expected[str(relative)] = row
        actual = {}; links = {}
        for path in root.rglob('*'):
            relative = str(path.relative_to(root)); mode = path.lstat().st_mode
            if stat.S_ISLNK(mode): links[relative] = os.readlink(path)
            elif stat.S_ISREG(mode):
                if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'): continue
                actual[relative] = path
            elif not stat.S_ISDIR(mode): raise AssertionError('inventory_special:'+role)
        need(set(actual) == set(expected), 'inventory_roster:'+role)
        need(links == {row['path']:row['target'] for row in record.get('symlinks', [])}, 'inventory_symlinks:'+role)
        for relative, path in actual.items():
            row = expected[relative]
            need(path.stat().st_size == row.get('bytes', row.get('size')) and digest_file(path) == row['sha256'], 'inventory_hash:'+role+':'+relative)
    need(roots['source'] == ROOT.resolve(), 'source_root_binding')
    need(roots['runtime'] == Path(sys.prefix).resolve(), 'runtime_root_binding')
    for role in ('classes', 'dependencies', 'test_tools'):
        need(roots[role] == Path(spec[role]).resolve(), 'spec_root_binding:'+role)


def validate_producer(spec):
    from audio_backend.recorder_metadata import decode
    from strict_uploader_smoke import validate_input
    manifest = Path(spec['manifest']); wav = Path(spec['input_wav'])
    need(not manifest.is_symlink() and not wav.is_symlink(), 'producer_symlink')
    need(digest_file(manifest) == spec['manifest_sha256'], 'producer_manifest_pin')
    metadata = decode(manifest.read_bytes())
    need(metadata['frames'] == metadata['target_frames'] == 144000 and
         metadata['sequence'] == metadata['frame_start'] == 0 and
         metadata['sample_rate_hz'] == 16000 and metadata['channels'] == 1 and
         metadata['close_reason'] == 'STOP' and
         metadata['capture_id'] == 'host-java-public-nine-second-wire-span', 'producer_stop_shape')
    need(validate_input(wav, spec['wav_sha256']) == 144000 and
         metadata['wav_sha256'] == spec['wav_sha256'], 'producer_wav_binding')
    with wave.open(str(wav), 'rb') as source:
        pcm = source.readframes(144001)
    need(hashlib.sha256(pcm).hexdigest() == metadata['pcm_sha256'] ==
         '824962e469c1b5c3969804b2846b1428fe4a194a1744d5599aa595ae7f69af73', 'producer_public_pcm')
    pinned = {Path(row['path']).resolve() for row in spec['pins']}
    need({manifest.resolve(), wav.resolve(), Path(spec['assets']).resolve()} <= pinned, 'required_input_asset_pins')
    need(manifest.parent.parent.resolve() == Path(spec['spool']).resolve(), 'producer_spool_binding')
    return metadata

def watch_child(spec, output):
    from audio_backend import recorder_receiver_publication as receiver
    original = receiver.inbox.inventory
    events = watchbase.Events(spec['receiver_events'])
    def observed(*args, **kwargs):
        value = original(*args, **kwargs)
        events.emit('receiver_inventory', entries=len(value['deliveries']))
        return value
    receiver.inbox.inventory = observed
    try: return watchbase.run_watch_child(spec, output)
    finally:
        receiver.inbox.inventory = original
        events.close()



def cleanup_wire(guard, keys, report):
    try:
        report['cleanup']=guard.cleanup()
        need(all(row['reaped'] and row['returncode'] == 0 for row in report['cleanup']), 'fixture_child_cleanup')
    except BaseException as error:
        report['cleanup_error']=str(error); report['passed']=False
    finally:
        report['peak_rss']=guard.peak
        try:
            shutil.rmtree(keys); report['ephemeral_keys_removed']=not keys.exists()
            need(report['ephemeral_keys_removed'], 'ephemeral_key_cleanup')
        except BaseException as error:
            report['key_cleanup_error']=str(error); report['passed']=False

def wire_driver(spec, output):
    """Own only the bounded TLS fixture subtree, separately from native workers."""
    sys.path[:0] = [spec['dependencies'], spec['test_tools'], str(ROOT/'qualification/receiver_callback_20261103')]
    from fixture_support import Guard, prepare_tls
    work = Path(output); work.mkdir(mode=0o700)
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))}); os.nice(19)
    guard = Guard(work); keys = work/'ephemeral-keys'; keys.mkdir(mode=0o700)
    report = dict(passed=False, native_inference=False)
    server = None
    old = {s:signal.signal(s, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt(signum)))
           for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        check_pins(spec['pins']); report['certificates'] = prepare_tls(keys)
        guard.invoke(['keytool', '-importcert', '-noprompt', '-storetype', 'PKCS12', '-keystore', keys/'trust.p12',
                      '-storepass', 'synthetic', '-alias', 'fixture-ca', '-file', keys/'ca.pem'], work/'trust.log')
        case = work/'wire'; case.mkdir(mode=0o700)
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([spec['dependencies'], spec['test_tools'], str(ROOT)]),
                   PYTHONDONTWRITEBYTECODE='1')
        server = guard.spawn([sys.executable, '-B', ROOT/'qualification/receiver_callback_20261103/receiver_callback_server.py',
                             case, keys/'server.pem', keys/'server.key'], work/'server.log', env)
        guard.until(lambda:(case/'ready.json').exists(), 'server_ready', processes=[server])
        ready=load(case/'ready.json'); need(ready['host']=='127.0.0.1','loopback_only')
        save(work/'ready.json',ready)
        java=['java','-Xmx64m','-XX:MaxMetaspaceSize=48m','-XX:ReservedCodeCacheSize=8m','-XX:+UseSerialGC',
              '-XX:ActiveProcessorCount=1','-Xss256k','-Djavax.net.ssl.trustStore='+str(keys/'trust.p12'),
              '-Djavax.net.ssl.trustStorePassword=synthetic','-Djavax.net.ssl.trustStoreType=PKCS12',
              '-cp',spec['classes'],'io.github.headmaster218.recorder.android.PublicSpeechWireClient',
              'success',case/'client',str(ready['port']),spec['spool'],spec['manifest_sha256'],spec['wav_sha256']]
        started=time.monotonic(); client=guard.spawn(java,work/'client.log')
        guard.until(lambda:(case/'marker-held.json').exists(),'marker_held',10,[server,client])
        # Parent reads actual held-writer evidence and observes the same watch twice.
        guard.until(lambda:(case/'release-marker').exists(),'parent_release',10,[server,client])
        guard.finish(client,max(.1,30-(time.monotonic()-started)))
        receipt=json.loads((work/'client.log').read_text().splitlines()[-1]); completion=load(case/'completed-delivery.json')
        need(receipt['status']=='verified' and receipt['local_unchanged'] and receipt['all_raw_sockets_closed'], 'wire_client_failure')
        need(receipt['callback_registrations']==receipt['callback_unregistrations'],'callback_leak')
        need(completion['handle_closed'] and completion['live_writers']==0 and completion['successful']
             and completion['result']['status']=='published' and completion['result']['pending_writers']==0
             and completion['duplicate_result']==completion['result'],'automatic_publication_failed')
        report.update(client=receipt,completion=completion)
        (case/'stop').touch(); guard.finish(server,5)
        metrics=load(case/'server-result.json')
        need(metrics['all_closed'] and metrics['live_writers_at_exit']==0 and not metrics['errors'],'server_cleanup')
        import socket
        closed=[]
        for port in metrics['listeners']:
            with socket.socket() as sock:
                sock.settimeout(.2); need(sock.connect_ex(('127.0.0.1',port))!=0,'listener_open');closed.append(port)
        report.update(server=metrics,listeners_closed=closed,passed=True)
    except BaseException as error:
        report['error']=dict(type=type(error).__name__,message=str(error)[:2048])
    finally:
        cleanup_wire(guard, keys, report)
        for signum,handler in old.items():signal.signal(signum,handler)
        try:check_pins(spec['pins']);report['pins_unchanged']=True
        except BaseException as error:report['pin_error']=str(error);report['passed']=False
        save(work/'RESULT.json',report)
    return 0 if report['passed'] else 1


def wait(gate, process, predicate, label, seconds=30):
    until=min(gate.deadline,time.monotonic()+seconds)
    while not predicate():
        gate.check();need(process.poll() is None,'child_early_exit:'+label)
        need(time.monotonic()<until,'deadline:'+label);time.sleep(.025)
    gate.check()


def run(spec, output):
    os.umask(0o077);output=Path(output).resolve();baseline.require_nonrepository_output(output)
    check_pins(spec['pins']); check_inventories(spec); metadata=validate_producer(spec)
    output.mkdir(mode=0o700);(output/'data').mkdir(mode=0o700)
    gate=Gate(output,1,300);gate.journal=(output/'http.jsonl').open('x')
    owned=[];report=dict(passed=False,device_test=False,deployed=False,accuracy_verified=False,
                         complete_capture_proven=False,source_pins=spec['pins'])
    def spawn(command,name):
        stdout=(output/(name+'.stdout')).open('xb');stderr=(output/(name+'.stderr')).open('xb')
        proc=subprocess.Popen([str(x) for x in command],stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,cwd=ROOT)
        owned.append((proc,name,stdout,stderr));return proc
    try:
        report['startup']=baseline.start_service(gate,spec['assets'])
        sid=gate.request(('POST','/sessions',201,{'name':'Bounded public-speech FTPS native chain'},{}))['id']
        save(output/'wire-spec.json',spec)
        driver=spawn([sys.executable,'-I','-B',__file__,'wire',output/'wire-spec.json',output/'ftps','--spec-sha256',digest_file(output/'wire-spec.json')],'wire')
        wait(gate,driver,lambda:(output/'ftps/wire/marker-held.json').exists(),'wire_marker_hold')
        ready=load(output/'ftps/ready.json');held=load(output/'ftps/wire/marker-held.json')
        need(not held['handle_closed'] and held['live_writers']==1 and 0<held['size']<=32,'not_live_marker')
        need(len(held['callbacks'])==2 and all(x['successful'] for x in held['callbacks']),'missing_data_callbacks')
        from audio_backend import recorder_inbox_runner as runner
        from audio_backend import recorder_receiver_publication as receiver
        from audio_backend.recorder_metadata import decode
        metadata=decode(Path(spec['manifest']).read_bytes())
        origin={key:metadata[key] for key in ('epoch','sample_rate_hz','channels','requested_input','actual_input','boundary','target_frames')}
        origin.update(capture_identity_sha256=hashlib.sha256(metadata['capture_id'].encode()).hexdigest(),
                      run_identity_sha256=hashlib.sha256(metadata['run_id'].encode()).hexdigest(),
                      first_sequence=metadata['sequence'],epoch_frame_start=metadata['frame_start'])
        config=dict(schema=runner.CONFIG_SCHEMA,inbox_dir=ready['inbox'],job_dir=str(output/'intake'),output_dir=str(output/'export'),
                    api='http://127.0.0.1:'+str(gate.port),session_id=sid,capture_id='combined-chain',span_origin=origin,
                    max_hours=1,max_inputs=1,receiver_publication=receiver.bind(ready['receiver_state']))
        runner.configure(state_dir=output/'runner',config=config)
        base=dict(api=config['api'],session_id=sid,capture_id=config['capture_id'],state_dir=str(output/'runner'),
                  job_dir=str(output/'intake'),repeat_output_dir=str(output/'export-repeat'),deliveries=[],
                  watch_events=str(output/'watch-events.jsonl'),receiver_events=str(output/'receiver-events.jsonl'))
        save(output/'watch-spec.json',base)
        before=inventory([Path(ready['inbox']),output/'runner',output/'intake'])
        watcher=spawn([sys.executable,'-I','-B',__file__,'watch',output/'watch-spec.json',output/'watch-result.json','--spec-sha256',digest_file(output/'watch-spec.json')],'watch')
        wait(gate,watcher,lambda:len(watchbase.read_events(base['receiver_events']))>=2,'two_held_empty_scans',10)
        scans=watchbase.read_events(base['receiver_events']);events=watchbase.read_events(base['watch_events'])
        need(all(x['entries']==0 for x in scans) and not any(x['event']=='request_start' for x in events),'held_marker_exposed')
        need(inventory([Path(ready['inbox']),output/'runner',output/'intake'])==before,'held_state_changed')
        save(output/'MARKER_HOLD.json',dict(actual_writer=held,scans=scans,before=before,after=before,request_count=0))
        (output/'ftps/wire/release-marker').touch()
        wait(gate,watcher,lambda:watcher.poll() is not None,'watch_complete',120)
        need(watcher.wait(timeout=1)==0,'watch_exit')
        watched=load(output/'watch-result.json');outcome=baseline.assert_returned(watched)
        need(outcome['status']=='exported' and outcome['inputs']==1 and outcome['export_completed'],'watch_no_export')
        wait(gate,driver,lambda:driver.poll() is not None,'driver_complete',10)
        need(driver.wait(timeout=1)==0 and load(output/'ftps/RESULT.json')['passed'],'wire_driver_failed')
        completion=load(output/'ftps/RESULT.json')['completion'];raw=Path(completion['path']).parent
        selected=Path(ready['inbox'])/raw.name
        from audio_backend.recorder_delivery import inspect_delivery
        original=inspect_delivery(raw,validate_metadata=True)
        need(original['close_reason']=='STOP' and original['frames']==144000 and original['sequence']==0 and original['frame_start']==0,'actual_stop_shape')
        need(inspect_delivery(selected,validate_metadata=True)==original,'snapshot_metadata')
        for name in baseline.FILES:
            need((raw/name).read_bytes()==(selected/name).read_bytes(),'snapshot_bytes')
            need((raw/name).stat().st_ino!=(selected/name).stat().st_ino,'snapshot_not_independent')
        need(digest_file(raw/'audio.wav')==spec['wav_sha256'],'producer_audio_changed')
        from audio_backend.recorder_delivery import parse_envelope,parse_marker
        need(parse_envelope((raw/'segment.v1.bin').read_bytes(),parse_marker((raw/'delivery.v1.txt').read_bytes()),return_local=True)
             ==Path(spec['manifest']).read_bytes(),'producer_manifest_changed')
        base['deliveries']=[str(selected)]
        status=baseline.child_phase(gate,base,'independent-status',operation='status');baseline.assert_reads_only(status)
        verify_status(baseline.assert_returned(status),[original])
        repeat=baseline.child_phase(gate,base,'independent-export-repeat',operation='export-repeat');baseline.assert_reads_only(repeat);baseline.assert_returned(repeat)
        exported=baseline.check_exports(output,[original])
        report.update(session_id=sid,selected_delivery=str(selected),raw_delivery=str(raw),watch=outcome,private_export=exported,
                      genuine_stop=original['close_reason']=='STOP',input_frames=original['frames'],scenario_complete=True)
    except BaseException as error:
        report['error']=dict(type=type(error).__name__,message=str(error)[:2048])
    finally:
        waits=[]
        for proc,name,stdout,stderr in reversed(owned):
            try:
                code=watchbase.reap(proc,output/(name+'-wait.json'));waits.append(dict(name=name,pid=proc.pid,exit_code=code))
            except BaseException as error:report[name+'_cleanup_error']=str(error)
            finally:stdout.close();stderr.close()
        report['child_waits']=waits;gate.set_phase('stopping')
        try:report['shutdown']=gate.stop()
        except BaseException as error:
            report['shutdown_error']=str(error)
            try:report['forced_cleanup']=gate.force_cleanup('combined_chain_shutdown_failed')
            except BaseException as cleanup:report['forced_cleanup_error']=str(cleanup)
        if gate.hold is not None and 'forced_cleanup_error' not in report:os.close(gate.hold)
        gate.journal.close()
        try:check_pins(spec['pins']);check_inventories(spec);report['pins_unchanged']=True
        except BaseException as error:report['pin_error']=str(error)
        report['peak_sampled_rss']=gate.peak_rss
        report['passed']=report.get('scenario_complete') is True and not any(k.endswith('error') for k in report)
        save(output/'RESULT.json',report)
    return 0 if report['passed'] else 1


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['run','watch','wire']);parser.add_argument('spec',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--spec-sha256',required=True)
    args=parser.parse_args()
    need(args.spec.is_file() and not args.spec.is_symlink() and digest_file(args.spec)==args.spec_sha256,'spec_hash')
    spec=load(args.spec)
    return {'run':run,'watch':watch_child,'wire':wire_driver}[args.mode](spec,args.output)
if __name__=='__main__':raise SystemExit(main())
