"""Independent fixed AMI B input; shared A geometry, not a cross-day holdout.

This is a separately pinned reader. Geometry predicates retain the same fixed geometry as A; no A input pin is accepted by this reader.
"""
import hashlib
import io
import os
import stat
import time
import wave

RATE = 16000
START = 840*RATE
FRAMES = 720*RATE
ORIGIN = 100003
END = ORIGIN+FRAMES
BYTES = 57179180
TOTAL_FRAMES = 28589568
SHA256 = '50e2c8e4737850c4bf3cc24e7087c755e1c733cd91822106afd0df485ba64893'
CHUNK = 168000
PAGE_FRAMES = (4704000, 4704000, 2112000)
PIECE_FRAMES = ((1920000, 1920000, 864000), (1920000, 1920000, 864000), (1920000, 192000))


def plan():
    pages, offset, position, source_index = [], 0, 0, 0
    for number, (frames, pieces) in enumerate(zip(PAGE_FRAMES, PIECE_FRAMES), 1):
        sources, local = [], 0
        for size in pieces:
            sources.append(dict(index=source_index, offset=offset+local, frames=size))
            local += size; source_index += 1
        chunks = []
        for local_start in range(0, frames, CHUNK):
            chunks.append(dict(position=position, offset=offset+local_start,
                               frames=min(CHUNK, frames-local_start)))
            position += 1
        if local != frames: raise AssertionError('page frame conservation')
        pages.append(dict(number=number, offset=offset, frames=frames, sources=sources, chunks=chunks))
        offset += frames
    if offset != FRAMES or source_index != 8 or position != 69:
        raise AssertionError('fixed B geometry differs')
    return pages


