import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from PySide6.QtCore import QItemSelectionModel, QEvent, QCoreApplication, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu
from app import MainWindow
from domain import Row, Profile, read_json
from error_log import ErrorLog, ErrorLogDialog


class TestMenu(QMenu):
    handler = None

    def exec(self, point):
        return type(self).handler(self, point)


class RowMenuLogTests(unittest.TestCase):
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
        self.w.error_log.clear()

    def tearDown(self):
        self.w.finish_script_editor()
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        self.qt.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def select(self, *rows):
        self.w.table.clearSelection()
        for row in rows:
            self.w.table.selectionModel().select(self.w.table.model().index(row, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

    def menu(self, row, trigger=None):
        actions = []
        def execute(menu, point):
            actions.extend(a.text() for a in menu.actions() if not a.isSeparator())
            if trigger:
                next(a for a in menu.actions() if a.text() == trigger).trigger()
        with patch('app.QMenu', TestMenu), patch.object(TestMenu, 'handler', execute):
            self.w.row_context_menu(self.w.table.visualRect(self.w.table.model().index(row, 4)).center())
        return actions

    def test_menu_preserves_multi_selection_and_targets_unselected_row(self):
        self.select(0, 2)
        self.assertEqual(self.menu(2), ['2개 행 단축키 해제', '2개 행 복제', '2개 행 삭제'])
        self.assertEqual(self.w.selected_row_indexes(), [0, 2])
        self.assertEqual(self.menu(1), ['단축키 변경…', '단축키 해제', '복제', '위에 행 추가', '아래에 행 추가', '삭제'])
        self.assertEqual(self.w.selected_row_indexes(), [1])

    def test_script_edit_menu_and_empty_area(self):
        self.w.profile.rows[1].action_mode = 'script'
        self.w.rebuild()
        with patch.object(self.w, 'edit_script') as edit:
            self.assertIn('스크립트 편집', self.menu(1, '스크립트 편집'))
            edit.assert_called_once_with(self.w.profile.rows[1].id)
        calls = []
        with patch('app.QMenu', TestMenu), patch.object(TestMenu, 'handler', lambda *args: calls.append(args)):
            self.w.row_context_menu(self.w.table.viewport().rect().bottomRight())
            self.assertEqual(calls, [])

    def test_cell_controls_forward_context_menu(self):
        controls = [self.w.table.cellWidget(1, column) for column in range(4)]
        controls.append(controls[1].lineEdit())
        for control in controls:
            self.select(0)
            calls = []
            with patch('app.QMenu', TestMenu), patch.object(TestMenu, 'handler', lambda *args: calls.append(args)):
                point = control.rect().center()
                event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, control.mapToGlobal(point))
                QApplication.sendEvent(control, event)
                self.assertEqual(len(calls), 1)
                self.assertEqual(self.w.selected_row_indexes(), [1])

    def test_duplicate_copies_contents_new_ids_blank_keys_and_saves_in_order(self):
        w = self.w
        w.profile.resolution = (1920, 1080)
        w.profile.target_program = {'exe': 'Game.exe', 'class': 'Game', 'title': 'Game'}
        original = list(w.profile.rows)
        original[0].script = 'sleep 5'
        original[0].memo = 'memo'
        original[2].x = 234
        self.select(2, 0)
        self.menu(0, '2개 행 복제')
        w.flush_pending_saves()
        self.assertEqual(w.profile.rows[:3], original)
        for source, copy in zip((original[0], original[2]), w.profile.rows[3:]):
            self.assertNotEqual(source.id, copy.id)
            self.assertEqual(copy, replace(source, id=copy.id, move_hk=''))
        self.assertEqual(w.selected_row_indexes(), [3, 4])
        self.assertEqual(w.table.currentRow(), 3)
        self.assertEqual(Profile.read(read_json(w.path)).export(), w.profile.export())
        self.assertEqual(w.profile.resolution, (1920, 1080))
        self.assertEqual(w.profile.target_program['exe'], 'Game.exe')

    def test_duplicate_limit_rejects_entire_batch(self):
        w = self.w
        w.profile.rows.extend(Row() for _ in range(196))
        w.rebuild()
        before = w.profile.export()
        self.select(0, 2)
        w.duplicate_rows()
        self.assertEqual(w.profile.export(), before)
        self.assertIn('200행', w.statusBar().currentMessage())

    def test_clear_keys_rebinds_and_delete_preserves_previous_row_focus(self):
        self.select(0, 2)
        self.menu(0, '2개 행 단축키 해제')
        self.w.flush_pending_saves()
        self.assertEqual([r.move_hk for r in self.w.profile.rows], ['', 'w', ''])
        self.assertNotIn('q', [binding.key for binding in self.w.hooks.bindings])
        self.menu(2, '삭제')
        self.assertEqual(self.w.table.currentRow(), 1)
        self.select(0, 1)
        self.menu(1, '2개 행 삭제')
        self.assertEqual(len(self.w.profile.rows), 2)

    def test_insert_above_and_below(self):
        original = self.w.profile.rows[1]
        self.menu(1, '위에 행 추가')
        self.assertIs(self.w.profile.rows[2], original)
        self.menu(2, '아래에 행 추가')
        self.assertEqual(self.w.table.currentRow(), 3)
        self.assertEqual((self.w.profile.rows[3].move_hk, self.w.profile.rows[3].memo), ('', ''))

    def test_duplicate_save_failure_keeps_changes_and_logs_error(self):
        from persistence import SaveResult
        original = self.w.path.read_bytes()
        with patch.object(self.w.store, '_save', return_value=SaveResult(False, error='disk full')):
            self.w.duplicate_rows()
            self.w.flush_pending_saves()
        self.assertTrue(self.w.dirty)
        self.assertEqual(len(self.w.profile.rows), 4)
        self.assertEqual(self.w.path.read_bytes(), original)
        self.assertIn('disk full', self.w.error_log.text())

    def test_error_history_survives_status_clear_and_excludes_normal_messages(self):
        self.w.statusBar().showMessage('normal')
        self.w.statusBar().show_save_result('saved')
        self.assertFalse(self.w.error_log.entries)
        self.w.on_engine_error('2행: bad input')
        self.w.statusBar().clearMessage()
        self.assertIn('2행: bad input', self.w.error_log.text())
        self.assertIn(self.w.path.name, self.w.error_log.text())
        with patch('app.QMessageBox.warning'):
            self.w.error('open failed')
        self.assertEqual(len(self.w.error_log.entries), 2)

    def test_log_dialog_live_updates_bounded_history_and_clear(self):
        log = ErrorLog(limit=2)
        dialog = ErrorLogDialog(log, self.w)
        try:
            for text in ('old', '<b>plain</b>', 'last'):
                log.record('test', text, 'macro.json')
            self.assertEqual(len(log.entries), 2)
            self.assertNotIn('old', dialog.viewer.toPlainText())
            self.assertIn('<b>plain</b>', dialog.viewer.toPlainText())
            log.record('test', 'x' * 10000)
            self.assertLess(len(log.entries[-1][-1]), 4100)
            dialog.clear_button.click()
            self.assertEqual(dialog.viewer.toPlainText(), '')
            self.assertFalse(dialog.copy_button.isEnabled())
        finally:
            dialog.deleteLater()

    def test_error_log_background_tracks_light_and_dark_theme(self):
        from PySide6.QtGui import QColor
        dialog = ErrorLogDialog(self.w.error_log, self.w)
        try:
            dialog.show()
            for theme in ('Light', 'Dark'):
                with self.subTest(theme=theme):
                    self.w.settings.theme = theme
                    self.w.apply_theme()
                    self.qt.processEvents()
                    image = dialog.viewer.viewport().grab().toImage()
                    background = image.pixelColor(image.width() - 4, image.height() - 4)
                    self.assertEqual(background.name(), QColor(self.w.theme_colors.code_background).name())
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_right_click_target_ignores_held_selection_modifiers(self):
        from PySide6.QtTest import QTest
        for modifier in (Qt.Key.Key_Control, Qt.Key.Key_Shift):
            with self.subTest(modifier=modifier):
                self.select(0, 2)
                QTest.keyPress(self.w.table, modifier)
                try:
                    self.assertIn('복제', self.menu(1))
                    self.assertEqual(self.w.selected_row_indexes(), [1])
                finally:
                    QTest.keyRelease(self.w.table, modifier)

    def test_script_validation_failure_is_recorded_with_line(self):
        self.w.edit_script(self.w.profile.rows[0].id)
        self.w.script_editor.editor.setPlainText('sleep 5\nbad_command')
        self.w.script_editor.save()
        self.assertIn('2행:', self.w.error_log.text())
        self.assertIn('스크립트 편집', self.w.error_log.text())
