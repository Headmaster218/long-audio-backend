"""Read-only asynchronous resource evidence across isolated PID namespaces.

This is an estimate, not a simultaneous aggregate-RSS guarantee. Never signal
PIDs from this feed. A separately approved source/freshness contract is required.
"""
from datetime import datetime
import json
from pathlib import Path
import time

from .fragment_model_guard import ResourceLimit


class EnduranceFeed:
    def __init__(self, metrics_path, progress_path, *, maximum_age_seconds=15):
        self.metrics = Path(metrics_path).resolve()
        self.progress = Path(progress_path).resolve()
        if not 0 < maximum_age_seconds <= 15:
            raise ValueError('External resource feed freshness must be <=15 seconds')
        self.maximum_age = maximum_age_seconds
        self.latest = None

    def sample(self):
        started = time.monotonic()
        failure = None
        while time.monotonic()-started <= 1.:
            try:
                before = self.metrics.stat()
                if before.st_size <= 0:
                    raise ValueError('Metrics feed empty')
                with self.metrics.open('rb') as source:
                    source.seek(max(0, before.st_size-256*1024))
                    data = source.read(256*1024)
                after = self.metrics.stat()
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError('Metrics feed changed during bounded read')
                if not data.endswith(b'\n'):
                    raise ValueError('Metrics feed last row is still being written')
                metric = json.loads(data.rstrip(b'\n').rsplit(b'\n', 1)[-1])
                if self.progress.stat().st_size > 1024*1024:
                    raise ValueError('Progress file exceeds bounded observer envelope')
                progress = json.loads(self.progress.read_text())
                observed = datetime.fromisoformat(progress['utc'].replace('Z', '+00:00'))
                if observed.tzinfo is None:
                    raise ValueError('Progress UTC has no timezone')
                now = time.time()
                ages = [now-after.st_mtime, now-observed.timestamp()]
                if any(age < -1 or age > self.maximum_age for age in ages):
                    raise ValueError(f'External resource evidence stale/future: ages={ages}')
                rss = metric.get('rss_bytes')
                if type(rss) is not int or rss < 0 or not isinstance(metric.get('processes'), list):
                    raise ValueError('External feed lacks explicit RSS/process identity evidence')
                # wall_seconds_this_invocation is deliberately optional: the
                # official observer writes a legitimate transitional progress.
                self.latest = {'rss_bytes': rss, 'pss_bytes': metric.get('pss_bytes'),
                    'metric_mtime': after.st_mtime, 'progress_utc': progress['utc'], 'ages_seconds': ages,
                    'processes': metric['processes'], 'observation': 'asynchronous_sample_not_simultaneous',
                    'processed_media_seconds': metric.get('processed_media_seconds')}
                return self.latest
            except (OSError, ValueError, KeyError, TypeError) as exc:
                failure = exc
                if self.latest is not None:
                    cached = dict(self.latest)
                    age = time.time()-cached['metric_mtime']
                    progress_age = time.time()-datetime.fromisoformat(cached['progress_utc'].replace('Z', '+00:00')).timestamp()
                    if 0 <= age <= self.maximum_age and 0 <= progress_age <= self.maximum_age:
                        cached.update(ages_seconds=[age, progress_age], transitional_read_using_last_fresh_sample=True)
                        return cached
                    raise ResourceLimit(f'Cached external sample is stale after transition: {failure}')
                time.sleep(.025)
        raise ResourceLimit(f'External resource feed unavailable after <=1s transition retry: {failure}')
