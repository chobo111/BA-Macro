"""Memo sizing measures changed text while retaining edit/resize behavior."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from app import MainWindow
from domain import Profile, Row, read_json


class MemoWidthCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.w = MainWindow(data_dir=self.folder.name, hook_enabled=False)
        self.w.timer.stop()
        self.w.show()
        self.settle()

    def tearDown(self):
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.folder.cleanup()

    def settle(self):
        self.qt.processEvents()
        self.qt.processEvents()

    def measured_notes(self, metrics, notes):
        return [call.args[0] for call in metrics.horizontalAdvance.call_args_list
                if call.args[0] in notes]

    def test_one_edited_note_is_remeasured_and_autosaved(self):
        notes = [f'row {index} memo' for index in range(200)]
        w = self.w
        w.profile = Profile([Row(memo=note) for note in notes])
        metrics = Mock(wraps=w.table.fontMetrics())
        with patch.object(w.table, 'fontMetrics', return_value=metrics):
            w.rebuild()
            self.settle()
            self.assertCountEqual(self.measured_notes(metrics, notes), notes)
            metrics.reset_mock()
            replacement = '수정한 메모'
            w.table.item(100, 4).setText(replacement)
            self.settle()
            w.flush_pending_saves()
            self.assertEqual(self.measured_notes(metrics, notes + [replacement]), [replacement])
        self.assertEqual(read_json(w.path)['macros'][100]['memo'], replacement)

    def test_shortening_and_removing_widest_note_recomputes_maximum(self):
        w = self.w
        w.profile = Profile([Row(memo='A' * 100), Row(memo='B' * 80), Row(memo='C')])
        w.rebuild()
        self.settle()
        widest = w.table.columnWidth(4)
        w.table.item(0, 4).setText('A')
        self.settle()
        shortened = w.table.columnWidth(4)
        self.assertLess(shortened, widest)
        deleted = w.profile.rows[1].id
        w.table.selectRow(1)
        w.delete_rows()
        self.settle()
        self.assertLess(w.table.columnWidth(4), shortened)
        self.assertNotIn(deleted, w._memo_widths)
        self.assertEqual(set(w._memo_widths), {row.id for row in w.profile.rows})

    def test_profile_font_dpi_and_theme_changes_invalidate_measurements(self):
        w = self.w
        notes = ['first memo', 'second memo']
        w.profile = Profile([Row(memo=note) for note in notes])
        w.rebuild()
        self.settle()
        metrics = Mock(wraps=w.table.fontMetrics())
        with patch.object(w.table, 'fontMetrics', return_value=metrics):
            # Even another profile with equal row IDs must refresh its sizing.
            w.profile = deepcopy(w.profile)
            w.rebuild()
            self.settle()
            self.assertCountEqual(self.measured_notes(metrics, notes), notes)
            for kind in (QEvent.Type.FontChange, QEvent.Type.DevicePixelRatioChange):
                with self.subTest(event=kind):
                    metrics.reset_mock()
                    QApplication.sendEvent(w.table, QEvent(kind))
                    self.settle()
                    self.assertCountEqual(self.measured_notes(metrics, notes), notes)
            metrics.reset_mock()
            w.settings.theme = 'Light'
            w.apply_theme()
            self.settle()
            self.assertCountEqual(self.measured_notes(metrics, notes), notes)
