import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QPersistentModelIndex, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton

from app import MainWindow
from domain import Profile, Row, read_json
from widgets import NoWheelSpinBox


class TableSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle('Fusion')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.window = MainWindow(data_dir=self.directory.name, hook_enabled=False)
        self.window.timer.stop()
        self.window.show()
        self.qt.processEvents()

    def tearDown(self):
        self.window.shutdown()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def controls(self, index):
        t = self.window.table
        return [t.cellWidget(index, col) for col in range(4)] + [t.item(index, 4)]

    def test_mode_switch_preserves_other_rows_and_own_key_mode_memo(self):
        w, t = self.window, self.window.table
        row = w.profile.rows[1]
        row.x, row.y = 123, 456
        w.rebuild()
        unchanged, previous = self.controls(0), self.controls(1)
        t.cellWidget(1, 3).setCurrentIndex(2)
        self.qt.processEvents()
        self.assertEqual(self.controls(0), unchanged)
        for col in (0, 3, 4):
            self.assertIs(self.controls(1)[col], previous[col])
        self.assertEqual(t.columnSpan(1, 1), 2)
        self.assertIsInstance(t.cellWidget(1, 1), QPushButton)
        self.assertIsNone(t.cellWidget(1, 2))
        t.cellWidget(1, 3).setCurrentIndex(0)
        self.qt.processEvents()
        self.assertEqual(t.columnSpan(1, 1), 1)
        self.assertIsInstance(t.cellWidget(1, 1), NoWheelSpinBox)
        self.assertEqual([t.cellWidget(1, col).value() for col in (1, 2)], [123, 456])
        self.assertEqual(self.controls(0), unchanged)
        w.flush_pending_saves()
        self.assertEqual(read_json(w.path)['macros'][1]['action_mode'], row.action_mode)

    def test_same_ids_new_profile_updates_values_without_saving(self):
        w, t = self.window, self.window.table
        previous = self.controls(0)
        w.profile = deepcopy(w.profile)
        row = w.profile.rows[0]
        row.move_hk, row.memo, row.x, row.y = 'shift+&', 'changed memo', 42, 99
        with patch.object(w, 'changed') as changed:
            w.rebuild(row.id)
        changed.assert_not_called()
        self.assertEqual(self.controls(0), previous)
        self.assertEqual(t.cellWidget(0, 0).text(), 'SHIFT+&&')
        self.assertEqual(t.item(0, 4).text(), 'changed memo')
        self.assertEqual(t.item(0, 4).toolTip(), 'changed memo')
        self.assertEqual([t.cellWidget(0, col).value() for col in (1, 2)], [42, 99])

    def test_reorder_both_directions_keeps_widgets_spans_and_callbacks(self):
        w, t = self.window, self.window.table
        w.profile.rows[0].action_mode = 'script'
        w.profile.rows[0].script = 'sleep 5'
        w.rebuild()
        rows = list(w.profile.rows)
        original = {row.id: self.controls(i) for i, row in enumerate(rows)}
        persistent = QPersistentModelIndex(t.model().index(0, 4))
        for origin, destination in ((0, 2), (2, 1), (1, 0)):
            w.move_row(origin, destination)
            self.qt.processEvents()
            self.assertEqual(persistent.row(), destination)
            for i, row in enumerate(w.profile.rows):
                self.assertEqual(self.controls(i), original[row.id])
                self.assertEqual(t.columnSpan(i, 1), 2 if row.action_mode == 'script' else 1)
                with patch.object(w, 'edit_key') as edit:
                    t.cellWidget(i, 0).click()
                edit.assert_called_once_with(row.id)
        with patch.object(w, 'edit_script') as edit:
            t.cellWidget(0, 1).click()
        edit.assert_called_once_with(rows[0].id)
        w.move_row(0, 2)
        t.cellWidget(0, 3).setCurrentIndex(1)
        self.qt.processEvents()
        self.assertEqual(rows[1].action_mode, 'move_only')
        self.assertEqual(rows[0].action_mode, 'script')
        w.flush_pending_saves()
        self.assertEqual([row['id'] for row in read_json(w.path)['macros']], [row.id for row in w.profile.rows])

    def test_delete_preserves_survivors_and_selects_previous_row(self):
        from PySide6.QtCore import QItemSelectionModel
        w, t = self.window, self.window.table
        w.profile = Profile([Row(action_mode='script', script='sleep 5') for _ in range(5)])
        w.rebuild()
        rows = list(w.profile.rows)
        original = {row.id: self.controls(i) for i, row in enumerate(rows)}
        t.clearSelection()
        for i in (2, 4):
            t.selectionModel().select(t.model().index(i, 0),
                                     QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        w.delete_rows()
        self.qt.processEvents()
        self.assertEqual(w.profile.rows, [rows[i] for i in (0, 1, 3)])
        self.assertEqual([index.row() for index in t.selectionModel().selectedRows()], [1])
        for i, row in enumerate(w.profile.rows):
            self.assertEqual(self.controls(i), original[row.id])
            self.assertEqual(t.columnSpan(i, 1), 2)
        w.flush_pending_saves()
        self.assertEqual(len(read_json(w.path)['macros']), 3)

    def test_add_preserves_existing_controls_and_binds_new_row(self):
        w, t = self.window, self.window.table
        previous = [self.controls(i) for i in range(3)]
        w.add_row()
        self.assertEqual([self.controls(i) for i in range(3)], previous)
        self.assertEqual(t.rowCount(), 4)
        with patch.object(w, 'edit_key') as edit:
            t.cellWidget(3, 0).click()
        edit.assert_called_once_with(w.profile.rows[3].id)

    def test_queued_mode_refresh_after_profile_replacement_uses_new_data(self):
        w, t = self.window, self.window.table
        t.cellWidget(0, 3).setCurrentIndex(2)
        w.profile = Profile([Row(x=12, y=34, memo='replacement')])
        w.rebuild()
        controls = self.controls(0)
        with patch.object(w, 'changed') as changed:
            self.qt.processEvents()
        changed.assert_not_called()
        self.assertEqual(self.controls(0), controls)
        self.assertEqual(t.rowCount(), 1)
        self.assertEqual(t.item(0, 4).data(Qt.ItemDataRole.UserRole), w.profile.rows[0].id)
        self.assertEqual(t.cellWidget(0, 1).value(), 12)
        self.assertEqual(t.columnSpan(0, 1), 1)

    def test_idle_edit_renders_pending_and_saved_states_without_skipping_stop(self):
        w = self.window
        with patch.object(w, 'update_labels', wraps=w.update_labels) as labels, \
                patch.object(w.engine, 'stop', wraps=w.engine.stop) as stop:
            w.live_memo_changed(0, 'new memo')
            self.assertEqual(labels.call_count, 1)
            self.assertIn('●', w.windowTitle())
            stop.assert_called_once()
            w.flush_pending_saves()
            self.assertEqual(labels.call_count, 2)
        self.assertNotIn('●', w.windowTitle())
        self.assertEqual(read_json(w.path)['macros'][0]['memo'], 'new memo')

    def test_profile_refresh_updates_window_title(self):
        w = self.window
        w.path = Path(self.directory.name) / 'different.json'
        w.rebuild()
        self.assertIn('different', w.windowTitle())

    def test_unrelated_refresh_preserves_incomplete_numeric_input(self):
        w, t = self.window, self.window.table
        spin = t.cellWidget(0, 1)
        spin.lineEdit().setText('-')
        t.cellWidget(1, 3).setCurrentIndex(2)
        self.qt.processEvents()
        self.assertIs(t.cellWidget(0, 1), spin)
        self.assertEqual(spin.lineEdit().text(), '-')

    def test_refresh_selects_row_even_while_control_is_held(self):
        from PySide6.QtTest import QTest
        w, t = self.window, self.window.table
        rid = w.profile.rows[1].id
        w.rebuild(rid)
        QTest.keyPress(t, Qt.Key.Key_Control)
        try:
            w.rebuild(rid)
            self.assertEqual([index.row() for index in t.selectionModel().selectedRows()], [1])
            self.assertEqual(t.currentRow(), 1)
        finally:
            QTest.keyRelease(t, Qt.Key.Key_Control)

    def test_delete_selects_previous_script_row_while_control_is_held(self):
        from PySide6.QtTest import QTest
        QTest.keyPress(self.window.table, Qt.Key.Key_Control)
        try:
            self.test_delete_preserves_survivors_and_selects_previous_row()
        finally:
            QTest.keyRelease(self.window.table, Qt.Key.Key_Control)

    def test_failed_save_as_restores_title_and_autosave_path(self):
        from app import QFileDialog
        from persistence import SaveResult
        w = self.window
        original = w.path
        destination = original.with_name('failed-copy.json')
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(destination), '')), \
                patch.object(w.store, '_save', return_value=SaveResult(False, error='denied')):
            self.assertFalse(w.save_as())
        self.assertEqual(w.path, original)
        self.assertIn('● ' + original.stem + ' —', w.windowTitle())
        self.assertNotIn(destination.stem, w.windowTitle())
        self.assertIn('denied', w.statusBar().currentMessage())
        w.live_memo_changed(0, 'after failed save-as')
        w.flush_pending_saves()
        self.assertEqual(read_json(original)['macros'][0]['memo'], 'after failed save-as')
        self.assertFalse(destination.exists())
