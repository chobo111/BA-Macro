"""New profile destinations normalize extensions before any file is replaced."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app import MainWindow
from domain import Profile, Settings, read_json
from persistence import ProfileStore
from profile_catalog import profile_entries


class OutputPathTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = ProfileStore(self.folder.name)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_normalizes_new_destinations_and_preserves_json_case(self):
        for name, expected in (('profile', 'profile.json'), ('profile.txt', 'profile.json'),
                               ('profile.JSON', 'profile.JSON')):
            with self.subTest(name=name):
                self.assertEqual(self.store.output_path(Path(self.folder.name, name)).name, expected)

    def test_settings_protection_uses_normalized_destination(self):
        for name in ('setting', 'setting.txt', 'SETTING.JSON'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.output_path(Path(self.folder.name, name))

    def test_existing_non_json_profile_can_still_be_saved_and_loaded(self):
        path = Path(self.folder.name, 'legacy.txt')
        result = self.store.save(path, Profile.default().export(), asdict(Settings())).result()
        self.assertTrue(result.profile_saved)
        self.assertEqual(self.store.load().path, path)
        self.assertFalse(path.with_suffix('.json').exists())


class ProfileOutputUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle('Fusion')

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.w = MainWindow(data_dir=self.folder.name, hook_enabled=False)
        self.w.timer.stop()
        self.w.show()
        self.qt.processEvents()

    def tearDown(self):
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.folder.cleanup()

    def destination(self, name):
        return self.w.data_dir / name

    def converted(self):
        candidate = deepcopy(self.w.profile)
        candidate.resolution = (640, 360)
        return candidate

    @staticmethod
    def choose(label, messages=None):
        def execute(dialog):
            if messages is not None:
                messages.append(dialog.text())
            next(button for button in dialog.buttons() if button.text() == label).click()
            return dialog.result()
        return execute

    def test_save_as_normalizes_and_keeps_profile_in_folder_menu(self):
        for name in ('saved-profile.txt', 'no-extension'):
            with self.subTest(name=name), patch.object(QFileDialog, 'getSaveFileName',
                    return_value=(str(self.destination(name)), '')):
                self.assertTrue(self.w.save_as())
                path = self.destination(name).with_suffix('.json')
                self.assertEqual(self.w.path, path)
                self.assertEqual(Profile.read(read_json(path)).export(), self.w.profile.export())
                self.assertIn(path, profile_entries(self.w.data_dir))
                self.assertFalse(self.destination(name).exists())

    def test_conversion_copy_normalizes_and_keeps_profile_in_folder_menu(self):
        source, original = self.w.path, self.w.path.read_bytes()
        with patch.object(QFileDialog, 'getSaveFileName',
                return_value=(str(self.destination('converted.txt')), '')):
            self.assertTrue(self.w.save_conversion(self.converted(), 'copy', self.w))
        path = self.destination('converted.json')
        self.assertEqual(self.w.path, path)
        self.assertIn(path, profile_entries(self.w.data_dir))
        self.assertEqual(Profile.read(read_json(path)).resolution, (640, 360))
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(self.destination('converted.txt').exists())

    def test_normalized_overwrite_cancel_preserves_profile_and_both_files(self):
        source, profile = self.w.path, self.w.profile
        original = source.read_bytes()
        final = self.destination('existing.json')
        selected = self.destination('existing.txt')
        final.write_text('keep json', encoding='utf-8')
        selected.write_text('keep txt', encoding='utf-8')
        for mode in ('save', 'copy'):
            with self.subTest(mode=mode), patch.object(QFileDialog, 'getSaveFileName',
                    return_value=(str(selected), '')), \
                    patch.object(QMessageBox, 'exec', self.choose('취소')):
                result = self.w.save_as() if mode == 'save' else self.w.save_conversion(self.converted(), 'copy', self.w)
                self.assertFalse(result)
            self.assertEqual(self.w.path, source)
            self.assertIs(self.w.profile, profile)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(final.read_text(encoding='utf-8'), 'keep json')
            self.assertEqual(selected.read_text(encoding='utf-8'), 'keep txt')

    def test_normalized_overwrite_confirmation_names_actual_json_file(self):
        final, selected = self.destination('existing.json'), self.destination('existing.txt')
        final.write_text('replace json', encoding='utf-8')
        selected.write_text('keep txt', encoding='utf-8')
        messages = []
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(selected), '')), \
                patch.object(QMessageBox, 'exec', self.choose('덮어쓰기', messages)):
            self.assertTrue(self.w.save_as())
        self.assertEqual(len(messages), 1)
        self.assertIn(final.name, messages[0])
        self.assertNotIn(selected.name, messages[0])
        self.assertEqual(Profile.read(read_json(final)).export(), self.w.profile.export())
        self.assertEqual(selected.read_text(encoding='utf-8'), 'keep txt')

    def test_unchanged_json_path_does_not_repeat_native_overwrite_confirmation(self):
        selected = self.destination('existing.JSON')
        selected.write_text('replace', encoding='utf-8')
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(selected), '')), \
                patch.object(QMessageBox, 'exec', side_effect=AssertionError('duplicate overwrite dialog')):
            self.assertTrue(self.w.save_as())
        self.assertEqual(self.w.path, selected)
        self.assertEqual(Profile.read(read_json(selected)).export(), self.w.profile.export())

    def test_normalized_settings_destination_is_rejected_for_both_entrypoints(self):
        source = self.w.path
        settings_before = self.w.settings_path.read_bytes()
        with patch.object(QFileDialog, 'getSaveFileName',
                return_value=(str(self.destination('setting.txt')), '')):
            self.assertFalse(self.w.save_as())
            with self.assertRaises(ValueError):
                self.w.save_conversion(self.converted(), 'copy', self.w)
        self.assertEqual(self.w.path, source)
        self.assertEqual(self.w.settings_path.read_bytes(), settings_before)

    def test_conversion_copy_cannot_normalize_back_to_original(self):
        source, before, profile = self.w.path, self.w.path.read_bytes(), self.w.profile
        with patch.object(QFileDialog, 'getSaveFileName',
                return_value=(str(source.with_suffix('.txt')), '')), \
                patch.object(QMessageBox, 'exec', side_effect=AssertionError('original must not be offered for overwrite')):
            with self.assertRaises(ValueError):
                self.w.save_conversion(self.converted(), 'copy', self.w)
        self.assertEqual(self.w.path, source)
        self.assertIs(self.w.profile, profile)
        self.assertEqual(source.read_bytes(), before)

    def test_cancel_save_as_leaves_script_draft_open_and_unsaved(self):
        self.w.edit_script(self.w.profile.rows[0].id)
        editor = self.w.script_editor
        editor.editor.setPlainText('sleep 123')
        source, before = self.w.path, self.w.path.read_bytes()
        final = self.destination('existing.json')
        final.write_text('keep', encoding='utf-8')
        for selected in ('', str(final.with_suffix('.txt'))):
            with self.subTest(selected=selected), patch.object(QFileDialog, 'getSaveFileName',
                    return_value=(selected, '')), patch.object(QMessageBox, 'exec', self.choose('취소')):
                self.assertFalse(self.w.save_as())
            self.assertIs(self.w.script_editor, editor)
            self.assertEqual(editor.editor.toPlainText(), 'sleep 123')
            self.assertEqual(self.w.path, source)
            self.assertEqual(source.read_bytes(), before)
        self.assertEqual(final.read_text(encoding='utf-8'), 'keep')


if __name__ == '__main__':
    unittest.main()
