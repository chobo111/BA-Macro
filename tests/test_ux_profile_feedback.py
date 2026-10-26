import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app import MainWindow
from domain import Profile, atomic_json, read_json
from persistence import ProfileStore, SaveResult


class ProfileFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.base = Path(self.folder.name)
        self.external = self.base / 'shared'
        self.external.mkdir()
        local = self.base / 'app'
        local.mkdir()
        with patch('app.Windows.windows', return_value=[]):
            self.w = MainWindow(data_dir=local, hook_enabled=False)
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

    def make_external(self):
        path = self.external / 'macro.json'
        atomic_json(path, Profile.default().export())
        return path

    def test_external_import_edits_same_local_file_after_restart_without_touching_original(self):
        w = self.w
        source = self.make_external()
        original = source.read_bytes()
        existing, before = w.path, w.path.read_bytes()
        self.assertTrue(w.switch_profile(source))
        local = w.path
        self.assertEqual(local.parent, w.data_dir)
        self.assertNotEqual(local, existing)
        self.assertIn('복사본', w.statusBar().currentMessage())
        self.assertEqual(w.profile_name.toolTip(), str(local))
        w.set_value(w.profile.rows[0].id, 'memo', 'local edits')
        w.flush_pending_saves()
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(existing.read_bytes(), before)
        restarted = ProfileStore(w.data_dir)
        try:
            loaded = restarted.load()
            self.assertEqual(loaded.path, local)
            self.assertEqual(loaded.profile.rows[0].memo, 'local edits')
        finally:
            restarted.close()
        self.assertTrue(w.switch_profile(source))
        self.assertNotEqual(w.path, local)
        self.assertEqual(read_json(local)['macros'][0]['memo'], 'local edits')

    def test_failed_import_keeps_current_profile_and_original(self):
        source = self.make_external()
        original = source.read_bytes()
        old_path, old_profile = self.w.path, self.w.profile
        with patch('persistence.atomic_json', side_effect=OSError('import denied')):
            self.assertFalse(self.w.switch_profile(source))
        self.assertEqual(self.w.path, old_path)
        self.assertIs(self.w.profile, old_profile)
        self.assertEqual(source.read_bytes(), original)
        self.assertIn('import denied', self.w.statusBar().currentMessage())

    def assert_export_is_local_for_editing(self, convert):
        w = self.w
        destination = self.external / 'export.json'
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(destination), '')):
            if convert:
                profile = deepcopy(w.profile)
                profile.resolution = (640, 360)
                self.assertTrue(w.save_conversion(profile, 'copy', w))
            else:
                self.assertTrue(w.save_as())
        self.assertEqual(w.path.parent, w.data_dir)
        self.assertNotEqual(w.path, destination)
        self.assertEqual(w.settings.last_profile, w.path.name)
        before = destination.read_bytes()
        w.set_value(w.profile.rows[0].id, 'memo', 'edit only local')
        w.flush_pending_saves()
        self.assertEqual(destination.read_bytes(), before)

    def test_save_as_external_keeps_local_editing_path(self):
        self.assert_export_is_local_for_editing(False)

    def test_conversion_export_keeps_local_editing_path(self):
        self.assert_export_is_local_for_editing(True)

    def fail_save(self):
        failed = Future()
        failed.set_result(SaveResult(False, error='disk full'))
        with patch.object(self.w.store, 'save', return_value=failed):
            self.assertTrue(self.w.save(automatic=True))
            self.w.flush_pending_saves()
        self.w.statusBar().clearMessage()
        self.qt.processEvents()

    def test_collapsed_failure_badge_shows_reason_and_retries_without_expanding(self):
        w = self.w
        self.fail_save()
        w.target_toggle.setChecked(False)
        self.qt.processEvents()
        self.assertTrue(w.collapsed_unsaved.isEnabled())
        self.assertIn('disk full', w.collapsed_unsaved.toolTip())
        seen = []
        def retry(dialog):
            seen.append(dialog.informativeText())
            next(button for button in dialog.buttons() if button.text() == '다시 저장').click()
            return dialog.result()
        pending = Future()
        with patch.object(QMessageBox, 'exec', retry), patch.object(w.store, 'save', return_value=pending) as save:
            QTest.mouseClick(w.collapsed_unsaved, Qt.MouseButton.LeftButton)
            self.assertEqual(save.call_count, 1)
            self.assertEqual(seen, ['disk full'])
            self.assertFalse(w.target_toggle.isChecked())
            self.assertFalse(w.collapsed_unsaved.isEnabled())
        pending.set_result(SaveResult(True))
        w.flush_pending_saves()
        self.assertTrue(w.collapsed_unsaved.isHidden())
        self.assertEqual(w.statusBar().currentMessage(), '저장 완료')

    def test_dismissing_save_failure_keeps_unsaved_edits(self):
        self.fail_save()
        def dismiss(dialog):
            next(button for button in dialog.buttons() if button.text() == '닫기').click()
            return dialog.result()
        with patch.object(QMessageBox, 'exec', dismiss), patch.object(self.w.store, 'save') as save:
            self.w.profile_unsaved.click()
            save.assert_not_called()
        self.assertTrue(self.w.dirty)
        self.assertTrue(self.w._profile_save_failed)

    def test_stale_completion_reenables_retry_without_clearing_newer_error(self):
        pending = Future()
        with patch.object(self.w.store, 'save', return_value=pending):
            self.w.save(automatic=True)
        with patch.object(self.w.store, 'save', side_effect=OSError('new failure')):
            self.w.save(automatic=True)
        self.assertFalse(self.w.profile_unsaved.isEnabled())
        pending.set_result(SaveResult(True))
        self.w.flush_pending_saves()
        self.assertTrue(self.w.profile_unsaved.isEnabled())
        self.assertIn('new failure', self.w.profile_unsaved.toolTip())

    def test_collapsed_app_status_tracks_missing_minimized_and_restored(self):
        w = self.w
        w.target_toggle.setChecked(False)
        w.update_target_info()
        self.assertEqual(w.collapsed_status.text(), '미실행')
        self.assertTrue(w.collapsed_status.isVisible())
        w.target = 123
        w.update_target_info(state='최소화')
        self.assertEqual(w.collapsed_status.text(), '최소화')
        self.assertFalse(w.resize_button.isEnabled())
        w.target_toggle.setChecked(True)
        self.assertTrue(w.collapsed_status.isHidden())
        w.target_toggle.setChecked(False)
        self.assertTrue(w.collapsed_status.isVisible())
        w.update_target_info((1920, 1080))
        self.assertTrue(w.collapsed_status.isHidden())
        self.assertTrue(w.resize_button.isEnabled())
        w.target = None
        w.update_target_info()
        self.assertEqual(w.collapsed_status.text(), '미실행')

    def test_long_shortcut_tooltip_preserves_full_chord_and_disabled_reason(self):
        w = self.w
        row = w.profile.rows[0]
        row.move_hk = 'alt+shift+page down'
        w.rebuild(row.id)
        button = w.table.cellWidget(0, 0)
        self.assertIn('Alt+Shift+PAGE DOWN', button.toolTip())
        self.assertIn('클릭하여 변경', button.toolTip())
        self.assertEqual(button.text(), 'ALT+SHIFT+PAGE DOWN')
        w._shortcut_issues[row.id] = 'duplicate'
        w.update_shortcut_button(button, row)
        self.assertIn('Alt+Shift+PAGE DOWN', button.toolTip())
        self.assertIn('duplicate', button.toolTip())
