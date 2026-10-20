from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import patch
from engine import Engine, Command, Cancelled


class FakeScriptInput:
    """In-memory interpreter backend used to isolate scheduling tests."""
    def __init__(self, native): self.native = native
    def __getattr__(self, name): return getattr(self.native, name)
    def begin(self, target, *, check_input=None): pass
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
    def bounds(self, hwnd): return (0, 0, 1920, 1080)
    def cursor(self, hwnd=None): return self.position
    def cursor_screen(self): return self.position
    def screen(self, hwnd, x, y): return x, y
    def local_point(self, hwnd, x, y): return x, y
    def point_belongs_to(self, hwnd, x, y): return True

    def move(self, x, y):
        self.position = (x, y)
        self.events.append(("move", x, y))

    def mouse_button(self, button, down):
        self.events.append(('mouse', button, down))
        if down:
            self.down.set()
            if self.fail_down:
                raise OSError('injected failure')


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
        self.command = Command(1, 30, 40, "move_only", "q", 1)

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

    def test_worker_error_keeps_submitted_profile_and_row_context(self):
        with patch.object(self.native, 'move', side_effect=OSError('move failed')):
            self.engine.submit(replace(self.command, error_context='raid.json · 2행 · Q'))
            self.wait_idle()
        self.assertEqual(self.errors, ['raid.json · 2행 · Q\nmove failed'])

    def test_wait_counts_guard_time_towards_requested_duration(self):
        for duration in (3, 100):
            with self.subTest(milliseconds=duration):
                clock = [0.0]
                def guard(_):
                    clock[0] += .002
                def wait(timeout):
                    self.assertGreater(timeout, 0)
                    self.assertLessEqual(timeout, .01)
                    clock[0] += timeout
                    return False
                with patch('engine.monotonic', side_effect=lambda: clock[0]), \
                        patch.object(self.engine, 'guard', side_effect=guard), \
                        patch.object(self.engine.cancel, 'wait', side_effect=wait):
                    self.engine.wait(duration, self.command)
                self.assertGreaterEqual(clock[0], duration / 1000)
                # Only the final safety check may extend beyond the deadline.
                self.assertLessEqual(clock[0], duration / 1000 + .002001)

    def test_coordinate_action_moves_without_click_or_restore(self):
        self.engine.execute(self.command)
        self.assertEqual(self.native.events, [('move', 30, 40)])
        self.assertEqual(self.native.position, (30, 40))

    def test_pause_releases_script_touch_and_clears_pending_switch(self):
        self.assertTrue(self.engine.submit(self.held_click()))
        self.assertTrue(self.native.down.wait(1))
        start = time.monotonic()
        self.engine.stop()
        self.wait_idle()
        self.assertLess(time.monotonic() - start, .5)
        self.assertIn(('mouse', 'left', False), self.native.events)
        self.assertEqual(self.native.position, (200, 300))
        self.assertEqual(self.errors, [])

    def test_focus_loss_releases_script_touch_without_second_click(self):
        self.engine.submit(self.held_click())
        self.assertTrue(self.native.down.wait(1))
        self.native.active_value = False
        self.wait_idle()
        self.assertEqual(self.native.events[-1], ('mouse', 'left', False))
        self.assertEqual(self.native.events.count(('mouse', 'left', True)), 1)
        self.assertEqual(self.native.position, (200, 300))


    def test_inactive_window_never_receives_input(self):
        self.native.active_value = False
        with self.assertRaises(Cancelled):
            self.engine.execute(self.command)
        self.assertEqual(self.native.events, [])

    def test_out_of_bounds_rejected_before_move(self):
        with self.assertRaises(ValueError):
            self.engine.execute(replace(self.command, x=1920))
        self.assertEqual(self.native.events, [])

    def test_script_release_attempted_when_send_fails(self):
        self.native.fail_down = True
        self.engine.submit(self.held_click())
        self.wait_idle()
        self.assertIn(('mouse', 'left', False), self.native.events)
        self.assertEqual(self.native.position, (200, 300))
        self.assertEqual(self.errors, ['injected failure'])

    def test_held_coordinate_key_moves_only_once(self):
        self.engine.held = lambda key: True
        self.engine.submit(self.command)
        self.wait_idle()
        self.assertEqual(self.native.events, [('move', 30, 40)])


    def test_resize_during_script_click_releases_touch(self):
        with patch.object(self.native, 'size', return_value=(1920,1080)) as size:
            self.engine.submit(self.held_click(expected_size=(1920,1080)))
            self.assertTrue(self.native.down.wait(1))
            size.return_value = (1280,720)
            self.wait_idle()
        self.assertIn(('mouse', 'left', False), self.native.events)
        self.assertEqual(self.native.position, (200,300))
        self.assertEqual(self.native.events.count(('mouse', 'left', True)), 1)
        self.assertEqual(len(self.errors), 1)
        self.assertIn('창 크기', self.errors[0])
        self.assertIsNone(self.engine.pending)

    def test_stale_window_size_rejected_before_any_physical_input(self):
        self.engine.submit(replace(self.command, expected_size=(1280,720)))
        self.wait_idle()
        self.assertEqual(self.native.events, [])
        self.assertEqual(len(self.errors), 1)

    def test_minimize_during_script_click_releases_without_resize_error(self):
        from domain import WindowSizeUnavailable
        with patch.object(self.native, 'size', return_value=(1920,1080)) as size:
            self.engine.submit(self.held_click(expected_size=(1920,1080)))
            self.assertTrue(self.native.down.wait(1))
            size.side_effect = WindowSizeUnavailable()
            self.wait_idle()
        self.assertIn(('mouse', 'left', False), self.native.events)
        self.assertEqual(self.native.position, (200,300))
        self.assertEqual(self.errors, [])

    def test_unselected_command_is_rejected_without_input(self):
        for mode in ('move_only', 'script'):
            self.engine.submit(replace(self.command, hwnd=None, mode=mode, script='click 10,20'))
            self.wait_idle()
        self.assertEqual(self.errors, ['앱을 선택하세요.'] * 2)
        self.assertEqual(self.native.events, [])


    def test_move_on_negative_monitor(self):
        with patch.object(self.native, 'screen', return_value=(-1890, -160)):
            self.engine.execute(self.command)
        self.assertEqual(self.native.events, [('move', -1890, -160)])

    def test_move_does_not_need_click_hit_test(self):
        with patch.object(self.native, 'point_belongs_to', return_value=False) as hit:
            self.engine.execute(self.command)
        hit.assert_not_called()
        self.assertEqual(self.native.events, [('move',30,40)])

    def held_click(self, **kwargs):
        return replace(self.command, mode='script', script='click 30,40', click_hold_ms=5000, **kwargs)

    def test_shutdown_releases_latched_script_touch(self):
        self.engine.submit(replace(self.command, mode='script', script='mouse_switch left'))
        self.wait_idle()
        self.assertTrue(self.native.down.is_set())
        self.engine.shutdown()
        self.assertEqual(self.native.events[-1], ('mouse', 'left', False))
        self.assertEqual(self.native.position, (200,300))
