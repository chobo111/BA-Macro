import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from concurrent.futures import Future
import tempfile
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QProgressDialog

from app import MainWindow
from domain import Profile, atomic_json
from profile_reads import read_profile
from profile_wait import wait_for_profile_read


class ProfileLoadTests(unittest.TestCase):
    def test_read_wait_discards_queued_coordinates_and_resumes_capture(self):
        w = self.w
        w.show()
        w.target = 123
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        self.qt.processEvents()
        future = Future()
        states = []
        def inspect_wait():
            states.append((editor.isEnabled(), editor.capture_enabled.is_set(),
                           QApplication.activeModalWidget()))
            editor.coordinate_clicked.emit(10, 20)
        with patch.object(editor, 'pick_coordinate') as pick, \
                patch.object(editor.native, 'local_point', return_value=(10, 20)):
            editor.coordinate_clicked.emit(10, 20)
            QTimer.singleShot(20, inspect_wait)
            QTimer.singleShot(70, lambda: future.set_result('done'))
            self.assertEqual(wait_for_profile_read(future, w), 'done')
            self.qt.processEvents()
            pick.assert_not_called()
            self.assertEqual(states, [(False, False, None)])
            self.assertTrue(editor.capture_enabled.is_set())
            editor.coordinate_clicked.emit(10, 20)
            self.qt.processEvents()
            pick.assert_called_once_with((10, 20))

    def test_busy_editor_discards_coordinates(self):
        w = self.w
        w.show()
        w.target = 123
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        with patch.object(editor, 'pick_coordinate') as pick:
            for flag in ('_profile_switching', '_waiting_for_write'):
                with self.subTest(flag=flag), patch.object(w, flag, True):
                    editor.capture_screen_point(10, 20)
                    self.assertFalse(editor.capture_enabled.is_set())
            pick.assert_not_called()
        editor.update_capture_enabled()
        self.assertTrue(editor.capture_enabled.is_set())

    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.windows = patch('app.Windows.windows', return_value=[])
        self.windows.start()
        self.w = MainWindow(data_dir=self.folder.name, hook_enabled=False)
        self.w.timer.stop()
        self.path = Path(self.folder.name) / 'other.json'
        atomic_json(self.path, Profile.default().export())

    def tearDown(self):
        self.w.shutdown()
        if self.w.profile_reader.started:
            self.w.profile_reader.thread.join(2)
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.windows.stop()
        self.folder.cleanup()

    def test_both_reads_run_off_gui_and_keep_original_until_complete(self):
        original, original_path = self.w.profile, self.w.path
        started, release = threading.Event(), threading.Event()
        reads, observations = [], []
        def slow(path):
            reads.append(threading.get_ident())
            release.clear()
            started.set()
            if not release.wait(2):
                raise TimeoutError('GUI is blocked')
            return read_profile(path)
        def heartbeat():
            if started.is_set():
                started.clear()
                observations.append((self.w.profile is original, self.w.path == original_path,
                                     self.w.eligible('pause'), self.w.centralWidget().isEnabled()))
                release.set()
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(heartbeat)
        timer.start()
        try:
            with patch('app.read_profile', side_effect=slow):
                self.assertTrue(self.w.switch_profile(self.path))
        finally:
            release.set()
            timer.stop()
        self.assertEqual(len(reads), 2)
        self.assertTrue(all(thread != threading.get_ident() for thread in reads))
        self.assertEqual(observations, [(True, True, False, False)] * 2)
        self.assertEqual(self.w.path, self.path)

    def test_second_read_observes_change_during_user_decision(self):
        def decision():
            profile = Profile.default()
            profile.rows[0].memo = 'changed during confirmation'
            atomic_json(self.path, profile.export())
            return True
        with patch.object(self.w, 'prepare_profile_change', side_effect=decision):
            self.assertTrue(self.w.switch_profile(self.path))
        self.assertEqual(self.w.profile.rows[0].memo, 'changed during confirmation')

    def test_invalid_second_read_keeps_current_profile(self):
        original, original_path = self.w.profile, self.w.path
        def decision():
            self.path.write_text('{broken', encoding='utf-8')
            return True
        with patch.object(self.w, 'prepare_profile_change', side_effect=decision):
            self.assertFalse(self.w.switch_profile(self.path))
        self.assertIs(self.w.profile, original)
        self.assertEqual(self.w.path, original_path)

    def test_cancel_slow_read_keeps_draft_and_ignores_late_result(self):
        original, original_path = self.w.profile, self.w.path
        self.w.edit_script(original.rows[0].id)
        editor = self.w.script_editor
        editor.editor.setPlainText('sleep 123')
        release = threading.Event()
        def slow(path):
            release.wait(2)
            return read_profile(path)
        def cancel():
            for dialog in self.w.findChildren(QProgressDialog):
                dialog.cancel()
        QTimer.singleShot(40, cancel)
        try:
            with patch('app.read_profile', side_effect=slow):
                self.assertFalse(self.w.switch_profile(self.path))
            self.assertFalse(release.is_set())
            self.assertIs(self.w.profile, original)
            self.assertEqual(self.w.path, original_path)
            self.assertIs(self.w.script_editor, editor)
            self.assertEqual(editor.editor.toPlainText(), 'sleep 123')
        finally:
            release.set()
        # Wait for the abandoned read without delivering anything to the UI.
        self.w.profile_reader.submit(lambda: None).result(timeout=2)
        self.qt.processEvents()
        self.assertIs(self.w.profile, original)
        self.w.finish_script_editor()

    def test_close_during_read_does_not_apply_late_profile(self):
        original = self.w.profile
        release = threading.Event()
        def slow(path):
            release.wait(2)
            return read_profile(path)
        QTimer.singleShot(40, self.w.close)
        try:
            with patch('app.read_profile', side_effect=slow):
                self.assertFalse(self.w.switch_profile(self.path))
            self.assertTrue(self.w.closing)
            self.assertFalse(release.is_set())
            self.assertIs(self.w.profile, original)
        finally:
            release.set()
        self.w.profile_reader.thread.join(2)
        self.qt.processEvents()
        self.assertIs(self.w.profile, original)
