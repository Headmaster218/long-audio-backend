import gzip
import io
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import package_source as package
from package_source import sanitized_json

def test_report_vectors_removed_recursively():
    source={'windows':[{'raw':{'speaker_embedding':[.1]*512,'segments':[{'embedding':[.2]*192,'text':'hello'}]}}], 'model':{'embedding_dimension':512,'embedding_version':'sha256:abc'},'cosine_scores':[.2,.7]}
    clean=sanitized_json(source)
    assert 'speaker_embedding' not in clean['windows'][0]['raw']
    assert clean['windows'][0]['raw']['segments']==[{'text':'hello'}]
    assert clean['model']==source['model']
    assert clean['cosine_scores']==[.2,.7]
    assert len(source['windows'][0]['raw']['speaker_embedding'])==512


def test_centroid_metric_summary_is_not_a_vector():
    source={'centroid':{'accepted':33,'recall':.94},'embedding_version':'abc'}
    assert sanitized_json(source)==source


def test_sqlite_json_text_vectors_are_removed_without_altering_transcript_text():
    import json
    encoded=json.dumps([.1]*512)
    source={'embedding':encoded,'embedding_version':'sha256:abc',
            'detail':json.dumps({'speaker_embedding':json.loads(encoded),'reason':'test'}),
            'provenance_json':json.dumps({'nested':{'voice_embedding':encoded},'score':.9}),
            'text':'{"embedding": [1, 2, 3]}'}
    clean=sanitized_json(source)
    assert 'embedding' not in clean
    assert json.loads(clean['detail'])=={'reason':'test'}
    assert json.loads(clean['provenance_json'])=={'nested':{},'score':.9}
    assert clean['text']==source['text']
    assert clean['embedding_version']=='sha256:abc'
    assert source['embedding']==encoded


def test_cpp_setup_manifest_and_requirements_are_packaged():
    from package_source import selected_files,ROOT
    names={p.relative_to(ROOT).as_posix() for p in selected_files()}
    assert {'moss-td-cpp-model-manifest.json','requirements-moss-td-cpp.txt',
            'scripts/setup_moss_td_cpp.py','audio_backend/moss_td_cpp_assets.py'} <= names
    assert not any(name.endswith('.gguf') or '/site-packages/' in name for name in names)


def test_experimental_identity_dataclass_and_encoded_state_never_export_vectors():
    import json
    state={'anchors':{'A':[{'evidence':{'vector':[.1,.2,.3],'support_hash':'abc','intervals':[[0,16000]]},'score':.9}]},
           'events':[{'evidence':{'vector':[.4,.5,.6]}}],
           'anchor_vectors':[[.7,.8,.9]],'negative_vector':'[0.2, 0.3, 0.4]',
           'automatic_vectors':{'B':[.3,.4,.5]},'learned_vectors':[.2,.1],
           'observation_vectors':[.1,.1]}
    clean=sanitized_json({'payload':json.dumps(state),'state':json.dumps(state),
                          'direct':state,'text':json.dumps({'vector':[1,2,3]})})
    expected={'anchors':{'A':[{'evidence':{'support_hash':'abc','intervals':[[0,16000]]},'score':.9}]},'events':[{'evidence':{}}]}
    assert json.loads(clean['payload'])==json.loads(clean['state'])==clean['direct']==expected
    assert clean['text']==json.dumps({'vector':[1,2,3]})


def test_vector_free_summary_counts_survive_privacy_sanitization():
    source={'anchor_vectors':3,'vectors':{'retained':4,'retracted':1},'scores':[.4,.5],'hashes':['abc','def']}
    assert sanitized_json(source)==source


def test_private_validation_and_report_caches_are_excluded_before_redaction(tmp_path,monkeypatch):
    import package_source as package
    monkeypatch.setattr(package,'ROOT',tmp_path)
    for name in ('validation/encoder/private/cache.json','validation/private_vectors/cache.json',
                 'reports/study/private/copy.json','reports/private_observations/cache.json',
                 'reports/study/public.json'):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{}')
    paths={p.relative_to(tmp_path).as_posix() for p in package.selected_files()}
    assert paths=={'reports/study/public.json'}


