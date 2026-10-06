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
        def post(hwnd, message, *args):
            self.events.append(message)
            return 1
        self.post = self.stack.enter_context(patch('native.post_message', side_effect=post))
        def resize(*args):
            self.events.append('resize')
            return 1
        self.position = self.stack.enter_context(patch('native.set_position', side_effect=resize))

    def test_resize_precedes_advisory_finish_and_preserves_position_activation_and_dpi(self):
        self.native.resize(123,1920,1080)
        self.assertEqual(self.events,['resize',native.WM_EXITSIZEMOVE])
        self.position.assert_called_once_with(123,None,0,0,1936,1119,0x0002|0x0004|0x0010)
        self.assertEqual(self.adjust.call_args.args[-1],144)
        self.post.assert_called_once_with(123,native.WM_EXITSIZEMOVE,0,0)

    def test_post_failure_does_not_invalidate_resize(self):
        self.post.side_effect=None
        self.post.return_value=0
        self.native.resize(123,1280,720)
        self.position.assert_called_once()
        self.post.assert_called_once()

    def test_resize_failure_is_preserved_without_posting_success_notification(self):
        error = OSError('resize failed')
        self.position.side_effect=error
        with self.assertRaises(OSError) as caught:
            self.native.resize(123,1280,720)
        self.assertIs(caught.exception,error)
        self.post.assert_not_called()

    def test_false_win32_resize_result_is_still_an_error(self):
        self.position.side_effect=None
        self.position.return_value=0
        with self.assertRaises(OSError):
            self.native.resize(123,1280,720)
        self.post.assert_not_called()

    def test_adjust_failure_does_not_resize_or_notify(self):
        self.adjust.side_effect=OSError('adjust failed')
        with self.assertRaisesRegex(OSError,'adjust failed'):
            self.native.resize(123,1280,720)
        self.post.assert_not_called()
        self.position.assert_not_called()

    def test_non_dpi_fallback_keeps_advisory_finish(self):
        with patch('native.adjust_dpi',None), patch('native.adjust',return_value=1) as adjust:
            self.native.resize(123,1280,720)
        adjust.assert_called_once()
        self.assertEqual(self.events,['resize',native.WM_EXITSIZEMOVE])
        self.position.assert_called_once_with(123,None,0,0,1280,720,0x0002|0x0004|0x0010)
