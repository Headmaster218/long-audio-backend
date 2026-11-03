"""Bounded declaration reports; no claims of real continuity or ingest readiness."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

import pytest
from audio_backend import recorder_sequence as s
from audio_backend.recorder_snapshot import create_snapshot
from audio_backend.recorder_metadata import decode
from test_recorder_metadata import metadata,utf,i


def row(seq=0,**changes):
    values=dict(sequence=seq,start=seq*4,frames=4,target=4,close='ROTATION',seam=seq>0)
    values.update(changes)
    local=metadata(**values);parsed=decode(local)
    return dict(input_index=seq,metadata=parsed,local_metadata_sha256=hashlib.sha256(local).hexdigest(),
                delivery=dict(capture_identity_sha256=hashlib.sha256(parsed['capture_id'].encode()).hexdigest(),wav_sha256=parsed['wav_sha256']))


def report(*rows):return s._report(list(rows))
def edge(*rows):return report(*rows)['captures'][0]['edges'][0]


def test_shuffled_exact_declarations_report_adjacency_not_continuity():
    a=row(0);b=row(1);a['input_index']=1;b['input_index']=0
    r=report(b,a);e=r['captures'][0]['edges'][0]
    assert e['relation']=='declared_adjacent' and not e['physical_continuity_proven']
    assert e['previous_input']==1 and e['current_input']==0
    assert not r['strict_import_ready'] and r['hardware_frame_mapping']=='UNESTABLISHED'


@pytest.mark.parametrize('change,reason',[
    ({'start':5},'declared_frame_gap'),({'start':3},'declared_frame_overlap'),
    ({'run':'new'},'same_epoch_declaration_conflict'),({'rate':8000},'same_epoch_declaration_conflict'),
    ({'channels':2},'same_epoch_declaration_conflict'),({'requested':'other'},'same_epoch_declaration_conflict'),
    ({'actual':'other'},'same_epoch_declaration_conflict'),({'boundary':'UNKNOWN'},'same_epoch_declaration_conflict'),
    ({'seam':False},'previous_seam_not_trusted'),({'close':'RECOVERED_TAIL','seam':False},'recovered_tail'),
])
def test_same_epoch_unknowns_not_joined(change,reason):
    e=edge(row(0),row(1,**change))
    assert e['relation']=='unresolved_boundary' and reason in e['reasons']


def test_missing_ordinals_and_prefix_are_not_repaired():
    r=report(row(2),row(5));c=r['captures'][0];e=c['edges'][0]
    assert c['prefix_not_supplied'] and c['first_supplied_sequence']==2
    assert e['missing_ordinal_count']==2 and 'missing_ordinals' in e['reasons']
    assert not r['strict_import_ready']


@pytest.mark.parametrize('close',['STOP','INTERRUPTION','RECOVERED_TAIL'])
def test_nonrotation_predecessor(close):
    assert 'previous_segment_not_rotation' in edge(row(0,close=close),row(1))['reasons']


@pytest.mark.parametrize('change,reasons',[
    ({'epoch':1,'boundary':'ROUTE_CHANGE_UNCERTAIN','start':0,'seam':False},['epoch_boundary_unmapped']),
    ({'epoch':1,'boundary':'RESTART','run':'new','start':0,'seam':False},['epoch_boundary_unmapped']),
    ({'epoch':1,'boundary':'RESTART','start':0,'seam':False},['restart_without_new_run']),
    ({'epoch':1,'boundary':'UNKNOWN','run':'new','start':0,'seam':False},['new_run_without_restart']),
    ({'epoch':2,'boundary':'UNKNOWN','start':0,'seam':False},['missing_epoch_declarations']),
    ({'epoch':1,'boundary':'START','start':0,'seam':False},['new_epoch_start_boundary']),
    ({'epoch':1,'boundary':'UNKNOWN','seam':False},['new_epoch_nonzero_start']),
    ({'epoch':1,'boundary':'UNKNOWN','start':0},['cross_epoch_seam_conflict']),
])
def test_new_epoch_always_unmapped(change,reasons):
    e=edge(row(0),row(1,**change))
    assert e['relation']=='unresolved_boundary' and set(reasons)<=set(e['reasons'])


def test_epoch_regression_and_different_captures():
    assert 'epoch_regression' in edge(row(0,epoch=1),row(1))['reasons']
    r=report(row(0),row(1,capture='another'))
    assert len(r['captures'])==2 and all(not c['edges'] for c in r['captures'])


def test_identical_repeat_is_diagnostic_not_extra_work():
    a=row(0);b=deepcopy(a);b['input_index']=3
    c=report(a,b,row(1))['captures'][0]
    assert c['input_count']==3 and c['unique_ordinal_count']==2
    assert c['duplicates'][0]['relation']=='same_content_repeat'
    assert len(c['edges'])==1


def test_conflicting_ordinal_blocks_adjacent_claim():
    a=row(0);b=row(0,pcm='c'*64);b['input_index']=3
    c=report(a,b,row(1))['captures'][0]
    assert c['conflicting_ordinals']==[0]
    assert c['duplicates'][0]['relation']=='conflicting_ordinal_content'
    assert 'conflicting_ordinal_content' in c['edges'][0]['reasons']


def fixture(root,seq=0,**changes):
    root.mkdir()
    pcm=b'\x00\x01'*4
    wav=struct.pack('<4sI8sIHHIIHH4sI',b'RIFF',44,b'WAVEfmt ',16,1,1,16000,32000,2,16,b'data',8)+pcm
    options=dict(sequence=seq,start=seq*4,frames=4,close='ROTATION',seam=seq>0,
                 pcm=hashlib.sha256(pcm).hexdigest(),wav=hashlib.sha256(wav).hexdigest())
    options.update(changes);local=metadata(**options);parsed=decode(local)
    source='segment-'+hashlib.sha256(parsed['capture_id'].encode()).hexdigest()+'-'+str(seq)+'.ready'
    env=b'RFT1'+utf('rev')+utf(source)+i(len(local))+local
    wh=hashlib.sha256(wav).hexdigest();binding=hashlib.sha256((source+'\n'+wh+'\nrev').encode()).hexdigest()
    marker='\n'.join(('RECORDER-DELIVERY-V1','r-'+binding+'-'+'0'*32,'rev',source,str(len(wav)),wh,str(len(env)),hashlib.sha256(env).hexdigest(),''))
    (root/'delivery.v1.txt').write_text(marker);(root/'segment.v1.bin').write_bytes(env);(root/'audio.wav').write_bytes(wav)
    dest=root.parent/(root.name+'-snapshot');create_snapshot(root,dest);return dest


def test_verified_snapshot_integration_preserves_every_byte(tmp_path):
    a=fixture(tmp_path/'a');b=fixture(tmp_path/'b',1)
    before={str(p):p.read_bytes() for root in (a,b) for p in root.iterdir()}
    r=s.reconcile_snapshots([b,a])
    assert r['observed_wav_bytes']==104 and r['captures'][0]['edges'][0]['relation']=='declared_adjacent'
    assert before=={str(p):p.read_bytes() for root in (a,b) for p in root.iterdir()}


def test_tampering_never_produces_partial_success_report(tmp_path):
    a=fixture(tmp_path/'a');b=fixture(tmp_path/'b',1);(b/'audio.wav').write_bytes(b'changed')
    with pytest.raises(s.Refused):s.reconcile_snapshots([a,b])


@pytest.mark.parametrize('paths',[[],[None]*33,'not-a-list'])
def test_input_count_bounds(paths):
    with pytest.raises(s.Refused,match='snapshot_count'):s.reconcile_snapshots(paths)


def test_total_byte_precheck(tmp_path,monkeypatch):
    a=fixture(tmp_path/'a');monkeypatch.setattr(s,'MAX_INPUT_BYTES',51)
    with pytest.raises(s.Refused,match='input_bytes'):s.reconcile_snapshots([a])


def test_cli(tmp_path,capsys):
    a=fixture(tmp_path/'a')
    assert s.main([str(a)])==0
    r=json.loads(capsys.readouterr().out);assert not r['strict_import_ready']
    assert 'captures' in r and str(a) not in json.dumps(r)


def test_target_change_is_unresolved_even_with_matching_frame_extent():
    result=edge(row(0),row(1,target=8,close='STOP'))
    assert 'same_epoch_declaration_conflict' in result['reasons']


def test_initial_subset_and_terminal_close_never_prove_complete_capture():
    result=report(row(0,start=4,close='STOP'))['captures'][0]
    assert result['predecessor_not_supplied'] and not result['capture_completeness_proven']
    assert result['last_supplied_close']=='STOP'


def anchored_rows():
    a,b=row(0),row(1)
    aa=decode(metadata(version=2))['anchors']
    for observation in aa['observations']:observation['samples_after']=8
    aa['sample_end']=4
    bb=deepcopy(aa);bb['sample_start']=4;bb['sample_end']=8
    a['metadata']['anchors']=aa;b['metadata']['anchors']=bb
    return a,b


def test_anchor_observation_warnings_never_become_clock_proof():
    a,b=anchored_rows()
    a['metadata']['anchors'].update(flags=2,anomaly_count=1,unavailable_count=1)
    e=edge(a,b)
    assert e['relation']=='declared_adjacent'
    assert {'anchor_anomaly_observations_present','anchor_queries_unavailable'}<=set(e['warnings'])
    assert e['hardware_frame_mapping']=='UNESTABLISHED' and not e['physical_continuity_proven']


def test_matching_shared_read_is_allowed_as_declaration_only():
    a,b=anchored_rows()
    assert edge(a,b)['relation']=='declared_adjacent'


@pytest.mark.parametrize('mutation,reason',[
    ('mode','observation_mode_changed'),('domain','anchor_clock_domain_changed'),
    ('extent','anchor_run_samples_discontinuous'),('ordinal','anchor_read_ordinals_discontinuous'),
    ('shared','shared_read_observation_conflict'),('read_extent','anchor_read_samples_discontinuous'),
])
def test_cross_segment_anchor_contradictions_are_unresolved(mutation,reason):
    a,b=anchored_rows();anchor=b['metadata']['anchors']
    if mutation=='mode':b['metadata']['anchors']=None
    elif mutation=='domain':anchor['observations'][0]['domain']='00000000-0000-0000-0000-000000000002'
    elif mutation=='extent':anchor['sample_start']=5
    elif mutation=='ordinal':anchor['observations'][0]['ordinal']=2
    elif mutation=='shared':anchor['observations'][0]['timestamp']['nano_time']=123
    elif mutation=='read_extent':anchor['observations'][0]['ordinal']=1
    result=edge(a,b)
    assert result['relation']=='unresolved_boundary' and reason in result['reasons']


def test_next_consecutive_distinct_read_is_allowed():
    a,b=anchored_rows()
    for observation in a['metadata']['anchors']['observations']:observation['samples_after']=4
    for observation in b['metadata']['anchors']['observations']:
        observation['ordinal']=1;observation['samples_before']=4
    assert edge(a,b)['relation']=='declared_adjacent'
