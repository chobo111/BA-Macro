"""Reusable Qt widgets, dialogs, and icons; no persistence or input runtime."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from PySide6.QtCore import Qt, Signal, QTimer, QPoint, QEvent, QSignalBlocker, QKeyCombination, QPersistentModelIndex
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QPolygon, QPen, QValidator
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QHeaderView, QAbstractItemView,
    QSpinBox, QLineEdit, QDialog, QFormLayout, QDialogButtonBox, QCheckBox, QKeySequenceEdit,
    QStyledItemDelegate, QStatusBar, QTabWidget, QWidget, QFrame, QApplication,
)

from domain import Settings
from shortcuts import hotkey, qt_hotkey, SHORTCUT_GUIDE
from row_drag import AnimatedRowsTable


@contextmanager
def managed_dialog(dialog):
    """Keep retrying dialogs alive until their caller finishes, then release Qt ownership."""
    try:
        yield dialog
    finally:
        dialog.deleteLater()


def make_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#47b8ed"))
    painter.drawRoundedRect(2, 2, 60, 60, 16, 16)
    painter.setBrush(QColor("#102435"))
    painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in [(19, 13), (47, 33), (34, 36), (28, 51)]]))
    painter.end()
    return QIcon(pixmap)


def make_run_icon(running):
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#102b1a' if running else '#ffffff'))
    if running:
        painter.drawRoundedRect(7, 7, 18, 18, 2, 2)
    else:
        painter.drawPolygon(QPolygon([QPoint(8, 5), QPoint(27, 16), QPoint(8, 27)]))
    painter.end()
    return QIcon(pixmap)


def make_pin_icon(muted, accent):
    icon = QIcon()
    for state, color in ((QIcon.State.Off, muted), (QIcon.State.On, accent)):
        pixmap = QPixmap(48, 48)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(24, 24)
        painter.rotate(35)
        painter.setPen(QPen(QColor(color), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(0, 6, 0, 19)
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in
            [(-9, -17), (9, -17), (6, -12), (6, -3), (11, 4), (-11, 4), (-6, -3), (-6, -12)]]))
        painter.end()
        icon.addPixmap(pixmap, QIcon.Mode.Normal, state)
    return icon


def make_trash_icon(color):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 2))
    painter.drawLine(5, 6, 19, 6)
    painter.drawLine(9, 3, 15, 3)
    painter.drawLine(7, 9, 8, 21)
    painter.drawLine(17, 9, 16, 21)
    painter.drawLine(8, 21, 16, 21)
    painter.drawLine(10, 10, 10, 17)
    painter.drawLine(14, 10, 14, 17)
    painter.end()
    return QIcon(pixmap)


class NoWheelCombo(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class TargetCombo(NoWheelCombo):
    opening = Signal()

    def showPopup(self):
        self.opening.emit()
        super().showPopup()


class MemoLengthValidator(QValidator):
    LIMIT = 1000

    def __init__(self, original, parent):
        super().__init__(parent)
        # Existing long notes remain editable and undoable without growing.
        self.limit = max(self.LIMIT, len(original))

    def validate(self, text, position):
        state = self.State.Acceptable if len(text) <= self.limit else self.State.Invalid
        return state, text, position


class MemoDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            original = index.data(Qt.ItemDataRole.EditRole) or ''
            editor.setProperty('originalMemo', original)
            index = QPersistentModelIndex(index)
            editor.setProperty('memoIndex', index)
            # Validate Unicode characters instead of truncating Qt's UTF-16 text.
            # Programmatic initialization must preserve even old, very long notes.
            editor.setMaxLength(2**31 - 1)
            editor.setValidator(MemoLengthValidator(original, editor))
            editor.textEdited.connect(lambda text: self.parent().memo_edited.emit(index.row(), text))
            notice = f'메모는 최대 {MemoLengthValidator.LIMIT:,}자까지 입력할 수 있습니다.'
            if len(original) > MemoLengthValidator.LIMIT:
                notice = f'기존 메모가 {MemoLengthValidator.LIMIT:,}자를 초과합니다. 원문은 유지되며 더 늘릴 수 없습니다.'
                self.parent().memo_notice.emit(notice)
            editor.inputRejected.connect(lambda: self.parent().memo_notice.emit(notice))
        return editor

    def eventFilter(self, editor, event):
        if (isinstance(editor, QLineEdit) and event.type() == QEvent.Type.KeyPress
                and event.matches(QKeySequence.StandardKey.Cancel)):
            # Live edits are already autosaved. Roll back the same way before
            # Qt closes the editor with RevertModelCache.
            original = editor.property('originalMemo') or ''
            if editor.text() != original:
                editor.setText(original)
                # setText is intentionally excluded from live autosave; cancellation
                # explicitly restores the original through the same ordered writer.
                index = editor.property('memoIndex')
                if index.isValid():
                    self.parent().memo_edited.emit(index.row(), original)
        return super().eventFilter(editor, event)


class RowsTable(AnimatedRowsTable):
    memo_edited = Signal(int, str)
    memo_notice = Signal(str)
    MEMO_MIN_WIDTH = 70

    def __init__(self):
        super().__init__(0, 5)
        self.setHorizontalHeaderLabels(["단축키", "X", "Y", "동작", "메모"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setDefaultSectionSize(36)
        self.setShowGrid(False)
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        delegate = MemoDelegate(self)
        delegate.closeEditor.connect(lambda *_: QTimer.singleShot(0, self, self.update_memo_tooltips))
        self.setItemDelegateForColumn(4, delegate)
        for column, width in ((0, 72), (1, 60), (2, 60), (3, 122), (4, 70)):
            self.setColumnWidth(column, width)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def setCellWidget(self, row, column, widget):
        super().setCellWidget(row, column, widget)
        for control in (widget, *widget.findChildren(QLineEdit)):
            control.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            control.customContextMenuRequested.connect(
                lambda point, control=control: self.customContextMenuRequested.emit(
                    self.viewport().mapFromGlobal(control.mapToGlobal(point))))

    def update_memo_tooltips(self):
        # Long notes stay within the existing window; show full text on hover.
        with QSignalBlocker(self):
            for index in range(self.rowCount()):
                item = self.item(index, 4)
                if item is not None:
                    item.setToolTip(item.text())



class BriefStatusBar(QStatusBar):
    error_raised = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._save_message = False

    def showMessage(self, message, timeout=4000):
        self._save_message = False
        super().showMessage(message, timeout)

    def show_error(self, message, source='일반'):
        self.error_raised.emit(source, str(message))
        self.showMessage(str(message))

    def show_save_result(self, message, *, failed=False):
        # Success must not replace an action notice or restart its expiry timer.
        # A retry may replace its own save failure; failures are always visible.
        if failed:
            self.error_raised.emit('저장', message)
        if failed or self._save_message or not self.currentMessage():
            self.showMessage(message)
            self._save_message = True


class ClickToCaptureEdit(QKeySequenceEdit):
    """Capture one chord before Qt handles dialog navigation or default buttons."""
    MODIFIER_KEYS = {
        Qt.Key.Key_Control: Qt.KeyboardModifier.ControlModifier,
        Qt.Key.Key_Shift: Qt.KeyboardModifier.ShiftModifier,
        Qt.Key.Key_Alt: Qt.KeyboardModifier.AltModifier,
        Qt.Key.Key_Meta: Qt.KeyboardModifier.MetaModifier,
    }
    MODIFIER_MASK = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
                     | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)

    def __init__(self, sequence, parent=None):
        super().__init__(sequence, parent)
        self.pending_modifier = None
        self.setMaximumSequenceLength(1)
        self.setFinishingKeyCombinations([])
        self.field = self.findChild(QLineEdit)
        self.installEventFilter(self)
        if self.field is not None:
            self.field.setReadOnly(True)
            self.field.installEventFilter(self)
        self.keySequenceChanged.connect(self.finish_waiting)
        self.finish_waiting()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            self.capture_key(event)
            event.accept()
            return True
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.pending_modifier = None
            if self.field is not None:
                self.field.setText('입력 대기중...')
        elif event.type() == QEvent.Type.FocusOut:
            self.pending_modifier = None
            self.finish_waiting()
        return super().eventFilter(watched, event)

    def capture_key(self, event):
        if event.isAutoRepeat():
            return
        key = event.key()
        if event.type() == QEvent.Type.KeyRelease:
            if key == self.pending_modifier:
                self.pending_modifier = None
                self.setKeySequence(QKeySequence(key))
                self.finish_waiting()
            return
        modifiers = event.modifiers() & self.MODIFIER_MASK
        if key in self.MODIFIER_KEYS:
            # Do not record a leading Ctrl as a standalone key when Ctrl+Q follows.
            other_modifiers = modifiers & ~self.MODIFIER_KEYS[key]
            self.pending_modifier = key if not other_modifiers else None
            return
        self.pending_modifier = None
        if key == Qt.Key.Key_unknown:
            return
        if key == Qt.Key.Key_Backtab:
            key = Qt.Key.Key_Tab
            modifiers |= Qt.KeyboardModifier.ShiftModifier
        self.setKeySequence(QKeySequence(QKeyCombination(modifiers, Qt.Key(key))))
        self.finish_waiting()

    def finish_waiting(self, *_):
        if self.field is not None:
            # Keep Qt's key identity (standalone Control) intact; normalize display only.
            label = self.keySequence().toString(QKeySequence.SequenceFormat.NativeText)
            self.field.setText(label.replace('Control', 'Ctrl'))


class KeyDialog(QDialog):
    def __init__(self, value, parent):
        super().__init__(parent)
        self.setWindowTitle("단축키 지정")
        self.setFixedWidth(280)
        layout = QVBoxLayout(self)
        self.editor = ClickToCaptureEdit(QKeySequence(qt_hotkey(value), QKeySequence.SequenceFormat.PortableText), self)
        line_edit = self.editor.findChild(QLineEdit)
        if line_edit is not None:
            line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.editor)
        guide = QLabel(SHORTCUT_GUIDE)
        guide.setWordWrap(True)
        layout.addWidget(guide)
        clear = QPushButton("지정 해제")
        clear.clicked.connect(self.editor.clear)
        layout.addWidget(clear)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('확인')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('취소')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.editor.setFocus()

    def value(self):
        return hotkey(self.editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText))


class SettingsDialog(QDialog):
    def __init__(self, settings, parent):
        super().__init__(parent)
        self.setWindowTitle("환경설정")
        self.setMinimumWidth(470)
        self.original = settings
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        forms = {}
        for label in ("일반", "동작 / 스크립트"):
            page = QWidget()
            form_layout = QFormLayout(page)
            form_layout.setFormAlignment(Qt.AlignmentFlag.AlignTop)
            forms[label] = form_layout
            self.tabs.addTab(page, label)
        form = forms["일반"]
        self.keys = {}
        for name, label in (("pause_hk", "작동 / 정지"), ("capture_hk", "선택한 행 좌표 캡처")):
            editor = ClickToCaptureEdit(QKeySequence(qt_hotkey(getattr(settings, name)), QKeySequence.SequenceFormat.PortableText))
            self.keys[name] = editor
            form.addRow(label, editor)
        guide = QLabel(SHORTCUT_GUIDE)
        guide.setWordWrap(True)
        form.addRow(guide)
        self.times = {}
        self.checks = {}
        for name, label in (("move_to_click_delay_ms", "이동 후 클릭 대기시간"), ("click_hold_ms", "클릭 유지 시간"),
                            ("cursor_return_delay_ms", "클릭 후 커서 복귀 대기시간"),
                            ("drag_segment_duration_ms", "curve 드래그 시간")):
            spin = QSpinBox()
            spin.setRange(1 if name == "click_hold_ms" else 0, 99999)
            spin.setValue(getattr(settings, name))
            spin.setFixedWidth(spin.fontMetrics().horizontalAdvance("10000") + spin.fontMetrics().horizontalAdvance("0") + 36)
            self.times[name] = spin
            field = QWidget()
            field_layout = QHBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(6)
            field_layout.addWidget(spin)
            field_layout.addWidget(QLabel("ms"))
            field_layout.addStretch()
            action_form = forms["동작 / 스크립트"]
            if name == "cursor_return_delay_ms":
                return_cursor = QCheckBox("클릭 후 원래 커서 위치로 복귀")
                return_cursor.setChecked(settings.return_cursor)
                return_cursor.toggled.connect(field.setEnabled)
                field.setEnabled(settings.return_cursor)
                self.checks["return_cursor"] = return_cursor
                action_form.addRow(return_cursor)
            if name == "drag_segment_duration_ms":
                separator = QFrame()
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setFrameShadow(QFrame.Shadow.Sunken)
                action_form.addRow(separator)
            action_form.addRow(label, field)
        for name, label in (("exclusive_hook", "처리한 단축키를 다른 앱에 전달하지 않기"),
                            ("auto_pause", "설정·편집 창을 열거나 변경할 때 자동 정지"),
                            ("auto_convert_on_start", "창 크기가 다르면 묻지 않고 자동 변환")):
            checkbox = QCheckBox(label)
            checkbox.setChecked(getattr(settings, name))
            self.checks[name] = checkbox
            form.addRow(checkbox)
        self.theme = QComboBox()
        self.theme.addItems(["Dark", "Light", "System"])
        self.theme.setCurrentText(settings.theme)
        form.addRow("테마", self.theme)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('저장')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('취소')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self):
        data = asdict(self.original)
        data.update({name: editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                     for name, editor in self.keys.items()})
        data.update({name: spin.value() for name, spin in self.times.items()})
        data.update({name: checkbox.isChecked() for name, checkbox in self.checks.items()})
        data["theme"] = self.theme.currentText()
        return Settings.read(data)


class ResizeDialog(QDialog):
    resizing = Signal()

    PRESETS = {
        "16:9": [(640, 360), (960, 540), (1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160)],
        "4:3": [(640, 480), (800, 600), (1024, 768), (1280, 960), (1600, 1200), (1920, 1440)],
    }

    def __init__(self, size, parent=None, *, convert_profile=True, request_resize=None, read_size=None):
        super().__init__(parent)
        self.setWindowTitle("대상 창 크기 조절")
        self.resize(420, 230)
        self.current_size = tuple(size)
        self.request_resize, self.read_size = request_resize, read_size
        self.pending = False
        self.resize_future = self.resize_executor = None
        self.refresh_notice = ''
        QApplication.instance().aboutToQuit.connect(self.wait_for_request)
        layout = QVBoxLayout(self)
        self.current_label = QLabel()
        layout.addWidget(self.current_label)
        modes = QHBoxLayout()
        self.mode_buttons = {}
        for mode in ("16:9", "4:3", "직접 입력"):
            button = QPushButton(mode)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, value=mode: self.change_ratio(value))
            self.mode_buttons[mode] = button
            modes.addWidget(button)
        layout.addLayout(modes)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.presets = NoWheelCombo()
        self.form.addRow("해상도", self.presets)
        self.custom_fields = QWidget()
        fields = QHBoxLayout(self.custom_fields)
        fields.setContentsMargins(0, 0, 0, 0)
        self.width_input, self.height_input = NoWheelSpinBox(), NoWheelSpinBox()
        for spin, value in zip((self.width_input, self.height_input), size):
            spin.setRange(1, 16384)
            spin.setValue(value)
            spin.setFixedWidth(spin.fontMetrics().horizontalAdvance("00000") + 36)
        fields.addWidget(self.width_input)
        fields.addWidget(QLabel("×"))
        fields.addWidget(self.height_input)
        fields.addWidget(QLabel("px"))
        self.aspect_lock = QCheckBox("비율 고정")
        fields.addWidget(self.aspect_lock)
        fields.addStretch()
        self.form.addRow("크기", self.custom_fields)
        self.convert_profile = QCheckBox("변경 후 프로필 해상도 변환")
        self.convert_profile.setChecked(convert_profile)
        layout.addWidget(self.convert_profile)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText("적용")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.buttons.accepted.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.custom_size = tuple(size)
        self.mode = "직접 입력"
        self.selections = {"16:9": (1280, 720), "4:3": (1024, 768)}
        self.lock_ratio = tuple(size)
        self.updating = False
        self.aspect_lock.toggled.connect(self.set_aspect_lock)
        self.width_input.valueChanged.connect(lambda value: self.dimension_changed(0))
        self.height_input.valueChanged.connect(lambda value: self.dimension_changed(1))
        self.presets.currentIndexChanged.connect(self.change_preset)
        self.verify_timer = QTimer(self)
        self.verify_timer.setInterval(100)
        self.verify_timer.timeout.connect(self.verify_resize)
        initial = next((ratio for ratio, sizes in self.PRESETS.items() if tuple(size) in sizes), "직접 입력")
        if initial in self.PRESETS:
            self.selections[initial] = tuple(size)
        self.change_ratio(initial)

    def update_state(self):
        self.current_label.setText('현재 %d × %d · 내부 화면' % self.current_size)
        self.apply_button.setEnabled(not self.pending and self.value() != self.current_size)

    def set_aspect_lock(self, enabled):
        if enabled:
            self.lock_ratio = self.value()

    def dimension_changed(self, axis):
        if self.updating:
            return
        if self.mode == '직접 입력' and self.aspect_lock.isChecked():
            self.updating = True
            try:
                w, h = self.lock_ratio
                if axis == 0:
                    value = max(1, min(16384, round(self.width_input.value() * h / w)))
                    self.height_input.setValue(value)
                    if round(self.width_input.value() * h / w) > 16384:
                        self.width_input.setValue(max(1, round(value * w / h)))
                else:
                    value = max(1, min(16384, round(self.height_input.value() * w / h)))
                    self.width_input.setValue(value)
                    if round(self.height_input.value() * w / h) > 16384:
                        self.height_input.setValue(max(1, round(value * h / w)))
            finally:
                self.updating = False
        self.update_state()

    def set_size(self, size):
        self.updating = True
        try:
            self.width_input.setValue(size[0])
            self.height_input.setValue(size[1])
        finally:
            self.updating = False
        self.update_state()

    def change_ratio(self, mode):
        if self.mode == "직접 입력":
            self.custom_size = self.value()
        self.mode = mode
        custom = mode == "직접 입력"
        for label, button in self.mode_buttons.items():
            button.setChecked(label == mode)
        self.form.setRowVisible(self.presets, not custom)
        self.form.setRowVisible(self.custom_fields, custom)
        self.width_input.setEnabled(custom)
        self.height_input.setEnabled(custom)
        if custom:
            self.set_size(self.custom_size)
        else:
            self.presets.blockSignals(True)
            self.presets.clear()
            for w, h in self.PRESETS[mode]:
                self.presets.addItem(f"{w} × {h}", (w, h))
            size = self.selections[mode]
            self.presets.setCurrentIndex(self.PRESETS[mode].index(size))
            self.presets.blockSignals(False)
            self.set_size(size)

    def change_preset(self, index):
        if index < 0:
            return
        size = self.presets.itemData(index)
        self.selections[self.mode] = size
        self.set_size(size)

    def value(self):
        return self.width_input.value(), self.height_input.value()

    def show_message(self, text, *, error=False):
        self.message.setText(text)
        self.message.setVisible(bool(text))
        parent = self.parentWidget()
        if error and parent is not None and hasattr(parent, 'record_error'):
            parent.record_error('창 크기', text)

    def set_pending(self, pending):
        self.pending = pending
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setEnabled(not pending)
        for button in self.mode_buttons.values():
            button.setEnabled(not pending)
        self.presets.setEnabled(not pending)
        self.custom_fields.setEnabled(not pending)
        self.convert_profile.setEnabled(not pending)
        self.update_state()

    def apply(self):
        if self.pending:
            return
        self.width_input.interpretText()
        self.height_input.interpretText()
        self.requested_size = self.value()
        if self.requested_size == self.current_size:
            return
        if self.request_resize is None:
            self.accept()
            return
        self.set_pending(True)
        self.show_message('실제 창 크기를 확인하고 있습니다…')
        try:
            self.resizing.emit()
            self.refresh_notice = ''
            self.resize_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='window-resize')
            self.resize_future = self.resize_executor.submit(self.request_resize, self.requested_size)
            self.attempts = self.matches = 0
            self.verify_timer.start()
        except Exception as error:
            self.release_executor()
            self.set_pending(False)
            self.show_message(str(error), error=True)

    def release_executor(self):
        if self.resize_executor is not None:
            self.resize_executor.shutdown(wait=False)
            self.resize_executor = None

    def wait_for_request(self):
        # Finish native cursor/focus cleanup before the application exits.
        if self.resize_future is not None:
            try:
                self.resize_future.result()
            except Exception:
                pass  # verify_resize reports failures while the dialog is open.
        self.release_executor()

    def verify_resize(self):
        if not self.pending:
            return
        try:
            if self.resize_future is not None:
                if not self.resize_future.done():
                    return
                completed, self.resize_future = self.resize_future, None
                self.release_executor()
                self.refresh_notice = completed.result() or ''
            actual = tuple(self.read_size())
            self.current_size = actual
            self.attempts += 1
            self.matches = self.matches + 1 if actual == self.requested_size else 0
            self.update_state()
            if self.matches >= 2:
                self.verify_timer.stop()
                self.set_pending(False)
                self.accept()
            elif self.attempts >= 10:
                self.verify_timer.stop()
                self.set_pending(False)
                requested = '%d × %d' % self.requested_size
                measured = '%d × %d' % actual
                self.show_message(f'요청한 크기로 변경되지 않았습니다. 요청 {requested} / 실제 {measured}', error=True)
        except Exception as error:
            self.verify_timer.stop()
            self.set_pending(False)
            self.show_message(str(error), error=True)

    def reject(self):
        if not self.pending:
            self.verify_timer.stop()
            super().reject()
