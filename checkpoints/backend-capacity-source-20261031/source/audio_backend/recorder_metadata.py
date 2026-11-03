"""Bounded read-only decoder for Android SegmentMetadata v1/v2.

Validating producer declarations is not authenticating microphone capture or
establishing a hardware/normalized-frame mapping. No importer lives here.
"""
import hashlib
import re
import struct
import uuid

MAX_BYTES = 8192
LONG_MAX = 2**63 - 1
GAPS = {'START', 'RESTART', 'ROUTE_CHANGE_UNCERTAIN', 'FORMAT_CHANGE', 'POLICY_SILENCE', 'DROPPED_FRAMES', 'UNKNOWN'}
CLOSES = {'ROTATION', 'STOP', 'INTERRUPTION', 'RECOVERED_TAIL'}
STATUSES = {'NOT_POLLED', 'AVAILABLE', 'UNAVAILABLE', 'QUERY_EXCEPTION'}


class InvalidMetadata(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise InvalidMetadata(reason)


def modified_utf8(data):
    """Accept canonical Java writeUTF bytes, including NUL and UTF-16 surrogates.

    Unpaired surrogates are rejected by this consumer, even though Java Strings
    can contain them: replacement-based identity hashing is not adopted.
    """
    units = []; i = 0
    while i < len(data):
        first = data[i]; i += 1
        if 1 <= first <= 0x7f:
            value = first
        elif 0xc0 <= first <= 0xdf:
            require(i < len(data) and data[i] & 0xc0 == 0x80, 'modified_utf8')
            value = ((first & 31) << 6) | (data[i] & 63); i += 1
            require(value >= 128 or (first == 0xc0 and value == 0), 'modified_utf8_overlong')
        elif 0xe0 <= first <= 0xef:
            require(i + 1 < len(data) and data[i] & 0xc0 == 0x80 and data[i+1] & 0xc0 == 0x80, 'modified_utf8')
            value = ((first & 15) << 12) | ((data[i] & 63) << 6) | (data[i+1] & 63); i += 2
            require(value >= 2048, 'modified_utf8_overlong')
        else:
            raise InvalidMetadata('modified_utf8')
        units.append(value)
        require(len(units) <= 512, 'metadata_text_units')
    try:
        return b''.join(struct.pack('>H', v) for v in units).decode('utf-16-be')
    except UnicodeError as exc:
        raise InvalidMetadata('unpaired_surrogate') from exc


class Reader:
    def __init__(self, data): self.data = data; self.offset = 0
    def take(self, count):
        require(count >= 0 and self.offset + count <= len(self.data), 'metadata_truncated')
        result = self.data[self.offset:self.offset+count]; self.offset += count
        return result
    def number(self, fmt): return struct.unpack('>' + fmt, self.take(struct.calcsize('>' + fmt)))[0]
    def i32(self): return self.number('i')
    def i64(self): return self.number('q')
    def text(self):
        count = self.number('H'); require(count <= 1536, 'metadata_text_bytes')
        value = modified_utf8(self.take(count))
        # Java String.trim removes only code units <= U+0020.
        require(value.strip(''.join(chr(i) for i in range(33))), 'metadata_text_empty')
        return value
    def boolean(self):
        value = self.number('B'); require(value in (0, 1), 'noncanonical_boolean')
        return bool(value)


def _read_observation(r):
    domain = r.text(); run = r.text()
    try: require(str(uuid.UUID(domain)) == domain, 'clock_domain')
    except ValueError as exc: raise InvalidMetadata('clock_domain') from exc
    names = ('ordinal', 'samples_before', 'samples_after', 'read_before', 'read_after',
             'wall_before', 'wall_millis', 'wall_after', 'uptime', 'configuration_before', 'configuration_after')
    item = dict(zip(names, (r.i64() for _ in names)), domain=domain, run_id=run)
    flags = r.i32(); status = r.text(); result = r.i32()
    timestamp = dict(zip(('frame_position', 'nano_time', 'query_before', 'query_after'), (r.i64() for _ in range(4))), status=status, result=result)
    require(status in STATUSES and timestamp['query_before'] >= 0 and timestamp['query_after'] >= 0, 'timestamp_fields')
    require(status != 'AVAILABLE' or result == 0, 'timestamp_available_result')
    require(status != 'UNAVAILABLE' or result != 0, 'timestamp_unavailable_result')
    require(status == 'AVAILABLE' or timestamp['frame_position'] == timestamp['nano_time'] == 0, 'timestamp_unavailable_values')
    require(status != 'NOT_POLLED' or result == timestamp['query_before'] == timestamp['query_after'] == 0, 'timestamp_not_polled')
    require(all(item[n] >= 0 for n in names if n != 'wall_millis'), 'observation_negative')
    require(item['samples_after'] > item['samples_before'] and flags & ~255 == 0, 'observation_fields')
    item.update(flags=flags, timestamp=timestamp)
    return item


def _anchors(r, run_id, frames, channels):
    require(r.i32() == 1 and r.i32() == 1 and r.text() == 'UNESTABLISHED', 'anchor_schema_mapping')
    names = ('sample_start', 'sample_end', 'read_count', 'query_count', 'unavailable_count', 'anomaly_count')
    a = dict(zip(names, (r.i64() for _ in names))); a['flags'] = r.i32()
    first = _read_observation(r); last = _read_observation(r)
    start, end, reads, queries, unavailable, anomalies = (a[n] for n in names)
    require(first['samples_before'] <= start < first['samples_after'] and last['samples_before'] < end <= last['samples_after'] and start < end, 'anchor_range')
    require(reads > 0 and 0 <= queries <= reads and 0 <= unavailable <= queries and 0 <= anomalies <= reads, 'anchor_counts')
    require(last['ordinal'] >= first['ordinal'] and reads == last['ordinal'] - first['ordinal'] + 1, 'anchor_ordinals')
    require(a['flags'] & ~255 == 0 and a['flags'] & (first['flags'] | last['flags']) == first['flags'] | last['flags'], 'anchor_flags')
    def same(item):
        require(item['domain'] == first['domain'] and item['run_id'] == first['run_id'], 'anchor_mixed_run')
    same(last)
    observations = [first, last]
    if queries > 0:
        fq = _read_observation(r); lq = _read_observation(r); same(fq); same(lq)
        require(first['ordinal'] <= fq['ordinal'] <= lq['ordinal'] <= last['ordinal'], 'anchor_query_ordinals')
        require(fq['timestamp']['status'] != 'NOT_POLLED' and lq['timestamp']['status'] != 'NOT_POLLED', 'anchor_query_status')
        observations += [fq, lq]
    if queries > unavailable:
        fa = _read_observation(r); la = _read_observation(r); same(fa); same(la)
        require(first['ordinal'] <= fa['ordinal'] <= la['ordinal'] <= last['ordinal'], 'anchor_available_ordinals')
        require(fa['timestamp']['status'] == la['timestamp']['status'] == 'AVAILABLE', 'anchor_available_status')
        observations += [fa, la]
    require(first['run_id'] == run_id and start % channels == 0 and end - start == frames * channels, 'anchor_pcm_binding')
    a.update(mapping='UNESTABLISHED', timebase='BOOTTIME', observations=observations)
    return a


def decode(data):
    require(type(data) is bytes and 40 <= len(data) <= MAX_BYTES, 'metadata_size')
    require(hashlib.sha256(data[:-32]).digest() == data[-32:], 'metadata_checksum')
    r = Reader(data[:-32]); require(r.take(4) == b'PCM1', 'metadata_magic')
    version = r.i32(); require(version in (1, 2), 'metadata_version')
    capture = r.text(); run = r.text(); epoch = r.i64(); rate = r.i32(); channels = r.i32()
    requested = r.text(); actual = r.text(); boundary = r.text()
    sequence = r.i64(); start = r.i64(); target = r.i64(); frames = r.i64()
    pcm_hash = r.text(); wav_hash = r.text(); close = r.text(); seam = r.boolean()
    require(epoch >= 0 and sequence >= 0 and start >= 0 and target > 0, 'intent_fields')
    require(rate > 0 and 1 <= channels <= 32 and boundary in GAPS, 'epoch_format')
    require(target * channels * 2 <= 0xffffffff - 36 and rate * channels * 2 <= 0xffffffff, 'riff_bound')
    require(0 < frames <= target and start + frames <= LONG_MAX and close in CLOSES, 'manifest_frames')
    require(close != 'ROTATION' or frames == target, 'rotation_frames')
    require(close != 'RECOVERED_TAIL' or not seam, 'recovered_seam')
    require(re.fullmatch(r'[0-9a-f]{64}', pcm_hash) and re.fullmatch(r'[0-9a-f]{64}', wav_hash), 'manifest_hash')
    anchors = _anchors(r, run, frames, channels) if version == 2 else None
    require(r.offset == len(r.data), 'metadata_trailing')
    return dict(schema_version=version, capture_id=capture, run_id=run, epoch=epoch,
                sample_rate_hz=rate, channels=channels, requested_input=requested, actual_input=actual,
                boundary=boundary, sequence=sequence, frame_start=start, target_frames=target,
                frames=frames, pcm_sha256=pcm_hash, wav_sha256=wav_hash, close_reason=close,
                trusted_previous_seam=seam, anchors=anchors)


def check_delivery_binding(metadata, delivery):
    expected = 'segment-' + hashlib.sha256(metadata['capture_id'].encode('utf-8')).hexdigest() + '-' + str(metadata['sequence']) + '.ready'
    require(expected == delivery['source_id'], 'metadata_source_binding')
    for key in ('frames', 'sample_rate_hz', 'channels', 'pcm_sha256', 'wav_sha256'):
        require(metadata[key] == delivery[key], 'metadata_wav_binding_' + key)
    return True
