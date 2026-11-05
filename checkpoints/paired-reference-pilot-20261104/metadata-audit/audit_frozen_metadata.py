"""Independent metadata audit. Never reads WAV bodies, models, scores, or network.

The original selector is not imported or executed. All original inputs remain
unchanged. Word-to-segment linkage is resolved from original NITE child hrefs.
"""
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import re
import struct
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FEAS = ROOT / 'fresh-ami-feasibility-20261104'
MANIFEST = FEAS / 'prospective_annotation_candidates.json'
ZIP = ROOT / 'backend-carryover-assets-20261104/ami_public_manual_1.6.2.zip'
NS = '{http://nite.sourceforge.net/}'
EXPECTED_SHA = 'fc297e67fbee68481809c600068ffc159df0742c395f00a1b51f767a13a16e52'
RATE, PAD, WIDTH = 16000, 1600, 32000
MEETINGS = ['ES2014a', 'ES2014d', 'ES2015a', 'ES2015d']
errors, warnings, checks = [], [], []


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, label, detail=None):
    if not ok:
        errors.append({'check': label, 'detail': detail})
    return bool(ok)


def frame(s):
    exact = Decimal(s) * RATE
    require(exact == exact.to_integral_value(), 'annotation_time_exact_on_16k_grid', s)
    return int(exact.to_integral_value())


def merge(intervals):
    result = []
    for left, right in sorted(intervals):
        if result and left <= result[-1][1]:
            result[-1] = (result[-1][0], max(right, result[-1][1]))
        else:
            result.append((left, right))
    return result


def overlaps(a, b, c, d):
    return a < d and c < b


def gaps_in(left, right, forbidden):
    cursor = left
    for a, b in merge(forbidden):
        if b <= cursor:
            continue
        if a >= right:
            break
        if cursor < a:
            yield cursor, min(a, right)
        cursor = max(cursor, b)
        if cursor >= right:
            return
    if cursor < right:
        yield cursor, right


manifest_bytes = MANIFEST.read_bytes()
manifest = json.loads(manifest_bytes)
require(sha(manifest_bytes) == EXPECTED_SHA, 'frozen_manifest_sha256')
input_paths = [MANIFEST, ZIP, FEAS/'meetings.xml', FEAS/'official_meetings_metadata.json',
               FEAS/'official_wav_range_headers.json', FEAS/'build_annotation_candidates.py',
               ROOT/'backend-paired-reference-pilot-20261104/ACQUISITION_RELEASE.json']
inputs = {str(p.relative_to(ROOT)): {'sha256': sha(p.read_bytes()), 'bytes': p.stat().st_size}
          for p in input_paths}
archive_hashes, data = {}, {}
with zipfile.ZipFile(ZIP) as archive:
    official_xml = archive.read('corpusResources/meetings.xml')
    archive_hashes['corpusResources/meetings.xml'] = sha(official_xml)
    require(official_xml == (FEAS/'meetings.xml').read_bytes(), 'retained_meetings_matches_original_zip')
    require(sha(official_xml) == manifest['meetings_sha256'], 'manifest_meetings_hash')
    meetings = {e.attrib['observation']: e for e in ET.fromstring(official_xml)}
    for meeting in MEETINGS:
        data[meeting] = {}
        agents = {e.attrib['nxt_agent']: e.attrib for e in meetings[meeting]}
        require(set(agents) == set('ABCD'), 'four_original_agents', meeting)
        for agent, speaker in agents.items():
            word_path = f'words/{meeting}.{agent}.words.xml'
            seg_path = f'segments/{meeting}.{agent}.segments.xml'
            word_bytes, seg_bytes = archive.read(word_path), archive.read(seg_path)
            for name, raw in [(word_path, word_bytes), (seg_path, seg_bytes)]:
                archive_hashes[name] = sha(raw)
                require(sha(raw) == manifest['annotation_sha256'][name], 'original_annotation_hash', name)
            words = []
            for e in ET.fromstring(word_bytes):
                a = e.attrib
                require('starttime' in a and 'endtime' in a, 'all_word_layer_events_timed', a.get(NS+'id'))
                words.append({'id': a[NS+'id'], 'start': frame(a['starttime']), 'end': frame(a['endtime']),
                              'lexical': e.tag == 'w' and a.get('punc') != 'true', 'tag': e.tag,
                              'text': e.text or ''})
            by_id = {w['id']: i for i,w in enumerate(words)}
            require(len(by_id) == len(words), 'unique_word_ids', word_path)
            segments = []
            for e in ET.fromstring(seg_bytes):
                a = e.attrib
                member_ids = []
                for child in e:
                    href = child.attrib['href']
                    filename, anchor = href.split('#', 1)
                    require(filename == word_path.split('/')[-1], 'segment_child_correct_file', href)
                    ids = re.findall(r'id\(([^)]+)\)', anchor)
                    require(len(ids) in [1, 2], 'segment_child_href_format', href)
                    first, last = by_id[ids[0]], by_id[ids[-1]]
                    require(first <= last, 'segment_child_order', href)
                    member_ids.extend(w['id'] for w in words[first:last+1])
                segments.append({'id': a[NS+'id'], 'start': frame(a['transcriber_start']),
                                 'end': frame(a['transcriber_end']), 'channel': a['channel'],
                                 'member_ids': member_ids})
            require(len({s['id'] for s in segments}) == len(segments), 'unique_segment_ids', seg_path)
            data[meeting][agent] = {'speaker': speaker, 'words': words, 'segments': segments,
                                   'chains': merge((w['start'], w['end']) for w in words
                                                   if w['lexical'] and w['end'] > w['start'])}

