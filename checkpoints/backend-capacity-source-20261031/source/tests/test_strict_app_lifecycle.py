"""Focused source-bound factory/launcher controls, not a real service qualification.

Only selected exact function/class ASTs run against explicit fake owners. A later
HTTP lifecycle must exercise real inherited ownership and Store construction.
"""
import ast
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PENDING = []  # Declared bounded test owners, retained on synthetic uncertainty.


def extract(name, names, env):
    path = ROOT / 'audio_backend' / name
    parsed = ast.parse(path.read_text(), filename=str(path))
    selected = [node for node in parsed.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    assert {node.name for node in selected} == set(names)
    env = dict(env)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), env)
    return env


class Lock:
    instances = []
    fail_close = False
    def __init__(self, path):
        self.path = path
        self.closes = 0
        self.adopted = None
        Lock.instances.append(self)
    def adopt(self, fd):
        self.adopted = fd
        return self
    def verify_inherited(self, path):
        assert path == self.path
    def release(self):
        self.closes += 1
        if self.fail_close:
            raise OSError('synthetic lock close')


@pytest.fixture(autouse=True)
def reset():
    Lock.instances = []
    Lock.fail_close = False
    yield
    # PENDING intentionally retains synthetic unknown owners, not real fds.


def factory(pending=lambda: None, make=lambda *a, **k: k):
    return extract('app.py', ('_StrictFactoryOwner', 'strict_factory_pending', 'create_app'), {
        'Path':Path, 'os':os, 'Settings':lambda:SimpleNamespace(data_dir=Path('/test'),strict_primary_enabled=False),
        'InstanceLock':Lock, 'pending_strict_initializer':pending,
        '_STRICT_FACTORY_OWNER':None, '_create_app':make,
    })


def settings(strict=True):
    return SimpleNamespace(data_dir=Path('/test'),strict_primary_enabled=strict)


def test_strict_missing_fd_before_factory_or_lock():
    calls = []
    g = factory(make=lambda *a, **k: calls.append(k))
    with pytest.raises(ValueError, match='prelocked'):
        g['create_app'](settings())
    assert not calls and not Lock.instances and g['_STRICT_FACTORY_OWNER'] is None


def test_legacy_factory_still_has_no_early_lock():
    g = factory()
    out = g['create_app'](settings(False))
    assert out['inherited_lock'] is None and out['strict_factory_owner'] is None
    assert not Lock.instances and g['_STRICT_FACTORY_OWNER'] is None


@pytest.mark.parametrize('safe', [True, False])
def test_post_store_failure_preserves_original_and_exact_owner(safe):
    store = SimpleNamespace(strict_cleanup_confirmed=lambda:safe)
    original = ValueError('helper failed')
    def make(*a, **k):
        k['strict_factory_owner'].store = store
        raise original
    g = factory(make=make)
    with pytest.raises(ValueError) as caught:
        g['create_app'](settings(),instance_lock_fd=10)
    assert caught.value is original
    if safe:
        assert Lock.instances[0].closes == 1 and g['_STRICT_FACTORY_OWNER'] is None
    else:
        owner = g['_STRICT_FACTORY_OWNER']; PENDING.append(owner)
        assert owner.store is store and owner.lock is Lock.instances[0]
        assert Lock.instances[0].closes == 0
        with pytest.raises(RuntimeError, match='ownership'):
            g['create_app'](settings(),instance_lock_fd=11)
        assert len(Lock.instances) == 1


def test_constructor_unknown_is_retained_before_store_assignment():
    state = {'pending':None}
    store = SimpleNamespace(strict_cleanup_confirmed=lambda:False)
    original = RuntimeError('constructor failed')
    def make(*a, **k):
        state['pending'] = store
        raise original
    g = factory(pending=lambda:state['pending'],make=make)
    with pytest.raises(RuntimeError) as caught:
        g['create_app'](settings(),instance_lock_fd=10)
    owner = g['_STRICT_FACTORY_OWNER']; PENDING.append(owner)
    assert caught.value is original and owner.store is store
    assert owner.lock is Lock.instances[0] and owner.lock.closes == 0


def test_body_error_dominates_unknown_factory_lock_close():
    original = ValueError('first body')
    def make(*a, **k):
        raise original
    Lock.fail_close = True
    g = factory(make=make)
    with pytest.raises(ValueError) as caught:
        g['create_app'](settings(),instance_lock_fd=10)
    owner = g['_STRICT_FACTORY_OWNER']; PENDING.append(owner)
    assert caught.value is original and owner.uncertain
    assert isinstance(owner.error,OSError) and owner.lock.closes == 1
    assert owner.release() is False and owner.lock.closes == 1


