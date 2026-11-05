"""Full-attempt admission before MKD; no disk replay or capacity repair."""
import pytest
from audio_backend import recorder_receiver_completion as c
from test_recorder_receiver_completion_review import example, adapter, contents, write_one

def other(source):return source.name[:-32]+'f'*32

def test_reservation_refuses_before_directory_and_releases_successful_slack(tmp_path):
 raw,ready,state,source=example(tmp_path);cap=sum(c.LIMITS.values())
 with adapter(state,ready,reserve_full_attempt=True,max_raw_bytes=cap) as owner:
  assert owner.admission_available()
  attempt=owner.begin_attempt(source.name)
  assert owner._charged_bytes==cap and not owner.admission_available()
  with pytest.raises(c.Refused,match='full_attempt_reservation'):owner.begin_attempt(other(source))
  assert not (raw/other(source)).exists()
  for name in c.FILES:write_one(owner,attempt,source,name)
  actual=sum((source/n).stat().st_size for n in c.FILES)
  assert owner._charged_bytes==actual and owner.status(attempt)['status']=='published'
  assert not owner.admission_available()  # Full reservation, not average file size.

def test_failed_transfer_keeps_reservation_and_restart_does_not_adopt(tmp_path):
 raw,ready,state,source=example(tmp_path);cap=sum(c.LIMITS.values())
 with adapter(state,ready,reserve_full_attempt=True,max_raw_bytes=cap) as owner:
  attempt=owner.begin_attempt(source.name)
  write_one(owner,attempt,source,c.FILES[0],successful=False)
  assert owner._charged_bytes==cap and not owner.admission_available()
 before=contents(raw)
 with adapter(state,ready,reserve_full_attempt=True,max_raw_bytes=cap) as owner:
  with pytest.raises(c.Refused,match='existing_attempt'):owner.begin_attempt(source.name)
  assert owner._charged_bytes==sum(len(v) for v in before.values())
 assert contents(raw)==before and not list(ready.iterdir())

def test_uncertain_mkdir_never_releases_reservation(tmp_path,monkeypatch):
 raw,ready,state,source=example(tmp_path);cap=sum(c.LIMITS.values())
 with adapter(state,ready,reserve_full_attempt=True,max_raw_bytes=cap) as owner:
  real=c.os.mkdir
  def after(*a,**k):real(*a,**k);raise OSError('injected after creation')
  monkeypatch.setattr(c.os,'mkdir',after)
  with pytest.raises(OSError):owner.begin_attempt(source.name)
  assert owner._charged_bytes==cap and (raw/source.name).is_dir()
  with pytest.raises(c.Refused,match='members_changed'):owner.admission_available()

def test_legacy_mode_still_reserves_per_file(tmp_path):
 raw,ready,state,source=example(tmp_path)
 with adapter(state,ready,max_raw_bytes=1) as owner:
  owner.begin_attempt(source.name)
  assert (raw/source.name).is_dir() and not owner.admission_available()
