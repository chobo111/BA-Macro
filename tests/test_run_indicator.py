import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
from app import MainWindow
from domain import WindowSizeUnavailable
from theme import DARK, LIGHT


class RunIndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle('Fusion')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.w = MainWindow(data_dir=self.directory.name, hook_enabled=False)
        self.w.timer.stop()
        self.w.show()
        self.qt.processEvents()

    def tearDown(self):
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        self.qt.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def test_start_stop_updates_status_without_logs_or_changing_shortcut(self):
        w = self.w
        b = w.pause_button
        original_text = b.text()
        original_icon = b.icon().cacheKey()
        original_geometry = b.geometry()
        w.statusBar().clearMessage()
        with patch.object(w.native, 'active', return_value=True):
            w.armed = True
            w.update_labels()
        self.assertEqual(b.state, 'active')
        self.assertEqual(b.toolTip(), '활성화됨')
        self.assertIn('정지', b.accessibleName())
        self.assertNotEqual(b.icon().cacheKey(), original_icon)
        w.stop()
        self.assertEqual(b.state, 'stopped')
        self.assertEqual(b.toolTip(), '정지됨')
        self.assertIn('시작', b.accessibleName())
        self.assertEqual(b.text(), original_text)
        self.assertEqual(b.geometry(), original_geometry)
        self.assertEqual(w.statusBar().currentMessage(), '')

    def test_focus_changes_only_status_and_reuses_existing_poll(self):
        w = self.w
        w.target = 99
        w.armed = True
        with patch.object(w.native, 'exists', return_value=True), \
             patch.object(w.native, 'size', return_value=(1280, 720)), \
             patch.object(w.native, 'cursor', return_value=(10, 20)), \
             patch.object(w.native, 'active', side_effect=[False, True, False]) as active:
            for state in ('waiting', 'active', 'waiting'):
                w.tick()
                self.assertEqual(w.pause_button.state, state)
                self.assertTrue(w.armed)
            self.assertEqual(active.call_count, 3)
        self.assertEqual(w.pause_button.toolTip(), '대기 중 — 앱으로 전환하면 작동')

    def test_minimized_target_waits_and_closed_target_stops(self):
        w = self.w
        w.target = 99
        w.armed = True
        w.pause_button.set_state(True, True)
        with patch.object(w.native, 'exists', return_value=True), \
             patch.object(w.native, 'size', side_effect=WindowSizeUnavailable()):
            w.tick()
        self.assertTrue(w.armed)
        self.assertEqual(w.pause_button.state, 'waiting')
        with patch.object(w.native, 'exists', return_value=False):
            w.tick()
        self.assertFalse(w.armed)
        self.assertEqual(w.pause_button.state, 'stopped')

    def test_animation_does_not_restart_on_poll_or_focus_change(self):
        b = self.w.pause_button
        spy = QSignalSpy(b.animation.stateChanged)
        b.set_state(True, True)
        b.animation.setCurrentTime(70)
        changes = spy.count()
        for _ in range(10):
            b.set_state(True, True)
            b.set_state(True, False)
        self.assertEqual(spy.count(), changes)
        self.assertEqual(b.animation.currentTime(), 70)
        b.animation.setCurrentTime(b.animation.duration())
        self.assertEqual(b.background, QColor(b.colors.run_background))
        b.set_state(False, False)
        b.animation.setCurrentTime(b.animation.duration())
        self.assertEqual(b.background, QColor(b.colors.panel))

    def test_theme_refresh_keeps_status_and_uses_theme_colors(self):
        b = self.w.pause_button
        for name, colors in (('Dark', DARK), ('Light', LIGHT)):
            for armed, focused, state in ((False, False, 'stopped'), (True, True, 'active'), (True, False, 'waiting')):
                b.set_state(armed, focused)
                self.w.settings.theme = name
                self.w.apply_theme()
                self.assertEqual(b.state, state)
                self.assertEqual(b.background, QColor(colors.run_background if armed else colors.panel))
                self.assertEqual(b.accessibleDescription(), b.toolTip())
                self.assertFalse(b.grab().isNull())

    def test_waiting_button_remains_clickable_to_stop(self):
        w = self.w
        w.armed = True
        w.pause_button.set_state(True, False)
        w.pause_button.click()
        self.assertFalse(w.armed)
        self.assertEqual(w.pause_button.state, 'stopped')
