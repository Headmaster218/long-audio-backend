"""Replay explicitly pinned local public PCM through unchanged Java recorder core.

Select nine one-second producer deliveries after two genuine preceding segments.
No model/service/device runs. No wire metadata is fabricated or edited by Python.
Output contains public audio and must remain outside all source trees and Git.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_nine_java_deliveries import digest, pins


def need(value, reason):
    if not value:
        raise ValueError(reason)


def pcm_crop(path, expected_sha256, start_frame):
    """Verify the whole local WAV before extracting exactly eleven PCM seconds."""
    need(not path.is_symlink() and path.is_file(), 'regular local WAV required')
    need(path.stat().st_size <= 512 * 1024**2, 'source WAV byte bound')
    need(len(expected_sha256) == 64 and
         all(c in '0123456789abcdef' for c in expected_sha256), 'explicit SHA-256 required')
    need(start_frame >= 0, 'negative source start')
    need(digest(path) == expected_sha256, 'source WAV hash mismatch')
    with wave.open(str(path), 'rb') as source:
        need((source.getnchannels(), source.getsampwidth(), source.getframerate(),
              source.getcomptype()) == (1, 2, 16000, 'NONE'), 'canonical PCM16 mono 16kHz required')
        need(source.getnframes() >= start_frame + 176000, 'source crop out of range')
        source.setpos(start_frame)
        pcm = source.readframes(176000)
    need(len(pcm) == 352000, 'short source crop')
    return pcm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recorder-source', required=True, type=Path)
    parser.add_argument('--producer-checkout', required=True, type=Path)
    parser.add_argument('--producer-commit', required=True)
    parser.add_argument('--input-wav', required=True, type=Path)
    parser.add_argument('--input-sha256', required=True)
    parser.add_argument('--source-start-frame', required=True, type=int)
    parser.add_argument('--source-title', required=True)
    parser.add_argument('--source-url', required=True)
    parser.add_argument('--license-url', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--terminal-reason', choices=('STOP', 'INTERRUPTION'), default='INTERRUPTION')
    args = parser.parse_args()
    os.umask(0o077)
    source, output = args.recorder_source.resolve(), args.output.resolve()
    backend = Path(__file__).resolve().parents[1]
    checkout = args.producer_checkout.resolve()
    input_wav = args.input_wav.absolute()
    pcm = pcm_crop(input_wav, args.input_sha256, args.source_start_frame)
    def git(*command):
        return subprocess.check_output(['git', '-C', str(checkout), *command])
    need(git('rev-parse', 'HEAD').decode().strip() == args.producer_commit, 'producer commit mismatch')
    need(not git('status', '--porcelain=v1'), 'dirty producer checkout')
    receipt_path = source / 'ASSEMBLED_SOURCE.json'
    receipt = json.loads(receipt_path.read_bytes())
    need(receipt['source_commit'] == args.producer_commit, 'assembly commit mismatch')
    for row in receipt['files']:
        path = source / row['path']
        need(not path.is_symlink(), 'assembly symlink')
        raw = path.read_bytes()
        need(len(raw) == row['bytes'] and digest(path) == row['sha256'], 'assembly file changed')
        need(git('show', args.producer_commit + ':' + row['source_path']) == raw,
             'assembly differs from committed source')
        need(hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
             == row['git_blob'], 'assembly Git blob mismatch')
    for tree in (source, backend, checkout):
        need(output != tree and tree not in output.parents, 'output must be outside source trees')
    output.mkdir(mode=0o700, exist_ok=False)
    classes = output / 'classes'
    classes.mkdir()
    raw_path = output / 'public-source-crop.pcm'
    raw_path.write_bytes(pcm)
    pcm_sha = digest(raw_path)
    java = sorted((source / 'app/src/main/java/io/github/headmaster218/recorder/core').glob('*.java'))
    need(java and not any(p.is_symlink() for p in java), 'regular production Java required')
    verified_paths = {row['path'] for row in receipt['files']}
    need(all(str(path.relative_to(source)) in verified_paths for path in java),
         'unverified production Java source')
    before = pins(source, java)
    harness = Path(__file__).with_name('PublicSpeechProducerDeliveries.java')
    harness_sha = digest(harness)
    flags = ['-Xmx64m', '-XX:MaxMetaspaceSize=48m', '-XX:ReservedCodeCacheSize=8m',
             '-XX:+UseSerialGC', '-XX:ActiveProcessorCount=1', '-Xss256k']
    with (output / 'compile.log').open('wb') as log:
        subprocess.run(['java', *flags, '-m', 'jdk.compiler/com.sun.tools.javac.Main',
            '-source', '8', '-target', '8', '-Xlint:all,-options', '-Werror',
            '-d', str(classes), *map(str, java), str(harness)],
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
    with (output / 'producer.log').open('wb') as log:
        subprocess.run(['java', *flags, '-cp', str(classes),
            'io.github.headmaster218.recorder.core.PublicSpeechProducerDeliveries',
            str(output / 'producer'), str(raw_path), pcm_sha, args.terminal_reason],
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
    need(before == pins(source, java), 'production Java changed')
    need(harness_sha == digest(harness), 'producer harness changed')
    need(digest(raw_path) == pcm_sha and digest(input_wav) == args.input_sha256, 'public input changed')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(backend))
    from audio_backend.recorder_delivery import inspect_delivery
    from audio_backend.recorder_metadata import decode
    rows = []
    for ordinal, directory in enumerate(sorted((output / 'producer/deliveries').iterdir())):
        report = inspect_delivery(directory, validate_metadata=True)
        sequence = ordinal + 2
        need(report['sequence'] == sequence and report['epoch'] == 7, 'original sequence or epoch changed')
        need(report['frame_start'] == sequence * 16000 and report['frames'] == 16000, 'original frames changed')
        need(report['close_reason'] == (args.terminal_reason if ordinal == 8 else 'ROTATION'), 'terminal reason changed')
        with wave.open(str(directory / 'audio.wav'), 'rb') as audio:
            actual = audio.readframes(16001)
        need(actual == pcm[sequence * 32000:(sequence + 1) * 32000], 'producer PCM differs from source')
        raw = (directory / 'segment.v1.bin').read_bytes()
        offset = 4
        for _ in range(2):
            size = struct.unpack_from('>H', raw, offset)[0]
            offset += 2 + size
        size = struct.unpack_from('>i', raw, offset)[0]
        local = raw[offset + 4:]
        need(len(local) == size, 'envelope metadata size mismatch')
        original = output / 'producer/spool' / report['source_id'] / 'manifest.bin'
        need(local == original.read_bytes(), 'envelope differs from producer manifest')
        rows.append(dict(ordinal=ordinal, path=str(directory), delivery=report,
            original_metadata=decode(local), source_frame_start=args.source_start_frame + sequence * 16000,
            source_frame_end=args.source_start_frame + (sequence + 1) * 16000,
            hashes={p.name: digest(p) for p in sorted(directory.iterdir())}))
    need(len(rows) == 9, 'selected delivery count mismatch')
    result = dict(schema='real-java-public-speech-nine-host-v1', passed=True,
        producer_execution='unchanged production Java recorder core on host JVM',
        input='pinned public speech PCM replay with synthetic observation timestamps',
        generated_segments=11, selected_deliveries=9, total_selected_frames=144000,
        original_first_sequence=2, original_last_sequence=10, original_epoch=7,
        original_start_frame=32000, original_end_frame=176000,
        terminal_reason=args.terminal_reason,
        terminal_api='PcmSegmentWriter.stop()' if args.terminal_reason == 'STOP' else 'PcmSegmentWriter.interrupt()',
        recorder_source=str(source), production_source_pins=before,
        harness_sha256=digest(harness), generator_sha256=digest(__file__),
        generator_helper_sha256=digest(Path(__file__).with_name('generate_nine_java_deliveries.py')),
        class_pins=pins(classes, sorted(classes.rglob('*.class'))),
        producer_commit=args.producer_commit, producer_checkout=str(checkout),
        current_source_commit_verified=True, producer_checkout_clean=True,
        assembly_receipt_sha256=digest(receipt_path), verified_assembly_files=len(receipt['files']),
        overlays=receipt['overlays'], source_title=args.source_title, source_url=args.source_url,
        license_url=args.license_url, source_wav=str(input_wav), source_wav_sha256=args.input_sha256,
        source_wav_bytes=input_wav.stat().st_size, generated_source_start_frame=args.source_start_frame,
        selected_source_start_frame=args.source_start_frame + 32000,
        selected_source_end_frame=args.source_start_frame + 176000,
        generated_pcm_sha256=pcm_sha, selected_pcm_sha256=hashlib.sha256(pcm[64000:]).hexdigest(),
        byte_transform='WAV container extraction only; exact original PCM; no gain/resampling/synthesis',
        device_test=False, network_test=False, native_analysis_test=False,
        hardware_frame_mapping='UNESTABLISHED', deliveries=rows)
    (output / 'FIXTURE.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(dict(passed=True, deliveries=len(rows), fixture=str(output / 'FIXTURE.json'),
                         sha256=digest(output / 'FIXTURE.json')), sort_keys=True))


if __name__ == '__main__':
    main()
