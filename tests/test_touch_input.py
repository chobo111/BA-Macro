import ctypes as C
from dataclasses import asdict, replace
import threading
import time
import unittest
from unittest.mock import Mock, patch
from domain import Settings
from engine import Command, Engine
from script_language import compile_script, ScriptRuntime, ScriptError
from touch_input import (TouchScriptInput, TouchDevice, PointerInfo, PointerTypeInfo,
                         DOWN, UPDATE, UP, CANCELED, INRANGE, INCONTACT)
from test_engine import FakeWindows


class Device:
    def __init__(self):
        self.events = []
        self.down = threading.Event()
        self.closed = False

    def send(self, point, flags):
        self.events.append((point, flags))
        if flags & DOWN:
            self.down.set()

    def close(self):
        self.closed = True


class TouchTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeWindows()
        self.native.point_belongs_to = Mock(return_value=True)
        self.device = Device()
        self.adapter = TouchScriptInput(self.native, lambda: self.device)
        self.command = Command(1, 30, 40, 'script', 'q', 0, 1, script_input_mode='touch')
        self.runtime = ScriptRuntime(self.adapter, lambda c: None, lambda ms, c: None, lambda k: False)

    def run_script(self, source):
        program = compile_script(source)
        self.adapter.begin(1, program)
        self.runtime.run(program, self.command)

    def test_curve_and_click_send_touch_without_moving_real_cursor(self):
        with patch.object(self.native, 'move', side_effect=AssertionError('physical cursor moved')), \
             patch.object(self.native, 'button', side_effect=AssertionError('physical mouse clicked')):
            self.run_script('curve 10,20 30,40\nclick mouse')
        self.assertEqual(self.device.events, [
            ((10, 20), DOWN | INRANGE | INCONTACT),
            ((30, 40), UPDATE | INRANGE | INCONTACT), ((30, 40), UP),
            ((200, 300), DOWN | INRANGE | INCONTACT), ((200, 300), UP)])
        self.assertEqual(self.native.position, (200, 300))

    def test_held_loop_refreshes_mouse_for_both_curve_directions(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.setUp()
                aims = [(220, 320), (440, 540), (660, 760)]
                remaining = iter(aims)
                def held(key):
                    point = next(remaining, None)
                    if point is None:
                        return False
                    self.native.position = point
                    return True
                self.runtime.held = held
                path = 'mouse 10,20' if reverse else '10,20 mouse'
                self.run_script('loop\ncurve ' + path + '\nsleep 5\nloop_end')
                starts = [point for point, flags in self.device.events if flags & DOWN]
                ends = [point for point, flags in self.device.events if flags & UP]
                self.assertEqual(starts, aims if reverse else [(10, 20)] * 3)
                self.assertEqual(ends, [(10, 20)] * 3 if reverse else aims)
                self.assertEqual(self.native.events, [])

    def test_mouse_refreshes_between_instructions_and_release_actions(self):
        def wait(ms, command):
            if ms == 7:
                self.native.position = (450, 550)
            elif ms == 9:
                self.native.position = (650, 750)
        self.runtime.wait = wait
        self.run_script('click mouse\nsleep 7\nmove mouse\nmouse_press left\n'
                        'mouse_release left\nsleep 9\nrelease_actions\nclick mouse')
        starts = [point for point, flags in self.device.events if flags & DOWN]
        self.assertEqual(starts, [(200, 300), (450, 550), (650, 750)])
        self.assertEqual(self.native.events, [])

    def test_curve_keeps_one_aim_during_drag_then_refreshes_next_curve(self):
        def wait(ms, command):
            if ms == 5:
                self.native.position = (600, 700)
        self.runtime.wait = wait
        self.run_script('curve_first_time 5\ncurve 10,20 mouse\ncurve 10,20 mouse')
        ends = [point for point, flags in self.device.events if flags & UP]
        self.assertEqual(ends, [(200, 300), (600, 700)])

    def test_live_mouse_outside_target_stops_without_extra_contact(self):
        def wait(ms, command):
            if ms == 7:
                self.native.position = (-10, 300)
        self.runtime.wait = wait
        with self.assertRaises(ScriptError):
            self.run_script('click mouse\nsleep 7\nclick mouse')
        self.assertEqual(sum(bool(flags & DOWN) for _, flags in self.device.events), 1)
        self.assertFalse(self.adapter.pressed)

    def test_virtual_move_only_changes_next_touch_location(self):
        self.run_script('move 40,50\nmouse_press left\nmouse_release left')
        self.assertEqual(self.device.events[0][0], (40, 50))
        self.assertEqual(self.native.events, [])

    def test_unsupported_commands_rejected_before_partial_input(self):
        for tail in ('mouse_press right', 'key_press a', 'text "hello"', 'loop 2\nmouse_switch middle\nloop_end'):
            with self.subTest(tail=tail), self.assertRaises(ScriptError):
                self.run_script('click 10,20\n' + tail)
        self.assertEqual(self.device.events, [])

    def test_target_required_and_occluded_points_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter.begin(0, compile_script('click 10,20'))
        self.native.active_value = False
        with self.assertRaises(ValueError):
            self.run_script('click 10,20')
        self.native.active_value = True
        self.native.point_belongs_to.return_value = False
        with self.assertRaises(ScriptError):
            self.run_script('click 10,20')
        self.assertEqual(self.device.events, [])

    def test_focus_loss_cancels_contact_at_last_injected_position(self):
        self.adapter.begin(1, compile_script('press 10,20'))
        self.adapter.move(10, 20)
        self.adapter.mouse_button('left', True)
        self.native.active_value = False
        self.adapter.mouse_button('left', False)
        self.assertEqual(self.device.events[-1], ((10, 20), UP | CANCELED))
        self.assertFalse(self.adapter.pressed)

    def test_long_hold_heartbeat_and_failed_drag_cleanup(self):
        self.adapter.begin(1, compile_script('press 10,20'))
        self.adapter.move(10, 20)
        self.adapter.mouse_button('left', True)
        self.adapter.last_frame -= 1
        self.adapter.keep_alive()
        self.assertEqual(self.device.events[-1], ((10, 20), UPDATE | INRANGE | INCONTACT))
        self.native.point_belongs_to.return_value = False
        with self.assertRaises(ValueError):
            self.adapter.move(50, 60)
        self.adapter.mouse_button('left', False)
        self.assertEqual(self.device.events[-1], ((10, 20), UP))

    def test_touch_api_packet_retry_and_failure_closes_device(self):
        device = TouchDevice.__new__(TouchDevice)
        device.handle = 123
        device.promotion_filter = None
        device.destroy = Mock()
        packets = []
        def inject(handle, packet, count):
            info = C.cast(packet, C.POINTER(PointerTypeInfo)).contents
            point = info.info.touchInfo.pointerInfo
            packets.append((info.type, point.pointerType, point.ptPixelLocation.x, point.ptPixelLocation.y, point.pointerFlags))
            return len(packets) > 1
        device.inject = inject
        with patch('touch_input.C.get_last_error', return_value=21), patch('touch_input.time.sleep'):
            device.send((-100, 200), DOWN | INRANGE | INCONTACT)
        self.assertEqual(packets[0], packets[1])
        self.assertEqual(packets[0][:4], (2, 2, -100, 200))
        device.inject = Mock(return_value=False)
        with patch('touch_input.C.get_last_error', return_value=5), self.assertRaises(OSError):
            device.send((-100, 200), UP)
        device.destroy.assert_called_once_with(123)
        self.assertIsNone(device.handle)

    def test_engine_stop_destroys_touch_and_keeps_coordinate_actions_physical(self):
        self.native.create_touch_input = lambda: self.adapter
        errors = []
        engine = Engine(self.native, lambda key: True, errors.append)
        try:
            engine.submit(replace(self.command, script='press 10,20\nsleep 600000'))
            self.assertTrue(self.device.down.wait(1))
            engine.stop()
            deadline = time.monotonic() + 1
            while not self.device.closed and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(self.device.closed)
            self.assertFalse(self.adapter.pressed)
            engine.submit(replace(self.command, mode='move_only', x=90, y=80))
            deadline = time.monotonic() + 1
            while self.native.position != (90, 80) and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(self.native.position, (90, 80))
            self.assertFalse(errors)
        finally:
            engine.shutdown()
        self.assertFalse(engine.worker.is_alive())

    def test_touch_settings_roundtrip_and_default(self):
        self.assertEqual(Settings().script_input_mode, 'mouse')
        settings = Settings(script_input_mode='touch')
        self.assertEqual(Settings.read(asdict(settings)), settings)
        with self.assertRaises(ValueError):
            Settings.read({'script_input_mode': 'unknown'})

    def test_promotion_filter_does_not_block_physical_mouse_or_unrelated_touch(self):
        from touch_input import TouchPromotionFilter
        from mouse_hook import MouseEvent
        gate = TouchPromotionFilter()
        gate.expect((120, 220))
        event = MouseEvent()
        event.point.x, event.point.y = 120, 220
        event.extra = 0
        self.assertFalse(gate.matches(event))
        event.extra = 0xFF515700  # pen
        self.assertFalse(gate.matches(event))
        event.extra = 0xFF515780  # touch promotion
        self.assertTrue(gate.matches(event))
        event.point.x = 999
        self.assertFalse(gate.matches(event))
        event.point.x = 120
        with patch('touch_input.time.monotonic', return_value=time.monotonic() + 1):
            self.assertFalse(gate.matches(event))
