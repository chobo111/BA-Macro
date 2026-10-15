"""Compact profile selection and read-only details for the main window."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QMenu, QFormLayout, QLabel, QLineEdit, QSizePolicy,
)

from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPen

from domain import read_json
from widgets import TargetCombo


def recent_profiles(paths, current):
    """Keep a small ordered history without changing or scanning other folders."""
    result = []
    for value in ([current] if current is not None else []) + list(paths):
        path = Path(value).resolve()
        if path not in result:
            result.append(path)
    return [str(path) for path in result[:10]]


def profile_entries(directory, current, recent):
    """Refresh only on demand; malformed unrelated JSON is not a profile entry."""
    result, seen = [], set()
    def add(path, group):
        path = Path(path).resolve()
        if path in seen or not path.is_file() or path.suffix.lower() != '.json':
            return
        try:
            data = read_json(path)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or not isinstance(data.get('macros'), list):
            return
        seen.add(path)
        result.append((path, group))
    if current is not None:
        add(current, '최근 사용')
    for path in recent:
        add(path, '최근 사용')
    for path in sorted(Path(directory).glob('*.json'), key=lambda path: path.name.casefold()):
        add(path, '이 폴더')
    return result


class ProfilePanel(QWidget):
    selected = Signal(object)
    opening = Signal()
    expanded = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.current_path = None
        self._saved_app = '지정된 앱 없음'
        self._basis = '알 수 없음'
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.selector = TargetCombo()
        self.selector.setAccessibleName('프로필 선택')
        self.selector.setMinimumWidth(0)
        self.selector.setFixedHeight(30)
        self.selector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.selector.view().setMinimumWidth(360)
        self.selector.opening.connect(self.opening)
        self.selector.activated.connect(self.choose)
        row.addWidget(self.selector, 1)
        self.manage = QPushButton()
        self.manage.setObjectName('profileManage')
        self.manage.setAccessibleName('프로필 관리')
        self.manage.setFixedSize(32, 30)
        self.menu = QMenu(self.manage)
        self.menu.setObjectName('appMenu')
        self.menu.setMinimumWidth(190)
        self.manage.setMenu(self.menu)
        row.addWidget(self.manage)
        layout.addLayout(row)
        self.summary = QPushButton()
        self.summary.setObjectName('profileSummary')
        self.summary.setCheckable(True)
        self.summary.setFixedHeight(24)
        self.summary.setMinimumWidth(0)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.summary.toggled.connect(self.show_details)
        layout.addWidget(self.summary)
        self.details = QWidget()
        form = QFormLayout(self.details)
        form.setContentsMargins(4, 2, 4, 6)
        form.setVerticalSpacing(4)
        self.values = {}
        for title in ('파일 위치', '동작 수', '저장 상태'):
            if title == '파일 위치':
                label = QLineEdit()
                label.setReadOnly(True)
                label.setFrame(False)
            else:
                label = QLabel()
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            form.addRow(title, label)
            self.values[title] = label
        layout.addWidget(self.details)
        self.details.hide()
        self.set_icon_color(self.palette().text().color().name())

    def set_icon_color(self, color):
        self._arrows = []
        for kind in ('right', 'down', 'menu'):
            pixmap = QPixmap(32, 32)
            pixmap.setDevicePixelRatio(2)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(color), 1.5))
            if kind == 'menu':
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                for x in (3, 7, 11):
                    painter.drawEllipse(x, 7, 2, 2)
            else:
                points = (6, 4, 10, 8, 6, 12) if kind == 'right' else (4, 6, 8, 10, 12, 6)
                painter.drawLine(*points[:4])
                painter.drawLine(*points[2:])
            painter.end()
            icon = QIcon(pixmap)
            if kind == 'menu':
                self.manage.setIcon(icon)
            else:
                self._arrows.append(icon)
        self.elide_summary()

    def set_paths(self, entries, current):
        current = Path(current).resolve() if current is not None else None
        with QSignalBlocker(self.selector):
            self.selector.clear()
            previous_group = None
            for path, group in entries:
                if group != previous_group:
                    if self.selector.count():
                        self.selector.insertSeparator(self.selector.count())
                    self.selector.addItem(group)
                    self.selector.model().item(self.selector.count() - 1).setEnabled(False)
                    previous_group = group
                self.selector.addItem(path.stem, str(path))
                index = self.selector.count() - 1
                self.selector.setItemData(index, str(path), Qt.ItemDataRole.ToolTipRole)
                self.selector.setItemData(index, Qt.CheckState.Checked if path == current else Qt.CheckState.Unchecked,
                                          Qt.ItemDataRole.CheckStateRole)
        self.set_current(current)

    def set_current(self, path):
        self.current_path = Path(path).resolve() if path is not None else None
        value = str(self.current_path) if self.current_path else None
        with QSignalBlocker(self.selector):
            index = self.selector.findData(value) if value else self.selector.findText('새 프로필')
            if index < 0:
                self.selector.insertItem(0, self.current_path.stem if self.current_path else '새 프로필', value)
                index = 0
            self.selector.setCurrentIndex(index)
            for i in range(self.selector.count()):
                if self.selector.itemData(i) is not None:
                    self.selector.setItemData(i, Qt.CheckState.Checked if i == index else Qt.CheckState.Unchecked,
                                              Qt.ItemDataRole.CheckStateRole)
        self.selector.setToolTip(value or '')

    def choose(self, index):
        path = self.selector.itemData(index)
        self.set_current(self.current_path)
        if path is not None and Path(path) != self.current_path:
            self.selected.emit(Path(path))

    def set_summary(self, app, basis):
        self._saved_app, self._basis = app, basis
        self.elide_summary()

    def elide_summary(self):
        suffix = f' · 기준 {self._basis}'
        metrics = self.summary.fontMetrics()
        available = max(10, self.width() - metrics.horizontalAdvance('지정 앱 ' + suffix) - 32)
        app = metrics.elidedText(self._saved_app, Qt.TextElideMode.ElideRight, available)
        self.summary.setIcon(self._arrows[int(self.summary.isChecked())])
        self.summary.setText(f'지정 앱 {app}{suffix}')
        self.summary.setAccessibleName(f'프로필 정보: 지정 앱 {self._saved_app}, 기준 {self._basis}')

    def show_details(self, visible):
        self.details.setVisible(visible)
        self.elide_summary()
        self.expanded.emit(visible)

    def set_details(self, details):
        values = dict(details)
        for name, label in self.values.items():
            text = values.get(name, '')
            if label.text() != text:
                label.setText(text)
                if isinstance(label, QLineEdit):
                    label.setCursorPosition(0)
                    label.setToolTip(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.elide_summary()
