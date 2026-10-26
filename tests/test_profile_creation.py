import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog
from app import MainWindow
from profile_dialogs import NewProfileDialog, DuplicateProfileDialog


class ProfileCreationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.w = MainWindow(data_dir=self.folder.name, hook_enabled=False)
        self.w.timer.stop()

    def tearDown(self):
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.folder.cleanup()

    def test_app_dialog_keeps_invalid_names_open_then_saves_in_app_folder(self):
        w = self.w
        original_path, original = w.path, w.path.read_bytes()
        def choose(dialog):
            for name in ('', '..', '../escape', 'bad?', 'CON', 'macro', 'setting'):
                dialog.name.setText(name)
                dialog.accept()
                self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertFalse(dialog.validation.isHidden())
            dialog.name.setText('새 프로필.json')
            dialog.accept()
            return dialog.result()
        with patch.object(NewProfileDialog, 'exec', choose), patch.object(QFileDialog, 'exec') as explorer:
            w.new_profile()
        explorer.assert_not_called()
        self.assertEqual(w.path, Path(self.folder.name) / '새 프로필.json')
        self.assertTrue(w.path.is_file())
        self.assertEqual(original_path.read_bytes(), original)

    def test_destination_created_during_confirmation_is_not_overwritten(self):
        w = self.w
        original = w.profile
        destination = w.data_dir / 'new.json'
        def prepare():
            destination.write_text('keep', encoding='utf-8')
            return True
        with patch.object(NewProfileDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
                patch.object(NewProfileDialog, 'value', return_value='new'), \
                patch.object(w, 'prepare_profile_change', side_effect=prepare):
            w.new_profile()
        self.assertIs(w.profile, original)
        self.assertEqual(destination.read_text(encoding='utf-8'), 'keep')

    def test_duplicate_dialog_validates_name_and_keeps_source_folder(self):
        w = self.w
        source, before = w.path, w.path.read_bytes()
        occupied = source.with_name(source.stem + ' 복사본.json')
        occupied.write_text('keep', encoding='utf-8')
        def choose(dialog):
            self.assertEqual(dialog.windowTitle(), '프로필 복제')
            self.assertEqual(dialog.value(), source.stem + ' 복사본 2')
            for name in ('', '../escape', 'bad?', 'CON', source.stem, occupied.stem, 'setting'):
                dialog.name.setText(name)
                dialog.accept()
                self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertFalse(dialog.validation.isHidden())
            dialog.name.setText('복제 연습.json')
            dialog.accept()
            return dialog.result()
        with patch.object(DuplicateProfileDialog, 'exec', choose), \
                patch.object(QFileDialog, 'getSaveFileName') as explorer:
            self.assertTrue(w.duplicate_profile())
        explorer.assert_not_called()
        self.assertEqual(w.path, source.with_name('복제 연습.json'))
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(occupied.read_text(encoding='utf-8'), 'keep')
        self.assertEqual(w.path.read_bytes(), before)

    def test_cancel_duplicate_keeps_editor_draft_and_original(self):
        w = self.w
        source, profile = w.path, w.profile
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        editor.editor.setPlainText('sleep 42')
        with patch.object(DuplicateProfileDialog, 'exec', return_value=QDialog.DialogCode.Rejected), \
                patch.object(w, 'prepare_profile_change') as prepare:
            self.assertFalse(w.duplicate_profile())
        prepare.assert_not_called()
        self.assertEqual(w.path, source)
        self.assertIs(w.profile, profile)
        self.assertIs(w.script_editor, editor)
        self.assertEqual(editor.editor.toPlainText(), 'sleep 42')
        w.finish_script_editor()
