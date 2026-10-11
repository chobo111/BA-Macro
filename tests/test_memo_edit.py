"""Memo edits preserve imported data and save only intentional changes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from app import MainWindow
from domain import Profile, atomic_json, read_json


class MemoEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle('Fusion')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.shutdown()
            self.window.close()
            self.window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.qt.processEvents()
        self.directory.cleanup()

    def open_profile(self, memo):
        path = Path(self.directory.name) / 'macro.json'
        profile = Profile.default()
        profile.rows[0].memo = memo
        atomic_json(path, profile.export())
        self.window = MainWindow(data_dir=self.directory.name, hook_enabled=False)
        self.window.timer.stop()
        self.window.show()
        self.qt.processEvents()
        self.assertEqual(self.window.profile.rows[0].memo, memo)
        return self.window

    def edit(self):
        self.window.table.editItem(self.window.table.item(0, 4))
        self.qt.processEvents()
        editor = self.qt.focusWidget()
        self.assertIsInstance(editor, QLineEdit)
        return editor

    def paste(self, editor, text):
        self.qt.clipboard().setText(text)
        QTest.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

    def assert_saved(self, text, *, table=False):
        w = self.window
        w.flush_pending_saves()
        self.assertEqual(w.profile.rows[0].memo, text)
        self.assertEqual(read_json(w.path)['macros'][0]['memo'], text)
        if table:
            self.assertEqual(w.table.item(0, 4).text(), text)

    def test_open_and_close_long_note_never_save_or_truncate(self):
        original = '가' * 32767 + '😀원문끝'
        w = self.open_profile(original)
        before = w.path.read_bytes()
        with patch.object(w, 'save', wraps=w.save) as save:
            for key in (Qt.Key.Key_Escape, Qt.Key.Key_Return):
                with self.subTest(key=key):
                    editor = self.edit()
                    self.assertEqual(editor.text(), original)
                    self.assertIn('1,000자', w.statusBar().currentMessage())
                    QTest.keyClick(editor, key)
                    self.qt.processEvents()
                    self.assert_saved(original, table=True)
                    self.assertEqual(w.path.read_bytes(), before)
            save.assert_not_called()

    def test_typing_at_limit_rejects_extra_character_without_saving(self):
        w = self.open_profile('a' * 999)
        editor = self.edit()
        editor.end(False)
        QTest.keyClicks(editor, 'b')
        self.assert_saved('a' * 999 + 'b')
        with patch.object(w, 'save', wraps=w.save) as save:
            QTest.keyClicks(editor, 'c')
            self.assert_saved('a' * 999 + 'b')
            save.assert_not_called()
        self.assertIn('1,000자', w.statusBar().currentMessage())

    def test_paste_rejects_whole_overlong_replacement_then_accepts_boundary(self):
        w = self.open_profile('original')
        editor = self.edit()
        editor.selectAll()
        with patch.object(w, 'save', wraps=w.save) as save:
            self.paste(editor, 'x' * 1001)
            self.assertEqual(editor.text(), 'original')
            self.assert_saved('original')
            save.assert_not_called()
        self.assertIn('1,000자', w.statusBar().currentMessage())
        editor.selectAll()
        self.paste(editor, 'x' * 1000)
        self.assertEqual(editor.text(), 'x' * 1000)
        self.assert_saved('x' * 1000)

    def test_unicode_characters_count_without_utf16_truncation(self):
        w = self.open_profile('')
        editor = self.edit()
        text = '가' * 500 + '😀' * 500
        self.paste(editor, text)
        self.assertEqual(editor.text(), text)
        self.assert_saved(text)
        self.paste(editor, '😀')
        self.assertEqual(editor.text(), text)
        self.assert_saved(text)
        self.assertIn('1,000자', w.statusBar().currentMessage())

    def test_input_method_commit_obeys_limit_and_autosaves(self):
        w = self.open_profile('a' * 999)
        editor = self.edit()
        editor.end(False)
        event = QInputMethodEvent()
        event.setCommitString('한')
        self.qt.sendEvent(editor, event)
        self.assert_saved('a' * 999 + '한')
        event = QInputMethodEvent()
        event.setCommitString('글')
        self.qt.sendEvent(editor, event)
        self.assert_saved('a' * 999 + '한')
        self.assertIn('1,000자', w.statusBar().currentMessage())

    def test_cancel_restores_entire_old_note_after_live_save(self):
        original = 'old' * 11000 + '😀끝'
        w = self.open_profile(original)
        editor = self.edit()
        editor.selectAll()
        self.paste(editor, 'short')
        self.assert_saved('short')
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        self.qt.processEvents()
        self.assert_saved(original, table=True)
        self.assertEqual(self.edit().text(), original)

    def test_old_note_cannot_grow_and_shortened_note_gets_new_limit_on_reopen(self):
        w = self.open_profile('a' * 1100)
        editor = self.edit()
        editor.end(False)
        QTest.keyClicks(editor, 'x')
        self.assert_saved('a' * 1100)
        self.assertIn('더 늘릴 수 없습니다', w.statusBar().currentMessage())
        QTest.keyClick(editor, Qt.Key.Key_Backspace)
        self.assert_saved('a' * 1099)
        QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assert_saved('a' * 1100)
        editor.selectAll()
        self.paste(editor, 'b' * 1000)
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.qt.processEvents()
        self.assert_saved('b' * 1000, table=True)
        editor = self.edit()
        editor.end(False)
        QTest.keyClicks(editor, 'x')
        self.assert_saved('b' * 1000)
        self.assertIn('1,000자', w.statusBar().currentMessage())

    def test_undo_redo_are_real_edits_and_focus_commit_does_not_save_twice(self):
        w = self.open_profile('a')
        editor = self.edit()
        editor.end(False)
        QTest.keyClicks(editor, 'b')
        self.assert_saved('ab')
        QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assert_saved('a')
        QTest.keyClick(editor, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assert_saved('ab')
        with patch.object(w, 'save', wraps=w.save) as save:
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.qt.processEvents()
            self.assert_saved('ab', table=True)
            save.assert_not_called()

    def test_pending_memo_tooltip_update_is_cancelled_when_table_is_destroyed(self):
        from PySide6.QtWidgets import QAbstractItemDelegate
        from widgets import RowsTable
        table = RowsTable()
        editor = QLineEdit(table)
        with patch.object(table, 'update_memo_tooltips') as refresh:
            table.itemDelegateForColumn(4).closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
            table.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.qt.processEvents()
            refresh.assert_not_called()
