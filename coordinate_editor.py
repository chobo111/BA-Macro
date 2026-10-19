"""Guard user-entered fixed coordinates while allowing captured baseline points."""
from PySide6.QtGui import QKeySequence, QTextCursor, QTextDocument
from PySide6.QtWidgets import QPlainTextEdit
from script_language import coordinate_tokens
from shiboken6 import isValid


class CoordinateTextEdit(QPlainTextEdit):
    """Validate actual user edits as one undoable transaction.

    Initial text, templates and inverse-scaled captures use programmatic text
    setters. Keyboard, paste, IME and drop edits use these guarded entry points.
    Undo/redo can only revisit already-approved transactions.
    """
    def __init__(self, source, can_edit_coordinates):
        super().__init__(source)
        self.can_edit_coordinates = can_edit_coordinates
        self.editing = False

    def edit_span(self):
        """Track the selected lines and their neighbours through a local edit.

        Adjacent lines cover Backspace/Delete joining paragraphs. Qt cursors
        track UTF-16 offsets and grow with inserted text, including pasted lines.
        """
        cursor = self.textCursor()
        document = self.document()
        first = document.findBlock(cursor.selectionStart())
        last = document.findBlock(cursor.selectionEnd())
        if first.previous().isValid():
            first = first.previous()
        if last.next().isValid():
            last = last.next()
        start, end = QTextCursor(document), QTextCursor(document)
        start.setPosition(first.position())
        start.setKeepPositionOnInsert(True)
        end.setPosition(last.position() + last.length() - 1)
        return start, end

    def span_text(self, span):
        start, end = span
        cursor = QTextCursor(self.document())
        cursor.setPosition(start.position())
        cursor.setPosition(end.position(), QTextCursor.MoveMode.KeepAnchor)
        return cursor.selectedText().replace('\u2029', '\n')

    def guarded_edit(self, operation, *, local=True):
        """Return whether this edit passed the coordinate-basis check."""
        if self.editing:
            operation()
            return True
        self.editing = True
        try:
            span = self.edit_span() if local else None
            read = (lambda: self.span_text(span)) if local else self.toPlainText
            before = read()
            revision = self.document().revision()
            cursor = self.textCursor()
            selection = cursor.anchor(), cursor.position()
            cursor.beginEditBlock()
            try:
                operation()
            finally:
                cursor.endEditBlock()
            if self.document().revision() == revision:
                return True
            after = read()
            if after == before:
                return True
            new = coordinate_tokens(after)
            previous = coordinate_tokens(before)
            if new == previous:
                return True
            # Removing the last coordinates needs no basis. If other lines
            # still contain coordinates, retain the existing deletion guard.
            if not new and (not local or not coordinate_tokens(self.toPlainText())):
                return True
            changed = self.textCursor()
            changed_selection = changed.anchor(), changed.position()
            # Restore the valid draft BEFORE a modal decision can save/convert it.
            super().undo()
            self.restore_selection(selection)
            approved = False
            try:
                approved = bool(self.can_edit_coordinates())
            finally:
                if not approved and isValid(self):
                    # Cancelled/failed edits cannot return via Ctrl+Y or the menu.
                    self.document().clearUndoRedoStacks(QTextDocument.Stacks.RedoStack)
            if not isValid(self):
                # Conversion can close the editor while an async save finishes.
                return False
            if approved:
                super().redo()
                self.restore_selection(changed_selection)
            return approved
        finally:
            self.editing = False

    def restore_selection(self, selection):
        cursor = self.textCursor()
        cursor.setPosition(selection[0])
        cursor.setPosition(selection[1], cursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Undo) or event.matches(QKeySequence.StandardKey.Redo):
            return super().keyPressEvent(event)
        self.guarded_edit(lambda: super(CoordinateTextEdit, self).keyPressEvent(event))

    def insertFromMimeData(self, source):
        self.guarded_edit(lambda: super(CoordinateTextEdit, self).insertFromMimeData(source))

    def inputMethodEvent(self, event):
        # IME replacement offsets need not be inside the current selection.
        self.guarded_edit(lambda: super(CoordinateTextEdit, self).inputMethodEvent(event), local=False)

    def dropEvent(self, event):
        approved = False
        try:
            # A drop can insert at a different cursor and move a remote selection.
            approved = self.guarded_edit(lambda: super(CoordinateTextEdit, self).dropEvent(event), local=False)
        finally:
            if not approved:
                # A source editor deletes its selection after an accepted MOVE.
                # Rolling back our document alone would still report success.
                event.ignore()
