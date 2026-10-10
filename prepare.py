"""Recompute the frozen cohort from checksum-verified official far-field assets.
No downloads. No inference. Generated references/crops remain outside Git.
"""
import argparse, array, hashlib, json, math, re, sys, wave
from pathlib import Path
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from fractions import Fraction
from collections import Counter
import unicodedata
from common import HERE, read, verify, external_output

def norm(s):
    return ''.join((c for c in unicodedata.normalize('NFKC', s).lower() if not unicodedata.category(c).startswith(('P', 'S'))))

def uq(s):
    return s[1:-1].replace('""', '"')
NUM = '[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?'
Q = '"(?:[^"]|"")*"'
INTERVAL = re.compile('intervals\\s*\\[(\\d+)\\]:\\s*xmin\\s*=\\s*(' + NUM + ')\\s*xmax\\s*=\\s*(' + NUM + ')\\s*text\\s*=\\s*(' + Q + ')', re.S)

def parse(p, session):
    if p.stat().st_size >= 8 * 1024 ** 2:
        raise ValueError('TextGrid byte bound exceeded')
    with p.open('rb') as f:
        data = f.read(8 * 1024 ** 2)
    if not len(data) < 8 * 1024 ** 2:
        raise ValueError('Malformed TextGrid or PCM input')
    text = data.decode('utf-16') if data.startswith((b'\xff\xfe', b'\xfe\xff')) else data.decode('utf-8-sig')
    if not ('Object class = "TextGrid"' in text and 'File type = "ooTextFile"' in text):
        raise ValueError('Malformed TextGrid or PCM input')
    blocks = re.split('\\bitem\\s*\\[(\\d+)\\]:', text)
    header = blocks[0]
    declared = int(re.search('\\bsize\\s*=\\s*(\\d+)', header)[1])
    if not len(blocks) == 1 + 2 * declared:
        raise ValueError('Malformed TextGrid or PCM input')
    rows = []
    tiers = []
    for k in range(declared):
        number, block = blocks[1 + 2 * k:3 + 2 * k]
        if not int(number) == k + 1:
            raise ValueError('Malformed TextGrid or PCM input')
        if not re.search('class\\s*=\\s*"IntervalTier"', block):
            raise ValueError('Malformed TextGrid or PCM input')
        speaker = uq(re.search('\\bname\\s*=\\s*(' + Q + ')', block, re.S)[1])
        if not speaker not in tiers:
            raise ValueError('Malformed TextGrid or PCM input')
        tiers.append(speaker)
        count = int(re.search('intervals:\\s*size\\s*=\\s*(\\d+)', block)[1])
        matches = list(INTERVAL.finditer(block))
        if not count == len(matches):
            raise ValueError('Malformed TextGrid or PCM input')
        if not len(re.findall('intervals\\s*\\[\\d+\\]', block)) == count:
            raise ValueError('Malformed TextGrid or PCM input')
        prior = None
        for i, m in enumerate(matches):
            idx, a, b, quoted = m.groups()
            if not int(idx) == i + 1:
                raise ValueError('Malformed TextGrid or PCM input')
            lo, hi = (Decimal(a), Decimal(b))
            if not (lo.is_finite() and hi.is_finite() and (0 <= lo < hi)):
                raise ValueError('Malformed TextGrid or PCM input')
            if not (prior is None or lo >= prior):
                raise ValueError(('same tier overlapping/out of order', p, speaker, a))
            prior = hi
            text_value = uq(quoted)
            id_input = [session, speaker, a, b]
            uid = hashlib.sha256(json.dumps(id_input, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            rows.append({'id': uid, 'session': session, 'speaker': speaker, 'tier_index': k + 1, 'interval_index': i + 1, 'native_start': a, 'native_end': b, 'native_text': text_value, 'reference': text_value.strip(), 'annotation_path': str(p.name)})
    return (rows, tiers)

def channel_blocks(w, a, b):
    w.setpos(a)
    remaining = b - a
    while remaining:
        n = min(4096, remaining)
        raw = w.readframes(n)
        if not len(raw) == n * 16:
            raise ValueError('Malformed TextGrid or PCM input')
        samples = array.array('h')
        samples.frombytes(raw)
        mono = samples[::8]
        if not len(mono) == n:
            raise ValueError('Malformed TextGrid or PCM input')
        yield mono
        remaining -= n

def select(rows, wav):
    """Exact rational RMS quartiles and greedy speaker balance, quiet first."""
    nonempty = [r for r in rows if r['reference']]
    eligible = []
    for row in rows:
        a, b = (Decimal(row['native_start']), Decimal(row['native_end']))
        lo = int((a * 16000).to_integral_value(rounding=ROUND_FLOOR))
        hi = int((b * 16000).to_integral_value(rounding=ROUND_CEILING))
        if not ''.join(norm(row['reference']).split()) or not Decimal(2) <= b - a <= Decimal('19.6'):
            continue
        if not 0 <= lo < hi <= wav.getnframes() or hi - lo > 320000:
            continue
        if any((other['id'] != row['id'] and Decimal(lo) / 16000 < Decimal(other['native_end']) and (Decimal(other['native_start']) < Decimal(hi) / 16000) for other in nonempty)):
            continue
        sumsq = sum((x * x for block in channel_blocks(wav, lo, hi) for x in block))
        row.update(start_frame=lo, end_frame=hi, frames=hi - lo, rms_sum_squares=sumsq, rms_frame_count=hi - lo)
        eligible.append(row)
    eligible.sort(key=lambda r: (Fraction(r['rms_sum_squares'], r['frames']), r['id']))
    q1, q3 = (len(eligible) // 4, 3 * len(eligible) // 4)
    for rank, row in enumerate(eligible):
        row.update(rms_rank_zero_based=rank, stratum='quiet' if rank < q1 else 'ordinary' if rank < q3 else 'upper_quartile')
    counts, strata_counts, chosen = (Counter(), Counter(), [])
    for stratum in ('quiet', 'ordinary'):
        candidates = [r for r in eligible if r['stratum'] == stratum]
        for _ in range(min(4, len(candidates))):
            r = min(candidates, key=lambda r: (counts[r['speaker']], strata_counts[stratum, r['speaker']], r['id']))
            candidates.remove(r)
            r['selection_index'] = len(chosen)
            counts[r['speaker']] += 1
            strata_counts[stratum, r['speaker']] += 1
            chosen.append(r)
    return chosen

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--native', type=Path, required=True, help='Directory containing Eval_Ali/Eval_Ali_far')
    ap.add_argument('--output', type=Path, required=True, help='New directory outside Git')
    args = ap.parse_args()
    if sys.byteorder != 'little':
        raise ValueError('This PCM reader requires a little-endian host')
    out = external_output(args.output)
    if out.exists():
        raise ValueError('Output exists; never silently replace a prior freeze')
    for entry in read(HERE / 'assets.json')['native_files']:
        verify(args.native / entry['path'], entry)
    native = args.native / 'Eval_Ali/Eval_Ali_far'
    expected = read(HERE / 'selection.json')
    chosen = []
    pinned_wavs = [args.native / e['path'] for e in read(HERE / 'assets.json')['native_files'] if e['path'].endswith('.wav')]
    for wp in sorted(pinned_wavs):
        grid = native / 'textgrid_dir' / ('_'.join(wp.stem.split('_')[:2]) + '.TextGrid')
        rows, _ = parse(grid, wp.stem)
        with wave.open(str(wp)) as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()) != (8, 2, 16000, 'NONE'):
                raise ValueError('Native format differs')
            chosen.extend(select(rows, w))
    if [r['id'] for r in chosen] != [r['id'] for r in expected]:
        raise ValueError('Recomputed cohort differs; do not silently widen eligibility')
    out.mkdir(parents=True)
    for row, pin in zip(chosen, expected):
        wp = native / 'audio_dir' / (row['session'] + '.wav')
        crop = out / (row['id'] + '.wav')
        h = hashlib.sha256()
        with wave.open(str(wp)) as w, wave.open(str(crop), 'wb') as dest:
            dest.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            for block in channel_blocks(w, row['start_frame'], row['end_frame']):
                pcm = block.tobytes()
                h.update(pcm)
                dest.writeframesraw(pcm)
        normalized = ''.join(norm(row['reference']).split())
        row.update(pcm16_sha256=h.hexdigest(), reference_utf8_sha256=hashlib.sha256(row['reference'].encode()).hexdigest(), normalized_reference_sha256=hashlib.sha256(normalized.encode()).hexdigest(), normalized_reference_characters=len(normalized))
        if {k: row[k] for k in pin} != pin:
            raise ValueError('Selection/PCM/reference identity differs')
    (out / 'references.json').write_text(json.dumps([{**pin, 'reference': r['reference']} for r, pin in zip(chosen, expected)], ensure_ascii=False) + '\n')
    print('Verified 56 selections, 5041440 frames, 1578 reference characters. No inference.')
if __name__ == '__main__':
    main()
