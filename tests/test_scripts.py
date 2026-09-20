from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import patch
from script_language import compile_script, ScriptError, ScriptRuntime
from engine import Engine, Command
from test_engine import FakeWindows


class ScriptWindows(FakeWindows):
    def key(self, key, down):
        self.events.append(('key', key, down))
        if down:
            self.down.set()

    def mouse_button(self, button, down):
        self.events.append(('mouse', button, down))
        if down:
            self.down.set()

    def text_char(self, char):
        self.events.append(('text', char))


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.native = ScriptWindows()
        self.waits = []
        self.command = Command(1, 0, 0, 'script', 'q', 0, 1, 0, 0, False)
        self.runtime = ScriptRuntime(self.native, lambda c: None, lambda ms, c: self.waits.append(ms), lambda k: False)

    def run_script(self, source):
        self.runtime.run(compile_script(source), self.command)

    def test_parser_errors_are_localized(self):
        for source in ('sleep -1', 'loop until_release\nclick mouse\nloop_end', 'release_actions\nloop\nclick mouse\nloop_end', 'loop 2\nclick mouse', 'loop_end', 'loop 1\nrelease_actions\nloop_end',
                       'joystick_press a', 'press 1,2 3,4', 'enter_staring', 'click 100000,0',
                       'text nope', 'key_press badkey', 'import os', 'loop 0\nclick mouse\nloop_end'):
            with self.subTest(source=source), self.assertRaises(ScriptError):
                compile_script(source)

    def test_click_mouse_snapshot_and_text(self):
        self.run_script('click 20,30\nsleep 12\nclick mouse\ntext "한글 // text"')
        self.assertIn(('move', 200, 300), self.native.events)
        self.assertEqual(''.join(e[1] for e in self.native.events if e[0] == 'text'), '한글 // text')
        self.assertIn(12, self.waits)

    def test_nested_loops(self):
        self.run_script('loop 2\nloop 3\nclick mouse\nloop_end\nloop_end')
        self.assertEqual(self.native.events.count(('mouse', 'left', True)), 6)

    def test_release_actions_and_cleanup(self):
        held = iter([True, True, False])
        self.runtime.held = lambda key: next(held)
        self.run_script('key_press a\nrelease_actions\nkey_release a')
        self.assertEqual(self.native.events, [('key', 'a', True), ('key', 'a', False)])
        self.assertEqual(self.waits, [10, 10])
        self.run_script('mouse_press right')
        self.assertEqual(self.native.events[-1], ('mouse', 'right', False))

    def test_toggle_persists_and_releases(self):
        self.run_script('key_switch a\nmouse_switch right')
        self.assertEqual(self.runtime.keys, {'a'})
        self.assertEqual(self.runtime.buttons, {'right'})
        self.run_script('key_switch a\nmouse_switch right')
        self.assertFalse(self.runtime.keys or self.runtime.buttons)
        self.run_script('switch 20,30\nrelease_all')
        self.assertFalse(self.runtime.keys or self.runtime.buttons)

    def test_curve_holds_until_release_section(self):
        self.run_script('curve_first_time 20\ncurve_last_time untilcmd\ncurve 10,20 30,40\nrelease_actions\ncurve_release')
        self.assertIn(('move', 30, 40), self.native.events)
        self.assertEqual(self.native.events[-1], ('mouse', 'left', False))

    def test_cancel_long_sleep_releases_keyboard(self):
        errors = []
        engine = Engine(self.native, lambda key: True, errors.append)
        try:
            engine.submit(replace(self.command, script='key_press a\nsleep 600000'))
            self.assertTrue(self.native.down.wait(1))
            engine.stop()
            deadline = time.monotonic() + 1
            while engine.busy and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertFalse(engine.busy)
            self.assertIn(('key', 'a', False), self.native.events)
            self.assertFalse(errors)
        finally:
            engine.shutdown()

    def test_until_release(self):
        ticks = iter([True, True, False])
        self.runtime.held = lambda key: next(ticks)
        self.run_script('loop\nclick mouse\nloop_end')
        self.assertEqual(self.native.events.count(('mouse', 'left', True)), 2)

    def test_runtime_failure_releases_pressed_keys(self):
        with self.assertRaises(ScriptError):
            self.run_script('key_press a\nclick -90000,0')
        self.assertEqual(self.native.events[-1], ('key', 'a', False))

    def test_curve_configurable_duration_per_segment(self):
        for duration in (0, 1, 10, 20, 100, 10000):
            with self.subTest(duration=duration):
                self.waits.clear()
                self.native.events.clear()
                self.command = replace(self.command, curve_duration=duration)
                self.run_script('curve_first_time 5\ncurve_last_time 7\ncurve 10,20 30,40 50,60')
                self.assertAlmostEqual(sum(self.waits), duration * 2 + 12)
                self.assertIn(('move', 30, 40), self.native.events)
                self.assertIn(('move', 50, 60), self.native.events)
                self.assertEqual(self.native.events[-1], ('mouse', 'left', False))

    def test_new_script_interrupts_long_wait_and_releases_before_next_input(self):
        errors = []
        completed = threading.Event()
        native = self.native
        original_text = native.text_char
        def text(char):
            original_text(char)
            completed.set()
        native.text_char = text
        engine = Engine(native, lambda key: True, errors.append)
        try:
            first = replace(self.command, script='key_press a\nmouse_press left\nloop\nsleep 600000\nloop_end\nrelease_actions\ntext "old"')
            self.assertTrue(engine.submit(first))
            self.assertTrue(native.down.wait(1))
            start = time.monotonic()
            self.assertTrue(engine.submit(replace(self.command, key='w', script='text "new"')))
            self.assertTrue(completed.wait(.5))
            self.assertLess(time.monotonic() - start, .5)
            events = native.events
            self.assertLess(events.index(('key', 'a', False)), events.index(('text', 'n')))
            if ('mouse', 'left', True) in events:
                self.assertLess(events.index(('mouse', 'left', False)), events.index(('text', 'n')))
            self.assertNotIn(('text', 'o'), events)
            self.assertFalse(errors)
        finally:
            engine.shutdown()

    def test_latest_pending_script_replaces_earlier_request(self):
        entered, unblock, completed = threading.Event(), threading.Event(), threading.Event()
        native = self.native
        original_text = native.text_char
        def text(char):
            original_text(char)
            if char == 'a':
                entered.set()
                unblock.wait(2)
            if char == 'c':
                completed.set()
        native.text_char = text
        errors = []
        engine = Engine(native, lambda key: True, errors.append)
        try:
            engine.submit(replace(self.command, script='text "a"\nsleep 600000'))
            self.assertTrue(entered.wait(1))
            engine.submit(replace(self.command, key='w', script='text "b"'))
            engine.submit(replace(self.command, key='e', script='text "c"'))
            unblock.set()
            self.assertTrue(completed.wait(1))
            self.assertEqual([e[1] for e in native.events if e[0] == 'text'], ['a', 'c'])
            self.assertFalse(errors)
        finally:
            unblock.set()
            engine.shutdown()

    def test_stop_clears_pending_switch(self):
        entered, unblock = threading.Event(), threading.Event()
        native = self.native
        def text(char):
            native.events.append(('text', char))
            entered.set()
            unblock.wait(2)
        native.text_char = text
        engine = Engine(native, lambda key: True)
        try:
            engine.submit(replace(self.command, script='text "a"\nsleep 600000'))
            self.assertTrue(entered.wait(1))
            engine.submit(replace(self.command, key='w', script='text "b"'))
            engine.stop()
            unblock.set()
            engine.shutdown()
            self.assertNotIn(('text', 'b'), native.events)
            self.assertFalse(engine.worker.is_alive())
        finally:
            unblock.set()
            engine.shutdown()
