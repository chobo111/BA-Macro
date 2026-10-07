"""Ordered background writes of immutable profile/settings snapshots."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from hashlib import sha256
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


@dataclass(frozen=True)
class _RecentCopy:
    path: Path
    fingerprint: bytes


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
        self.settings_recovery_required = False
        self.recent_copies = {}
        self.closed = False
        self.queue_lock = Lock()
        self.queued_save = None

    def load(self):
        warnings = []
        settings_valid = True
        self.settings_recovery_required = False
        try:
            settings = Settings.read(read_json(self.settings_path)) if self.settings_path.exists() else Settings()
        except Exception as error:
            settings = Settings()
            settings_valid = False
            self.settings_recovery_required = True
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

    def _is_settings_path(self, path):
        candidate, settings = Path(path).resolve(), self.settings_path.resolve()
        # WindowsPath equality is case-insensitive; resolve also follows aliases.
        return candidate == settings or (candidate.exists() and settings.exists()
                                         and candidate.samefile(settings))

    def validate_profile_path(self, path):
        if self._is_settings_path(path):
            raise ValueError('환경설정 파일에는 프로필을 저장할 수 없습니다. 다른 파일명을 선택하세요.')

    def rename_path(self, path, name):
        name = name.strip()
        if name.lower().endswith('.json'):
            name = name[:-5]
        if (not name or name.endswith(('.', ' '))
                or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name)
                or PureWindowsPath(name + '.json').is_reserved()):
            raise ValueError('사용할 수 없는 파일명입니다. 다른 이름을 입력하세요.')
        destination = Path(path).with_name(name + '.json')
        self.validate_profile_path(destination)
        return destination

    def rename(self, source, destination, settings):
        """Rename after queued writes, then update the startup profile reference."""
        with self.queue_lock:
            if self.closed:
                raise RuntimeError('Profile store is closed')
            self.queued_save = None
            future = self.executor.submit(self._rename, Path(source), Path(destination), deepcopy(settings))
        return future.result()

    def _rename(self, source, destination, settings):
        try:
            self.validate_profile_path(source)
            self.validate_profile_path(destination)
            if source.parent.resolve() != destination.parent.resolve():
                raise ValueError('프로필 이름은 같은 폴더 안에서만 변경할 수 있습니다.')
            if destination.exists() and destination != source:
                raise FileExistsError('같은 이름의 파일이 있습니다. 다른 이름을 입력하세요.')
            # Windows rename fails if another file occupies the destination.
            source.rename(destination)
        except Exception as error:
            return SaveResult(False, error=str(error))
        self._release_recent_copy(source)
        self.recent_copies.pop(source.resolve(), None)
        try:
            return SaveResult(True, self._settings(destination, settings))
        except Exception as error:
            # The file has moved; callers must follow it even if settings failed.
            return SaveResult(True, error=str(error))

    def _backup_invalid_settings(self):
        if not self.settings_path.exists():
            return
        content = self.settings_path.read_bytes()
        index = 0
        while True:
            suffix = '' if index == 0 else f'.{index}'
            backup = self.settings_path.with_name(f'setting.invalid{suffix}.bak')
            try:
                with backup.open('xb') as stream:
                    stream.write(content)
                return
            except FileExistsError:
                index += 1

    def _release_recent_copy(self, path):
        # Opening or explicitly saving a local copy makes it an independent profile.
        path = Path(path).resolve()
        self.recent_copies = {source: copy for source, copy in self.recent_copies.items()
                              if copy.path != path}

    def _sync_recent_copy(self, path, profile):
        path = Path(path).resolve()
        if path.parent == self.directory:
            self._release_recent_copy(path)
            return path
        copy = self.recent_copies.get(path)
        if copy is not None:
            try:
                unchanged = sha256(copy.path.read_bytes()).digest() == copy.fingerprint
            except OSError:
                unchanged = False
            if not unchanged:
                del self.recent_copies[path]
                copy = None
        if copy is None:
            local = self.directory / path.name
            number = 1
            reserved = {item.path for item in self.recent_copies.values()}
            while local.exists() or local in reserved or self._is_settings_path(local):
                local = self.directory / f'{path.stem}_{number}{path.suffix}'
                number += 1
        else:
            local = copy.path
        snapshot = profile if profile is not None else read_json(path)
        if copy is None or read_json(local) != snapshot:
            atomic_json(local, snapshot)
        self.recent_copies[path] = _RecentCopy(local, sha256(local.read_bytes()).digest())
        return local

    def _settings(self, path, settings, profile=None, recover=False):
        if path:
            self.validate_profile_path(path)
        if self.settings_recovery_required and not recover:
            raise ValueError('불러오지 못한 환경설정 원본을 보존했습니다. 환경설정에서 저장하면 원본을 백업하고 새 설정을 적용합니다.')
        if self.settings_recovery_required:
            self._backup_invalid_settings()
        if path:
            settings['last_profile'] = self._sync_recent_copy(path, profile).name
        if self.settings_recovery_required or settings != self.settings_snapshot:
            atomic_json(self.settings_path, settings)
            self.settings_snapshot = deepcopy(settings)
        self.settings_recovery_required = False
        return settings.get('last_profile', '')

    def _save(self, path, profile, settings):
        try:
            self.validate_profile_path(path)
            self._release_recent_copy(path)
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

    def settings(self, path, settings, *, recover=False):
        with self.queue_lock:
            self.queued_save = None
            future = self.executor.submit(self._settings, path, deepcopy(settings), recover=recover)
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
