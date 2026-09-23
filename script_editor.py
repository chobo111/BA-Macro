"""Modal overlay for editing and validating per-row scripts."""
import re
from PySide6.QtCore import Qt, QEvent, QPoint, Signal, QTimer
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QPlainTextEdit, QComboBox, QSizeGrip
from mouse_hook import CoordinateHook
from native import to_client, check
import ctypes as C
from ctypes import wintypes as W
from script_language import compile_script, ScriptError

class Highlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        command = QTextCharFormat()
        command.setForeground(QColor('#66cfff'))
        match = re.match(r'\s*([a-z_]+)', text)
        if match:
            self.setFormat(match.start(1), len(match.group(1)), command)
        comment = QTextCharFormat()
        comment.setForeground(QColor('#8fa0b2'))
        if text.lstrip().startswith('//'):
            self.setFormat(0, len(text), comment)


class ScriptEditor(QDialog):
    coordinate_clicked = Signal(int, int)

    def __init__(self, source, native, target, parent):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle('스크립트 작성')
        self.setObjectName('scriptOverlay')
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.coordinate_hook = None
        self.coordinate_clicked.connect(self.capture_screen_point, Qt.ConnectionType.QueuedConnection)
        self.source = source
        self.native, self.target = native, target
        self.syncing_geometry = False

        self.drag_offset = None
        panel = self.panel = QFrame(self)
        panel.setObjectName('editorPanel')
        self.setMinimumSize(400, 340)
        self.setGeometry(parent.frameGeometry())
        panel.resize(self.size())
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        self.titlebar = QFrame(panel)
        self.titlebar.setObjectName('editorTitlebar')
        self.titlebar.setFixedHeight(42)
        header = QHBoxLayout(self.titlebar)
        header.setContentsMargins(12, 0, 12, 0)
        heading = QLabel('스크립트 작성')
        heading.setStyleSheet('font-size: 15px; font-weight: 600;')
        header.addWidget(heading)
        header.addStretch()
        layout.addWidget(self.titlebar)
        for widget in (self.titlebar, heading):
            widget.setCursor(Qt.CursorShape.SizeAllCursor)
            widget.installEventFilter(self)
        toolbar = QHBoxLayout()
        self.templates = QComboBox()
        self.templates.addItems(['예제 선택', '스킬 카드 > 마우스 슬라이드', '마우스 > 스킬 카드 슬라이드'])
        self.templates.activated.connect(self.insert_template)
        toolbar.addWidget(self.templates)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        coordinates = QHBoxLayout()
        self.cursor_label = QLabel()
        coordinates.addWidget(self.cursor_label)
        coordinates.addStretch()
        layout.addLayout(coordinates)
        body = QHBoxLayout()
        self.editor = QPlainTextEdit(source)
        self.editor.setObjectName('codeEditor')
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setPlaceholderText('프로그램 밖에서 우클릭하면\n현재 입력 위치에 좌표(x,y)가 추가됩니다.')
        self.highlighter = Highlighter(self.editor.document())
        body.addWidget(self.editor, 1)
        layout.addLayout(body, 1)
        self.validation = QLabel('')
        self.validation.setObjectName('scriptLog')
        self.validation.setTextFormat(Qt.TextFormat.PlainText)
        self.validation.setWordWrap(True)
        self.validation.setMinimumHeight(36)
        self.validation.hide()
        self.log_timer = QTimer(self)
        self.log_timer.setSingleShot(True)
        self.log_timer.setInterval(4000)
        self.log_timer.timeout.connect(self.validation.hide)
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton('취소')
        cancel.clicked.connect(self.reject)
        save = QPushButton('저장')
        save.clicked.connect(self.save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        footer.addWidget(QSizeGrip(self))
        layout.addLayout(footer)
        layout.addWidget(self.validation)
        self.setStyleSheet('''
            #editorPanel { background: #182634; border: 1px solid #3a5269; border-radius: 0px; }
            QWidget { color: #e8f1fa; font-size: 13px; }
            QLabel { background: transparent; border: none; }
            #scriptLog { background: #0e1822; color: #a8bdd0; border: 1px solid #33495d; border-radius: 4px; padding: 6px 8px; }
            #scriptLog[error="true"] { color: #ffb0a6; border-color: #a55450; }
            #editorTitlebar { background: #2d4860; border: 1px solid #52718a; border-radius: 6px; }
            QPlainTextEdit { background: #0e1822; border: 1px solid #33495d; padding: 10px; }
            #codeEditor { font-family: Consolas, 'Malgun Gothic'; font-size: 15px; }
            QPushButton, QComboBox { background: #263c50; border: 1px solid #4a6780; padding: 7px 12px; border-radius: 5px; }
            QPushButton:hover { background: #315779; }
        ''')
        self.cursor_timer = QTimer(self)
        self.cursor_timer.setInterval(100)
        self.cursor_timer.timeout.connect(self.update_cursor)
        self.editor.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        self.parentWidget().installEventFilter(self)
        self.sync_to_parent()
        self.update_cursor()
        self.cursor_timer.start()
        if self.parentWidget().hook_enabled and self.coordinate_hook is None:
            hook = CoordinateHook(self.coordinate_clicked.emit)
            try:
                hook.start()
                self.coordinate_hook = hook
            except Exception as error:
                hook.close()
                self.log(f'좌표 입력 초기화 실패: {error}', error=True)

    def hideEvent(self, event):
        self.cursor_timer.stop()
        self.parentWidget().removeEventFilter(self)
        if self.coordinate_hook is not None:
            self.coordinate_hook.close()
            self.coordinate_hook = None
        super().hideEvent(event)

    def capture_screen_point(self, x, y):
        if not self.isVisible():
            return
        try:
            point = W.POINT(x, y)
            if self.target:
                check(to_client(self.target, C.byref(point)))
            self.pick_coordinate((point.x, point.y))
        except OSError as error:
            self.log(str(error), error=True)

    def validate(self):
        try:
            compile_script(self.editor.toPlainText())
            self.log('문법 검사 통과')
            return True
        except ScriptError as error:
            self.log(str(error), error=True)
            cursor = QTextCursor(self.editor.document().findBlockByNumber(max(0, error.line-1)))
            self.editor.setTextCursor(cursor)
            self.editor.setFocus()
            return False

    def save(self):
        if self.validate():
            self.source = self.editor.toPlainText()
            self.accept()

    def log(self, message, error=False):
        self.validation.setText(('오류 · ' if error else '') + message)
        self.validation.setProperty('error', error)
        self.validation.show()
        self.log_timer.start()
        self.validation.style().unpolish(self.validation)
        self.validation.style().polish(self.validation)

    def insert_template(self, index):
        examples = ['',
            'loop\ncurve_first_time 5\ncurve_last_time 5\ncurve X,Y mouse // 스킬 카드 좌표 XY의 스킬 카드를 마우스 위치에 연사\nsleep 5\nloop_end',
            'loop\ncurve_first_time 5\ncurve_last_time 5\ncurve mouse X,Y // 스킬 카드에 마우스를 올리고 사용하면, XY좌표에 스킬을 연사\nsleep 5\nloop_end']
        if 0 < index < len(examples):
            self.editor.setPlainText(examples[index])
            self.templates.setCurrentIndex(0)
            self.log('X,Y를 실제 좌표로 바꾼 뒤 저장하세요.')

    def update_cursor(self):
        try:
            x, y = self.native.cursor(self.target)
            self.cursor_label.setText(f'{x}, {y}')
        except OSError:
            self.cursor_label.setText('— , —')

    def sync_to_parent(self):
        self.syncing_geometry = True
        try:
            self.setGeometry(self.parentWidget().frameGeometry())
        finally:
            self.syncing_geometry = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'panel'):
            self.panel.resize(event.size())
        parent = self.parentWidget()
        if self.isVisible() and not self.syncing_geometry:
            frame = parent.frameGeometry().size() - parent.size()
            client = event.size() - frame
            if parent.size() != client:
                self.syncing_geometry = True
                try:
                    parent.resize(client)
                finally:
                    self.syncing_geometry = False
                self.sync_to_parent()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget():
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Move) and not self.syncing_geometry:
                self.sync_to_parent()
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.panel.mapToGlobal(QPoint())
            self.drag_panel_origin = self.pos()
            self.drag_parent_origin = self.parentWidget().pos()
            return True
        if event.type() == QEvent.Type.MouseMove and self.drag_offset is not None:
            position = event.globalPosition().toPoint() - self.drag_offset
            self.parentWidget().move(self.drag_parent_origin + position - self.drag_panel_origin)
            self.move(position)
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = None
            return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            local = event.position().toPoint()
            parent = self.parentWidget()
            if self.panel.geometry().contains(local):
                return
            if parent.isVisible() and parent.frameGeometry().contains(event.globalPosition().toPoint()):
                return
            self.pick_coordinate()
            event.accept()
            return
        super().mousePressEvent(event)

    def pick_coordinate(self, point=None):
        try:
            x, y = point if point is not None else self.native.cursor(self.target)
            l, t, r, b = self.native.bounds(self.target)
            if not (l <= x < r and t <= y < b and abs(x) <= 99999 and abs(y) <= 99999):
                self.log('대상 영역 안의 좌표를 우클릭하세요. 좌표는 최대 5자리입니다.', error=True)
                return
            self.editor.insertPlainText(f'{x},{y}')
            self.log(f'좌표 추가: {x}, {y}')
            self.editor.setFocus()
        except OSError as error:
            self.log(str(error), error=True)
