from __future__ import annotations

from dataclasses import asdict, replace
from copy import deepcopy
import os
from pathlib import Path
import sys

from PySide6.QtCore import Qt, Signal, QObject, QTimer, QStandardPaths, QSize
from PySide6.QtGui import QKeySequence

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidgetItem, QSpinBox, QFileDialog, QMessageBox, QDialog,
    QSystemTrayIcon, QMenu, QGroupBox, QSizePolicy, QFrame,
)

from domain import MODES, Profile, Row, Settings, read_json, same_program, WindowSizeUnavailable
from engine import Engine
from native import Windows
from interactive_resize import resize as resize_window
from hotkeys import Hotkeys
from execution_plan import ExecutionPlan
from coordinate_transform import aspect_warning
from coordinate_cache import scaled_point
from script_editor import ScriptEditor
from persistence import ProfileStore
from profile_saves import ProfileSaves
from profile_dialogs import RenameProfileDialog, ProfileInfoDialog
from conversion_dialog import ConversionDialog
from version import APP_NAME, VERSION
from theme import DARK, LIGHT, palette, stylesheet
from session import ExecutionSession, editing_dialog
from shortcuts import ShortcutError, qt_hotkey
from single_instance import SingleInstance
from startup_dialogs import AppMismatchDialog, SizeMismatchDialog, CoordinateAppDialog
from widgets import (managed_dialog, make_icon, make_run_icon, make_pin_icon, make_trash_icon, TargetCombo, RowsTable,
                     BriefStatusBar, KeyDialog, SettingsDialog, ResizeDialog, NoWheelCombo, NoWheelSpinBox)

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
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_icon())
        self.resize(420, 420)
        self.setMinimumHeight(340)
        self.native = Windows()
        self.session = ExecutionSession(self.native)
        self.dirty = False
        self.rebuilding = False
        self.script_editor = None
        self.path = None
        self.data_dir = Path(data_dir or BASE).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = ProfileStore(self.data_dir)
        self.settings_path = self.store.settings_path
        self.profile_saves = ProfileSaves()
        self.settings_error = ""
        self.settings = Settings()
        self.profile = Profile.default()
        self.load_error = ""
        self.hook_enabled = hook_enabled
        loaded = self.store.load()
        self.settings, self.profile, self.path = loaded.settings, loaded.profile, loaded.path
        self.load_error = loaded.warning
        self.native.display_id = self.settings.display_id
        self.display_ticks = 0
        try:
            self.native.refresh_displays()
        except OSError as error:
            self.load_error = str(error)
        self.bridge = Bridge()
        self.engine = Engine(self.native, lambda key: self.hooks.held(key), self.bridge.error.emit)
        self.hooks = Hotkeys(self.bridge.action.emit, self.eligible, self.engine.stop, self.bridge.error.emit)
        self.bridge.action.connect(self.dispatch, Qt.ConnectionType.QueuedConnection)
        self.bridge.error.connect(self.on_engine_error, Qt.ConnectionType.QueuedConnection)
        self.bridge.saved.connect(self.save_completed, Qt.ConnectionType.QueuedConnection)
        self.setStatusBar(BriefStatusBar(self))
        self.build_ui()
        self.build_menu()
        self.build_tray()
        self.apply_theme()
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
        target_box = self.target_box = QGroupBox("대상 창")
        target_box.setFixedWidth(384)
        target_layout = QHBoxLayout(target_box)
        target_layout.setContentsMargins(6, 10, 6, 4)
        target_layout.setSpacing(6)
        self.targets = TargetCombo()
        self.targets.opening.connect(self.refresh_windows)
        self.targets.setFixedWidth(170)
        self.targets.setMinimumContentsLength(10)
        self.targets.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.targets.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.targets.view().setMinimumWidth(360)
        self.targets.currentIndexChanged.connect(self.change_target)
        target_layout.addWidget(self.targets)
        self.targets.setFixedHeight(30)
        self.resize_button = self.button("창 크기", self.resize_dialog)
        self.resize_button.setFixedSize(68, 30)
        target_layout.addWidget(self.resize_button)
        self.target_info = QLabel()
        self.target_info.setObjectName("muted")
        self.target_info.setMinimumWidth(0)
        self.target_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        target_layout.addWidget(self.target_info, 1)
        layout.addWidget(target_box, 0, Qt.AlignmentFlag.AlignLeft)

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
        self.pause_button = self.button("시작", self.toggle, True)
        self.pause_button.setFixedSize(106, 30)
        self.run_icons = (make_run_icon(False), make_run_icon(True))
        self.pause_button.setIconSize(QSize(16, 16))
        toolbar.addWidget(self.pause_button)
        layout.addWidget(toolbar_panel, 0, Qt.AlignmentFlag.AlignLeft)
        self.table = RowsTable()
        self.table.itemChanged.connect(self.memo_changed)
        self.table.memo_edited.connect(self.live_memo_changed)
        self.table.moved.connect(self.move_row)
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
        content_width = (sum(table.columnWidth(column) for column in range(4))
                         + table.MEMO_MIN_WIDTH + table.verticalHeader().width() + table.frameWidth() * 2)
        # Stretching the memo column must not move controls or resize the window.
        self.target_box.setFixedWidth(content_width)
        self.toolbar_panel.setFixedWidth(content_width)
        width = content_width + margins.left() + margins.right()
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
                               ("저장", self.save), ("다른 이름으로 저장…", self.save_as),
                               ("프로필 이름 변경…", self.rename_profile)):
            file_menu.addAction(text, callback)
        tools = self.menuBar().addMenu("설정")
        tools.addAction("환경설정", self.settings_dialog)
        tools.addAction("프로필 해상도 변환…", self.convert_dialog)
        about = self.menuBar().addMenu("정보")
        about.addAction("프로그램 정보", self.about_dialog)
        about.addAction("현재 프로필 정보", self.profile_info_dialog)
        self.cursor_label = QLabel("— , —")
        self.cursor_label.setObjectName("cursor")
        self.cursor_label.setFixedWidth(152)
        self.cursor_label.setContentsMargins(6, 0, 8, 0)
        self.cursor_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.cursor_label.setAccessibleName("실시간 커서 좌표")
        self.menuBar().setCornerWidget(self.cursor_label, Qt.Corner.TopRightCorner)

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
        self.pin_button.setIcon(make_pin_icon(colors.muted, colors.accent))
        self.delete_button.setIcon(make_trash_icon(colors.muted))
        if self.script_editor is not None:
            self.script_editor.highlighter.apply_theme(colors)

    def update_labels(self):
        name = self.path.stem if self.path else "새 프로필"
        shortcut = QKeySequence(qt_hotkey(self.settings.pause_hk), QKeySequence.SequenceFormat.PortableText)
        shortcut_text = shortcut.toString(QKeySequence.SequenceFormat.NativeText).replace('Control', 'Ctrl')
        self.pause_button.setText(shortcut_text.replace('&', '&&'))
        action = "정지" if self.armed else "시작"
        self.pause_button.setAccessibleName(f'{action} ({shortcut_text})')
        if self.pause_button.property("running") != self.armed:
            self.pause_button.setProperty("running", self.armed)
            self.pause_button.setIcon(self.run_icons[bool(self.armed)])
            self.pause_button.style().unpolish(self.pause_button)
            self.pause_button.style().polish(self.pause_button)
            self.pause_button.update()
        self.pause_button.setFixedWidth(max(106, self.pause_button.sizeHint().width()))
        self.setWindowTitle(f"{'● ' if self.dirty else ''}{name} — {APP_NAME}")
        if hasattr(self, "tray"):
            self.tray.setToolTip(APP_NAME + " · " + ("작동 중" if self.armed else "일시정지"))

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
        coordinate_width = self.table.fontMetrics().horizontalAdvance("-99999") + 16
        for column in (1, 2):
            self.table.setColumnWidth(column, coordinate_width)
        for i, row in enumerate(self.profile.rows):
            item = QTableWidgetItem(row.memo)
            item.setData(Qt.ItemDataRole.UserRole, row.id)
            self.table.setItem(i, 4, item)
            button = self.button(row.move_hk.upper(), lambda checked=False, rid=row.id: self.edit_key(rid))
            button.setToolTip('단축키 지정')
            button.setAccessibleName('단축키 지정')
            self.table.setCellWidget(i, 0, button)
            if row.action_mode == 'script':
                self.table.setSpan(i, 1, 1, 2)
                script_button = self.button('스크립트 작성', lambda checked=False, rid=row.id: self.edit_script(rid))
                script_button.setToolTip('스크립트 편집' if row.script else '실행할 스크립트를 작성하세요')
                self.table.setCellWidget(i, 1, script_button)
            else:
                for column, field in ((1, "x"), (2, "y")):
                    spin = NoWheelSpinBox()
                    spin.setRange(-99999, 99999)
                    spin.setValue(getattr(row, field))
                    spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
                    spin.valueChanged.connect(lambda value, rid=row.id, f=field: self.set_value(rid, f, value))
                    self.table.setCellWidget(i, column, spin)
            mode = NoWheelCombo()
            mode.setMinimumContentsLength(1)
            mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            for value, label in MODES.items():
                mode.addItem(label, value)
                mode.setItemData(mode.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
            mode.setCurrentIndex(list(MODES).index(row.action_mode))
            mode.setToolTip(MODES[row.action_mode])
            mode.currentIndexChanged.connect(lambda index, widget=mode: widget.setToolTip(list(MODES.values())[index]))
            mode.currentIndexChanged.connect(lambda index, rid=row.id: self.set_value(rid, "action_mode", list(MODES)[index]))
            self.table.setCellWidget(i, 3, mode)
        self.rebuilding = False
        self.table.update_memo_tooltips()
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
            self.table.update_memo_tooltips()

    def set_value(self, rid, field, value):
        row = self.row(rid)
        if row and getattr(row, field) != value:
            setattr(row, field, value)
            self.changed()
            if field == 'action_mode':
                QTimer.singleShot(0, lambda: self.rebuild(rid))

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
        return self.session.eligible(action, self.settings.auto_pause)

    def validate_profile_shortcuts(self, profile, settings=None):
        settings = self.settings if settings is None else settings
        Hotkeys.validate((settings.capture_hk, settings.pause_hk,
                          *(row.move_hk for row in profile.rows)))

    def rebind(self):
        values = [(self.settings.pause_hk, "pause", ""), (self.settings.capture_hk, "capture_selected", "")]
        for row in self.profile.rows:
            values.append((row.move_hk, "move", row.id))
        self.hooks.configure(values, self.settings.exclusive_hook)

    def pause_for_configuration(self):
        if self.settings.auto_pause:
            self.stop()

    def stop(self):
        self.session.stop()
        self.engine.stop()
        if hasattr(self, "pause_button"):
            self.update_labels()

    def toggle(self):
        if self.armed:
            self.stop()
            return
        if self.modal or (self.session.editor_open and self.settings.auto_pause):
            return
        if self.target is None:
            self.refresh_windows(restore_saved=True)
            if self.target is None:
                self.statusBar().showMessage('프로필에 지정된 앱을 찾을 수 없거나 구분할 수 없습니다. 앱을 선택하세요.')
                return
        if self.target and not self.native.exists(self.target):
            self.statusBar().showMessage("먼저 대상 창을 선택하세요.")
            return
        self.session.stop()
        try:
            plan = self.confirm_start()
            if plan is None:
                return
            if not self.target:
                self.native.refresh_displays()
                self.native.validate_context(0)
            plan.check_context(self.native, self.target, self.profile.resolution)
            self.rebind()
            if self.hook_enabled:
                self.hooks.start()
            self.session.start(plan)
        except WindowSizeUnavailable as error:
            self.stop()
            self.statusBar().showMessage(str(error))
            return
        except Exception as error:
            self.error(str(error))
            return
        self.update_labels()
        if plan.automatic and self.settings.auto_convert_on_start:
            warning = aspect_warning(plan.source_resolution, plan.expected_size)
            if warning:
                self.statusBar().showMessage(warning)

    def check_running_size(self, size=None):
        if self.armed and self.session.plan is not None:
            try:
                self.session.plan.check_context(self.native, self.target, self.profile.resolution, size=size)
            except WindowSizeUnavailable:
                return False
            except (OSError, ValueError, RuntimeError) as error:
                self.on_engine_error(str(error))
                return False
        return True

    @editing_dialog(hooks=False, pause=False)
    def confirm_start(self):
        target, profile, path = self.target, self.profile, self.path
        if target is None:
            self.statusBar().showMessage('앱 또는 전체화면을 선택하세요.')
            return None
        display_id = self.native.display_id

        def selection_valid():
            if target != self.target or (target and not self.native.exists(target)):
                self.statusBar().showMessage('선택한 앱이 변경되거나 닫혔습니다. 다시 선택하세요.')
                return False
            if self.profile is not profile or self.path != path or (not target and self.native.display_id != display_id):
                self.statusBar().showMessage('프로필 또는 디스플레이가 변경되었습니다. 다시 시작하세요.')
                return False
            return True

        resolution = self.profile.resolution
        automatic = False
        remember_automatic = False
        saved = dict(self.profile.target_program)
        current = self.native.window_identity(target) if target else {}
        if saved and not same_program(saved, current):
            with managed_dialog(AppMismatchDialog(saved, current, self)) as dialog:
                dialog.exec()
                choice = dialog.choice()
            if choice not in ('new', 'copy', 'change') or not selection_valid():
                return None
            if choice in ('new', 'copy'):
                if not self.confirm_changes() or not selection_valid():
                    return None
                latest = self.native.window_identity(target) if target else {}
                if latest != current and not same_program(current, latest):
                    self.statusBar().showMessage('선택한 앱이 변경되었습니다. 다시 선택하세요.')
                    return None
                candidate = deepcopy(self.profile) if choice == 'copy' else Profile.default()
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
        current_size = tuple(self.native.size(target)) if target or self.native.display_id else None
        if resolution and current_size is not None:
            if current_size != resolution:
                if target and self.settings.auto_convert_on_start:
                    choice = 'auto'
                else:
                    with managed_dialog(SizeMismatchDialog(
                            resolution, current_size, self, can_resize=bool(target))) as dialog:
                        dialog.exec()
                        choice = dialog.choice()
                        remember_automatic = (choice == 'auto' and bool(target)
                                              and dialog.skip_warning.isChecked() is True)
                if not selection_valid():
                    return None
                if choice == 'resize':
                    self.resize_dialog(profile_size=resolution)
                    return None
                elif choice == 'auto' and target:
                    if tuple(self.native.size(target)) != tuple(current_size):
                        self.statusBar().showMessage('앱 크기가 변경되었습니다. 다시 시작하세요.')
                        return None
                    automatic = True
                elif choice == 'convert' and not target:
                    self.convert_dialog(reason='프로필 좌표를 선택한 디스플레이 크기로 변환합니다. 저장 후 다시 시작하세요.')
                    return None
                else:
                    return None
        if target:
            latest = self.native.window_identity(target)
            if not same_program(current, latest):
                self.statusBar().showMessage('선택한 앱이 변경되었습니다. 다시 선택하세요.')
                return None
            current = latest
        plan = ExecutionPlan(target, resolution, current_size if target else None,
                             automatic=automatic, display_id=display_id)
        plan.check_context(self.native, self.target, self.profile.resolution)
        if remember_automatic:
            self.commit_settings(replace(self.settings, auto_convert_on_start=True))
        if current != self.profile.target_program:
            candidate = replace(self.profile, target_program=dict(current))
            self.commit_start_profile(candidate, self.path or self.next_profile_path())
            self.profile.target_program = dict(current)
        return plan

    def commit_start_profile(self, candidate, path):
        """Save the approved candidate before changing the current profile/app."""
        self.flush_pending_saves()
        self.validate_profile_shortcuts(candidate)
        result = self.store.save(path, candidate.export(), asdict(self.settings)).result()
        if not result.profile_saved:
            raise OSError(f'프로필 저장 실패: {result.error}')
        self.path, self.dirty = path, False
        self.settings_error = result.error
        if result.recent_name:
            self.settings.last_profile = result.recent_name
        if result.error:
            self.statusBar().showMessage(f'프로필 저장 완료 · 최근 프로필 정보 저장 실패: {result.error}')

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
            if row.action_mode == 'script' and not self.target:
                self.statusBar().showMessage('스크립트를 실행하려면 앱을 선택하세요.')
                return
            plan = self.session.plan
            if plan is None:
                return
            try:
                command = plan.command(row, self.settings)
            except ValueError as error:
                self.on_engine_error(str(error))
                return
            if not self.engine.submit(command):
                self.statusBar().showMessage("실행기가 종료되어 실행할 수 없습니다.")

    @editing_dialog(hooks=False, pause=False)
    def confirm_coordinate_app(self, target, *, expected=None):
        if target is None or target != self.target or (target and not self.native.exists(target)):
            raise ValueError('좌표를 가져온 앱이 변경되거나 닫혔습니다. 다시 캡처하세요.')
        current = self.native.window_identity(target) if target else {}
        if expected is not None and current != expected and not same_program(expected, current):
            raise ValueError('좌표를 가져온 앱이 변경되었습니다. 다시 캡처하세요.')
        saved = self.profile.target_program
        # This changes saved coordinate ownership, independently of start-warning preferences.
        if saved and current != saved and not same_program(saved, current):
            with managed_dialog(CoordinateAppDialog(saved, current, self)) as dialog:
                dialog.exec()
                if not dialog.confirmed():
                    return None
        if target is None or target != self.target or (target and not self.native.exists(target)):
            raise ValueError('좌표를 가져온 앱이 변경되거나 닫혔습니다. 다시 캡처하세요.')
        latest = self.native.window_identity(target) if target else {}
        if latest != current and not same_program(current, latest):
            raise ValueError('좌표를 가져온 앱이 변경되었습니다. 다시 캡처하세요.')
        return latest

    def capture(self, rid):
        if self.target is None:
            self.statusBar().showMessage('좌표를 불러오려면 앱 또는 전체화면을 선택하세요.')
            return
        row = self.row(rid)
        target = self.target
        if row and row.action_mode == 'script':
            self.statusBar().showMessage('스크립트 작성 화면에서 좌표를 추가하세요.')
            return
        if not row:
            return
        if self.target and not self.native.exists(target):
            self.statusBar().showMessage('대상 창이 닫혔습니다. 창을 다시 선택하세요.')
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
            has_coordinates = any(r.x or r.y or r.script.strip() for r in profile.rows)
            if ((basis and basis != (w, h) and not target) or
                    (not basis and has_coordinates)):
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

    def refresh_windows(self, *, restore_saved=False):
        self.targets.blockSignals(True)
        self.targets.clear()
        self.targets.addItem("미선택", None)
        self.targets.addItem("전체화면", 0)
        try:
            windows = self.native.windows(os.getpid())
            for hwnd, title in windows:
                self.targets.addItem(title, hwnd)
            index = self.targets.findData(self.target)
            if restore_saved and (self.target is None or index < 0):
                restored = self.native.find_target(self.profile.target_program, windows)
                index = self.targets.findData(restored) if restored else 0
            self.targets.setCurrentIndex(max(0, index))
        except Exception as error:
            self.statusBar().showMessage(str(error))
        self.targets.blockSignals(False)
        self.change_target(remember=False)

    def change_target(self, *_, remember=True):
        target = self.targets.currentData()
        self.targets.setToolTip(self.targets.currentText())
        if target != self.target:
            self.stop()
            self.target = target
        if remember and target is not None:
            try:
                identity = self.native.window_identity(target) if target else {}
                if identity != self.profile.target_program and (
                        not self.profile.target_program or same_program(self.profile.target_program, identity)):
                    self.profile.target_program = identity
                    self.changed()
            except OSError as error:
                self.statusBar().showMessage(str(error))

    def tick(self):
        try:
            self.display_ticks += 1
            if self.display_ticks % 10 == 0:
                previous = self.native.displays
                self.native.refresh_displays()
                if self.target == 0 and self.native.display_id:
                    before = [d for d in previous if d.id == self.native.display_id]
                    after = [d for d in self.native.displays if d.id == self.native.display_id]
                    if before != after:
                        self.stop()
                        self.statusBar().showMessage('디스플레이 연결·배치가 변경되어 중지했습니다. 환경설정에서 확인하세요.')
                    self.native.validate_context(0)
            if self.target and not self.native.exists(self.target):
                self.stop()
                self.target = None
                self.targets.blockSignals(True)
                self.targets.setCurrentIndex(0)
                self.targets.blockSignals(False)
                self.statusBar().showMessage("대상 창이 닫혔습니다. 새 창을 선택하세요.")
            if self.target:
                w, h = self.native.size(self.target)
                self.check_running_size((w, h))
                self.target_info.setText(f"{w} × {h}  ·  {'활성' if self.native.active(self.target) else '대기'}")
            else:
                self.target_info.clear()
            coordinate_target = self.script_editor.target if self.script_editor is not None else self.target
            if coordinate_target is None:
                self.cursor_label.setText('— , —')
            else:
                x, y = self.native.cursor(coordinate_target)
                self.cursor_label.setText(f"{x:>5}, {y:>5}")
        except WindowSizeUnavailable:
            known = '%d × %d · ' % self.run_size if self.run_size is not None else ''
            self.target_info.setText(known + '최소화')
            self.cursor_label.setText('— , —')
        except OSError as error:
            if self.target == 0 and self.native.display_id:
                if self.armed:
                    self.stop()
                    self.statusBar().showMessage(str(error))
                self.cursor_label.setText('디스플레이 확인 필요')
            else:
                self.target_info.setText("창 상태 확인 중")
                self.cursor_label.setText("— , —")

    @editing_dialog(hooks=True)
    def edit_key(self, rid):
        row = self.row(rid)
        if row is None:
            return
        try:
            with managed_dialog(KeyDialog(row.move_hk, self)) as dialog:
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
                        self.block_conflicting_shortcuts(error)
                        self.rebuild(rid)
                        return
                    except Exception:
                        row.move_hk = old
                        raise
                    self.changed()
                    self.rebuild(rid)
        except Exception as error:
            self.statusBar().showMessage(str(error))

    def block_conflicting_shortcuts(self, error):
        self.stop()
        self.hooks.close()
        self.hooks.configure([], self.settings.exclusive_hook)
        self.statusBar().showMessage(f'{error} · 남은 충돌을 수정하세요. 모두 해결하면 자동저장됩니다.')

    def restart_hooks(self):
        try:
            self.rebind()
            if self.hook_enabled:
                self.hooks.start()
        except ShortcutError as error:
            self.block_conflicting_shortcuts(error)
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
        focus_row = self.profile.rows[max(0, min(selected) - 1)]
        self.changed()
        self.rebuild(focus_row.id)
        self.restart_hooks()

    def move_row(self, origin, target):
        if origin == target or not 0 <= target < len(self.profile.rows):
            return
        row = self.profile.rows.pop(origin)
        self.profile.rows.insert(target, row)
        self.changed()
        self.rebuild(row.id)

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
        try:
            self.store.validate_profile_path(path)
            if not self.confirm_changes():
                return
            candidate = Profile.default()
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
                message += f' · 최근 프로필 정보 저장 실패: {self.settings_error}'
            self.statusBar().showMessage(message)
        except (ValueError, OSError, RuntimeError) as error:
            self.statusBar().showMessage(f'새 프로필 생성 실패: {error}')

    @editing_dialog(hooks=False)
    def open_profile(self):
        if not self.confirm_changes():
            return
        try:
            path, _ = QFileDialog.getOpenFileName(self, "불러오기", str(self.path or self.data_dir), "매크로 프로필 (*.json)")
            if not path:
                return
            profile = Profile.read(read_json(path))
            self.validate_profile_shortcuts(profile)
            self.finish_script_editor()
            self.profile, self.path, self.dirty = profile, Path(path), False
            self.stop()
            self.target = None
            self.refresh_windows(restore_saved=True)
            self.rebuild()
            self.rebind()
            self.persist_settings()
            self.statusBar().showMessage("프로필을 불러왔습니다. 설정 → 프로필 해상도 변환에서 내 해상도에 맞출 수 있습니다.")
        except Exception as error:
            self.error(str(error))

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
            result = future.result()
            self.save_completed(revision, result)
            return result.profile_saved
        except Exception as error:
            self.dirty = True
            self.update_labels()
            self.statusBar().showMessage(f'저장 실패: {error}')
            return False

    def save_completed(self, revision, result):
        if not self.profile_saves.complete(revision, self.path):
            return
        self.dirty = not result.profile_saved
        self.settings_error = result.error if result.profile_saved else ''
        if result.recent_name:
            self.settings.last_profile = result.recent_name
        self.update_labels()
        if not result.profile_saved:
            self.statusBar().showMessage(f'자동저장 실패: {result.error} · 변경사항은 아직 저장되지 않았습니다.')
        elif result.error:
            self.statusBar().showMessage(f'프로필 저장 완료 · 설정/최근 프로필 정보 저장 실패: {result.error}')
        else:
            self.statusBar().showMessage(f'저장 완료: {self.path}')

    def flush_pending_saves(self):
        for revision, future in self.profile_saves.pending():
            self.save_completed(revision, future.result())

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
            self.statusBar().showMessage(str(error))
            return False
        previous = self.path
        self.path = Path(path)
        if self.save():
            return True
        self.path = previous
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
            result = self.store.rename(original, destination, asdict(self.settings))
            if not result.profile_saved:
                raise OSError(result.error)
            self.path = destination
            self.settings_error = result.error
            if result.recent_name:
                self.settings.last_profile = result.recent_name
            self.update_labels()
            message = f'프로필 이름 변경 완료: {destination.stem}'
            if result.error:
                message += f' · 최근 프로필 정보 저장 실패: {result.error}'
            self.statusBar().showMessage(message)
        except (ValueError, OSError, RuntimeError) as error:
            self.statusBar().showMessage(f'이름 변경 실패: {error}')

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
            ('현재 선택', self.targets.currentText()),
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
        candidate.last_profile = self.store.settings(self.path, asdict(candidate), recover=recover)
        if candidate.display_id != self.settings.display_id:
            self.stop()
        self.settings = candidate
        self.native.display_id = candidate.display_id
        self.settings_error = ''

    @editing_dialog(hooks=True)
    def settings_dialog(self):
        try:
            displays = self.native.refresh_displays()
        except OSError as error:
            self.statusBar().showMessage(str(error))
            displays = self.native.displays
        with managed_dialog(SettingsDialog(self.settings, self, displays)) as dialog:
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
                    self.statusBar().showMessage(str(error))
                    break
                except Exception as error:
                    self.error(str(error))

    @editing_dialog(hooks=False)
    def resize_dialog(self, *, profile_size=None):
        if not self.native.exists(self.target):
            self.statusBar().showMessage("대상 창을 먼저 선택하세요.")
            return
        try:
            target = self.target
            def read_size():
                if target != self.target or not self.native.exists(target):
                    raise OSError("대상 창이 닫혔거나 변경되었습니다.")
                return self.native.size(target)
            refresh_notice = ''
            def request_resize(size):
                nonlocal refresh_notice
                read_size()
                self.stop()
                refresh_notice = resize_window(self.native, target, *size)
            with managed_dialog(ResizeDialog(
                    read_size(), self, convert_profile=False if profile_size else self.settings.resize_convert_profile,
                    request_resize=request_resize, read_size=read_size)) as dialog:
                if profile_size:
                    if not all(1 <= value <= 16384 for value in profile_size):
                        raise ValueError('창 크기는 1~16384px 범위여야 합니다. 프로필 좌표 변환을 사용하세요.')
                    dialog.change_ratio('직접 입력')
                    dialog.set_size(profile_size)
                    dialog.convert_profile.hide()
                    QTimer.singleShot(0, dialog, dialog.apply)
                accepted = dialog.exec() == QDialog.DialogCode.Accepted
                convert = not profile_size and dialog.convert_profile.isChecked()
                if not profile_size and convert != self.settings.resize_convert_profile:
                    self.commit_settings(replace(self.settings, resize_convert_profile=convert))
                if accepted:
                    message = ('창 크기를 변경했습니다. 다시 시작을 눌러 주세요.' if profile_size else
                               '창 내부 크기 변경 완료: %d × %d' % dialog.current_size)
                    self.statusBar().showMessage(message + (' · ' + refresh_notice if refresh_notice else ''))
                    if convert:
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
            current = self.native.size(self.target) if (self.target and self.native.exists(self.target)) or (self.target == 0 and self.native.display_id) else None
            with managed_dialog(ConversionDialog(
                    self.profile, self.path or self.data_dir / "macro.json",
                    current, self.save_conversion, self, reason,
                    target_label="선택한 디스플레이" if self.target == 0 and self.native.display_id else "대상 창 내부")) as dialog:
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
        result = self.store.save(path, profile.export(), asdict(self.settings)).result()
        if not result.profile_saved:
            raise ValueError(result.error)
        self.profile, self.path, self.dirty = profile, path, False
        self.settings_error = result.error
        if result.recent_name:
            self.settings.last_profile = result.recent_name
        self.rebuild()
        self.rebind()
        self.update_labels()
        message = f'변환 완료: {path.name}'
        if result.error:
            message += f' · 최근 프로필 정보 저장 실패: {result.error}'
        self.statusBar().showMessage(message)
        return True

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
        QMessageBox.information(self, '프로그램 정보', f'{APP_NAME}\n버전: {VERSION}')

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.finish_script_editor()
        self.timer.stop()
        self.stop()
        self.hooks.close()
        self.engine.shutdown()
        self.flush_pending_saves()
        self.store.close()
        self.tray.hide()

    def closeEvent(self, event):
        if self.closing:
            event.accept()
            return
        self.stop()
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
    # Keep the lock location stable across the display-name change.
    app.setApplicationName("BlueArchiveStudio")
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("Chobo")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    data_dir.mkdir(parents=True, exist_ok=True)
    instance = SingleInstance(data_dir, app)
    try:
        if not instance.acquire():
            return 0
        window = MainWindow()
        instance.activate_requested.connect(window.restore)
        app.aboutToQuit.connect(window.shutdown)
        window.show()
        return app.exec()
    except OSError as error:
        QMessageBox.critical(None, APP_NAME, str(error))
        return 1
    finally:
        instance.close()



if __name__ == "__main__":
    raise SystemExit(main())
