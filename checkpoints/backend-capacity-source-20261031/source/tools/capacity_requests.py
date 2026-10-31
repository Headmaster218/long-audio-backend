"""Pure request descriptions; no transport, implicit retries, or model imports.
Caller journals descriptions and completed responses before advancing.
"""
from urllib.parse import quote

RATE = 16000
CHUNK_FRAMES = 168000
CHUNKS_PER_PAGE = 28
PAGE_FRAMES = CHUNKS_PER_PAGE * CHUNK_FRAMES
SOURCE_FRAMES = (120 * RATE, 120 * RATE, 54 * RATE)


def page_base(sid, capture, number):
    assert type(number) is int and 1 <= number <= 1024
    return f'/sessions/{quote(sid, safe="")}/recordings/{quote(capture, safe="")}/pages/{number}'


def create_session(name):
    return ('POST', '/sessions', 201, {'name': name, 'processing_mode': 'legacy'}, {})


def create_page(sid, capture, number):
    base = page_base(sid, capture, number)
    return ('POST', base.rsplit('/', 1)[0], 202,
            {'page_number': number, 'capture_origin_frame': 0}, {})


def put_source(sid, capture, number, index, wav_bytes):
    """wav_bytes must be exact saved mono PCM16LE/16k WAV for this source."""
    assert type(index) is int and 0 <= index < len(SOURCE_FRAMES)
    start = (number - 1) * PAGE_FRAMES + sum(SOURCE_FRAMES[:index])
    return ('PUT', page_base(sid, capture, number) + f'/sources?source_start_frame={start}',
            202, wav_bytes, {'Content-Type': 'audio/wav',
            'Idempotency-Key': f'{capture}.page.{number}.source.{index + 1}'})


def seal_page(sid, capture, number, total_pages, revision):
    assert 1 <= number <= total_pages <= 1024
    assert type(revision) is int and revision >= 0
    return ('POST', page_base(sid, capture, number) + '/seal', 202,
            {'idempotency_key': f'{capture}.page.{number}.seal',
             'expected_revision': revision, 'end_frame': number * PAGE_FRAMES,
             'eos': number == total_pages}, {})


def inspect_page(sid, capture, number):
    return ('GET', page_base(sid, capture, number), 200, None, {})


def chunk_progress(sid, after=0):
    """Use after=last contiguous completed ROWID cursor, not page/chunk number.
    A page of 64 covers the <=56 active chunks. Never advance past unfinished.
    """
    assert type(after) is int and after >= 0
    return ('GET', f'/sessions/{quote(sid, safe="")}/chunks?after={after}&limit=64', 200, None, {})


def check_page_geometry(page, number, total_pages):
    """Check a successfully sealed page, before relying on its work identities."""
    start = (number - 1) * PAGE_FRAMES
    assert page['page_number'] == number and page['capture_origin_frame'] == 0
    assert page['start_frame'] == start and page['end_frame'] == start + PAGE_FRAMES
    assert page['eos'] is (number == total_pages)
    assert page['state'] in ('pending_receipts', 'published')
    rows = page['work_chunks']
    assert len(rows) == CHUNKS_PER_PAGE
    assert len({r['chunk_id'] for r in rows}) == CHUNKS_PER_PAGE
    for offset, row in enumerate(rows):
        position = (number - 1) * CHUNKS_PER_PAGE + offset
        assert row['position'] == position and row['frames'] == CHUNK_FRAMES
        assert row['capture_start_frame'] == position * CHUNK_FRAMES
        assert row['timeline_start'] == position * CHUNK_FRAMES / RATE
        assert row['duration'] == CHUNK_FRAMES / RATE
    assert page['optional_receipts']['gaps'] == 0
    if page['released']:
        assert page['state'] == 'published'
        assert page['capture_end_frame'] >= start + PAGE_FRAMES
        assert page['optional_receipts']['ready'] is True
    return tuple(r['chunk_id'] for r in rows)
