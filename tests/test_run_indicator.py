import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor
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

    def test_click_stops_without_changing_shortcut_or_geometry_or_logging(self):
        w, b = self.w, self.w.pause_button
        text, geometry = b.text(), b.geometry()
        stopped_icon = b.icon().cacheKey()
        w.statusBar().clearMessage()
        w.armed = True
        w.update_labels()
        self.assertTrue(b.running)
        self.assertEqual(b.toolTip(), '작동 중')
        self.assertNotEqual(stopped_icon, b.icon().cacheKey())
        b.click()
        self.assertFalse(w.armed)
        self.assertFalse(b.running)
        self.assertEqual(b.toolTip(), '정지 상태')
        self.assertEqual(stopped_icon, b.icon().cacheKey())
        self.assertEqual(b.text(), text)
        self.assertEqual(b.geometry(), geometry)
        self.assertEqual(w.statusBar().currentMessage(), '')

    def test_focus_and_minimization_keep_running_indicator(self):
        w = self.w
        w.target = 99
        w.armed = True
        w.update_labels()
        with patch.object(w.native, 'exists', return_value=True), \
             patch.object(w.native, 'size', return_value=(1280, 720)) as size, \
             patch.object(w.native, 'cursor', return_value=(10, 20)), \
             patch.object(w.native, 'active', side_effect=[True, False]):
            for _ in range(2):
                w.tick()
                self.assertTrue(w.pause_button.running)
                self.assertEqual(w.pause_button.toolTip(), '작동 중')
            size.side_effect = WindowSizeUnavailable()
            w.tick()
            self.assertTrue(w.pause_button.running)
        with patch.object(w.native, 'exists', return_value=False):
            w.tick()
        self.assertFalse(w.pause_button.running)

    def test_light_dark_dot_colors_render_and_theme_changes_preserve_state(self):
        w, b = self.w, self.w.pause_button
        for name, colors in (('Light', LIGHT), ('Dark', DARK)):
            for armed in (False, True):
                w.armed = armed
                w.update_labels()
                w.settings.theme = name
                w.apply_theme()
                self.qt.processEvents()
                picture = b.grab().toImage()
                scale = picture.devicePixelRatio()
                pixel = picture.pixelColor(int((b.width() - 11.5) * scale), int(b.height() / 2 * scale))
                self.assertEqual(pixel, QColor(colors.status_running if armed else colors.status_stopped))
                self.assertEqual(b.running, armed)
                self.assertEqual(b.accessibleDescription(), b.toolTip())
