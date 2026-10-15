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


class CaptionMenu:
    def __init__(self, window, menu):
        self.window, self.menu = window, menu
        self.icon = None
        self.pending = False
        self.install_icon()

    def install_icon(self):
        if QApplication.platformName() != 'windows':
            return
        dark = QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        rgb = QColor('#f0f0f0' if dark else '#202020')
        size = max(16, round(16 * self.window.devicePixelRatioF()))
        icon = make_menu_icon(rgb).pixmap(size, size).toImage().toHICON()
        if not icon:
            return
        send_message(int(self.window.winId()), 0x0080, 0, icon)  # WM_SETICON / ICON_SMALL
        previous, self.icon = self.icon, icon
        if previous:
            destroy_icon(previous)

    def handle(self, message):
        # Capture only the caption icon and Alt+Space. Other native frame behavior stays native.
        if (message.message in (0x00A1, 0x00A3) and message.wParam == 3
                or message.message == 0x0112 and (message.wParam & 0xFFF0) == 0xF100 and message.lParam == 32):
            if not self.pending:
                self.pending = True
                QTimer.singleShot(0, self.show)
            return True
        if message.message in (0x001A, 0x02E0):  # Settings / DPI changes.
            QTimer.singleShot(0, self.refresh)
        return False

    def refresh(self):
        if not self.window.closing:
            self.install_icon()

    def show(self):
        self.pending = False
        if not self.window.closing:
            self.menu.popup(self.window.mapToGlobal(QPoint(0, 0)))

    def close(self):
        if self.icon:
            send_message(int(self.window.winId()), 0x0080, 0, 0)
            destroy_icon(self.icon)
            self.icon = None
