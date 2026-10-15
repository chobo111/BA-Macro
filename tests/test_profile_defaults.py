"""Default presets must not take shortcuts already assigned to global actions."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog
from app import MainWindow
from domain import Profile, Settings, atomic_json, read_json
from persistence import ProfileStore

GAME = {'exe': 'Game.exe', 'class': 'Game', 'title': 'Game'}
OTHER = {'exe': 'Other.exe', 'class': 'Other', 'title': 'Other'}


class DefaultProfileTests(unittest.TestCase):
    def test_presets_only_clear_reserved_keys_and_keep_three_empty_rows(self):
        for reserved, expected in (((), ['q', 'w', 'e']), (('Q',), ['', 'w', 'e']),
                                   (('w',), ['q', '', 'e']), (('e',), ['q', 'w', '']),
                                   (('q', 'w'), ['', '', 'e']),
                                   (('ctrl+q', 'shift+e'), ['q', 'w', 'e'])):
            with self.subTest(reserved=reserved):
                profile = Profile.default(reserved)
                profile.validate(reserved)
                self.assertEqual([r.move_hk for r in profile.rows], expected)
                self.assertTrue(all(not r.memo and not r.script and r.x == r.y == 0
                                    and r.action_mode == 'move_only' for r in profile.rows))
                self.assertEqual(len({r.id for r in profile.rows}), 3)

    def load(self, contents=None):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            settings = Settings(pause_hk='q', capture_hk='w')
            atomic_json(directory / 'setting.json', asdict(settings))
            path = directory / 'macro.json'
            if contents is not None:
                path.write_text(contents, encoding='utf-8')
            store = ProfileStore(directory)
            try:
                loaded = store.load()
                loaded.profile.validate((loaded.settings.pause_hk, loaded.settings.capture_hk))
                self.assertEqual([r.move_hk for r in loaded.profile.rows], ['', '', 'e'])
                self.assertEqual((loaded.settings.pause_hk, loaded.settings.capture_hk), ('q', 'w'))
                if contents is None:
                    self.assertEqual(loaded.warning, '')
                    self.assertEqual(loaded.path, path)
                    self.assertEqual(Profile.read(read_json(path)), loaded.profile)
                else:
                    self.assertIsNone(loaded.path)
                    self.assertTrue(loaded.warning)
                    self.assertEqual(path.read_text(encoding='utf-8'), contents)
            finally:
                store.close()

    def test_missing_profile_is_created_with_nonconflicting_presets(self):
        self.load()

    def test_corrupt_profile_is_preserved_with_valid_fallback(self):
        self.load('{invalid json')

    def test_existing_conflicting_profile_is_not_silently_rewritten(self):
        import json
        self.load(json.dumps(Profile.default().export()))


class DefaultProfileUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        directory = Path(self.directory.name)
        profile = Profile.default()
        profile.rows[0].move_hk, profile.rows[1].move_hk = 'a', 's'
        profile.rows[0].memo = 'keep original'
        profile.target_program, profile.resolution = dict(GAME), (1280, 720)
        atomic_json(directory / 'macro.json', profile.export())
        atomic_json(directory / 'setting.json', asdict(Settings(pause_hk='q', capture_hk='w')))
        self.w = MainWindow(data_dir=directory, hook_enabled=False)
        self.w.timer.stop()
        self.w.target = 123
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(self.w.native, 'exists', return_value=True))
        self.patches.enter_context(patch.object(self.w.native, 'size', return_value=(1280, 720)))
        self.patches.enter_context(patch.object(self.w.native, 'window_identity', return_value=dict(OTHER)))
        self.patches.enter_context(patch.object(self.w.native, 'windows', return_value=[]))
        self.patches.enter_context(patch.object(self.w.engine, 'submit'))

    def tearDown(self):
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.patches.close()
        self.directory.cleanup()

    def assert_saved(self, previous_path, previous_bytes, expected):
        w = self.w
        self.assertNotEqual(w.path, previous_path)
        self.assertEqual(previous_path.read_bytes(), previous_bytes)
        self.assertEqual([row.move_hk for row in Profile.read(read_json(w.path)).rows], expected)
        w.validate_profile_shortcuts(w.profile)
        self.assertEqual((w.settings.pause_hk, w.settings.capture_hk), ('q', 'w'))
        self.assertEqual(read_json(w.settings_path)['last_profile'], w.path.name)
        self.assertFalse(w.dirty)
        self.assertFalse(w.armed)

    def test_file_menu_creates_new_profile_with_custom_global_keys(self):
        w = self.w
        previous_path, previous_bytes = w.path, w.path.read_bytes()
        destination = previous_path.with_name('new.json')
        with patch.object(QFileDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(QFileDialog, 'selectedFiles', return_value=[str(destination)]):
            w.new_profile()
        self.assertEqual(w.path, destination)
        self.assert_saved(previous_path, previous_bytes, ['', '', 'e'])

    def test_app_mismatch_new_profile_respects_global_keys(self):
        w = self.w
        previous_path, previous_bytes = w.path, w.path.read_bytes()
        with patch('app.AppMismatchDialog') as prompt:
            prompt.return_value.choice.return_value = 'new'
            w.toggle()
        self.assert_saved(previous_path, previous_bytes, ['', '', 'e'])
        self.assertEqual(w.profile.target_program, OTHER)

    def test_app_mismatch_copy_preserves_existing_row_keys(self):
        w = self.w
        previous_path, previous_bytes = w.path, w.path.read_bytes()
        with patch('app.AppMismatchDialog') as prompt:
            prompt.return_value.choice.return_value = 'copy'
            w.toggle()
        self.assert_saved(previous_path, previous_bytes, ['a', 's', 'e'])
        self.assertEqual(w.profile.rows[0].memo, 'keep original')
