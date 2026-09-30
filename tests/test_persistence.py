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
