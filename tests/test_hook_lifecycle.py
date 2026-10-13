"""Input hooks must release OS resources on both success and failure."""
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from keyboard_hook import PhysicalHook, U, K
from mouse_hook import CoordinateHook
from touch_input import TouchPromotionFilter


class HookLifecycleTests(unittest.TestCase):
    def hooks(self):
        return ((PhysicalHook(lambda event: True), 13),
                (CoordinateHook(lambda x, y: None), 14),
                (TouchPromotionFilter(), 14))

    def fake_os(self):
        stack = ExitStack()
        api = {}
        for name in ('SetWindowsHookExW', 'GetMessageW', 'PeekMessageW',
                     'CallNextHookEx', 'UnhookWindowsHookEx'):
            api[name] = stack.enter_context(patch.object(U, name))
        stack.enter_context(patch.object(K, 'GetCurrentThreadId', return_value=42))
        stack.enter_context(patch.object(K, 'GetModuleHandleW', return_value=1))
        return stack, api

    def test_keyboard_capture_and_touch_filter_register_forward_and_release(self):
        for hook, kind in self.hooks():
            with self.subTest(hook=type(hook).__name__):
                stack, api = self.fake_os()
                with stack:
                    callback = None
                    def install(actual_kind, proc, module, thread):
                        nonlocal callback
                        self.assertEqual(actual_kind, kind)
                        callback = proc
                        return 123
                    def pump(*args):
                        # Negative hook codes must always reach the next hook.
                        self.assertEqual(callback(-1, 0, 0), 7)
                        return 0
                    api['SetWindowsHookExW'].side_effect = install
                    api['CallNextHookEx'].return_value = 7
                    api['GetMessageW'].side_effect = pump
                    hook.run()
                    self.assertTrue(hook.ready.is_set())
                    self.assertIsNone(hook.error)
                    api['UnhookWindowsHookEx'].assert_called_once_with(123)

    def test_registration_failure_is_reported_without_releasing_an_invalid_handle(self):
        for hook, kind in self.hooks():
            with self.subTest(hook=type(hook).__name__):
                stack, api = self.fake_os()
                with stack, patch('keyboard_hook.C.get_last_error', return_value=5):
                    api['SetWindowsHookExW'].return_value = 0
                    hook.thread = Mock()
                    hook.thread.start.side_effect = hook.run
                    with self.assertRaises(OSError) as raised:
                        hook.start()
                    self.assertIs(hook.error, raised.exception)
                    self.assertTrue(hook.ready.is_set())
                    api['GetMessageW'].assert_not_called()
                    api['UnhookWindowsHookEx'].assert_not_called()

    def test_message_loop_failure_releases_registered_handle_and_reports_error(self):
        for hook, kind in self.hooks():
            with self.subTest(hook=type(hook).__name__):
                stack, api = self.fake_os()
                with stack:
                    failure = OSError('message queue unavailable')
                    api['SetWindowsHookExW'].return_value = 123
                    api['GetMessageW'].side_effect = failure
                    hook.run()
                    self.assertTrue(hook.ready.is_set())
                    self.assertIs(hook.error, failure)
                    api['UnhookWindowsHookEx'].assert_called_once_with(123)

    def test_negative_message_result_reports_os_error_and_releases_handle(self):
        for hook, kind in self.hooks():
            with self.subTest(hook=type(hook).__name__):
                stack, api = self.fake_os()
                with stack, patch('keyboard_hook.C.get_last_error', return_value=5):
                    api['SetWindowsHookExW'].return_value = 123
                    api['GetMessageW'].side_effect = [1, -1]
                    hook.run()
                    self.assertIsInstance(hook.error, OSError)
                    self.assertEqual(hook.error.winerror, 5)
                    self.assertTrue(hook.ready.is_set())
                    api['UnhookWindowsHookEx'].assert_called_once_with(123)

    def test_timeout_cancels_late_registration_and_passes_input_through(self):
        from threading import Event
        for hook, kind in self.hooks():
            with self.subTest(hook=type(hook).__name__):
                stack, api = self.fake_os()
                registering, resume = Event(), Event()
                callbacks = []
                join = hook.thread.join
                def install(actual_kind, callback, module, thread):
                    callbacks.append(callback)
                    registering.set()
                    if not resume.wait(2):
                        raise RuntimeError('test registration did not resume')
                    return 123
                def timeout(seconds):
                    self.assertTrue(registering.wait(1))
                    return False
                with stack, patch.object(U, 'PostThreadMessageW', return_value=True), \
                        patch.object(hook.ready, 'wait', side_effect=timeout), \
                        patch.object(hook.thread, 'join', side_effect=lambda timeout=None: join(0)):
                    api['SetWindowsHookExW'].side_effect = install
                    api['CallNextHookEx'].return_value = 7
                    try:
                        with self.assertRaisesRegex(RuntimeError, '시간이 초과'):
                            hook.start()
                        self.assertTrue(hook.stopping.is_set())
                        # No event data is dereferenced after cancellation.
                        self.assertEqual(callbacks[0](0, 0x100, 0), 7)
                    finally:
                        resume.set()
                        join(1)
                    self.assertFalse(hook.thread.is_alive())
                    api['GetMessageW'].assert_not_called()
                    api['UnhookWindowsHookEx'].assert_called_once_with(123)

    def test_timeout_before_message_queue_prevents_registration(self):
        from threading import Event
        hook = PhysicalHook(lambda event: False)
        stack, api = self.fake_os()
        entering, resume = Event(), Event()
        join = hook.thread.join
        def thread_id():
            entering.set()
            if not resume.wait(2):
                raise RuntimeError('test queue creation did not resume')
            return 42
        def timeout(seconds):
            self.assertTrue(entering.wait(1))
            return False
        with stack, patch.object(K, 'GetCurrentThreadId', side_effect=thread_id), \
                patch.object(U, 'PostThreadMessageW') as post, \
                patch.object(hook.ready, 'wait', side_effect=timeout), \
                patch.object(hook.thread, 'join', side_effect=lambda timeout=None: join(0)):
            try:
                with self.assertRaisesRegex(RuntimeError, '시간이 초과'):
                    hook.start()
                post.assert_not_called()
            finally:
                resume.set()
                join(1)
            self.assertFalse(hook.thread.is_alive())
            api['SetWindowsHookExW'].assert_not_called()
            api['GetMessageW'].assert_not_called()

    def test_runtime_failure_notifies_after_release_and_can_close_on_hook_thread(self):
        from threading import Event
        for hook, kind in self.hooks():
            with self.subTest(hook=type(hook).__name__):
                stack, api = self.fake_os()
                resume, reported = Event(), Event()
                errors = []
                def pump(*args):
                    if not resume.wait(2):
                        raise RuntimeError('test pump did not resume')
                    return -1
                def report(error):
                    api['UnhookWindowsHookEx'].assert_called_once_with(123)
                    hook.close()
                    errors.append(error)
                    reported.set()
                hook.on_error = report
                with stack, patch('keyboard_hook.C.get_last_error', return_value=5):
                    api['SetWindowsHookExW'].return_value = 123
                    api['GetMessageW'].side_effect = pump
                    try:
                        hook.start()
                        resume.set()
                        self.assertTrue(reported.wait(1))
                    finally:
                        resume.set()
                        hook.close()
                    self.assertFalse(hook.thread.is_alive())
                    self.assertEqual(errors, [hook.error])
                    self.assertEqual(hook.error.winerror, 5)

    def test_callback_failure_passes_input_releases_hook_and_reports_once(self):
        import ctypes as C
        from keyboard_hook import KeyEvent
        from mouse_hook import MouseEvent, W
        failure = RuntimeError('input handler failed')
        broken = Mock(side_effect=failure)
        cases = [
            (PhysicalHook(broken), KeyEvent(vk=0x51, scan=16), 0x100),
            (CoordinateHook(broken), MouseEvent(W.POINT(10, 20), 0, 0, 0, 0), 0x204),
            (CoordinateHook(Mock(), can_capture=broken), MouseEvent(), 0x204),
        ]
        touch = TouchPromotionFilter()
        touch.matches = broken
        cases.append((touch, MouseEvent(), 0x200))
        for hook, event, message in cases:
            with self.subTest(hook=type(hook).__name__, gate=bool(getattr(hook, 'can_capture', None) is broken)):
                broken.reset_mock()
                stack, api = self.fake_os()
                callbacks, results, errors = [], [], []
                def installed(kind, callback, module, thread):
                    callbacks.append(callback)
                    return 123
                def pump(*args):
                    results.append(callbacks[0](0, message, C.addressof(event)))
                    # Already queued callbacks must pass through after failure.
                    results.append(callbacks[0](0, message, C.addressof(event)))
                    return 1
                def report(error):
                    api['UnhookWindowsHookEx'].assert_called_once_with(123)
                    errors.append(error)
                hook.on_error = report
                with stack, patch.object(U, 'PostThreadMessageW', return_value=True) as post, \
                        patch.object(U, 'WindowFromPoint', return_value=1), \
                        patch.object(U, 'GetWindowThreadProcessId', return_value=1):
                    api['SetWindowsHookExW'].side_effect = installed
                    # End the baseline loop too, even before the fix exists.
                    api['GetMessageW'].side_effect = lambda *args: pump() if not results else 0
                    api['CallNextHookEx'].return_value = 7
                    hook.run()
                    self.assertEqual(results, [7, 7])
                    self.assertIs(hook.error, failure)
                    self.assertEqual(errors, [failure])
                    broken.assert_called_once()
                    post.assert_called_once_with(42, 0x12, 0, 0)
                    api['GetMessageW'].assert_called_once()

    def test_closed_hook_cannot_start_or_register_again(self):
        for hook, kind in self.hooks():
            with self.subTest(hook=type(hook).__name__):
                stack, api = self.fake_os()
                with stack:
                    hook.close()
                    hook.close()
                    with self.assertRaisesRegex(RuntimeError, '종료된'):
                        hook.start()
                    hook.run()
                    api['SetWindowsHookExW'].assert_not_called()
                    api['GetMessageW'].assert_not_called()
