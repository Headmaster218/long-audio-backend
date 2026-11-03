"""Producer declaration validation, never microphone or import authority."""
import hashlib
import struct

import pytest
from audio_backend import recorder_metadata as m
from audio_backend import recorder_delivery as delivery


def utf(value):
    units = value.encode('utf-16-be', 'surrogatepass'); out = bytearray()
    for i in range(0, len(units), 2):
        v = int.from_bytes(units[i:i+2], 'big')
        if 1 <= v <= 127: out.append(v)
        elif v <= 2047: out.extend((0xc0 | v >> 6, 0x80 | v & 63))
        else: out.extend((0xe0 | v >> 12, 0x80 | (v >> 6) & 63, 0x80 | v & 63))
    return struct.pack('>H',len(out)) + out


def i(value): return struct.pack('>i', value)
def q(value): return struct.pack('>q', value)
def seal(data): return data + hashlib.sha256(data).digest()


def observation(**changes):
    v = dict(domain='00000000-0000-0000-0000-000000000001', run='run', ordinal=0,
             samples_before=0, samples_after=3, read_before=1, read_after=2,
             wall_before=3, wall_millis=4, wall_after=5, uptime=6,
             config_before=0, config_after=0, flags=1, status='AVAILABLE', result=0,
             position=2**32+5, nanos=1, query_before=1, query_after=2)
    v.update(changes)
    return (utf(v['domain'])+utf(v['run'])+b''.join(q(v[k]) for k in
            ('ordinal','samples_before','samples_after','read_before','read_after','wall_before','wall_millis','wall_after','uptime','config_before','config_after'))
            +i(v['flags'])+utf(v['status'])+i(v['result'])+b''.join(q(v[k]) for k in ('position','nanos','query_before','query_after')))


def anchors(**changes):
    v = dict(start=0,end=3,reads=1,queries=1,unavailable=0,anomalies=0,flags=1,
             first=observation(),last=observation(),fq=observation(),lq=observation(),fa=observation(),la=observation(),mapping='UNESTABLISHED')
    v.update(changes)
    data = i(1)+i(1)+utf(v['mapping'])+b''.join(q(v[k]) for k in ('start','end','reads','queries','unavailable','anomalies'))+i(v['flags'])+v['first']+v['last']
    if v['queries']>0:data+=v['fq']+v['lq']
    if v['queries']>v['unavailable']:data+=v['fa']+v['la']
    return data


def metadata(**changes):
    v = dict(version=1,capture='synthetic-中文-🎵',run='run',epoch=0,rate=16000,channels=1,
             requested='phone',actual='phone',boundary='START',sequence=0,start=0,target=4,frames=3,
             pcm='a'*64,wav='b'*64,close='STOP',seam=0,anchor=anchors())
    v.update(changes)
    data = b'PCM1'+i(v['version'])+utf(v['capture'])+utf(v['run'])+q(v['epoch'])+i(v['rate'])+i(v['channels'])
    data += utf(v['requested'])+utf(v['actual'])+utf(v['boundary'])+b''.join(q(v[k]) for k in ('sequence','start','target','frames'))
    data += utf(v['pcm'])+utf(v['wav'])+utf(v['close'])+bytes([v['seam']])
    if v['version']==2:data+=v['anchor']
    return seal(data)


@pytest.mark.parametrize('version',[1,2])
def test_semantic_decode(version):
    result=m.decode(metadata(version=version))
    assert result['capture_id']=='synthetic-中文-🎵' and result['frames']==3
    if version==2:
        assert result['anchors']['mapping']=='UNESTABLISHED'
        assert result['anchors']['observations'][0]['timestamp']['frame_position']==2**32+5


@pytest.mark.parametrize('change',[
    {'epoch':-1},{'rate':0},{'channels':0},{'channels':33},{'boundary':'NEW'},
    {'sequence':-1},{'start':-1},{'target':0},{'frames':0},{'frames':5},
    {'target':2**32},{'rate':2**31-1,'channels':2},{'start':2**63-2},
    {'pcm':'x'*64},{'wav':'b'*63},{'close':'NEW'},{'close':'ROTATION'},
    {'close':'RECOVERED_TAIL','seam':1},{'seam':2},{'capture':' \x00\t'},
    {'run':'x'*513},{'capture':'🎵'*257},{'capture':'\ud800'},
])
def test_invalid_declarations_refused_with_valid_checksum(change):
    with pytest.raises(m.InvalidMetadata):m.decode(metadata(**change))


