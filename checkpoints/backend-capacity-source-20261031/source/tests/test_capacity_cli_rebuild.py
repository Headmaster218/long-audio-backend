"""Fresh CLI admission/binding checks; no model or service launch."""
import pytest
from audio_backend import __main__ as cli

@pytest.mark.parametrize('flag,value',[
 ('--lexical-evidence-session-mib','0'),('--lexical-evidence-session-mib','1025'),
 ('--recording-capture-mib','65537'),('--recording-total-mib','0'),
 ('--strict-primary-lifetime-records','131073'),('--strict-primary-receipt-records','20001'),
 ('--strict-payload-source-storage','bad')])
def test_invalid_before_lock(flag,value,monkeypatch,tmp_path):
 from audio_backend.resources import InstanceLock
 monkeypatch.setattr(InstanceLock,'adopt',lambda *a:pytest.fail('invalid settings reached lock'))
 with pytest.raises(SystemExit) as e:cli.main([flag,value,'--instance-lock-fd','123','--data-dir',str(tmp_path)])
 assert e.value.code==2

@pytest.mark.parametrize('env,flag,value,attr',[
 ('AUDIO_LEXICAL_EVIDENCE_SESSION_MIB','--lexical-evidence-session-mib','256','lexical_evidence_session_mib'),
 ('AUDIO_RECORDING_CAPTURE_MIB','--recording-capture-mib','2048','recording_capture_mib'),
 ('AUDIO_RECORDING_TOTAL_MIB','--recording-total-mib','8192','recording_total_mib'),
 ('AUDIO_STRICT_PRIMARY_LIFETIME_RECORDS','--strict-primary-lifetime-records','32768','strict_primary_lifetime_records'),
 ('AUDIO_STRICT_PRIMARY_RECEIPT_RECORDS','--strict-primary-receipt-records','12000','strict_primary_receipt_records'),
 ('AUDIO_STRICT_PAYLOAD_SOURCE_STORAGE','--strict-payload-source-storage','inline','strict_payload_source_storage')])
def test_cli_overrides_invalid_environment(env,flag,value,attr,monkeypatch):
 monkeypatch.setenv(env,'invalid');monkeypatch.setattr(cli,'_main',lambda args,*a:args)
 args=cli.main([flag,value]);assert str(getattr(args,attr))==value


def test_shared_parse_and_defaults(monkeypatch,tmp_path):
 from audio_backend import resources
 class Hold:
  def __init__(self,path):pass
  def adopt(self,fd):assert fd==123
  def release(self):pass
 monkeypatch.setattr(resources,'InstanceLock',Hold);monkeypatch.setattr(cli,'_main',lambda args,*a:args)
 args=cli.main(['--strict-primary','--strict-primary-policy','strict-primary-payload-v2',
  '--strict-payload-source-storage','shared-source-v1','--instance-lock-fd','123','--data-dir',str(tmp_path)])
 assert args.strict_payload_source_storage=='shared-source-v1' and cli._STRICT_LAUNCHER_OWNER is None
 defaults=cli.main([])
 assert (defaults.recording_capture_mib,defaults.recording_total_mib,defaults.lexical_evidence_session_mib)==(1024,4096,32)
 assert defaults.strict_primary_lifetime_records==2056 and defaults.strict_payload_source_storage=='inline'


def test_numeric_settings_binding(monkeypatch,tmp_path):
 import uvicorn
 calls=[];monkeypatch.setattr(uvicorn,'run',lambda app,**kw:calls.append(app.state.settings))
 cli.main(['--data-dir',str(tmp_path),'--model-dir',str(tmp_path/'unused'),
  '--recording-capture-mib','2048','--recording-total-mib','8192','--lexical-evidence-session-mib','256',
  '--strict-primary-lifetime-records','32768','--strict-primary-receipt-records','12000'])
 s=calls[0]
 assert (s.recording_capture_bytes,s.recording_total_bytes,s.lexical_evidence_session_bytes)==(2048*1024**2,8192*1024**2,256*1024**2)
 assert s.strict_primary_lifetime_records==32768 and s.strict_primary_receipt_records==12000
