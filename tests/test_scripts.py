from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import patch
from script_language import compile_script, ScriptError, ScriptRuntime
from engine import Engine, Command
from test_engine import FakeWindows


class ScriptWindows(FakeWindows):
    def move(self, x, y):
        self.events.append(('move', x, y))

    def create_touch_input(self):
        adapter = super().create_touch_input()
        adapter.move = self.move
        return adapter

    def mouse_button(self, button, down):
        self.events.append(('mouse', button, down))
        if down:
            self.down.set()


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.native = ScriptWindows()
        self.waits = []
        self.command = Command(1, 0, 0, 'script', 'q', 1)
        self.runtime = ScriptRuntime(self.native, lambda c: None, lambda ms, c: self.waits.append(ms), lambda k: False)

    def run_script(self, source):
        self.runtime.run(compile_script(source), self.command)

    def test_parser_errors_are_localized(self):
        for source in ('sleep -1', 'loop until_release\nclick mouse\nloop_end', 'release_actions\nloop\nclick mouse\nloop_end', 'loop 2\nclick mouse', 'loop_end', 'loop 1\nrelease_actions\nloop_end',
                       'joystick_press a', 'press 1,2 3,4', 'enter_staring', 'click 100000,0',
                       'text nope', 'key_press badkey', 'import os', 'loop 0\nclick mouse\nloop_end'):
            with self.subTest(source=source), self.assertRaises(ScriptError):
                compile_script(source)

    def test_oversized_integer_arguments_are_localized(self):
        digits = '9' * 5000
        for command, suffix, minimum, maximum in (
                ('sleep', '', 0, 600000),
                ('curve_first_time', '', 0, 600000),
                ('curve_last_time', '', 0, 600000),
                ('loop', '\nclick mouse\nloop_end', 1, 10000)):
            with self.subTest(command=command):
                with self.assertRaises(ScriptError) as raised:
                    compile_script(f'// comment\n{command} {digits}{suffix}')
                self.assertEqual(raised.exception.line, 2)
                self.assertEqual(str(raised.exception),
                                 f'2행: {minimum}~{maximum} 범위의 정수가 필요합니다.')

    def test_oversized_coordinates_are_localized(self):
        digits = '9' * 5000
        for command in ('click', 'move', 'press', 'release', 'switch', 'curve'):
            for point in (f'{digits},0', f'0,-{digits}'):
                with self.subTest(command=command, axis=point.startswith('0,')):
                    with self.assertRaises(ScriptError) as raised:
                        suffix = ' mouse' if command == 'curve' else ''
                        compile_script(f'sleep 1\n{command} {point}{suffix}')
                    self.assertEqual(raised.exception.line, 2)
                    self.assertEqual(str(raised.exception),
                                     '2행: 좌표는 부호를 제외하고 최대 5자리입니다.')

    def test_integer_argument_boundaries_and_decimal_notation(self):
        for command in ('sleep', 'curve_first_time', 'curve_last_time'):
            for token, expected in (('0', 0), ('600000', 600000),
                                    ('0' * 100 + '5', 5), ('００٥', 5)):
                with self.subTest(command=command, token=token):
                    program = compile_script(f'{command} {token}')
                    self.assertEqual(program.pressed[0].args, (expected,))
            for token in ('-1', '600001'):
                with self.subTest(command=command, token=token), self.assertRaises(ScriptError):
                    compile_script(f'{command} {token}')
        for token, expected in (('1', 1), ('10000', 10000), ('００٢', 2)):
            with self.subTest(loop=token):
                program = compile_script(f'loop {token}\nclick mouse\nloop_end')
                self.assertEqual(program.pressed[0].args, (expected,))
        for token in ('0', '10001'):
            with self.subTest(loop=token), self.assertRaises(ScriptError):
                compile_script(f'loop {token}\nclick mouse\nloop_end')

    def test_coordinate_boundaries_and_decimal_notation(self):
        for token, expected in (('-99999,99999', (-99999, 99999)),
                                ('0' * 100 + '5,-００٢', (5, -2))):
            with self.subTest(token=token):
                program = compile_script(f'click {token}')
                self.assertEqual(program.pressed[0].args, (expected,))
        for token in ('-100000,0', '0,100000'):
            with self.subTest(token=token), self.assertRaises(ScriptError):
                compile_script(f'click {token}')

    def test_click_mouse_snapshot_and_comments(self):
        self.run_script('click 20,30\nsleep 12\nclick mouse // 한글 주석')
        self.assertIn(('move', 200, 300), self.native.events)
        self.assertEqual(self.native.events.count(('mouse', 'left', True)), 2)
        self.assertIn(12, self.waits)

    def test_nested_loops(self):
        self.run_script('loop 2\nloop 3\nclick mouse\nloop_end\nloop_end')
        self.assertEqual(self.native.events.count(('mouse', 'left', True)), 6)

    def test_release_actions_and_cleanup(self):
        held = iter([True, True, False])
        self.runtime.held = lambda key: next(held)
        self.run_script('mouse_press left\nrelease_actions\nmouse_release left')
        self.assertEqual(self.native.events, [('mouse', 'left', True), ('mouse', 'left', False)])
        self.assertEqual(self.waits, [10, 10])
        self.run_script('mouse_press left')
        self.assertEqual(self.native.events[-1], ('mouse', 'left', False))

    def test_toggle_persists_and_releases(self):
        self.run_script('mouse_switch left')
        self.assertEqual(self.runtime.buttons, {'left'})
        self.run_script('mouse_switch left')
        self.assertFalse(self.runtime.buttons)
        self.run_script('switch 20,30\nrelease_all')
        self.assertFalse(self.runtime.buttons)

    def test_curve_holds_until_release_section(self):
        self.run_script('curve_first_time 20\ncurve_last_time untilcmd\ncurve 10,20 30,40\nrelease_actions\ncurve_release')
        self.assertIn(('move', 30, 40), self.native.events)
        self.assertEqual(self.native.events[-1], ('mouse', 'left', False))

    def test_cancel_long_sleep_releases_contact(self):
        errors = []
        engine = Engine(self.native, lambda key: True, errors.append)
        try:
            engine.submit(replace(self.command, script='mouse_press left\nsleep 600000'))
            self.assertTrue(self.native.down.wait(1))
            engine.stop()
            deadline = time.monotonic() + 1
            while engine.busy and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertFalse(engine.busy)
            self.assertIn(('mouse', 'left', False), self.native.events)
            self.assertFalse(errors)
        finally:
            engine.shutdown()

    def test_until_release(self):
        ticks = iter([True, True, False])
        self.runtime.held = lambda key: next(ticks)
        self.run_script('loop\nclick mouse\nloop_end')
        self.assertEqual(self.native.events.count(('mouse', 'left', True)), 2)

    def test_runtime_failure_releases_contact(self):
        with self.assertRaises(ScriptError):
            self.run_script('mouse_press left\nclick -90000,0')
        self.assertEqual(self.native.events[-1], ('mouse', 'left', False))

    def test_curve_configurable_duration_per_segment(self):
        for duration in (0, 1, 10, 20, 100, 10000):
            with self.subTest(duration=duration):
                self.waits.clear()
                self.native.events.clear()
                self.command = replace(self.command, drag_segment_duration_ms=duration)
                self.run_script('curve_first_time 5\ncurve_last_time 7\ncurve 10,20 30,40 50,60')
                self.assertAlmostEqual(sum(self.waits), duration * 2 + 12)
                self.assertIn(('move', 30, 40), self.native.events)
                self.assertIn(('move', 50, 60), self.native.events)
                self.assertEqual(self.native.events[-1], ('mouse', 'left', False))

    def test_new_script_interrupts_long_wait_and_releases_before_next_input(self):
        errors = []
        completed = threading.Event()
        native = self.native
        original_move = native.move
        def move(x, y):
            original_move(x, y)
            completed.set()
        native.move = move
        engine = Engine(native, lambda key: True, errors.append)
        try:
            first = replace(self.command, script='mouse_press left\nloop\nsleep 600000\nloop_end\nrelease_actions\nmove 999,999')
            self.assertTrue(engine.submit(first))
            self.assertTrue(native.down.wait(1))
            start = time.monotonic()
            self.assertTrue(engine.submit(replace(self.command, key='w', script='move 30,40')))
            self.assertTrue(completed.wait(.5))
            self.assertLess(time.monotonic() - start, .5)
            events = native.events
            self.assertLess(events.index(('mouse', 'left', False)), events.index(('move', 30, 40)))
            self.assertNotIn(('move', 999, 999), events)
            self.assertFalse(errors)
        finally:
            engine.shutdown()

    def test_latest_pending_script_replaces_earlier_request(self):
        entered, unblock, completed = threading.Event(), threading.Event(), threading.Event()
        native = self.native
        original_move = native.move
        def move(x, y):
            original_move(x, y)
            if x == 10:
                entered.set()
                unblock.wait(2)
            if x == 50:
                completed.set()
        native.move = move
        errors = []
        engine = Engine(native, lambda key: True, errors.append)
        try:
            engine.submit(replace(self.command, script='move 10,20\nsleep 600000'))
            self.assertTrue(entered.wait(1))
            engine.submit(replace(self.command, key='w', script='move 30,40'))
            engine.submit(replace(self.command, key='e', script='move 50,60'))
            unblock.set()
            self.assertTrue(completed.wait(1))
            self.assertEqual([e[1] for e in native.events if e[0] == 'move'], [10, 50])
            self.assertFalse(errors)
        finally:
            unblock.set()
            engine.shutdown()

    def test_stop_clears_pending_switch(self):
        entered, unblock = threading.Event(), threading.Event()
        native = self.native
        def move(x, y):
            native.events.append(('move', x, y))
            entered.set()
            unblock.wait(2)
        native.move = move
        engine = Engine(native, lambda key: True)
        try:
            engine.submit(replace(self.command, script='move 10,20\nsleep 600000'))
            self.assertTrue(entered.wait(1))
            engine.submit(replace(self.command, key='w', script='move 30,40'))
            engine.stop()
            unblock.set()
            engine.shutdown()
            self.assertNotIn(('move', 30, 40), native.events)
            self.assertFalse(engine.worker.is_alive())
        finally:
            unblock.set()
            engine.shutdown()

    def test_compilation_cache_reuses_only_unchanged_valid_source(self):
        from dataclasses import FrozenInstanceError
        from script_language import _compile_cached
        _compile_cached.cache_clear()
        source = 'loop 2\nclick mouse\nloop_end'
        first = compile_script(source)
        with patch('script_language.strip_comment', side_effect=AssertionError('parsed again')):
            self.assertIs(compile_script(source), first)
        with self.assertRaises(FrozenInstanceError):
            first.pressed[0].name = 'sleep'
        changed = compile_script('loop 3\nclick mouse\nloop_end')
        self.assertNotEqual(first, changed)
        for source in ([], {}, None, 'x' * 100001, 'sleep invalid'):
            with self.subTest(source_type=type(source).__name__), self.assertRaises(ScriptError):
                compile_script(source)
        for index in range(40):
            compile_script(f'sleep {index}')
        self.assertEqual(_compile_cached.cache_info().currsize, 16)

    def test_cached_program_refreshes_mouse_position_each_run(self):
        program = compile_script('move mouse')
        for point in ((100, 200), (300, 400)):
            self.native.position = point
            self.runtime.run(program, self.command)
            self.assertEqual(self.native.events[-1], ('move', *point))

    def test_idle_worker_sleeps_but_held_input_still_checks_focus(self):
        idle, watching, released = threading.Event(), threading.Event(), threading.Event()
        errors = []
        engine = Engine(self.native, lambda key: False, errors.append)
        original_wait = engine.condition.wait_for
        original_button = self.native.mouse_button
        def wait(predicate, timeout=None):
            (idle if timeout is None else watching).set()
            return original_wait(predicate, timeout)
        def button(name, down):
            original_button(name, down)
            if not down:
                released.set()
        try:
            with patch.object(engine.condition, 'wait_for', side_effect=wait), \
                 patch.object(self.native, 'mouse_button', side_effect=button):
                engine.stop()
                self.assertTrue(idle.wait(1))
                idle.clear()
                engine.submit(replace(self.command, script='mouse_switch left'))
                self.assertTrue(watching.wait(1))
                self.native.active_value = False
                self.assertTrue(released.wait(1))
                self.assertTrue(idle.wait(1))
            self.assertFalse(errors)
        finally:
            engine.shutdown()
        self.assertFalse(engine.worker.is_alive())

    def test_unsupported_commands_fail_in_both_sections_and_nested_loops(self):
        commands = ('key_press a', 'key_release a', 'key_switch a', 'text "hello"',
                    'mouse_press right', 'mouse_release middle', 'mouse_switch back')
        for command in commands:
            for prefix, suffix, line in (('click mouse\n', '', 2),
                                         ('loop 2\n', '\nloop_end', 2),
                                         ('release_actions\n', '', 2)):
                with self.subTest(command=command, prefix=prefix), self.assertRaises(ScriptError) as raised:
                    compile_script(prefix + command + suffix)
                self.assertEqual(raised.exception.line, line)
