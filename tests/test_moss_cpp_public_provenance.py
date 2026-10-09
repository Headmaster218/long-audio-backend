from audio_backend.overlap_store import _provenance
import pytest


def test_cpp_consumed_hashes_and_unknown_generation_count_visible():
    expected=dict(runtime_provider='transcribe_cpp',runtime_version='0.2.3',runtime_source_revision='a'*40,
        runtime_manifest_sha256='b'*64,runtime_library_sha256='c'*64,model_sha256='d'*64,
        source_weight_sha256='e'*64,requested_device='cpu',actual_backend='CPU',actual_device_kind='CPU',
        actual_device_name='CPU',generation_budget=512,generated_tokens=None,generation_token_count_known=False,native_output_truncated=False)
    assert _provenance(expected)==expected


def test_runtime_paths_and_arbitrary_metadata_stay_private():
    p=_provenance(dict(runtime_library_sha256='a'*64,actual_device_name='/private/host/device',runtime_provider='transcribe_cpp',secret='/private/file',source_weight_sha256='b'*64))
    assert 'actual_device_name'not in p and'secret'not in p


@pytest.mark.parametrize('value',[dict(generation_token_count_known=False,generated_tokens=0),dict(generation_token_count_known=0),dict(native_output_truncated=1),dict(generation_budget=True),dict(generation_budget=513),dict(generated_tokens=-1)])
def test_no_fake_zero_or_invalid_runtime_metadata(value):
    with pytest.raises(ValueError):_provenance(value)


def test_adaptive_budget_is_not_misrepresented_as_fixed512():
    values=dict(precision='q8_0',generation_budget=328,generation_budget_policy='native_adaptive',
        fixed_512_token_parity=False,session_n_ctx=700,audio_token_count=100,prompt_token_count=188,finish_reason='eos')
    assert _provenance(values)==values


@pytest.mark.parametrize('variable',['LD_LIBRARY_PATH','LD_PRELOAD'])
def test_cpp_loader_override_rejected_before_execution(monkeypatch,variable):
    from audio_backend.moss_td_candidates import reject_cpp_loader_overrides,UnsafeCppEnvironment
    monkeypatch.delenv('LD_LIBRARY_PATH',raising=False);monkeypatch.delenv('LD_PRELOAD',raising=False)
    reject_cpp_loader_overrides({'moss_td_runtime':'cpp'})
    monkeypatch.setenv(variable,'/untrusted/runtime')
    with pytest.raises(UnsafeCppEnvironment):reject_cpp_loader_overrides({'moss_td_runtime':'cpp'})
    reject_cpp_loader_overrides({'moss_td_runtime':'transformers'})


@pytest.mark.parametrize('path',[r'C:\private\device','C:/private/device',r'\\server\private\device',' /private/device'])
def test_no_foreign_platform_host_path_in_device_metadata(path):
    assert 'actual_device_name'not in _provenance({'actual_device_name':path})
