"""Starting imported fixed coordinates requires an explicit authoring basis."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from concurrent.futures import Future
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from app import MainWindow
from conversion_dialog import BaselineResolutionDialog
from domain import Profile, atomic_json, read_json
from persistence import SaveResult
from ui_test_support import fail_on_unexpected_error_dialogs, mock_target_context

GAME = {'exe': 'Game.exe', 'class': 'GameWindow', 'title': 'Game'}
OTHER = {'exe': 'Other.exe', 'class': 'OtherWindow', 'title': 'Other'}


def accept_basis(dialog):
    dialog.resolution.set_value((1280, 720))
    dialog.accept()
    return dialog.result()


class MissingBasisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle('Fusion')

    def setUp(self):
        fail_on_unexpected_error_dialogs(self)
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / 'macro.json'
        profile = Profile.default()
        profile.target_program = dict(GAME)
        profile.rows[0].x, profile.rows[0].y = 640, 360
        atomic_json(path, profile.export())
        self.window = MainWindow(data_dir=self.directory.name, hook_enabled=False)
        self.window.timer.stop()
        self.window.target = 123
        self.patches = ExitStack()
        self.patches.enter_context(mock_target_context(self.window))
        native = self.window.native
        self.exists = self.patches.enter_context(patch.object(native, 'exists', return_value=True))
        self.identity = self.patches.enter_context(patch.object(native, 'window_identity', return_value=dict(GAME)))
        self.patches.enter_context(patch.object(native, 'size', return_value=(1920, 1080)))
        self.patches.enter_context(patch.object(native, 'active', return_value=True))
        self.submit = self.patches.enter_context(patch.object(self.window.engine, 'submit'))

    def tearDown(self):
        self.window.shutdown()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.patches.close()
        self.directory.cleanup()

    def test_dialog_requires_both_dimensions_without_guessing(self):
        dialog = BaselineResolutionDialog(self.window)
        self.assertEqual(dialog.value(), (0, 0))
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.resolution.spins[0].setValue(1280)
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.resolution.spins[1].setValue(720)
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertEqual(dialog.save_button.text(), '저장')
        buttons = dialog.findChild(QDialogButtonBox)
        self.assertEqual(buttons.button(QDialogButtonBox.StandardButton.Cancel).text(), '취소')
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog.deleteLater()

    def test_cancel_blocks_fixed_and_retained_coordinates_even_with_auto_enabled(self):
        w = self.window
        row = w.profile.rows[0]
        cases = ((640, 360, 'move_only', ''), (0, 0, 'script', 'click 0,0'),
                 (0, 0, 'move_only', 'curve 100,200 mouse'),
                 (25, 50, 'script', 'sleep 5'), (0, 0, 'script', 'click 640,'))
        for automatic in (False, True):
            for x, y, mode, script in cases:
                with self.subTest(automatic=automatic, mode=mode, script=script):
                    row.x, row.y, row.action_mode, row.script = x, y, mode, script
                    w.settings.auto_convert_on_start = automatic
                    w.save()
                    before, before_file = w.profile.export(), w.path.read_bytes()
                    with patch.object(BaselineResolutionDialog, 'exec', return_value=QDialog.DialogCode.Rejected) as prompt, \
                         patch('app.SizeMismatchDialog') as mismatch:
                        w.toggle()
                    prompt.assert_called_once()
                    mismatch.assert_not_called()
                    self.assertFalse(w.armed)
                    self.assertIsNone(w.session.plan)
                    self.assertEqual(w.profile.export(), before)
                    self.assertEqual(w.path.read_bytes(), before_file)
        self.submit.assert_not_called()

    def test_unset_rows_and_mouse_only_scripts_do_not_require_a_basis(self):
        w = self.window
        row = w.profile.rows[0]
        row.x = row.y = 0
        for script in ('', 'sleep 5', 'click mouse', 'curve mouse mouse', '// click 640,360'):
            with self.subTest(script=script):
                row.action_mode = 'script' if script else 'move_only'
                row.script = script
                with patch('app.BaselineResolutionDialog') as prompt:
                    w.toggle()
                prompt.assert_not_called()
                self.assertTrue(w.armed)
                self.assertIsNone(w.profile.resolution)
                self.assertEqual(w.session.plan.expected_size, (1920, 1080))
                w.stop()

    def test_accept_saves_basis_without_scaling_then_next_start_converts_in_memory(self):
        w = self.window
        w.profile.rows[1].script = 'curve 100,200 mouse // keep source'
        before_rows = w.profile.export()['macros']
        with patch.object(BaselineResolutionDialog, 'exec', accept_basis), \
             patch('app.SizeMismatchDialog') as mismatch:
            w.toggle()
        mismatch.assert_not_called()
        self.assertFalse(w.armed)
        self.assertIsNone(w.session.plan)
        self.assertEqual(w.profile.resolution, (1280, 720))
        self.assertEqual(w.profile.export()['macros'], before_rows)
        saved = read_json(w.path)
        self.assertEqual(saved['target_resolution'], '1280x720')
        self.assertEqual(saved['macros'], before_rows)
        self.submit.assert_not_called()
        self.assertIn('다시 시작', w.statusBar().currentMessage())
        with patch('app.BaselineResolutionDialog') as prompt, patch('app.SizeMismatchDialog') as mismatch:
            mismatch.return_value.choice.return_value = 'auto'
            w.toggle()
        prompt.assert_not_called()
        mismatch.assert_called_once()
        self.assertTrue(w.armed)
        command = w.session.plan.command(w.profile.rows[0], w.settings)
        self.assertEqual((command.x, command.y), (960, 540))
        self.assertEqual(read_json(w.path), saved)

    def test_remembered_auto_conversion_still_requires_basis_first(self):
        w = self.window
        w.settings.auto_convert_on_start = True
        with patch.object(BaselineResolutionDialog, 'exec', accept_basis), patch('app.SizeMismatchDialog') as mismatch:
            w.toggle()
            self.assertFalse(w.armed)
            w.toggle()
        mismatch.assert_not_called()
        self.assertTrue(w.armed)
        self.assertTrue(w.session.plan.automatic)

    def test_profile_save_failure_preserves_original_profile_and_file(self):
        w = self.window
        before, path, before_file = w.profile.export(), w.path, w.path.read_bytes()
        future = Future()
        future.set_result(SaveResult(False, error='disk full'))
        with patch.object(BaselineResolutionDialog, 'exec', accept_basis), \
             patch.object(w.store, 'save', return_value=future), patch.object(w, 'error') as error:
            w.toggle()
        self.assertIn('disk full', error.call_args.args[0])
        self.assertEqual(w.profile.export(), before)
        self.assertEqual(w.path, path)
        self.assertEqual(path.read_bytes(), before_file)
        self.assertFalse(w.armed)
        self.submit.assert_not_called()

    def test_settings_only_failure_keeps_successfully_saved_basis(self):
        w = self.window
        with patch.object(BaselineResolutionDialog, 'exec', accept_basis), \
             patch.object(w.store, '_settings', side_effect=OSError('settings full')):
            w.toggle()
        self.assertEqual(w.profile.resolution, (1280, 720))
        self.assertEqual(Profile.read(read_json(w.path)).resolution, (1280, 720))
        self.assertIn('settings full', w.statusBar().currentMessage())
        self.assertFalse(w.armed)

    def test_context_change_during_dialog_cannot_write_metadata(self):
        w = self.window
        original_profile, original_path = w.profile, w.path
        before_file = original_path.read_bytes()
        changes = (lambda: setattr(w, 'target', 456),
                   lambda: setattr(w, 'path', original_path.with_name('other.json')),
                   lambda: setattr(w, 'profile', Profile.default()),
                   lambda: setattr(original_profile, 'resolution', (800, 600)),
                   lambda: setattr(self.exists, 'return_value', False),
                   lambda: setattr(self.identity, 'return_value', dict(OTHER)))
        for change in changes:
            with self.subTest(change=changes.index(change)):
                w.profile, w.path, w.target = original_profile, original_path, 123
                original_profile.resolution = None
                self.exists.return_value = True
                self.identity.return_value = dict(GAME)
                def accept_after_change(dialog):
                    change()
                    return accept_basis(dialog)
                with patch.object(BaselineResolutionDialog, 'exec', accept_after_change), patch.object(w.store, 'save') as save:
                    w.toggle()
                save.assert_not_called()
                self.assertFalse(w.armed)
                self.assertEqual(original_path.read_bytes(), before_file)
                self.assertIn('변경', w.statusBar().currentMessage())

    def test_changed_app_is_saved_only_with_confirmed_basis(self):
        w = self.window
        self.identity.return_value = dict(OTHER)
        original = w.profile.export()
        with patch('app.AppMismatchDialog') as mismatch, \
             patch.object(BaselineResolutionDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
            mismatch.return_value.choice.return_value = 'change'
            w.toggle()
        self.assertEqual(w.profile.export(), original)
        with patch('app.AppMismatchDialog') as mismatch, patch.object(BaselineResolutionDialog, 'exec', accept_basis):
            mismatch.return_value.choice.return_value = 'change'
            w.toggle()
        self.assertFalse(w.armed)
        self.assertEqual(w.profile.target_program, OTHER)
        self.assertEqual(w.profile.resolution, (1280, 720))
        self.assertEqual(Profile.read(read_json(w.path)).target_program, OTHER)

    def test_cancel_app_mismatch_does_not_open_basis_dialog(self):
        self.identity.return_value = dict(OTHER)
        with patch('app.AppMismatchDialog') as mismatch, patch('app.BaselineResolutionDialog') as prompt:
            mismatch.return_value.choice.return_value = 'cancel'
            self.window.toggle()
        prompt.assert_not_called()
        self.assertFalse(self.window.armed)

    def test_reentrant_start_is_ignored_even_with_auto_pause_disabled(self):
        w = self.window
        w.settings.auto_pause = False
        calls = []
        def accept_after_start(dialog):
            calls.append(True)
            w.toggle()
            self.assertFalse(w.armed)
            return accept_basis(dialog)
        with patch.object(BaselineResolutionDialog, 'exec', accept_after_start):
            w.toggle()
        self.assertEqual(calls, [True])
        self.assertFalse(w.armed)
        self.assertIsNone(w.session.plan)
        self.submit.assert_not_called()