def test_recovered_tail_and_rotation_rules():
    assert not m.decode(metadata(close='RECOVERED_TAIL'))['trusted_previous_seam']
    assert m.decode(metadata(close='ROTATION',frames=4,seam=1))['frames']==4


def test_java_text_and_canonical_modified_utf():
    assert m.decode(metadata(capture='\u00a0'))['capture_id']=='\u00a0'
    assert m.modified_utf8(b'\xc0\x80')=='\0'
    for data in (b'\0',b'\xc0\x81',b'\xc1\xbf',b'\xe0\x80\x80',b'\xf0\x9f\x8e\xb5',b'\x80',b'\xe4\xb8'):
        with pytest.raises(m.InvalidMetadata):m.modified_utf8(data)
    assert m.decode(metadata(capture='🎵'*256))['capture_id']=='🎵'*256


@pytest.mark.parametrize('change',[
    {'start':-1},{'end':4},{'reads':0},{'reads':2},{'queries':2},{'unavailable':2},
    {'anomalies':2},{'flags':0},{'flags':256},{'mapping':'ESTABLISHED'},
    {'first':observation(run='other')},{'last':observation(domain='00000000-0000-0000-0000-000000000002')},
    {'fq':observation(ordinal=2)},{'fa':observation(ordinal=2)},
    {'fa':observation(status='UNAVAILABLE',result=1,position=0,nanos=0)},
])
def test_anchor_semantic_refusals(change):
    with pytest.raises(m.InvalidMetadata):m.decode(metadata(version=2,anchor=anchors(**change)))


@pytest.mark.parametrize('change',[
    {'domain':'not-uuid'},{'samples_before':-1},{'samples_after':0},{'ordinal':-1},
    {'query_before':-1},{'flags':256},{'status':'AVAILABLE','result':1},
    {'status':'UNAVAILABLE','result':0},{'status':'UNAVAILABLE','result':1},
    {'status':'NOT_POLLED','position':0,'nanos':0},{'status':'NEW'},
])
def test_bad_read_observations(change):
    with pytest.raises(m.InvalidMetadata):m.decode(metadata(version=2,anchor=anchors(first=observation(**change))))


@pytest.mark.parametrize('status,result,queries,unavailable',[
    ('NOT_POLLED',0,0,0),('UNAVAILABLE',1,1,1),('QUERY_EXCEPTION',0,1,1)])
def test_unavailable_observations(status,result,queries,unavailable):
    obs=observation(status=status,result=result,position=0,nanos=0,query_before=0,query_after=0)
    a=anchors(queries=queries,unavailable=unavailable,first=obs,last=obs,fq=obs,lq=obs)
    assert m.decode(metadata(version=2,anchor=a))['anchors']['query_count']==queries


def test_raw_negative_and_regressing_timestamp_is_not_silently_corrected():
    obs=observation(position=-3,nanos=-4,query_before=8,query_after=1,wall_millis=-1)
    a=anchors(first=obs,last=obs,fq=obs,lq=obs,fa=obs,la=obs)
    parsed=m.decode(metadata(version=2,anchor=a))['anchors']['observations'][0]
    assert parsed['timestamp']['frame_position']==-3 and parsed['timestamp']['query_after']==1


def test_truncation_checksum_trailing_unknown_version():
    data=metadata()
    for invalid in (data[:-1],data+b'x',seal(data[:-32]+b'x'),metadata(version=3),seal(b'BAD1'+data[4:-32])):
        with pytest.raises(m.InvalidMetadata):m.decode(invalid)
    # Every proper prefix is safely rejected, including structural truncation
    # whose checksum is freshly recomputed.
    for n in range(len(data)-32):
        with pytest.raises(m.InvalidMetadata):m.decode(seal(data[:n]))


