from dataclasses import replace
import threading
import unittest

from domain import WindowPositionChanged
from engine import Command, Engine
from script_language import ScriptRuntime, compile_script
from touch_input import TouchScriptInput, DOWN, UPDATE, UP, CANCELED
from test_engine import FakeWindows
from test_touch_input import Device


class WindowPositionInputTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeWindows()
        self.origin = [-1920, -200]
        self.native.screen = lambda h,x,y: (x+self.origin[0], y+self.origin[1])
        self.native.local_point = lambda h,x,y: (x-self.origin[0], y-self.origin[1])
        self.device = Device()
        self.adapter = TouchScriptInput(self.native, lambda: self.device)
        self.native.create_touch_input = lambda: self.adapter
        self.command = Command(1,800,600,'script','q',10,
                               expected_size=(1920,1080), expected_origin=tuple(self.origin))
        self.runtime = ScriptRuntime(self.adapter, lambda c: None, lambda ms,c: None, lambda k: False)

    def tearDown(self):
        self.adapter.close()

    def move_window(self):
        self.origin[0] += 20
        self.origin[1] += 30

    def test_window_move_during_device_creation_blocks_first_contact(self):
        def create():
            self.move_window()
            return self.device
        self.adapter.device_factory = create
        self.adapter.begin(1)
        with self.assertRaises(WindowPositionChanged):
            self.runtime.run(compile_script('click 800,600'), self.command)
        self.assertEqual(self.device.events, [])
        self.assertTrue(self.device.closed)
        self.assertFalse(self.adapter.pressed)

    def test_window_move_during_curve_cancels_contact_before_next_point(self):
        self.adapter.begin(1)
        self.runtime.wait = lambda ms,c: self.move_window()
        with self.assertRaises(WindowPositionChanged):
            self.runtime.run(compile_script('curve 800,600 900,600'), self.command)
        self.assertEqual(len(self.device.events), 2)
        self.assertTrue(self.device.events[0][1] & DOWN)
        self.assertEqual(self.device.events[-1][1], UP | CANCELED)
        self.assertFalse(any(flags & UPDATE for _,flags in self.device.events))
        self.assertFalse(self.adapter.pressed)
        self.assertFalse(self.runtime.buttons)

    def test_release_cancels_instead_of_clicking_if_window_moved_after_last_guard(self):
        self.adapter.begin(1)
        self.adapter.move(-1120,400)
        self.adapter.mouse_button('left', True)
        self.move_window()
        self.adapter.mouse_button('left', False)
        self.assertEqual(self.device.events[-1][1], UP | CANCELED)
        self.assertFalse(self.adapter.pressed)

    def test_idle_latched_input_is_closed_and_engine_stopped_on_window_move(self):
        reported = threading.Event()
        errors = []
        def report(error):
            errors.append(error)
            reported.set()
        engine = Engine(self.native, lambda key: False, report)
        try:
            engine.submit(replace(self.command, script='switch 800,600'))
            self.assertTrue(self.device.down.wait(1))
            self.move_window()
            self.assertTrue(reported.wait(1))
            self.assertTrue(engine.cancel.is_set())
            self.assertIsNone(engine.pending)
            self.assertFalse(self.adapter.pressed)
            self.assertTrue(self.device.closed)
            self.assertIn('창 위치가 변경', errors[0])
        finally:
            engine.shutdown()
        self.assertFalse(engine.worker.is_alive())

    def test_physical_move_rechecks_position_after_coordinate_resolution(self):
        original = self.native.screen
        def screen(hwnd,x,y):
            point = original(hwnd,x,y)
            if (x,y) == (800,600):
                self.move_window()
            return point
        self.native.screen = screen
        engine = Engine(self.native, lambda key: False)
        try:
            with self.assertRaises(WindowPositionChanged):
                engine.execute(replace(self.command, mode='move_only'))
            self.assertEqual(self.native.events, [])
        finally:
            engine.shutdown()

    def test_new_command_uses_new_position_after_restart(self):
        self.move_window()
        command = replace(self.command, expected_origin=tuple(self.origin))
        self.adapter.begin(1)
        self.runtime.run(compile_script('click 800,600'), command)
        self.assertEqual(self.device.events[0][0], self.native.screen(1,800,600))
        self.assertEqual(self.device.events[-1][1], UP)

    def test_restarting_touch_context_cannot_adopt_moved_window_with_contact_held(self):
        self.adapter.begin(1)
        self.runtime.run(compile_script('switch 800,600'), self.command)
        self.move_window()
        with self.assertRaises(WindowPositionChanged):
            self.adapter.begin(1)
        self.runtime.release_all()
        self.assertEqual(self.device.events[-1][1], UP | CANCELED)
