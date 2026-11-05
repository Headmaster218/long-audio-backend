"""Bounded native-turn evidence and deterministic, non-oracle interval selection."""
import hashlib
import re
from . import recorder_intake as single
from . import transcript_contract as c
from .overlap_geometry import overlapping_regions
from .recorder_incremental_span_status import MAX_WORK_CHUNKS

require = single.require
SCHEMA = 'recorder-native-turn-export-v1'
HEX = re.compile(r'[a-f0-9]{64}\Z')

def digest(value):
    return hashlib.sha256(c.encoded(value)).hexdigest()

def validate_chunk(value, sid, cid, frames, start, source_sha256=None):
    require(type(value) is dict and type(frames) is int and 0<frames<=168000, 'native_turn_object')
    body = {k:v for k,v in value.items() if k != 'evidence_sha256'}
    require(value.get('schema') == 'bound-native-turn-evidence-v1' and
        value.get('session_id') == sid and value.get('chunk_id') == cid and
        value.get('frames') == frames and value.get('timeline_start_frame') == start and
        value.get('capture_start_frame') == start and value.get('evidence_sha256') == digest(body),
        'native_turn_binding')
    require(type(value.get('source_sha256')) is str and HEX.fullmatch(value['source_sha256']) and
        (source_sha256 is None or value['source_sha256'] == source_sha256), 'native_turn_source')
    cores=value.get('cores'); require(type(cores) is list and 1<=len(cores)<=4, 'native_turn_core_bound')
    cursor=0;total=0
    for core in cores:
        a,b=core.get('start_frame'),core.get('end_frame')
        require(type(a) is int and type(b) is int and a==cursor<b<=frames, 'native_turn_core_coverage')
        cursor=b
        require(core.get('status') in ('retained','existing','empty','unknown_only') and
            all(type(core.get(k)) is str and HEX.fullmatch(core[k]) for k in ('slot','proof_sha256')),
            'native_turn_proof')
        scope=core.get('scope');require(type(scope) is dict and scope.get('session_id')==sid and
            type(scope.get('epoch')) is str, 'native_turn_scope')
        turns=core.get('turns');require(type(turns) is list and len(turns)<=256,'native_turn_count')
        seen=set()
        for turn in turns:
            lo,hi=turn.get('start_frame'),turn.get('end_frame');label=turn.get('speaker_local_id')
            require(type(lo) is int and type(hi) is int and start+a<=lo<hi<=start+b and
                type(label) is str and label in {scope['epoch']+':speaker_'+str(i) for i in range(1,9)} and
                type(turn.get('ordinal')) is int and turn['ordinal']>=0 and turn['ordinal'] not in seen,
                'native_turn_geometry')
            seen.add(turn['ordinal'])
        total+=len(turns)
    require(cursor==frames and total<=256, 'native_turn_full_coverage')
    return value

def validate_work(work, frames, check=lambda:None):
    require(type(frames) is int and frames>0 and
        type(work) is list and 1<=len(work)<=MAX_WORK_CHUNKS,'native_turn_work_bound')
    cursor=0;seen=set()
    for row in work:
        check()
        require(type(row) is dict and type(row.get('chunk_id')) is str and
            re.fullmatch('[a-f0-9]{32}',row['chunk_id']) and row['chunk_id'] not in seen and
            type(row.get('span_start_frame')) is int and type(row.get('span_end_frame')) is int and
            row['span_start_frame']==cursor<row['span_end_frame']<=frames and
            row['span_end_frame']-cursor<=168000,
            'native_turn_work_coverage')
        cursor=row['span_end_frame'];seen.add(row['chunk_id'])
    require(cursor==frames,
            'native_turn_work_coverage')
    return seen

def select(document, source, work, source_hashes, check=lambda:None):
    check()
    require(type(document) is dict and document.get('schema')==SCHEMA and
        document.get('source_content_binding')==source and document.get('frames')==source['frames'],
        'native_turn_export_source')
    seen=validate_work(work,source['frames'],check)
    require(type(source_hashes) is dict and set(source_hashes)==seen,'native_turn_source_coverage')
    chunks=document.get('chunks'); require(type(chunks) is list and len(chunks)==len(work), 'native_turn_work_coverage')
    detected=[];supports=[]
    for item, expected in zip(chunks,work):
        check()
        start=expected['span_start_frame'];frames=expected['span_end_frame']-start
        validate_chunk(item,source['target']['session_id'],expected['chunk_id'],frames,start,
            source_hashes[expected['chunk_id']])
        for core in item['cores']:
            check()
            a,b=start+core['start_frame'],start+core['end_frame']
            turns=[dict(start=t['start_frame'],end=t['end_frame'],speaker_local_id=t['speaker_local_id'])
                for t in core['turns']]
            # Existing geometry algorithm, with integer-valued frame coordinates.
            for region in overlapping_regions(turns,b,capacity=8):
                lo,hi=int(region['start']),int(region['end'])
                detected.append(dict(start_frame=lo,end_frame=hi,chunk_id=item['chunk_id'],
                    core_slot=core['slot'],detected_source_count=region['detected_source_count']))
                supports.append((max(a,lo-16000),min(b,hi+16000)))
    # Context from adjacent detected regions may overlap. Merge only time supports,
    # never speaker labels or model output tracks; chronology is score-independent.
    merged=[]
    for a,b in sorted(supports):
        check()
        if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    all_ranges=[]
    for a,b in merged:
        check()
        while a<b:
            end=min(b,a+320000);all_ranges.append(dict(start_frame=a,end_frame=end));a=end
    chosen=all_ranges[:8]
    # Native detections can share time at adjacent scope edges, so count union.
    intervals=[]
    for d in sorted(detected,key=lambda x:(x['start_frame'],x['end_frame'])):
        check()
        a,b=d['start_frame'],d['end_frame']
        if intervals and a<=intervals[-1][1]:intervals[-1]=(intervals[-1][0],max(b,intervals[-1][1]))
        else:intervals.append((a,b))
    covered=sum(max(0,min(b,r['end_frame'])-max(a,r['start_frame'])) for a,b in intervals for r in chosen)
    return dict(schema='native-overlap-selection-v1',ranges=chosen,detected_regions=detected,
        evidence_sha256=digest(document),evidence_status='complete_validated_accepted_core_coverage',selection='chronological-native-intersections-1s-context-v1',
        detected_overlap_frames=sum(b-a for a,b in intervals),selected_detected_overlap_frames=covered,
        selected_support_frames=sum(r['end_frame']-r['start_frame'] for r in chosen),
        omitted_support_ranges=max(0,len(all_ranges)-len(chosen)),max_ranges=8,max_range_frames=320000,
        detection_accuracy_verified=False,reference_overlap_coverage=None,
        absent_detection_does_not_prove_no_overlap=True,global_speaker_identity=False)
