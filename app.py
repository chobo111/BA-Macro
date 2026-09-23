from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import sys

from PySide6.QtCore import Qt, Signal, QObject, QTimer, QStandardPaths, QLockFile, QSize

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidgetItem, QSpinBox, QFileDialog, QMessageBox, QDialog, QFormLayout,
    QDialogButtonBox, QSystemTrayIcon, QMenu, QGroupBox, QSizePolicy,
)

from domain import MODES, Profile, Row, Settings, read_json
from engine import Engine, Command
from native import Windows
from hotkeys import Hotkeys
from script_language import compile_script
from script_editor import ScriptEditor
from persistence import ProfileStore
from session import ExecutionSession, editing_dialog
from widgets import (make_icon, make_pin_icon, make_trash_icon, TargetCombo, RowsTable,
                     BriefStatusBar, KeyDialog, SettingsDialog, ResizeDialog)

BASE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
MODE_LABELS = MODES


class Bridge(QObject):
    action = Signal(str, str)
    error = Signal(str)
    saved = Signal(int, object)


class MainWindow(QMainWindow):
    armed = property(lambda self: self.session.armed, lambda self, value: setattr(self.session, "armed", value))
    modal = property(lambda self: self.session.modal, lambda self, value: setattr(self.session, "modal", value))
    closing = property(lambda self: self.session.closing, lambda self, value: setattr(self.session, "closing", value))
    target = property(lambda self: self.session.target, lambda self, value: setattr(self.session, "target", value))

    def __init__(self, data_dir=None, hook_enabled=True):
        super().__init__()
        self.setWindowTitle("Blue Archive Studio")
        self.setWindowIcon(make_icon())
        self.resize(420, 420)
        self.setMinimumHeight(340)
        self.native = Windows()
        self.session = ExecutionSession(self.native)
        self.dirty = False
        self.rebuilding = False
        self.path = None
        self.data_dir = Path(data_dir or BASE).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = ProfileStore(self.data_dir)
        self.settings_path = self.store.settings_path
        self.save_revision = 0
        self.pending_saves = {}
        self.settings_error = ""
        self.settings = Settings()
        self.profile = Profile.default()
        self.load_error = ""
        self.hook_enabled = hook_enabled
        loaded = self.store.load()
        self.settings, self.profile, self.path = loaded.settings, loaded.profile, loaded.path
        self.load_error = loaded.warning
        self.bridge = Bridge()
        self.engine = Engine(self.native, lambda key: self.hooks.held(key), self.bridge.error.emit)
        self.hooks = Hotkeys(self.bridge.action.emit, self.eligible, self.engine.stop)
        self.bridge.action.connect(self.dispatch, Qt.ConnectionType.QueuedConnection)
        self.bridge.error.connect(self.on_engine_error, Qt.ConnectionType.QueuedConnection)
        self.bridge.saved.connect(self.save_completed, Qt.ConnectionType.QueuedConnection)
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

    @editing_dialog(hooks=True)
    def edit_script(self, rid):
        row = self.row(rid)
        if row is None:
            return
        dialog = ScriptEditor(row.script, self.native, self.target, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            row.script = dialog.source
            self.changed()
            self.rebuild(rid)
            if not self.dirty:
                self.statusBar().showMessage('스크립트를 반영하고 자동저장했습니다.')

    def eligible(self, action):
        return self.session.eligible(action, self.settings.auto_pause)

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
            program = None
            if row.action_mode == 'script':
                try:
                    program = compile_script(row.script)
                except ValueError as error:
                    self.on_engine_error(str(error))
                    return
            command = Command(self.target, row.x, row.y, row.action_mode, row.move_hk,
                              s.click_delay, s.click_duration, s.after_action_delay, row.script, curve_duration=s.curve_duration, program=program)
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

    @editing_dialog(hooks=True)
    def edit_key(self, rid):
        row = self.row(rid)
        if row is None:
            return
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
        self.flush_pending_saves()
        self.pause_for_configuration()
        if not self.dirty:
            return True
        previous_modal = self.modal
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
            self.modal = previous_modal
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

    @editing_dialog(hooks=False)
    def open_profile(self):
        if not self.confirm_changes():
            return
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

    def next_profile_path(self):
        self.flush_pending_saves()
        return self.store.next_path()

    def save(self, automatic=False):
        if self.path is None:
            if automatic:
                self.path = self.next_profile_path()
            else:
                return self.save_as()
        try:
            self.profile.validate((self.settings.capture_hk, self.settings.pause_hk))
            self.save_revision += 1
            revision = self.save_revision
            future = self.store.save(self.path, self.profile.export(), asdict(self.settings))
            self.pending_saves[revision] = (self.path, future)
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
        pending = self.pending_saves.pop(revision, None)
        if not pending or revision != self.save_revision or pending[0] != self.path:
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
        for revision, (_, future) in list(self.pending_saves.items()):
            self.save_completed(revision, future.result())

    @editing_dialog(hooks=False)
    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "프로필 저장", str(self.path or self.data_dir / "macro.json"), "매크로 프로필 (*.json)")
        if not path:
            return False
        previous = self.path
        self.path = Path(path)
        if self.save():
            return True
        self.path = previous
        return False

    def persist_settings(self):
        self.flush_pending_saves()
        self.settings.last_profile = self.store.settings(self.path, asdict(self.settings))
        self.settings_error = ''

    @editing_dialog(hooks=True)
    def settings_dialog(self):
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

    @editing_dialog(hooks=False)
    def resize_dialog(self):
        if not self.native.exists(self.target):
            self.statusBar().showMessage("대상 창을 먼저 선택하세요.")
            return
        try:
            dialog = ResizeDialog(self.native.size(self.target), self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.native.resize(self.target, *dialog.value())
                self.statusBar().showMessage("창 크기 변경을 요청했습니다. 저장 좌표는 설정 → 좌표 변환에서 조정하세요.")
        except Exception as error:
            self.error(str(error))

    @editing_dialog(hooks=False)
    def convert_dialog(self):
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

    @editing_dialog(hooks=False)
    def help_dialog(self):
        QMessageBox.information(self, "사용 방법", "1. 대상 게임/에뮬레이터 창을 선택합니다.\n"
            "2. 행을 선택하고 대상 창의 원하는 위치에 커서를 놓은 뒤 좌표 캡처 키(기본 Ctrl+S)를 누릅니다.\n"
            "3. 단축키와 동작을 지정하면 변경사항이 자동저장됩니다.\n"
            "4. 스크립트 실행 모드는 스크립트 작성 버튼에서 명령을 작성합니다.\n"
            "5. 작동 시작을 누른 뒤 단축키를 누릅니다.\n\n"
            "자동 중지 옵션이 켜져 있으면 편집 시 일시정지됩니다. 작동/정지 키는 일시정지 중에도 유지됩니다.\n"
            "기존 매크로의 JSON 프로필을 열 수 있습니다. 다른 매크로 앱은 종료한 뒤 사용하세요.")

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
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
