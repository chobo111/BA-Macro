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