def test_only_explicit_owned_v2_reproduction_helpers_are_included(tmp_path,monkeypatch):
    import package_source as package
    monkeypatch.setattr(package,'ROOT',tmp_path)
    for name in ('validation/eres2net_v2_identity/study.py','validation/eres2net_v2_identity/runner.py',
                 'validation/eres2net_v2_identity/provenance/vendor.py','validation/eres2net_v2_identity/private/private.py'):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# fixture')
    paths={p.relative_to(tmp_path).as_posix() for p in package.selected_files()}
    assert paths=={'validation/eres2net_v2_identity/study.py','validation/eres2net_v2_identity/runner.py'}


def write_evidence(tmp_path,monkeypatch,data,name='reports/study/evidence.json.gz'):
    monkeypatch.setattr(package,'ROOT',tmp_path)
    path=tmp_path/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data)
    return path


def test_gzip_nested_private_vectors_are_removed_and_output_is_deterministic(tmp_path,monkeypatch):
    source={'windows':[{'speaker_embedding':[.1,.2],'score':.9}],
            'payload':json.dumps({'nested':{'voiceprint':'[0.3, 0.4]','reason':'test'}}),
            'centroid':{'accepted':3},'cosine_scores':[.2,.7]}
    original=gzip.compress(json.dumps(source).encode(),mtime=12345)
    path=write_evidence(tmp_path,monkeypatch,original)
    data,changed=package.packaged_bytes(path)
    assert changed is True
    clean=json.loads(gzip.decompress(data))
    assert clean['windows']==[{'score':.9}]
    assert json.loads(clean['payload'])=={'nested':{'reason':'test'}}
    assert clean['centroid']==source['centroid']
    assert clean['cosine_scores']==source['cosine_scores']
    assert data[4:8]==b'\0\0\0\0'  # Fixed gzip mtime.
    assert data[3]&8==0  # No source filename in the gzip header.
    assert package.packaged_bytes(path)==(data,True)
    assert path.read_bytes()==original  # The source evidence stays untouched.


@pytest.mark.parametrize('compressed',[False,True])
def test_unchanged_evidence_is_preserved_byte_for_byte(tmp_path,monkeypatch,compressed):
    original=b'{ "scores" : [0.1, 0.2], "embedding_dimension": 512 }\n\n'
    if compressed:
        output=io.BytesIO()
        with gzip.GzipFile(filename='original-evidence.json',fileobj=output,mode='wb',mtime=123456,compresslevel=1) as stream:
            stream.write(original)
        original=output.getvalue()
    name='reports/study/evidence.json'+('.gz' if compressed else '')
    path=write_evidence(tmp_path,monkeypatch,original,name)
    assert package.packaged_bytes(path)==(original,False)


@pytest.mark.parametrize('data',[
    b'not gzip',
    b'\x1f\x8b',
    gzip.compress(b'{}')[:-1],
    gzip.compress(b'{}')[:-8]+b'\0'*8,  # Invalid CRC/length trailer.
    gzip.compress(b'{}')+b'invalid trailing gzip data',
    gzip.compress(b'not JSON'),
    gzip.compress(b'{"broken":'),
    gzip.compress(b'\xff'),
    gzip.compress(b''),
    gzip.compress(b'{}')+gzip.compress(b'{}'),
])
def test_malformed_gzip_or_json_is_rejected(tmp_path,monkeypatch,data):
    path=write_evidence(tmp_path,monkeypatch,data)
    with pytest.raises(ValueError,match='Invalid structured evidence, refusing to package'):
        package.packaged_bytes(path)


@pytest.mark.parametrize('compressed',[False,True])
@pytest.mark.parametrize('data',[
    b'{"embedding":[1,2],"embedding":3}',
    b'{"nested":{"x":1,"x":2}}',
    b'{"payload":"{\\"embedding\\":[1,2],\\"embedding\\":3}"}',
    b'{"score":NaN}',
    b'['*1100+b'0'+b']'*1100,
])
def test_ambiguous_or_unparseable_structured_evidence_fails_closed(tmp_path,monkeypatch,data,compressed):
    if compressed:data=gzip.compress(data)
    path=write_evidence(tmp_path,monkeypatch,data,'validation/evidence.json'+('.gz' if compressed else ''))
    with pytest.raises(ValueError,match='Invalid structured evidence, refusing to package'):
        package.packaged_bytes(path)


