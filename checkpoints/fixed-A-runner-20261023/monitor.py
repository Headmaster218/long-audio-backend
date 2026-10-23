"""Sampled, conservative resource checks. No process or service is launched here."""
import os
import stat
import time

GIB = 1024 ** 3
RSS_CAP = 6 * GIB
OUTPUT_CAP = GIB - 32 * 1024 ** 2
FREE_FLOOR = 11 * GIB // 2


class ResourceRefusal(RuntimeError):
    pass


class RetainedMonitor:
    """Inode lifetime maxima survive disappearance; partial scans never succeed."""
    def __init__(self, root, deadline, *, clock=time.monotonic, sleep=time.sleep):
        self.root = os.fspath(root)
        root_info = os.stat(self.root, follow_symlinks=False)
        if not stat.S_ISDIR(root_info.st_mode):
            raise ResourceRefusal('fresh output root is not a directory')
        self.root_identity = (root_info.st_dev, root_info.st_ino)
        self.deadline = deadline
        self.clock, self.sleep = clock, sleep
        self.seen = {}
        self.last_complete = None
        self.full_scans = self.partial_scans = self.recovered = 0
        self.sampling_gap = False
        self.max_gap_seconds = 0.0
        self.last_recovered_gap_seconds = None

    def _deadline(self, until):
        if self.clock() >= min(until, self.deadline):
            raise ResourceRefusal('resource observation deadline exceeded')

    def _floor(self):
        fs = os.statvfs(self.root)
        if fs.f_bavail * fs.f_frsize < FREE_FLOOR:
            raise ResourceRefusal('free space floor violated')

    def _cap(self):
        if len(self.seen) > 65536 or sum(self.seen.values()) > OUTPUT_CAP:
            raise ResourceRefusal('retained inode envelope exceeded')

    def _visit(self, fd, depth=0):
        if depth > 64:
            raise ResourceRefusal('output directory depth exceeded')
        with os.scandir(fd) as entries:
            self._visit_entries(fd, depth, entries)

    def _visit_entries(self, fd, depth, entries):
        for entry in entries:
            name = entry.name
            self._entries += 1
            if self._entries > 65536:
                raise ResourceRefusal('output traversal entry cap exceeded')
            self._deadline(self._scan_until)
            self._floor()
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                key = (info.st_dev, info.st_ino)
                self.seen[key] = max(self.seen.get(key, 0), info.st_size,
                                     info.st_blocks * 512)
                self._cap()
            elif stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=fd)
                try:
                    actual = os.fstat(child)
                    if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                        raise ResourceRefusal('directory identity changed')
                    self._visit(child, depth+1)
                finally:
                    os.close(child)
            else:
                raise ResourceRefusal('symlink or special output entry')

    def _scan(self):
        self._entries = 0
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != self.root_identity:
                raise ResourceRefusal('output root identity changed')
            self._visit(fd)
        finally:
            os.close(fd)

    def check(self):
        partial_start = None
        for attempt in range(32):
            until = self.deadline if partial_start is None else partial_start + 1.0
            self._deadline(until)
            self._floor()
            self._cap()
            self._scan_until = min(until, self.deadline)
            try:
                self._scan()
            except FileNotFoundError:
                now = self.clock()
                self.partial_scans += 1
                self.sampling_gap = True
                if partial_start is None:
                    partial_start = now
                if self.last_complete is not None:
                    self.max_gap_seconds = max(self.max_gap_seconds, now-self.last_complete)
                self._floor()
                self._cap()
                self._deadline(partial_start + 1.0)
                self.sleep(min(.01, max(0, min(self.deadline, partial_start+1)-self.clock())))
                continue
            now = self.clock()
            self.full_scans += 1
            if self.sampling_gap and self.last_complete is not None:
                gap = now - self.last_complete
                self.max_gap_seconds = max(self.max_gap_seconds, gap)
                self.last_recovered_gap_seconds = gap
            self._floor()
            self._cap()
            self._deadline(until)
            self.last_complete = now
            if partial_start is not None:
                self.recovered += 1
            self.sampling_gap = False
            return sum(self.seen.values())
        raise ResourceRefusal('fresh complete scan unavailable within 32 attempts')


def sampled_rss(originals, psutil):
    """Include supervisor; only proven exited original PID races are tolerated."""
    supervisor = psutil.Process(os.getpid())
    total, seen = supervisor.memory_info().rss, {supervisor.pid}
    for original in originals:
        try:
            process = psutil.Process(original.pid)
            processes = [process, *process.children(recursive=True)]
        except psutil.NoSuchProcess as exc:
            if exc.pid == original.pid and original.poll() is not None:
                continue
            raise
        for process in processes:
            if process.pid in seen:
                continue
            seen.add(process.pid)
            try:
                total += process.memory_info().rss
            except psutil.NoSuchProcess as exc:
                if not (process.pid == original.pid == exc.pid and original.poll() is not None):
                    raise
    if total > RSS_CAP:
        raise ResourceRefusal('sampled RSS envelope exceeded')
    return total
