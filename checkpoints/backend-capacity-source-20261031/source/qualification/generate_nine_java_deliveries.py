"""Compile real recorder Java sources and generate nine host-only producer deliveries.

No wire metadata is generated or rewritten by Python. Output, classes, PCM and
logs must be outside the recorder/backend source trees. No device or service runs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pins(root, paths):
    return [dict(path=str(p.relative_to(root)), bytes=p.stat().st_size, sha256=digest(p)) for p in paths]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recorder-source', required=True, type=Path)
    parser.add_argument('--producer-checkout', required=True, type=Path)
    parser.add_argument('--producer-commit', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source, output = args.recorder_source.resolve(), args.output.resolve()
    backend = Path(__file__).resolve().parents[1]
    checkout = args.producer_checkout.resolve()
    def git(*command):
        return subprocess.check_output(['git', '-C', str(checkout), *command])
    if git('rev-parse', 'HEAD').decode().strip() != args.producer_commit:
        raise ValueError('checkout HEAD does not equal pinned commit')
    if git('status', '--porcelain=v1'):
        raise ValueError('producer checkout is dirty')
    receipt_path = source / 'ASSEMBLED_SOURCE.json'
    receipt = json.loads(receipt_path.read_bytes())
    if receipt['source_commit'] != args.producer_commit:
        raise ValueError('assembly commit mismatch')
    for row in receipt['files']:
        path = source / row['path']
        raw = path.read_bytes()
        if len(raw) != row['bytes'] or digest(path) != row['sha256']:
            raise ValueError('assembly file changed: ' + row['path'])
        committed = git('show', args.producer_commit + ':' + row['source_path'])
        if committed != raw:
            raise ValueError('assembly differs from committed source: ' + row['path'])
        if hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != row['git_blob']:
            raise ValueError('assembly Git blob mismatch: ' + row['path'])
    for tree in (source, backend):
        if output == tree or tree in output.parents:
            raise ValueError('output must be outside source trees')
    output.mkdir(mode=0o700, exist_ok=False)
    classes = output / 'classes'
    classes.mkdir()
    java = sorted((source / 'app/src/main/java/io/github/headmaster218/recorder/core').glob('*.java'))
    if not java or any(p.is_symlink() for p in java):
        raise ValueError('regular production Java sources required')
    before = pins(source, java)
    harness = Path(__file__).with_name('NineProducerDeliveries.java')
    flags = ['-Xmx64m', '-XX:MaxMetaspaceSize=48m', '-XX:ReservedCodeCacheSize=8m',
             '-XX:+UseSerialGC', '-XX:ActiveProcessorCount=1', '-Xss256k']
    command = ['java', *flags, '-m', 'jdk.compiler/com.sun.tools.javac.Main',
               '-source', '8', '-target', '8', '-Xlint:all,-options', '-Werror',
               '-d', str(classes), *map(str, java), str(harness)]
    with (output / 'compile.log').open('wb') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
    with (output / 'producer.log').open('wb') as log:
        subprocess.run(['java', *flags, '-cp', str(classes),
                       'io.github.headmaster218.recorder.core.NineProducerDeliveries',
                       str(output / 'producer')], stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=60)
    if before != pins(source, java):
        raise ValueError('production sources changed during run')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(backend))
    from audio_backend.recorder_delivery import inspect_delivery
    from audio_backend.recorder_metadata import decode
    rows = []
    for ordinal, directory in enumerate(sorted((output / 'producer/deliveries').iterdir())):
        report = inspect_delivery(directory, validate_metadata=True)
        expected_seq = ordinal + 2
        assert report['sequence'] == expected_seq and report['epoch'] == 7
        assert report['frame_start'] == expected_seq * 16000 and report['frames'] == 16000
        assert report['close_reason'] == ('INTERRUPTION' if ordinal == 8 else 'ROTATION')
        raw = (directory / 'segment.v1.bin').read_bytes()
        offset = 4
        for _ in range(2):
            size = struct.unpack_from('>H', raw, offset)[0]
            offset += 2 + size
        size = struct.unpack_from('>i', raw, offset)[0]
        local = raw[offset + 4:]
        assert len(local) == size
        original = output / 'producer/spool' / report['source_id'] / 'manifest.bin'
        assert local == original.read_bytes(), 'envelope differs from producer manifest'
        decoded = decode(local)
        rows.append(dict(ordinal=ordinal, path=str(directory), delivery=report,
                         original_metadata=decoded,
                         hashes={p.name: digest(p) for p in sorted(directory.iterdir())}))
    assert len(rows) == 9
    manifest = source / 'BUILD_SOURCE_INPUTS.json'
    differences = []
    if manifest.is_file():
        for row in json.loads(manifest.read_bytes()):
            path = source / row['path']
            if not path.is_file() or digest(path) != row['sha256']:
                differences.append(row['path'])
    result = dict(schema='real-java-producer-nine-host-v1', passed=True,
                  producer_execution='unchanged production Java recorder core on host JVM',
                  input='generated digital silence and synthetic observation timestamps',
                  generated_segments=11, selected_deliveries=9, total_selected_frames=144000,
                  original_first_sequence=2, original_last_sequence=10, original_epoch=7,
                  original_start_frame=32000, original_end_frame=176000,
                  recorder_source=str(source), production_source_pins=before,
                  harness_sha256=digest(harness), generator_sha256=digest(__file__),
                  class_pins=pins(classes, sorted(classes.rglob('*.class'))),
                  producer_commit=args.producer_commit, producer_checkout=str(checkout),
                  current_source_commit_verified=True, producer_checkout_clean=True,
                  assembly_receipt_sha256=digest(receipt_path),
                  verified_assembly_files=len(receipt['files']), overlays=receipt['overlays'],
                  build_input_manifest_sha256=digest(manifest) if manifest.is_file() else None,
                  stale_build_input_manifest_differences=differences,
                  device_test=False, network_test=False, native_analysis_test=False,
                  hardware_frame_mapping='UNESTABLISHED', deliveries=rows)
    (output / 'FIXTURE.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(dict(passed=True, deliveries=len(rows), fixture=str(output / 'FIXTURE.json'),
                         sha256=digest(output / 'FIXTURE.json')), sort_keys=True))


if __name__ == '__main__':
    main()