@pytest.mark.parametrize('wrapped', [False, True])
def test_inherited_inspection_oserror_never_claims_positive_close(monkeypatch,wrapped):
    original = RuntimeError('MOSS ownership namespace unavailable') if wrapped else OSError('inspector close unknown')
    def fail(*a, **k):
        raise original
    if wrapped:
        module = ModuleType('audio_backend.moss_handy_jobs'); module.ownership_fence = fail
        monkeypatch.setitem(sys.modules,'audio_backend.moss_handy_jobs',module)
        # Exact pre-Store inspector call site; subsequent helper code is not reached.
        g = extract('app.py', ('_create_app',), {'__package__':'audio_backend'})
        make = g['_create_app']
    else:
        make = fail
    g = factory(make=make)
    with pytest.raises(type(original)) as caught:
        g['create_app'](settings(),instance_lock_fd=10)
    owner = g['_STRICT_FACTORY_OWNER']; PENDING.append(owner)
    assert caught.value is original and owner.uncertain and owner.error is original
    assert owner.lock.closes == 0


def test_successful_factory_transfers_same_owner_until_positive_close():
    store = SimpleNamespace(strict_cleanup_confirmed=lambda:True)
    def make(*a, **k):
        k['strict_factory_owner'].store = store
        return k['strict_factory_owner']
    g = factory(make=make)
    owner = g['create_app'](settings(),instance_lock_fd=10)
    assert owner is g['_STRICT_FACTORY_OWNER'] and owner.store is store
    assert owner.release() is True and owner.lock.closes == 1
    assert g['_STRICT_FACTORY_OWNER'] is None
    assert owner.release() is True and owner.lock.closes == 1
    # Static integration assertion: one fixed Worker owner is bound before the
    # registered Capture is exposed. This is not a real lifespan execution.
    tree = ast.parse((ROOT / 'audio_backend' / 'app.py').read_text())
    bindings = [node for node in ast.walk(tree) if isinstance(node,ast.Assign)
        and any(isinstance(t,ast.Attribute) and t.attr == '_strict_failure_owner' for t in node.targets)]
    exposures = [node for node in ast.walk(tree) if isinstance(node,ast.Assign)
        and any(isinstance(t,ast.Attribute) and t.attr == 'native_owned_cells' for t in node.targets)
        and isinstance(node.value,ast.Name) and node.value.id == 'registered_cells']
    assert len(bindings) == 2 and len(exposures) == 1
    assert {ast.unparse(node.targets[0]) for node in bindings} == {
        'registered_cells._strict_failure_owner','registered_cells.authority._strict_failure_owner'}
    assert all(isinstance(node.value,ast.Name) and node.value.id == 'worker' for node in bindings)
    assert all(node.lineno < exposures[0].lineno for node in bindings)


def launcher(monkeypatch, body):
    import argparse
    resource = ModuleType('audio_backend.resources'); resource.InstanceLock = Lock
    monkeypatch.setitem(sys.modules, 'audio_backend.resources', resource)
    return extract('__main__.py', ('main',), {
        '__doc__':'test launcher', '__package__':'audio_backend', 'argparse':argparse,
        'os':os, 'Path':Path, '_STRICT_LAUNCHER_OWNER':None, '_main':body,
    })


def test_strict_cli_refuses_without_parent_hold(monkeypatch):
    calls = []
    g = launcher(monkeypatch, lambda *a:calls.append(a))
    with pytest.raises(SystemExit) as caught:
        g['main'](['--strict-primary'])
    assert caught.value.code == 2 and not calls and not Lock.instances


def test_strict_cli_holds_launcher_until_original_body_returns(monkeypatch):
    observed = []
    def body(args, parser, argv, lock):
        observed.append((lock,lock.closes))
        return 'done'
    g = launcher(monkeypatch,body)
    monkeypatch.delitem(sys.modules,'audio_backend.app',raising=False)
    assert g['main'](['--strict-primary','--instance-lock-fd','10','--data-dir','/test']) == 'done'
    assert observed == [(Lock.instances[0],0)]
    assert Lock.instances[0].closes == 1 and g['_STRICT_LAUNCHER_OWNER'] is None


def test_strict_cli_close_error_keeps_owner_and_original_exception(monkeypatch):
    original = ValueError('original service failure')
    def body(*a):
        raise original
    g = launcher(monkeypatch,body)
    monkeypatch.delitem(sys.modules,'audio_backend.app',raising=False)
    Lock.fail_close = True
    with pytest.raises(ValueError) as caught:
        g['main'](['--strict-primary','--instance-lock-fd','10','--data-dir','/test'])
    owner = g['_STRICT_LAUNCHER_OWNER']; PENDING.append(owner)
    assert caught.value is original and owner['uncertain']
    assert owner['lock'] is Lock.instances[0] and owner['lock'].closes == 1
    with pytest.raises(RuntimeError,match='ownership'):
        g['main'](['--strict-primary','--instance-lock-fd','11'])
    assert owner['lock'].closes == 1


