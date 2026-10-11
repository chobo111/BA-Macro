"""Guard user-entered fixed coordinates while allowing captured baseline points."""
import re
from PySide6.QtGui import QKeySequence, QTextDocument
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
    """Validate actual user edits as one undoable transaction.

    Initial text, templates and inverse-scaled captures use programmatic text
    setters. User edits use these guarded entry points, including the standard
    context menu. Undo/redo can only revisit already-approved transactions.
    """
    def __init__(self, source, can_edit_coordinates):
        super().__init__(source)
        self.can_edit_coordinates = can_edit_coordinates
        self.editing = False

    def guarded_edit(self, operation):
        """Return whether this edit passed the coordinate-basis check."""
        if self.editing:
            operation()
            return True
        self.editing = True
        try:
            before = self.toPlainText()
            cursor = self.textCursor()
            selection = cursor.anchor(), cursor.position()
            cursor.beginEditBlock()
            try:
                operation()
            finally:
                cursor.endEditBlock()
            after = self.toPlainText()
            new = coordinate_tokens(after)
            if not new or new == coordinate_tokens(before):
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
                if not approved:
                    # Cancelled/failed edits cannot return via Ctrl+Y or the menu.
                    self.document().clearUndoRedoStacks(QTextDocument.Stacks.RedoStack)
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

    def cut(self):
        self.guarded_edit(super().cut)

    def delete_selection(self):
        self.guarded_edit(lambda: self.textCursor().removeSelectedText())

    def createStandardContextMenu(self, *args):
        menu = super().createStandardContextMenu(*args)
        callbacks = {'edit-cut': self.cut, 'edit-delete': self.delete_selection}
        for action in menu.actions():
            callback = callbacks.get(action.objectName())
            if callback is not None:
                action.triggered.disconnect()
                action.triggered.connect(callback)
        return menu

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu(event.pos())
        try:
            menu.exec(event.globalPos())
        finally:
            menu.deleteLater()

    def insertFromMimeData(self, source):
        self.guarded_edit(lambda: super(CoordinateTextEdit, self).insertFromMimeData(source))

    def inputMethodEvent(self, event):
        self.guarded_edit(lambda: super(CoordinateTextEdit, self).inputMethodEvent(event))

    def dropEvent(self, event):
        approved = False
        try:
            approved = self.guarded_edit(lambda: super(CoordinateTextEdit, self).dropEvent(event))
        finally:
            if not approved:
                # A source editor deletes its selection after an accepted MOVE.
                # Rolling back our document alone would still report success.
                event.ignore()
