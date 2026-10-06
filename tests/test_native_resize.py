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
        def frame(hwnd, rect):
            rect._obj.left, rect._obj.top = -100, 70
            rect._obj.right, rect._obj.bottom = 1196, 829
            return 1
        self.frame = self.stack.enter_context(patch('native.get_window_rect',side_effect=frame))
        def sizing(hwnd, message, edge, pointer, flags, timeout, result):
            self.events.append(message)
            rect = C.cast(pointer,C.POINTER(native.W.RECT)).contents
            self.proposed = (rect.left,rect.top,rect.right,rect.bottom)
            return 1
        self.sizing = self.stack.enter_context(patch('native.send_message_timeout',side_effect=sizing))
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
        self.assertEqual(self.events,[native.WM_SIZING,'resize',native.WM_EXITSIZEMOVE])
        self.position.assert_called_once_with(123,None,0,0,1936,1119,0x0002|0x0004|0x0010)
        self.assertEqual(self.adjust.call_args.args[-1],144)
        self.post.assert_called_once_with(123,native.WM_EXITSIZEMOVE,0,0)
        self.assertEqual(self.proposed,(-100,70,1836,1189))
        self.assertEqual(self.sizing.call_args.args[:3],(123,native.WM_SIZING,8))
        self.assertEqual(self.sizing.call_args.args[4:6],(0x23,300))

    def test_post_failure_does_not_invalidate_resize(self):
        self.post.side_effect=None
        self.post.return_value=0
        self.native.resize(123,1280,720)
        self.position.assert_called_once()
        self.post.assert_called_once()

    def test_resize_failure_is_preserved_and_finishes_sizing_notification(self):
        error = OSError('resize failed')
        self.position.side_effect=error
        with self.assertRaises(OSError) as caught:
            self.native.resize(123,1280,720)
        self.assertIs(caught.exception,error)
        self.post.assert_called_once()

    def test_false_win32_resize_result_is_still_an_error(self):
        self.position.side_effect=None
        self.position.return_value=0
        with self.assertRaises(OSError):
            self.native.resize(123,1280,720)
        self.post.assert_called_once()

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
        self.assertEqual(self.events,[native.WM_SIZING,'resize',native.WM_EXITSIZEMOVE])
        self.position.assert_called_once_with(123,None,0,0,1280,720,0x0002|0x0004|0x0010)

    def test_sizing_timeout_keeps_resize_and_returns_diagnostic(self):
        self.sizing.side_effect=None
        self.sizing.return_value=0
        notice=self.native.resize(123,1280,720)
        self.assertIn('크기 조절 알림',notice)
        self.position.assert_called_once()
        self.post.assert_called_once()

    def test_rect_query_failure_still_resizes_without_sending_pointer(self):
        self.frame.side_effect=None
        self.frame.return_value=0
        notice=self.native.resize(123,1280,720)
        self.assertIn('창 영역 확인',notice)
        self.sizing.assert_not_called()
        self.position.assert_called_once()
        self.post.assert_called_once()

    def test_game_rect_edits_do_not_silently_change_requested_size(self):
        def modify(hwnd,message,edge,pointer,*args):
            C.cast(pointer,C.POINTER(native.W.RECT)).contents.right += 100
            return 1
        self.sizing.side_effect=modify
        self.native.resize(123,1280,720)
        self.position.assert_called_once_with(123,None,0,0,1296,759,0x0002|0x0004|0x0010)

    def test_access_denied_is_reported_without_cancelling_resize(self):
        def denied(*args):
            C.set_last_error(5)
            return 0
        self.sizing.side_effect=denied
        self.post.side_effect=denied
        notice=self.native.resize(123,1280,720)
        self.assertIn('Windows 오류 5',notice)
        self.assertIn('크기 조절 알림',notice)
        self.assertIn('완료 알림',notice)
        self.position.assert_called_once()
