"""Track profile-save completions without coupling file writes to Qt state."""
from concurrent.futures import Future
from pathlib import Path


class ProfileSaves:
    """UI-thread bookkeeping for ordered, possibly coalesced profile saves.

    Begin before submitting a write: even a failed submission makes older
    completions stale. Register each successful submission separately, including
    calls that share a Future because the store coalesced their snapshots.
    """

    def __init__(self):
        self._revision = 0
        self._pending: dict[int, tuple[Path, Future]] = {}

    @property
    def revision(self):
        return self._revision

    def begin(self):
        self._revision += 1
        return self._revision

    def register(self, revision, path, future):
        self._pending[revision] = (Path(path), future)

    def complete(self, revision, current_path):
        """Consume a completion; only the latest current-profile save applies."""
        pending = self._pending.pop(revision, None)
        return (pending is not None and revision == self._revision
                and current_path is not None and pending[0] == Path(current_path))

    def pending(self):
        """Snapshot submitted futures in order for a caller-managed flush."""
        return tuple((revision, future) for revision, (_, future)
                     in self._pending.items())
