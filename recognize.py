"""Explicit single-arm reproduction. Never downloads or installs assets.
Run each arm in a separate pinned environment. Outputs contain corpus-derived
predictions and MUST remain outside Git. No retry or default promotion occurs.
"""
import argparse, array, hashlib, importlib.metadata, json, os, sys, sysconfig, wave
from pathlib import Path
from common import HERE, backend, external_output, read, verify


def samples(path, row):
    with wave.open(str(path)) as w:
        if (w.getnchannels(),w.getsampwidth(),w.getframerate(),w.getnframes(),w.getcomptype()) != (1,2,16000,row['frames'],'NONE'):
            raise ValueError('Crop format/frame mismatch')
        pcm = w.readframes(row['frames'])
    if hashlib.sha256(pcm).hexdigest() != row['pcm16_sha256']:
        raise ValueError('Crop PCM mismatch')
    from audio_backend.qwen_candidates import pcm16_to_float32le
    result = array.array('f'); result.frombytes(pcm16_to_float32le(pcm))
    if sys.byteorder != 'little': result.byteswap()
    return result


def sensevoice_call(model, data):
    import numpy as np
    stream = model.create_stream()
    stream.accept_waveform(16000, np.frombuffer(data, dtype=np.float32))
    model.decode_stream(stream)
    r = stream.result
    if not isinstance(r.text, str) or len(r.text.encode()) > 65536:
        raise ValueError('Invalid/unbounded SenseVoice result')
    return {'status':'success','text':r.text,'raw':{'language':str(r.lang)},'truncated':False}


def qwen_call(model, api, data):
    from audio_backend.qwen_candidate_runner import decode_window
    native = decode_window(model, api, data)
    status = {'returned_text':'success','returned_empty':'success','output_truncated':'truncated',
              'aborted':'aborted','native_error':'model_failure','schema_error':'unknown'}[native['status']]
    if native['issues'] or not native['truncation_available'] or type(native['native_status']) is not int or not 0 <= native['native_status'] <= 18:
        status = 'unknown'
    if native['native_status'] in set(range(1,19)) - {13,18}: status = 'infrastructure_failure'
    return {'status':status,'text':native['text'] or '', 'raw':native,'truncated':native['was_truncated'] is True}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('arm', choices=('sensevoice','qwen'))
    ap.add_argument('--backend', type=Path, required=True)
    ap.add_argument('--crops', type=Path, required=True)
    ap.add_argument('--model', type=Path, required=True)
    ap.add_argument('--tokens', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--i-understand-model-execution', action='store_true', required=True)
    args = ap.parse_args()
    out = external_output(args.output, args.backend)
    if out.exists(): raise ValueError('Never overwrite or implicitly retry an arm')
    if sys.version_info[:2] != (3,12): raise ValueError('Pinned Python 3.12 required')
    if any(os.environ.get(k) for k in ('LD_PRELOAD','LD_LIBRARY_PATH')):
        raise ValueError('Native loader overrides are not admitted')
    sys.path.insert(0, str(backend(args.backend)))
    pins = read(HERE/'assets.json'); runtime = read(HERE/'runtime.json')
    rows = read(HERE/'selection.json')
    # Verify ALL crops before any model load. No reference text enters the recognizer.
    for row in rows: samples(args.crops/(row['id']+'.wav'), row)
    model = api = None
    if args.arm == 'sensevoice':
        for package in ('sherpa-onnx','numpy'):
            if importlib.metadata.version(package) != runtime[package]: raise ValueError('Runtime version differs: '+package)
        verify(args.model, pins['sensevoice-model.int8.onnx'])
        if not args.tokens: raise ValueError('SenseVoice requires --tokens')
        verify(args.tokens, pins['sensevoice-tokens.txt'])
        import sherpa_onnx
        model = sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(args.model),tokens=str(args.tokens),
                    num_threads=2,language='auto',use_itn=True,provider='cpu')
    else:
        for package in ('transcribe-cpp','transcribe-cpp-native'):
            if importlib.metadata.version(package) != runtime[package]: raise ValueError('Runtime version differs: '+package)
        site = Path(sysconfig.get_paths()['purelib'])
        for entry in runtime['qwen_files']: verify(site/entry['path'], entry)
        verify(args.model, pins['Qwen3-ASR-0.6B-Q5_K_M.gguf'])
        import transcribe_cpp as api
        if api.__version__ != '0.2.3' or api.native_commit() not in (runtime['native_commit'], runtime['native_commit'][:7]):
            raise ValueError('Native commit/version differs')
        native = next(e for e in runtime['qwen_files'] if e['path'].endswith('/libtranscribe.so'))
        verify(Path(api.library_path()), native)
        model = api.Model(str(args.model), backend='cpu')
        if model.backend.casefold() != 'cpu' or model.device.kind != 'cpu' or model.device.device_type != 'cpu' or model.arch != 'qwen3_asr' or model.variant != 'qwen3-asr-0.6b':
            model.close(); raise ValueError('Actual model/device differs')
    try:
        # Exclusive create: failures remain visible, no accidental replacement.
        with out.open('x') as f:
            for row in rows:
                try:
                    data = samples(args.crops/(row['id']+'.wav'),row)
                    result = sensevoice_call(model,data) if args.arm == 'sensevoice' else qwen_call(model,api,data)
                except Exception as exc:
                    result = {'status':'infrastructure_failure','text':'','raw':{'exception_type':type(exc).__name__},'truncated':False}
                f.write(json.dumps(dict(id=row['id'],arm=args.arm,**result),ensure_ascii=False)+'\n'); f.flush()
    finally:
        if args.arm == 'qwen': model.close()

if __name__ == '__main__': main()
