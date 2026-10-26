"""Row-add mode selection is separate from insertion and preserves table edits."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import MainWindow
from domain import Profile, Row, read_json


class AddRowModesTests(unittest.TestCase):
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

    def action(self, mode):
        return next(action for action in self.window.add_mode_menu.actions()
                    if action.data() == mode)

    def click_add(self):
        button = self.window.add_button
        QTest.mouseClick(button, Qt.MouseButton.LeftButton,
                         pos=QPoint(15, button.height() // 2))
        self.qt.processEvents()

    def test_initial_main_button_adds_cursor_row(self):
        w = self.window
        self.assertTrue(self.action('move_only').isChecked())
        self.assertFalse(self.action('script').isChecked())
        w.table.clearSelection()
        count = len(w.profile.rows)
        self.click_add()
        self.assertEqual(len(w.profile.rows), count + 1)
        self.assertEqual(w.profile.rows[-1].action_mode, 'move_only')
        self.assertIsNone(w.script_editor)

    def test_selecting_mode_only_changes_exclusive_selection_and_feedback(self):
        w = self.window
        before_profile, before_file = w.profile.export(), w.path.read_bytes()
        self.action('script').trigger()
        self.assertTrue(self.action('script').isChecked())
        self.assertFalse(self.action('move_only').isChecked())
        self.assertIn('스크립트', w.add_button.toolTip())
        self.assertIn('스크립트', w.add_button.accessibleName())
        self.assertEqual(w.profile.export(), before_profile)
        self.assertEqual(w.path.read_bytes(), before_file)
        self.assertIsNone(w.script_editor)
        self.action('move_only').trigger()
        self.assertTrue(self.action('move_only').isChecked())
        self.assertFalse(self.action('script').isChecked())
        self.assertIn('커서 이동', w.add_button.toolTip())

    def test_repeated_main_click_uses_selected_script_mode_and_autosaves(self):
        w = self.window
        w.table.clearSelection()
        self.action('script').trigger()
        count = len(w.profile.rows)
        with patch.object(w, 'edit_script') as editor:
            self.click_add()
            self.click_add()
        editor.assert_not_called()
        added = w.profile.rows[count:]
        self.assertEqual(len(added), 2)
        self.assertTrue(all(row.action_mode == 'script' for row in added))
        self.assertTrue(all((row.move_hk, row.memo, row.script) == ('', '', '') for row in added))
        self.assertTrue(self.action('script').isChecked())
        self.assertIsNone(w.script_editor)
        w.flush_pending_saves()
        saved = Profile.read(read_json(w.path))
        self.assertEqual(saved.export(), w.profile.export())

    def test_mode_add_inserts_below_last_selected_row_and_selects_new_row(self):
        w = self.window
        original = list(w.profile.rows)
        w.table.clearSelection()
        for index in (0, 1):
            w.table.selectionModel().select(w.table.model().index(index, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.action('script').trigger()
        self.click_add()
        self.assertEqual(w.profile.rows[:2], original[:2])
        self.assertIs(w.profile.rows[3], original[2])
        self.assertEqual(w.profile.rows[2].action_mode, 'script')
        self.assertEqual(w.table.currentRow(), 2)
        self.assertEqual(w.selected_row_indexes(), [2])

    def test_right_click_opens_mode_menu_without_adding_row(self):
        w = self.window
        button, menu = w.add_button, w.add_mode_menu
        count = len(w.profile.rows)
        visible = []
        timer = QTimer(w)
        timer.setSingleShot(True)

        def choose():
            visible.append(menu.isVisible())
            self.action('script').trigger()
            menu.close()

        timer.timeout.connect(choose)
        timer.start(30)
        try:
            point = button.rect().center()
            event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point,
                                      button.mapToGlobal(point))
            QApplication.sendEvent(button, event)
            QTest.qWait(60)
        finally:
            timer.stop()
            menu.close()
        self.assertEqual(visible, [True])
        self.assertEqual(len(w.profile.rows), count)
        self.assertTrue(self.action('script').isChecked())

    def test_row_limit_rejects_script_add_without_modifying_profile(self):
        w = self.window
        w.profile.rows.extend(Row() for _ in range(200 - len(w.profile.rows)))
        w.rebuild()
        self.action('script').trigger()
        before_profile, before_file = w.profile.export(), w.path.read_bytes()
        self.click_add()
        self.assertEqual(w.profile.export(), before_profile)
        self.assertEqual(w.path.read_bytes(), before_file)
        self.assertIn('200행', w.statusBar().currentMessage())

    def test_explicit_position_insert_uses_selected_mode(self):
        w = self.window
        self.action('script').trigger()
        w.insert_row(1)
        self.assertEqual(w.profile.rows[1].action_mode, 'script')
        self.assertTrue(self.action('script').isChecked())
        self.click_add()
        self.assertEqual(w.profile.rows[2].action_mode, 'script')
        self.action('move_only').trigger()
        w.insert_row(1)
        self.assertEqual(w.profile.rows[1].action_mode, 'move_only')
        self.assertTrue(self.action('move_only').isChecked())

    def test_add_button_fits_minimum_width_and_left_click_does_not_open_menu(self):
        w = self.window
        for theme in ('Light', 'Dark'):
            with self.subTest(theme=theme):
                w.settings.theme = theme
                w.apply_theme()
                w.resize(320, 260)
                self.qt.processEvents()
                self.assertEqual(w.width(), 320)
                buttons = (w.add_button, w.delete_button, w.pin_button, w.pause_button)
                bounds = [button.rect().translated(button.mapTo(w.toolbar_panel, QPoint(0, 0)))
                          for button in buttons]
                for left, right in zip(bounds, bounds[1:]):
                    self.assertLess(left.right(), right.left())
                self.assertGreaterEqual(bounds[0].left(), 0)
                count = len(w.profile.rows)
                self.click_add()
                self.assertEqual(len(w.profile.rows), count + 1)
                self.assertFalse(w.add_mode_menu.isVisible())
                self.assertLess(bounds[-1].right(), w.toolbar_panel.width())


if __name__ == '__main__':
    unittest.main()
