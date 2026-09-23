from __future__ import annotations

from dataclasses import asdict, replace
import os
from pathlib import Path
import sys

from PySide6.QtCore import Qt, Signal, QObject, QTimer, QStandardPaths, QPoint, QLockFile, QSize
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QPolygon, QPen
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSpinBox, QLineEdit, QFileDialog, QMessageBox, QDialog, QFormLayout,
    QDialogButtonBox, QCheckBox, QKeySequenceEdit, QSystemTrayIcon, QMenu, QGroupBox, QStyledItemDelegate, QSizePolicy, QStatusBar,
)

from domain import MODES, Profile, Row, Settings, atomic_json, read_json, hotkey
from engine import Engine, Command
from native import Windows
from hotkeys import Hotkeys
from script_language import compile_script
from script_editor import ScriptEditor

BASE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
MODE_LABELS = MODES


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


class Bridge(QObject):
    action = Signal(str, str)
    error = Signal(str)


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


class TargetCombo(QComboBox):
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


class KeyDialog(QDialog):
    def __init__(self, value, parent):
        super().__init__(parent)
        self.setWindowTitle("단축키 지정")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("입력 칸을 클릭한 뒤 사용할 키 조합을 누르세요."))
        self.editor = QKeySequenceEdit(QKeySequence(value), self)
        self.editor.setMaximumSequenceLength(1)
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
    def __init__(self, settings, parent):
        super().__init__(parent)
        self.setWindowTitle("환경설정")
        self.setMinimumWidth(470)
        self.original = settings
        form = QFormLayout(self)
        self.keys = {}
        for name, label in (("pause_hk", "작동 / 정지"), ("capture_hk", "선택한 행 좌표 캡처")):
            editor = QKeySequenceEdit(QKeySequence(getattr(settings, name)))
            editor.setMaximumSequenceLength(1)
            self.keys[name] = editor
            form.addRow(label, editor)
        self.times = {}
        for name, label in (("click_delay", "이동 후 클릭 대기"), ("click_duration", "클릭 유지 시간"),
                            ("curve_duration", "드래그 이동 시간 (좌표 구간당)"),
                            ("after_action_delay", "동작 완료 후 대기")):
            spin = QSpinBox()
            spin.setRange(1 if name == "click_duration" else 0, 10000)
            spin.setSuffix(" ms")
            spin.setValue(getattr(settings, name))
            self.times[name] = spin
            form.addRow(label, spin)
        self.checks = {}
        for name, label in (("exclusive_hook", "처리한 단축키를 다른 앱에 전달하지 않기"),
                            ("auto_pause", "설정·편집 창을 열거나 변경할 때 자동 중지")):
            checkbox = QCheckBox(label)
            checkbox.setChecked(getattr(settings, name))
            self.checks[name] = checkbox
            form.addRow(checkbox)
        self.theme = QComboBox()
        self.theme.addItems(["Dark", "Light", "System"])
        self.theme.setCurrentText(settings.theme)
        form.addRow("테마", self.theme)
        note = QLabel("전체화면은 화면 좌표로, 창 선택 시 해당 창이 활성화된 동안 실행됩니다.\n자동 중지가 켜져 있을 때는 설정·편집 후 다시 시작하세요.")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('저장')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('취소')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def value(self):
        data = asdict(self.original)
        data.update({name: editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                     for name, editor in self.keys.items()})
        data.update({name: spin.value() for name, spin in self.times.items()})
        data.update({name: checkbox.isChecked() for name, checkbox in self.checks.items()})
        data["theme"] = self.theme.currentText()
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


