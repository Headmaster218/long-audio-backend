"""Exact official short input. No resampling, labels or reference selection."""
import hashlib
import io
import os
import stat
import wave

FRAMES = 114432
RATE = 16000
ORIGIN = 100003
END = ORIGIN+FRAMES
SHA256 = 'eb1eb008904465b74c304aad8342e8c7d3c6e61ffe9f66adcaca9cf0f76a93f4'
BYTES = 228908


def load_input(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('short input must be regular')
        data = stream.read(BYTES+1)
    if len(data) != BYTES or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('official short input bytes changed')
    with wave.open(io.BytesIO(data), 'rb') as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes(), wav.getcomptype()) != (1, 2, RATE, FRAMES, 'NONE'):
            raise ValueError('short PCM geometry changed')
        if len(wav.readframes(FRAMES+1)) != FRAMES*2:
            raise ValueError('short PCM frame count differs')
    return data


def validate_completed(page, chunks, capture):
    """Published EOS is input state only; prove one completed exact work chunk."""
    if page.get('state') in ('interrupted', 'cancelled', 'failed'):
        raise RuntimeError('page terminal failure')
    work = page.get('work_chunks', [])
    rows = chunks.get('chunks', [])
    if any(row.get('status') in ('failed', 'cancelled') for row in work+rows):
        raise RuntimeError('short chunk failed')
    if page.get('state') != 'published' or page.get('released') is not True:
        return False
    expected = {'page_number': 1, 'eos': True, 'start_frame': ORIGIN,
                'end_frame': END, 'capture_origin_frame': ORIGIN, 'capture_end_frame': END}
    if any(page.get(key) != value for key, value in expected.items()):
        raise ValueError('short page geometry differs')
    if len(page.get('sources', [])) != 1 or len(work) != 1 or len(rows) != 1:
        raise ValueError('short source/chunk counts differ')
    source, chunk, row = page['sources'][0], work[0], rows[0]
    if (source.get('frames'), source.get('bytes'), source.get('digest'), source.get('source_start_frame')) != (FRAMES, BYTES, SHA256, ORIGIN):
        raise ValueError('short accepted source differs')
    if (chunk.get('frames'), chunk.get('capture_start_frame'), chunk.get('timeline_start'),
            chunk.get('duration'), chunk.get('position')) != (FRAMES, ORIGIN, 0, FRAMES/RATE, 0):
        raise ValueError('short work offsets differ')
    if row.get('id') != chunk.get('chunk_id'):
        raise ValueError('short work identity differs')
    ranges = page.get('ranges', [])
    if len(ranges) != 1:
        raise ValueError('short frame conservation range count differs')
    item = ranges[0]
    if (item.get('source_id'), item.get('chunk_id'), item.get('source_start_frame'),
            item.get('capture_start_frame'), item.get('work_start_frame'), item.get('frames'),
            item.get('relation')) != (source.get('id'), row.get('id'), 0, ORIGIN, 0, FRAMES, 'owner'):
        raise ValueError('short exact frame conservation differs')
    if (capture.get('eos') is not True or capture.get('page_count') != 1
            or capture.get('released_pages') != 1 or capture.get('pending_page') is not None
            or capture.get('capture_origin_frame') != ORIGIN or capture.get('capture_end_frame') != END):
        raise ValueError('short capture closure differs')
    if chunk.get('status') != 'completed' or row.get('status') != 'completed':
        return False
    if (row.get('duration'), row.get('timeline_start'), row.get('next_offset')) != (FRAMES/RATE, 0, FRAMES/RATE):
        raise ValueError('short completed offsets differ')
    return True
