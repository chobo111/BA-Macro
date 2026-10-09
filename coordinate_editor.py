"""Guard user-entered fixed coordinates while allowing captured baseline points."""
import re
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QPlainTextEdit
from coordinate_transform import COORD_COMMANDS


def coordinate_tokens(source):
    result = []
    for line in source.splitlines():
        code = line.split('//', 1)[0].split(None, 1)
        if len(code) == 2 and code[0].lower() in COORD_COMMANDS:
            numbers = tuple(re.findall(r'-?\d+', code[1]))
            if numbers:
                result.append((code[0].lower(), numbers))
    return tuple(result)


class CoordinateTextEdit(QPlainTextEdit):
    def __init__(self, source, can_edit_coordinates):
        super().__init__(source)
        self.can_edit_coordinates = can_edit_coordinates

    def allow_replacement(self, replacement, *, start=None, end=None):
        cursor = self.textCursor()
        start = cursor.selectionStart() if start is None else start
        end = cursor.selectionEnd() if end is None else end
        source = self.toPlainText()
        encoded = source.encode('utf-16-le')
        proposed = (encoded[:start*2].decode('utf-16-le') + replacement
                    + encoded[end*2:].decode('utf-16-le'))
        new = coordinate_tokens(proposed)
        if new and new != coordinate_tokens(source):
            return self.can_edit_coordinates()
        return True

    def keyPressEvent(self, event):
        cursor = self.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        replacement = event.text()
        if event.matches(QKeySequence.StandardKey.Cut):
            replacement = ''
        elif event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if start == end:
                selection = self.textCursor()
                operation = (selection.MoveOperation.PreviousCharacter if event.key() == Qt.Key.Key_Backspace
                             else selection.MoveOperation.NextCharacter)
                selection.movePosition(operation, selection.MoveMode.KeepAnchor)
                start,end = selection.selectionStart(),selection.selectionEnd()
            replacement = ''
        elif event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            replacement = '\n'
        elif not replacement or any(ord(char) < 32 for char in replacement):
            return super().keyPressEvent(event)
        if not self.allow_replacement(replacement,start=start,end=end):
            event.accept()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        if source.hasText() and not self.allow_replacement(source.text()):
            return
        super().insertFromMimeData(source)

    def inputMethodEvent(self, event):
        commit = event.commitString()
        if commit:
            cursor = self.textCursor()
            start,end = cursor.selectionStart(),cursor.selectionEnd()
            if event.replacementLength():
                start = cursor.position()+event.replacementStart()
                end = start+event.replacementLength()
            if not self.allow_replacement(commit,start=start,end=end):
                event.accept()
                return
        super().inputMethodEvent(event)

    def dropEvent(self, event):
        source = event.mimeData()
        cursor = self.cursorForPosition(event.position().toPoint())
        if source.hasText() and not self.allow_replacement(source.text(),start=cursor.position(),end=cursor.position()):
            event.ignore()
            return
        super().dropEvent(event)
