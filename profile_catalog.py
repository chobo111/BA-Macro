"""On-demand profile discovery in the current folder."""
from collections import OrderedDict
from pathlib import Path
from stat import S_ISREG
from domain import read_json


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
                valid = isinstance(data, dict) and isinstance(data.get('macros'), list)
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

    def filter_profiles(self, paths):
        """Check cached files before misses can evict them during a folder scan."""
        present = set(paths)
        for path in list(self._entries):
            if path not in present:
                del self._entries[path]
        cached = [path for path in paths if path in self._entries]
        uncached = [path for path in paths if path not in self._entries]
        valid = {path for path in cached + uncached if self.is_profile(path)}
        return [path for path in paths if path in valid]


def profile_entries(directory, *, cache=None):
    """Refresh file metadata on demand, parsing only changed/uncached files."""
    cache = cache if cache is not None else ProfileCatalog()
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
        add(path)
    return cache.filter_profiles(result)
