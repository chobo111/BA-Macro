import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QProgressDialog
from app import MainWindow
from domain import Profile, atomic_json, read_json
from persistence import SaveResult
from profile_wait import wait_for_write


class WriteWaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        with patch('app.Windows.windows', return_value=[]):
            self.w = MainWindow(data_dir=self.folder.name, hook_enabled=False)
        self.w.timer.stop()
        self.w.show()
        self.qt.processEvents()

    def tearDown(self):
        self.w.shutdown()
        self.w.close()
        self.w.deleteLater()
        self.qt.processEvents()
        self.folder.cleanup()

    def test_settings_switch_keeps_gui_alive_and_blocks_reentry(self):
        w = self.w
        path = Path(self.folder.name)/'other.json'
        atomic_json(path, Profile.default().export())
        original = w.store._settings
        release = threading.Event()
        writing = threading.Event()
        observations = []
        def slow(*args, **kwargs):
            writing.set()
            if not release.wait(2):
                raise OSError('GUI failed to release writer')
            return original(*args, **kwargs)
        def heartbeat():
            if not writing.is_set():
                return
            timer.stop()
            observations.append((w._waiting_for_write, w.centralWidget().isEnabled(), w.eligible('pause')))
            w.close()
            observations.append(w.closing)
            release.set()
        with patch.object(w.store, '_settings', side_effect=slow):
            timer = QTimer()
            timer.setInterval(10)
            timer.timeout.connect(heartbeat)
            timer.start()
            try:
                self.assertTrue(w.switch_profile(path))
            finally:
                timer.stop()
                release.set()
        self.assertEqual(observations, [(True,False,False),False])
        self.assertEqual(w.path,path)
        self.assertEqual(read_json(w.settings_path)['last_profile'],'other.json')
        self.assertTrue(w.centralWidget().isEnabled())
        self.assertFalse(w._waiting_for_write)

    def test_manual_save_failure_preserves_dirty_profile_and_restores_controls(self):
        w = self.w
        before = w.path.read_bytes()
        w.profile.rows[0].memo = 'unsaved'
        pending = Future()
        QTimer.singleShot(30, lambda: pending.set_result(SaveResult(False,error='disk full')))
        with patch.object(w.store,'save',return_value=pending):
            self.assertFalse(w.save())
        self.assertTrue(w.dirty)
        self.assertEqual(w.profile.rows[0].memo,'unsaved')
        self.assertEqual(w.path.read_bytes(),before)
        self.assertTrue(w.centralWidget().isEnabled())
        self.assertFalse(w._waiting_for_write)

    def test_settings_failure_preserves_runtime_and_propagates_error(self):
        w = self.w
        previous = w.settings
        pending = Future()
        QTimer.singleShot(30, lambda: pending.set_exception(OSError('disk full')))
        with patch.object(w.store,'settings_async',return_value=pending):
            with self.assertRaisesRegex(OSError,'disk full'):
                w.commit_settings(replace(previous,click_hold_ms=999))
        self.assertIs(w.settings,previous)
        self.assertTrue(w.centralWidget().isEnabled())
        self.assertFalse(w._waiting_for_write)

    def test_conversion_save_failure_does_not_replace_profile(self):
        w = self.w
        original = w.profile
        before = w.path.read_bytes()
        pending = Future()
        QTimer.singleShot(30,lambda: pending.set_result(SaveResult(False,error='disk full')))
        with patch.object(w.store,'save',return_value=pending):
            with self.assertRaisesRegex(ValueError,'disk full'):
                w.save_conversion(replace(original,resolution=(1920,1080)),'current',w)
        self.assertIs(w.profile,original)
        self.assertEqual(w.path.read_bytes(),before)

    def test_active_dialog_is_disabled_and_restored_while_waiting(self):
        owner = QDialog(self.w)
        owner.setModal(True)
        owner.show()
        self.qt.processEvents()
        pending = Future()
        observations = []
        def heartbeat():
            observations.append(owner.isEnabled())
            pending.set_result('saved')
        QTimer.singleShot(30, heartbeat)
        try:
            self.assertEqual(wait_for_write(pending,self.w),'saved')
            self.assertEqual(observations,[False])
            self.assertTrue(owner.isEnabled())
        finally:
            owner.close()
            owner.deleteLater()

    def test_autosave_flush_processes_queued_completion_once(self):
        w = self.w
        pending = Future()
        QTimer.singleShot(30,lambda: pending.set_result(SaveResult(True)))
        with patch.object(w.store,'save',return_value=pending):
            w.profile.rows[0].memo = 'saved'
            self.assertTrue(w.save(automatic=True))
            w.flush_pending_saves()
        self.qt.processEvents()
        self.assertFalse(w.dirty)
        self.assertFalse(w.profile_saves.pending())

    def test_rename_finishes_on_worker_without_freezing_gui(self):
        w = self.w
        old = w.path
        release = threading.Event()
        original = w.store._rename
        def slow(*args, **kwargs):
            if not release.wait(2):
                raise OSError('GUI failed to release writer')
            return original(*args, **kwargs)
        with patch('app.RenameProfileDialog') as factory, patch.object(w.store,'_rename',side_effect=slow):
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.value.return_value = 'renamed'
            QTimer.singleShot(30,release.set)
            try:
                w.rename_profile()
            finally:
                release.set()
        self.assertEqual(w.path.name,'renamed.json')
        self.assertTrue(w.path.exists())
        self.assertFalse(old.exists())
        self.assertEqual(read_json(w.settings_path)['last_profile'],'renamed.json')

    def test_slow_write_progress_cannot_discard_committed_write(self):
        pending = Future()
        observations = []
        def inspect():
            progress = self.w.findChild(QProgressDialog)
            observations.append(progress.isVisible())
            progress.reject()
            progress.close()
            observations.append(progress.isVisible())
            pending.set_result('saved')
        QTimer.singleShot(350, inspect)
        self.assertEqual(wait_for_write(pending,self.w),'saved')
        self.assertEqual(observations,[True,True])
        self.assertFalse(self.w._waiting_for_write)
