# PUBLIC INSPECTION COPY. Do not execute or import.
"""Source-only endurance orchestration. No executable entry point or model imports.

A reviewed Operations implementation is deliberately required. This module does
not turn the short smoke's monitor into an endurance monitor by changing a timer.
"""
from __future__ import annotations

raise RuntimeError("Inspection-only transformed source: not an executable or importable distribution.")


from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Protocol
import hashlib
import os
import stat

MIB = 1024 ** 2
GIB = 1024 ** 3
FRAMES = 7_680_000
PREFIX_SHA256 = 'REDACTED_INPUT_CONTENT_SHA256'


class Stop(RuntimeError):
    """Preserve evidence and positively stop the owned family; never reset state."""


@dataclass(frozen=True)
class Plan:
    python: str
    api: str
    session_id: str
    capture_id: str
    journal: str
    origin: int = REDACTED_FRAME_ORIGIN
    inputs: int = 90
    period: int = 480
    restart_after: int = 45
    capture_seconds: int = 43_200
    overall_seconds: int = 86_400  # PROPOSED, not launch-approved.
    cleanup_seconds: int = 120
    stopped_seconds: int = 600
    append_seconds: int = 1800  # PROPOSED; smoke measurements must size it.
    attempts: int = 3
    disk_floor: int = 11 * GIB // 2
    disk_bytes: int = 8 * GIB
    memory_bytes: int = 6 * GIB
    max_original_bytes: int = 90 * (FRAMES * 2 + 44)

    def validate(self):
        if (self.inputs, self.period, self.capture_seconds, self.restart_after) != (90, 480, 43200, 45):
            raise Stop('This proposal is exactly the fixed 12-hour, 90-input experiment')
        if not 0 <= self.origin <= (1 << 53) - 1 - self.inputs * FRAMES:
            raise Stop('Invalid exact device-frame origin')
        if self.overall_seconds <= self.capture_seconds + self.stopped_seconds + self.cleanup_seconds:
            raise Stop('No bounded drain and stop reserve')
        if not 1 <= self.attempts <= 3:
            raise Stop('Retry bound')
        if not self.api.startswith('http://127.0.0.1:'):
            raise Stop('This experiment only permits its local API')
        if not all((self.python, self.session_id, self.capture_id, self.journal)):
            raise Stop('Explicit stable identities required')

    @property
    def live_seconds(self):
        return self.overall_seconds - self.stopped_seconds - self.cleanup_seconds


@dataclass(frozen=True)
class Input:
    index: int
    path: str
    # Full original identity is pinned before server start, not just a pathname.
    signature: tuple[int, int, int, int, int]
    sha256: str = PREFIX_SHA256

    @property
    def input_id(self):
        return f'repeated-prefix-{self.index + 1:03d}'


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def verify_inputs(plan: Plan, inputs: tuple[Input, ...], check):
    """Read-only; never creates copies. Streaming hash and bounded 90-entry index."""
    plan.validate()
    if len(inputs) != plan.inputs:
        raise Stop('Exactly 90 approved copies required')
    seen = set()
    paths = set()
    total = 0
    for index, item in enumerate(inputs):
        check()
        path = Path(item.path)
        if item.index != index or not path.is_absolute() or item.sha256 != PREFIX_SHA256:
            raise Stop('Input manifest order/path/hash mismatch')
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_mode & 0o222:
            raise Stop('Copies must be single-link, read-only regular files')
        if identity(before) != item.signature or item.signature[:2] in seen or str(path) in paths:
            raise Stop('Changed or reused original identity')
        digest = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            if identity(os.fstat(stream.fileno())) != item.signature:
                raise Stop('Input changed at open')
            while block := stream.read(MIB):
                check()
                digest.update(block)
            if identity(os.fstat(stream.fileno())) != item.signature:
                raise Stop('Input changed during hash')
        if digest.hexdigest() != item.sha256 or identity(path.lstat()) != item.signature:
            raise Stop('Input hash or final identity mismatch')
        total += before.st_size
        seen.add(item.signature[:2])
        paths.add(str(path))
    if total > plan.max_original_bytes:
        raise Stop('Original-byte envelope exceeded')