rosters = {}
meeting_evidence = []
for split, series in [('C','ES2014'), ('T','ES2015')]:
    first, last = meetings[series+'a'], meetings[series+'d']
    ids_a = sorted(s.attrib['global_name'] for s in first)
    ids_d = sorted(s.attrib['global_name'] for s in last)
    require(ids_a == ids_d, 'same_global_persons_across_a_d', series)
    require(first.attrib['dateOnly'] == last.attrib['dateOnly'], 'same_day_a_d', series)
    rosters[split] = ids_a
    for m in [first, last]:
        require(m.attrib['type'] == 'scenario', 'official_scenario_type', m.attrib['observation'])
        meeting_evidence.append({'meeting': m.attrib['observation'], **{k:m.attrib.get(k) for k in
            ['dateOnly','startTime','duration','visibility','seen_type']},
            'global_ids': sorted(s.attrib['global_name'] for s in m)})
    for i, person in enumerate(ids_a):
        expected = {'split':split, 'person_id':person, 'role':'known' if i < 2 else 'unknown',
                    'reference_meeting':series+'a' if i < 2 else None,
                    'query_meeting':series+'d','date':first.attrib['dateOnly']}
        require(expected in manifest['roster'], 'deterministic_global_id_roster', expected)
require(not set(rosters['C']) & set(rosters['T']), 'C_T_global_ids_disjoint')
require(len(manifest['roster']) == 8, 'eight_roster_people')

headers = json.loads((FEAS/'official_wav_range_headers.json').read_text())
source_evidence = {}
require(len(headers) == 4, 'four_sources')
for h in headers:
    raw = bytes.fromhex(h['header_hex'])
    require(len(raw) == 44 and h['body_bytes'] == 44, '44_byte_header', h['url'])
    require(sha(raw) == h['header_sha256'], 'source_header_hash', h['url'])
    fields = struct.unpack('<4sI4s4sIHHIIHH4sI', raw)
    total = int(h['headers']['content-range'].split('/')[-1])
    require((fields[0],fields[2],fields[3],fields[4],fields[5],fields[6],fields[7],fields[8],fields[9],fields[10],fields[11]) ==
            (b'RIFF',b'WAVE',b'fmt ',16,1,1,16000,32000,2,16,b'data'), 'PCM_mono_16k_16bit_data_offset44', h['url'])
    require(fields[1]+8 == fields[12]+44 == total, 'RIFF_data_total_geometry', h['url'])
    require(h['status'] == 206 and h['requested_range'] == 'bytes=0-43' and
            h['headers']['content-range'] == f'bytes 0-43/{total}', 'recorded_header_range_response', h['url'])
    source_evidence[h['url']] = {'total_bytes':total, 'header_sha256':sha(raw),
        'etag':h['headers']['etag'], 'last_modified':h['headers']['last-modified'],
        'pcm_frames':fields[12]//2, 'format_tag':1,'channels':1,'sample_rate':16000,
        'bits_per_sample':16,'block_align':2,'data_offset':44}

