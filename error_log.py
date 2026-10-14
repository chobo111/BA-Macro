"""Bounded, session-only error history, shared by status messages and dialogs."""
from collections import deque
from datetime import datetime
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QApplication


class ErrorLog(QObject):
    changed = Signal()

    def __init__(self, parent=None, limit=200):
        super().__init__(parent)
        self.entries = deque(maxlen=limit)

    def record(self, source, message, profile=''):
        if not message:
            return
        message = str(message)
        if len(message) > 4000:
            message = message[:4000] + '\n… 내용 생략'
        self.entries.append((datetime.now().strftime('%Y-%m-%d %H:%M:%S'), source, profile, message))
        self.changed.emit()

    def clear(self):
        self.entries.clear()
        self.changed.emit()

    def text(self):
        return '\n\n'.join(f'[{time}] {source} · {profile}\n{message}'
                           for time, source, profile, message in self.entries)


class ErrorLogDialog(QDialog):
    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.setWindowTitle('오류 로그')
        self.resize(620, 380)
        self.history = history
        layout = QVBoxLayout(self)
        self.count = QLabel()
        layout.addWidget(self.count)
        self.viewer = QPlainTextEdit()
        self.viewer.setObjectName('errorLog')
        self.viewer.setReadOnly(True)
        self.viewer.setPlaceholderText('기록된 오류가 없습니다.')
        layout.addWidget(self.viewer)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton('전체 복사')
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(history.text()))
        self.clear_button = QPushButton('비우기')
        self.clear_button.clicked.connect(history.clear)
        close = QPushButton('닫기')
        close.clicked.connect(self.accept)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)
        history.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.count.setText(f'이번 실행 중 최근 {self.history.entries.maxlen}건 · {len(self.history.entries)}건 기록')
        self.viewer.setPlainText(self.history.text())
        self.viewer.verticalScrollBar().setValue(self.viewer.verticalScrollBar().maximum())
        self.copy_button.setEnabled(bool(self.history.entries))
        self.clear_button.setEnabled(bool(self.history.entries))
