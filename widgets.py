"""Reusable Qt widgets, dialogs, and icons; no persistence or input runtime."""
from __future__ import annotations

from dataclasses import asdict


from PySide6.QtCore import Qt, Signal, QTimer, QPoint, QEvent
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QPolygon, QPen
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QTableWidget, QHeaderView, QAbstractItemView,
    QSpinBox, QLineEdit, QDialog, QFormLayout, QDialogButtonBox, QCheckBox, QKeySequenceEdit,
    QStyledItemDelegate, QStatusBar, QTabWidget, QWidget, QFrame,
)

from domain import Settings, hotkey


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


def make_pin_icon():
    icon = QIcon()
    for state, color in ((QIcon.State.Off, "#91a6ba"), (QIcon.State.On, "#47b8ed")):
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


def make_trash_icon():
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#91a6ba'), 2))
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


class MemoDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.textChanged.connect(lambda text: self.parent().resize_memo_column(index.row(), text))
            editor.textChanged.connect(lambda text: self.parent().memo_edited.emit(index.row(), text))
        return editor


class RowsTable(QTableWidget):
    moved = Signal(int, int)
    memo_edited = Signal(int, str)
    content_width_changed = Signal()

    def __init__(self):
        super().__init__(0, 5)
        self.setHorizontalHeaderLabels(["단축키", "X", "Y", "동작", "메모"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setDefaultSectionSize(36)
        self.setShowGrid(False)
        self.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        delegate = MemoDelegate(self)
        delegate.closeEditor.connect(lambda *_: QTimer.singleShot(0, self.resize_memo_column))
        self.setItemDelegateForColumn(4, delegate)
        for column, width in ((0, 72), (1, 60), (2, 60), (3, 122), (4, 70)):
            self.setColumnWidth(column, width)

    def resize_memo_column(self, editing_row=-1, editing_text=""):
        texts = [self.item(i, 4).text() for i in range(self.rowCount())
                 if i != editing_row and self.item(i, 4)]
        texts.append(editing_text)
        width = max((self.fontMetrics().horizontalAdvance(text) + 18 for text in texts), default=70)
        self.setColumnWidth(4, max(70, min(width, 1200)))
        self.content_width_changed.emit()

    def dropEvent(self, event):
        if event.source() is self:
            origin = self.currentRow()
            target = self.indexAt(event.position().toPoint()).row()
            if target < 0:
                target = self.rowCount() - 1
            if origin >= 0:
                self.moved.emit(origin, target)
            event.accept()
        else:
            event.ignore()


class BriefStatusBar(QStatusBar):
    def showMessage(self, message, timeout=4000):
        super().showMessage(message, timeout)


class ClickToCaptureEdit(QKeySequenceEdit):
    """Display a capture prompt without clearing the stored shortcut."""
    def __init__(self, sequence, parent=None):
        super().__init__(sequence, parent)
        self.waiting = False
        self.field = self.findChild(QLineEdit)
        self.installEventFilter(self)
        if self.field is not None:
            self.field.installEventFilter(self)
        self.keySequenceChanged.connect(self.finish_waiting)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.waiting = True
            if self.field is not None:
                self.field.setText("입력 대기중...")
        return super().eventFilter(watched, event)

    def finish_waiting(self, *_):
        if self.waiting:
            self.waiting = False
            if self.field is not None:
                self.field.setText(self.keySequence().toString(QKeySequence.SequenceFormat.NativeText))

    def keyPressEvent(self, event):
        self.finish_waiting()
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.finish_waiting()
        super().focusOutEvent(event)


class KeyDialog(QDialog):
    def __init__(self, value, parent):
        super().__init__(parent)
        self.setWindowTitle("단축키 지정")
        self.setFixedWidth(280)
        layout = QVBoxLayout(self)
        hint = QLabel("입력 칸을 클릭한 뒤\n사용할 키 조합을 누르세요.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.editor = ClickToCaptureEdit(QKeySequence(value), self)
        self.editor.setMaximumSequenceLength(1)
        line_edit = self.editor.findChild(QLineEdit)
        if line_edit is not None:
            line_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.editor)
        clear = QPushButton("지정 해제")
        clear.clicked.connect(self.editor.clear)
        layout.addWidget(clear)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.editor.setFocus()

    def value(self):
        return hotkey(self.editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText))


class SettingsDialog(QDialog):
    def __init__(self, settings, parent, displays=()):
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
        self.displays = QComboBox()
        self.displays.addItem("전체 디스플레이", '')
        for index, display in enumerate(displays, 1):
            self.displays.addItem(f'{index}번 · {display.name} · {display.width}×{display.height}', display.id)
        selected = self.displays.findData(settings.display_id)
        if selected < 0:
            self.displays.addItem('연결되지 않은 디스플레이 · 다시 선택하세요', settings.display_id)
            selected = self.displays.count()-1
        self.displays.setCurrentIndex(selected)
        form.addRow("작동 디스플레이", self.displays)
        self.keys = {}
        for name, label in (("pause_hk", "작동 / 정지"), ("capture_hk", "선택한 행 좌표 캡처")):
            editor = QKeySequenceEdit(QKeySequence(getattr(settings, name)))
            editor.setMaximumSequenceLength(1)
            self.keys[name] = editor
            form.addRow(label, editor)
        self.times = {}
        self.checks = {}
        for name, label in (("move_to_click_delay_ms", "이동 후 클릭 대기시간"), ("click_hold_ms", "클릭 유지 시간"),
                            ("cursor_return_delay_ms", "클릭 후 커서 복귀 대기시간"),
                            ("drag_segment_duration_ms", "curve 드래그 시간")):
            spin = QSpinBox()
            spin.setRange(1 if name == "click_hold_ms" else 0, 10000)
            spin.setValue(getattr(settings, name))
            self.times[name] = spin
            field = QWidget()
            field_layout = QHBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.addWidget(spin, 1)
            field_layout.addWidget(QLabel("ms"))
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
                            ("auto_pause", "설정·편집 창을 열거나 변경할 때 자동 정지")):
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
        data["display_id"] = self.displays.currentData()
        return Settings.read(data)


