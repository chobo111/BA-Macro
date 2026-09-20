"""Modal overlay for editing and validating per-row scripts."""
import re
from PySide6.QtCore import Qt, QEvent, QPoint, Signal
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QVBoxLayout, QLabel,
                             QPushButton, QPlainTextEdit, QComboBox)
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
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.coordinate_hook = None
        self.coordinate_clicked.connect(self.capture_screen_point, Qt.ConnectionType.QueuedConnection)
        self.source = source
        self.native, self.target = native, target
        area = parent.screen().availableGeometry()

        self.drag_offset = None
        panel = self.panel = QFrame(self)
        panel.setObjectName('editorPanel')
        panel.setFixedSize(min(600, area.width()-32), min(540, area.height()-32))
        self.setFixedSize(panel.size())
        origin = parent.frameGeometry().topLeft()
        origin.setX(max(area.left(), min(origin.x(), area.right() - self.width() + 1)))
        origin.setY(max(area.top(), min(origin.y(), area.bottom() - self.height() + 1)))
        self.move(origin)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        self.titlebar = QFrame(panel)
        self.titlebar.setObjectName('editorTitlebar')
        self.titlebar.setFixedHeight(42)
        self.titlebar.setToolTip('드래그하여 메인 창과 함께 이동')
        header = QHBoxLayout(self.titlebar)
        header.setContentsMargins(12, 0, 12, 0)
        heading = QLabel('⠿  스크립트 작성')
        heading.setStyleSheet('font-size: 15px; font-weight: 600;')
        header.addWidget(heading)
        header.addStretch()
        drag_hint = QLabel('메인 창과 함께 이동')
        drag_hint.setStyleSheet('color: #b2c9dc; font-size: 12px;')
        header.addWidget(drag_hint)
        self.position = QLabel()
        layout.addWidget(self.titlebar)
        for widget in (self.titlebar, heading, drag_hint):
            widget.setCursor(Qt.CursorShape.SizeAllCursor)
            widget.installEventFilter(self)
        toolbar = QHBoxLayout()
        self.templates = QComboBox()
        self.templates.addItems(['예제 선택', '클릭 후 원위치', '누르는 동안 반복', '키 누름 / 해제', '드래그'])
        self.templates.activated.connect(self.insert_template)
        toolbar.addWidget(self.templates)
        toolbar.addStretch()
        toolbar.addWidget(self.position)
        layout.addLayout(toolbar)
        body = QHBoxLayout()
        self.editor = QPlainTextEdit(source)
        self.editor.setObjectName('codeEditor')
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setPlaceholderText('프로그램 밖에서 우클릭하면\n현재 입력 위치에 좌표(x,y)가 추가됩니다.')
        self.editor.cursorPositionChanged.connect(self.update_position)
        self.highlighter = Highlighter(self.editor.document())
        body.addWidget(self.editor, 1)
        layout.addLayout(body, 1)
        self.validation = QLabel('')
        self.validation.setObjectName('scriptLog')
        self.validation.setTextFormat(Qt.TextFormat.PlainText)
        self.validation.setWordWrap(True)
        self.validation.setMinimumHeight(36)
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton('취소')
        cancel.clicked.connect(self.reject)
        save = QPushButton('스크립트 저장')
        save.clicked.connect(self.save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        layout.addLayout(footer)
        layout.addWidget(self.validation)
        self.setStyleSheet('''
            #editorPanel { background: #182634; border: 1px solid #3a5269; border-radius: 10px; }
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
        self.update_position()
        self.editor.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        if self.parentWidget().hook_enabled and self.coordinate_hook is None:
            hook = CoordinateHook(self.coordinate_clicked.emit)
            try:
                hook.start()
                self.coordinate_hook = hook
            except Exception as error:
                hook.close()
                self.log(f'좌표 입력 초기화 실패: {error}', error=True)

    def hideEvent(self, event):
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

    def update_position(self):
        cursor = self.editor.textCursor()
        self.position.setText(f'{cursor.blockNumber()+1}행 · {cursor.positionInBlock()+1}열')

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
        self.validation.style().unpolish(self.validation)
        self.validation.style().polish(self.validation)

    def insert_template(self, index):
        examples = ['', 'click 420,280\nsleep 120\nmove mouse\n',
            'loop\n  click mouse\n  sleep 80\nloop_end\n',
            'key_press space\nrelease_actions\nkey_release space\n',
            'curve_first_time 80\ncurve_last_time 80\ncurve 180,240 480,240\n']
        if index:
            self.editor.insertPlainText(examples[index])
            self.templates.setCurrentIndex(0)

    def eventFilter(self, watched, event):
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
