# Unexecuted portable template; generate every configuration placeholder before use.
"""Exactly 42 model-free test groups, bounded by the preparation process guard.

Six product-helper groups extract only the pinned pure function AST, never import
product/runtime providers. Synthetic data is labeled; historical counts are not runs.
"""
from __future__ import annotations
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time
import types
import unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import binding
from common import Deadlines, Refused, canonical, sha
from test_seam import (OwnershipFixtures, SeamActualFixtures, FakeClock, FakeJournal,
                       retained_root, fixture_deadline)


def prepared_config():
    return binding.decode((HERE.parent/'config.template.json').read_bytes())


def digest_helper():
    source,pins=binding.fixture_source()
    path=source/'audio_backend/native_owned_cells.py'; raw=path.read_bytes()
    if sha(raw)!=pins['audio_backend/native_owned_cells.py']:raise Refused('pure_helper_source_pin')
    tree=ast.parse(raw)
    wanted={'Unavailable','float32_digest'}
    nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in wanted]
    if {n.name for n in nodes}!=wanted:raise Refused('pure_helper_exact_AST')
    env={'hashlib':hashlib,'struct':struct,'CELL_PCM_BYTES':640000,'IO_BYTES':65536}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),env)
    return env['float32_digest'],env['Unavailable']


def float_oracle(pcm):
    return hashlib.sha256(b''.join(struct.pack('<f',v[0]/32768.)
                           for v in struct.iter_unpack('<h',pcm))).hexdigest()


class DigestBoundaryFixtures(unittest.TestCase):
    def test_full_21_second_source_refused(self):
        helper,error=digest_helper()
        with self.assertRaisesRegex(error,'invalid_cell_pcm'):helper(bytes(672000))

    def test_exact_20_second_nonzero_origin_slice(self):
        helper,_=digest_helper()
        source=bytes(range(256))*2625
        composite=source[8000*2:328000*2]
        self.assertEqual(len(composite),640000)
        self.assertNotEqual(composite,source[:640000])
        self.assertEqual(helper(composite),float_oracle(composite))
        import capture
        text=Path(capture.__file__).read_text()
        self.assertNotIn('float32_digest(full)',text)
        self.assertIn("role='source', pins=pins['source']",text)
        self.assertIn('full == source_pcm',text)

    def test_320000_and_320001_frame_boundary(self):
        helper,error=digest_helper()
        self.assertEqual(helper(bytes(640000)),float_oracle(bytes(640000)))
        with self.assertRaises(error):helper(bytes(640002))

    def test_odd_bytes_refused(self):
        helper,error=digest_helper()
        for count in (1,3,639999):
            with self.assertRaises(error):helper(bytes(count))

    def test_exact_bytes_type_required(self):
        helper,error=digest_helper()
        class ByteSubclass(bytes):pass
        for value in (bytearray(b'\0\0'),memoryview(b'\0\0'),ByteSubclass(b'\0\0'),None,'00'):
            with self.assertRaises(error):helper(value)
        self.assertEqual(helper(b''),hashlib.sha256(b'').hexdigest())

    def test_signed_little_endian_oracle(self):
        helper,_=digest_helper()
        values=(-32768,-32767,-256,-1,0,1,255,256,32767)
        pcm=b''.join(struct.pack('<h',x) for x in values)
        expected=hashlib.sha256(b''.join(struct.pack('<f',x/32768.) for x in values)).hexdigest()
        self.assertEqual(helper(pcm),expected)
        self.assertNotEqual(helper(pcm),hashlib.sha256(b''.join(struct.pack('>f',x/32768.) for x in values)).hexdigest())


