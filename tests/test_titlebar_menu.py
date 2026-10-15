import unittest
from ctypes import wintypes
from unittest.mock import Mock, patch
from titlebar_menu import CaptionMenu


class CaptionMenuTests(unittest.TestCase):
    def setUp(self):
        self.window, self.menu = Mock(), Mock()
        self.window.closing = False
        with patch.object(CaptionMenu, 'install_icon'):
            self.caption = CaptionMenu(self.window, self.menu)

    def test_icon_and_alt_space_open_once_and_double_click_does_not_close(self):
        with patch('titlebar_menu.QTimer.singleShot') as schedule:
            for msg, wp, lp in ((0xA1, 3, 0), (0xA3, 3, 0), (0x112, 0xF100, 32)):
                self.assertTrue(self.caption.handle(wintypes.MSG(message=msg, wParam=wp, lParam=lp)))
            schedule.assert_called_once()
            self.caption.show()
        self.menu.popup.assert_called_once()
        self.window.close.assert_not_called()

    def test_other_caption_and_system_commands_remain_native(self):
        with patch('titlebar_menu.QTimer.singleShot') as schedule:
            for msg, wp in ((0xA1, 2), (0xA3, 2), (0x112, 0xF020), (0x112, 0xF030), (0x112, 0xF060)):
                self.assertFalse(self.caption.handle(wintypes.MSG(message=msg, wParam=wp)))
            schedule.assert_not_called()

    def test_pending_popup_and_refresh_do_nothing_after_shutdown(self):
        self.window.closing = True
        with patch.object(self.caption, 'install_icon') as refresh:
            self.caption.show()
            self.caption.refresh()
            refresh.assert_not_called()
        self.menu.popup.assert_not_called()
