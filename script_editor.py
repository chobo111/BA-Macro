"""Embedded script editor that leaves the main title bar and menus accessible."""
import re
from math import floor
from threading import Event
from PySide6.QtCore import Qt, QEvent, QPoint, Signal, QTimer
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QWidget, QDialog, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QSizeGrip, QApplication, QSizePolicy
from mouse_hook import CoordinateHook
from widgets import NoWheelCombo, ElidedLabel
from script_language import compile_script, coordinate_tokens, ScriptError
from script_help import ScriptHelpDialog
from domain import same_program
from coordinate_cache import scaled_point
from script_completion import ScriptTextEdit
from coordinate_transform import aspect_warning

class Highlighter(QSyntaxHighlighter):
    def __init__(self, document, colors):
        super().__init__(document)
        self.apply_theme(colors)

    def apply_theme(self, colors):
        self.command = QTextCharFormat()
        self.command.setForeground(QColor(colors.command))
        self.comment = QTextCharFormat()
        self.comment.setForeground(QColor(colors.comment))
        self.rehighlight()

    def highlightBlock(self, text):
        match = re.match(r'\s*([a-z_]+)', text, re.IGNORECASE)
        if match:
            self.setFormat(match.start(1), len(match.group(1)), self.command)
        comment_start = text.find('//')
        if comment_start >= 0:
            # Qt format ranges use UTF-16 units, including emoji in comments.
            start = len(text[:comment_start].encode('utf-16-le')) // 2
            length = len(text[comment_start:].encode('utf-16-le')) // 2
            self.setFormat(start, length, self.comment)


