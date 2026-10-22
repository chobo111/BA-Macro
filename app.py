from __future__ import annotations

from dataclasses import asdict, replace
from copy import deepcopy
import os
from ctypes import wintypes
from pathlib import Path
import sys

from PySide6.QtCore import Qt, Signal, QObject, QTimer, QStandardPaths, QSize, QSignalBlocker, QModelIndex, QItemSelectionModel
from PySide6.QtGui import QKeySequence, QDesktopServices
from PySide6.QtCore import QUrl

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidgetItem, QSpinBox, QFileDialog, QMessageBox, QDialog,
    QSystemTrayIcon, QMenu, QSizePolicy, QFrame, QGridLayout, QToolButton, QDialogButtonBox,
)

from domain import MODES, Profile, Row, same_program, WindowSizeUnavailable
from engine import Engine
from error_log import ErrorLog, ErrorLogDialog
from coordinate_diagnostics import CoordinateDiagnostics
from diagnostics_dialog import CoordinateDiagnosticsDialog
from native import Windows
from interactive_resize import resize as resize_window
from hotkeys import Hotkeys
from keyboard_hook import current_keyboard_layout
from execution_plan import ExecutionPlan
from coordinate_transform import aspect_warning
from coordinate_cache import scaled_point
from script_editor import ScriptEditor
from persistence import ProfileStore
from profile_saves import ProfileSaves
from profile_dialogs import RenameProfileDialog, ProfileInfoDialog
from profile_catalog import ProfileCatalogJobs
from profile_wait import wait_for_saves, wait_for_write, wait_for_profile_read
from profile_reads import ProfileReadWorker, read_profile
from conversion_dialog import ConversionDialog, BaselineResolutionDialog
from version import APP_NAME, VERSION, REPOSITORY_URL
from theme import DARK, LIGHT, palette, stylesheet
from session import ExecutionSession, editing_dialog
from shortcuts import ShortcutError, qt_hotkey
from single_instance import SingleInstance
import elevation
from titlebar_menu import CaptionMenu
from startup_dialogs import AppMismatchDialog, SizeMismatchDialog, CoordinateAppDialog, ManualCoordinateDialog
from widgets import (managed_dialog, make_icon, make_github_icon, RunButton, make_pin_icon, make_trash_icon, TargetCombo, RowsTable,
                     BriefStatusBar, KeyDialog, SettingsDialog, ResizeDialog, NoWheelCombo, NoWheelSpinBox, ElidedLabel)

BASE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


class Bridge(QObject):
    action = Signal(str, str)
    error = Signal(str)
    saved = Signal(int, object)


