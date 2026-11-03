"""Independent finite acceptance decision; changes no qualification or runtime."""
import hashlib,json
from pathlib import Path
B=Path('/workspace/scratch/12ec3121e93f');O=Path(__file__).resolve().parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
R=B/'backend-recorder-intake-20261103';C=B/'backend-runtime-candidate-20261103';T=B/'backend-cellstore-candidate-results02-20261103'
pins=json.loads((T/'SOURCE_PINS.json').read_text())
assert all(h(C/name)==digest for name,digest in pins.items())
prior=json.loads((B/'backend-runtime-acceptance-review-20261103/DECISION.json').read_text())
for name,item in prior['evidence'].items():assert h(B/name)==item['sha256']
assert json.loads((T/'RESULT.json').read_text())['exit_code']==0
assert '9 passed' in (T/'pytest.log').read_text()
old=(R/'audio_backend/native_owned_cell_store.py').read_text();candidate=(C/'audio_backend/native_owned_cell_store.py').read_text()
assert h(R/'audio_backend/native_owned_cell_store.py')=='efbd83e985482d47d3337e7290cddfacc680ca77c49452c904c18457211393d8'
package='6ac9a15ee2c393bd994e6b3784ed3f5fb97c9b4f986cbce03dccbad6226778df'
assert old.replace('096bb1a3902a75a2c3723f2d7863a8d3e9ed627bd862e6538eca2bc517d6e29b',package).replace('d1eafbcf6bde0071d301f488ba82e69fa3bbadc446c62775276e4998efbdbe67',h(C/'qualification/EXPERIMENTAL_PROFILE_BASIS.json'))==candidate
report=json.loads((O/'REPORT.json').read_text())
assert report['status']=='accepted_production_schema_saved_byte_evidence'
evidence_paths=['backend-cellstore-physical-evidence-20261103/RESULT.json','backend-cellstore-independent-audit-20261103/REPORT.json','backend-cellstore-independent-audit-20261103/audit.py','backend-cellstore-candidate-results02-20261103/RESULT.json','backend-cellstore-candidate-results02-20261103/SOURCE_PINS.json','backend-cellstore-candidate-results02-20261103/pytest.log','backend-cellstore-candidate-results02-20261103/two_literal_diff.patch','backend-runtime-acceptance-review-20261103/DECISION.json','backend-recorder-intake-20261103/docs/NATIVE_OWNED_CELLS.md','backend-runtime-candidate-20261103/docs/RUNTIME_REQUALIFICATION_20261025_PENDING.md']
result={
 'schema':'independent-runtime-acceptance-decision-v2',
 'decision':'accepted_exact_limited_fresh_service_candidate',
 'runtime_qualification_accepted':True,
 'short_service_smoke_gate_unlock_accepted':True,
 'production_profile_edited_by_reviewer':False,
 'general_production_rollout_approved':False,
 'candidate_package_sha256':package,
 'runtime_identity':json.loads((B/'backend-cellstore-physical-evidence-20261103/RESULT.json').read_text())['identity'],
 'evidence':{name:{'sha256':h(B/name),'bytes':(B/name).stat().st_size} for name in evidence_paths},
 'basis':[
  'Previously blocking combined production-DDL/full-capacity/crash-recovery gap is closed by independently audited fresh evidence.',
  'Exact unchanged production DDL including indexes, 6144 pages and actual SQLITE_FULL; 2048 structurally valid Receipt sources, 55 positive-frame cells and 55 linked dependencies.',
  '6044 complete journal records independently checked; 177 changed pages reconstruct exact baseline; separate SQLite recovery restores bytes and independent typed six-table oracle.',
  'Production allocation accounting includes namespace directory plus max(logical,allocated) files: 49975296 bytes, slack4384; sampled finite conditional evidence, not universal hard bound.',
  'Nine previously independently reviewed real-verifier CellStore tests remain hash-pinned and unchanged; schema/restart/read/pin, ownership conflict and fail-closed refusals complement synthetic physical fixtures.',
  'The documented finite conditional process-crash acceptance basis is met; no additional production-schema physical or closure blocker identified for this exact limited scope.'
 ],
 'required_scope':[
  'A new explicit qualification evidence record must bind this decision, physical RESULT, independent audit and nine-test evidence. Do not reuse the earlier experimental generic-only basis as final acceptance.',
  'Change only package fingerprint and evidence identity for this exact runtime; all interpreter/sourceID/options/Linux unixVFS/device27/sector65536/header1/slack2MiB and existing database/journal/row/blob bounds remain unchanged.',
  'Only fresh isolated short bounded synthetic service startup/seal smoke is unlocked. This does not establish service success, native-model readiness, uploader results or broader deployment.',
  'Before launching the smoke, verify final profile diff and actual runtime/source pins; preserve fail-closed verifier, ownership and cleanup behavior. No migration or acceptance of old-store qualification bindings.',
  'No bypass flags, forced verifier success, cache regeneration, runtime substitutions or model changes are approved by this decision.'
 ],
 'limits':[
  'No power-loss, filesystem fault, hostile same-UID isolation or instantaneous allocation/RSS guarantee.',
  'Synthetic opaque physical proofs and synthetic authority/lock tokens do not establish recorder provenance or actual OS lock integration; the fresh service smoke must assess its own integration and cleanup.',
  'Historical STOP/KILL/wait events are consistent saved receipts, not independently observed retrospectively; raw saved-byte reconstruction and logical content were independently verified.',
  'Original interrupted pair was never opened with SQLite by reviewer; only already-recovered clean bytes were opened immutable/read-only.',
  'No service/model executed and no production/runtime/qualification edited during this review.'
 ],
 'decision_script_sha256':h(Path(__file__))}
(O/'DECISION.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
print(json.dumps({'decision':result['decision'],'report_sha256':h(O/'REPORT.json'),'decision_sha256':h(O/'DECISION.json'),'decision_script_sha256':h(Path(__file__))},sort_keys=True))
