"""Ordered background writes of immutable profile/settings snapshots."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from dataclasses import asdict
from domain import Profile, Settings, atomic_json, read_json


@dataclass(frozen=True)
class SaveResult:
    profile_saved: bool
    recent_name: str = ''
    error: str = ''


@dataclass(frozen=True)
class LoadedProfile:
    settings: Settings
    profile: Profile
    path: Path | None
    warning: str = ''


@dataclass
class _QueuedSave:
    path: Path
    profile: dict
    settings: dict
    future: Future | None = None


class ProfileStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.settings_path = self.directory / 'setting.json'
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='profile-save')
        self.settings_snapshot = None
        self.recent_copies = {}
        self.closed = False
        self.queue_lock = Lock()
        self.queued_save = None

    def load(self):
        warnings = []
        settings_valid = True
        try:
            settings = Settings.read(read_json(self.settings_path)) if self.settings_path.exists() else Settings()
        except Exception as error:
            settings = Settings()
            settings_valid = False
            warnings.append(f'환경설정 불러오기 실패: {error}')
        recent = self.directory / Path(settings.last_profile).name if settings.last_profile else None
        path = recent if recent and recent.is_file() else self.directory / 'macro.json'
        try:
            profile = Profile.read(read_json(path)) if path.exists() else Profile.default()
            profile.validate((settings.capture_hk, settings.pause_hk))
            if not path.exists() and not settings_valid:
                path = None
            elif not path.exists():
                result = self.save(path, profile.export(), asdict(settings)).result()
                if not result.profile_saved:
                    raise OSError(result.error)
                if result.error:
                    warnings.append(f'설정 저장 실패: {result.error}')
                if result.recent_name:
                    settings.last_profile = result.recent_name
            elif settings_valid:
                try:
                    settings.last_profile = self.settings(path, asdict(settings))
                except Exception as error:
                    warnings.append(f'설정 저장 실패: {error}')
        except Exception as error:
            profile, path = Profile.default(), None
            warnings.append(f'프로필 불러오기 실패: {error}')
        return LoadedProfile(settings, profile, path, ' · '.join(warnings))

    def next_path(self):
        path = self.directory / 'macro.json'
        number = 1
        while path.exists():
            path = self.directory / f'macro_{number}.json'
            number += 1
        return path

    def _recent_path(self, path):
        path = Path(path).resolve()
        if path.parent == self.directory:
            return path
        if path not in self.recent_copies:
            local = self.directory / path.name
            number = 1
            while local.exists() or local in self.recent_copies.values():
                local = self.directory / f'{path.stem}_{number}{path.suffix}'
                number += 1
            self.recent_copies[path] = local
        return self.recent_copies[path]

    def _settings(self, path, settings, profile=None):
        if path:
            local = self._recent_path(path)
            if local != Path(path).resolve():
                snapshot = profile if profile is not None else read_json(path)
                if not local.exists() or read_json(local) != snapshot:
                    atomic_json(local, snapshot)
            settings['last_profile'] = local.name
        if settings != self.settings_snapshot:
            atomic_json(self.settings_path, settings)
            self.settings_snapshot = deepcopy(settings)
        return settings.get('last_profile', '')

    def _save(self, path, profile, settings):
        try:
            atomic_json(path, profile)
        except Exception as error:
            return SaveResult(False, error=str(error))
        try:
            return SaveResult(True, self._settings(path, settings, profile))
        except Exception as error:
            return SaveResult(True, error=str(error))

    def _run_save(self, batch):
        with self.queue_lock:
            if self.queued_save is batch:
                self.queued_save = None
        # Once claimed by the worker, this snapshot cannot be replaced.
        return self._save(batch.path, batch.profile, batch.settings)

    def save(self, path, profile, settings, *, coalesce=False):
        """Merge only consecutive, unstarted autosaves for the same path.

        Merged callers share completion of the newest snapshot. Explicit saves,
        settings writes and flushes form barriers and retain submission order.
        """
        batch = _QueuedSave(Path(path), deepcopy(profile), deepcopy(settings))
        with self.queue_lock:
            if self.closed:
                raise RuntimeError('Profile store is closed')
            pending = self.queued_save
            if coalesce and pending is not None and pending.path == batch.path:
                pending.profile, pending.settings = batch.profile, batch.settings
                return pending.future
            self.queued_save = batch if coalesce else None
            batch.future = self.executor.submit(self._run_save, batch)
            return batch.future

    def settings(self, path, settings):
        with self.queue_lock:
            self.queued_save = None
            future = self.executor.submit(self._settings, path, deepcopy(settings))
        return future.result()

    def flush(self):
        with self.queue_lock:
            if self.closed:
                return
            self.queued_save = None
            future = self.executor.submit(lambda: None)
        future.result()

    def close(self):
        with self.queue_lock:
            if self.closed:
                return
            self.closed = True
            self.queued_save = None
        self.executor.shutdown(wait=True)
