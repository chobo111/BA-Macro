import ctypes as C
from contextlib import ExitStack
import unittest
from unittest.mock import patch

import native


class NativeResizeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.native = native.Windows.__new__(native.Windows)
        self.events = []
        def adjust(rect, *args):
            rect._obj.left = -8
            rect._obj.top = -31
            rect._obj.right += 8
            rect._obj.bottom += 8
            return 1
        self.adjust = self.stack.enter_context(patch('native.adjust_dpi', side_effect=adjust))
        self.stack.enter_context(patch('native.window_dpi', return_value=144))
        self.stack.enter_context(patch('native.get_long', return_value=0))
        self.stack.enter_context(patch('native.get_menu', return_value=0))
        def send(hwnd, message, *args):
            self.events.append(message)
            # A zero message result means success for these WM messages.
            args[-1]._obj.value = 0
            return 1
        self.send = self.stack.enter_context(patch('native.send_message_timeout', side_effect=send))
        def resize(*args):
            self.events.append('resize')
            return 1
        self.position = self.stack.enter_context(patch('native.set_position', side_effect=resize))

    def test_boundaries_surround_resize_and_preserve_position_activation_and_dpi(self):
        self.native.resize(123,1920,1080)
        self.assertEqual(self.events,[native.WM_ENTERSIZEMOVE,'resize',native.WM_EXITSIZEMOVE])
        self.position.assert_called_once_with(123,None,0,0,1936,1119,0x0002|0x0004|0x0010)
        self.assertEqual(self.adjust.call_args.args[-1],144)
        for call in self.send.call_args_list:
            self.assertEqual(call.args[0],123)
            self.assertEqual(call.args[2:6],(0,0,0x0001|0x0002|0x0020,1000))

    def test_start_timeout_does_not_resize_and_attempts_finish(self):
        self.send.side_effect = [0,1]
        with self.assertRaisesRegex(OSError,'시작 알림 실패'):
            self.native.resize(123,1280,720)
        self.position.assert_not_called()
        self.assertEqual([c.args[1] for c in self.send.call_args_list],
                         [native.WM_ENTERSIZEMOVE,native.WM_EXITSIZEMOVE])

    def test_resize_failure_finishes_and_keeps_original_error(self):
        for finish_result in (0,1):
            self.send.reset_mock()
            self.send.side_effect = [1,finish_result]
            error = OSError('resize failed')
            self.position.side_effect = error
            with self.assertRaises(OSError) as caught:
                self.native.resize(123,1280,720)
            self.assertIs(caught.exception,error)
            self.assertEqual(self.send.call_args.args[1],native.WM_EXITSIZEMOVE)

    def test_finish_timeout_is_not_reported_as_success(self):
        self.send.side_effect = [1,0]
        with self.assertRaisesRegex(OSError,'완료 알림 실패'):
            self.native.resize(123,1280,720)
        self.position.assert_called_once()

    def test_win32_error_is_included_in_failure(self):
        def fail(*args):
            C.set_last_error(5)
            return 0
        self.send.side_effect=fail
        with self.assertRaisesRegex(OSError,'WinError 5'):
            native.notify_resize(123,native.WM_ENTERSIZEMOVE)

    def test_adjust_failure_does_not_begin_resize(self):
        self.adjust.side_effect=OSError('adjust failed')
        with self.assertRaisesRegex(OSError,'adjust failed'):
            self.native.resize(123,1280,720)
        self.send.assert_not_called()
        self.position.assert_not_called()

    def test_non_dpi_fallback_still_delivers_boundaries(self):
        with patch('native.adjust_dpi',None), patch('native.adjust',return_value=1) as adjust:
            self.native.resize(123,1280,720)
        adjust.assert_called_once()
        self.assertEqual(self.events,[native.WM_ENTERSIZEMOVE,'resize',native.WM_EXITSIZEMOVE])
        self.position.assert_called_once_with(123,None,0,0,1280,720,0x0002|0x0004|0x0010)
