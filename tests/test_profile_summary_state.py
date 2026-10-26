import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from concurrent.futures import Future
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QFileDialog

from app import MainWindow
from domain import Profile, atomic_json
from persistence import SaveResult
from shortcuts import ShortcutError
from profile_dialogs import DuplicateProfileDialog
from PySide6.QtWidgets import QDialog


class ProfileSummaryStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.patches = ExitStack()
        self.patches.enter_context(patch('app.Windows.windows', return_value=[]))
        self.w = MainWindow(data_dir=self.directory, hook_enabled=False)
        self.w.timer.stop()
        self.w.settings.auto_pause = False
        self.w.show()
        self.qt.processEvents()

    def tearDown(self):
        for _, future in self.w.profile_saves.pending():
            if not future.done():
                future.set_result(SaveResult(True))
        self.w.flush_pending_saves()
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.patches.close()
        self.folder.cleanup()

    def submit(self, future=None):
        future = future if future is not None else Future()
        with patch.object(self.w.store, 'save', return_value=future):
            self.assertTrue(self.w.save(automatic=True))
        return future, self.w.profile_saves.revision

    def complete(self, future, result):
        future.set_result(result)
        self.w.flush_pending_saves()
        self.qt.processEvents()

    def assert_badge(self, failed):
        self.assertEqual(self.w._profile_save_failed, failed)
        expanded = self.w.target_toggle.isChecked()
        self.assertEqual(self.w.profile_unsaved.isVisible(), failed and expanded)
        self.assertEqual(self.w.collapsed_unsaved.isVisible(), failed and not expanded)

    def test_pending_autosave_has_no_failure_badge(self):
        future, _ = self.submit()
        self.assertTrue(self.w.dirty)
        self.assert_badge(False)
        self.complete(future, SaveResult(True))
        self.assert_badge(False)

    def test_failure_remains_visible_during_retry_and_clears_after_success(self):
        future, _ = self.submit()
        self.complete(future, SaveResult(False, error='disk full'))
        self.assert_badge(True)
        retried, _ = self.submit()
        self.assert_badge(True)
        self.complete(retried, SaveResult(True))
        self.assert_badge(False)
        self.assertFalse(self.w.dirty)

    def test_settings_only_failure_does_not_mark_saved_profile_unsaved(self):
        future, _ = self.submit()
        self.complete(future, SaveResult(False, error='profile unavailable'))
        self.assert_badge(True)
        retried, _ = self.submit()
        self.complete(retried, SaveResult(True, error='settings unavailable'))
        self.assert_badge(False)
        self.assertFalse(self.w.dirty)
        self.assertEqual(self.w.settings_error, 'settings unavailable')

    def test_stale_failure_cannot_replace_latest_success(self):
        old, old_revision = self.submit()
        latest, latest_revision = self.submit()
        latest.set_result(SaveResult(True))
        self.w.save_completed(latest_revision, latest.result())
        self.assert_badge(False)
        old.set_result(SaveResult(False, error='stale failure'))
        self.w.save_completed(old_revision, old.result())
        self.qt.processEvents()
        self.assert_badge(False)
        self.assertFalse(self.w.dirty)

    def test_submission_failure_survives_older_pending_success(self):
        old, _ = self.submit()
        with patch.object(self.w.store, 'save', side_effect=RuntimeError('writer closed')):
            self.assertFalse(self.w.save(automatic=True))
        self.assert_badge(True)
        self.complete(old, SaveResult(True))
        self.assert_badge(True)
        self.assertTrue(self.w.dirty)

    def test_validation_failure_survives_older_pending_success(self):
        old, _ = self.submit()
        with patch.object(self.w, 'validate_profile_shortcuts', side_effect=ShortcutError('conflict')):
            self.assertFalse(self.w.save(automatic=True))
        self.assert_badge(True)
        self.complete(old, SaveResult(True))
        self.assert_badge(True)

    def test_coalesced_completion_applies_latest_failure_once(self):
        shared = Future()
        self.submit(shared)
        self.submit(shared)
        self.complete(shared, SaveResult(False, error='disk full'))
        self.assert_badge(True)
        self.assertEqual(self.w.profile_saves.pending(), ())

    def test_cancel_save_as_preserves_existing_failure_state(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                future, _ = self.submit()
                self.complete(future, SaveResult(not failed, error='disk full' if failed else ''))
                original = self.w.path
                with patch.object(QFileDialog, 'getSaveFileName', return_value=('', '')):
                    self.assertFalse(self.w.save_as())
                self.assertEqual(self.w.path, original)
                self.assert_badge(failed)

    def test_collapsed_summary_tracks_profile_and_badge_without_growing_window(self):
        w = self.w
        width = w.width()
        future, _ = self.submit()
        self.complete(future, SaveResult(False, error='disk full'))
        w.target_toggle.setChecked(False)
        self.qt.processEvents()
        self.assert_badge(True)
        self.assertTrue(w.collapsed_profile.isVisible())
        self.assertEqual(w.collapsed_profile.text(), f'지정 앱 없음 / {w.path.stem}')
        w.target_toggle.setChecked(True)
        self.qt.processEvents()
        self.assert_badge(True)
        self.assertFalse(w.collapsed_profile.isVisible())
        self.assertEqual(w.width(), width)

    def test_collapsed_profile_updates_after_switch_without_losing_basis(self):
        w = self.w
        # Match a settled window: the first themed table rebuild measures fonts.
        w.rebuild()
        self.qt.processEvents()
        destination = self.directory / ('긴 프로필 이름 ' * 8 + '.json')
        profile = Profile.default()
        profile.resolution = (1920, 1080)
        atomic_json(destination, profile.export())
        w.target_toggle.setChecked(False)
        self.qt.processEvents()
        width = w.width()
        self.assertTrue(w.switch_profile(destination))
        self.qt.processEvents()
        self.assertEqual(w.collapsed_profile.text(), f'지정 앱 없음 / {destination.stem}')
        self.assertEqual(w.profile_name.text(), destination.stem)
        self.assertEqual(w.profile_resolution.text(), '기준 1920×1080')
        self.assertTrue(w.collapsed_profile.isVisible())
        self.assert_badge(False)
        self.assertEqual(w.width(), width)

    def test_failed_duplicate_does_not_mark_unchanged_current_profile_unsaved(self):
        original = self.w.path
        failed = Future()
        failed.set_result(SaveResult(False, error='copy unavailable'))
        destination = self.directory / 'copy.json'
        with patch.object(DuplicateProfileDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
                patch.object(DuplicateProfileDialog, 'value', return_value=destination.stem), \
                patch.object(self.w.store, 'save', return_value=failed):
            self.assertFalse(self.w.duplicate_profile())
        self.assertEqual(self.w.path, original)
        self.assertFalse(self.w.dirty)
        self.assert_badge(False)



    def test_unselected_label_tracks_saved_app_without_selecting_window(self):
        w = self.w
        w.profile.target_program = {'exe':'Game.exe', 'class':'Unity', 'title':'Blue Archive'}
        w.update_labels()
        w.update_target_info()
        self.assertIsNone(w.target)
        self.assertIsNone(w.targets.currentData())
        self.assertEqual(w.targets.placeholderText(), 'Blue Archive')
        self.assertEqual(w.targets.toolTip(), 'Blue Archive')
        self.assertEqual(w.collapsed_profile.text(), f'Blue Archive / {w.path.stem}')
        self.assertEqual(w.resize_button.text(), '미실행')
        self.assertFalse(w.resize_button.isEnabled())
        w.profile.target_program = {'exe':'Other.exe', 'class':'Unity', 'title':''}
        w.update_labels()
        self.assertEqual(w.targets.placeholderText(), 'Other.exe')
        self.assertIsNone(w.target)

    def test_closed_app_returns_to_saved_app_name_and_not_running(self):
        w = self.w
        w.profile.target_program = {'exe':'Game.exe', 'class':'Unity', 'title':'Blue Archive'}
        w.targets.blockSignals(True)
        w.targets.addItem('Blue Archive (running)', 123)
        w.targets.setCurrentIndex(w.targets.findData(123))
        w.targets.blockSignals(False)
        w.target = 123
        w.update_profile_summary()
        w.update_target_info((1280,720))
        self.assertEqual(w.resize_button.text(), '현재 1280×720')
        with patch.object(w.native, 'exists', return_value=False):
            w.tick()
        self.assertIsNone(w.target)
        self.assertEqual(w.targets.placeholderText(), 'Blue Archive')
        self.assertEqual(w.resize_button.text(), '미실행')
        self.assertEqual(w.collapsed_profile.text(), f'Blue Archive / {w.path.stem}')

    def test_saved_app_placeholder_is_not_a_duplicate_menu_item(self):
        w = self.w
        w.profile.target_program = {'exe':'Game.exe', 'class':'Unity', 'title':'Blue Archive'}
        with patch.object(w.native, 'windows', return_value=[(123, 'Blue Archive')]), \
             patch.object(w.native, 'window_identity', return_value=dict(w.profile.target_program)):
            w.refresh_windows()
            self.assertEqual(w.targets.count(), 1)
            self.assertEqual(w.targets.itemData(0), 123)
            self.assertEqual(w.targets.itemText(0), 'Blue Archive')
            self.assertEqual(w.targets.placeholderText(), 'Blue Archive')
            self.assertEqual(w.targets.currentIndex(), -1)
            self.assertIsNone(w.target)
            self.assertEqual(w.resize_button.text(), '미실행')
            w.targets.setCurrentIndex(0)
            self.assertEqual(w.target, 123)
            w.refresh_windows()
            self.assertEqual(w.targets.currentData(), 123)
            self.assertEqual(w.targets.count(), 1)

    def test_missing_app_does_not_select_an_unrelated_first_window(self):
        w = self.w
        w.profile.target_program = {'exe':'Game.exe', 'class':'Unity', 'title':'Blue Archive'}
        for windows in ([], [(456, 'Other app')]):
            with self.subTest(windows=windows), \
                 patch.object(w.native, 'windows', return_value=windows), \
                 patch.object(w.native, 'find_target', return_value=None):
                w.refresh_windows(restore_saved=True)
                self.assertEqual(w.targets.count(), len(windows))
                self.assertEqual(w.targets.currentIndex(), -1)
                self.assertIsNone(w.target)
                self.assertEqual(w.targets.placeholderText(), 'Blue Archive')
                self.assertEqual(w.resize_button.text(), '미실행')


if __name__ == '__main__':
    unittest.main()
