import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import random
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from coordinate_editor import CoordinateTextEdit
from script_language import coordinate_tokens


class CoordinateGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.guard = Mock(return_value=False)
        self.editor = CoordinateTextEdit('', self.guard)

    def tearDown(self):
        self.editor.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def select(self, text, start, end=None):
        self.editor.setPlainText(text)
        cursor = self.editor.textCursor()
        cursor.setPosition(len(text[:start].encode('utf-16-le')) // 2)
        cursor.setPosition(len(text[:start if end is None else end].encode('utf-16-le')) // 2,
                           QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)

    def test_navigation_does_not_read_document_or_scan_coordinates(self):
        source = 'click 10,20\n' * 8000
        self.select(source, len(source))
        with patch.object(self.editor, 'toPlainText', side_effect=AssertionError('full read')), \
                patch('coordinate_editor.coordinate_tokens') as scan:
            for key, modifier in ((Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier),
                                  (Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier),
                                  (Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)):
                QTest.keyClick(self.editor, key, modifier)
            scan.assert_not_called()
        self.guard.assert_not_called()

    def test_typing_checks_only_neighbouring_lines_and_still_rejects_coordinates(self):
        source = 'click 10,20\n' * 7999 + 'click 10,20'
        self.select(source, len(source))
        with patch.object(self.editor, 'toPlainText', side_effect=AssertionError('full read')), \
                patch('coordinate_editor.coordinate_tokens', wraps=coordinate_tokens) as scan:
            QTest.keyClick(self.editor, Qt.Key.Key_1)
            self.assertTrue(scan.called)
            self.assertTrue(all(len(call.args[0]) < 100 for call in scan.call_args_list))
        self.guard.assert_called_once()
        self.assertEqual(self.editor.toPlainText(), source)
        self.editor.redo()
        self.assertEqual(self.editor.toPlainText(), source)

    def test_comment_typing_does_not_read_full_document_or_require_approval(self):
        source = 'click 10,20\n' * 7999 + '// note '
        self.select(source, len(source))
        with patch.object(self.editor, 'toPlainText', side_effect=AssertionError('full read')):
            QTest.keyClicks(self.editor, '123')
        self.guard.assert_not_called()
        self.assertEqual(self.editor.toPlainText(), source + '123')

    def test_joining_lines_cannot_create_unapproved_coordinates(self):
        for source, position, key in (
                ('cl\nick 10,20', 3, Qt.Key.Key_Backspace),
                ('cl\nick 10,20', 2, Qt.Key.Key_Delete),
                ('// 😀\ncl\nick 10,20', 8, Qt.Key.Key_Backspace)):
            with self.subTest(source=source, key=key):
                self.guard.reset_mock()
                self.select(source, position)
                QTest.keyClick(self.editor, key)
                self.guard.assert_called_once()
                self.assertEqual(self.editor.toPlainText(), source)

    def test_removing_last_coordinates_and_removing_one_of_several_keep_existing_rules(self):
        for tail, guarded in (('', False), ('\nsleep 1\nsleep 2\nclick 30,40', True)):
            with self.subTest(tail=tail):
                source = 'click 10,20' + tail
                self.select(source, 0, 11)
                self.guard.reset_mock()
                QTest.keyClick(self.editor, Qt.Key.Key_Delete)
                self.assertEqual(self.guard.call_count, int(guarded))
                self.assertEqual(self.editor.toPlainText(), source if guarded else '')

    def test_local_replacements_match_full_document_coordinate_rules(self):
        rng = random.Random(42)
        source = '// 😀 메모\nclick 10,20\nsleep 5\n//click 30,40\nmove mouse\nclick 10,20'
        replacements = ('', '1', 'click 50,60', '\n', '//', '\nclick 7,8\n', '😀\n메모')
        for _ in range(100):
            start, end = sorted(rng.choices(range(len(source) + 1), k=2))
            replacement = rng.choice(replacements)
            expected = source[:start] + replacement + source[end:]
            coordinates = coordinate_tokens(expected)
            guarded = bool(coordinates and coordinates != coordinate_tokens(source))
            with self.subTest(start=start, end=end, replacement=replacement):
                self.select(source, start, end)
                self.guard.reset_mock()
                self.editor.guarded_edit(lambda: self.editor.textCursor().insertText(replacement))
                self.assertEqual(self.guard.call_count, int(guarded))
                self.assertEqual(self.editor.toPlainText(), source if guarded else expected)
