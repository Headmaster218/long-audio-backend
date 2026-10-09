import importlib.util
from pathlib import Path
import io,json,struct,time,wave
from datetime import datetime,timezone
import pytest
# This frozen execution helper uses Unix resource and process-group APIs.
# Windows portable production code does not import it.
pytest.importorskip('resource', reason='Linux-only bounded research execution helper')
spec=importlib.util.spec_from_file_location('array_control',Path(__file__).resolve().parents[1]/'scripts/benchmarks/run_array_identity_replacement.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def header(channels=1,rate=16000,width=2):
    out=io.BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(channels);w.setsampwidth(width);w.setframerate(rate);w.writeframes(b'\0'*(100*channels*width))
    return out.getvalue()


def test_pcm_header_offset_without_full_payload():
    raw=header();info=m.parse_wav_header(raw[:44],len(raw))
    assert info['data_offset']==44 and info['frames']==100


def test_non44_header_offset_is_respected():
    raw=header();body=raw[8:36]+b'JUNK'+struct.pack('<I',4)+b'test'+raw[36:]
    changed=b'RIFF'+struct.pack('<I',len(body))+body
    assert m.parse_wav_header(changed[:56],len(changed))['data_offset']==56


@pytest.mark.parametrize('channels,rate,width',[(2,16000,2),(1,8000,2),(1,16000,1)])
def test_unsupported_format_refused(channels,rate,width):
    raw=header(channels,rate,width)
    with pytest.raises(RuntimeError,match='Unexpected WAV format'):m.parse_wav_header(raw,len(raw))


def test_partial_header_requests_more_not_assumed44():
    raw=header();assert m.parse_wav_header(raw[:20],len(raw)) is None


def test_header_source_size_mismatch_refused():
    raw=header()
    with pytest.raises(RuntimeError,match='RIFF total'):m.parse_wav_header(raw,len(raw)+2)


def test_http200_refused_before_any_body_read():
    with pytest.raises(RuntimeError,match='body refused'):m.validate_range_response(200,{},0,9)


def test_exact206_range_required():
    assert m.validate_range_response(206,{'Content-Range':'bytes 0-9/100','Content-Length':'10'},0,9)==100
    for h in [{'Content-Range':'bytes 1-10/100','Content-Length':'10'},
              {'Content-Range':'bytes 0-9/100','Content-Length':'9'},
              {'Content-Range':'bytes 0-9/100','Content-Length':'10','Content-Encoding':'gzip'},
              {'Content-Length':'10'}]:
        with pytest.raises(RuntimeError):m.validate_range_response(206,h,0,9)


def telemetry_files(tmp_path,now,partial=False):
    metric={'wall_seconds':100,'rss_bytes':2*1024**3,'host_available_memory_bytes':3*1024**3,'disk_free_bytes':6*1024**3,'processed_media_seconds':7000}
    (tmp_path/'metrics.jsonl').write_text(json.dumps(metric)+'\n'+('{"partial"' if partial else ''))
    p={'utc':datetime.fromtimestamp(now,timezone.utc).isoformat(),'wall_seconds_this_invocation':100,'chunks':[{'status':'running'}]}
    (tmp_path/'progress.json').write_text(json.dumps(p))


def test_authoritative_telemetry_fresh_and_complete(tmp_path):
    now=time.time();telemetry_files(tmp_path,now,True)
    assert m.telemetry(now,live=tmp_path)['run_rss_bytes']==2*1024**3


def test_stale_or_stopped_telemetry_fails_closed(tmp_path):
    now=time.time();telemetry_files(tmp_path,now-20)
    with pytest.raises(RuntimeError,match='stale'):m.telemetry(now,live=tmp_path)
    telemetry_files(tmp_path,now);p=json.loads((tmp_path/'progress.json').read_text());p['chunks'][0]['status']='stopped';(tmp_path/'progress.json').write_text(json.dumps(p))
    with pytest.raises(RuntimeError,match='restart'):m.telemetry(now,live=tmp_path)


def test_stale_complete_metric_not_hidden_by_fresh_partial_write(tmp_path):
    now=time.time();telemetry_files(tmp_path,now,True)
    p=json.loads((tmp_path/'progress.json').read_text());p['wall_seconds_this_invocation']=120;(tmp_path/'progress.json').write_text(json.dumps(p))
    with pytest.raises(RuntimeError,match='contemporaneous'):m.telemetry(now,live=tmp_path)


def test_conditional_centroid_same_fixed_math_no_future_label():
    gallery={'A':[(1.,0.),(1.,0.),(1.,0.)],'B':[(0.,1.),(0.,1.),(0.,1.)]}
    q={'id':'q','recording_id':'later','speaker':'unseen','start':0.,'end':3.,'embedding':[1.,0.]}
    r=m.score_gallery(gallery,[q],{'A','B'})[0]
    assert r['assigned']=='A' and not r['known']
    q['speaker']='A';s=m.score_gallery(gallery,[q],{'A','B'})[0]
    assert s['assigned']==r['assigned'] and s['score']==r['score']


def test_pairing_rejects_missing_denominator():
    with pytest.raises(RuntimeError,match='denominator'):m.comparison([], [{'id':'q'}])


def test_overlapping_annotation_intervals_not_double_counted():
    assert m.union_seconds([(0,2),(1,3),(4,5)])==4


verifier_spec = importlib.util.spec_from_file_location(
    'array_verifier', Path(__file__).resolve().parents[1] /
    'scripts/benchmarks/verify_array_identity_replacement.py')
verifier = importlib.util.module_from_spec(verifier_spec)
verifier_spec.loader.exec_module(verifier)


def test_verifier_normalization_resists_overflow():
    assert sum(x * x for x in verifier.unit([1e308, 1e308])) == pytest.approx(1.)


@pytest.mark.parametrize('vector', [[], [0., 0.], [float('nan'), 1.], [float('inf'), 0.]])
def test_verifier_invalid_vectors_refused(vector):
    with pytest.raises(ValueError):
        verifier.unit(vector)


def test_verifier_excludes_own_incomplete_stdout(tmp_path):
    (tmp_path / 'verification.json').write_text('')
    (tmp_path / 'result.json').write_text('{}')
    assert [p.name for p in verifier.public_json_paths(tmp_path)] == ['result.json']


@pytest.mark.parametrize('value', [{'embedding': []}, {'unknown_key': [0.] * 512}])
def test_verifier_rejects_vector_exports(value):
    with pytest.raises(AssertionError):
        verifier.privacy(value)


def test_verifier_allows_aggregate_report():
    verifier.privacy({'correct': 2, 'interval': [.1, .9], 'rows': [{'status': 'abstain'}]})
