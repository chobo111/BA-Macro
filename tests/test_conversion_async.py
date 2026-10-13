import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QTimer, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from shiboken6 import isValid

from conversion import plan_conversion
from conversion_dialog import ConversionDialog
from domain import Profile, Row


class ConversionAsyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialogs = []
        self.releases = []

    def tearDown(self):
        for release in self.releases:
            release.set()
        for dialog, jobs in self.dialogs:
            jobs.close()
            jobs.executor.shutdown(wait=True, cancel_futures=True)
            if isValid(dialog):
                dialog.reject()
                dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def dialog(self, callback=None):
        profile = Profile([Row(x=10, y=20, script='click 10,20')], (100,100))
        callback = Mock(return_value=False) if callback is None else callback
        dialog = ConversionDialog(profile, 'temporary.json', (200,200), callback)
        self.dialogs.append((dialog, dialog.jobs))
        dialog.show()
        return dialog

    def settle(self, dialog):
        for _ in range(300):
            if dialog.future is None:
                return
            QTest.qWait(10)
        self.fail('Conversion did not finish')

    def slow_conversion(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        calls = []
        def run(profile, source, destination, **kwargs):
            calls.append((threading.get_ident(), profile.rows[0].x, destination))
            entered.set()
            release.wait(2)
            return plan_conversion(profile, source, destination, **kwargs)
        return run, entered, release, calls

    def test_slow_conversion_keeps_ui_responsive_and_uses_a_snapshot(self):
        run, entered, release, calls = self.slow_conversion()
        with patch('conversion_jobs.plan_conversion', side_effect=run):
            dialog = self.dialog()
            self.assertTrue(entered.wait(1))
            self.assertFalse(dialog.copy_button.isEnabled())
            self.assertFalse(dialog.apply_button.isEnabled())
            self.assertIn('변환', dialog.summary.text())
            dialog.profile.rows[0].x = 99
            ticks = []
            timer = QTimer()
            timer.setInterval(5)
            timer.timeout.connect(lambda: ticks.append(True))
            timer.start()
            try:
                QTest.qWait(70)
            finally:
                timer.stop()
            self.assertGreater(len(ticks), 2)
            self.assertNotEqual(calls[0][0], threading.get_ident())
            release.set()
            self.settle(dialog)
            self.assertEqual(dialog.plan.profile.rows[0].x, 20)
            self.assertTrue(dialog.apply_button.isEnabled())
            self.assertEqual(dialog.profile.rows[0].x, 99)

    def test_only_latest_size_result_is_shown(self):
        run, entered, release, calls = self.slow_conversion()
        with patch('conversion_jobs.plan_conversion', side_effect=run):
            dialog = self.dialog()
            self.assertTrue(entered.wait(1))
            old = dialog.future
            dialog.destination.set_value((300,300))
            dialog.refresh()
            dialog.destination.set_value((400,400))
            dialog.refresh()
            release.set()
            self.settle(dialog)
        self.assertTrue(old.done())
        self.assertEqual(dialog.plan.profile.resolution, (400,400))
        self.assertEqual(dialog.plan.profile.rows[0].x, 40)
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.save_callback.assert_not_called()

    def test_save_revalidates_snapshot_and_callback_runs_once_on_ui_thread(self):
        callbacks = []
        def save(profile, mode, dialog):
            callbacks.append((threading.get_ident(), profile.rows[0].x, mode))
            return True
        dialog = self.dialog(save)
        self.settle(dialog)
        dialog.profile.rows[0].x = 30
        dialog.save('apply')
        dialog.save('copy')
        self.settle(dialog)
        self.assertEqual(callbacks, [(threading.get_ident(), 60, 'apply')])
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(dialog.jobs.closed)

    def test_changing_size_discards_pending_save_intent(self):
        dialog = self.dialog()
        self.settle(dialog)
        run, entered, release, _ = self.slow_conversion()
        with patch('conversion_jobs.plan_conversion', side_effect=run):
            dialog.save('apply')
            self.assertTrue(entered.wait(1))
            dialog.destination.set_value((300,300))
            dialog.refresh()
            release.set()
            self.settle(dialog)
        dialog.save_callback.assert_not_called()
        self.assertEqual(dialog.plan.profile.resolution, (300,300))
        self.assertIsNone(dialog.pending_save)

    def test_invalid_size_cancels_old_results_and_disables_save(self):
        run, entered, release, _ = self.slow_conversion()
        with patch('conversion_jobs.plan_conversion', side_effect=run):
            dialog = self.dialog()
            self.assertTrue(entered.wait(1))
            dialog.destination.set_value((0,200))
            dialog.refresh()
            release.set()
            QTest.qWait(50)
        self.assertIsNone(dialog.plan)
        self.assertFalse(dialog.copy_button.isEnabled())
        self.assertIn('너비·높이', dialog.error.text())

    def test_worker_and_save_failures_allow_retry(self):
        callback = Mock(side_effect=OSError('disk denied'))
        with patch('conversion_jobs.plan_conversion', side_effect=ValueError('invalid source')):
            dialog = self.dialog(callback)
            self.settle(dialog)
        self.assertIn('invalid source', dialog.error.text())
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.save('apply')
        self.settle(dialog)
        self.assertIn('disk denied', dialog.error.text())
        self.assertTrue(dialog.apply_button.isEnabled())
        callback.side_effect = None
        callback.return_value = True
        dialog.save('apply')
        self.settle(dialog)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_close_while_saving_prevents_late_callback(self):
        dialog = self.dialog()
        self.settle(dialog)
        run, entered, release, _ = self.slow_conversion()
        with patch('conversion_jobs.plan_conversion', side_effect=run):
            dialog.save('apply')
            self.assertTrue(entered.wait(1))
            future = dialog.future
            dialog.reject()
            self.assertFalse(dialog.isVisible())
            self.assertTrue(dialog.jobs.closed)
            release.set()
            dialog.jobs.executor.shutdown(wait=True)
            QTest.qWait(50)
        self.assertTrue(future.done())
        dialog.save_callback.assert_not_called()
        self.assertFalse(dialog.poll.isActive())

    def test_destroying_dialog_cancels_worker_without_widget_callbacks(self):
        run, entered, release, _ = self.slow_conversion()
        with patch('conversion_jobs.plan_conversion', side_effect=run):
            dialog = self.dialog()
            self.assertTrue(entered.wait(1))
            jobs = dialog.jobs
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertFalse(isValid(dialog))
            self.assertTrue(jobs.closed)
            release.set()
            jobs.executor.shutdown(wait=True)
            self.qt.processEvents()

    def test_error_summary_keeps_total_blocks_saves_and_clears_on_retry(self):
        dialog = self.dialog()
        self.settle(dialog)
        dialog.profile.rows[0].script = 'click 10,20\n' * 5
        dialog.source.set_value((5,5))
        dialog.refresh()
        self.settle(dialog)
        self.assertEqual(dialog.plan.error_count, 6)
        self.assertEqual(len(dialog.plan.errors), 3)
        message = dialog.error.text()
        self.assertIn('오류 6개:', message)
        self.assertIn('외 3개', message)
        self.assertEqual(message.count('범위 밖입니다.'), 3)
        for mode in ('copy', 'apply'):
            dialog.save(mode)
            self.settle(dialog)
            self.assertFalse(dialog.copy_button.isEnabled())
            self.assertFalse(dialog.apply_button.isEnabled())
        dialog.save_callback.assert_not_called()
        dialog.source.set_value((100,100))
        dialog.refresh()
        self.settle(dialog)
        self.assertEqual(dialog.plan.error_count, 0)
        self.assertEqual(dialog.plan.errors, [])
        self.assertEqual(dialog.error.text(), '')
        self.assertTrue(dialog.copy_button.isEnabled())
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.save('apply')
        self.settle(dialog)
        dialog.save_callback.assert_called_once()
