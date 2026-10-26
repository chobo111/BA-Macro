"""Rename validates inside its dialog and rechecks before touching files."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog
from app import MainWindow
from domain import atomic_json, read_json
from profile_dialogs import RenameProfileDialog


class RenameValidationTests(unittest.TestCase):
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

    def test_invalid_names_stay_open_then_corrected_name_is_saved(self):
        w = self.w
        original, content = w.path, w.path.read_bytes()
        atomic_json(original.with_name('taken.json'), w.profile.export())

        def choose(dialog):
            for name in ('', '../escape', 'bad/name', 'CON', 'taken', 'setting'):
                dialog.name.setText(name)
                dialog.accept()
                self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertFalse(dialog.validation.isHidden())
                self.assertTrue(dialog.validation.text())
                self.assertEqual(w.path, original)
                self.assertEqual(original.read_bytes(), content)
            dialog.name.setText('수정한 이름.json')
            dialog.accept()
            return dialog.result()

        with patch.object(RenameProfileDialog, 'exec', choose):
            w.rename_profile()
        self.assertEqual(w.path.name, '수정한 이름.json')
        self.assertFalse(original.exists())
        self.assertEqual(w.path.read_bytes(), content)
        self.assertEqual(read_json(w.settings_path)['last_profile'], w.path.name)

    def test_invalid_name_then_cancel_preserves_original(self):
        w = self.w
        original, content = w.path, w.path.read_bytes()

        def cancel(dialog):
            dialog.name.setText('bad/name')
            dialog.accept()
            self.assertFalse(dialog.validation.isHidden())
            dialog.reject()
            return dialog.result()

        with patch.object(RenameProfileDialog, 'exec', cancel):
            w.rename_profile()
        self.assertEqual(w.path, original)
        self.assertEqual(original.read_bytes(), content)

    def test_collision_after_acceptance_never_overwrites_other_file(self):
        w = self.w
        original, content = w.path, w.path.read_bytes()
        destination = Path(self.folder.name) / 'raced.json'

        def choose(dialog):
            dialog.name.setText('raced')
            dialog.accept()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
            destination.write_bytes(b'another writer')
            return dialog.result()

        with patch.object(RenameProfileDialog, 'exec', choose):
            w.rename_profile()
        self.assertEqual(w.path, original)
        self.assertEqual(original.read_bytes(), content)
        self.assertEqual(destination.read_bytes(), b'another writer')
        self.assertIn('같은 이름', w.statusBar().currentMessage())
