import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QLockFile
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from single_instance import SingleInstance


class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.primary = SingleInstance(self.folder.name)
        self.requests = []
        self.primary.activate_requested.connect(lambda: self.requests.append(True))

    def tearDown(self):
        self.primary.close()
        self.primary.deleteLater()
        self.qt.processEvents()
        self.folder.cleanup()

    def test_repeated_launch_requests_existing_window_and_keeps_lock(self):
        self.assertTrue(self.primary.acquire())
        with patch('single_instance.allow_foreground') as grant:
            for _ in range(3):
                secondary = SingleInstance(self.folder.name)
                try:
                    self.assertFalse(secondary.acquire())
                finally:
                    secondary.close()
                    secondary.deleteLater()
                self.qt.processEvents()
            self.assertEqual(self.requests, [True] * 3)
            self.assertEqual(grant.call_count, 3)
            grant.assert_called_with(os.getpid())
        competitor = QLockFile(str(Path(self.folder.name) / 'instance.lock'))
        self.assertFalse(competitor.tryLock(0))

    def test_close_allows_a_new_primary(self):
        self.assertTrue(self.primary.acquire())
        self.primary.close()
        replacement = SingleInstance(self.folder.name)
        try:
            self.assertTrue(replacement.acquire())
        finally:
            replacement.close()

    def test_failed_listener_releases_lock(self):
        with patch.object(self.primary.server, 'listen', return_value=False):
            with self.assertRaises(OSError):
                self.primary.acquire()
        replacement = SingleInstance(self.folder.name)
        try:
            self.assertTrue(replacement.acquire())
        finally:
            replacement.close()

    def test_older_running_version_is_restored_by_its_process_id(self):
        legacy = QLockFile(str(Path(self.folder.name) / 'instance.lock'))
        self.assertTrue(legacy.tryLock(0))
        try:
            with patch('single_instance.time.monotonic', side_effect=(0, 2)), \
                 patch('single_instance.allow_foreground') as grant, \
                 patch('single_instance.restore_process_window', return_value=True) as restore:
                self.assertFalse(self.primary.acquire())
                grant.assert_called_once_with(os.getpid())
                restore.assert_called_once_with(os.getpid())
        finally:
            legacy.unlock()

    def test_second_process_notifies_first_without_starting_another_owner(self):
        self.assertTrue(self.primary.acquire())
        source = (
            "import sys; from PySide6.QtCore import QCoreApplication; "
            "import single_instance; "
            "single_instance.allow_foreground=lambda pid: None; "
            "app=QCoreApplication([]); "
            "owner=single_instance.SingleInstance(sys.argv[1]); "
            "sys.exit(5 if owner.acquire() else 0)"
        )
        process = subprocess.Popen(
            [sys.executable, '-c', source, self.folder.name],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and (process.poll() is None or not self.requests):
                QTest.qWait(10)
            output, error = process.communicate(timeout=1)
            self.assertEqual(process.returncode, 0, error.decode(errors='replace'))
            self.assertEqual(self.requests, [True])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_lock_from_crashed_process_does_not_block_startup(self):
        source = (
            "import os,sys; from PySide6.QtCore import QCoreApplication,QLockFile; "
            "app=QCoreApplication([]); lock=QLockFile(sys.argv[1]); "
            "assert lock.tryLock(0); os._exit(0)"
        )
        result = subprocess.run(
            [sys.executable, '-c', source, str(Path(self.folder.name) / 'instance.lock')],
            capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertTrue(self.primary.acquire())
