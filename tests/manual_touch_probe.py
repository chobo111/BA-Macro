"""Manual Windows integration probe: click Run, then inspect the result JSON.
No hooks, no user profile writes, and input is confined to this test window.
"""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton
from engine import Engine, Command
from native import Windows
from touch_input import TouchDevice, TouchScriptInput


class Probe(QWidget):
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.game_test = '--game-help' in sys.argv
        self.setWindowTitle('Blue Archive Studio - Game Touch Probe' if self.game_test else 'Blue Archive Studio - Touch Input Probe')
        self.resize(640, 420)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        layout = QVBoxLayout(self)
        self.run = QPushButton('Test game help in 3 seconds' if self.game_test else 'Run touch probe')
        self.run.clicked.connect(lambda: QTimer.singleShot(3000 if self.game_test else 250, self.start_probe))
        layout.addWidget(self.run)
        self.status = QLabel('The probe draws a touch stroke below. Physical cursor should stay at Run.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.native = Windows()
        self.native.refresh_displays()
        self.errors = []
        self.frames = []
        owner = self
        class RecordingDevice(TouchDevice):
            def send(self, position, flags):
                before = owner.native.cursor_screen()
                super().send(position, flags)
                owner.frames.append({'point': position, 'cursor_before': before,
                                     'cursor_after': owner.native.cursor_screen()})
        self.native.create_touch_input = lambda: TouchScriptInput(self.native, RecordingDevice)
        self.error.connect(self.errors.append)
        self.engine = Engine(self.native, lambda key: False, self.error.emit)
        self.received = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.finish_probe)
        self.started = 0

    def nativeEvent(self, event_type, message):
        msg = W.MSG.from_address(int(message))
        if msg.message in (0x245, 0x246, 0x247, 0x240):
            self.received.append(int(msg.message))
        return super().nativeEvent(event_type, message)

    def event(self, event):
        if event.type() in (event.Type.TouchBegin, event.Type.TouchUpdate, event.Type.TouchEnd):
            self.received.append(event.type().name)
            event.accept()
            return True
        return super().event(event)

    def start_probe(self):
        self.run.setEnabled(False)
        self.received.clear()
        self.errors.clear()
        self.frames.clear()
        self.before = self.native.cursor_screen()
        target = int(self.winId())
        script = 'curve_first_time 20\ncurve_last_time 20\ncurve 120,220 380,220'
        if self.game_test:
            import os
            candidates = [hwnd for hwnd, title in self.native.windows(os.getpid()) if title == 'Blue Archive']
            if len(candidates) != 1 or self.native.size(candidates[0]) not in ((1280, 720), (1920, 1080)):
                self.status.setText('Expected one Blue Archive window with 1280x720 client area; cancelled.')
                self.run.setEnabled(True)
                return
            target = candidates[0]
            width, height = self.native.size(target)
            script = f'click {round(267 * width / 1280)},{round(24 * height / 720)}'
        command = Command(target, 0, 0, 'script', 'q', 0, 20,
                          script=script,
                          drag_segment_duration_ms=100, script_input_mode='touch')
        self.started = time.monotonic()
        self.engine.submit(command)
        self.timer.start(100)

    def finish_probe(self):
        if time.monotonic() - self.started < .5:
            return
        self.timer.stop()
        after = self.native.cursor_screen()
        result = {'cursor_before': self.before, 'cursor_after': after,
                  'cursor_unchanged': self.before == after, 'received': self.received,
                  'errors': self.errors, 'frames': self.frames}
        result_path = Path(__file__).resolve().parents[3] / 'work' / ('touch-game-probe-result.json' if self.game_test else 'touch-probe-result.json')
        result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        self.status.setText(json.dumps(result, ensure_ascii=False))
        self.run.setEnabled(True)

    def closeEvent(self, event):
        self.timer.stop()
        self.engine.shutdown()
        event.accept()


if __name__ == '__main__':
    app = QApplication([])
    app.setStyle('Fusion')
    probe = Probe()
    probe.show()
    app.exec()
