"""Modeless diagnostic controls; recording never enables the macro itself."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton


class CoordinateDiagnosticsDialog(QDialog):
    def __init__(self, diagnostics, parent):
        super().__init__(parent)
        self.diagnostics = diagnostics
        self.setWindowTitle('좌표 진단')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(640, 480)
        self.last_state = None
        layout = QVBoxLayout(self)
        guide = QLabel('기록 시작 후 매크로를 활성화하고, 대상 앱에서 행 단축키를 누르세요.\n'
                       '다음 동작의 입력을 최대 20개까지 기록합니다. 창을 닫으면 기록이 정지됩니다.')
        guide.setWordWrap(True)
        layout.addWidget(guide)
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setPlaceholderText('기록 시작을 누르세요. 진단 결과는 이번 앱 실행 중에만 보관됩니다.')
        layout.addWidget(self.viewer)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        self.start_button = QPushButton('기록 시작')
        self.start_button.clicked.connect(self.arm)
        self.stop_button = QPushButton('기록 정지')
        self.stop_button.clicked.connect(self.stop)
        self.copy_button = QPushButton('진단 결과 복사')
        self.copy_button.clicked.connect(self.copy)
        self.cursor_button = QPushButton('커서로 위치 확인')
        self.cursor_button.clicked.connect(self.check_cursor)
        close = QPushButton('닫기')
        close.clicked.connect(self.close)
        for button in (self.start_button, self.stop_button, self.copy_button, self.cursor_button, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def arm(self):
        screens = '; '.join(f'{s.name()} {round(s.devicePixelRatio() * 100)}%' for s in QApplication.screens())
        self.diagnostics.arm(screens)
        self.message.setText('다음 행 동작을 기다립니다. 진단 중에는 추가 측정으로 입력 간격이 달라질 수 있습니다.')
        self.refresh()

    def stop(self):
        self.diagnostics.stop()
        self.refresh()

    def refresh(self):
        trace = self.diagnostics.trace
        state = (id(trace), trace.revision if trace else 0, self.diagnostics.armed)
        if state != self.last_state:
            self.last_state = state
            self.viewer.setPlainText(trace.text() if trace else '')
            self.copy_button.setEnabled(trace is not None)
            self.cursor_button.setEnabled(bool(trace and trace.frames))
            self.stop_button.setEnabled(self.diagnostics.armed or bool(trace and trace.sampling))

    def copy(self):
        trace = self.diagnostics.trace
        if trace:
            QApplication.clipboard().setText(trace.text())
            self.message.setText('진단 결과를 복사했습니다.')

    def check_cursor(self):
        parent = self.parentWidget()
        trace = self.diagnostics.trace
        if trace is None:
            return
        parent.stop()
        self.diagnostics.stop()
        try:
            parent.native.move(*trace.cursor_point(parent.native, parent.target))
            self.message.setText('매크로를 정지하고 마지막 성공 기록의 위치로 커서만 이동했습니다. 클릭하지 않습니다.')
        except (OSError, ValueError) as error:
            self.message.setText(str(error))
        self.refresh()

    def done(self, result):
        self.diagnostics.stop()
        super().done(result)

    def closeEvent(self, event):
        self.diagnostics.stop()
        super().closeEvent(event)