class MainWindow(QMainWindow):
    def __init__(self, data_dir=None, hook_enabled=True):
        super().__init__()
        self.setWindowTitle("Blue Archive Studio")
        self.setWindowIcon(make_icon())
        self.resize(420, 420)
        self.setMinimumHeight(340)
        self.native = Windows()
        self.armed = False
        self.modal = False
        self.closing = False
        self.target = 0
        self.editing = False
        self.dirty = False
        self.rebuilding = False
        self.path = None
        self.data_dir = Path(data_dir or BASE).resolve()
        self.recent_copies = {}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "setting.json"
        self.settings = Settings()
        self.profile = Profile.default()
        self.load_error = ""
        self.hook_enabled = hook_enabled
        try:
            if self.settings_path.exists():
                self.settings = Settings.read(read_json(self.settings_path))
            recent = self.data_dir / Path(self.settings.last_profile).name if self.settings.last_profile else None
            self.path = recent if recent and recent.is_file() else self.data_dir / "macro.json"
            if self.path.exists():
                self.profile = Profile.read(read_json(self.path))
            else:
                atomic_json(self.path, self.profile.export())
            self.profile.validate((self.settings.capture_hk, self.settings.pause_hk))
            self.persist_settings()
        except Exception as error:
            self.load_error = f"설정을 불러오지 못해 기본값으로 시작했습니다: {error}"
            self.settings, self.profile, self.path = Settings(), Profile.default(), None
        self.bridge = Bridge()
        self.engine = Engine(self.native, lambda key: self.hooks.held(key), self.bridge.error.emit)
        self.hooks = Hotkeys(self.bridge.action.emit, self.eligible, self.engine.stop)
        self.bridge.action.connect(self.dispatch, Qt.ConnectionType.QueuedConnection)
        self.bridge.error.connect(self.on_engine_error, Qt.ConnectionType.QueuedConnection)
        self.setStatusBar(BriefStatusBar(self))
        self.build_ui()
        self.build_menu()
        self.build_tray()
        self.apply_theme()
        self.rebuild()
        self.refresh_windows()
        try:
            self.rebind()
            if hook_enabled:
                self.hooks.start()
        except Exception as error:
            self.load_error = f"단축키 초기화 실패: {error}"
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.statusBar().showMessage(self.load_error)

    def button(self, text, callback, primary=False):
        button = QPushButton(text)
        button.clicked.connect(callback)
        if primary:
            button.setObjectName("primary")
        return button

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(6)
        target_box = QGroupBox("대상 창")
        target_layout = QHBoxLayout(target_box)
        target_layout.setContentsMargins(6, 10, 6, 4)
        target_layout.setSpacing(6)
        self.targets = TargetCombo()
        self.targets.opening.connect(self.refresh_windows)
        self.targets.setMinimumWidth(120)
        self.targets.setMaximumWidth(280)
        self.targets.setMinimumContentsLength(10)
        self.targets.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.targets.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.targets.view().setMinimumWidth(360)
        self.targets.currentIndexChanged.connect(self.change_target)
        target_layout.addWidget(self.targets, 1)
        target_layout.addWidget(self.button("창 크기", self.resize_dialog))
        self.target_info = QLabel("대상 창을 선택하세요")
        self.target_info.setMinimumWidth(0)
        self.target_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        target_layout.addWidget(self.target_info, 1)
        layout.addWidget(target_box)

        profile_bar = QHBoxLayout()
        profile_bar.setSpacing(6)
        self.cursor_label = QLabel("— , —")
        self.cursor_label.setObjectName("cursor")
        self.cursor_label.setFixedWidth(128)
        cursor_policy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        cursor_policy.setRetainSizeWhenHidden(True)
        self.cursor_label.setSizePolicy(cursor_policy)
        profile_bar.addWidget(self.cursor_label)
        self.pin_button = QPushButton()
        self.pin_button.setIcon(make_pin_icon())
        self.pin_button.setIconSize(QSize(20, 20))
        self.pin_button.setFixedSize(32, 30)
        self.pin_button.setAccessibleName("항상 위에 표시")
        self.pin_button.setCheckable(True)
        self.pin_button.setToolTip("다른 창보다 항상 위에 표시")
        self.pin_button.toggled.connect(self.pin)
        profile_bar.addWidget(self.pin_button)
        self.pause_button = self.button("▶ 작동 시작", self.toggle, True)
        self.pause_button.setFixedWidth(106)
        profile_bar.addWidget(self.pause_button)
        profile_bar.addStretch()
        layout.addLayout(profile_bar)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        for text, name, callback in (("+", "행 추가", self.add_row), ("", "선택 삭제", self.delete_rows)):
            button = self.button(text, callback)
            button.setFixedSize(32, 30)
            button.setAccessibleName(name)
            button.setToolTip(name)
            if not text:
                button.setIcon(make_trash_icon())
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.table = RowsTable()
        self.table.itemChanged.connect(self.memo_changed)
        self.table.memo_edited.connect(self.live_memo_changed)
        self.table.moved.connect(self.move_row)
        self.table.content_width_changed.connect(self.fit_content_width, Qt.ConnectionType.QueuedConnection)
        self.table.horizontalHeader().sectionResized.connect(self.queue_fit_content_width)
        self.table.verticalScrollBar().rangeChanged.connect(self.queue_fit_content_width)
        layout.addWidget(self.table, 1)
        self.update_labels()

    def queue_fit_content_width(self, *_):
        QTimer.singleShot(0, self.fit_content_width)

    def fit_content_width(self):
        if self.closing:
            return
        table = self.table
        margins = self.centralWidget().layout().contentsMargins()
        width = (table.horizontalHeader().length() + table.verticalHeader().width()
                 + table.frameWidth() * 2 + margins.left() + margins.right())
        if table.verticalScrollBar().maximum() > 0:
            width += table.verticalScrollBar().sizeHint().width()
        screen = self.screen()
        if screen:
            frame = max(0, self.frameGeometry().width() - self.geometry().width())
            width = min(width, screen.availableGeometry().width() - frame)
        previous = getattr(self, "_content_width", None)
        self._content_width = width
        self.setMinimumWidth(400)
        if previous is None or (width > previous and width > self.width()):
            self.resize(width, self.height())

    def build_menu(self):
        file_menu = self.menuBar().addMenu("파일")
        for text, callback in (("새 프로필", self.new_profile), ("불러오기", self.open_profile),
                               ("저장", self.save), ("다른 이름으로 저장…", self.save_as)):
            file_menu.addAction(text, callback)
        tools = self.menuBar().addMenu("설정")
        tools.addAction("환경설정", self.settings_dialog)
        tools.addAction("해상도에 맞춰 좌표 변환…", self.convert_dialog)
        about = self.menuBar().addMenu("도움말")
        about.addAction("사용 방법", self.help_dialog)

    def build_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Blue Archive Studio · 일시정지")
        menu = QMenu(self)
        menu.addAction("창 열기", self.restore)
        menu.addAction("작동 / 정지", self.toggle)
        menu.addSeparator()
        menu.addAction("종료", self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def apply_theme(self):
        dark = self.settings.theme == "Dark" or (self.settings.theme == "System" and
                QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        bg, panel, text, muted, border, alt = (("#101923", "#192634", "#e6eff8", "#91a6ba", "#304456", "#1d2d3e") if dark
                else ("#f0f5fa", "#ffffff", "#172b40", "#526d85", "#cddbe7", "#f5f8fc"))
        self.setStyleSheet(f"""
            QMainWindow, QDialog {{background: {bg}; color: {text};}}
            QWidget {{font-family: 'Segoe UI', 'Malgun Gothic'; font-size: 13px; color: {text};}}
            QLabel {{background: transparent;}}
            QLabel#muted {{color: {muted};}}
            QLabel#cursor {{color: #47b8ed; font-family: Consolas; font-weight: 600;}}
            QGroupBox {{background: {panel}; border: 1px solid {border}; border-radius: 6px; margin-top: 8px; padding: 4px 2px 2px;}}
            QGroupBox::title {{subcontrol-origin: margin; left: 15px; padding: 0 5px; color: {muted};}}
            QPushButton {{background: {panel}; border: 1px solid {border}; border-radius: 5px; padding: 4px 7px;}}
            QPushButton:hover {{border-color: #47b8ed; background: {alt};}}
            QPushButton:pressed {{background: {border};}}
            QPushButton:checked {{background: #285275; color: #ffffff; border-color: #47b8ed;}}
            QPushButton#primary {{background: #47b8ed; color: #102435; border: none; font-weight: 700;}}
            QTableWidget {{background: {panel}; alternate-background-color: {alt}; border: 1px solid {border}; border-radius: 8px; selection-background-color: #285275;}}
            QTableWidget::item {{padding: 4px;}}
            QHeaderView {{background: {bg};}}
            QHeaderView::section {{background: {bg}; color: {muted}; padding: 6px 3px; border: none;}}
            QTableCornerButton::section {{background: {bg}; border: none;}}
            QLineEdit, QSpinBox, QComboBox, QKeySequenceEdit {{background: {panel}; border: 1px solid {border}; border-radius: 4px; padding: 3px; selection-background-color: #28638a;}}
            QComboBox QAbstractItemView {{background: {panel}; selection-background-color: #285275;}}
            QMenuBar, QMenu, QStatusBar {{background: {bg}; color: {muted};}}
            QMenu::item:selected {{background: {border};}}
            QToolTip {{background: {panel}; color: {text}; border: 1px solid {border};}}
        """)

    def update_labels(self):
        name = self.path.stem if self.path else "새 프로필"
        self.pause_button.setText("■ 일시정지" if self.armed else "▶ 작동 시작")
        self.setWindowTitle(f"{'● ' if self.dirty else ''}{name} — Blue Archive Studio")
        if hasattr(self, "tray"):
            self.tray.setToolTip("Blue Archive Studio · " + ("작동 중" if self.armed else "일시정지"))

    def changed(self):
        self.pause_for_configuration()
        self.dirty = True
        self.update_labels()
        return self.save(automatic=True)

    def row(self, row_id):
        return next((r for r in self.profile.rows if r.id == row_id), None)

    def rebuild(self, selected=None):
        self.rebuilding = True
        self.table.clearSpans()
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.profile.rows))
        for i, row in enumerate(self.profile.rows):
            item = QTableWidgetItem(row.memo)
            item.setData(Qt.ItemDataRole.UserRole, row.id)
            self.table.setItem(i, 4, item)
            button = self.button(row.move_hk.upper() or "지정", lambda checked=False, rid=row.id: self.edit_key(rid))
            self.table.setCellWidget(i, 0, button)
            if row.action_mode == 'script':
                self.table.setSpan(i, 1, 1, 2)
                script_button = self.button('스크립트 작성', lambda checked=False, rid=row.id: self.edit_script(rid))
                script_button.setToolTip('스크립트 편집' if row.script else '실행할 스크립트를 작성하세요')
                self.table.setCellWidget(i, 1, script_button)
            else:
                for column, field in ((1, "x"), (2, "y")):
                    spin = QSpinBox()
                    spin.setRange(-99999, 99999)
                    spin.setValue(getattr(row, field))
                    spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
                    self.table.setColumnWidth(column, spin.fontMetrics().horizontalAdvance("-99999") + 16)
                    spin.valueChanged.connect(lambda value, rid=row.id, f=field: self.set_value(rid, f, value))
                    self.table.setCellWidget(i, column, spin)
            mode = QComboBox()
            mode.setMinimumContentsLength(1)
            mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            for value, label in MODES.items():
                mode.addItem(MODE_LABELS[value], value)
                mode.setItemData(mode.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
            mode.setCurrentIndex(list(MODES).index(row.action_mode))
            mode.setToolTip(MODES[row.action_mode])
            mode.currentIndexChanged.connect(lambda index, widget=mode: widget.setToolTip(list(MODES.values())[index]))
            mode.currentIndexChanged.connect(lambda index, rid=row.id: self.set_value(rid, "action_mode", list(MODES)[index]))
            self.table.setCellWidget(i, 3, mode)
        self.rebuilding = False
        self.table.resize_memo_column()
        if selected:
            index = next((i for i, r in enumerate(self.profile.rows) if r.id == selected), 0)
            self.table.selectRow(index)
        elif self.profile.rows:
            self.table.selectRow(0)
        self.update_labels()

    def live_memo_changed(self, index, text):
        if not self.rebuilding and 0 <= index < len(self.profile.rows):
            self.set_value(self.profile.rows[index].id, 'memo', text)

    def memo_changed(self, item):
        if not self.rebuilding and item.column() == 4:
            self.set_value(item.data(Qt.ItemDataRole.UserRole), "memo", item.text())
            self.table.resize_memo_column()

    def set_value(self, rid, field, value):
        row = self.row(rid)
        if row and getattr(row, field) != value:
            setattr(row, field, value)
            self.changed()
            if field == 'action_mode':
                QTimer.singleShot(0, lambda: self.rebuild(rid))

    def edit_script(self, rid):
        row = self.row(rid)
        if row is None:
            return
        self.pause_for_configuration()
        self.modal = True
        if self.settings.auto_pause:
            self.hooks.close()
        try:
            dialog = ScriptEditor(row.script, self.native, self.target, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                row.script = dialog.source
                self.changed()
                self.rebuild(rid)
                if not self.dirty:
                    self.statusBar().showMessage('스크립트를 반영하고 자동저장했습니다.')
        finally:
            self.modal = False
            self.restart_hooks()

    def eligible(self, action):
        if self.closing:
            return False
        if self.modal:
            if self.settings.auto_pause or action == "capture_selected":
                return False
            if action != "pause" and self.native.foreground_owned_by(os.getpid()):
                return False
        if action in ("pause", "capture_selected"):
            return True
        if action == "move":
            return self.armed and (not self.target or self.native.active(self.target))
        return not self.target or self.native.active(self.target)

    def rebind(self):
        self.profile.validate((self.settings.capture_hk, self.settings.pause_hk))
        values = [(self.settings.pause_hk, "pause", ""), (self.settings.capture_hk, "capture_selected", "")]
        for row in self.profile.rows:
            values.append((row.move_hk, "move", row.id))
        self.hooks.configure(values, self.settings.exclusive_hook)

    def pause_for_configuration(self):
        if self.settings.auto_pause:
            self.stop()

    def stop(self):
        self.armed = False
        self.engine.stop()
        if hasattr(self, "pause_button"):
            self.update_labels()

    def toggle(self):
        if self.modal and self.settings.auto_pause:
            return
        if self.armed:
            self.stop()
            self.statusBar().showMessage("일시정지했습니다. 같은 단축키로 다시 시작할 수 있습니다.")
            return
        if self.target and not self.native.exists(self.target):
            self.statusBar().showMessage("먼저 대상 창을 선택하세요.")
            return
        try:
            self.rebind()
            if self.hook_enabled:
                self.hooks.start()
        except Exception as error:
            self.error(str(error))
            return
        self.armed = True
        self.update_labels()
        self.statusBar().showMessage("전체화면에서 작동합니다." if not self.target else
                                     "작동 준비 완료. 대상 창으로 전환하면 실행 키를 사용할 수 있습니다.")

    def dispatch(self, action, rid):
        if not self.eligible(action):
            return
        if action == "pause":
            self.toggle()
            return
        if action == "capture_selected":
            index = self.table.currentRow()
            if index >= 0:
                self.capture(self.profile.rows[index].id)
            return
        row = self.row(rid)
        if not row:
            return
        if action == "move" and self.eligible("move"):
            s = self.settings
            if row.action_mode == 'script':
                try:
                    compile_script(row.script)
                except ValueError as error:
                    self.on_engine_error(str(error))
                    return
            command = Command(self.target, row.x, row.y, row.action_mode, row.move_hk,
                              s.click_delay, s.click_duration, s.return_delay, s.after_action_delay, row.script, curve_duration=s.curve_duration)
            if not self.engine.submit(command):
                self.statusBar().showMessage("실행기가 종료되어 실행할 수 없습니다.")

    def capture(self, rid):
        row = self.row(rid)
        if row and row.action_mode == 'script':
            self.statusBar().showMessage('스크립트 작성 화면에서 좌표를 추가하세요.')
            return
        if not row:
            return
        if self.target and not self.native.exists(self.target):
            self.statusBar().showMessage('대상 창이 닫혔습니다. 창을 다시 선택하세요.')
            return
        try:
            x, y = self.native.cursor(self.target)
            w, h = self.native.size(self.target)
            left, top, right, bottom = self.native.bounds(self.target)
            if not left <= x < right or not top <= y < bottom:
                raise ValueError("커서를 대상 영역 안으로 옮겨 주세요.")
            if abs(x) > 99999 or abs(y) > 99999:
                raise ValueError('좌표는 부호를 제외하고 최대 5자리까지 저장할 수 있습니다.')
            row.x, row.y = x, y
            self.profile.resolution = (w, h)
            self.changed()
            self.rebuild(rid)
            if not self.dirty:
                self.statusBar().showMessage(f"좌표 캡처 및 자동저장 완료: {x}, {y} · 일시정지됨")
        except Exception as error:
            self.on_engine_error(str(error))

    def refresh_windows(self):
        self.targets.blockSignals(True)
        self.targets.clear()
        self.targets.addItem("전체화면", 0)
        try:
            windows = self.native.windows(os.getpid())
            for hwnd, title in windows:
                self.targets.addItem(title, hwnd)
            index = self.targets.findData(self.target)
            if not self.target or index < 0:
                restored = self.native.find_target(self.profile.target_program, windows)
                index = self.targets.findData(restored)
            self.targets.setCurrentIndex(max(0, index))
        except Exception as error:
            self.statusBar().showMessage(str(error))
        self.targets.blockSignals(False)
        self.change_target(remember=False)

    def change_target(self, *_, remember=True):
        target = self.targets.currentData() or 0
        self.targets.setToolTip(self.targets.currentText())
        if target != self.target:
            self.stop()
            self.target = target
        if remember:
            try:
                identity = self.native.window_identity(target) if target else {}
                if identity != self.profile.target_program:
                    self.profile.target_program = identity
                    self.changed()
            except OSError as error:
                self.statusBar().showMessage(str(error))

    def tick(self):
        try:
            if self.target and not self.native.exists(self.target):
                self.stop()
                self.target = 0
                self.targets.blockSignals(True)
                self.targets.setCurrentIndex(0)
                self.targets.blockSignals(False)
                self.statusBar().showMessage("대상 창이 닫혔습니다. 새 창을 선택하세요.")
            if self.target:
                w, h = self.native.size(self.target)
                self.target_info.setText(f"{w} × {h}  ·  {'활성' if self.native.active(self.target) else '대기'}")
            else:
                self.target_info.setText("대상 창을 선택하세요")
            x, y = self.native.cursor(self.target or None)
            self.cursor_label.setText(f"{x:>5}, {y:>5}")
        except OSError:
            self.target_info.setText("창 상태 확인 중")

    def edit_key(self, rid):
        row = self.row(rid)
        if row is None:
            return
        self.pause_for_configuration()
        self.modal = True
        if self.settings.auto_pause:
            self.hooks.close()
        try:
            dialog = KeyDialog(row.move_hk, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                value = dialog.value()
                Hotkeys.compile(value)
                old = row.move_hk
                row.move_hk = value
                try:
                    self.rebind()
                except Exception:
                    row.move_hk = old
                    raise
                self.changed()
                self.rebuild(rid)
        except Exception as error:
            self.statusBar().showMessage(str(error))
        finally:
            self.modal = False
            self.restart_hooks()

    def restart_hooks(self):
        try:
            self.rebind()
            if self.hook_enabled:
                self.hooks.start()
        except Exception as error:
            self.on_engine_error(str(error))

    def add_row(self):
        if len(self.profile.rows) >= 200:
            return
        row = Row()
        self.profile.rows.append(row)
        self.changed()
        self.rebuild(row.id)

    def delete_rows(self):
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not selected:
            return
        if len(selected) == len(self.profile.rows):
            self.statusBar().showMessage("최소 한 행은 남겨 주세요.")
            return
        self.profile.rows = [r for i, r in enumerate(self.profile.rows) if i not in selected]
        self.changed()
        self.rebuild()
        self.rebind()

    def move_row(self, origin, target):
        if origin == target or not 0 <= target < len(self.profile.rows):
            return
        row = self.profile.rows.pop(origin)
        self.profile.rows.insert(target, row)
        self.changed()
        self.rebuild(row.id)

    def confirm_changes(self):
        self.pause_for_configuration()
        if not self.dirty:
            return True
        self.modal = True
        try:
            dialog = QMessageBox(QMessageBox.Icon.Question, "저장하지 않은 변경",
                                 "프로필 변경 사항을 저장할까요?", parent=self)
            dialog.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                      QMessageBox.StandardButton.Cancel)
            for button, label in ((QMessageBox.StandardButton.Save, "저장"),
                                  (QMessageBox.StandardButton.Discard, "저장 안 함"),
                                  (QMessageBox.StandardButton.Cancel, "취소")):
                dialog.button(button).setText(label)
            dialog.setDefaultButton(QMessageBox.StandardButton.Save)
            dialog.setEscapeButton(QMessageBox.StandardButton.Cancel)
            answer = dialog.exec()
        finally:
            self.modal = False
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        return self.save() if answer == QMessageBox.StandardButton.Save else True

    def new_profile(self):
        if self.confirm_changes():
            self.profile, self.path, self.dirty = Profile.default(), self.next_profile_path(), False
            self.target = 0
            self.refresh_windows()
            self.changed()
            self.rebuild()
            self.restart_hooks()

    def open_profile(self):
        if not self.confirm_changes():
            return
        self.modal = True
        try:
            path, _ = QFileDialog.getOpenFileName(self, "불러오기", str(self.path or self.data_dir), "매크로 프로필 (*.json)")
            if not path:
                return
            profile = Profile.read(read_json(path))
            profile.validate((self.settings.capture_hk, self.settings.pause_hk))
            for row in profile.rows:
                Hotkeys.compile(row.move_hk)
            self.profile, self.path, self.dirty = profile, Path(path), False
            self.stop()
            self.target = 0
            self.refresh_windows()
            self.rebuild()
            self.rebind()
            self.persist_settings()
            self.statusBar().showMessage("프로필을 불러왔습니다. 대상 창과 해상도를 확인하세요.")
        except Exception as error:
            self.error(str(error))
        finally:
            self.modal = False

    def next_profile_path(self):
        path = self.data_dir / 'macro.json'
        number = 1
        while path.exists():
            path = self.data_dir / f'macro_{number}.json'
            number += 1
        return path

    def save(self, automatic=False):
        if self.path is None:
            if automatic:
                self.path = self.next_profile_path()
            else:
                return self.save_as()
        try:
            self.profile.validate((self.settings.capture_hk, self.settings.pause_hk))
            atomic_json(self.path, self.profile.export())
            self.persist_settings()
            self.dirty = False
            self.update_labels()
            self.statusBar().showMessage(f"{'자동저장' if automatic else '저장'} 완료: {self.path}")
            return True
        except Exception as error:
            if automatic:
                self.dirty = True
                self.update_labels()
                self.statusBar().showMessage(f'자동저장 실패: {error} · 변경사항은 아직 저장되지 않았습니다.')
            else:
                self.error(str(error))
            return False

    def save_as(self):
        self.pause_for_configuration()
        self.modal = True
        try:
            path, _ = QFileDialog.getSaveFileName(self, "프로필 저장", str(self.path or self.data_dir / "macro.json"), "매크로 프로필 (*.json)")
            if not path:
                return False
            previous = self.path
            self.path = Path(path)
            if self.save():
                return True
            self.path = previous
            return False
        finally:
            self.modal = False

    def persist_settings(self):
        if self.path:
            path = self.path.resolve()
            if path.parent != self.data_dir:
                # Keep a portable copy without overwriting an unrelated local profile.
                local = self.recent_copies.get(path)
                if local is None:
                    local = self.data_dir / path.name
                    number = 1
                    while local.exists():
                        local = self.data_dir / f"{path.stem}_{number}{path.suffix}"
                        number += 1
                    self.recent_copies[path] = local
                atomic_json(local, read_json(path))
                path = local
            self.settings.last_profile = path.name
        atomic_json(self.settings_path, asdict(self.settings))

    def settings_dialog(self):
        self.pause_for_configuration()
        self.modal = True
        if self.settings.auto_pause:
            self.hooks.close()
        try:
            dialog = SettingsDialog(self.settings, self)
            while dialog.exec() == QDialog.DialogCode.Accepted:
                try:
                    settings = dialog.value()
                    self.profile.validate((settings.capture_hk, settings.pause_hk))
                    Hotkeys.compile(settings.capture_hk)
                    Hotkeys.compile(settings.pause_hk)
                    self.settings = settings
                    self.persist_settings()
                    self.apply_theme()
                    self.update_labels()
                    break
                except Exception as error:
                    self.error(str(error))
        finally:
            self.modal = False
            self.restart_hooks()

    def dimensions_dialog(self, title, labels, values):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        form = QFormLayout(dialog)
        spins = []
        for label, value in zip(labels, values):
            spin = QSpinBox()
            spin.setRange(1, 16384)
            spin.setValue(value)
            form.addRow(label, spin)
            spins.append(spin)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        return [spin.value() for spin in spins] if dialog.exec() == QDialog.DialogCode.Accepted else None

    def resize_dialog(self):
        self.pause_for_configuration()
        if not self.native.exists(self.target):
            self.statusBar().showMessage("대상 창을 먼저 선택하세요.")
            return
        self.modal = True
        try:
            dialog = ResizeDialog(self.native.size(self.target), self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.native.resize(self.target, *dialog.value())
                self.statusBar().showMessage("창 크기 변경을 요청했습니다. 저장 좌표는 설정 → 좌표 변환에서 조정하세요.")
        except Exception as error:
            self.error(str(error))
        finally:
            self.modal = False

    def convert_dialog(self):
        self.pause_for_configuration()
        self.modal = True
        try:
            current = self.native.size(self.target if self.native.exists(self.target) else 0)
            values = self.dimensions_dialog("프로필 좌표 변환", ["원본 너비", "원본 높이", "변경 너비", "변경 높이"],
                                             (*(self.profile.resolution or current), *current))
            if values:
                self.profile.convert(values[:2], values[2:])
                self.changed()
                self.rebuild()
                if not self.dirty:
                    self.statusBar().showMessage("좌표를 변환하고 자동저장했습니다.")
        except Exception as error:
            self.error(str(error))
        finally:
            self.modal = False

    def on_engine_error(self, text):
        self.stop()
        self.statusBar().showMessage("중단: " + text)

    def error(self, text):
        self.stop()
        previous = self.modal
        self.modal = True
        try:
            QMessageBox.warning(self, "확인해 주세요", text)
        finally:
            self.modal = previous

    def pin(self, enabled):
        try:
            self.native.topmost(int(self.winId()), enabled)
            self.pin_button.setToolTip("항상 위에 표시: " + ("켜짐" if enabled else "꺼짐"))
        except OSError as error:
            self.pin_button.blockSignals(True)
            self.pin_button.setChecked(not enabled)
            self.pin_button.blockSignals(False)
            self.statusBar().showMessage(str(error))

    def hide_to_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            self.tray.showMessage("Blue Archive Studio", "트레이 아이콘을 두 번 클릭하면 창을 다시 엽니다.")
        else:
            self.showMinimized()

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def help_dialog(self):
        self.pause_for_configuration()
        self.modal = True
        QMessageBox.information(self, "사용 방법", "1. 대상 게임/에뮬레이터 창을 선택합니다.\n"
            "2. 행을 선택하고 대상 창의 원하는 위치에 커서를 놓은 뒤 좌표 캡처 키(기본 Ctrl+S)를 누릅니다.\n"
            "3. 단축키와 동작을 지정하면 변경사항이 자동저장됩니다.\n"
            "4. 스크립트 실행 모드는 스크립트 작성 버튼에서 명령을 작성합니다.\n"
            "5. 작동 시작을 누른 뒤 단축키를 누릅니다.\n\n"
            "자동 중지 옵션이 켜져 있으면 편집 시 일시정지됩니다. 작동/정지 키는 일시정지 중에도 유지됩니다.\n"
            "기존 매크로의 JSON 프로필을 열 수 있습니다. 다른 매크로 앱은 종료한 뒤 사용하세요.")
        self.modal = False

    def shutdown(self):
        self.closing = True
        self.timer.stop()
        self.stop()
        self.hooks.close()
        self.engine.shutdown()
        self.tray.hide()

    def closeEvent(self, event):
        if not self.confirm_changes():
            event.ignore()
            return
        try:
            self.persist_settings()
        except Exception as error:
            self.statusBar().showMessage(str(error))
        self.shutdown()
        event.accept()
        QApplication.instance().quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("BlueArchiveStudio")
    app.setOrganizationName("Chobo")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    data_dir.mkdir(parents=True, exist_ok=True)
    instance_lock = QLockFile(str(data_dir / "instance.lock"))
    if not instance_lock.tryLock(100):
        QMessageBox.information(None, "Blue Archive Studio", "이미 실행 중입니다. 작업 표시줄 또는 트레이에서 창을 열어 주세요.")
        return 0
    window = MainWindow()
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
