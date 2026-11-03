"""Bounded, model-free single public speech STOP producer. Never starts a server."""
import argparse, hashlib, json, pathlib, struct, subprocess, sys, wave, os

def digest(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--android-source',required=True,type=pathlib.Path); p.add_argument('--producer-checkout',required=True,type=pathlib.Path); p.add_argument('--backend-source',required=True,type=pathlib.Path); p.add_argument('--input-wav',required=True,type=pathlib.Path); p.add_argument('--output',required=True,type=pathlib.Path); a=p.parse_args(); os.umask(0o077)
 commit='2c14d6f9a2dc6ccb4514a7fa8ce15faf914a0ef9'; expected='a15017d0d3c871866971ee178052a0ce3a7e44d6fbf89fc23dcab326511b65f5'; selected='824962e469c1b5c3969804b2846b1428fe4a194a1744d5599aa595ae7f69af73'
 git=lambda *v: subprocess.check_output(['git','-C',str(a.producer_checkout),*v])
 assert git('rev-parse','HEAD').decode().strip()==commit and not git('status','--porcelain=v1')
 receipt=json.loads((a.android_source/'ASSEMBLED_SOURCE.json').read_text()); assert receipt['source_commit']==commit
 for row in receipt['files']:
  f=a.android_source/row['path']; raw=f.read_bytes(); assert not f.is_symlink() and digest(f)==row['sha256'] and raw==git('show',commit+':'+row['source_path'])
 assert not a.input_wav.is_symlink() and digest(a.input_wav)==expected
 with wave.open(str(a.input_wav),'rb') as f:
  assert (f.getnchannels(),f.getsampwidth(),f.getframerate(),f.getcomptype())==(1,2,16000,'NONE'); f.setpos(26672000); pcm=f.readframes(144000)
 assert len(pcm)==288000 and hashlib.sha256(pcm).hexdigest()==selected
 out=a.output.resolve(); assert all(out!=t.resolve() and t.resolve() not in out.parents for t in (a.android_source,a.backend_source,a.producer_checkout,pathlib.Path(__file__).parent)); out.mkdir(exist_ok=False); classes=out/'classes'; classes.mkdir(); raw=out/'public-selected.pcm'; raw.write_bytes(pcm)
 src=pathlib.Path(__file__).parent; sources=sorted((a.android_source/'app/src/main/java/io/github/headmaster218/recorder/core').glob('*.java'))+[a.android_source/'app/src/main/java/io/github/headmaster218/recorder/android/FtpsNetwork.java']+sorted((a.android_source/'app/src/testFixtures/ftps-wire/java/android').rglob('*.java'))+[src/'PublicSpeechSingleProducer.java',src/'PublicSpeechWireClient.java']; before={str(f):digest(f) for f in sources}
 flags=['-Xmx64m','-XX:MaxMetaspaceSize=48m','-XX:ReservedCodeCacheSize=8m','-XX:+UseSerialGC','-XX:ActiveProcessorCount=1','-Xss256k']
 with (out/'compile.log').open('wb') as log: subprocess.run(['java',*flags,'-m','jdk.compiler/com.sun.tools.javac.Main','-source','8','-target','8','-Xlint:all,-options','-Werror','-d',str(classes),*map(str,sources)],check=True,timeout=60,stdout=log,stderr=subprocess.STDOUT)
 with (out/'producer.log').open('wb') as log: subprocess.run(['java',*flags,'-cp',str(classes),'io.github.headmaster218.recorder.core.PublicSpeechSingleProducer',str(out/'producer'),str(raw),selected,'STOP'],check=True,timeout=60,stdout=log,stderr=subprocess.STDOUT)
 assert before=={str(f):digest(f) for f in sources}
 sys.path.insert(0,str(a.backend_source.resolve())); from audio_backend.recorder_delivery import inspect_delivery
 directory=out/'producer/deliveries/000'; report=inspect_delivery(directory,validate_metadata=True); assert report['frames']==144000 and report['sequence']==0 and report['frame_start']==0 and report['close_reason']=='STOP'
 with wave.open(str(directory/'audio.wav'),'rb') as f: assert f.readframes(144001)==pcm
 envelope=(directory/'segment.v1.bin').read_bytes(); offset=4
 for _ in range(2): offset+=2+struct.unpack_from('>H',envelope,offset)[0]
 size=struct.unpack_from('>i',envelope,offset)[0]; manifest=out/'producer/spool'/report['source_id']/'manifest.bin'; assert envelope[offset+4:]==manifest.read_bytes() and len(manifest.read_bytes())==size
 result=dict(passed=True,producer_commit=commit,verified_assembly_files=len(receipt['files']),source_wav_sha256=expected,selected_pcm_sha256=selected,source_frame_start=26672000,source_frame_end=26816000,host_origin_frame=0,source_title='AMI Consortium / University of Edinburgh, AMI Meeting Corpus, EN2002a Mix-Headset',source_url='https://groups.inf.ed.ac.uk/ami/corpus/',license_url='https://creativecommons.org/licenses/by/4.0/',synthetic_observation_timestamps=True,hardware_mapping_established=False,terminal_api='PcmSegmentWriter.stop()',delivery=report,delivery_path=str(directory),spool=str(out/'producer/spool'),manifest_sha256=digest(manifest),wav_sha256=digest(directory/'audio.wav'),production_and_fixture_source_pins=before,class_pins={str(f.relative_to(classes)):digest(f) for f in sorted(classes.rglob('*.class'))},network_test=False,native_test=False)
 (out/'FIXTURE.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n'); print(json.dumps({'passed':True,'fixture':str(out/'FIXTURE.json'),'sha256':digest(out/'FIXTURE.json')}))
if __name__=='__main__': main()
