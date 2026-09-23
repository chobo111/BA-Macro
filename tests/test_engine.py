from dataclasses import replace
import threading
import time
import unittest
from engine import Engine, Command, Cancelled


class FakeWindows:
    def __init__(self):
        self.events = []
        self.active_value = True
        self.down = threading.Event()
        self.position = (200, 300)
        self.fail_down = False

    def active(self, hwnd): return self.active_value
    def size(self, hwnd): return (1920, 1080)
    def bounds(self, hwnd): return (0, 0, 1920, 1080) if hwnd else (-1920, -1080, 1920, 1080)
    def cursor(self, hwnd=None): return self.position
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
    def setUp(self):
        self.native = FakeWindows()
        self.errors = []
        self.engine = Engine(self.native, lambda key: False, self.errors.append)
        self.command = Command(1, 30, 40, "move_and_click", "q", 0, 1, 0, 0)

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
        self.assertEqual(self.native.events, [("move", 30, 40), ("button", True), ("button", False)])
        self.native.events.clear()
        self.engine.execute(replace(self.command, mode="move_only"))
        self.assertEqual(self.native.events, [("move", 30, 40)])
        self.native.events.clear()
        self.native.position = (200, 300)
        self.engine.execute(replace(self.command, mode="move_and_click"))
        self.assertEqual(self.native.position, (30, 40))
        self.assertEqual(sum(e == ("button", True) for e in self.native.events), 1)

    def test_pause_releases_button_and_clears_pending_switch(self):
        self.assertTrue(self.engine.submit(replace(self.command, click_duration=5000)))
        self.assertTrue(self.native.down.wait(1))
        start = time.monotonic()
        self.engine.stop()
        self.wait_idle()
        self.assertLess(time.monotonic() - start, .5)
        self.assertIn(("button", False), self.native.events)
        self.assertEqual(self.errors, [])

    def test_focus_loss_releases_button_without_second_click(self):
        self.engine.submit(replace(self.command, click_duration=5000))
        self.assertTrue(self.native.down.wait(1))
        self.native.active_value = False
        self.wait_idle()
        self.assertEqual(self.native.events[-1], ("button", False))
        self.assertEqual(sum(e == ("button", True) for e in self.native.events), 1)

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
