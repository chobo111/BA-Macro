from contextlib import ExitStack
import unittest
from unittest.mock import patch
import native
from interactive_resize import resize


class NativeResizeTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.native=native.Windows.__new__(native.Windows)
        self.events=[]
        def adjust(rect,*args):
            rect._obj.left=-8
            rect._obj.top=-31
            rect._obj.right+=8
            rect._obj.bottom+=8
            return 1
        self.adjust=self.stack.enter_context(patch('native.adjust_dpi',side_effect=adjust))
        self.stack.enter_context(patch('native.window_dpi',return_value=144))
        self.stack.enter_context(patch('native.get_long',return_value=0))
        self.stack.enter_context(patch('native.get_menu',return_value=0))
        self.position=self.stack.enter_context(patch('native.set_position',side_effect=lambda *a:self.events.append('resize') or 1))
        self.refresh=self.stack.enter_context(patch('interactive_resize.refresh',side_effect=lambda *args:self.events.append('refresh') or ''))

    def test_resize_precedes_interactive_refresh_with_dpi_and_position_preserved(self):
        self.assertEqual(resize(self.native,123,1920,1080),'')
        self.assertEqual(self.events,['resize','refresh'])
        self.position.assert_called_once_with(123,None,0,0,1936,1119,0x0002|0x0004|0x0010)
        self.assertEqual(self.adjust.call_args.args[-1],144)
        self.refresh.assert_called_once_with(self.native,123,(1920,1080))

    def test_refresh_notice_does_not_invalidate_base_resize(self):
        self.refresh.side_effect=None
        self.refresh.return_value='갱신 확인 필요'
        self.assertEqual(resize(self.native,123,1280,720),'갱신 확인 필요')
        self.position.assert_called_once()

    def test_resize_failure_does_not_start_interactive_operation(self):
        self.position.side_effect=None
        self.position.return_value=0
        with self.assertRaises(OSError):
            resize(self.native,123,1280,720)
        self.refresh.assert_not_called()

    def test_adjust_failure_does_not_resize(self):
        self.adjust.side_effect=OSError('adjust failed')
        with self.assertRaisesRegex(OSError,'adjust failed'):
            resize(self.native,123,1280,720)
        self.position.assert_not_called()
        self.refresh.assert_not_called()

    def test_non_dpi_fallback_keeps_refresh(self):
        with patch('native.adjust_dpi',None),patch('native.adjust',return_value=1) as adjust:
            resize(self.native,123,1280,720)
        adjust.assert_called_once()
        self.position.assert_called_once_with(123,None,0,0,1280,720,0x0002|0x0004|0x0010)
        self.refresh.assert_called_once_with(self.native,123,(1280,720))
