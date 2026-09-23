"""Typed Win32 boundary. Coordinates use the target client area."""
import ctypes as C
from ctypes import wintypes as W
from script_language import KEYS

U = C.WinDLL("user32", use_last_error=True)
ULONG_PTR = C.c_size_t


class MouseInput(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KeyboardInput(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HardwareInput(C.Structure):
    _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]


class Payload(C.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]


class Input(C.Structure):
    _fields_ = [("type", W.DWORD), ("payload", Payload)]


def bind(name, args, result):
    fn = getattr(U, name)
    fn.argtypes, fn.restype = args, result
    return fn


foreground = bind("GetForegroundWindow", [], W.HWND)
metrics = bind("GetSystemMetrics", [C.c_int], C.c_int)
valid_window = bind("IsWindow", [W.HWND], W.BOOL)
visible = bind("IsWindowVisible", [W.HWND], W.BOOL)
minimized = bind("IsIconic", [W.HWND], W.BOOL)
get_rect = bind("GetClientRect", [W.HWND, C.POINTER(W.RECT)], W.BOOL)
to_screen = bind("ClientToScreen", [W.HWND, C.POINTER(W.POINT)], W.BOOL)
to_client = bind("ScreenToClient", [W.HWND, C.POINTER(W.POINT)], W.BOOL)
get_cursor = bind("GetCursorPos", [C.POINTER(W.POINT)], W.BOOL)
set_cursor = bind("SetCursorPos", [C.c_int, C.c_int], W.BOOL)
send_input = bind("SendInput", [W.UINT, C.POINTER(Input), C.c_int], W.UINT)
get_title = bind("GetWindowTextW", [W.HWND, W.LPWSTR, C.c_int], C.c_int)
get_pid = bind("GetWindowThreadProcessId", [W.HWND, C.POINTER(W.DWORD)], W.DWORD)
get_long = bind("GetWindowLongW", [W.HWND, C.c_int], W.LONG)
get_window = bind("GetWindow", [W.HWND, W.UINT], W.HWND)
get_shell_window = bind("GetShellWindow", [], W.HWND)
DWM = C.WinDLL('dwmapi')
get_dwm_attribute = DWM.DwmGetWindowAttribute
get_dwm_attribute.argtypes = [W.HWND, W.DWORD, C.c_void_p, W.DWORD]
get_dwm_attribute.restype = C.c_long
get_menu = bind("GetMenu", [W.HWND], W.HMENU)
set_position = bind("SetWindowPos", [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT], W.BOOL)
adjust = bind("AdjustWindowRectEx", [C.POINTER(W.RECT), W.DWORD, W.BOOL, W.DWORD], W.BOOL)
adjust_dpi = (bind("AdjustWindowRectExForDpi", [C.POINTER(W.RECT), W.DWORD, W.BOOL, W.DWORD, W.UINT], W.BOOL)
              if hasattr(U, "AdjustWindowRectExForDpi") else None)
window_dpi = bind("GetDpiForWindow", [W.HWND], W.UINT) if hasattr(U, "GetDpiForWindow") else None
ENUM = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
enum_windows = bind("EnumWindows", [ENUM, W.LPARAM], W.BOOL)


def check(value):
    if not value:
        raise OSError("Windows 입력/창 제어에 실패했습니다. 대상 창과 권한을 확인하세요.")
    return value


class Windows:
    def taskbar_window(self, hwnd):
        if not visible(hwnd) or hwnd == get_shell_window():
            return False
        cloaked = W.DWORD()
        # Cloaked UWP hosts retain WS_VISIBLE even after their UI is closed.
        if get_dwm_attribute(hwnd, 14, C.byref(cloaked), C.sizeof(cloaked)) == 0 and cloaked.value:
            return False
        style = get_long(hwnd, -20) & 0xffffffff
        if style & 0x00000080:  # WS_EX_TOOLWINDOW
            return False
        if style & 0x00040000:  # WS_EX_APPWINDOW explicitly requests a taskbar entry.
            return True
        return not (style & 0x08000000 or get_window(hwnd, 4))  # NOACTIVATE / GW_OWNER

    def windows(self, exclude_pid):
        result = []
        @ENUM
        def visit(hwnd, _):
            pid = W.DWORD()
            get_pid(hwnd, C.byref(pid))
            text = C.create_unicode_buffer(1024)
            get_title(hwnd, text, len(text))
            if text.value and pid.value != exclude_pid and self.taskbar_window(hwnd):
                result.append((int(hwnd), text.value))
            return True
        check(enum_windows(visit, 0))
        return sorted(result, key=lambda x: x[1].lower())

    def active(self, hwnd):
        return bool(hwnd and valid_window(hwnd) and not minimized(hwnd) and foreground() == hwnd)

    def exists(self, hwnd):
        return bool(hwnd and valid_window(hwnd))

    def foreground(self):
        return int(foreground() or 0)

    def cursor(self, hwnd=None):
        point = W.POINT()
        check(get_cursor(C.byref(point)))
        if hwnd:
            check(to_client(hwnd, C.byref(point)))
        return point.x, point.y

    def screen(self, hwnd, x, y):
        if not hwnd:
            return x, y
        point = W.POINT(x, y)
        check(to_screen(hwnd, C.byref(point)))
        return point.x, point.y

    def move(self, x, y):
        check(set_cursor(x, y))

    def button(self, down):
        self.mouse_button('left', down)

    def mouse_button(self, button, down):
        flags = {'left': (0x2, 0x4), 'right': (0x8, 0x10), 'middle': (0x20, 0x40),
                 'back': (0x80, 0x100), 'forward': (0x80, 0x100)}
        data = 1 if button == 'back' else 2 if button == 'forward' else 0
        packet = Input(type=0, payload=Payload(mi=MouseInput(0, 0, data, flags[button][0 if down else 1], 0, 0)))
        check(send_input(1, C.byref(packet), C.sizeof(Input)))

    def key(self, name, down):
        vk = KEYS[name]
        extended = name in {'left', 'right', 'up', 'down', 'home', 'end', 'pageup', 'pagedown', 'insert', 'delete'}
        packet = Input(type=1, payload=Payload(ki=KeyboardInput(vk, 0, (0 if down else 2) | int(extended), 0, 0)))
        check(send_input(1, C.byref(packet), C.sizeof(Input)))

    def text_char(self, char):
        units = char.encode('utf-16-le')
        for i in range(0, len(units), 2):
            code = int.from_bytes(units[i:i+2], 'little')
            packet = Input(type=1, payload=Payload(ki=KeyboardInput(0, code, 4, 0, 0)))
            try:
                check(send_input(1, C.byref(packet), C.sizeof(Input)))
            finally:
                packet.payload.ki.dwFlags = 6
                check(send_input(1, C.byref(packet), C.sizeof(Input)))

    def size(self, hwnd):
        if not hwnd:
            return metrics(78), metrics(79)
        rect = W.RECT()
        check(get_rect(hwnd, C.byref(rect)))
        return rect.right, rect.bottom

    def bounds(self, hwnd):
        if not hwnd:
            x, y = metrics(76), metrics(77)
            return x, y, x + metrics(78), y + metrics(79)
        w, h = self.size(hwnd)
        return 0, 0, w, h

    def topmost(self, hwnd, enabled):
        # Preserve the HWND, geometry, visibility and activation state.
        check(set_position(hwnd, -1 if enabled else -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010))

    def resize(self, hwnd, width, height):
        rect = W.RECT(0, 0, width, height)
        args = (C.byref(rect), get_long(hwnd, -16) & 0xffffffff,
                bool(get_menu(hwnd)), get_long(hwnd, -20) & 0xffffffff)
        if adjust_dpi and window_dpi:
            check(adjust_dpi(*args, window_dpi(hwnd) or 96))
        else:
            check(adjust(*args))
        check(set_position(hwnd, None, 0, 0, rect.right - rect.left, rect.bottom - rect.top, 0x0002 | 0x0004 | 0x0010))
