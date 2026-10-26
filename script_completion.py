"""Local, token-based completion for the script editor."""
import re

from PySide6.QtCore import Qt, QEvent, QPointF, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QListWidget, QListWidgetItem
from shiboken6 import isValid

from coordinate_editor import CoordinateTextEdit


COMMANDS = (
    ('move', '가상 터치 이동'),
    ('click', '좌표 클릭'),
    ('press', '누르기'),
    ('release', '누름 해제'),
    ('switch', '누름·해제 전환'),
    ('mouse_press', '현재 위치 누르기'),
    ('mouse_release', '현재 위치 해제'),
    ('mouse_switch', '현재 위치 누름·해제 전환'),
    ('sleep', '대기'),
    ('curve', '드래그'),
    ('curve_first_time', '드래그 시작 대기 시간'),
    ('curve_last_time', '드래그 끝 대기 시간'),
    ('curve_release', '드래그 해제'),
    ('release_all', '모든 터치 해제'),
    ('loop', '반복 시작'),
    ('loop_end', '반복 끝'),
    ('release_actions', '단축키를 놓은 뒤 실행'),
)
NO_ARGUMENT = {'curve_release', 'release_all', 'loop_end', 'release_actions'}


def completion_context(line, position, explicit=False):
    """Return a token span and candidates; inspect only this line, never compile."""
    comment = line.find('//')
    if comment >= 0:
        if position >= comment:
            return None
        line = line[:comment]
    tokens = list(re.finditer(r'\S+', line))
    token = next((t for t in tokens if t.start() <= position <= t.end()), None)
    start, end = (token.start(), token.end()) if token else (position, position)
    if token and not re.fullmatch(r'[A-Za-z_]+', token.group()):
        return None
    prefix = line[start:position].lower()
    if prefix and not re.fullmatch(r'[a-z_]+', prefix):
        return None
    index = sum(t.end() < position for t in tokens)
    if index != 0 or (not prefix and not explicit):
        return None
    choices = COMMANDS
    choices = tuple(choice for choice in choices if choice[0].startswith(prefix))
    if not choices or (not explicit and len(choices) == 1 and choices[0][0] == prefix):
        return None
    return start, end, choices


class ScriptTextEdit(CoordinateTextEdit):
    coordinate_requested = Signal(QPointF)

    def __init__(self, source, can_edit_coordinates):
        super().__init__(source, can_edit_coordinates)
        self.completions = QListWidget(self.viewport())
        self.completions.setObjectName('scriptCompletions')
        self.completions.setAccessibleName('명령어 자동완성')
        self.completions.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.completions.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.completions.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.completions.setUniformItemSizes(True)
        self.completions.viewport().installEventFilter(self)
        self.completions.hide()
        self.completions.itemClicked.connect(self.accept_completion)
        self.textChanged.connect(self.completions.hide)
        self.cursorPositionChanged.connect(self.completions.hide)
        self.verticalScrollBar().valueChanged.connect(self.completions.hide)
        self.horizontalScrollBar().valueChanged.connect(self.completions.hide)

    def current_completion(self, explicit=False):
        cursor = self.textCursor()
        if cursor.hasSelection():
            return None
        before = QTextCursor(cursor)
        before.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
        return completion_context(cursor.block().text(), len(before.selectedText()), explicit)

    def show_completions(self, explicit=False):
        context = self.current_completion(explicit)
        if not context or not self.hasFocus():
            self.completions.hide()
            return
        self.completions.clear()
        for word, description in context[2]:
            item = QListWidgetItem(f'{word}  —  {description}')
            item.setData(Qt.ItemDataRole.UserRole, word)
            item.setToolTip(item.text())
            self.completions.addItem(item)
        self.completions.setCurrentRow(0)
        rect = self.cursorRect()
        viewport = self.viewport().rect()
        row_height = self.completions.sizeHintForRow(0)
        height = min(row_height * min(6, self.completions.count()) + 4,
                     max(rect.top(), viewport.height() - rect.bottom() - 1))
        if height < row_height + 4:
            self.completions.hide()
            return
        width = min(viewport.width(), self.completions.sizeHintForColumn(0) + 28)
        x = min(max(0, rect.left()), viewport.width() - width)
        y = rect.bottom() + 1
        if y + height > viewport.height():
            y = max(0, rect.top() - height)
        self.completions.setGeometry(x, y, width, height)
        self.completions.show()
        self.completions.raise_()

    def accept_completion(self, item=None):
        item = item or self.completions.currentItem()
        context = self.current_completion(explicit=True)
        self.completions.hide()
        if item is None or context is None:
            return
        word = item.data(Qt.ItemDataRole.UserRole)
        start, end, choices = context
        if word not in {choice[0] for choice in choices}:
            return
        cursor = self.textCursor()
        line = cursor.block().text()
        block_start = cursor.block().position()
        # QTextCursor offsets count UTF-16 units, including non-BMP characters.
        cursor.setPosition(block_start + len(line[:start].encode('utf-16-le')) // 2)
        cursor.setPosition(block_start + len(line[:end].encode('utf-16-le')) // 2,
                           QTextCursor.MoveMode.KeepAnchor)
        replacement = word
        if word not in NO_ARGUMENT and (end == len(line) or not line[end].isspace()):
            replacement += ' '

        def insert():
            cursor.insertText(replacement)
            self.setTextCursor(cursor)
        self.guarded_edit(insert)

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            if (event.key() == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.ControlModifier
                    or hasattr(self, 'completions') and self.completions.isVisible()
                    and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Escape)):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.show_completions(explicit=True)
            event.accept()
            return
        if self.completions.isVisible() and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                step = -1 if event.key() == Qt.Key.Key_Up else 1
                self.completions.setCurrentRow((self.completions.currentRow() + step) % self.completions.count())
                event.accept()
                return
            if event.key() == Qt.Key.Key_Tab:
                self.accept_completion()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Escape:
                self.completions.hide()
                event.accept()
                return
        self.completions.hide()
        super().keyPressEvent(event)
        if (isValid(self) and event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab,
                                                 Qt.Key.Key_Backtab, Qt.Key.Key_Escape)
                and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                                             | Qt.KeyboardModifier.MetaModifier)
                and (event.text() or event.key() == Qt.Key.Key_Backspace)):
            self.show_completions()

    def focusOutEvent(self, event):
        self.completions.hide()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self.completions.hide()
        super().hideEvent(event)

    def resizeEvent(self, event):
        if hasattr(self, 'completions'):
            self.completions.hide()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        self.completions.hide()
        if event.button() == Qt.MouseButton.RightButton:
            # Capture at the existing insertion point, not the clicked text.
            event.accept()
            self.coordinate_requested.emit(event.globalPosition())
            return
        super().mousePressEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.completions.viewport():
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                self.mousePressEvent(event)
                return True
            if event.type() == QEvent.Type.ContextMenu:
                self.contextMenuEvent(event)
                return True
        return super().eventFilter(watched, event)

    def contextMenuEvent(self, event):
        # Windows sends this separately on release; do not capture twice.
        event.accept()

    def inputMethodEvent(self, event):
        self.completions.hide()
        super().inputMethodEvent(event)