@pytest.mark.parametrize('enabled',[True,False])
def test_strict_cli_factory_unknown_blocks_launcher_release(monkeypatch,enabled):
    original = RuntimeError('Store failed before app assignment')
    def body(*a):
        raise original
    g = launcher(monkeypatch,body)
    app = ModuleType('audio_backend.app'); app.strict_factory_pending = lambda:True
    monkeypatch.setitem(sys.modules,'audio_backend.app',app)
    with pytest.raises(RuntimeError) as caught:
        g['main']((['--strict-primary'] if enabled else []) + ['--instance-lock-fd','10','--data-dir','/test'])
    owner = g['_STRICT_LAUNCHER_OWNER']; PENDING.append(owner)
    assert caught.value is original and owner['uncertain'] and owner['lock'].closes == 0


@pytest.mark.parametrize('started,complete,body_error', [
    (False,False,False), (True,True,False), (True,False,False), (False,False,True),
])
def test_run_app_strict_lifecycle_release_boundary(monkeypatch,started,complete,body_error):
    original = ValueError('original uvicorn failure')
    hold = Lock(Path('/test/instance.lock'))
    hold.file = SimpleNamespace(fileno=lambda:10)
    closes = []
    def release():
        closes.append('child')
        if body_error:
            raise OSError('synthetic child close uncertainty')
        return True
    app = SimpleNamespace(state=SimpleNamespace(instance_lock_lifespan_started=started,
        instance_lock_cleanup_complete=complete, strict_factory_owner=SimpleNamespace(release=release)))
    appmod = ModuleType('audio_backend.app')
    appmod.strict_factory_pending = lambda:False
    supplied = []
    def create(*a,**k):
        supplied.append(k['instance_lock_fd'])
        return app
    appmod.create_app = create
    uvicorn = ModuleType('uvicorn')
    def run(value,**kwargs):
        assert value is app and hold.closes == 0 and kwargs['workers'] == 1
        if body_error:
            raise original
    uvicorn.run = run
    monkeypatch.setitem(sys.modules,'audio_backend.app',appmod)
    monkeypatch.setitem(sys.modules,'uvicorn',uvicorn)
    owner = {'lock':hold,'app':None,'uncertain':False,'error':None}
    g = extract('__main__.py', ('_run_app',), {
        '__package__':'audio_backend', 'os':SimpleNamespace(dup=lambda fd:fd+10),
        '_STRICT_LAUNCHER_OWNER':owner, '_INHERITED_APPS_PENDING_CLEANUP':[],
    })
    args = SimpleNamespace(native_root_reattestation=None,host='127.0.0.1',port=8765)
    if body_error:
        with pytest.raises(ValueError) as caught:
            g['_run_app'](settings(),args,hold)
        assert caught.value is original
    elif started and not complete:
        with pytest.raises(RuntimeError,match='unresolved'):
            g['_run_app'](settings(),args,hold)
    else:
        g['_run_app'](settings(),args,hold)
    assert supplied == [20] and owner['app'] is app and hold.closes == 0
    if body_error or (started and not complete):
        PENDING.append(owner)
        assert owner['uncertain']
    else:
        assert app.state.instance_lock_cleanup_complete and not owner['uncertain']
    assert closes == ([] if started else ['child'])


@pytest.mark.parametrize('variant,accepted', [
    ('legacy',True), ('strict',True), ('nonbool',False), ('missing_cells',False), ('gc',False),
])
def test_strict_settings_profile(tmp_path,variant,accepted):
    # Actual Settings construction is model-free; all model paths are names only.
    from audio_backend.settings import Settings
    options = dict(data_dir=tmp_path,model_dir=tmp_path)
    if variant != 'legacy':
        options.update(strict_primary_enabled=True,
            recording_primary_observation_policy='primary-clean-history-v1',
            recording_speaker_links_enabled=True,
            native_owned_support_enabled=True,native_owned_cells_enabled=True,
            native_owned_core_seams_enabled=True,native_owned_bounded_core_support_enabled=True,
            lexical_evidence_enabled=True,asr_backend='sensevoice',diarization_backend='nemotron',
            native_diarization_model='/model/native',native_diarization_library='/model/native.so',
            recording_speaker_model_path='/model/speaker.onnx')
    if variant == 'nonbool':options['strict_primary_enabled'] = 1
    if variant == 'missing_cells':options['native_owned_cells_enabled'] = False
    if variant == 'gc':options['native_owned_compact_gc_enabled'] = True
    if accepted:
        value = Settings(**options)
        assert value.strict_primary_enabled is (variant == 'strict')
    else:
        with pytest.raises(ValueError):
            Settings(**options)
