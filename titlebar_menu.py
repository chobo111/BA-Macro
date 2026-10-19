"""Windows caption menu button, preserving the native frame and taskbar icon."""
import ctypes as C
from ctypes import wintypes as W
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from widgets import make_menu_icon

user32 = C.WinDLL('user32', use_last_error=True)
send_message = user32.SendMessageW
send_message.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
send_message.restype = C.c_ssize_t
destroy_icon = user32.DestroyIcon
destroy_icon.argtypes = [W.HICON]
destroy_icon.restype = W.BOOL

set_dwm_attribute = C.WinDLL('dwmapi').DwmSetWindowAttribute
set_dwm_attribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
set_dwm_attribute.restype = C.c_long
DWMWA_TEXT_COLOR = 36


class TRACKMOUSEEVENT(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('dwFlags', W.DWORD), ('hwndTrack', W.HWND), ('dwHoverTime', W.DWORD)]


track_mouse_event = user32.TrackMouseEvent
track_mouse_event.argtypes = [C.POINTER(TRACKMOUSEEVENT)]
track_mouse_event.restype = W.BOOL


class CaptionMenu:
    def __init__(self, window, menu):
        self.window, self.menu = window, menu
        self.icon = None
        self.pending = False
        self.hovered = False
        self.menu_open = False
        self.menu.aboutToShow.connect(self.menu_shown)
        self.menu.aboutToHide.connect(self.menu_hidden)
        self.refresh()

    def apply_text_color(self):
        if QApplication.platformName() != 'windows':
            return False
        # An explicit DWM text color also applies to an inactive caption. Keep
        # native activation/focus behavior; only the coordinate text stays vivid.
        dark = QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        color = W.DWORD(0xF0F0F0 if dark else 0x202020)
        # Older Windows versions can reject this optional Windows 11 attribute.
        return set_dwm_attribute(int(self.window.winId()), DWMWA_TEXT_COLOR,
                                 C.byref(color), C.sizeof(color)) >= 0

    def install_icon(self):
        if QApplication.platformName() != 'windows':
            return
        dark = QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        rgb = QColor('#f0f0f0' if dark else '#202020')
        size = max(16, round(16 * self.window.devicePixelRatioF()))
        background = ('#40576b' if dark else '#d5e5f3') if self.highlighted else None
        icon = make_menu_icon(rgb, background).pixmap(size, size).toImage().toHICON()
        if not icon:
            return
        send_message(int(self.window.winId()), 0x0080, 0, icon)  # WM_SETICON / ICON_SMALL
        previous, self.icon = self.icon, icon
        if previous:
            destroy_icon(previous)

    @property
    def highlighted(self):
        return self.hovered or self.menu_open

    def set_hovered(self, hovered):
        previous = self.highlighted
        self.hovered = hovered
        if previous != self.highlighted:
            self.refresh()

    def menu_shown(self):
        previous = self.highlighted
        self.menu_open = True
        if not previous:
            self.refresh()

    def menu_hidden(self):
        previous = self.highlighted
        self.menu_open = self.hovered = False
        if previous:
            self.refresh()

    def handle(self, message):
        if self.window.closing:
            return False
        if message.message == 0x00A0:  # WM_NCMOUSEMOVE / HTSYSMENU.
            hovered = message.wParam == 3
            if hovered and not self.hovered:
                tracking = TRACKMOUSEEVENT(C.sizeof(TRACKMOUSEEVENT), 0x12, int(self.window.winId()), 0)
                track_mouse_event(C.byref(tracking))  # TME_LEAVE | TME_NONCLIENT.
            self.set_hovered(hovered)
        elif (message.message in (0x02A2, 0x0200, 0x001F)
              or message.message == 0x0086 and not message.wParam):
            # Nonclient leave, movement into the client area, cancel or deactivation.
            self.set_hovered(False)
        # Capture only the caption icon and Alt+Space. Other native frame behavior stays native.
        if (message.message in (0x00A1, 0x00A3) and message.wParam == 3
                or message.message == 0x0112 and (message.wParam & 0xFFF0) == 0xF100 and message.lParam == 32):
            if not self.pending:
                self.pending = True
                QTimer.singleShot(0, self.show)
            return True
        if message.message in (0x001A, 0x02E0, 0x031A):  # Settings / DPI / theme changes.
            QTimer.singleShot(0, self.refresh)
        return False

    def refresh(self):
        if not self.window.closing:
            self.install_icon()
            self.apply_text_color()

    def show(self):
        self.pending = False
        if not self.window.closing:
            self.menu.popup(self.window.mapToGlobal(QPoint(0, 0)))

    def close(self):
        if self.icon:
            send_message(int(self.window.winId()), 0x0080, 0, 0)
            destroy_icon(self.icon)
            self.icon = None