class BindingFixtures(unittest.TestCase):
    def test_valid_current_config_and_pending_execution(self):
        config=prepared_config()
        self.assertIs(binding.validate_config(config),config)
        self.assertIsNone(config['inventory']);self.assertIsNone(config['input_pins'])
        self.assertTrue(all(value is None for value in config['prerequisites'].values()))
        with self.assertRaisesRegex(Refused,'pending_fresh_prerequisite'):
            binding.validate_config(config,ready=True)
        from test_seam import ReceiptReaderHelper
        ReceiptReaderHelper.check_readers(self,config)

    def test_absent_or_false_actual_authority_refused(self):
        cfg=prepared_config();raw=canonical(cfg)
        for authority in (None,{}, {'schema':binding.AUTHORITY_SCHEMA,'authorized':False,
                'config_sha256':sha(raw),'scope':binding.SCOPE,'run_directory':cfg['paths']['output_dir']}):
            with self.assertRaises(Refused):binding.validate_authority(authority,raw,cfg)

    def test_changed_config_invalidates_actual_authority(self):
        cfg=prepared_config();cfg['paths']['output_dir']=str(HERE.parent/'runs/run-fixture0')
        raw=canonical(cfg)
        authority={'schema':binding.AUTHORITY_SCHEMA,'authorized':True,'config_sha256':sha(raw),
            'scope':binding.SCOPE,'run_directory':cfg['paths']['output_dir']}
        binding.validate_authority(authority,raw,cfg)
        with self.assertRaisesRegex(Refused,'actual_authority_binding'):
            binding.validate_authority(authority,raw+b'\n',cfg)
        with self.assertRaises(Refused):binding.validate_config(dict(cfg,unapproved=True))

    def test_wrong_product_or_runtime_refused(self):
        base=prepared_config()
        for mutation in ('commit','source','runtime','python','import'):
            cfg=copy.deepcopy(base)
            if mutation=='commit':cfg['product_commit']='0'*40
            elif mutation=='import':cfg['runtime_import']['sha256']='0'*64
            else:cfg['paths'][{'source':'source_root','runtime':'runtime_root','python':'python'}[mutation]]='/wrong/current'
            with self.assertRaises(Refused):binding.validate_config(cfg)

    def test_wrong_input_or_model_refused(self):
        base=prepared_config()
        for mutation in ('input','MOSS','native','source_geometry'):
            cfg=copy.deepcopy(base)
            if mutation=='input':cfg['input_descriptor']['sha256']='0'*64
            elif mutation=='source_geometry':cfg['input_descriptor']['frames']=320000
            elif mutation=='MOSS':cfg['moss_sha256']='551c'+'0'*60
            else:cfg['model_acceptance']['native']['sha256']='0'*64
            with self.assertRaises(Refused):binding.validate_config(cfg)

    def test_invalid_path_duplicate_keys_and_canonical_sets(self):
        for path in ('relative','/a//b','/a/../b','/a/./b','/a/','//a','/a\\b'):
            with self.assertRaises(Refused):binding.path_text(path)
        for raw in (b'{"x":1,"x":2}',b'{"files":{"/x":{},"/x":{}}}'):
            with self.assertRaisesRegex(Refused,'duplicate_JSON_key'):binding.decode(raw)
        row={'path':'/explicit-fixture','bytes':1,'sha256':'a'*64}
        with self.assertRaisesRegex(Refused,'duplicate_inventory_path'):binding.rows_map([row,row])
        from server import admit_native_turns
        helper=types.SimpleNamespace(require=binding.require,encoded=canonical,MAX_TURNS=32,MAX_LABELS=8)
        turns=[{'start':0.,'end':21.,'speaker_local_id':'native:two'},
               {'start':19.,'end':20.,'speaker_local_id':'native:one'}]
        self.assertEqual(admit_native_turns(helper,turns),admit_native_turns(helper,list(reversed(turns))))
        self.assertEqual(admit_native_turns(helper,turns),frozenset(('native:one','native:two')))
        for frames in (307040,320000,336001,True):
            with self.assertRaises(Refused):admit_native_turns(helper,turns,frames=frames)

    def test_wrong_limits_or_phase_geometry_refused(self):
        base=prepared_config()
        for field,value in [('outer_seconds',1801),('cpus',[3]),('address_space_bytes',16*2**30),
                ('aggregate_sampled_rss_bytes',True),('incremental_disk_bytes',545*2**20)]:
            cfg=copy.deepcopy(base);cfg['limits'][field]=value
            with self.assertRaisesRegex(Refused,'closed_limits'):binding.validate_config(cfg)
        cfg=copy.deepcopy(base);cfg['phases']['primary'][1]+=1
        with self.assertRaisesRegex(Refused,'closed_phases'):binding.validate_config(cfg)
        clock=FakeClock();_,deadline=fixture_deadline(clock)
        record=deadline.enter('primary',spec_sha256='fixture')
        with self.assertRaises(Refused):deadline.enter('preflight',spec_sha256='fixture')
        changed=dict(record,deadline=record['deadline']+1)
        with self.assertRaises(Refused):deadline.enter('primary',record=changed,spec_sha256='fixture')

    def test_incomplete_cleanup_cannot_publish_success(self):
        value={'status':'PROVISIONAL_CAPTURE_COMPLETE','posthash':{'all_matched':True}}
        cleanup={'cleanup':'CONFIRMED','remaining_owned':[]}
        self.assertTrue(binding.publishable(value,cleanup,[],None))
        for bad in ({'cleanup':'UNKNOWN'},{'cleanup':'CONFIRMED','remaining_owned':[123]}):
            with self.assertRaises(Refused):binding.publishable(value,bad,[],None)
        for bad in ({'status':'INCOMPLETE','posthash':{'all_matched':True}},
                    {'status':'PROVISIONAL_CAPTURE_COMPLETE','posthash':{'all_matched':False}}):
            with self.assertRaises(Refused):binding.publishable(bad,cleanup,[],None)
        with self.assertRaises(Refused):binding.publishable(value,cleanup,['failure'],None)
        with self.assertRaises(Refused):binding.publishable(value,cleanup,[],RuntimeError('producer'))
        ErrorPathHelper.check_failed_controller_posthash_and_bounded_shutdown(self)