def uploader_argv(plan: Plan, remaining: float, item: Input | None):
    """Exact installed append-v3 CLI; same journal/session/capture on every retry."""
    if remaining <= 0:
        raise Stop('No upload deadline remains')
    argv = [plan.python, '-B', '-m', 'audio_backend.recording_uploader',
            '--api', plan.api, '--session-id', plan.session_id,
            '--capture-id', plan.capture_id, '--capture-origin-frame', str(plan.origin),
            '--state-dir', plan.journal, '--max-inputs', '90', '--max-hours', '12',
            '--max-original-bytes', str(plan.max_original_bytes),
            '--disk-floor-bytes', str(plan.disk_floor),
            '--deadline-seconds', str(min(plan.append_seconds, remaining))]
    if item is None:
        return argv + ['--finalize']
    return argv + ['--append', '--input', item.path, '--input-id', item.input_id,
                   '--input-start-frame', str(plan.origin + item.index * FRAMES)]


class Operations(Protocol):
    """Required adapter, not supplied or implicitly trusted by this proposal.

    Every method must enforce the frozen release, sampled charged family budget,
    disk budget/floor and absolute deadlines. Exceptions must not abandon children.
    See REVIEW.md for exact ownership, observation and checkpoint obligations.
    """
    def check(self) -> None: ...
    def elapsed(self) -> float: ...  # Single monotonic origin, never reset at restart.
    def wait_until(self, elapsed: float) -> None: ...  # Sample/observe while waiting.
    def event(self, kind: str, value: Mapping) -> None: ...  # Bounded durable log.
    def upload(self, argv: list[str]) -> Mapping: ...  # Positive original wait first.
    def checkpoint(self) -> Mapping: ...  # Authenticated append-v3, lock held.
    def start(self) -> str: ...  # Exact settings; one new empty legacy session ID.
    def restart(self) -> None: ...  # Stopped checkpoint/proof, wait, same state, compare.
    def primary_complete(self) -> bool: ...  # Exhaustive bounded final page sweep.
    def drain_tick(self, deadline: float) -> None: ...
    def stop_and_readback(self, successful_live: bool) -> Mapping: ...


def committed(checkpoint: Mapping, count: int, finalized: bool):
    """Normalized adapter view, never legacy uploader.json/pages assumptions."""
    expected = count * FRAMES
    if (checkpoint.get('inputs') != count or checkpoint.get('clean_inputs') != count
            or checkpoint.get('active_input') is not None
            or checkpoint.get('frames') != expected
            or checkpoint.get('finalized') is not finalized):
        raise Stop('Durable committed-input boundary does not match schedule')
    published = checkpoint.get('published_frames')
    if type(published) is not int or not 0 <= published <= expected:
        raise Stop('Invalid published frontier')
    if finalized and published != expected:
        raise Stop('Finalization must publish every frame')
    return published


def ingest(plan: Plan, ops: Operations, item: Input | None):
    count = plan.inputs if item is None else item.index + 1
    for attempt in range(1, plan.attempts + 1):
        ops.check()
        started = ops.elapsed()
        if started >= plan.live_seconds:
            raise Stop('Live deadline exhausted')
        result = ops.upload(uploader_argv(plan, plan.live_seconds - started, item))
        # The adapter only returns after positive original wait and owned-child
        # reconciliation. Retrying an uncertain still-live uploader is forbidden.
        if result.get('positive_wait') is not True or result.get('owned_children_resolved') is not True:
            raise Stop('Uploader lifecycle is unresolved; no retry')
        ops.event('append' if item else 'finalize', dict(
            input_id=item.input_id if item else None, attempt=attempt,
            elapsed=ops.elapsed(), seconds=ops.elapsed() - started,
            returncode=result.get('returncode'), outcome=result.get('outcome')))
        if result.get('returncode') == 0:
            return committed(ops.checkpoint(), count, item is None)
        if result.get('outcome') != 'retryable_uncertain' or attempt == plan.attempts:
            raise Stop('Ingestion failed; retain exact journal and stable input ID')
        # No alternate ID, new journal, new session, or skipped input. The product
        # authenticates/reconciles its last durable checkpoint on the same retry.
        ops.wait_until(min(ops.elapsed() + 5, plan.live_seconds))
    raise Stop('Unreachable retry exhaustion')