class MainWindow(QMainWindow):
    armed = property(lambda self: self.session.armed, lambda self, value: setattr(self.session, "armed", value))
    modal = property(lambda self: self.session.modal, lambda self, value: setattr(self.session, "modal", value))
    closing = property(lambda self: self.session.closing, lambda self, value: setattr(self.session, "closing", value))
    target = property(lambda self: self.session.target, lambda self, value: setattr(self.session, "target", value))

    run_size = property(lambda self: self.session.run_size)
    coordinate_transform = property(lambda self: self.session.coordinate_transform)

    def __init__(self, data_dir=None, hook_enabled=True):
        super().__init__()
        self.setWindowIcon(make_icon())
        self.resize(420, 420)
        self.setMinimumHeight(260)
        self.native = Windows()
        self.session = ExecutionSession(self.native)
        self.dirty = False
        self._profile_save_failed = False
        self.rebuilding = False
        self.script_editor = None
        self._profile_switching = False
        self._waiting_for_write = False
        self._restarting = False
        self._selected_identity = {}
        self.data_dir = Path(data_dir or BASE).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = ProfileStore(self.data_dir)
        self.settings_path = self.store.settings_path
        self.profile_saves = ProfileSaves()
        self.profile_catalog = ProfileCatalogJobs()
        self.profile_reader = ProfileReadWorker('profile-read')
        self._profile_listing = None
        self._profile_poll = QTimer(self)
        self._profile_poll.setInterval(30)
        self._profile_poll.timeout.connect(self.finish_profile_listing)
        self.settings_error = ""
        self.hook_enabled = hook_enabled
        loaded = self.store.load()
        self.settings, self.profile, self.path = loaded.settings, loaded.profile, loaded.path
        self.load_error = loaded.warning
        self._validated_shortcuts = None
        self.bridge = Bridge()
        self.coordinate_diagnostics = CoordinateDiagnostics()
        self.diagnostics_window = None
        self.engine = Engine(self.native, lambda key: self.hooks.held(key), self.bridge.error.emit,
                             diagnostics=self.coordinate_diagnostics)
        self.hooks = Hotkeys(self.bridge.action.emit, self.eligible, self.engine.stop, self.bridge.error.emit)
        self.bridge.action.connect(self.dispatch, Qt.ConnectionType.QueuedConnection)
        self.bridge.error.connect(self.on_engine_error, Qt.ConnectionType.QueuedConnection)
        self.bridge.saved.connect(self.save_completed, Qt.ConnectionType.QueuedConnection)
        self.error_log = ErrorLog(self)
        self.setStatusBar(BriefStatusBar(self))
        self.statusBar().error_raised.connect(self.record_error)
        self.build_ui()
        self.build_menu()
        self.build_tray()
        self.apply_theme()
        self.caption_menu = CaptionMenu(self, self.main_menu)
        QApplication.styleHints().colorSchemeChanged.connect(self.system_theme_changed)
        self.rebuild()
        self.refresh_windows(restore_saved=True)
        try:
            self.rebind()
            if hook_enabled:
                self.hooks.start()
        except Exception as error:
            self.load_error = f"단축키 초기화 실패: {error}"
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        if self.load_error:
            self.statusBar().show_error(self.load_error, '초기화')

    def button(self, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(6)
        target_box = self.target_box = QFrame()
        target_box.setObjectName('targetBox')
        target_box.setFixedWidth(384)
        target_outer = QVBoxLayout(target_box)
        target_outer.setContentsMargins(1, 1, 1, 1)
        target_outer.setSpacing(0)
        self.target_toggle = QToolButton()
        self.target_toggle.setObjectName('targetToggle')
        self.target_toggle.setText('대상 앱')
        self.target_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.target_toggle.setCheckable(True)
        self.target_toggle.setFixedHeight(30)
        self.target_toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.collapsed_profile = ElidedLabel()
        self.collapsed_profile.setObjectName('muted')
        self.collapsed_profile.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.collapsed_profile.setTextFormat(Qt.TextFormat.PlainText)
        self.collapsed_profile.setMinimumWidth(0)
        self.collapsed_profile.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.profile_unsaved = QLabel('미저장')
        self.collapsed_unsaved = QLabel('미저장')
        for badge in (self.profile_unsaved, self.collapsed_unsaved):
            badge.setObjectName('profileUnsaved')
            badge.setToolTip('프로필 변경사항이 저장되지 않았습니다.')
            badge.hide()
        collapsed_line = QHBoxLayout(self.target_toggle)
        collapsed_line.setContentsMargins(92, 0, 10, 0)
        collapsed_line.setSpacing(6)
        collapsed_line.addWidget(self.collapsed_profile, 1)
        self.collapsed_status = QLabel()
        self.collapsed_status.setObjectName('targetStatus')
        self.collapsed_status.hide()
        collapsed_line.addWidget(self.collapsed_status)
        collapsed_line.addWidget(self.collapsed_unsaved)
        for label in (self.collapsed_profile, self.collapsed_unsaved):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        target_outer.addWidget(self.target_toggle)
        self.target_content = QWidget()
        target_grid = QGridLayout(self.target_content)
        target_grid.setContentsMargins(10, 2, 10, 10)
        target_grid.setHorizontalSpacing(8)
        target_grid.setVerticalSpacing(6)
        target_grid.setColumnStretch(0, 1)
        self.targets = TargetCombo()
        self.targets.opening.connect(self.refresh_windows)
        self.targets.setMinimumWidth(0)
        self.targets.setMinimumContentsLength(10)
        self.targets.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.targets.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.targets.currentIndexChanged.connect(self.change_target)
        self.targets.setFixedHeight(30)
        target_grid.addWidget(self.targets, 0, 0)
        self.resize_button = self.button('미실행', self.resize_dialog)
        self.resize_button.setFixedSize(136, 30)
        self.resize_button.setAccessibleName('앱 해상도 · 창 크기 조절')
        self.resize_button.setToolTip('창 크기 변경')
        self.resize_button.setEnabled(False)
        target_grid.addWidget(self.resize_button, 0, 1)
        self.profile_name = ElidedLabel()
        self.profile_name.setObjectName('profileName')
        self.profile_name.setTextFormat(Qt.TextFormat.PlainText)
        self.profile_name.setAccessibleName('현재 프로필')
        self.profile_name.setMinimumWidth(0)
        self.profile_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.profile_resolution = QLabel()
        self.profile_resolution.setObjectName('muted')
        self.profile_resolution.setAccessibleName('프로필 앱 기준 해상도')
        self.profile_resolution.setMinimumWidth(0)
        self.profile_resolution.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.profile_resolution.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.profile_resolution.setFixedHeight(24)
        profile_line = QHBoxLayout()
        profile_line.setSpacing(6)
        profile_caption = QLabel('프로필')
        profile_caption.setObjectName('muted')
        profile_line.addWidget(profile_caption)
        profile_line.addWidget(self.profile_name, 1)
        profile_line.addWidget(self.profile_unsaved)
        target_grid.addLayout(profile_line, 1, 0)
        target_grid.addWidget(self.profile_resolution, 1, 1)
        self.target_info = QLabel()
        self.target_info.setObjectName('targetStatus')
        self.target_info.setMinimumWidth(0)
        self.target_info.setTextFormat(Qt.TextFormat.PlainText)
        self.target_info.setWordWrap(True)
        self.target_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.target_info.hide()
        target_grid.addWidget(self.target_info, 2, 0, 1, 2)
        target_outer.addWidget(self.target_content)
        self.target_toggle.toggled.connect(self.set_target_expanded)
        self.target_toggle.setChecked(True)
        layout.addWidget(target_box)

        toolbar_panel = self.toolbar_panel = QWidget()
        toolbar_panel.setFixedWidth(384)
        toolbar = QHBoxLayout(toolbar_panel)
        toolbar.setSpacing(4)
        toolbar.setContentsMargins(0, 2, 0, 2)
        for text, name, callback, attribute in (
                ("+", "행 추가", self.add_row, "add_button"),
                ("", "선택 삭제", self.delete_rows, "delete_button")):
            button = self.button(text, callback)
            button.setFixedSize(32, 30)
            button.setAccessibleName(name)
            button.setToolTip(name)
            if not text:
                button.setIconSize(QSize(18, 18))
            setattr(self, attribute, button)
            toolbar.addWidget(button)
        toolbar.addSpacing(4)
        separator = QFrame()
        separator.setObjectName("toolbarSeparator")
        separator.setFixedSize(1, 20)
        toolbar.addWidget(separator)
        toolbar.addStretch()
        self.pin_button = QPushButton()
        self.pin_button.setIconSize(QSize(20, 20))
        self.pin_button.setFixedSize(32, 30)
        self.pin_button.setAccessibleName("항상 위에 표시")
        self.pin_button.setCheckable(True)
        self.pin_button.setToolTip("다른 창보다 항상 위에 표시")
        self.pin_button.toggled.connect(self.pin)
        toolbar.addWidget(self.pin_button)
        self.pause_button = RunButton()
        self.pause_button.clicked.connect(self.toggle)
        self.pause_button.setFixedSize(106, 30)
        self.pause_button.setIconSize(QSize(16, 16))
        toolbar.addWidget(self.pause_button)
        layout.addWidget(toolbar_panel)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self.fit_content_width)
        self.table = RowsTable()
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.row_context_menu)
        self.table.itemChanged.connect(self.memo_changed)
        self.table.memo_edited.connect(self.live_memo_changed)
        self.table.memo_notice.connect(self.statusBar().showMessage)
        self.table.moved.connect(self.move_row)
        self.table.horizontalHeader().sectionResized.connect(self.queue_fit_content_width)
        self.table.verticalHeader().geometriesChanged.connect(self.queue_fit_content_width)
        self.table.verticalScrollBar().rangeChanged.connect(self.queue_fit_content_width)
        layout.addWidget(self.table, 1)
        self.update_labels()

    def set_target_expanded(self, expanded):
        self.target_content.setVisible(expanded)
        self.target_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.target_toggle.setAccessibleName('대상 앱 접기' if expanded else '대상 앱 펼치기')
        self.target_toggle.setText('대상 앱' if expanded else '')
        self.target_toggle.layout().setContentsMargins(92 if expanded else 26, 0, 10, 0)
        self.update_profile_summary()

    def queue_fit_content_width(self, *_):
        if not self.closing and not self._fit_timer.isActive():
            self._fit_timer.start()

    def fit_content_width(self):
        if self.closing:
            return
        table = self.table
        metrics = table.fontMetrics()
        memo_padding = 24 + metrics.horizontalAdvance('가')
        memo_width = max(table.MEMO_MIN_WIDTH,
                         max((metrics.horizontalAdvance(row.memo) + memo_padding
                              for row in self.profile.rows), default=0))
        memo_width = min(memo_width, table.horizontalHeader().maximumSectionSize())
        if table.columnWidth(4) != memo_width:
            table.setColumnWidth(4, memo_width)
        margins = self.centralWidget().layout().contentsMargins()
        content_width = (sum(table.columnWidth(column) for column in range(table.columnCount()))
                         + table.verticalHeader().width() + table.frameWidth() * 2)
        scroll_width = (table.verticalScrollBar().sizeHint().width()
                        if table.verticalScrollBar().maximum() > 0 else 0)
        width = content_width + scroll_width + margins.left() + margins.right()
        screen = self.screen()
        if screen:
            frame = max(0, self.frameGeometry().width() - self.geometry().width())
            width = min(width, screen.availableGeometry().width() - frame)
        table_width = width - margins.left() - margins.right()
        for panel in (table, self.target_box, self.toolbar_panel):
            panel.setMinimumWidth(0)
            panel.setMaximumWidth(table_width)
        previous = getattr(self, "_sheet_content_width", None)
        self._sheet_content_width = content_width
        self.setMinimumWidth(320)
        if previous is None or (content_width > previous and width > self.width()):
            self.resize(width, self.height())

    def build_menu(self):
        bar = self.menuBar()
        bar.setObjectName('appMenuBar')
        bar.setAccessibleName('앱 메뉴')
        bar.setFixedHeight(0)
        bar.hide()
        menu = self.main_menu = bar.addMenu('메뉴')
        menu.setObjectName('appMenu')
        menu.setMinimumWidth(210)
        menu.menuAction().setToolTip('메뉴')
        bar.removeAction(menu.menuAction())
        self.profile_menu = menu.addMenu('프로필 선택')
        self.profile_menu.setObjectName('appMenu')
        self.profile_menu.setMaximumWidth(420)
        self.profile_menu.setToolTipsVisible(True)
        self.profile_menu.aboutToShow.connect(self.refresh_profiles)
        menu.addSeparator()
        for text, callback in (("새 프로필…", self.new_profile), ("불러오기…", self.open_profile),
                               ("복제…", self.duplicate_profile), ("이름 변경…", self.rename_profile),
                               ("폴더 열기", self.open_profile_folder)):
            menu.addAction(text, callback)
        for group in (
            (("환경설정", self.settings_dialog), ("프로필 해상도 변환…", self.convert_dialog)),
            (("현재 프로필 정보", self.profile_info_dialog), ("앱 정보", self.about_dialog),
             ("오류 로그", self.error_log_dialog), ("좌표 진단", self.coordinate_diagnostics_dialog)),
        ):
            if menu.actions():
                menu.addSeparator()
            for text, callback in group:
                menu.addAction(text, callback)

    def build_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip(f"{APP_NAME} · 일시정지")
        menu = QMenu(self)
        menu.addAction("창 열기", self.restore)
        menu.addAction("작동 / 정지", self.toggle)
        menu.addSeparator()
        menu.addAction("종료", self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def system_theme_changed(self, scheme):
        if not self.closing and self.settings.theme == "System":
            self.apply_theme()

    def apply_theme(self):
        dark = self.settings.theme == "Dark" or (self.settings.theme == "System" and
                QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        self.theme_colors = colors = DARK if dark else LIGHT
        self.setPalette(palette(colors))
        self.setStyleSheet(stylesheet(colors))
        self.pause_button.apply_theme(colors)
        self.pause_button.setFixedWidth(max(106, self.pause_button.sizeHint().width()))
        self.pin_button.setIcon(make_pin_icon(colors.muted, colors.accent))
        self.delete_button.setIcon(make_trash_icon(colors.muted))
        for github in self.findChildren(QToolButton, 'githubLink'):
            github.setIcon(make_github_icon(colors.text))
        if self.script_editor is not None:
            self.script_editor.highlighter.apply_theme(colors)

    def update_labels(self):
        shortcut = QKeySequence(qt_hotkey(self.settings.pause_hk), QKeySequence.SequenceFormat.PortableText)
        shortcut_text = shortcut.toString(QKeySequence.SequenceFormat.NativeText).replace('Control', 'Ctrl')
        self.pause_button.setText(shortcut_text.replace('&', '&&'))
        action = "정지" if self.armed else "시작"
        self.pause_button.setAccessibleName(f'{action} ({shortcut_text})')
        self.pause_button.set_running(self.armed)
        self.pause_button.setFixedWidth(max(106, self.pause_button.sizeHint().width()))
        self.update_profile_summary()
        self.update_profile_checks()
        if hasattr(self, "tray"):
            self.tray.setToolTip(APP_NAME + " · " + ("작동 중" if self.armed else "일시정지"))

    def changed(self):
        self.pause_for_configuration()
        self.dirty = True
        return self.save(automatic=True)

    def row(self, row_id):
        return next((r for r in self.profile.rows if r.id == row_id), None)

    def rebuild(self, selected=None):
        """Synchronize by row ID so edits and moves retain existing controls."""
        table = self.table
        self.rebuilding = True
        try:
            with QSignalBlocker(table):
                current = [table.item(i, 4).data(Qt.ItemDataRole.UserRole)
                           for i in range(table.rowCount())]
                desired = [row.id for row in self.profile.rows]
                if current != desired:
                    # Qt moves items/widgets, but spans remain at their old indices.
                    table.clearSpans()
                    desired_ids = set(desired)
                    for i in range(len(current) - 1, -1, -1):
                        if current[i] not in desired_ids:
                            table.removeRow(i)
                            current.pop(i)
                    for i, rid in enumerate(desired):
                        if i < len(current) and current[i] == rid:
                            continue
                        if rid in current:
                            origin = current.index(rid)
                            table.model().moveRows(QModelIndex(), origin, 1, QModelIndex(), i)
                            current.insert(i, current.pop(origin))
                        else:
                            table.insertRow(i)
                            current.insert(i, rid)
                coordinate_width = table.fontMetrics().horizontalAdvance("-99999") + 16
                for column in (1, 2):
                    if table.columnWidth(column) != coordinate_width:
                        table.setColumnWidth(column, coordinate_width)
                for i, row in enumerate(self.profile.rows):
                    self.sync_row(i, row)
        finally:
            self.rebuilding = False
        self.queue_fit_content_width()
        if self.profile.rows:
            index = next((i for i, r in enumerate(self.profile.rows) if r.id == selected), 0)
            # Moving focus to an existing cell widget can alter selection.
            # Apply the final row selection after that focus change.
            table.setCurrentCell(index, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
            table.selectionModel().select(table.model().index(index, 0),
                                          QItemSelectionModel.SelectionFlag.ClearAndSelect
                                          | QItemSelectionModel.SelectionFlag.Rows)
        if selected is None:
            self.update_labels()

    def sync_row(self, index, row):
        table = self.table
        item = table.item(index, 4)
        if item is None:
            item = QTableWidgetItem(row.memo)
            item.setData(Qt.ItemDataRole.UserRole, row.id)
            table.setItem(index, 4, item)
            button = self.button(row.move_hk.upper().replace('&', '&&'), lambda checked=False, rid=row.id: self.edit_key(rid))
            button.setToolTip('단축키 지정')
            button.setAccessibleName('단축키 지정')
            table.setCellWidget(index, 0, button)
            mode = NoWheelCombo()
            mode.setMinimumContentsLength(1)
            mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            for value, label in MODES.items():
                mode.addItem(label, value)
                mode.setItemData(mode.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
            mode.currentIndexChanged.connect(lambda value, rid=row.id: self.set_value(rid, "action_mode", list(MODES)[value]))
            table.setCellWidget(index, 3, mode)
        if item.text() != row.memo:
            item.setText(row.memo)
        if item.toolTip() != row.memo:
            item.setToolTip(row.memo)
        button = table.cellWidget(index, 0)
        key_text = row.move_hk.upper().replace('&', '&&')
        if button.text() != key_text:
            button.setText(key_text)
        mode = table.cellWidget(index, 3)
        mode_index = list(MODES).index(row.action_mode)
        if mode.currentIndex() != mode_index:
            with QSignalBlocker(mode):
                mode.setCurrentIndex(mode_index)
        if mode.toolTip() != MODES[row.action_mode]:
            mode.setToolTip(MODES[row.action_mode])
        if row.action_mode == 'script':
            if table.columnSpan(index, 1) != 2:
                table.setSpan(index, 1, 1, 2)
            if not isinstance(table.cellWidget(index, 1), QPushButton):
                table.removeCellWidget(index, 2)
                script_button = self.button('스크립트 작성', lambda checked=False, rid=row.id: self.edit_script(rid))
                table.setCellWidget(index, 1, script_button)
            script_button = table.cellWidget(index, 1)
            tip = '스크립트 편집' if row.script else '실행할 스크립트를 작성하세요'
            if script_button.toolTip() != tip:
                script_button.setToolTip(tip)
        else:
            if table.columnSpan(index, 1) != 1:
                table.setSpan(index, 1, 1, 1)
            for column, field in ((1, "x"), (2, "y")):
                spin = table.cellWidget(index, column)
                if not isinstance(spin, NoWheelSpinBox):
                    spin = NoWheelSpinBox()
                    spin.setRange(-99999, 99999)
                    spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
                    spin.valueChanged.connect(lambda value, rid=row.id, f=field: self.edit_coordinate_value(rid, f, value))
                    table.setCellWidget(index, column, spin)
                if spin.value() != getattr(row, field):
                    with QSignalBlocker(spin):
                        spin.setValue(getattr(row, field))

    def live_memo_changed(self, index, text):
        if not self.rebuilding and 0 <= index < len(self.profile.rows):
            self.set_value(self.profile.rows[index].id, 'memo', text)

    def memo_changed(self, item):
        if not self.rebuilding and item.column() == 4:
            self.set_value(item.data(Qt.ItemDataRole.UserRole), "memo", item.text())
            self.table.update_memo_tooltips()

    def set_value(self, rid, field, value):
        row = self.row(rid)
        if row and getattr(row, field) != value:
            setattr(row, field, value)
            self.changed()
            if field == 'memo':
                self.queue_fit_content_width()
            if field == 'action_mode':
                QTimer.singleShot(0, lambda: self.rebuild(rid))

    def edit_coordinate_value(self, rid, field, value):
        row = self.row(rid)
        if row is None or getattr(row, field) == value:
            return
        prepared = self.prepare_manual_coordinate_basis()
        if prepared is None:
            self.rebuild(rid)
            return
        size, identity = prepared
        self.profile.resolution = size
        self.profile.target_program = identity
        self.set_value(rid, field, value)

    @editing_dialog(hooks=False, pause=False)
    def prepare_manual_coordinate_basis(self, *, basis=None, confirm_app=True):
        """Return (size, app identity), or None when the edit is denied.

        Matching dimensions do not imply a matching app. Table edits confirm
        ownership before committing; scripts retain it in the draft and confirm
        on save. Both paths recheck the profile and size after any prompt.
        """
        target, profile, path = self.target, self.profile, self.path
        original_basis = profile.resolution
        if target is None:
            # Offline edits use the stored basis; never infer a desktop size/app.
            return basis or profile.resolution, dict(profile.target_program)
        if not self.native.exists(target):
            self.stop()
            self.statusBar().showMessage('대상 앱이 닫혔습니다. 앱 선택을 해제한 뒤 다시 입력하세요.')
            return None
        try:
            size = tuple(self.native.size(target))
            basis = basis or profile.resolution
            if basis == size or (basis is None and not profile.has_fixed_coordinates()):
                identity = (self.confirm_coordinate_app(target) if confirm_app
                            else self.native.window_identity(target))
                if identity is None:
                    return None
                if (self.profile is not profile or self.path != path or self.target != target or
                        profile.resolution != original_basis or tuple(self.native.size(target)) != size):
                    raise ValueError('입력 중 앱 또는 프로필 기준이 변경되었습니다. 다시 입력하세요.')
                return size, identity
            self.stop()
            with managed_dialog(ManualCoordinateDialog(basis, size, self)) as dialog:
                dialog.exec()
                choice = dialog.choice()
            if (self.profile is not profile or self.path != path or self.target != target or
                    profile.resolution != original_basis or not self.native.exists(target)):
                return None
            if choice == 'resize' and basis:
                self.resize_dialog(profile_size=basis)
            elif choice == 'convert':
                self.convert_dialog(reason='현재 앱 크기에 맞게 프로필 좌표를 변환한 뒤 직접 입력하세요.')
            # Discard the pending edit: conversion may replace the profile/editor.
            return None
        except (OSError, ValueError) as error:
            self.on_engine_error(str(error))
            return None

    def edit_script(self, rid):
        if self.script_editor is not None:
            self.script_editor.editor.setFocus()
            return
        row = self.row(rid)
        if row is None:
            return
        self.pause_for_configuration()
        profile = self.profile
        dialog = ScriptEditor(row.script, self.native, self.target, self)
        dialog.set_row_context(row, self.profile.rows.index(row) + 1)
        self.script_editor = dialog
        self.session.editor_open = True
        def finished(result):
            self.script_editor = None
            self.session.editor_open = False
            if result == QDialog.DialogCode.Accepted and self.profile is profile and self.row(rid) is row:
                row.script = dialog.source
                if isinstance(dialog.capture_resolution, tuple):
                    self.profile.resolution = dialog.capture_resolution
                    self.profile.target_program = dialog.capture_program
                self.changed()
                self.rebuild(rid)
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.show()
        dialog.editor.setFocus()

    def finish_script_editor(self, save=False):
        dialog = self.script_editor
        if dialog is None:
            return True
        if save and dialog.editor.toPlainText() != dialog.source:
            dialog.save()
        else:
            dialog.reject()
        return self.script_editor is None

    def eligible(self, action):
        if self._profile_switching or self._waiting_for_write or self._restarting:
            return False
        return self.session.eligible(action, self.settings.auto_pause)

    def validate_profile_shortcuts(self, profile, settings=None):
        settings = self.settings if settings is None else settings
        keys = (settings.capture_hk, settings.pause_hk,
                *(row.move_hk for row in profile.rows))
        signature = (keys, current_keyboard_layout())
        if signature != self._validated_shortcuts:
            # Memo/coordinate autosaves reuse only a successful validation.
            Hotkeys.validate(keys)
            self._validated_shortcuts = signature

    def rebind(self):
        values = [(self.settings.pause_hk, "pause", ""), (self.settings.capture_hk, "capture_selected", "")]
        for row in self.profile.rows:
            values.append((row.move_hk, "move", row.id))
        self.hooks.configure(values, self.settings.exclusive_hook)

    def pause_for_configuration(self):
        if self.settings.auto_pause:
            self.stop()

    def stop(self):
        was_armed = self.armed
        self.session.stop()
        self.engine.stop()
        if was_armed and hasattr(self, "pause_button"):
            self.update_labels()

    def toggle(self):
        if self._profile_switching or self._waiting_for_write or self._restarting:
            return
        if self.armed:
            self.stop()
            return
        if self.modal or (self.session.editor_open and self.settings.auto_pause):
            return
        if not self.target:
            self.refresh_windows(restore_saved=True)
            if not self.target:
                self.statusBar().showMessage('프로필에 지정된 앱을 찾을 수 없거나 구분할 수 없습니다. 앱을 선택하세요.')
                return
        if self.target and not self.native.exists(self.target):
            self.statusBar().showMessage("먼저 대상 앱을 선택하세요.")
            return
        self.session.stop()
        try:
            if elevation.requires_elevation(self.target):
                if self.request_admin_restart(prompt=True) != QMessageBox.StandardButton.Ignore:
                    return
            plan = None
            try:
                if not self.native.is_minimized(self.target):
                    plan = self.confirm_start()
                    if plan is None:
                        return
                    plan.check_context(self.native, self.target, self.profile.resolution)
            except WindowSizeUnavailable:
                # A minimize/restore race also defers the size decision.
                plan = None
            awaiting_restore = plan is None
            if awaiting_restore:
                plan = ExecutionPlan(self.target, self.profile.resolution, None)
            self.rebind()
            if self.hook_enabled:
                self.hooks.start()
            self.session.start(plan, awaiting_restore=awaiting_restore)
        except Exception as error:
            self.error(str(error))
            return
        self.update_labels()
        if plan.automatic and self.settings.auto_convert_on_start:
            warning = aspect_warning(plan.source_resolution, plan.expected_size)
            if warning:
                self.statusBar().showMessage(warning)

    def check_startup_elevation(self):
        if self.closing or not self.settings.always_run_as_admin:
            return
        try:
            if not elevation.is_elevated():
                self.request_admin_restart(prompt=False)
        except OSError as error:
            self.statusBar().show_error(f'실행 권한 확인 실패: {error}')

    def request_admin_restart(self, *, prompt=True):
        """Return Ignore only for explicit permission to continue this start."""
        if self.closing or self._restarting or self._waiting_for_write or self._profile_switching:
            return False
        self.stop()
        self._restarting = True
        previous_modal = self.modal
        self.modal = True
        launch = None
        try:
            if prompt:
                with managed_dialog(QMessageBox(QMessageBox.Icon.Question, '관리자 권한 필요',
                        '선택한 앱이 관리자 권한으로 실행 중입니다.\n'
                        'BA Macro를 관리자 권한으로 다시 실행할까요?', parent=self)) as dialog:
                    restart = dialog.addButton('관리자 권한으로 재실행', QMessageBox.ButtonRole.AcceptRole)
                    ignore = dialog.addButton('무시하고 실행', QMessageBox.ButtonRole.ActionRole)
                    cancel = dialog.addButton('취소', QMessageBox.ButtonRole.RejectRole)
                    ignore.clicked.connect(dialog.accept)
                    dialog.setDefaultButton(restart)
                    dialog.setEscapeButton(cancel)
                    dialog.exec()
                    if dialog.clickedButton() is ignore:
                        return QMessageBox.StandardButton.Ignore
                    if dialog.clickedButton() is not restart:
                        return False
            # Commit the active delegate before taking the restart snapshot.
            focus = QApplication.focusWidget()
            if focus is not None and self.isAncestorOf(focus):
                focus.clearFocus()
            if not self.save():
                return False
            self.persist_settings()
            launch = elevation.ElevatedLaunch(self.data_dir, int(self.winId()))
            if not elevation.await_ready(launch, self):
                raise OSError('관리자 앱의 시작을 확인하지 못했습니다. 기존 앱을 유지합니다.')
            try:
                self.shutdown()
                launch.commit()
            finally:
                # Shutdown cannot be rolled back. Even a failed handoff must
                # finish exiting instead of leaving a disabled window behind.
                QApplication.instance().quit()
            return True
        except OSError as error:
            if getattr(error, 'winerror', None) == 1223:
                self.statusBar().showMessage('관리자 권한 요청을 취소했습니다.')
            else:
                self.statusBar().show_error(f'관리자 재실행 실패: {error}')
            return False
        except Exception as error:
            self.statusBar().show_error(f'관리자 재실행 실패: {error}')
            return False
        finally:
            if launch is not None:
                launch.close()
            self.modal = previous_modal
            self._restarting = False

    def check_running_size(self, size=None):
        if self.armed and self.session.plan is not None:
            if self.session.awaiting_restore:
                return self.complete_deferred_start(size)
            try:
                self.session.plan.check_context(self.native, self.target, self.profile.resolution, size=size)
            except WindowSizeUnavailable:
                return False
            except (OSError, ValueError, RuntimeError) as error:
                self.on_engine_error(str(error))
                return False
        return True

    def complete_deferred_start(self, size=None):
        if (self.modal or self._profile_switching
                or self._waiting_for_write or self.closing):
            return False
        pending = self.session.plan
        try:
            pending.check_context(self.native, self.target, self.profile.resolution)
            # Do not open confirmation dialogs until a client area is available.
            if size is None:
                self.native.size(self.target)
            plan = self.confirm_start()
            # Stop, app changes and profile changes during a dialog cancel the request.
            if not self.armed or self.session.plan is not pending:
                return False
            if plan is None:
                self.stop()
                return False
            plan.check_context(self.native, self.target, self.profile.resolution)
            self.rebind()
            self.session.start(plan)
            self.update_labels()
            if plan.automatic and self.settings.auto_convert_on_start:
                warning = aspect_warning(plan.source_resolution, plan.expected_size)
                if warning:
                    self.statusBar().showMessage(warning)
            return True
        except WindowSizeUnavailable:
            return False
        except Exception as error:
            self.on_engine_error(str(error))
            return False

    @editing_dialog(hooks=False, pause=False)
    def confirm_start(self):
        target, profile, path = self.target, self.profile, self.path
        if not target:
            self.statusBar().showMessage('앱을 선택하세요.')
            return None

        def selection_valid():
            if target != self.target or not self.native.exists(target):
                self.statusBar().showMessage('선택한 앱이 변경되거나 닫혔습니다. 다시 선택하세요.')
                return False
            if self.profile is not profile or self.path != path:
                self.statusBar().showMessage('프로필이 변경되었습니다. 다시 시작하세요.')
                return False
            return True

        resolution = self.profile.resolution
        automatic = False
        remember_automatic = False
        saved = dict(self.profile.target_program)
        current = self.native.window_identity(target)
        if saved and not same_program(saved, current):
            with managed_dialog(AppMismatchDialog(saved, current, self)) as dialog:
                dialog.exec()
                choice = dialog.choice()
            if choice not in ('new', 'copy', 'change') or not selection_valid():
                return None
            if choice in ('new', 'copy'):
                if not self.confirm_changes() or not selection_valid():
                    return None
                latest = self.native.window_identity(target)
                if latest != current and not same_program(current, latest):
                    self.statusBar().showMessage('선택한 앱이 변경되었습니다. 다시 선택하세요.')
                    return None
                candidate = (deepcopy(self.profile) if choice == 'copy'
                             else Profile.default((self.settings.capture_hk, self.settings.pause_hk)))
                candidate.target_program = latest
                self.validate_profile_shortcuts(candidate)
                self.stop()
                self.finish_script_editor()
                new_path = self.next_profile_path()
                self.commit_start_profile(candidate, new_path)
                self.profile = candidate
                self.rebuild()
                self.rebind()
                self.update_labels()
                message = f'새 프로필 생성: {new_path.name} · 내용을 확인한 뒤 시작하세요.'
                if self.settings_error:
                    message += f' · 최근 프로필 정보 저장 실패: {self.settings_error}'
                self.statusBar().showMessage(message)
                return None
        if not selection_valid():
            return None
        if resolution is None and profile.has_fixed_coordinates():
            self.set_missing_profile_resolution(current)
            return None
        current_size = tuple(self.native.size(target))
        if resolution:
            if current_size != resolution:
                if self.settings.auto_convert_on_start:
                    choice = 'auto'
                else:
                    with managed_dialog(SizeMismatchDialog(
                            resolution, current_size, self)) as dialog:
                        dialog.exec()
                        choice = dialog.choice()
                        remember_automatic = choice == 'auto' and dialog.skip_warning.isChecked() is True
                if not selection_valid():
                    return None
                if choice == 'resize':
                    self.resize_dialog(profile_size=resolution)
                    return None
                elif choice == 'auto':
                    if tuple(self.native.size(target)) != tuple(current_size):
                        self.statusBar().showMessage('앱 크기가 변경되었습니다. 다시 시작하세요.')
                        return None
                    automatic = True
                else:
                    return None
        if target:
            latest = self.native.window_identity(target)
            if not same_program(current, latest):
                self.statusBar().showMessage('선택한 앱이 변경되었습니다. 다시 선택하세요.')
                return None
            current = latest
        plan = ExecutionPlan(target, resolution, current_size, automatic=automatic)
        plan.check_context(self.native, self.target, self.profile.resolution)
        if remember_automatic:
            self.commit_settings(replace(self.settings, auto_convert_on_start=True))
        if current != self.profile.target_program:
            candidate = replace(self.profile, target_program=dict(current))
            self.commit_start_profile(candidate, self.path or self.next_profile_path())
            self.profile.target_program = dict(current)
        return plan

    def set_missing_profile_resolution(self, approved_app):
        """Commit only a user-specified basis, leaving execution stopped."""
        profile, path, target = self.profile, self.path, self.target
        self.stop()
        with managed_dialog(BaselineResolutionDialog(self)) as dialog:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            resolution = dialog.value()
        if (self.profile is not profile or self.path != path or self.target != target
                or profile.resolution is not None or not self.native.exists(target)):
            self.statusBar().showMessage('앱 또는 프로필이 변경되었습니다. 다시 시작하세요.')
            return
        current = self.native.window_identity(target)
        if current != approved_app and not same_program(approved_app, current):
            self.statusBar().showMessage('선택한 앱이 변경되었습니다. 다시 시작하세요.')
            return
        candidate = replace(profile, resolution=resolution, target_program=dict(current))
        self.commit_start_profile(candidate, path or self.next_profile_path())
        profile.resolution = resolution
        profile.target_program = dict(current)
        self.update_labels()
        message = '프로필 기준 해상도를 저장했습니다. 다시 시작을 눌러 주세요.'
        if self.settings_error:
            message += f' · 최근 프로필 정보 저장 실패: {self.settings_error}'
        self.statusBar().showMessage(message)

    def commit_start_profile(self, candidate, path):
        """Save the approved candidate before changing the current profile/app."""
        self.flush_pending_saves()
        self.validate_profile_shortcuts(candidate)
        result = wait_for_write(self.store.save(path, candidate.export(), asdict(self.settings)), self)
        if not result.profile_saved:
            raise OSError(f'프로필 저장 실패: {result.error}')
        self.path, self.dirty = path, False
        self._profile_save_failed = False
        self.settings_error = result.error
        if result.recent_name:
            self.settings.last_profile = result.recent_name
        if result.error:
            self.statusBar().show_error(f'프로필 저장 완료 · 최근 프로필 정보 저장 실패: {result.error}')

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
            if not self.check_running_size():
                return
            plan = self.session.plan
            if plan is None:
                return
            try:
                context = f'{self.path.name if self.path else "새 프로필"} · {self.profile.rows.index(row) + 1}행 · {row.move_hk.upper()}'
                command = replace(plan.command(row, self.settings), error_context=context)
            except ValueError as error:
                self.on_engine_error(str(error))
                return
            if not self.engine.submit(command):
                self.statusBar().showMessage("실행기가 종료되어 실행할 수 없습니다.")

    @editing_dialog(hooks=False, pause=False)
    def confirm_coordinate_app(self, target, *, expected=None):
        if not target or target != self.target or not self.native.exists(target):
            raise ValueError('좌표를 가져온 앱이 변경되거나 닫혔습니다. 다시 캡처하세요.')
        current = self.native.window_identity(target)
        if expected is not None and current != expected and not same_program(expected, current):
            raise ValueError('좌표를 가져온 앱이 변경되었습니다. 다시 캡처하세요.')
        saved = self.profile.target_program
        # This changes saved coordinate ownership, independently of start-warning preferences.
        if saved and current != saved and not same_program(saved, current):
            with managed_dialog(CoordinateAppDialog(saved, current, self)) as dialog:
                dialog.exec()
                if not dialog.confirmed():
                    return None
        if not target or target != self.target or not self.native.exists(target):
            raise ValueError('좌표를 가져온 앱이 변경되거나 닫혔습니다. 다시 캡처하세요.')
        latest = self.native.window_identity(target)
        if latest != current and not same_program(current, latest):
            raise ValueError('좌표를 가져온 앱이 변경되었습니다. 다시 캡처하세요.')
        return latest

    def capture(self, rid):
        if not self.target:
            self.statusBar().showMessage('좌표를 불러오려면 앱을 선택하세요.')
            return
        row = self.row(rid)
        target = self.target
        if row and row.action_mode == 'script':
            self.statusBar().showMessage('스크립트 작성 화면에서 좌표를 추가하세요.')
            return
        if not row:
            return
        if not self.native.exists(target):
            self.statusBar().showMessage('대상 앱이 닫혔습니다. 창을 다시 선택하세요.')
            return
        try:
            profile, path = self.profile, self.path
            basis = profile.resolution
            x, y = self.native.cursor(target)
            w, h = self.native.size(target)
            left, top, right, bottom = self.native.bounds(target)
            if not left <= x < right or not top <= y < bottom:
                raise ValueError("커서를 대상 영역 안으로 옮겨 주세요.")
            if abs(x) > 99999 or abs(y) > 99999:
                raise ValueError('좌표는 부호를 제외하고 최대 5자리까지 저장할 수 있습니다.')
            if not basis and profile.has_fixed_coordinates():
                self.convert_dialog(reason="저장 좌표와 현재 화면의 해상도 기준을 먼저 맞춰 주세요. 변환 후 좌표를 다시 캡처하세요.")
                return
            converted = basis is not None and basis != (w, h)
            warning = ''
            if converted:
                # Use the saved basis even after auto-pause clears the execution plan.
                x, y = scaled_point((x, y), (w, h), basis)
                warning = aspect_warning(basis, (w, h))
            identity = self.confirm_coordinate_app(target)
            if identity is None:
                return
            if (self.profile is not profile or self.path != path or
                    profile.resolution != basis or self.row(rid) is not row):
                raise ValueError('캡처 중 프로필이 변경되었습니다. 다시 캡처하세요.')
            if self.native.size(target) != (w, h):
                raise ValueError('캡처 중 화면 크기가 변경되었습니다. 다시 캡처하세요.')
            row.x, row.y = x, y
            profile.target_program = identity
            profile.resolution = basis or (w, h)
            queued = self.changed()
            self.rebuild(rid)
            if queued and converted:
                self.statusBar().showMessage(f'기준 좌표로 역변환하여 캡처: {x}, {y}' + (' · ' + warning if warning else ''))
            elif not self.dirty:
                self.statusBar().showMessage(f"좌표 캡처 및 자동저장 완료: {x}, {y} · 일시정지됨")
        except Exception as error:
            self.on_engine_error(str(error))

    def unselected_target_text(self):
        identity = self.profile.target_program
        name = identity.get('title') or identity.get('exe') or '지정 앱 없음'
        return name

    def refresh_windows(self, *, restore_saved=False):
        self.targets.blockSignals(True)
        self.targets.clear()
        self.targets.setPlaceholderText(self.unselected_target_text())
        try:
            windows = self.native.windows(os.getpid())
            for hwnd, title in windows:
                self.targets.addItem(title, hwnd)
            index = self.targets.findData(self.target)
            if restore_saved and (self.target is None or index < 0):
                restored = self.native.find_target(self.profile.target_program, windows)
                index = self.targets.findData(restored) if restored else -1
            self.targets.setCurrentIndex(index)
        except Exception as error:
            self.targets.setCurrentIndex(-1)
            self.statusBar().show_error(str(error))
        self.targets.blockSignals(False)
        self.change_target(remember=False)

    def change_target(self, *_, remember=True):
        target = self.targets.currentData()
        self.targets.setToolTip(self.targets.currentText())
        if target != self.target:
            self.stop()
            self.target = target
        self._selected_identity = {}
        if target is not None:
            try:
                self._selected_identity = self.native.window_identity(target)
            except OSError as error:
                if remember:
                    self.statusBar().show_error(str(error))
        if remember and self._selected_identity:
            identity = self._selected_identity
            if identity != self.profile.target_program and (
                    not self.profile.target_program or same_program(self.profile.target_program, identity)):
                self.profile.target_program = identity
                self.changed()
        self.update_target_info()
        self.update_profile_summary()
        self.set_cursor_position()

    def tick(self):
        try:
            if self.target and not self.native.exists(self.target):
                self.stop()
                self.target = None
                self.targets.blockSignals(True)
                self.targets.setCurrentIndex(-1)
                self.targets.blockSignals(False)
                self.update_profile_summary()
                self.statusBar().showMessage("대상 앱이 닫혔습니다. 새 창을 선택하세요.")
            if self.target:
                w, h = self.native.size(self.target)
                self.check_running_size((w, h))
                self.update_target_info((w, h))
            else:
                self.update_target_info()
            coordinate_target = self.target
            if not coordinate_target:
                self.set_cursor_position()
            else:
                x, y = self.native.cursor(coordinate_target)
                self.set_cursor_position((x, y))
        except WindowSizeUnavailable:
            self.update_target_info(state='최소화')
            self.set_cursor_position()
        except OSError:
            self.update_target_info(state="창 상태 확인 중")
            self.set_cursor_position()

    @editing_dialog(hooks=True)
    def edit_key(self, rid):
        row = self.row(rid)
        if row is None:
            return
        try:
            with managed_dialog(KeyDialog(row.move_hk, self)) as dialog:
                def validate_shortcut(value):
                    others = (self.settings.capture_hk, self.settings.pause_hk,
                              *(r.move_hk for r in self.profile.rows if r.id != rid))
                    # Check only the edited key so existing unrelated conflicts
                    # can still be repaired one row at a time.
                    Hotkeys.validate((value,))
                    for other in others:
                        Hotkeys.validate((value, other))
                dialog.validate_shortcut = validate_shortcut
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    value = dialog.value()
                    repairing = False
                    try:
                        self.validate_profile_shortcuts(self.profile)
                    except ShortcutError:
                        repairing = True
                        # Allow existing conflicts elsewhere to remain temporarily,
                        # but never introduce a conflict involving this new value.
                        others = (self.settings.capture_hk, self.settings.pause_hk,
                                  *(r.move_hk for r in self.profile.rows if r.id != rid))
                        for other in others:
                            Hotkeys.validate((value, other))
                    old = row.move_hk
                    row.move_hk = value
                    try:
                        self.rebind()
                    except ShortcutError as error:
                        if not repairing:
                            row.move_hk = old
                            raise
                        self.profile_saves.begin()
                        self.dirty = True
                        self._profile_save_failed = True
                        self.update_labels()
                        self.block_conflicting_shortcuts(error)
                        self.rebuild(rid)
                        return
                    except Exception:
                        row.move_hk = old
                        raise
                    self.changed()
                    self.rebuild(rid)
        except Exception as error:
            self.statusBar().show_error(str(error))

    def block_conflicting_shortcuts(self, error):
        self.stop()
        self.hooks.close()
        self.hooks.configure([], self.settings.exclusive_hook)
        self.statusBar().show_error(f'{error} · 남은 충돌을 수정하세요. 모두 해결하면 자동저장됩니다.', "단축키")

    def restart_hooks(self):
        try:
            self.rebind()
            if self.hook_enabled:
                self.hooks.start()
        except ShortcutError as error:
            self.block_conflicting_shortcuts(error)
        except Exception as error:
            self.on_engine_error(str(error))

    @editing_dialog(hooks=False, pause=False)
    def row_context_menu(self, point):
        index = self.table.indexAt(point)
        if not index.isValid():
            return
        row_index = index.row()
        if not self.table.selectionModel().isRowSelected(row_index, QModelIndex()):
            self.table.setCurrentCell(row_index, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.table.selectionModel().select(self.table.model().index(row_index, 0),
                QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
        selected = self.selected_row_indexes()
        count = len(selected)
        row = self.profile.rows[row_index]
        with managed_dialog(QMenu(self)) as menu:
            if count == 1:
                if row.action_mode == 'script':
                    menu.addAction('스크립트 편집', lambda: self.edit_script(row.id))
                menu.addAction('단축키 변경…', lambda: self.edit_key(row.id))
            clear = menu.addAction('단축키 해제' if count == 1 else f'{count}개 행 단축키 해제', self.clear_row_keys)
            clear.setEnabled(any(self.profile.rows[i].move_hk for i in selected))
            menu.addSeparator()
            menu.addAction('복제' if count == 1 else f'{count}개 행 복제', self.duplicate_rows)
            if count == 1:
                menu.addAction('위에 행 추가', lambda: self.insert_row(row_index))
                menu.addAction('아래에 행 추가', lambda: self.insert_row(row_index + 1))
            menu.addSeparator()
            menu.addAction('삭제' if count == 1 else f'{count}개 행 삭제', self.delete_rows)
            menu.exec(self.table.viewport().mapToGlobal(point))

    def selected_row_indexes(self):
        return sorted(index.row() for index in self.table.selectionModel().selectedRows())

    def clear_row_keys(self):
        selected = self.selected_row_indexes()
        if not selected or not any(self.profile.rows[i].move_hk for i in selected):
            return
        for i in selected:
            self.profile.rows[i].move_hk = ''
        self.changed()
        self.rebuild(self.profile.rows[selected[0]].id)
        self.restart_hooks()

    def duplicate_rows(self):
        selected = self.selected_row_indexes()
        if not selected:
            return
        if len(self.profile.rows) + len(selected) > 200:
            self.statusBar().showMessage('최대 200행까지 사용할 수 있습니다. 복제할 행 수를 줄여 주세요.')
            return
        copies = [replace(self.profile.rows[i], id=Row().id, move_hk='') for i in selected]
        position = selected[-1] + 1
        self.profile.rows[position:position] = copies
        self.changed()
        self.rebuild(copies[0].id)
        for i in range(position, position + len(copies)):
            self.table.selectionModel().select(self.table.model().index(i, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

    def insert_row(self, position):
        if len(self.profile.rows) >= 200:
            self.statusBar().showMessage('최대 200행까지 사용할 수 있습니다.')
            return
        row = Row()
        self.profile.rows.insert(position, row)
        self.changed()
        self.rebuild(row.id)

    def record_error(self, source, text):
        self.error_log.record(source, text, self.path.name if self.path else '새 프로필')

    def coordinate_diagnostics_dialog(self):
        if self.diagnostics_window is None:
            self.diagnostics_window = CoordinateDiagnosticsDialog(self.coordinate_diagnostics, self)
            self.diagnostics_window.destroyed.connect(lambda: setattr(self, 'diagnostics_window', None))
        self.diagnostics_window.show()
        self.diagnostics_window.raise_()
        self.diagnostics_window.activateWindow()

    @editing_dialog(hooks=False)
    def error_log_dialog(self):
        with managed_dialog(ErrorLogDialog(self.error_log, self)) as dialog:
            dialog.exec()

    def add_row(self):
        selected = self.selected_row_indexes()
        position = selected[-1] + 1 if selected else len(self.profile.rows)
        self.insert_row(position)

    def delete_rows(self):
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not selected:
            return
        if len(selected) == len(self.profile.rows):
            self.statusBar().showMessage("최소 한 행은 남겨 주세요.")
            return
        self.profile.rows = [r for i, r in enumerate(self.profile.rows) if i not in selected]
        focus_row = self.profile.rows[max(0, min(selected) - 1)]
        self.changed()
        self.rebuild(focus_row.id)
        self.restart_hooks()

    def move_row(self, origin, target):
        if origin == target or not (0 <= origin < len(self.profile.rows) and 0 <= target < len(self.profile.rows)):
            return
        row = self.profile.rows.pop(origin)
        self.profile.rows.insert(target, row)
        self.changed()
        self.rebuild(row.id)

    def refresh_profiles(self):
        if self.closing:
            return
        directory = self.path.parent if self.path is not None else self.data_dir
        self.profile_menu.clear()
        self.profile_menu.addAction('이 폴더').setEnabled(False)
        self.profile_menu.addAction('불러오는 중…').setEnabled(False)
        self._profile_listing = (directory, self.profile_catalog.submit(directory))
        self._profile_poll.start()

    def finish_profile_listing(self):
        if self.closing or self._profile_listing is None:
            return
        directory, future = self._profile_listing
        if not future.done():
            return
        self._profile_listing = None
        self._profile_poll.stop()
        current_directory = self.path.parent if self.path is not None else self.data_dir
        if directory != current_directory:
            return
        try:
            entries = future.result()
            menu = self.profile_menu
            menu.clear()
            menu.addAction('이 폴더').setEnabled(False)
            for path in entries:
                action = menu.addAction(path.stem.replace('&', '&&'))
                action.setData(str(path))
                action.setToolTip(str(path))
                action.setCheckable(True)
                action.triggered.connect(lambda checked=False, selected=path: self.switch_profile(selected))
            if not entries:
                menu.addAction('프로필 없음').setEnabled(False)
            self.update_profile_checks()
        except Exception as error:
            self.profile_menu.clear()
            self.profile_menu.addAction('이 폴더').setEnabled(False)
            self.profile_menu.addAction('목록을 불러오지 못했습니다').setEnabled(False)
            self.statusBar().show_error(f'프로필 목록을 불러오지 못했습니다: {error}')

    def update_profile_checks(self):
        if not hasattr(self, 'profile_menu'):
            return
        current = self.path.resolve() if self.path is not None else None
        for action in self.profile_menu.actions():
            if action.data() is not None:
                action.setChecked(current is not None and Path(action.data()) == current)

    def update_profile_summary(self):
        name = self.path.stem if self.path else '새 프로필'
        basis = '%d×%d' % self.profile.resolution if self.profile.resolution else '알 수 없음'
        self.profile_name.setText(name)
        self.profile_name.setToolTip(name)
        self.profile_resolution.setText(f'기준 {basis}')
        self.profile_resolution.setToolTip(f'기준 {basis}')
        placeholder = self.unselected_target_text()
        self.targets.setPlaceholderText(placeholder)
        if self.target is None:
            self.targets.setToolTip(placeholder)
        app_name = self.targets.currentText() if self.target else placeholder
        summary = f'{app_name} / {name}'
        self.collapsed_profile.setText(summary)
        expanded = self.target_toggle.isChecked()
        self.collapsed_profile.setVisible(not expanded)
        self.collapsed_status.setVisible(not expanded and bool(self.collapsed_status.text()))
        self.target_toggle.setToolTip('' if expanded else summary)
        self.profile_unsaved.setVisible(self._profile_save_failed)
        self.collapsed_unsaved.setVisible(not expanded and self._profile_save_failed)

    def set_cursor_position(self, point=None):
        text = f'{point[0]}, {point[1]}' if point is not None else '—, —'
        if self.windowTitle() != text:
            self.setWindowTitle(text)

    def update_target_info(self, size=None, *, state=''):
        warning = bool(self.target and self._selected_identity and self.profile.target_program
                       and not same_program(self.profile.target_program, self._selected_identity))
        resolution = '미실행'
        text = ''
        if self.target is not None:
            resolution = state or ('현재 %d×%d' % size if size else '현재 —')
            if warning:
                text = '지정 앱과 다름'
            elif self.armed and self.coordinate_transform is not None:
                text = '자동 변환 사용 중'
        if self.resize_button.text() != resolution:
            self.resize_button.setText(resolution)
        self.resize_button.setEnabled(self.target is not None and not state)
        if self.target_info.text() != text:
            self.target_info.setText(text)
        self.target_info.setVisible(bool(text))
        self.collapsed_status.setText('앱 불일치' if warning else ('자동 변환' if text else ''))
        self.collapsed_status.setToolTip(text)
        self.collapsed_status.setVisible(not self.target_toggle.isChecked() and bool(text))
        for label in (self.target_info, self.collapsed_status):
            if label.property('warning') != warning:
                label.setProperty('warning', warning)
                label.style().unpolish(label)
                label.style().polish(label)

    def prepare_profile_change(self):
        """Do not leave the current profile until its latest edits are saved."""
        editor = self.script_editor
        if editor is not None and editor.editor.toPlainText() != editor.source:
            with managed_dialog(QMessageBox(QMessageBox.Icon.Question, '저장하지 않은 스크립트',
                                             '작성 중인 스크립트를 저장할까요?', parent=self)) as dialog:
                dialog.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                          QMessageBox.StandardButton.Cancel)
                for button, text in ((QMessageBox.StandardButton.Save, '저장'),
                                     (QMessageBox.StandardButton.Discard, '버리기'),
                                     (QMessageBox.StandardButton.Cancel, '취소')):
                    dialog.button(button).setText(text)
                dialog.setDefaultButton(QMessageBox.StandardButton.Save)
                dialog.setEscapeButton(QMessageBox.StandardButton.Cancel)
                answer = dialog.exec()
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            if not self.finish_script_editor(save=answer == QMessageBox.StandardButton.Save):
                return False
        if self.dirty and not self.save(automatic=True):
            return False
        futures = [future for _, future in self.profile_saves.pending()]
        if not wait_for_saves(futures, self):
            return False
        self.flush_pending_saves()
        return not self.dirty

    def run_profile_change(self, operation):
        if self._profile_switching or self._waiting_for_write or self.closing:
            return False
        self._profile_switching = True
        self.stop()
        try:
            with self.session.editing(False, self.stop):
                return operation()
        except Exception as error:
            self.statusBar().show_error(str(error), '프로필 전환')
            return False
        finally:
            self._profile_switching = False
            self.update_labels()

    def switch_profile(self, path):
        path = Path(path)
        if self.path is not None and path == self.path:
            self.update_profile_checks()
            return True
        def change():
            # Reject invalid destinations before asking to discard an editor draft.
            loaded = wait_for_profile_read(self.profile_reader.submit(read_profile, path), self)
            if loaded is None:
                return False
            resolved_path, candidate = loaded
            # Layout-dependent shortcut matching stays on the GUI thread.
            self.validate_profile_shortcuts(candidate)
            if resolved_path == self.path:
                return True
            if not self.prepare_profile_change():
                return False
            # The file may have changed while the user decided about their draft.
            loaded = wait_for_profile_read(self.profile_reader.submit(read_profile, resolved_path), self)
            if loaded is None:
                return False
            resolved_path, profile = loaded
            self.validate_profile_shortcuts(profile)
            self.finish_script_editor()
            self.profile, self.path, self.dirty = profile, resolved_path, False
            self._profile_save_failed = False
            self.target = None
            self.refresh_windows(restore_saved=True)
            self.rebuild()
            self.rebind()
            self.persist_settings()
            self.statusBar().showMessage(f'프로필을 불러왔습니다: {path.stem}')
            return True
        return self.run_profile_change(change)

    @editing_dialog(hooks=False)
    def duplicate_profile(self):
        directory = self.path.parent if self.path else self.data_dir
        name = self.path.stem if self.path else '새 프로필'
        destination = directory / f'{name} 복사본.json'
        index = 2
        while destination.exists():
            destination = directory / f'{name} 복사본 {index}.json'
            index += 1
        path, _ = QFileDialog.getSaveFileName(self, '프로필 복제', str(destination), '매크로 프로필 (*.json)')
        if not path:
            return False
        path = Path(path)
        if path.suffix.lower() != '.json':
            path = path.with_name(path.name + '.json')
        def duplicate():
            self.store.validate_profile_path(path)
            if self.path and (path.resolve() == self.path.resolve() or (path.exists() and path.samefile(self.path))):
                raise ValueError('복제할 프로필은 다른 이름으로 저장하세요.')
            if not self.prepare_profile_change():
                return False
            candidate = deepcopy(self.profile)
            self.commit_start_profile(candidate, path)
            self.finish_script_editor()
            self.profile = candidate
            self.refresh_windows(restore_saved=True)
            self.rebuild()
            self.restart_hooks()
            self.statusBar().showMessage(f'프로필 복제 완료: {path.stem}')
            return True
        return self.run_profile_change(duplicate)

    def open_profile_folder(self):
        directory = self.path.parent if self.path else self.data_dir
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            self.statusBar().show_error('프로필 폴더를 열지 못했습니다.')

    def confirm_changes(self):
        self.flush_pending_saves()
        self.pause_for_configuration()
        script_dirty = self.script_editor is not None and self.script_editor.editor.toPlainText() != self.script_editor.source
        if not self.dirty and not script_dirty:
            return True
        previous_modal = self.modal
        self.modal = True
        try:
            with managed_dialog(QMessageBox(
                    QMessageBox.Icon.Question, "저장하지 않은 변경",
                    "프로필 변경 사항을 저장할까요?", parent=self)) as dialog:
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
            self.modal = previous_modal
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        self.finish_script_editor()
        return True

    @editing_dialog(hooks=False)
    def new_profile(self):
        with managed_dialog(QFileDialog(self, '새 프로필 생성', str(self.next_profile_path()),
                                        '매크로 프로필 (*.json)')) as dialog:
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            dialog.setFileMode(QFileDialog.FileMode.AnyFile)
            dialog.setDefaultSuffix('json')
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            path = Path(dialog.selectedFiles()[0])
        def create():
            self.store.validate_profile_path(path)
            if not self.prepare_profile_change():
                return
            candidate = Profile.default((self.settings.capture_hk, self.settings.pause_hk))
            self.stop()
            self.commit_start_profile(candidate, path)
            self.finish_script_editor()
            self.profile = candidate
            self.target = None
            self.refresh_windows()
            self.rebuild()
            self.restart_hooks()
            message = f'새 프로필 생성: {path.name}'
            if self.settings_error:
                self.record_error('설정 저장', self.settings_error)
                message += f' · 최근 프로필 정보 저장 실패: {self.settings_error}'
            self.statusBar().showMessage(message)
        self.run_profile_change(create)

    @editing_dialog(hooks=False)
    def open_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "불러오기", str(self.path or self.data_dir), "매크로 프로필 (*.json)")
        if path:
            return self.switch_profile(path)

    def next_profile_path(self):
        self.flush_pending_saves()
        return self.store.next_path()

    def save(self, automatic=False):
        if not automatic and not self.finish_script_editor(save=True):
            return False
        if self.path is None:
            if automatic:
                self.path = self.next_profile_path()
            else:
                return self.save_as()
        revision = self.profile_saves.begin()
        try:
            self.validate_profile_shortcuts(self.profile)
            future = self.store.save(self.path, self.profile.export(), asdict(self.settings), coalesce=automatic)
            self.profile_saves.register(revision, self.path, future)
            self.dirty = True
            self.update_labels()
            if automatic:
                future.add_done_callback(lambda done: self.bridge.saved.emit(revision, done.result()))
                return True
            result = wait_for_write(future, self)
            self.save_completed(revision, result)
            return result.profile_saved
        except Exception as error:
            self.dirty = True
            self._profile_save_failed = True
            self.update_labels()
            self.statusBar().show_save_result(f'저장 실패: {error}', failed=True)
            return False

    def save_completed(self, revision, result):
        if not self.profile_saves.complete(revision, self.path):
            return
        self.dirty = not result.profile_saved
        self._profile_save_failed = not result.profile_saved
        self.settings_error = result.error if result.profile_saved else ''
        if result.recent_name:
            self.settings.last_profile = result.recent_name
        self.update_labels()
        if not result.profile_saved:
            self.statusBar().show_save_result(f'자동저장 실패: {result.error} · 변경사항은 아직 저장되지 않았습니다.', failed=True)
        elif result.error:
            self.statusBar().show_save_result(f'프로필 저장 완료 · 설정/최근 프로필 정보 저장 실패: {result.error}', failed=True)
        else:
            self.statusBar().show_save_result(f'저장 완료: {self.path}')

    def flush_pending_saves(self):
        for revision, future in self.profile_saves.pending():
            self.save_completed(revision, wait_for_write(future, self, protect_ui=False))

    @editing_dialog(hooks=False)
    def save_as(self):
        if not self.finish_script_editor(save=True):
            return False
        path, _ = QFileDialog.getSaveFileName(self, "프로필 저장", str(self.path or self.data_dir / "macro.json"), "매크로 프로필 (*.json)")
        if not path:
            return False
        try:
            self.store.validate_profile_path(path)
        except (ValueError, OSError) as error:
            self.statusBar().show_error(str(error))
            return False
        previous = self.path
        self.path = Path(path)
        if self.save():
            return True
        self.path = previous
        self.update_labels()
        return False

    @editing_dialog(hooks=False)
    def rename_profile(self):
        if self.path is None:
            self.statusBar().showMessage('프로필을 먼저 저장한 뒤 이름을 변경하세요.')
            return
        original, profile = self.path, self.profile
        with managed_dialog(RenameProfileDialog(original.stem, self)) as dialog:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            name = dialog.name.text()
        if self.path != original or self.profile is not profile:
            return
        try:
            destination = self.store.rename_path(original, name)
            if destination.name == original.name:
                return
            self.flush_pending_saves()
            if self.dirty:
                if not self.save(automatic=True):
                    return
                self.flush_pending_saves()
                if self.dirty:
                    return
            result = wait_for_write(
                self.store.rename_async(original, destination, asdict(self.settings)), self)
            if not result.profile_saved:
                raise OSError(result.error)
            self.path = destination
            self.settings_error = result.error
            if result.recent_name:
                self.settings.last_profile = result.recent_name
            self.update_labels()
            message = f'프로필 이름 변경 완료: {destination.stem}'
            if result.error:
                self.record_error('설정 저장', result.error)
                message += f' · 최근 프로필 정보 저장 실패: {result.error}'
            self.statusBar().showMessage(message)
        except (ValueError, OSError, RuntimeError) as error:
            self.statusBar().show_error(f'이름 변경 실패: {error}')

    def profile_details(self):
        def size_text(size):
            return f'{size[0]} × {size[1]}' if size else '알 수 없음'

        identity = self.profile.target_program
        app_name = identity.get('title') or identity.get('exe') or identity.get('class') or '지정된 앱 없음'
        if identity.get('exe') and identity['exe'] != app_name:
            app_name += f" ({identity['exe']})"
        current_size = '—'
        if self.target is not None:
            try:
                current_size = size_text(self.native.size(self.target))
            except WindowSizeUnavailable:
                current_size = '최소화'
            except OSError:
                current_size = '확인할 수 없음'
        counts = ', '.join(f'{label} {sum(row.action_mode == mode for row in self.profile.rows)}개'
                           for mode, label in MODES.items())
        transform = self.coordinate_transform
        conversion = '사용 안 함'
        if transform is not None:
            conversion = f'사용 중 · {size_text(transform[0])} → {size_text(transform[1])}'
        state = '저장 중' if self.profile_saves.pending() else ('저장되지 않은 변경 있음' if self.dirty or self.path is None else '저장됨')
        if self.script_editor is not None and self.script_editor.editor.toPlainText() != self.script_editor.source:
            state += ' · 스크립트 편집 중(미저장)'
        return [
            ('프로필 이름', self.path.stem if self.path else '새 프로필'),
            ('파일 위치', str(self.path.resolve()) if self.path else '저장되지 않음'),
            ('지정 앱', app_name),
            ('기준 크기', size_text(self.profile.resolution)),
            ('현재 선택', self.targets.currentText() or '미선택'),
            ('현재 크기', current_size),
            ('동작 수', f'총 {len(self.profile.rows)}개 · {counts}'),
            ('자동 변환', conversion),
            ('저장 상태', state),
        ]

    @editing_dialog(hooks=False, pause=False)
    def profile_info_dialog(self):
        with managed_dialog(ProfileInfoDialog(self.profile_details(), self)) as dialog:
            dialog.exec()

    def persist_settings(self, *, recover=False):
        self.commit_settings(self.settings, recover=recover)

    def commit_settings(self, settings, *, recover=False):
        """Persist a candidate before exposing it to input hooks or the runtime."""
        self.flush_pending_saves()
        candidate = replace(settings)
        candidate.last_profile = wait_for_write(
            self.store.settings_async(self.path, asdict(candidate), recover=recover), self)
        self.settings = candidate
        self.settings_error = ''

    @editing_dialog(hooks=True)
    def settings_dialog(self):
        with managed_dialog(SettingsDialog(self.settings, self)) as dialog:
            dialog.validate_settings = lambda settings: self.validate_profile_shortcuts(self.profile, settings)
            dialog.validation_failed.connect(lambda text: self.record_error('환경설정', text))
            while dialog.exec() == QDialog.DialogCode.Accepted:
                try:
                    settings = dialog.value()
                    self.validate_profile_shortcuts(self.profile, settings)
                    self.commit_settings(settings, recover=True)
                    # A global shortcut can resolve the last outstanding row
                    # conflict; persist the edits that were waiting for it.
                    if self.dirty:
                        self.save(automatic=True)
                    self.apply_theme()
                    self.update_labels()
                    break
                except ShortcutError as error:
                    self.statusBar().show_error(str(error))
                    dialog.show_validation_error(error)
                except Exception as error:
                    self.error(str(error))

    @editing_dialog(hooks=False)
    def resize_dialog(self, *, profile_size=None):
        if not self.native.exists(self.target):
            self.statusBar().showMessage("대상 앱을 먼저 선택하세요.")
            return
        try:
            target = self.target
            def read_size():
                if target != self.target or not self.native.exists(target):
                    raise OSError("대상 앱이 닫혔거나 변경되었습니다.")
                return self.native.size(target)
            native = self.native
            def request_resize(size):
                # Worker callback: immutable target and native APIs only, no Qt state.
                return resize_window(native, target, *size)
            with managed_dialog(ResizeDialog(
                    read_size(), self, convert_profile=False if profile_size else self.settings.resize_convert_profile,
                    request_resize=request_resize, read_size=read_size)) as dialog:
                dialog.resizing.connect(self.stop)
                if profile_size:
                    if not all(1 <= value <= 16384 for value in profile_size):
                        raise ValueError('창 크기는 1~16384px 범위여야 합니다. 프로필 좌표 변환을 사용하세요.')
                    dialog.change_ratio('직접 입력')
                    dialog.set_size(profile_size)
                    dialog.convert_profile.hide()
                    QTimer.singleShot(0, dialog, dialog.apply)
                accepted = dialog.exec() == QDialog.DialogCode.Accepted
                convert = not profile_size and dialog.convert_profile.isChecked()
                if accepted and not profile_size and convert != self.settings.resize_convert_profile:
                    self.commit_settings(replace(self.settings, resize_convert_profile=convert))
                if accepted:
                    message = ('창 크기를 변경했습니다. 다시 시작을 눌러 주세요.' if profile_size else
                               '창 내부 크기 변경 완료: %d × %d' % dialog.current_size)
                    if dialog.refresh_notice:
                        self.record_error('창 크기', dialog.refresh_notice)
                    self.statusBar().showMessage(message + (' · ' + dialog.refresh_notice if dialog.refresh_notice else ''))
                    if convert and tuple(dialog.current_size) != self.profile.resolution:
                        self.convert_dialog(reason="창 크기가 바뀌었습니다. 저장 좌표도 새 해상도에 맞출 수 있습니다.")
        except Exception as error:
            self.error(str(error))

    @editing_dialog(hooks=False)
    def convert_dialog(self, *, reason=""):
        if not self.finish_script_editor(save=True):
            return
        self.stop()
        self.flush_pending_saves()
        try:
            try:
                current = self.native.size(self.target) if self.target and self.native.exists(self.target) else None
            except WindowSizeUnavailable:
                current = None
            with managed_dialog(ConversionDialog(
                    self.profile, self.path or self.data_dir / "macro.json",
                    current, self.save_conversion, self, reason)) as dialog:
                dialog.exec()
        except Exception as error:
            self.error(str(error))

    def save_conversion(self, profile, mode, dialog):
        self.flush_pending_saves()
        path = self.path or self.next_profile_path()
        if mode == 'copy':
            stem = f"{path.stem}_{profile.resolution[0]}x{profile.resolution[1]}"
            suggested = path.with_name(stem + '.json')
            number = 2
            while suggested.exists():
                suggested = path.with_name(f'{stem}_{number}.json')
                number += 1
            selected, _ = QFileDialog.getSaveFileName(dialog, "변환본으로 저장", str(suggested), "매크로 프로필 (*.json)")
            if not selected:
                return False
            path = Path(selected)
            if not path.suffix:
                path = path.with_suffix('.json')
            if self.path and path.resolve() == self.path.resolve():
                raise ValueError('원본을 보존하려면 다른 파일명을 지정하세요. 덮어쓰려면 현재 프로필에 적용을 사용하세요.')
        self.validate_profile_shortcuts(profile)
        result = wait_for_write(self.store.save(path, profile.export(), asdict(self.settings)), self)
        if not result.profile_saved:
            raise ValueError(result.error)
        self.profile, self.path, self.dirty = profile, path, False
        self._profile_save_failed = False
        self.settings_error = result.error
        if result.recent_name:
            self.settings.last_profile = result.recent_name
        self.rebuild()
        self.rebind()
        self.update_labels()
        message = f'변환 완료: {path.name}'
        if result.error:
            self.record_error('설정 저장', result.error)
            message += f' · 최근 프로필 정보 저장 실패: {result.error}'
        self.statusBar().showMessage(message)
        return True

    def on_engine_error(self, text):
        self.stop()
        self.statusBar().show_error("중단: " + text, "실행")

    def error(self, text):
        self.record_error('일반', text)
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
            self.statusBar().show_error(str(error))

    def nativeEvent(self, event_type, message):
        if event_type == b'windows_generic_MSG' and hasattr(self, 'caption_menu'):
            msg = wintypes.MSG.from_address(int(message))
            if self.caption_menu.handle(msg):
                return True, 0
        return super().nativeEvent(event_type, message)

    def restore(self):
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()
        modal = QApplication.activeModalWidget()
        if modal is not None and modal is not self:
            modal.raise_()
            modal.activateWindow()

    @editing_dialog(hooks=False)
    def about_dialog(self):
        with managed_dialog(QDialog(self)) as dialog:
            dialog.setWindowTitle('앱 정보')
            layout = QVBoxLayout(dialog)
            label = QLabel(f'<b>{APP_NAME}</b><br>버전: {VERSION}')
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
            github = QToolButton(dialog)
            github.setObjectName('githubLink')
            github.setIcon(make_github_icon(self.theme_colors.text))
            github.setIconSize(QSize(28, 28))
            github.setFixedSize(40, 40)
            github.setCursor(Qt.CursorShape.PointingHandCursor)
            github.setAccessibleName('GitHub 저장소 열기')
            github.setToolTip('GitHub · chobo111/BA-Macro')
            github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPOSITORY_URL)))
            layout.addWidget(github, 0, Qt.AlignmentFlag.AlignHCenter)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.button(QDialogButtonBox.StandardButton.Close).setText('닫기')
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.exec()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.coordinate_diagnostics.stop()
        self.finish_script_editor()
        self.timer.stop()
        self._fit_timer.stop()
        self._profile_poll.stop()
        self._profile_listing = None
        self.profile_catalog.close()
        self.profile_reader.close()
        self.stop()
        self.hooks.close()
        self.engine.shutdown()
        self.flush_pending_saves()
        self.store.close()
        self.tray.hide()
        self.caption_menu.close()

    def closeEvent(self, event):
        # Completed shutdown must be allowed to close even inside the restart
        # guard; otherwise QApplication.quit() is vetoed and the child times out.
        if self.closing:
            event.accept()
            return
        if self._waiting_for_write or self._restarting:
            event.ignore()
            return
        self.stop()
        if not self.confirm_changes():
            event.ignore()
            return
        try:
            self.persist_settings()
        except Exception as error:
            self.statusBar().show_error(str(error))
        self.shutdown()
        event.accept()
        QApplication.instance().quit()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--elevated-parent', type=int)
    parser.add_argument('--elevated-ready')
    parser.add_argument('--data-dir', type=Path)
    options = parser.parse_args()
    app = QApplication(sys.argv)
    # Keep the lock location stable across the display-name change.
    app.setApplicationName("BlueArchiveStudio")
    # Windows appends a nonempty display name to native window captions.
    app.setApplicationDisplayName('')
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("Chobo")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    data_dir.mkdir(parents=True, exist_ok=True)
    instance = SingleInstance(data_dir, app)
    try:
        if options.elevated_parent is not None or options.elevated_ready is not None:
            if options.elevated_parent is None or not options.elevated_ready:
                raise OSError('잘못된 재실행 요청입니다.')
            elevation.accept_handoff(options.elevated_parent, options.elevated_ready)
        if not instance.acquire():
            return 0
        window = MainWindow(data_dir=options.data_dir)
        instance.activate_requested.connect(window.restore)
        app.aboutToQuit.connect(window.shutdown)
        window.show()
        QTimer.singleShot(0, window, window.check_startup_elevation)
        return app.exec()
    except OSError as error:
        QMessageBox.critical(None, APP_NAME, str(error))
        return 1
    finally:
        instance.close()



if __name__ == "__main__":
    raise SystemExit(main())