@pytest.mark.parametrize('compressed',[False,True])
def test_structured_input_size_is_bounded_before_parsing(tmp_path,monkeypatch,compressed):
    monkeypatch.setattr(package,'MAX_STRUCTURED_BYTES',64)
    data=b'{"text":"'+b'a'*128+b'"}'
    # An oversized gzip filename makes even a tiny valid report exceed the input cap.
    if compressed:
        output=io.BytesIO()
        with gzip.GzipFile(filename='x'*128,fileobj=output,mode='wb',mtime=0) as stream:
            stream.write(b'{}')
        data=output.getvalue()
    path=write_evidence(tmp_path,monkeypatch,data,'reports/evidence.json'+('.gz' if compressed else ''))
    with pytest.raises(ValueError,match='input exceeds byte limit'):
        package.packaged_bytes(path)


def test_gzip_expansion_is_bounded_across_concatenated_members(tmp_path,monkeypatch):
    monkeypatch.setattr(package,'MAX_STRUCTURED_BYTES',128)
    data=gzip.compress(b'{}'+b' '*62)+gzip.compress(b' '*65)
    assert len(data)<128
    path=write_evidence(tmp_path,monkeypatch,data)
    reads=[]
    original_read=gzip.GzipFile.read

    def bounded_read(self,size=-1):
        reads.append(size)
        return original_read(self,size)

    monkeypatch.setattr(gzip.GzipFile,'read',bounded_read)
    with pytest.raises(ValueError,match='Expanded structured evidence exceeds byte limit'):
        package.packaged_bytes(path)
    assert reads==[129]


def test_gzip_exact_expansion_limit_is_accepted_without_recompression(tmp_path,monkeypatch):
    monkeypatch.setattr(package,'MAX_STRUCTURED_BYTES',128)
    data=gzip.compress(b'{}'+b' '*126,mtime=54321)
    path=write_evidence(tmp_path,monkeypatch,data)
    assert package.packaged_bytes(path)==(data,False)


def test_owned_helpers_and_public_gzip_use_fixed_allowlists_not_manifest_paths(tmp_path,monkeypatch):
    monkeypatch.setattr(package,'ROOT',tmp_path)
    base='validation/eres2net_v2_identity/'
    owned={base+name for name in (
        'study.py','runner.py','acquire_model.py','write_report.py','test_study.py','finalize.py',
        'parity/acquire.py','parity/worker.py','parity/run_guarded.py',
        'parity/diagnose_frontend.py','parity/diagnose_frontend_torch.py',
        'author_frontend/corrected.py','v1_author_control/control.py')}
    public={base+folder+'/'+name+'.json.gz' for folder in ('author_frontend','v1_author_control')
            for name in ('calibration-frozen','replay')}
    forbidden={base+name for name in (
        'provenance/vendor.py','parity/vendor.py','parity/source/worker.py','parity/private/worker.py',
        'parity/private_features/worker.py','author_frontend/new_helper.py','private/study.py',
        'author_frontend/unreviewed.json.gz','private/replay.json.gz','private_vectors/replay.json.gz')}
    forbidden.add('reports/unreviewed.json.gz')
    for name in owned|public|forbidden:
        path=tmp_path/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(gzip.compress(b'{}') if name.endswith('.gz') else b'# fixture')
    manifest=base+'public/packaging-manifest.json'
    path=tmp_path/manifest
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'owned_python_execution_helpers':[{'path':name} for name in forbidden],
                                'public_gzip_allowlist':list(forbidden)}))
    paths={p.relative_to(tmp_path).as_posix() for p in package.selected_files()}
    assert paths==owned|public|{manifest}
    assert len(owned)==13 and len(public)==4


@pytest.mark.parametrize('root',['reports','validation','benchmarks','scripts','docs','tests'])
@pytest.mark.parametrize('private',['private','private_vectors'])
def test_private_path_components_are_excluded_consistently(tmp_path,monkeypatch,root,private):
    monkeypatch.setattr(package,'ROOT',tmp_path)
    for name in (f'{root}/study/{private}/manifest.json',f'{root}/{private}/nested/README.md',
                 f'{root}/study/public/manifest.json'):
        path=tmp_path/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('{}')
    paths={p.relative_to(tmp_path).as_posix() for p in package.selected_files()}
    assert paths=={f'{root}/study/public/manifest.json'}
