import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt, QCoreApplication, QEvent, QPointF
from PySide6.QtGui import QContextMenuEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit

from script_completion import ScriptTextEdit, completion_context, COMMANDS
from script_help import COMMAND_HELP


class CompletionContextTests(unittest.TestCase):
    def words(self, text, explicit=False):
        result = completion_context(text, len(text), explicit)
        return [word for word, _ in result[2]] if result else []

    def test_commands_indentation_case_and_explicit_empty(self):
        self.assertEqual(self.words('  CU'), ['curve', 'curve_first_time', 'curve_last_time', 'curve_release'])
        self.assertEqual(self.words('  '), [])
        self.assertEqual(len(self.words('  ', True)), len(COMMANDS))
        self.assertEqual({x.split()[0] for x, _ in COMMAND_HELP}, {x for x, _ in COMMANDS})

    def test_arguments_never_offer_completion_even_with_shortcut(self):
        for text in ('curve ', 'curve 10,20 m', 'click mouse ', 'LOOP U',
                     'mouse_press ', 'curve_last_time un', 'move mouse ',
                     'press 1,2 ', 'loop 2 ', 'curve_first_time ', 'sleep '):
            for explicit in (False, True):
                with self.subTest(text=text, explicit=explicit):
                    self.assertEqual(self.words(text, explicit), [])

    def test_no_comments_numbers_or_non_keyword_tokens(self):
        for text in ('// cu', 'click mouse // cu', 'loop 3', 'curve 1,2', 'move -1,', 'curve 🐱'):
            for explicit in (False, True):
                self.assertEqual(self.words(text, explicit), [])

    def test_cursor_at_start_of_number_does_not_offer_replacement(self):
        self.assertIsNone(completion_context('curve 100,200', 6, True))
        self.assertIsNone(completion_context('loop 25', 5, True))

    def test_middle_token_span_preserves_arguments_and_comments(self):
        result = completion_context('  curve_last_time 5 // 설명', 5)
        self.assertEqual(result[:2], (2, 17))
        self.assertEqual(result[2][0][0], 'curve')


class CompletionEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        layout = QVBoxLayout(self.window)
        self.guard = Mock(return_value=True)
        self.editor = ScriptTextEdit('', self.guard)
        layout.addWidget(self.editor)
        self.other = QLineEdit()
        layout.addWidget(self.other)
        self.window.resize(540, 340)
        self.window.show()
        self.window.activateWindow()
        self.editor.setFocus()
        self.qt.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def set_text(self, text, position=None):
        self.editor.setPlainText(text)
        cursor = self.editor.textCursor()
        cursor.setPosition(len(text) if position is None else position)
        self.editor.setTextCursor(cursor)

    def popup(self):
        QTest.keyClick(self.editor, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
        self.qt.processEvents()
        return self.editor.completions

    def test_typing_navigation_tab_and_undo(self):
        QTest.keyClicks(self.editor, 'cu')
        self.assertTrue(self.editor.completions.isVisible())
        QTest.keyClick(self.editor, Qt.Key.Key_Down)
        QTest.keyClick(self.editor, Qt.Key.Key_Tab)
        self.assertEqual(self.editor.toPlainText(), 'curve_first_time ')
        self.assertFalse(self.editor.completions.isVisible())
        self.editor.undo()
        self.assertEqual(self.editor.toPlainText(), 'cu')
        self.guard.assert_not_called()

    def test_enter_is_newline_escape_only_dismisses_and_shortcut_reopens(self):
        QTest.keyClicks(self.editor, 'cu')
        QTest.keyClick(self.editor, Qt.Key.Key_Escape)
        self.assertFalse(self.editor.completions.isVisible())
        self.assertEqual(self.editor.toPlainText(), 'cu')
        self.assertTrue(self.popup().isVisible())
        QTest.keyClick(self.editor, Qt.Key.Key_Return)
        self.assertEqual(self.editor.toPlainText(), 'cu\n')
        self.assertFalse(self.editor.completions.isVisible())

    def test_typing_arguments_does_not_open_or_insert_completion(self):
        for source, text in [('curve 10,20 ', 'mouse'), ('loop ', 'until_release'),
                             ('mouse_press ', 'left'), ('curve_last_time ', 'untilcmd')]:
            with self.subTest(source=source):
                self.set_text(source)
                for character in text:
                    QTest.keyClicks(self.editor, character)
                    self.assertFalse(self.editor.completions.isVisible())
                self.assertFalse(self.popup().isVisible())
                self.assertEqual(self.editor.toPlainText(), source + text)
        self.guard.assert_not_called()

    def test_completing_middle_of_token_keeps_existing_tail(self):
        self.set_text('  curve_last_time 5 // 설명', 5)
        self.popup()
        self.editor.completions.setCurrentRow(1)
        QTest.keyClick(self.editor, Qt.Key.Key_Tab)
        self.assertEqual(self.editor.toPlainText(), '  curve_first_time 5 // 설명')

    def test_command_completion_obeys_coordinate_guard(self):
        self.set_text('cl 10,20', 2)
        self.guard.return_value = False
        self.popup()
        QTest.keyClick(self.editor, Qt.Key.Key_Tab)
        self.assertEqual(self.editor.toPlainText(), 'cl 10,20')
        self.guard.assert_called_once()
        self.assertFalse(self.editor.document().isRedoAvailable())
        self.guard.return_value = True
        self.popup()
        QTest.keyClick(self.editor, Qt.Key.Key_Tab)
        self.assertEqual(self.editor.toPlainText(), 'click 10,20')

    def test_programmatic_insert_template_and_selection_dismiss(self):
        QTest.keyClicks(self.editor, 'cu')
        self.editor.insertPlainText('10,20')
        self.assertFalse(self.editor.completions.isVisible())
        self.set_text('cu')
        self.popup()
        self.editor.setPlainText('loop\nsleep 5\nloop_end')
        self.assertFalse(self.editor.completions.isVisible())
        self.editor.selectAll()
        self.assertFalse(self.popup().isVisible())

    def test_focus_change_does_not_grab_other_widget_or_create_popup_window(self):
        QTest.keyClicks(self.editor, 'cu')
        self.assertIsNone(QApplication.activePopupWidget())
        QTest.mouseClick(self.other, Qt.MouseButton.LeftButton)
        self.assertTrue(self.other.hasFocus())
        self.assertFalse(self.editor.completions.isVisible())

    def test_mouse_selection_inserts_command(self):
        QTest.keyClicks(self.editor, 'cu')
        popup = self.editor.completions
        item = popup.item(2)
        QTest.mouseClick(popup.viewport(), Qt.MouseButton.LeftButton,
                         pos=popup.visualItemRect(item).center())
        self.assertEqual(self.editor.toPlainText(), 'curve_last_time ')
        self.assertTrue(self.editor.hasFocus())

    def test_right_click_on_completion_requests_capture_once_without_accepting_command(self):
        QTest.keyClicks(self.editor, 'cu')
        popup = self.editor.completions
        point = popup.visualItemRect(popup.item(2)).center()
        global_point = QPointF(popup.viewport().mapToGlobal(point))
        position = self.editor.textCursor().position()
        capture = Mock()
        self.editor.coordinate_requested.connect(capture)
        QTest.mouseClick(popup.viewport(), Qt.MouseButton.RightButton, pos=point)
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, popup.viewport().mapToGlobal(point))
        QApplication.sendEvent(popup.viewport(), event)
        capture.assert_called_once_with(global_point)
        self.assertEqual(self.editor.toPlainText(), 'cu')
        self.assertEqual(self.editor.textCursor().position(), position)
        self.assertFalse(popup.isVisible())
        self.assertIsNone(QApplication.activePopupWidget())
        self.guard.assert_not_called()

    def test_list_stays_inside_editor_at_bottom_and_hides_on_resize(self):
        self.set_text('\n' * 40 + 'cu')
        self.editor.ensureCursorVisible()
        self.qt.processEvents()
        popup = self.popup()
        self.assertTrue(popup.isVisible())
        self.assertTrue(self.editor.viewport().rect().contains(popup.geometry()))
        self.window.resize(400, 250)
        self.qt.processEvents()
        self.assertFalse(popup.isVisible())

    def test_current_line_only_and_no_completion_in_comment(self):
        self.set_text('sleep 1\n' * 5000 + '// cu')
        self.assertFalse(self.popup().isVisible())
        self.set_text('sleep 1\n' * 5000 + 'cu')
        self.editor.ensureCursorVisible()
        self.qt.processEvents()
        with patch.object(self.editor, 'toPlainText', side_effect=AssertionError('whole document read')):
            self.editor.show_completions(explicit=True)
            self.assertTrue(self.editor.completions.isVisible())