class BInput:
    """One verified regular FD; bounded piece reads, no whole waveform in RAM."""
    def __init__(self, path, deadline): self.path, self.deadline = path, deadline
    def __enter__(self):
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        self.stream = os.fdopen(fd, 'rb')
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != BYTES:
                raise ValueError('B original type/size differs')
            self.identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            digest = hashlib.sha256()
            while True:
                if time.monotonic() >= self.deadline: raise TimeoutError('B input hash deadline')
                block = self.stream.read(1024*1024)
                if not block: break
                digest.update(block)
            if digest.hexdigest() != SHA256: raise ValueError('B original hash differs')
            self.stream.seek(0)
            self.wav = wave.open(self.stream, 'rb')
            if (self.wav.getnchannels(), self.wav.getsampwidth(), self.wav.getframerate(),
                    self.wav.getnframes(), self.wav.getcomptype()) != (1, 2, RATE, TOTAL_FRAMES, 'NONE'):
                raise ValueError('B original WAV geometry differs')
            return self
        except BaseException as original:
            self._close(original)
            raise
    def piece(self, source):
        if source not in [item for page in plan() for item in page['sources']]:
            raise ValueError('unplanned B source')
        if time.monotonic() >= self.deadline: raise TimeoutError('B piece deadline')
        before = os.fstat(self.stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != self.identity:
            raise ValueError('B input changed after verification')
        self.wav.setpos(START+source['offset'])
        pcm = self.wav.readframes(source['frames'])
        if len(pcm) != source['frames']*2: raise ValueError('B source short read')
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(RATE); wav.writeframes(pcm)
        data = output.getvalue()
        after = os.fstat(self.stream.fileno())
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != self.identity:
            raise ValueError('B input changed during piece read')
        if len(data) != 44+source['frames']*2 or len(data) > 4000000:
            raise ValueError('B bounded WAV envelope differs')
        return data
    def _close(self, original=None):
        errors = []
        for name in ('wav', 'stream'):
            handle = getattr(self, name, None)
            if handle is not None:
                try: handle.close()
                except BaseException as exc: errors.append(exc)
        if errors:
            if original is not None:
                original.add_note('B input cleanup failures: '+repr([type(e).__name__ for e in errors]))
            else:
                raise errors[0]
    def __exit__(self, exc_type, exc, traceback):
        self._close(exc)


def validate_page(value, expected, receipts, *, require_completed=False):
    if value.get('state') in ('interrupted', 'cancelled', 'failed'):
        raise RuntimeError('B page terminal failure')
    work = value.get('work_chunks', [])
    if any(row.get('status') in ('failed', 'cancelled') for row in work):
        raise RuntimeError('B chunk terminal failure')
    if value.get('state') != 'published' or value.get('released') is not True:
        return False
    if (value.get('page_number'), value.get('start_frame'), value.get('end_frame'),
            value.get('eos'), value.get('capture_origin_frame')) != (
            expected['number'], ORIGIN+expected['offset'], ORIGIN+expected['offset']+expected['frames'],
            expected['number'] == 3, ORIGIN):
        raise ValueError('B page geometry differs')
    sources = value.get('sources', [])
    if len(sources) != len(expected['sources']) or len(work) != len(expected['chunks']):
        raise ValueError('B page source/chunk count differs')
    for source, planned in zip(sources, expected['sources']):
        receipt = receipts[planned['index']]
        if (source.get('frames'), source.get('bytes'), source.get('digest'), source.get('source_start_frame')) != (
                planned['frames'], receipt['bytes'], receipt['sha256'], ORIGIN+planned['offset']):
            raise ValueError('B accepted source differs')
    for row, planned in zip(work, expected['chunks']):
        if (row.get('position'), row.get('frames'), row.get('capture_start_frame'),
                row.get('timeline_start'), row.get('duration')) != (
                planned['position'], planned['frames'], ORIGIN+planned['offset'],
                planned['offset']/RATE, planned['frames']/RATE):
            raise ValueError('B chunk offsets differ')
    ranges = []
    for source, sp in zip(sources, expected['sources']):
        for row, cp in zip(work, expected['chunks']):
            start = max(sp['offset'], cp['offset'])
            end = min(sp['offset']+sp['frames'], cp['offset']+cp['frames'])
            if end > start:
                ranges.append((source['id'], row['chunk_id'], start-sp['offset'],
                               ORIGIN+start, start-cp['offset'], end-start, 'owner'))
    actual = [(r.get('source_id'), r.get('chunk_id'), r.get('source_start_frame'),
               r.get('capture_start_frame'), r.get('work_start_frame'), r.get('frames'), r.get('relation'))
              for r in value.get('ranges', [])]
    if sorted(actual) != sorted(ranges) or sum(r[5] for r in actual) != expected['frames']:
        raise ValueError('B exact frame conservation differs')
    return not require_completed or all(row.get('status') == 'completed' for row in work)


def validate_final(pages, chunks, capture, receipts):
    if len(pages) != 3: raise ValueError('B page count differs')
    done = all([validate_page(value, expected, receipts, require_completed=True)
                for value, expected in zip(pages, plan())])
    rows = chunks.get('chunks', [])
    if any(row.get('status') in ('failed', 'cancelled') for row in rows):
        raise RuntimeError('B final chunk failure')
    if len(rows) != 69: raise ValueError('B final chunk count differs')
    work = [row for page in pages for row in page.get('work_chunks', [])]
    if len(work) != 69: return False
    for row, published, expected in zip(rows, work, [c for p in plan() for c in p['chunks']]):
        if row.get('id') != published.get('chunk_id'):
            raise ValueError('B chunk listing identity differs')
        if row.get('status') != 'completed': done = False
        elif (row.get('duration'), row.get('timeline_start'), row.get('next_offset')) != (
                expected['frames']/RATE, expected['offset']/RATE, expected['frames']/RATE):
            raise ValueError('B final processed offsets differ')
    if (capture.get('eos') is not True or capture.get('page_count') != 3 or capture.get('released_pages') != 3
            or capture.get('pending_page') is not None or capture.get('capture_origin_frame') != ORIGIN
            or capture.get('capture_end_frame') != END):
        return False
    return done