# Reconstruct all qualifying starts with an independent union/complement sweep.
# Unlike the builder, do not iteratively subtract intervals or reuse its helpers.
replay = {}
for m, agents in data.items():
    for agent, own in agents.items():
        forbidden = [(s['start']-PAD,s['end']+PAD) for other, d in agents.items()
                     if other != agent for s in d['segments']]
        forbidden += [(w['start']-PAD,w['end']+PAD) for w in own['words']
                      if not w['lexical'] and w['end'] > w['start']]
        candidates = []
        for s in own['segments']:
            possibilities = []
            for left,right in own['chains']:
                left, right = max(left+PAD,s['start']+PAD), min(right-PAD,s['end']-PAD)
                for lo,hi in gaps_in(left,right,forbidden):
                    if hi-lo >= WIDTH:
                        possibilities.append((lo,hi))
            if possibilities:
                lo,hi = min(possibilities)
                candidates.append((lo,s['id'],hi))
        selected = []
        for candidate in sorted(candidates):
            if not selected or candidate[0]-selected[-1][0] >= 30*RATE:
                selected.append(candidate)
        replay[(m,agent)] = selected

rows = manifest['rows']
require(len(rows) == 36, '36_frozen_coordinates')
require(Counter(r['crop_role'] for r in rows) == {'reference':12,'query':24}, '12_reference_24_query')
require(len({(r['meeting'],r['start_frame'],r['end_frame']) for r in rows}) == 36, '36_unique_source_coordinates')
row_evidence, groups = [], defaultdict(list)
for row in rows:
    m, agent = row['meeting'], row['agent']
    own = data[m][agent]
    a,b = row['start_frame'],row['end_frame']
    label = f"{row['split']}/{row['person_id']}/{row['crop_role']}/{row['slot']}"
    errs_before = len(errors)
    require(own['speaker']['global_name'] == row['person_id'], 'row_global_id', label)
    require(own['speaker']['channel'] == row['channel'], 'row_channel_id', label)
    s = next(s for s in own['segments'] if s['id'] == row['segment_id'])
    require(s['channel'] == row['channel'], 'segment_channel', label)
    require(s['start'] == row['segment_start_frame'] and s['end'] == row['segment_end_frame'], 'original_segment_times', label)
    require(s['start']+PAD <= a < b <= s['end']-PAD and b-a == WIDTH, '2s_inside_segment_100ms_erosion', label)
    require(Decimal(str(row['start_seconds']))*RATE == a and Decimal(str(row['end_seconds']))*RATE == b, 'coordinate_second_frame_exact', label)
    supporting_chains = [(l,r) for l,r in own['chains'] if l+PAD <= a and b <= r-PAD]
    require(len(supporting_chains) == 1, 'continuous_lexical_union_100ms_erosion', label)
    covered_words = [w for w in own['words'] if w['lexical'] and overlaps(a,b,w['start'],w['end'])]
    require(all(w['id'] in s['member_ids'] for w in covered_words), 'all_support_words_are_segment_children', label)
    child_chains = merge((w['start'],w['end']) for w in own['words'] if w['lexical'] and w['id'] in s['member_ids'] and w['end'] > w['start'])
    require(any(l+PAD <= a and b <= r-PAD for l,r in child_chains), 'segment_linked_words_continuous_100ms_support', label)
    other_hits = [s2['id'] for a2,d in data[m].items() if a2 != agent for s2 in d['segments']
                  if overlaps(a,b,s2['start']-PAD,s2['end']+PAD)]
    require(not other_hits, 'all_other_speaker_segments_excluded_100ms', {'row':label,'hits':other_hits})
    bad_hits = [w['id'] for w in own['words'] if not w['lexical'] and w['end'] > w['start'] and overlaps(a,b,w['start']-PAD,w['end']+PAD)]
    require(not bad_hits, 'own_positive_nonlexical_events_excluded_100ms', {'row':label,'hits':bad_hits})
    # Extra defense: detect any other-speaker timed event outside its segment bounds.
    other_event_hits = [w['id'] for a2,d in data[m].items() if a2 != agent for w in d['words']
                        if w['end'] > w['start'] and overlaps(a,b,w['start']-PAD,w['end']+PAD)]
    require(not other_event_hits, 'other_speaker_positive_events_excluded_100ms', {'row':label,'hits':other_event_hits})
    ignored_zero = [w['id'] for w in own['words'] if not w['lexical'] and w['tag'] != 'w' and w['end'] == w['start'] and a <= w['start'] < b]
    url = f'https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/{m}/audio/{m}.Mix-Headset.wav'
    require(row['official_wav_url'] == url, 'official_mix_headset_source', label)
    h = source_evidence[url]
    require(row['source_file_size_bytes'] == h['total_bytes'] and row['source_etag'] == h['etag'] and row['source_last_modified'] == h['last_modified'], 'row_matches_header_source_binding', label)
    require(row['verified_audio_format'] == {k:h[k] for k in ['format_tag','channels','sample_rate','bits_per_sample','block_align','data_offset']}, 'row_header_geometry', label)
    require(row['prospective_http_byte_range'] == f'bytes={44+a*2}-{44+b*2-1}' and b <= h['pcm_frames'], 'row_source_byte_range', label)
    expected = replay[(m,agent)][row['slot']-1]
    require((a,row['segment_id'],row['earliest_qualifying_interval_end_frame']) == expected, 'independent_earliest_greedy_replay', label)
    groups[(row['split'],row['person_id'],row['crop_role'])].append(row)
    row_evidence.append({'row':label, 'meeting':m, 'agent':agent, 'segment_id':s['id'], 'start_frame':a,
        'end_frame':b, 'start_seconds':a/RATE,'end_seconds':b/RATE, 'checks_passed':len(errors)==errs_before,
        'lexical_chain_frames':supporting_chains, 'support_word_ids':[w['id'] for w in covered_words],
        'support_text':' '.join(w['text'] for w in covered_words), 'own_zero_duration_nonlexical_inside':ignored_zero,
        'minimum_segment_boundary_clearance_seconds':min(a-s['start'],s['end']-b)/RATE,
        'other_speaker_segment_overlap_count':len(other_hits), 'own_positive_nonlexical_overlap_count':len(bad_hits),
        'other_speaker_positive_event_overlap_count':len(other_event_hits)})