def resume_boundary(inputs: tuple[Input, ...], resume: Mapping, baseline: Mapping):
    """Bind a clean or partial moving boundary to the qualified receipt.

    This metadata-only check runs before startup. The adapter separately checks
    all pinned stopped source bytes; a fresh full checkpoint is compared later.
    """
    count = resume.get('completed_inputs')
    kind = resume.get('boundary_kind')
    journal = baseline.get('journal')
    if (type(count) is not int or not 0 <= count < len(inputs)
            or kind not in ('clean', 'partial')
            or not isinstance(journal, Mapping)
            or any(type(item.index) is not int or item.index != i
                   for i, item in enumerate(inputs))):
        raise Stop('Invalid pinned resume input boundary')
    for key, expected in (('inputs', count + (kind == 'partial')),
                          ('clean_inputs', count)):
        if type(journal.get(key)) is not int or journal[key] != expected:
            raise Stop('Resume count differs from pinned journal')
    item = inputs[count]
    prefix = count * FRAMES
    frames = journal.get('frames')
    published = journal.get('published_frames')
    if (resume.get('resume_input_id') != item.input_id
            or journal.get('finalized') is not False
            or 'active_input' not in journal
            or type(frames) is not int
            or type(published) is not int or not 0 <= published <= frames):
        raise Stop('Resume identity/finalization/frontier mismatch')
    active = journal['active_input']
    if kind == 'clean':
        if active is not None or frames != prefix:
            raise Stop('Clean resume boundary differs from pinned journal')
    else:
        if (not isinstance(active, Mapping)
                or active.get('id') != item.input_id
                or type(active.get('normalized_start')) is not int
                or active['normalized_start'] != prefix
                or active.get('clean_eof') is not False):
            raise Stop('Resume active input differs from ordered pinned boundary')
        consumed = active.get('frames')
        if (type(consumed) is not int or not 0 <= consumed <= FRAMES
                or frames != prefix + consumed):
            raise Stop('Resume frame cursor/frontier mismatch')
        original = active.get('identity')
        if (not isinstance(original, Mapping) or original.get('path') != item.path
                or original.get('sha256') != item.sha256
                or original.get('signature') != list(item.signature)):
            raise Stop('Resume active original differs from pinned input')
    return count, published


def run(plan: Plan, inputs: tuple[Input, ...], ops: Operations, resume=None):
    """Run only with separately approved adapter/release; no default actual path.

    Batch n arrives after n*480 seconds, not all at time zero. The last batch is
    due at 12 hours. If behind, retain FIFO and report lag; do not drop inputs.
    """
    plan.validate()
    successful = False
    try:
        verify_inputs(plan, inputs, ops.check)
        if resume is None:
            plan = replace(plan, session_id=ops.start())
            schedule_origin = ops.elapsed(); frontier = 0; skip = 0
        else:
            skip, frontier = resume_boundary(inputs, resume, ops.baseline)
            plan = replace(plan, session_id=ops.resume_start())
            schedule_origin = resume['started_upper'] - resume['started_lower'] + resume['schedule_offset']
            from resume_state import require_checkpoint
            checkpoint = ops.checkpoint()
            if checkpoint != ops.baseline['journal']:
                raise Stop('Resume checkpoint differs from pinned baseline')
            if require_checkpoint(checkpoint) != frontier:
                raise Stop('Resume published frontier changed')
        plan.validate()
        for item in inputs[skip:]:
            due = schedule_origin + (item.index + 1) * plan.period
            ops.wait_until(due)
            ops.check()
            ops.event('arrival', dict(input_id=item.input_id, due=due,
                                      lag_seconds=max(0., ops.elapsed() - due)))
            current = ingest(plan, ops, item)
            if current < frontier:
                raise Stop('Published capture frontier regressed')
            frontier = current
            if item.index + 1 == plan.restart_after:
                ops.restart()
                current = committed(ops.checkpoint(), plan.restart_after, False)
                if current < frontier:
                    raise Stop('Restart regressed published frontier')
                frontier = current
        ingest(plan, ops, None)
        while not ops.primary_complete():
            ops.check()
            if ops.elapsed() >= plan.live_seconds:
                raise Stop('Bounded drain exhausted with primary work incomplete')
            ops.drain_tick(plan.live_seconds)
        successful = True
    finally:
        # Must run even on adapter start/verification failure; adapter is safe when
        # no child exists. It preserves the failure, reports cleanup independently,
        # and never represents failed cleanup as an ordinary operation pass.
        report = ops.stop_and_readback(successful)
    return report
