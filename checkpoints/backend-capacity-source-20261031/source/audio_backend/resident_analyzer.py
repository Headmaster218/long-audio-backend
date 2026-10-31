"""One Worker-owned, bounded resident analyzer; no shared model caches."""
import json
import tempfile
import threading
from pathlib import Path

from .resources import AnalyzerProcess


class ResidentAnalyzer:
    MAX_REQUESTS = 8

    def __init__(self, settings, *, factory=AnalyzerProcess):
        self.settings = settings
        self.factory = factory
        self.process = None
        self.directory = None
        self.config = None
        self.successes = 0
        self.poisoned = False
        self.uncertain = False
        self.closed = False
        self._lock = threading.Lock()
        self._active = False

    def _idle(self, process, config):
        try:
            current = json.dumps(self.settings.inference_config(), sort_keys=True, allow_nan=False)
            return (not self.closed and not self.poisoned and not self._active
                    and self.process is process and self.config == config == current
                    and not process.busy and process.proc.poll() is None)
        except Exception:
            return False

    def _retire(self):
        process = self.process
        if process is not None:
            # Disable reuse before touching the original owner's cleanup.
            self.poisoned = True
            permit = getattr(self.settings, '_native_execution_permit', None)
            try:
                process.close()
                if process.proc.poll() is None or process.proc.wait(timeout=0) is None:
                    raise RuntimeError('Resident analyzer original wait unconfirmed')
            except BaseException:
                self.uncertain = True
                raise
            finally:
                if permit is not None:
                    permit.finish_primary(process)
            # Never discard an uncertain handle or start another child over it.
            self.process = None
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None
        self.config = None
        self.successes = 0
        self.poisoned = self.uncertain

    def _ensure(self):
        config = json.dumps(self.settings.inference_config(), sort_keys=True, allow_nan=False)
        if self.process is not None and self.config != config:
            self._retire()
        if self.process is None:
            self.directory = tempfile.TemporaryDirectory(prefix='analyzer-', dir=self.settings.data_dir / 'tmp')
            path = Path(self.directory.name) / 'config.json'
            path.write_text(config)
            self.config = config
            self.process = self.factory(self.settings, path)
            permit = getattr(self.settings, '_native_execution_permit', None)
            if permit is not None:
                process = self.process
                permit.bind_primary(process, resident=lambda: self._idle(process, config))
        if self.process.proc.poll() is not None or self.process.busy:
            raise RuntimeError('Resident analyzer is not ready for a fresh request')
        return self.process

    def analyze(self, wav, output, stop, **arguments):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('Resident analyzer request already in flight')
        self._active = True
        try:
            if self.closed or self.poisoned:
                raise RuntimeError('Resident analyzer is closed or cleanup is uncertain')
            if stop():
                raise InterruptedError('Worker stopping or chunk paused/cancelled')
            if Path(output).exists():
                raise ValueError('Resident analyzer requires a fresh output path')
            process = self._ensure()
            result = process.analyze(wav, output, stop, **arguments)
            if stop():
                raise InterruptedError('Worker stopping or chunk paused/cancelled')
            self.successes += 1
            if self.successes >= self.MAX_REQUESTS:
                self._retire()
            return result
        except BaseException:
            if not self.uncertain:self._retire()
            raise
        finally:
            self._active = False
            self._lock.release()

    def retire(self):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('Cannot retire an in-flight resident analyzer')
        try:
            self._retire()
        finally:
            self._lock.release()

    def close(self):
        self.closed = True
        self.retire()