group_evidence = []
for (split,person,role), group in sorted(groups.items()):
    group.sort(key=lambda r:r['slot'])
    require([r['slot'] for r in group] == [1,2,3], 'primary_and_two_extra_slots', [split,person,role])
    require(len({r['segment_id'] for r in group}) == 3, 'three_distinct_original_segments', [split,person,role])
    spacings = [(group[i+1]['start_frame']-group[i]['start_frame'])/RATE for i in range(2)]
    require(all(x >= 30 for x in spacings), 'at_least_30_second_start_separation', [split,person,role,spacings])
    require(all(r['meeting'].endswith('a' if role == 'reference' else 'd') for r in group), 'a_reference_d_query', [split,person,role])
    group_evidence.append({'split':split,'person_id':person,'crop_role':role,'slots':[1,2,3], 'start_spacings_seconds':spacings})
for roster in manifest['roster']:
    r = [x for x in rows if x['person_id'] == roster['person_id']]
    expected = {'reference':3,'query':3} if roster['role']=='known' else {'query':3}
    require(Counter(x['crop_role'] for x in r) == expected, 'per_person_role_crop_counts', roster['person_id'])

warnings = [
    'All claims concern annotation time support, not physical acoustic purity, listening certification, or speech quality.',
    'Lexical means positive-duration <w> without punc=true. It includes filled pauses and can have imperfect forced-alignment timing; words may be truncated at crop boundaries.',
    'Zero-duration nonlexical annotations are not positive-duration events and are not excluded by the frozen rule. Per-row IDs are reported.',
    'The official metadata has empty startTime for all four meetings. Earlier/later refers to scenario a/d ordering, not independently established wall-clock timestamps.',
    'C and T have disjoint official global IDs within this experiment. Historical exposure remains unknown because old AMI20/full-holdout inventories are missing; parent accepted this limitation.',
    'ES2015a/d are marked seen/training in official AMI metadata. This audit does not establish freedom from model pretraining or earlier experiment exposure.',
    'The retained HTTP 44-byte responses prove recorded header geometry only, not current server state or full-source audio hashes. No WAV body was read by this audit.',
    'The manifest internal status says prospective because the original is preserved. ACQUISITION_RELEASE.json separately freezes its exact SHA256.',
    'This is English same-day separate-scenario-meeting evidence. It is not Chinese, cross-day, long-duration, or final acceptance evidence.'
]
require(sha(MANIFEST.read_bytes()) == EXPECTED_SHA, 'frozen_manifest_unchanged_after_audit')
report = {'status':'PASS_WITH_EXPLICIT_LIMITATIONS' if not errors else 'FAIL',
    'manifest_sha256':EXPECTED_SHA, 'inputs':inputs, 'original_archive_member_hashes':archive_hashes,
    'method':'Independent XML parsing, exact Decimal-to-frame verification, segment child-link resolution, union/complement interval sweep, and static selector review; no original selector execution or import.',
    'scope':{'audio_read':False,'model_read':False,'score_read':False,'network_download':False,'git_used':False,'frozen_inputs_modified':False},
    'counts':{'people':len(manifest['roster']), 'reference_crops':sum(r['crop_role']=='reference' for r in rows),
              'query_crops':sum(r['crop_role']=='query' for r in rows),'all_crops':len(rows),'duration_seconds':72,
              'original_annotation_files':32,'source_header_bytes':176},
    'global_ids':rosters, 'meetings':meeting_evidence,'sources':source_evidence,
    'selection_code_review':{'selector_sha256':inputs[str((FEAS/'build_annotation_candidates.py').relative_to(ROOT))]['sha256'],
        'reads_only_metadata_annotations_headers':True,'no_waveform_or_model_score_selection':True,
        'earliest_per_segment_then_greedy_30s_starts_reproduced':not any(e['check']=='independent_earliest_greedy_replay' for e in errors),
        'determinism_scope':'Exact frozen four meetings and sorted official global IDs; does not claim global earliest choice among the entire AMI corpus.'},
    'row_evidence':row_evidence,'group_evidence':group_evidence,'limitations':warnings,'errors':errors}
