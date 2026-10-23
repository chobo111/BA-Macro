import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from coordinate_diagnostics import CoordinateDiagnostics
from engine import Command, Engine
from script_language import ScriptRuntime, compile_script
from touch_input import TouchScriptInput
from domain import WindowPositionChanged
from test_engine import FakeWindows
from test_touch_input import Device


class CoordinateDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeWindows()
        self.native.window_identity = Mock(return_value={'title': 'Game', 'exe': 'game.exe', 'class': 'Game'})
        self.native.window_process_id = Mock(return_value=42)
        self.environment = Mock(return_value={'BA Macro 권한': '일반', '대상 앱 권한': '일반'})
        self.history = CoordinateDiagnostics(self.environment)
        self.command = Command(1, 800, 600, 'script', 'q', 10, expected_size=(1920,1080),
                               source_resolution=(1920,1080))
        self.device = Device()
        self.adapter = TouchScriptInput(self.native, lambda: self.device)
        self.runtime = ScriptRuntime(self.adapter, lambda c: None, lambda ms,c: None, lambda k: False)

    def tearDown(self):
        worker = self.history._environment_worker
        self.history.close()
        if worker is not None:
            worker.join(2)
            self.assertFalse(worker.is_alive())

    def wait_environment(self, trace):
        deadline = time.monotonic() + 1
        while trace.context.get('환경 정보') == '조회 중' and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertNotEqual(trace.context.get('환경 정보'), '조회 중')

    def start(self):
        self.history.arm('DISPLAY1 150%')
        trace = self.history.begin(self.command, self.native)
        self.wait_environment(trace)
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

    def test_small_window_move_during_initialization_blocks_input_even_with_diagnostics(self):
        origin = [0,0]
        self.native.screen = lambda h,x,y: (x+origin[0], y+origin[1])
        self.native.local_point = lambda h,x,y: (x-origin[0], y-origin[1])
        def factory():
            origin[:] = [20,30]
            return self.device
        self.adapter.device_factory = factory
        trace = self.start()
        with self.assertRaises(WindowPositionChanged):
            self.runtime.run(compile_script('click 800,600'), self.command)
        self.assertEqual(trace.frames, [])
        self.assertEqual(self.device.events, [])
        self.assertTrue(self.device.closed)

    def test_window_move_during_diagnostic_sample_blocks_contact(self):
        origin = [0,0]
        self.native.screen = lambda h,x,y: (x+origin[0], y+origin[1])
        self.native.local_point = lambda h,x,y: (x-origin[0], y-origin[1])
        trace = self.start()
        sample = trace.sample
        def sample_then_move(*args):
            frame = sample(*args)
            origin[:] = [20,30]
            return frame
        with patch.object(trace, 'sample', side_effect=sample_then_move):
            with self.assertRaises(WindowPositionChanged):
                self.runtime.run(compile_script('click 800,600'), self.command)
        self.assertEqual(self.device.events, [])
        self.assertTrue(self.device.closed)
        self.assertEqual(len(trace.frames), 1)
        self.assertIn('실패: 앱 창 위치가 변경', trace.frames[0]['result'])

    def test_curve_midpoint_does_not_reuse_previous_instruction_origin(self):
        origin = [0,0]
        self.native.screen = lambda h,x,y: (x+origin[0], y+origin[1])
        self.native.local_point = lambda h,x,y: (x-origin[0], y-origin[1])
        trace = self.start()
        self.runtime.run(compile_script('click 800,600'), self.command)
        # Resume from the moved window only after explicitly rebinding input.
        origin[:] = [20,30]
        self.adapter.begin(1)
        self.runtime.run(compile_script('curve 680,470 880,670'),
                         replace(self.command, drag_segment_duration_ms=100))
        midpoint = next(f for f in trace.frames if f['screen'] == (800,600) and f['kind'] == '이동/유지')
        self.assertEqual(midpoint['origin'], (20,30))
        self.assertEqual(midpoint['client'], (780,570))
        self.assertEqual(midpoint['client'], midpoint['local'])
        self.assertNotIn('계산 이후 앱 위치가 변경', trace.text())

    def test_environment_failure_preserves_identity_for_cursor_check(self):
        self.environment.side_effect = OSError('DPI unavailable')
        self.native.exists = Mock(return_value=True)
        trace = self.start()
        self.runtime.run(compile_script('click 800,600'), self.command)
        self.assertEqual(trace.cursor_point(self.native, 1), (800,600))
        self.assertIn('환경 확인 오류: DPI unavailable', trace.text())

    def test_slow_environment_query_does_not_block_stop_or_revive_old_recording(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def query(hwnd):
            calls.append(hwnd)
            if len(calls) == 1:
                entered.set()
                if not release.wait(2):
                    raise TimeoutError('test query timed out')
                return {'environment': 'old'}
            return {'environment': 'new'}
        self.history.read_environment = query
        self.history.arm('old display')
        old = self.history.begin(self.command, self.native)
        try:
            self.assertTrue(entered.wait(1))
            worker = self.history._environment_worker
            # Intermediate pending requests must be replaced, not accumulated.
            for index in range(5):
                self.history.arm(f'new display {index}')
                latest = self.history.begin(self.command, self.native)
                self.assertIs(self.history._environment_worker, worker)
            self.assertTrue(old.stopped)
            self.assertEqual(old.context['환경 정보'], '조회 중단')
            self.assertEqual(calls, [1])
        finally:
            release.set()
        self.wait_environment(latest)
        self.assertIs(self.history.trace, latest)
        self.assertEqual(latest.context['디스플레이 배율'], 'new display 4')
        self.assertEqual(latest.context['environment'], 'new')
        self.assertNotIn('environment', old.context)
        self.assertEqual(calls, [1, 1])

    def test_slow_environment_does_not_delay_input_stop_replacement_or_shutdown(self):
        from touch_input import UP
        entered, release, released, moved = (threading.Event() for _ in range(4))
        def query(hwnd):
            entered.set()
            if not release.wait(3):
                raise TimeoutError('test query timed out')
            return {}
        self.history.read_environment = query
        self.native.create_touch_input = lambda: self.adapter
        original_send = self.device.send
        def send(point, flags, **kwargs):
            original_send(point, flags, **kwargs)
            if flags & UP:
                released.set()
        self.device.send = send
        self.native.move = Mock(side_effect=lambda x,y: moved.set())
        engine = Engine(self.native, lambda k: True, diagnostics=self.history)
        try:
            self.history.arm()
            engine.submit(replace(self.command, script='press 800,600\nsleep 600000'))
            self.assertTrue(entered.wait(1))
            self.assertTrue(self.device.down.wait(.5), 'input waited for environment data')
            engine.stop()
            self.assertTrue(released.wait(.5), 'stop waited for environment data')
            engine.submit(replace(self.command, mode='move_only'))
            self.assertTrue(moved.wait(.5), 'replacement waited for environment data')
            self.history.close()
            engine.shutdown()
            self.assertFalse(engine.worker.is_alive())
            self.assertFalse(release.is_set())
        finally:
            release.set()
            engine.shutdown()

    def test_close_discards_pending_environment_and_late_results(self):
        entered, release = threading.Event(), threading.Event()
        def query(hwnd):
            entered.set()
            release.wait(2)
            return {'late': 'value'}
        self.history.read_environment = Mock(side_effect=query)
        self.history.arm()
        old = self.history.begin(self.command, self.native)
        self.assertTrue(entered.wait(1))
        worker = self.history._environment_worker
        try:
            self.history.arm()
            pending = self.history.begin(self.command, self.native)
            self.history.close()
            self.history.arm()
            self.assertIsNone(self.history.begin(self.command, self.native))
            self.assertFalse(self.history.armed)
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.history.read_environment.assert_called_once()
        self.assertNotIn('late', old.context)
        self.assertNotIn('late', pending.context)
        self.assertEqual(pending.context['환경 정보'], '조회 중단')

    def test_cursor_check_allows_title_changes_but_rejects_other_windows_or_apps(self):
        self.native.exists = Mock(return_value=True)
        trace = self.start()
        self.runtime.run(compile_script('click 800,600'), self.command)
        changed_title = {'title': 'Game 59 FPS', 'exe': 'GAME.EXE', 'class': 'Game'}
        self.native.window_identity.return_value = changed_title
        self.assertEqual(trace.cursor_point(self.native, 1), (800,600))
        for target, pid, identity in (
            (2, 42, changed_title), (1, 43, changed_title),
            (1, 42, {**changed_title, 'exe': 'other.exe'}),
            (1, 42, {**changed_title, 'class': 'Other'}),
        ):
            with self.subTest(target=target, pid=pid, identity=identity):
                self.native.window_process_id.return_value = pid
                self.native.window_identity.return_value = identity
                with self.assertRaises(ValueError):
                    trace.cursor_point(self.native, target)

    def test_environment_drops_results_from_replaced_process(self):
        def query(hwnd):
            self.native.window_process_id.return_value = 43
            return {'wrong app': 'value'}
        self.history.read_environment = query
        trace = self.start()
        self.assertNotIn('wrong app', trace.context)
        self.assertIn('환경 조회 중 앱이 변경', trace.context['환경 확인 오류'])

    def test_dpi_awareness_is_read_on_input_thread_and_environment_on_worker(self):
        caller = threading.get_ident()
        threads = {}
        def read_dpi():
            threads['dpi'] = threading.get_ident()
            return 'input dpi'
        def query(hwnd):
            threads['environment'] = threading.get_ident()
            return {}
        self.history.read_environment = query
        with patch('coordinate_diagnostics.input_dpi_awareness', side_effect=read_dpi):
            trace = self.start()
        self.assertEqual(threads['dpi'], caller)
        self.assertNotEqual(threads['environment'], caller)
        self.assertEqual(trace.context['입력 스레드 DPI 인식'], 'input dpi')

    def test_limit_stops_measurement_without_stopping_script(self):
        trace = self.start()
        with patch.object(trace, 'sample', wraps=trace.sample) as sample:
            self.runtime.run(compile_script('loop 30\nclick 800,600\nloop_end'), self.command)
        self.assertEqual(len(trace.frames), 20)
        self.assertEqual(len(self.device.events), 60)
        self.assertEqual(sample.call_count, 20)
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
             patch.object(native, 'window_process_id', return_value=42), \
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