class ScriptEditor(QDialog):
    coordinate_clicked = Signal(int, int)
    capture_failed = Signal(object, str)

    def __init__(self, source, native, target, parent):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle('스크립트 작성')
        self.setObjectName('scriptOverlay')
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.coordinate_hook = None
        self.capture_enabled = Event()
        self.capture_blockers = set()
        self.coordinate_clicked.connect(self.capture_screen_point, Qt.ConnectionType.QueuedConnection)
        self.capture_failed.connect(self.on_capture_failed, Qt.ConnectionType.QueuedConnection)
        self.capture_resolution = None
        self.capture_profile = parent.profile
        self.profile_resolution = parent.profile.resolution
        self.capture_program = None
        self.source = source
        self.native, self.target = native, target or None
        self.syncing_geometry = False

        panel = self.panel = QFrame(self)
        panel.setObjectName('editorPanel')
        self.setGeometry(self.parent_area())
        panel.resize(self.size())
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        toolbar = QHBoxLayout()
        self.templates = NoWheelCombo()
        self.templates.addItems(['예제 선택', '좌표 → 마우스 슬라이드', '마우스 → 좌표 슬라이드',
                                 '좌표 → 좌표 슬라이드', '좌표→좌표 연속 클릭'])
        self.templates.activated.connect(self.insert_template)
        toolbar.addWidget(self.templates)
        toolbar.addStretch()
        help_button = QPushButton('?')
        help_button.setObjectName('scriptHelpButton')
        help_button.setFixedSize(32, 32)
        help_button.setAccessibleName('도움말')
        help_button.setToolTip('도움말')
        help_button.clicked.connect(self.show_help)
        toolbar.addWidget(help_button)
        layout.addLayout(toolbar)
        self.row_context = QWidget()
        context_layout = QHBoxLayout(self.row_context)
        context_layout.setContentsMargins(0, 0, 0, 0)
        self.row_identity = ElidedLabel()
        self.row_identity.setObjectName('muted')
        self.row_identity.setTextFormat(Qt.TextFormat.PlainText)
        self.row_identity.setMinimumWidth(0)
        self.row_identity.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.row_coordinates = QLabel()
        self.row_coordinates.setObjectName('muted')
        self.row_coordinates.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        context_layout.addWidget(self.row_identity, 1)
        context_layout.addWidget(self.row_coordinates)
        self.row_context.hide()
        layout.addWidget(self.row_context)
        body = QHBoxLayout()
        self.editor = ScriptTextEdit(source, self.can_edit_coordinates)
        self.editor.coordinate_requested.connect(self.capture_editor_point)
        self.editor.setObjectName('codeEditor')
        self.editor.setLineWrapMode(ScriptTextEdit.LineWrapMode.NoWrap)
        self.editor.setPlaceholderText('우클릭으로 현재 입력 위치에 좌표를 불러오기')
        self.highlighter = Highlighter(self.editor.document(), parent.theme_colors)
        body.addWidget(self.editor, 1)
        layout.addLayout(body, 1)
        self.validation = QLabel('')
        self.validation.setObjectName('scriptLog')
        self.validation.setTextFormat(Qt.TextFormat.PlainText)
        self.validation.setWordWrap(True)
        self.validation.setFixedHeight(self.validation.fontMetrics().lineSpacing() * 2 + 14)
        policy = self.validation.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.validation.setSizePolicy(policy)
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
        save.setObjectName('scriptSaveButton')
        save.clicked.connect(self.save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        self.size_grip = QSizeGrip(self)
        self.size_grip.resize(self.size_grip.sizeHint())
        footer.addSpacing(self.size_grip.width())
        layout.addLayout(footer)
        layout.addWidget(self.validation)
        self.editor.setFocus()
        self.position_size_grip()

    def set_row_context(self, row, number):
        identity = row.move_hk.upper() if row.move_hk else f'{number}행'
        if row.memo.strip():
            identity += ' · ' + row.memo
        self.row_identity.setText(identity)
        self.row_identity.setToolTip(identity)
        self.row_coordinates.setText(f'커서 이동: {row.x},{row.y}')
        self.row_coordinates.setToolTip('해당 행에 저장된 프로필 기준 좌표')
        self.row_context.show()

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
        QApplication.instance().installEventFilter(self)
        self.sync_to_parent()
        self.update_capture_enabled()
        if self.target and self.parentWidget().hook_enabled and self.coordinate_hook is None:
            hook = CoordinateHook(self.coordinate_clicked.emit, self.capture_enabled.is_set,
                                  lambda error: self.capture_failed.emit(hook, str(error)))
            try:
                hook.start()
                self.coordinate_hook = hook
            except Exception as error:
                hook.close()
                self.log(f'좌표 입력 초기화 실패: {error}', error=True)

    def on_capture_failed(self, hook, text):
        if self.isVisible() and self.coordinate_hook is hook:
            hook.close()
            self.coordinate_hook = None
            self.log(f'좌표 입력 감시 실패: {text}', error=True)

    def hideEvent(self, event):
        self.capture_enabled.clear()
        self.capture_blockers.clear()
        QApplication.instance().removeEventFilter(self)
        if self.coordinate_hook is not None:
            self.coordinate_hook.close()
            self.coordinate_hook = None
        super().hideEvent(event)

    def update_capture_enabled(self):
        # Only the GUI thread reads Qt state. The hook reads an Event snapshot.
        parent = self.parentWidget()
        busy = any(getattr(parent, flag, False) for flag in
                   ('_profile_switching', '_waiting_for_write', 'closing'))
        allowed = (self.isVisible() and self.isEnabled() and not busy
                   and bool(self.target) and not self.capture_blockers
                   and not QApplication.activeModalWidget() and not QApplication.activePopupWidget())
        if allowed:
            self.capture_enabled.set()
        else:
            self.capture_enabled.clear()

    def capture_screen_point(self, x, y):
        self.update_capture_enabled()
        if not self.capture_enabled.is_set():
            return
        try:
            self.pick_coordinate(self.native.local_point(self.target, x, y))
        except OSError as error:
            self.log(str(error), error=True)

    def capture_editor_point(self, position):
        self.update_capture_enabled()
        if self.capture_enabled.is_set():
            # Windows Qt screen origins are native pixels; sizes and offsets
            # are device-independent. Use the clicked screen, not the window's
            # screen, so spanning windows and negative monitor origins work.
            screen = QApplication.screenAt(QPoint(floor(position.x()), floor(position.y())))
            if screen is None:
                self.log('클릭한 디스플레이를 찾지 못했습니다. 다시 캡처하세요.', error=True)
                return
            origin = screen.geometry().topLeft()
            ratio = screen.devicePixelRatio()
            self.capture_screen_point(origin.x() + round((position.x() - origin.x()) * ratio),
                                      origin.y() + round((position.y() - origin.y()) * ratio))

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
            source = self.editor.toPlainText()
            if self.capture_resolution is not None and coordinate_tokens(source):
                try:
                    self.validate_capture_profile()
                    identity = self.parentWidget().confirm_coordinate_app(self.target, expected=self.capture_program)
                    if identity is None:
                        return
                    self.validate_capture_profile()
                    self.capture_program = identity
                except (ValueError, OSError) as error:
                    self.log(str(error), error=True)
                    return
            else:
                # Deleted/comment-only coordinates must not impose a stale app
                # requirement or change the profile's existing coordinate basis.
                self.capture_resolution = None
                self.capture_program = None
            self.source = source
            self.accept()

    def log(self, message, error=False):
        if error:
            self.parentWidget().record_error('스크립트 편집', message)
        self.validation.setText(('오류 · ' if error else '') + message)
        self.validation.setToolTip(self.validation.text())
        self.validation.setProperty('error', error)
        self.validation.show()
        self.log_timer.start()
        self.validation.style().unpolish(self.validation)
        self.validation.style().polish(self.validation)

    def insert_template(self, index):
        examples = ['',
            'loop\ncurve_first_time 5\ncurve_last_time 5\ncurve X,Y mouse\nsleep 5\nloop_end',
            'loop\ncurve_first_time 5\ncurve_last_time 5\ncurve mouse X,Y\nsleep 5\nloop_end',
            'loop\ncurve_first_time 5\ncurve_last_time 5\ncurve X,Y X,Y\nsleep 5\nloop_end',
            'loop\nclick mouse\nsleep 3\nclick X,Y\nloop_end']
        if 0 < index < len(examples):
            cursor = self.editor.textCursor()
            cursor.beginEditBlock()
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.insertText(examples[index])
            cursor.endEditBlock()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.editor.setTextCursor(cursor)
            self.templates.setCurrentIndex(0)
            self.log('X,Y를 실제 좌표로 바꾼 뒤 저장하세요.')

    def parent_area(self):
        return self.parentWidget().rect()

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
            top = self.parent_area().top()
            self.syncing_geometry = True
            try:
                parent.resize(event.size().width(), event.size().height() + top)
            finally:
                self.syncing_geometry = False
            self.sync_to_parent()

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self and kind == QEvent.Type.EnabledChange:
            self.update_capture_enabled()
            # Wait helpers restore widgets before clearing their busy flags.
            QTimer.singleShot(0, self, self.update_capture_enabled)
        if isinstance(watched, QWidget):
            if ((kind == QEvent.Type.Show and watched.isWindow()
                 and (watched.windowModality() != Qt.WindowModality.NonModal
                      or watched.windowType() == Qt.WindowType.Popup))
                    or (kind == QEvent.Type.WindowBlocked and watched in (self, self.parentWidget()))):
                self.capture_blockers.add(watched)
                self.capture_enabled.clear()
            elif kind in (QEvent.Type.Hide, QEvent.Type.WindowUnblocked) and watched in self.capture_blockers:
                self.capture_blockers.discard(watched)
                # Qt clears activeModalWidget/activePopupWidget after Hide.
                QTimer.singleShot(0, self, self.update_capture_enabled)
        if watched is self.parentWidget() and kind in (QEvent.Type.Resize, QEvent.Type.Move):
            if not self.syncing_geometry:
                self.sync_to_parent()
        return super().eventFilter(watched, event)

    def validate_capture_profile(self):
        profile = self.parentWidget().profile
        if profile is not self.capture_profile or profile.resolution != self.profile_resolution:
            raise ValueError('프로필 기준 크기가 변경되었습니다. 편집기를 다시 열어 주세요.')
        return profile

    def check_capture_program(self, identity):
        if (self.capture_program is not None and identity != self.capture_program
                and not same_program(self.capture_program, identity)):
            raise ValueError('좌표를 가져온 앱이 변경되었습니다. 편집기를 다시 열어 주세요.')

    def can_edit_coordinates(self):
        try:
            self.validate_capture_profile()
            parent = self.parentWidget()
            if self.target != parent.target:
                raise ValueError('선택한 앱이 변경되었습니다. 편집기를 다시 열어 주세요.')
            if self.target is None:
                return True
            prepared = parent.prepare_manual_coordinate_basis(
                basis=self.capture_resolution or self.profile_resolution, confirm_app=False)
            if prepared is None:
                return False
            size, identity = prepared
            self.check_capture_program(identity)
            # Share the capture draft: cancellation never writes profile metadata.
            self.capture_resolution = size
            self.capture_program = identity
            return True
        except (ValueError, OSError) as error:
            self.log(str(error), error=True)
            return False

    def pick_coordinate(self, point=None):
        if not self.target:
            self.log('앱을 선택한 뒤 편집기를 다시 열어 주세요.', error=True)
            return
        try:
            x, y = point if point is not None else self.native.cursor(self.target)
            l, t, r, b = self.native.bounds(self.target)
            if not (l <= x < r and t <= y < b and abs(x) <= 99999 and abs(y) <= 99999):
                self.log('대상 영역 안의 좌표를 우클릭하세요. 좌표는 최대 5자리입니다.', error=True)
                return
            size = self.native.size(self.target)
            profile = self.validate_capture_profile()
            basis = self.capture_resolution or profile.resolution
            if not basis and profile.has_fixed_coordinates():
                self.log('해상도 기준이 다릅니다. 편집기를 닫고 ☰ → 프로필 해상도 변환을 먼저 진행하세요.', error=True)
                return
            identity = self.native.window_identity(self.target)
            self.check_capture_program(identity)
            warning = ''
            converted = basis is not None and basis != size
            if converted:
                x, y = scaled_point((x, y), size, basis)
                warning = aspect_warning(basis, size)
            if self.native.size(self.target) != size:
                raise ValueError('캡처 중 창 크기가 변경되었습니다. 다시 캡처하세요.')
            self.capture_program = identity
            self.capture_resolution = basis or size
            self.editor.insertPlainText(f'{x},{y}')
            message = f'기준 좌표로 변환하여 추가: {x}, {y}' if converted else f'좌표 추가: {x}, {y}'
            self.log(message + (' · ' + warning if warning else ''))
            self.editor.setFocus()
        except (OSError, ValueError) as error:
            self.log(str(error), error=True)
