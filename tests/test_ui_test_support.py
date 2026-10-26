"""Unexpected Qt callback errors must produce failures, not modal waits."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from ui_test_support import fail_on_unexpected_error_dialogs


class UiTestSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_unexpected_warning_in_qt_slot_becomes_test_failure(self):
        class Probe(unittest.TestCase):
            def runTest(self):
                fail_on_unexpected_error_dialogs(self)
                button = QPushButton()
                button.clicked.connect(lambda: QMessageBox.warning(None, 'Start failed', 'fake HWND'))
                button.click()

        result = unittest.TestResult()
        Probe().run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.failures), 1)
        self.assertIn('Start failed: fake HWND', result.failures[0][1])

    def test_explicit_warning_mock_still_controls_expected_error(self):
        class Probe(unittest.TestCase):
            def runTest(self):
                fail_on_unexpected_error_dialogs(self)
                with patch.object(QMessageBox, 'warning') as warning:
                    QMessageBox.warning(None, 'Expected', 'handled')
                    warning.assert_called_once()

        result = unittest.TestResult()
        Probe().run(result)
        self.assertTrue(result.wasSuccessful(), result.failures or result.errors)
