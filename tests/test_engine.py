from dataclasses import replace
import threading
import time
import unittest
from engine import Engine, Command, Cancelled


class FakeScriptInput:
    """In-memory interpreter backend used to isolate scheduling tests."""
    def __init__(self, native): self.native = native
    def __getattr__(self, name): return getattr(self.native, name)
    def begin(self, target, program): pass
    def keep_alive(self): pass
    def close(self): pass
    def move(self, x, y): self.native.events.append(('move', x, y))


class FakeWindows:
    def __init__(self):
        self.events = []
        self.active_value = True
        self.down = threading.Event()
        self.position = (200, 300)
        self.fail_down = False

    def create_touch_input(self): return FakeScriptInput(self)

    def active(self, hwnd): return self.active_value
    def size(self, hwnd): return (1920, 1080)
    def bounds(self, hwnd): return (0, 0, 1920, 1080) if hwnd else (-1920, -1080, 1920, 1080)
    def cursor(self, hwnd=None): return self.position
    def cursor_screen(self): return self.position
    def screen(self, hwnd, x, y): return x, y

    def move(self, x, y):
        self.position = (x, y)
        self.events.append(("move", x, y))

    def button(self, down):
        self.events.append(("button", down))
        if down:
            self.down.set()
            if self.fail_down:
                raise OSError("injected failure")


class EngineTests(unittest.TestCase):
    def test_precompiled_program_is_not_compiled_again(self):
        from unittest.mock import patch
        from script_language import compile_script
        program = compile_script('sleep 1')
        with patch('engine.compile_script', side_effect=AssertionError('compiled twice')):
            self.engine.submit(replace(self.command, mode='script', script='sleep 1', program=program))
            self.wait_idle()
        self.assertEqual(self.errors, [])

    def setUp(self):
        self.native = FakeWindows()
        self.errors = []
        self.engine = Engine(self.native, lambda key: False, self.errors.append)
        self.command = Command(1, 30, 40, "move_and_click", "q", 0, 1)

    def tearDown(self):
        self.engine.shutdown()
        self.assertFalse(self.engine.worker.is_alive())

    def wait_idle(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.engine.condition:
                if not self.engine.busy and self.engine.pending is None:
                    return
            time.sleep(.005)
        self.fail("worker did not finish")

    def test_coordinate_modes(self):
        self.engine.execute(self.command)
        self.assertEqual(self.native.events, [("move", 30, 40), ("button", True), ("button", False), ("move", 200, 300)])
        self.native.events.clear()
        self.engine.execute(replace(self.command, mode="move_only"))
        self.assertEqual(self.native.events, [("move", 30, 40)])
        self.native.events.clear()
        self.native.position = (200, 300)
        self.engine.execute(replace(self.command, mode="move_and_click"))
        self.assertEqual(self.native.position, (200, 300))
        self.assertEqual(sum(e == ("button", True) for e in self.native.events), 1)

    def test_pause_releases_button_and_clears_pending_switch(self):
        self.assertTrue(self.engine.submit(replace(self.command, click_hold_ms=5000)))
        self.assertTrue(self.native.down.wait(1))
        start = time.monotonic()
        self.engine.stop()
        self.wait_idle()
        self.assertLess(time.monotonic() - start, .5)
        self.assertIn(("button", False), self.native.events)
        self.assertEqual(self.native.position, (200, 300))
        self.assertEqual(self.errors, [])

    def test_focus_loss_releases_button_without_second_click(self):
        self.engine.submit(replace(self.command, click_hold_ms=5000))
        self.assertTrue(self.native.down.wait(1))
        self.native.active_value = False
        self.wait_idle()
        self.assertEqual(self.native.events[-2:], [("button", False), ("move", 200, 300)])
        self.assertEqual(sum(e == ("button", True) for e in self.native.events), 1)

    def test_returns_to_original_screen_point_outside_target(self):
        self.native.position = (-2200, -100)
        self.engine.execute(self.command)
        self.assertEqual(self.native.position, (-2200, -100))
        self.assertEqual(self.native.events[-2:], [("button", False), ("move", -2200, -100)])

    def test_cursor_return_wait_occurs_after_release_before_restore(self):
        from unittest.mock import patch
        def record_wait(milliseconds, command):
            self.native.events.append(("wait", milliseconds))
        with patch.object(self.engine, 'wait', side_effect=record_wait):
            self.engine.execute(replace(self.command, cursor_return_delay_ms=80))
        self.assertEqual(self.native.events, [
            ("move", 30, 40), ("wait", 0), ("button", True), ("wait", 1),
            ("button", False), ("wait", 80), ("move", 200, 300)])
        self.native.events.clear()
        with patch.object(self.engine, 'wait') as wait:
            self.engine.execute(replace(self.command, mode='move_only'))
            wait.assert_not_called()

    def test_cursor_return_wait_can_be_cancelled_without_holding_button(self):
        from unittest.mock import patch
        entered = threading.Event()
        original_wait = self.engine.wait
        def wait(milliseconds, command):
            if milliseconds == 5000:
                entered.set()
            original_wait(milliseconds, command)
        with patch.object(self.engine, 'wait', side_effect=wait):
            self.engine.submit(replace(self.command, cursor_return_delay_ms=5000))
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.native.events[-1], ("button", False))
            start = time.monotonic()
            self.engine.stop()
            self.wait_idle()
            self.assertLess(time.monotonic() - start, .5)
        self.assertEqual(self.native.position, (200, 300))
        self.assertEqual(self.errors, [])

    def test_disabled_cursor_return_skips_restore_and_return_wait(self):
        from unittest.mock import patch
        with patch.object(self.engine, 'wait') as wait, patch.object(self.native, 'cursor_screen') as cursor:
            self.engine.execute(replace(self.command, return_cursor=False, cursor_return_delay_ms=5000))
            cursor.assert_not_called()
        self.assertEqual([call.args[0] for call in wait.call_args_list], [0, 1])
        self.assertEqual(self.native.events, [("move", 30, 40), ("button", True), ("button", False)])
        self.assertEqual(self.native.position, (30, 40))

    def test_inactive_window_never_receives_input(self):
        self.native.active_value = False
        with self.assertRaises(Cancelled):
            self.engine.execute(self.command)
        self.assertEqual(self.native.events, [])

    def test_out_of_bounds_rejected_before_move(self):
        with self.assertRaises(ValueError):
            self.engine.execute(replace(self.command, x=1920))
        self.assertEqual(self.native.events, [])

    def test_release_attempted_when_send_fails(self):
        self.native.fail_down = True
        with self.assertRaises(OSError):
            self.engine.execute(self.command)
        self.assertIn(("button", False), self.native.events)
        self.assertEqual(self.native.position, (200, 300))

    def test_held_coordinate_key_clicks_only_once(self):
        self.engine.held = lambda key: True
        self.engine.submit(self.command)
        self.wait_idle()
        self.assertEqual(sum(e == ("button", True) for e in self.native.events), 1)

    def test_fullscreen_accepts_negative_coordinates_without_active_window(self):
        self.native.active_value = False
        self.engine.execute(replace(self.command, hwnd=0, x=-500, y=-300))
        self.assertEqual(self.native.events[0], ("move", -500, -300))
        self.assertEqual(sum(e == ("button", True) for e in self.native.events), 1)

    def test_fullscreen_rejects_off_desktop_and_still_cancels(self):
        with self.assertRaises(ValueError):
            self.engine.execute(replace(self.command, hwnd=0, x=-2000))
        self.engine.stop()
        with self.assertRaises(Cancelled):
            self.engine.execute(replace(self.command, hwnd=0))
