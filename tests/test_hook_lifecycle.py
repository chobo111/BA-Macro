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