(OUT/'metadata_audit.json').write_text(json.dumps(report,indent=2)+'\n')
lines = ['# Independent frozen AMI metadata audit', '', f"Status: **{report['status']}**", '',
    f'Frozen manifest SHA-256: `{EXPECTED_SHA}`', '',
    '## Verified', '',
    '- 36 unique two-second coordinates: 12 references for four known people; 24 queries for all eight people.',
    '- Each reference/query group has slots 1, 2, 3 from distinct original segments, with starts at least 30 seconds apart.',
    '- References use scenario a; queries use scenario d on the same date. C/T official global IDs are disjoint.',
    '- Every crop is inside its original segment with 100 ms erosion; its support words are linked children of that segment and continuously cover the crop with 100 ms chain erosion.',
    '- No crop overlaps any other speaker segment expanded by 100 ms or any own positive-duration nonlexical event expanded by 100 ms. Extra check: no other-speaker positive-duration word-layer event overlaps after the same expansion.',
    '- Independent interval union/complement reconstruction reproduced every earliest coordinate and qualifying interval endpoint under the frozen greedy selection rule.',
    '- All 32 original annotation member hashes match. Retained meetings.xml is byte-identical to the copy in the official annotation archive.',
    '- Four retained official Mix-Headset header records decode as PCM mono, 16 kHz, 16 bit, with data at byte 44. Every crop byte range and file-size bound matches.',
    '- Selector code only uses meeting metadata, manual annotations, and retained header metadata. It does not read audio, model embeddings, or scores.', '',
    '## Exact claim limits', ''] + ['- '+w for w in warnings] + ['', '## Reproduction', '',
    'Run `python backend-paired-reference-pilot-20261104/metadata-audit/audit_frozen_metadata.py` from the workspace. It only reads metadata inputs and writes this audit directory.', '',
    '`metadata_audit.json` includes original input/member hashes, per-row word IDs and support text, per-group start spacings, and explicit failures (if any).', '',
    f'Failures: {len(errors)}', '']
if errors:
    lines += [json.dumps(errors,indent=2), '']
(OUT/'METADATA_AUDIT.md').write_text('\n'.join(lines))
print(json.dumps({'status':report['status'],'errors':errors,'zero_duration_nonlexical_rows':[
    {'row':r['row'],'ids':r['own_zero_duration_nonlexical_inside']} for r in row_evidence if r['own_zero_duration_nonlexical_inside']],
    'output':str(OUT)},indent=2))