# The retained error-path helper below is deliberately not a discoverable test class.

class ErrorPathHelper:
    def check_failed_controller_posthash_and_bounded_shutdown(self):
        import bootstrap,capture
        order=[]
        now=time.monotonic()
        d=Deadlines({'GUARD_WORK_END':str(now+1740),'GUARD_ABSOLUTE_END':str(now+1800),
                     'GUARD_STOP_PATH':'/explicitly-absent-fixture-stop'})
        with retained_root() as td:
            spec={'output_dir':td,'_sha256':'x'}
            def cleanup(path):order.append('cleanup');return {'cleanup':'CONFIRMED','originals':[]}
            ledger=types.SimpleNamespace(originals=[],cleanup=cleanup)
            def post(*a,**k):order.append('posthash');return {'all_matched':True}
            with patch.object(bootstrap,'terminal_observation',return_value={'status':'UNOBSERVED'}),patch.object(capture,'verify_inventory',side_effect=post):
                bootstrap.finalize(spec,d,FakeJournal(),ledger,'controller',None,RuntimeError('explicit fixture failure'))
            self.assertEqual(order,['cleanup','posthash'])
            value=json.loads((Path(td)/'capture-result.json').read_text())
            self.assertEqual(value['status'],'INCOMPLETE');self.assertTrue(value['posthash']['all_matched'])

            # Keep the controller's real lifecycle assignments and exception guard.
            # Omit only the independent HTTP/product body, replacing it with bounded
            # failure injection; no server, provider or extra retained root is opened.
            import copy
            tree=ast.parse(Path(capture.__file__).read_text())
            original=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_controller')
            lifecycle=copy.deepcopy(original)
            outer=next(n for n in lifecycle.body if isinstance(n,ast.Try))
            selected=[];constructors=[];normal_stops=[]
            for node in outer.body:
                if not isinstance(node,ast.Assign):continue
                names=[n.id for n in node.targets if isinstance(n,ast.Name)]
                call=node.value
                if names==['active'] and isinstance(call,ast.Constant) and call.value is None:
                    selected.append(node)
                elif names==['active_mode']:
                    selected.append(node)
                elif isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='Server':
                    mode=call.args[-1].value;constructors.append(mode);selected.append(node)
                    selected.extend(ast.parse(f"abort_point({mode!r},'initialized')").body)
                elif isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=='stop_and_prove':
                    mode={'server':'capture','off':'restart-off','on':'restart-on'}[call.func.value.id]
                    normal_stops.append(mode);selected.append(node)
                    selected.extend(ast.parse(f"abort_point({mode!r},'stopped')").body)
            self.assertEqual(constructors,['capture','restart-off','restart-on'])
            self.assertEqual(normal_stops,constructors)
            outer.body=selected;ast.fix_missing_locations(lifecycle)
            cases=[('normal',None)]+[(kind,mode) for mode in constructors for kind in
                ('cooperative','stop_error','expired','STOP','partial','normal_stop_error','after_stopped','expires_during_stop')]
            for serial,(kind,target) in enumerate(cases):
                root=Path(td)/f'abort-{serial:02}';root.mkdir()
                spec={'output_dir':str(root),'_sha256':'explicit-fake-abort','python':'/explicit-fixture-python'}
                clock,deadline=fixture_deadline(FakeClock());deadline.enter('lifecycle',spec_sha256=spec['_sha256'])
                immutable=dict(deadline.phase_records['lifecycle']);order=[];owners=[];ends=[]
                primary=RuntimeError('same original controller failure')
                secondary=RuntimeError('secondary stop failure '+('x'*700))
                stop_flag={'set':False};deadline.stop=types.SimpleNamespace(exists=lambda:stop_flag['set'])
                call_end=deadline.call_end
                def bounded_end(maximum):
                    end=call_end(maximum);ends.append((maximum,end));return end
                deadline.call_end=bounded_end
                enter=deadline.enter
                def enter_phase(name,**kwargs):
                    if name=='posthash':order.append('posthash-entry')
                    return enter(name,**kwargs)
                deadline.enter=enter_phase
                def cleanup(path):
                    order.append('cleanup')
                    for owner in owners:
                        if owner.proc.returncode is None:owner.proc.returncode=-15
                    return {'cleanup':'CONFIRMED','remaining_owned':[],
                        'originals':[{'pid':o.proc.pid,'original_waited':True,'returncode':o.proc.returncode} for o in owners]}
                ledger=types.SimpleNamespace(originals=[],cleanup=cleanup,census=lambda:[])
                class FakeServer:
                    command=capture.Server.command
                    stop_and_prove=capture.Server.stop_and_prove
                    def __init__(obj,spec,deadlines,journal,ledger,mode):
                        obj.spec=spec;obj.deadlines=deadlines;obj.journal=journal;obj.ledger=ledger;obj.mode=mode
                        obj.stopped_and_reaped=False;obj.stop_attempted=False;obj.sequence=0
                        obj.dir=root/mode;obj.dir.mkdir();obj.log_error=None
                        obj.log_thread=types.SimpleNamespace(join=lambda timeout:None,is_alive=lambda:False)
                        obj.proc=types.SimpleNamespace(pid=910000+serial*3+len(owners),returncode=None,
                            args=[spec['python'],'-I','-B','-S',str(bootstrap.HERE/'bootstrap.py'),'--mode','server'])
                        obj.proc.poll=lambda:obj.proc.returncode;obj.proc.wait=lambda timeout:obj.proc.returncode
                        owners.append(obj);ledger.originals.append(obj.proc)
                        if kind=='partial' and mode==target:raise primary
                    def wait_json(obj,path,end):
                        order.append('cooperative:'+obj.mode)
                        self.assertTrue(obj.stop_attempted)
                        self.assertLessEqual(end,immutable['deadline'])
                        if obj.mode==target:
                            if kind=='stop_error':raise secondary
                            if kind=='normal_stop_error':raise primary
                            if kind=='expires_during_stop':
                                clock.now=end;deadline.check(end)
                        obj.proc.returncode=0
                        # Explicit fake bootstrap terminal, never acoustic evidence.
                        capture.atomic_json(root/f'server-{obj.proc.pid}-terminal.json',
                            {'schema':'terminal-python-provenance-v1','status':'COMPLETE','pid':obj.proc.pid,
                                'mode':'server','spec_sha256':spec['_sha256'],'cleanup':'CONFIRMED','failures':[]})
                        return {}
                def abort_point(mode,stage):
                    if mode!=target:return
                    if stage=='initialized' and kind not in ('normal_stop_error','after_stopped','partial'):
                        if kind=='expired':clock.now=immutable['deadline']
                        if kind=='STOP':stop_flag['set']=True
                        raise primary
                    if stage=='stopped' and kind=='after_stopped':raise primary
                def receipt(path,maximum,check):
                    check()
                    if Path(path).name=='server-final.json':
                        return json.dumps({'control_stopped':True,'worker_stopped':True,'control_errors':[]}).encode()
                    return json.dumps({'cleanup':'CONFIRMED','remaining_owned':[],'originals':[]}).encode()
                journal=FakeJournal()
                ns={'Path':Path,'Server':FakeServer,'abort_point':abort_point,'atomic_json':capture.atomic_json}
                exec(compile(ast.Module(body=[lifecycle],type_ignores=[]),str(capture.__file__),'exec'),ns)
                with patch.object(capture,'bounded_read',side_effect=receipt),patch.object(capture.time,'sleep',return_value=None):
                    if kind=='normal':
                        self.assertIsNone(ns['run_controller'](spec,deadline,journal,ledger))
                    else:
                        with self.assertRaises(RuntimeError) as got:ns['run_controller'](spec,deadline,journal,ledger)
                        self.assertIs(got.exception,primary)
                self.assertEqual(deadline.phase_records['lifecycle'],immutable)
                self.assertTrue(all(end<=immutable['deadline'] for _,end in ends))
                self.assertTrue(all(maximum in (20,35) for maximum,_ in ends))
                for mode in constructors:self.assertLessEqual(order.count('cooperative:'+mode),1)
                if kind=='normal':
                    self.assertEqual(order,['cooperative:'+mode for mode in constructors])
                    self.assertTrue(all(o.stopped_and_reaped and o.stop_attempted for o in owners))
                    self.assertFalse((root/'capture-result.json').exists())
                    self.assertFalse(any(args==('controller_abort_stop',) for args,_ in journal.events))
                    continue
                value=json.loads((root/'capture-result.json').read_text())
                stop=value['cooperative_stop'];expected={'cooperative':'STOPPED','stop_error':'FAILED',
                    'expired':'FAILED','STOP':'FAILED','partial':'NOT_INITIALIZED',
                    'normal_stop_error':'ALREADY_ATTEMPTED','after_stopped':'ALREADY_STOPPED',
                    'expires_during_stop':'FAILED'}[kind]
                self.assertEqual(stop['status'],expected);self.assertEqual(stop['mode'],target)
                self.assertEqual(value['status'],'INCOMPLETE');self.assertEqual(value['error'],'RuntimeError')
                self.assertEqual(value['detail'],str(primary))
                self.assertEqual([fields for args,fields in journal.events if args==('controller_abort_stop',)],[stop])
                if kind=='stop_error':
                    self.assertEqual(stop['exception'],'RuntimeError');self.assertEqual(stop['detail'],str(secondary)[:500])
                if kind in ('expired','STOP','expires_during_stop'):self.assertEqual(stop['exception'],'Refused')
                if kind in ('expired','STOP','partial'):
                    self.assertNotIn('cooperative:'+target,order);self.assertFalse(owners[-1].stop_attempted)
                else:self.assertEqual(order.count('cooperative:'+target),1)
                def post(*args,**kwargs):order.append('posthash');return {'all_matched':True}
                with patch.object(bootstrap,'terminal_observation',return_value={'status':'UNOBSERVED'}),                        patch.object(capture,'verify_inventory',side_effect=post):
                    bootstrap.finalize(spec,deadline,journal,ledger,'controller',None,primary)
                value=json.loads((root/'capture-result.json').read_text())
                terminal=json.loads((root/f'controller-{os.getpid()}-terminal.json').read_text())
                self.assertEqual(value['status'],'INCOMPLETE');self.assertEqual(value['cooperative_stop'],stop)
                self.assertEqual(terminal['status'],'INCOMPLETE');self.assertEqual(terminal['producer_error']['detail'],str(primary))
                self.assertLess(order.index('cleanup'),order.index('posthash-entry'))
                for i,event in enumerate(order):
                    if event.startswith('cooperative:'):self.assertLess(i,order.index('cleanup'))
                if kind!='STOP':
                    self.assertLess(order.index('cleanup'),order.index('posthash'));self.assertTrue(value['posthash']['all_matched'])
                if kind in ('stop_error','expired','partial','normal_stop_error','expires_during_stop'):
                    self.assertTrue(any(row['stage']=='child_provenance' and
                        row['detail']=='missing_child_terminal_without_verified_live_signal_gap'
                        for row in terminal['failures']))
                if kind in ('cooperative','after_stopped'):
                    self.assertFalse(any(row['stage']=='child_provenance' for row in terminal['failures']))
                    self.assertEqual(len(terminal['children']),len(owners))
                    self.assertTrue(all(o.stopped_and_reaped and o.proc.returncode==0 for o in owners))
                self.assertEqual(deadline.phase_records['lifecycle'],immutable)


