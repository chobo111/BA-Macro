"""Embedded script editor that leaves the main title bar and menus accessible."""
import re
from PySide6.QtCore import Qt, QEvent, Signal, QTimer
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QPlainTextEdit, QComboBox, QSizeGrip, QApplication
from mouse_hook import CoordinateHook
from script_language import compile_script, ScriptError
from script_help import ScriptHelpDialog

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
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle('스크립트 작성')
        self.setObjectName('scriptOverlay')
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.coordinate_hook = None
        self.coordinate_clicked.connect(self.capture_screen_point, Qt.ConnectionType.QueuedConnection)
        self.capture_resolution = None
        self.source = source
        self.native, self.target = native, target
        self.syncing_geometry = False

        panel = self.panel = QFrame(self)
        panel.setObjectName('editorPanel')
        self.setGeometry(self.parent_area())
        panel.resize(self.size())
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        toolbar = QHBoxLayout()
        self.templates = QComboBox()
        self.templates.addItems(['예제 선택', '스킬 카드 > 마우스 슬라이드', '마우스 > 스킬 카드 슬라이드'])
        self.templates.activated.connect(self.insert_template)
        toolbar.addWidget(self.templates)
        toolbar.addStretch()
        help_button = QPushButton('도움말')
        help_button.clicked.connect(self.show_help)
        toolbar.addWidget(help_button)
        layout.addLayout(toolbar)
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
        self.size_grip = QSizeGrip(self)
        self.size_grip.resize(self.size_grip.sizeHint())
        footer.addSpacing(self.size_grip.width())
        layout.addLayout(footer)
        layout.addWidget(self.validation)
        self.setStyleSheet('''
            #editorPanel { background: #182634; border: 1px solid #3a5269; border-radius: 0px; }
            QWidget { color: #e8f1fa; font-size: 13px; }
            QLabel { background: transparent; border: none; }
            #scriptLog { background: #0e1822; color: #a8bdd0; border: 1px solid #33495d; border-radius: 4px; padding: 6px 8px; }
            #scriptLog[error="true"] { color: #ffb0a6; border-color: #a55450; }
            QPlainTextEdit { background: #0e1822; border: 1px solid #33495d; padding: 10px; }
            #codeEditor { font-family: Consolas, 'Malgun Gothic'; font-size: 15px; }
            QPushButton, QComboBox { background: #263c50; border: 1px solid #4a6780; padding: 7px 12px; border-radius: 5px; }
            QPushButton:hover { background: #315779; }
        ''')
        self.editor.setFocus()
        self.position_size_grip()

    def show_help(self):
        dialog = ScriptHelpDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def position_size_grip(self):
        if hasattr(self, 'size_grip'):
            self.size_grip.move(self.width() - self.size_grip.width(), self.height() - self.size_grip.height())
            self.size_grip.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self.parentWidget().installEventFilter(self)
        self.sync_to_parent()
        if self.parentWidget().hook_enabled and self.coordinate_hook is None:
            hook = CoordinateHook(self.coordinate_clicked.emit)
            try:
                hook.start()
                self.coordinate_hook = hook
            except Exception as error:
                hook.close()
                self.log(f'좌표 입력 초기화 실패: {error}', error=True)

    def hideEvent(self, event):
        self.parentWidget().removeEventFilter(self)
        if self.coordinate_hook is not None:
            self.coordinate_hook.close()
            self.coordinate_hook = None
        super().hideEvent(event)

    def capture_screen_point(self, x, y):
        if not self.isVisible() or QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return
        try:
            self.pick_coordinate(self.native.local_point(self.target, x, y))
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

    def parent_area(self):
        parent = self.parentWidget()
        top = parent.menuBar().geometry().bottom() + 1
        return parent.rect().adjusted(0, top, 0, 0)

    def sync_to_parent(self):
        self.syncing_geometry = True
        try:
            self.setGeometry(self.parent_area())
            self.raise_()
        finally:
            self.syncing_geometry = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_size_grip()
        if hasattr(self, 'panel'):
            self.panel.resize(event.size())
        parent = self.parentWidget()
        if self.isVisible() and not self.syncing_geometry:
            top = parent.menuBar().geometry().bottom() + 1
            self.syncing_geometry = True
            try:
                parent.resize(event.size().width(), event.size().height() + top)
            finally:
                self.syncing_geometry = False
            self.sync_to_parent()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            if not self.syncing_geometry:
                self.sync_to_parent()
        return super().eventFilter(watched, event)

    def pick_coordinate(self, point=None):
        try:
            x, y = point if point is not None else self.native.cursor(self.target)
            l, t, r, b = self.native.bounds(self.target)
            if not (l <= x < r and t <= y < b and abs(x) <= 99999 and abs(y) <= 99999):
                self.log('대상 영역 안의 좌표를 우클릭하세요. 좌표는 최대 5자리입니다.', error=True)
                return
            size = self.native.size(self.target)
            profile = self.parentWidget().profile
            basis = self.capture_resolution or profile.resolution
            has_coordinates = any(row.x or row.y or row.script.strip() for row in profile.rows)
            if (basis and basis != size) or (not basis and has_coordinates):
                self.log('해상도 기준이 다릅니다. 편집기를 닫고 설정 → 프로필 해상도 변환을 먼저 진행하세요.', error=True)
                return
            self.capture_resolution = size
            self.editor.insertPlainText(f'{x},{y}')
            self.log(f'좌표 추가: {x}, {y}')
            self.editor.setFocus()
        except OSError as error:
            self.log(str(error), error=True)