class ResizeDialog(QDialog):
    PRESETS = {
        "16:9": [(640, 360), (960, 540), (1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160)],
        "4:3": [(640, 480), (800, 600), (1024, 768), (1280, 960), (1600, 1200), (1920, 1440)],
    }

    def __init__(self, size, parent=None):
        super().__init__(parent)
        self.setWindowTitle("대상 창 크기 조절")
        self.form = QFormLayout(self)
        self.ratio = QComboBox()
        self.ratio.addItems(["16:9", "4:3", "직접 입력"])
        self.presets = QComboBox()
        self.width_input, self.height_input = QSpinBox(), QSpinBox()
        for spin, value in zip((self.width_input, self.height_input), size):
            spin.setRange(1, 16384)
            spin.setValue(value)
            spin.setSuffix(" px")
        self.form.addRow("화면 비율", self.ratio)
        self.form.addRow("해상도", self.presets)
        self.form.addRow("너비", self.width_input)
        self.form.addRow("높이", self.height_input)
        self.custom_size = tuple(size)
        self.mode = "직접 입력"
        self.selections = {"16:9": (1280, 720), "4:3": (1024, 768)}
        initial = next((ratio for ratio, sizes in self.PRESETS.items() if tuple(size) in sizes), "직접 입력")
        if initial in self.PRESETS:
            self.selections[initial] = tuple(size)
        self.ratio.setCurrentText(initial)
        self.ratio.currentTextChanged.connect(self.change_ratio)
        self.presets.currentIndexChanged.connect(self.change_preset)
        self.change_ratio(initial)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("적용")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.form.addRow(buttons)

    def change_ratio(self, mode):
        if self.mode == "직접 입력":
            self.custom_size = self.value()
        self.mode = mode
        custom = mode == "직접 입력"
        self.form.setRowVisible(self.presets, not custom)
        self.width_input.setEnabled(custom)
        self.height_input.setEnabled(custom)
        if custom:
            size = self.custom_size
        else:
            self.presets.blockSignals(True)
            self.presets.clear()
            for w, h in self.PRESETS[mode]:
                self.presets.addItem(f"{w} × {h}", (w, h))
            size = self.selections[mode]
            self.presets.setCurrentIndex(self.PRESETS[mode].index(size))
            self.presets.blockSignals(False)
        self.width_input.setValue(size[0])
        self.height_input.setValue(size[1])

    def change_preset(self, index):
        if index < 0:
            return
        size = self.PRESETS[self.mode][index]
        self.selections[self.mode] = size
        self.width_input.setValue(size[0])
        self.height_input.setValue(size[1])

    def value(self):
        return self.width_input.value(), self.height_input.value()