CLASSES=(SeamActualFixtures,OwnershipFixtures,DigestBoundaryFixtures,BindingFixtures)


def exact_ids():
    return sorted(cls.__name__+'.'+name for cls in CLASSES
                  for name in unittest.defaultTestLoader.getTestCaseNames(cls))


def main():
    required=('GUARD_RUN_DIRECTORY','GUARD_STOP_PATH','GUARD_WORK_END','GUARD_ABSOLUTE_END',
              'FRESH_SEAM_CONFIG','FRESH_SEAM_CONFIG_SHA256','FRESH_SEAM_FIXTURE_AUTHORITY','FRESH_SEAM_FIXTURE_AUTHORITY_SHA256')
    if not all(name in os.environ for name in required):raise SystemExit('Bounded model-free test configuration required')
    # The model-free configuration excludes inference and never loads bootstrap.main.
    if any(name in os.environ for name in ('FRESH_SEAM_AUTHORITY','FRESH_SEAM_AUTHORITY_SHA256')):
        raise SystemExit('Model-free gate refuses actual authority')
    manifest=binding.decode((HERE.parent/'SOURCE_MANIFEST.json').read_bytes())
    ids=exact_ids()
    if len(ids)!=42 or ids!=manifest['fixture_ids']:raise SystemExit('Exact fresh 42-group manifest required')
    binding.verify_fixture_invocation(ids)
    suite=unittest.TestSuite(cls(name) for cls in CLASSES
            for name in unittest.defaultTestLoader.getTestCaseNames(cls))
    result=unittest.TextTestRunner(verbosity=2,failfast=True).run(suite)
    print(json.dumps({'schema':'fresh-seam-model-free-result-v1','label':'EXPLICIT_SYNTHETIC_MODEL_FREE',
        'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'skips':len(result.skipped),'ids':ids,'actual_acoustic_calls':0,'historical_runs_adopted':False,
        'harness_files':{str(HERE/name):{'bytes':(HERE/name).stat().st_size,'sha256':sha((HERE/name).read_bytes())}
                         for name in binding.HARNESS_CAPS}}),flush=True)
    return 0 if result.wasSuccessful() and result.testsRun==42 and not result.skipped else 1


if __name__=='__main__':
    raise SystemExit(main())
