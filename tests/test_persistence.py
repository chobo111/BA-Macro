from dataclasses import asdict
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from domain import Profile, Settings, atomic_json, read_json
from persistence import ProfileStore


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = ProfileStore(self.folder.name)
        self.path = Path(self.folder.name) / 'macro.json'
        self.settings = asdict(Settings())

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_rename_updates_recent_profile_and_preserves_contents(self):
        profile = Profile.default().export()
        self.store.save(self.path, profile, self.settings).result()
        destination = self.store.rename_path(self.path, '레이드.JSON')
        result = self.store.rename(self.path, destination, self.settings)
        self.assertTrue(result.profile_saved)
        self.assertEqual(result.recent_name, '레이드.json')
        self.assertFalse(self.path.exists())
        self.assertEqual(read_json(destination), profile)
        self.assertEqual(self.store.load().path, destination)

    def test_rename_rejects_invalid_reserved_and_settings_names(self):
        for name in ('', '..', '../other', 'other/name', 'other\\name', 'bad:name',
                     'bad?.json', 'NUL', 'con.txt', 'COM1', '.json', 'name..json', 'setting'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.rename_path(self.path, name)

    def test_rename_never_overwrites_an_existing_profile(self):
        self.store.save(self.path, Profile.default().export(), self.settings).result()
        destination = self.path.with_name('other.json')
        destination.write_text('keep', encoding='utf-8')
        result = self.store.rename(self.path, destination, self.settings)
        self.assertFalse(result.profile_saved)
        self.assertTrue(self.path.exists())
        self.assertEqual(destination.read_text(encoding='utf-8'), 'keep')

    def test_rename_failure_preserves_original_and_recent_setting(self):
        self.store.save(self.path, Profile.default().export(), self.settings).result()
        destination = self.path.with_name('other.json')
        with patch.object(Path, 'rename', side_effect=OSError('locked')):
            result = self.store.rename(self.path, destination, self.settings)
        self.assertFalse(result.profile_saved)
        self.assertTrue(self.path.exists())
        self.assertFalse(destination.exists())
        self.assertEqual(read_json(self.store.settings_path)['last_profile'], 'macro.json')

    def test_rename_settings_failure_reports_file_already_moved(self):
        self.store.save(self.path, Profile.default().export(), self.settings).result()
        destination = self.path.with_name('other.json')
        with patch('persistence.atomic_json', side_effect=OSError('settings locked')):
            result = self.store.rename(self.path, destination, self.settings)
        self.assertTrue(result.profile_saved)
        self.assertIn('settings locked', result.error)
        self.assertTrue(destination.exists())
        self.assertFalse(self.path.exists())
        self.store.settings(destination, self.settings)
        self.assertEqual(self.store.load().path, destination)

    def test_rename_external_profile_creates_recent_copy_with_new_name(self):
        with tempfile.TemporaryDirectory() as external:
            source = Path(external) / 'external.json'
            profile = Profile.default().export()
            self.store.save(source, profile, self.settings).result()
            destination = source.with_name('new.json')
            result = self.store.rename(source, destination, self.settings)
            self.assertTrue(result.profile_saved)
            self.assertEqual(result.recent_name, 'new.json')
            self.assertEqual(read_json(self.store.directory / 'new.json'), profile)
            self.assertEqual(read_json(self.store.directory / 'external.json'), profile)
            self.assertEqual(self.store.load().path.name, 'new.json')

    def test_rename_waits_for_queued_autosaves(self):
        entered, release = threading.Event(), threading.Event()
        def write(path, data):
            if Path(path) == self.path and not entered.is_set():
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test timeout')
            atomic_json(path, data)
        profile = Profile.default().export()
        destination = self.path.with_name('new.json')
        with patch('persistence.atomic_json', side_effect=write):
            first = self.store.save(self.path, profile, self.settings, coalesce=True)
            try:
                self.assertTrue(entered.wait(1))
                profile['macros'][0]['memo'] = 'latest'
                last = self.store.save(self.path, profile, self.settings, coalesce=True)
            finally:
                release.set()
            result = self.store.rename(self.path, destination, self.settings)
        self.assertTrue(first.result().profile_saved)
        self.assertTrue(last.result().profile_saved)
        self.assertTrue(result.profile_saved)
        self.assertFalse(self.path.exists())
        self.assertEqual(read_json(destination)['macros'][0]['memo'], 'latest')

    def test_case_only_rename_updates_filename_and_recent_setting(self):
        self.store.save(self.path, Profile.default().export(), self.settings).result()
        destination = self.path.with_name('Macro.json')
        result = self.store.rename(self.path, destination, self.settings)
        self.assertTrue(result.profile_saved)
        self.assertEqual(result.recent_name, 'Macro.json')
        self.assertIn('Macro.json', [p.name for p in self.path.parent.iterdir()])

    def test_ordered_snapshots_and_unchanged_settings_not_rewritten(self):
        entered, release = threading.Event(), threading.Event()
        writes = []
        original_thread = threading.get_ident()
        def write(path, data):
            self.assertNotEqual(threading.get_ident(), original_thread)
            writes.append(Path(path).name)
            if len(writes) == 1:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test timeout')
            atomic_json(path, data)
        profile = Profile.default().export()
        with patch('persistence.atomic_json', side_effect=write):
            first = self.store.save(self.path, profile, self.settings)
            try:
                self.assertTrue(entered.wait(2))
                profile['macros'][0]['memo'] = 'latest'
                last = self.store.save(self.path, profile, self.settings)
                profile['macros'][0]['memo'] = 'mutated after submit'
                self.assertFalse(last.done())
            finally:
                release.set()
            self.assertTrue(first.result().profile_saved)
            self.assertTrue(last.result().profile_saved)
        self.assertEqual(read_json(self.path)['macros'][0]['memo'], 'latest')
        self.assertEqual(writes.count('macro.json'), 2)
        self.assertEqual(writes.count('setting.json'), 1)

    def test_partial_success_and_settings_retry(self):
        def fail_settings(path, data):
            if Path(path) == self.store.settings_path:
                raise OSError('settings unavailable')
            atomic_json(path, data)
        profile = Profile.default().export()
        with patch('persistence.atomic_json', side_effect=fail_settings):
            result = self.store.save(self.path, profile, self.settings).result()
        self.assertTrue(result.profile_saved)
        self.assertIn('settings unavailable', result.error)
        self.assertEqual(read_json(self.path), profile)
        self.assertEqual(self.store.settings(self.path, self.settings), 'macro.json')
        self.assertEqual(read_json(self.store.settings_path)['last_profile'], 'macro.json')

    def test_load_does_not_discard_profile_when_settings_write_fails(self):
        profile = Profile.default()
        profile.rows[0].memo = 'keep this'
        atomic_json(self.path, profile.export())
        with patch('persistence.atomic_json', side_effect=OSError('settings unavailable')):
            loaded = self.store.load()
        self.assertEqual(loaded.path, self.path)
        self.assertEqual(loaded.profile.rows[0].memo, 'keep this')
        self.assertIn('settings unavailable', loaded.warning)

    def test_close_drains_pending_writes(self):
        profile = Profile.default().export()
        future = self.store.save(self.path, profile, self.settings)
        self.store.close()
        self.assertTrue(future.done())
        self.assertEqual(read_json(self.path), profile)

    def test_autosave_burst_keeps_running_and_latest_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        writes = []
        def write(path, data):
            if Path(path) == self.path:
                writes.append(data['macros'][0]['memo'])
                if len(writes) == 1:
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError('test timeout')
            atomic_json(path, data)
        profile = Profile.default().export()
        with patch('persistence.atomic_json', side_effect=write):
            first = self.store.save(self.path, profile, self.settings, coalesce=True)
            try:
                self.assertTrue(entered.wait(2))
                pending = []
                for index in range(100):
                    profile['macros'][0]['memo'] = str(index)
                    pending.append(self.store.save(self.path, profile, self.settings, coalesce=True))
                profile['macros'][0]['memo'] = 'changed after submission'
                self.assertFalse(pending[-1].done())
            finally:
                release.set()
            self.store.close()
        self.assertTrue(first.result().profile_saved)
        self.assertTrue(all(f.done() and f.result().profile_saved for f in pending))
        self.assertEqual(writes, ['', '99'])
        self.assertEqual(read_json(self.path)['macros'][0]['memo'], '99')

    def test_explicit_save_and_different_paths_are_coalescing_barriers(self):
        release = threading.Event()
        self.store.executor.submit(lambda: release.wait(3))
        writes = []
        def write(path, data):
            if Path(path) != self.store.settings_path:
                writes.append((Path(path).name, data['macros'][0]['memo']))
            atomic_json(path, data)
        profile = Profile.default().export()
        other = self.path.with_name('other.json')
        with patch('persistence.atomic_json', side_effect=write):
            try:
                futures = []
                for path, memo, automatic in [(self.path, 'a', True), (self.path, 'explicit', False),
                                               (self.path, 'b', True), (other, 'other', True),
                                               (self.path, 'c', True), (self.path, 'latest', True)]:
                    profile['macros'][0]['memo'] = memo
                    futures.append(self.store.save(path, profile, self.settings, coalesce=automatic))
            finally:
                release.set()
            self.store.flush()
        self.assertTrue(all(f.result().profile_saved for f in futures))
        self.assertEqual(writes, [('macro.json', 'a'), ('macro.json', 'explicit'),
                                  ('macro.json', 'b'), ('other.json', 'other'), ('macro.json', 'latest')])

    def test_settings_write_separates_autosave_batches(self):
        release, submitted = threading.Event(), threading.Event()
        self.store.executor.submit(lambda: release.wait(3))
        profile = Profile.default().export()
        first = self.store.save(self.path, profile, self.settings, coalesce=True)
        submit = self.store.executor.submit
        def observe(fn, *args, **kwargs):
            future = submit(fn, *args, **kwargs)
            if fn == self.store._settings:
                submitted.set()
            return future
        errors = []
        def save_settings():
            try:
                self.store.settings(self.path, self.settings)
            except Exception as error:
                errors.append(error)
        with patch.object(self.store.executor, 'submit', side_effect=observe):
            thread = threading.Thread(target=save_settings)
            thread.start()
            try:
                self.assertTrue(submitted.wait(2))
                profile['macros'][0]['memo'] = 'after settings'
                last = self.store.save(self.path, profile, self.settings, coalesce=True)
                self.assertIsNot(first, last)
            finally:
                release.set()
                thread.join(3)
            self.store.flush()
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertTrue(last.result().profile_saved)
        self.assertEqual(read_json(self.path)['macros'][0]['memo'], 'after settings')

    def test_coalesced_failure_reaches_all_callers_and_can_retry(self):
        release = threading.Event()
        self.store.executor.submit(lambda: release.wait(3))
        profile = Profile.default().export()
        with patch('persistence.atomic_json', side_effect=OSError('disk unavailable')):
            try:
                first = self.store.save(self.path, profile, self.settings, coalesce=True)
                profile['macros'][0]['memo'] = 'latest'
                last = self.store.save(self.path, profile, self.settings, coalesce=True)
            finally:
                release.set()
            self.store.flush()
        for future in (first, last):
            self.assertFalse(future.result().profile_saved)
            self.assertIn('disk unavailable', future.result().error)
        self.assertTrue(self.store.save(self.path, profile, self.settings, coalesce=True).result().profile_saved)
        self.assertEqual(read_json(self.path)['macros'][0]['memo'], 'latest')

    def test_invalid_settings_are_not_overwritten_on_startup(self):
        invalid = {'click_delay': 7}
        atomic_json(self.store.settings_path, invalid)
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    atomic_json(self.path, Profile.default().export())
                before = self.store.settings_path.read_bytes()
                loaded = self.store.load()
                self.assertIn('지원하지 않는 설정 항목', loaded.warning)
                self.assertEqual(self.store.settings_path.read_bytes(), before)
                self.assertEqual(loaded.path, self.path if existing else None)

    def test_profile_cannot_overwrite_settings_through_aliases(self):
        original = b'{"theme":"Light"}'
        self.store.settings_path.write_bytes(original)
        alias = self.store.directory / 'linked.json'
        alias.hardlink_to(self.store.settings_path)
        candidates = (self.store.settings_path, self.store.directory / 'SETTING.JSON',
                      self.store.directory / 'sub' / '..' / 'setting.json', alias)
        for path in candidates:
            with self.subTest(path=path):
                for automatic in (False, True):
                    result = self.store.save(path, Profile.default().export(), self.settings,
                                             coalesce=automatic).result()
                    self.assertFalse(result.profile_saved)
                    self.assertIn('환경설정 파일', result.error)
                    self.assertEqual(self.store.settings_path.read_bytes(), original)
                with self.assertRaises(ValueError):
                    self.store.settings(path, self.settings)
        self.assertEqual(alias.read_bytes(), original)

    def test_external_setting_named_profile_uses_nonreserved_recent_copy(self):
        with tempfile.TemporaryDirectory() as external:
            path = Path(external) / 'setting.json'
            profile = Profile.default().export()
            result = self.store.save(path, profile, self.settings).result()
            self.assertTrue(result.profile_saved)
            self.assertEqual(result.error, '')
            self.assertEqual(result.recent_name, 'setting_1.json')
            self.assertEqual(read_json(self.store.directory / result.recent_name), profile)
            self.assertNotIn('macros', read_json(self.store.settings_path))

    def test_invalid_settings_survive_profile_autosave_and_settings_flush(self):
        original = b'{"click_hold_ms":9876,"auto_pause":false,"unknown":1}'
        self.store.settings_path.write_bytes(original)
        atomic_json(self.path, Profile.default().export())
        loaded = self.store.load()
        settings = asdict(loaded.settings)
        result = self.store.save(self.path, loaded.profile.export(), settings, coalesce=True).result()
        self.assertTrue(result.profile_saved)
        self.assertIn('원본을 보존', result.error)
        with self.assertRaises(ValueError):
            self.store.settings(self.path, settings)
        self.store.flush()
        self.assertEqual(self.store.settings_path.read_bytes(), original)

    def test_explicit_settings_recovery_preserves_raw_backup_and_resumes_autosave(self):
        original = b'{bad json'
        self.store.settings_path.write_bytes(original)
        existing_backup = self.store.directory / 'setting.invalid.bak'
        existing_backup.write_bytes(b'older backup')
        loaded = self.store.load()
        settings = asdict(loaded.settings)
        settings['click_hold_ms'] = 87
        self.store.settings(None, settings, recover=True)
        self.assertEqual(existing_backup.read_bytes(), b'older backup')
        self.assertEqual((self.store.directory / 'setting.invalid.1.bak').read_bytes(), original)
        self.assertEqual(read_json(self.store.settings_path)['click_hold_ms'], 87)
        self.assertFalse(self.store.settings_recovery_required)
        self.assertEqual(self.store.save(self.path, loaded.profile.export(), settings).result().error, '')

    def test_failed_settings_recovery_write_keeps_recovery_protection(self):
        original = b'{bad json'
        self.store.settings_path.write_bytes(original)
        loaded = self.store.load()
        with patch('persistence.atomic_json', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.store.settings(None, asdict(loaded.settings), recover=True)
        self.assertTrue(self.store.settings_recovery_required)
        self.assertEqual(self.store.settings_path.read_bytes(), original)
        with self.assertRaises(ValueError):
            self.store.settings(None, asdict(loaded.settings))

    def test_recent_copy_reused_only_while_store_owned_and_unchanged(self):
        with tempfile.TemporaryDirectory() as external:
            source = Path(external) / 'shared.json'
            profile = Profile.default().export()
            first = self.store.save(source, profile, self.settings).result()
            local = self.store.directory / first.recent_name
            profile['macros'][0]['memo'] = 'external update'
            second = self.store.save(source, profile, self.settings).result()
            self.assertEqual(second.recent_name, first.recent_name)
            self.assertEqual(read_json(local), profile)
            # Even opening the unchanged copy hands it over to the user.
            self.store.settings(local, self.settings)
            third = self.store.settings(source, self.settings)
            self.assertNotEqual(third, local.name)
            self.assertEqual(read_json(local), profile)

    def test_saving_local_recent_copy_preserves_edits_on_external_reopen(self):
        with tempfile.TemporaryDirectory() as external:
            source = Path(external) / 'shared.json'
            profile = Profile.default().export()
            first = self.store.save(source, profile, self.settings).result()
            local = self.store.directory / first.recent_name
            profile['macros'][0]['memo'] = 'local edits'
            self.assertTrue(self.store.save(local, profile, self.settings).result().profile_saved)
            original = local.read_bytes()
            recent = self.store.settings(source, self.settings)
            self.assertEqual(local.read_bytes(), original)
            self.assertNotEqual(recent, local.name)
            self.assertEqual(read_json(self.store.directory / recent), read_json(source))

    def test_recent_copy_manual_edits_or_damage_are_preserved(self):
        with tempfile.TemporaryDirectory() as external:
            for index, content in enumerate((b'{"manual":"edit"}', b'{broken json')):
                with self.subTest(content=content):
                    source = Path(external) / f'shared{index}.json'
                    first = self.store.save(source, Profile.default().export(), self.settings).result()
                    local = self.store.directory / first.recent_name
                    local.write_bytes(content)
                    recent = self.store.settings(source, self.settings)
                    self.assertEqual(local.read_bytes(), content)
                    self.assertNotEqual(recent, local.name)
                    self.assertEqual(read_json(self.store.directory / recent), read_json(source))

    def test_failed_recent_copy_write_can_retry_without_overwriting_existing_file(self):
        with tempfile.TemporaryDirectory() as external:
            source = Path(external) / 'shared.json'
            profile = Profile.default().export()
            atomic_json(source, profile)
            with patch('persistence.atomic_json', side_effect=OSError('copy unavailable')):
                with self.assertRaises(OSError):
                    self.store.settings(source, self.settings)
            local = self.store.directory / source.name
            local.write_bytes(b'user file created during failure')
            recent = self.store.settings(source, self.settings)
            self.assertNotEqual(recent, local.name)
            self.assertEqual(local.read_bytes(), b'user file created during failure')