def test_source_and_wav_binding():
    parsed=m.decode(metadata())
    report={k:parsed[k] for k in ('frames','sample_rate_hz','channels','pcm_sha256','wav_sha256')}
    report['source_id']='segment-'+hashlib.sha256(parsed['capture_id'].encode()).hexdigest()+'-0.ready'
    assert m.check_delivery_binding(parsed,report)
    for key in report:
        bad=dict(report); bad[key]=0 if isinstance(bad[key],int) else 'wrong'
        with pytest.raises(m.InvalidMetadata):m.check_delivery_binding(parsed,bad)


def test_strict_opt_in_rejects_opaque_transport_fixture(tmp_path):
    from test_recorder_delivery import fixture
    fixture(tmp_path)
    assert delivery.inspect_delivery(tmp_path)['metadata_semantics_validated'] is False
    with pytest.raises(delivery.Refused):delivery.inspect_delivery(tmp_path,validate_metadata=True)


def test_max_signed_rate_mono_fits_unsigned_riff_byte_rate():
    assert m.decode(metadata(rate=2**31-1))['sample_rate_hz']==2**31-1


def semantic_delivery(root, **changes):
    pcm=b'\x00\x01'*3
    wav=struct.pack('<4sI8sIHHIIHH4sI',b'RIFF',42,b'WAVEfmt ',16,1,1,16000,32000,2,16,b'data',6)+pcm
    fields=dict(pcm=hashlib.sha256(pcm).hexdigest(),wav=hashlib.sha256(wav).hexdigest())
    fields.update(changes)
    local=metadata(**fields)
    source='segment-'+hashlib.sha256('synthetic-中文-🎵'.encode()).hexdigest()+'-0.ready'
    envelope=b'RFT1'+utf('revision')+utf(source)+i(len(local))+local
    wh=hashlib.sha256(wav).hexdigest(); mh=hashlib.sha256(envelope).hexdigest()
    binding=hashlib.sha256((source+'\n'+wh+'\nrevision').encode()).hexdigest()
    marker='\n'.join(('RECORDER-DELIVERY-V1','r-'+binding+'-'+'0'*32,'revision',source,str(len(wav)),wh,str(len(envelope)),mh,''))
    (root/'delivery.v1.txt').write_text(marker);(root/'segment.v1.bin').write_bytes(envelope);(root/'audio.wav').write_bytes(wav)


@pytest.mark.parametrize('version',[1,2])
def test_opt_in_semantics_binds_actual_observed_audio_and_retains_import_refusal(tmp_path,version):
    semantic_delivery(tmp_path,version=version)
    r=delivery.inspect_delivery(tmp_path,validate_metadata=True)
    assert r['metadata_semantics_validated'] and not r['strict_import_ready']
    assert r['hardware_frame_mapping']=='UNESTABLISHED'
    assert r['capture_identity_sha256']==hashlib.sha256('synthetic-中文-🎵'.encode()).hexdigest()
    assert 'capture_id' not in r and 'run_id' not in r


@pytest.mark.parametrize('change',[{'capture':'other'},{'sequence':1},{'pcm':'f'*64},{'wav':'f'*64},{'frames':2},{'rate':8000},{'channels':2}])
def test_outer_transport_success_does_not_mask_inner_audio_mismatch(tmp_path,change):
    semantic_delivery(tmp_path,**change)
    assert delivery.inspect_delivery(tmp_path)['transport_bytes_match']
    with pytest.raises(delivery.Refused,match='metadata_.*binding'):
        delivery.inspect_delivery(tmp_path,validate_metadata=True)


@pytest.mark.parametrize('version',[1,2])
def test_semantic_cli(tmp_path,capsys,version):
    import json
    semantic_delivery(tmp_path,version=version)
    assert delivery.main([str(tmp_path),'--validate-metadata'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['metadata_semantics_validated'] and not result['strict_import_ready']
    semantic_delivery(tmp_path,pcm='c'*64)
    assert delivery.main([str(tmp_path),'--validate-metadata'])==2
    assert json.loads(capsys.readouterr().out)['error']=='metadata_wav_binding_pcm_sha256'
