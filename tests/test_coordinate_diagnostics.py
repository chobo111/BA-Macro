import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from coordinate_diagnostics import CoordinateDiagnostics
from engine import Command, Engine
from script_language import ScriptRuntime, compile_script
from touch_input import TouchScriptInput
from test_engine import FakeWindows
from test_touch_input import Device


class CoordinateDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeWindows()
        self.native.window_identity = Mock(return_value={'title': 'Game', 'exe': 'game.exe', 'class': 'Game'})
        self.environment = Mock(return_value={'BA Macro 권한': '일반', '대상 앱 권한': '일반'})
        self.history = CoordinateDiagnostics(self.environment)
        self.command = Command(1, 800, 600, 'script', 'q', 10, expected_size=(1920,1080),
                               source_resolution=(1920,1080))
        self.device = Device()
        self.adapter = TouchScriptInput(self.native, lambda: self.device)
        self.runtime = ScriptRuntime(self.adapter, lambda c: None, lambda ms,c: None, lambda k: False)

    def start(self):
        self.history.arm('DISPLAY1 150%')
        trace = self.history.begin(self.command, self.native)
        self.adapter.diagnostic = trace
        self.adapter.begin(1)
        return trace

    def test_disabled_diagnostics_never_queries_environment(self):
        self.assertIsNone(self.history.begin(self.command, self.native))
        self.environment.assert_not_called()
        self.native.window_identity.assert_not_called()

    def test_nonzero_and_negative_origins_report_local_and_screen_coordinates(self):
        for origin in ((120,80), (-1920,-200)):
            with self.subTest(origin=origin):
                self.native.screen = lambda h,x,y: (x+origin[0], y+origin[1])
                self.native.local_point = lambda h,x,y: (x-origin[0], y-origin[1])
                trace = self.start()
                self.runtime.run(compile_script('click 800,600'), self.command)
                frame = trace.frames[0]
                self.assertEqual(frame['client'], (800,600))
                self.assertEqual(frame['origin'], origin)
                self.assertEqual(frame['before'], origin)
                self.assertEqual(frame['local'], (800,600))
                self.assertEqual(frame['screen'], (800+origin[0],600+origin[1]))
                self.assertEqual(frame['result'], '성공')
                self.assertIn('게임이 실제 처리한 위치: 확인 불가', trace.text())
                self.assertIsNone(self.history.begin(self.command, self.native))

    def test_small_window_move_is_visible_in_trace_without_correcting_input(self):
        origin = [0,0]
        self.native.screen = lambda h,x,y: (x+origin[0], y+origin[1])
        self.native.local_point = lambda h,x,y: (x-origin[0], y-origin[1])
        def factory():
            origin[:] = [20,30]
            return self.device
        self.adapter.device_factory = factory
        trace = self.start()
        self.runtime.run(compile_script('click 800,600'), self.command)
        self.assertEqual(trace.frames[0]['client'], (800,600))
        self.assertEqual(trace.frames[0]['local'], (780,570))
        self.assertIn('앱 위치가 변경', trace.text())
        self.assertEqual(self.device.events[0][0], (800,600))

    def test_limit_stops_measurement_without_stopping_script(self):
        trace = self.start()
        with patch.object(self.native, 'screen', wraps=self.native.screen) as screen:
            self.runtime.run(compile_script('loop 30\nclick 800,600\nloop_end'), self.command)
        self.assertEqual(len(trace.frames), 20)
        self.assertEqual(len(self.device.events), 60)
        self.assertEqual(screen.call_count, 50)  # 30 resolves + 20 diagnostic measurements.
        self.assertFalse(trace.sampling)

    def test_stop_and_rearm_do_not_mix_old_trace_frames(self):
        old = self.start()
        self.history.arm('new display')
        self.runtime.run(compile_script('click 800,600'), self.command)
        self.assertEqual(old.frames, [])
        self.assertTrue(self.history.armed)
        self.assertIsNone(self.history.trace)

    def test_environment_and_measurement_failure_do_not_cancel_input(self):
        self.environment.side_effect = OSError('denied')
        trace = self.start()
        real_screen = self.native.screen
        self.native.screen = lambda h,x,y: (_ for _ in ()).throw(OSError('read failed')) if (x,y)==(0,0) else real_screen(h,x,y)
        self.runtime.run(compile_script('click 800,600'), self.command)
        self.assertEqual(len(self.device.events), 2)
        self.assertIn('측정 오류: read failed', trace.text())
        self.assertIn('환경 확인 오류: denied', trace.text())

    def test_failed_input_is_not_reported_as_success(self):
        trace = self.start()
        def fail(point, flags, *, before_send=None):
            before_send()
            raise OSError('input denied')
        self.device.send = fail
        with self.assertRaises(OSError):
            self.runtime.run(compile_script('click 800,600'), self.command)
        self.assertTrue(all(f['result'].startswith('실패') for f in trace.frames))

    def test_engine_finishes_script_and_move_traces(self):
        self.native.create_touch_input = lambda: self.adapter
        engine = Engine(self.native, lambda k: False, diagnostics=self.history)
        try:
            for mode in ('script', 'move_only'):
                command = replace(self.command, mode=mode, script='click 800,600', click_hold_ms=0)
                self.history.arm()
                engine.submit(command)
                deadline = time.monotonic()+2
                while time.monotonic()<deadline:
                    if self.history.trace and self.history.trace.stopped:
                        break
                    time.sleep(.005)
                trace = self.history.trace
                self.assertIsNotNone(trace)
                self.assertTrue(trace.stopped)
                self.assertEqual(trace.state, '동작 종료')
                self.assertEqual(trace.frames[0]['client'], (800,600))
        finally:
            engine.shutdown()


class CoordinateDiagnosticUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        from app import MainWindow
        self.folder = tempfile.TemporaryDirectory()
        self.window = MainWindow(data_dir=self.folder.name, hook_enabled=False)
        self.window.timer.stop()
        self.window.coordinate_diagnostics.read_environment = lambda hwnd: {}
        self.window.coordinate_diagnostics_dialog()
        self.dialog = self.window.diagnostics_window

    def tearDown(self):
        self.dialog.close()
        self.window.shutdown()
        self.window.close()
        self.qt.processEvents()
        self.folder.cleanup()

    def test_dialog_is_modeless_and_escape_cancels_recording(self):
        self.assertFalse(self.dialog.isModal())
        self.assertFalse(self.window.modal)
        self.dialog.start_button.click()
        self.assertTrue(self.window.coordinate_diagnostics.armed)
        self.dialog.reject()
        self.assertFalse(self.window.coordinate_diagnostics.armed)

    def test_copy_and_cursor_check_validate_recorded_app_and_size(self):
        native = self.window.native
        self.window.target = 123
        command = Command(123,10,20,'move_only','q',10,expected_size=(1920,1080))
        with patch.object(native, 'window_identity', return_value={'title':'Game'}), \
             patch.object(native, 'exists', return_value=True), \
             patch.object(native, 'size', return_value=(1920,1080)) as size, \
             patch.object(native, 'screen', side_effect=lambda h,x,y:(x+100,y+50)), \
             patch.object(native, 'local_point', side_effect=lambda h,x,y:(x-100,y-50)), \
             patch.object(native, 'move') as move:
            self.dialog.arm()
            trace = self.window.coordinate_diagnostics.begin(command,native)
            trace.resolved((110,70),(10,20))
            trace.record(trace.sample(native,(110,70),'커서 이동'),'성공')
            trace.finish()
            self.dialog.refresh()
            self.dialog.copy_button.click()
            self.assertIn('110,70', self.qt.clipboard().text())
            self.dialog.cursor_button.click()
            move.assert_called_once_with(110,70)
            move.reset_mock()
            size.return_value = (1280,720)
            self.dialog.cursor_button.click()
            move.assert_not_called()
            self.assertIn('크기가 변경', self.dialog.message.text())
