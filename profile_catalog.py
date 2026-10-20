"""On-demand profile discovery in the current folder."""
from collections import OrderedDict
from concurrent.futures import CancelledError
from pathlib import Path
from stat import S_ISREG
from threading import Event
from domain import PROFILE_FORMAT, read_json
from profile_reads import ProfileReadWorker


class ProfileCatalog:
    """Bounded metadata cache for menu discovery, never for loading profiles."""
    def __init__(self, max_entries=256):
        if max_entries < 1:
            raise ValueError('Cache capacity must be positive')
        self.max_entries = max_entries
        self._entries = OrderedDict()

    @staticmethod
    def _signature(path):
        info = path.stat()
        if not S_ISREG(info.st_mode):
            raise OSError('Not a regular file')
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def is_profile(self, path):
        try:
            signature = self._signature(path)
            cached = self._entries.get(path)
            if cached is not None and cached[0] == signature:
                self._entries.move_to_end(path)
                return cached[1]
            self._entries.pop(path, None)
            try:
                data = read_json(path)
                valid = (isinstance(data, dict) and data.get('format') == PROFILE_FORMAT
                         and isinstance(data.get('macros'), list))
            except (ValueError, RecursionError):
                valid = False
            # Do not cache a read that raced another app's file write/replacement.
            if self._signature(path) == signature:
                self._entries[path] = signature, valid
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)
            return valid
        except OSError:
            # Access failures may be transient; retry on the next menu opening.
            self._entries.pop(path, None)
            return False

    def filter_profiles(self, paths, *, cancelled=None):
        """Check cached files before misses can evict them during a folder scan."""
        present = set(paths)
        for path in list(self._entries):
            if path not in present:
                del self._entries[path]
        cached = [path for path in paths if path in self._entries]
        uncached = [path for path in paths if path not in self._entries]
        valid = set()
        for path in cached + uncached:
            check_cancelled(cancelled)
            if self.is_profile(path):
                valid.add(path)
        check_cancelled(cancelled)
        return [path for path in paths if path in valid]


def check_cancelled(cancelled):
    if cancelled is not None and cancelled():
        raise CancelledError()


def profile_entries(directory, *, cache=None, cancelled=None):
    """Refresh file metadata on demand, parsing only changed/uncached files."""
    cache = cache if cache is not None else ProfileCatalog()
    check_cancelled(cancelled)
    result, seen = [], set()
    def add(path):
        try:
            path = Path(path).resolve()
        except OSError:
            return
        if path in seen or path.suffix.lower() != '.json':
            return
        seen.add(path)
        result.append(path)
    for path in sorted(Path(directory).glob('*.json'), key=lambda path: path.name.casefold()):
        check_cancelled(cancelled)
        add(path)
    return cache.filter_profiles(result, cancelled=cancelled)


class ProfileCatalogJobs:
    """One worker owns the cache; only the latest menu request is observed."""
    def __init__(self):
        self.cache = ProfileCatalog()
        self.worker = ProfileReadWorker('profile-catalog')
        self.future = None
        self.cancelled = Event()
        self.closed = False

    def cancel(self):
        self.cancelled.set()
        if self.future is not None:
            self.future.cancel()
            self.future = None

    def submit(self, directory):
        if self.closed:
            raise RuntimeError('Profile catalog is closed')
        self.cancel()
        self.cancelled = Event()
        self.future = self.worker.submit(profile_entries, Path(directory), cache=self.cache,
                                           cancelled=self.cancelled.is_set)
        return self.future

    def close(self):
        if not self.closed:
            self.closed = True
            self.cancel()
            self.worker.close()
