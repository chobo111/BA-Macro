import unittest
from ctypes import wintypes
from unittest.mock import Mock, patch
from titlebar_menu import CaptionMenu


class CaptionMenuTests(unittest.TestCase):
    def setUp(self):
        self.window, self.menu = Mock(), Mock()
        self.window.closing = False
        self.window.winId.return_value = 123
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

    def test_hover_tracks_leave_and_does_not_redraw_for_every_mouse_move(self):
        with patch('titlebar_menu.track_mouse_event', return_value=True) as track, patch.object(self.caption, 'install_icon') as redraw:
            message = wintypes.MSG(message=0xA0, wParam=3)
            self.assertFalse(self.caption.handle(message))
            self.assertFalse(self.caption.handle(message))
            self.assertTrue(self.caption.highlighted)
            track.assert_called_once()
            tracking = track.call_args.args[0]._obj
            self.assertEqual((tracking.dwFlags, tracking.hwndTrack), (0x12,123))
            redraw.assert_called_once()
            self.caption.handle(wintypes.MSG(message=0xA0, wParam=2))
            self.assertFalse(self.caption.highlighted)
            self.assertEqual(redraw.call_count,2)

    def test_hover_clears_on_leave_client_motion_and_deactivation(self):
        for message, wp in ((0x2A2,0),(0x200,0),(0x1F,0),(0x86,0)):
            with self.subTest(message=message), patch.object(self.caption,'install_icon'):
                self.caption.set_hovered(True)
                self.assertFalse(self.caption.handle(wintypes.MSG(message=message,wParam=wp)))
                self.assertFalse(self.caption.highlighted)

    def test_open_menu_stays_highlighted_until_closed(self):
        with patch.object(self.caption,'install_icon') as redraw:
            self.caption.set_hovered(True)
            self.caption.menu_shown()
            self.caption.handle(wintypes.MSG(message=0x2A2))
            self.assertTrue(self.caption.highlighted)
            self.assertEqual(redraw.call_count,1)
            self.caption.menu_hidden()
            self.assertFalse(self.caption.highlighted)
            self.assertEqual(redraw.call_count,2)

    def test_keyboard_open_also_highlights_menu_button(self):
        with patch.object(self.caption,'install_icon') as redraw:
            self.caption.menu_shown()
            self.assertTrue(self.caption.highlighted)
            self.caption.menu_hidden()
            self.assertFalse(self.caption.highlighted)
            self.assertEqual(redraw.call_count,2)

    def test_hover_messages_after_shutdown_do_not_touch_native_resources(self):
        self.window.closing = True
        with patch('titlebar_menu.track_mouse_event') as track, patch.object(self.caption,'install_icon') as redraw:
            self.assertFalse(self.caption.handle(wintypes.MSG(message=0xA0,wParam=3)))
            track.assert_not_called()
            redraw.assert_not_called()
