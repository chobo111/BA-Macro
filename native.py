"""Typed Win32 boundary. Coordinates use the target client area."""
import ntpath
import ctypes as C
from ctypes import wintypes as W
from domain import target_identity
from displays import enumerate_displays, monitor_info

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
get_class = bind("GetClassNameW", [W.HWND, W.LPWSTR, C.c_int], C.c_int)
K = C.WinDLL('kernel32', use_last_error=True)
K.OpenProcess.argtypes, K.OpenProcess.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
K.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
K.QueryFullProcessImageNameW.restype = W.BOOL
K.CloseHandle.argtypes, K.CloseHandle.restype = [W.HANDLE], W.BOOL
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
window_from_point = bind("WindowFromPoint", [W.POINT], W.HWND)
get_ancestor = bind("GetAncestor", [W.HWND, W.UINT], W.HWND)
ENUM = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
enum_windows = bind("EnumWindows", [ENUM, W.LPARAM], W.BOOL)


def check(value):
    if not value:
        raise OSError("Windows 입력/창 제어에 실패했습니다. 대상 창과 권한을 확인하세요.")
    return value


class Windows:
    def __init__(self):
        self.display_id = ''
        self.displays = ()

    def refresh_displays(self):
        self.displays = tuple(enumerate_displays())
        return self.displays

    def selected_display(self):
        if not self.display_id:
            return None
        matches = [d for d in self.displays if d.id == self.display_id]
        if len(matches) != 1:
            raise OSError('선택한 디스플레이가 없거나 구분할 수 없습니다. 환경설정에서 다시 선택하세요.')
        return matches[0]

    def validate_context(self, hwnd):
        if hwnd or not self.display_id:
            return
        display = self.selected_display()
        info = monitor_info(display.handle)
        r = info.monitor
        if (info.device, r.left, r.top, r.right-r.left, r.bottom-r.top) != (
                display.device, display.left, display.top, display.width, display.height):
            raise OSError('디스플레이 배치 또는 해상도가 변경되었습니다. 확인 후 다시 시작하세요.')

    def validate_pointer(self, hwnd):
        if not hwnd and self.display_id:
            x, y = self.cursor()
            left, top, right, bottom = self.bounds(0)
            if not left <= x < right or not top <= y < bottom:
                raise OSError('커서가 선택한 디스플레이 밖에 있습니다.')

    def local_point(self, hwnd, x, y):
        point = W.POINT(x, y)
        if hwnd:
            check(to_client(hwnd, C.byref(point)))
            return point.x, point.y
        display = self.selected_display()
        return (x-display.left, y-display.top) if display else (x, y)

    def window_identity(self, hwnd):
        check(valid_window(hwnd))
        title, classname = C.create_unicode_buffer(1024), C.create_unicode_buffer(256)
        get_title(hwnd, title, len(title))
        get_class(hwnd, classname, len(classname))
        pid = W.DWORD()
        get_pid(hwnd, C.byref(pid))
        process = K.OpenProcess(0x1000, False, pid.value)
        exe = ''
        if process:
            try:
                path, size = C.create_unicode_buffer(32768), W.DWORD(32768)
                if K.QueryFullProcessImageNameW(process, 0, path, C.byref(size)):
                    exe = ntpath.basename(path.value)
            finally:
                K.CloseHandle(process)
        return target_identity({'exe': exe, 'class': classname.value, 'title': title.value})

    def find_target(self, saved, windows):
        saved = target_identity(saved)
        if not saved:
            return 0
        candidates = []
        for hwnd, _ in windows:
            try:
                info = target_identity(self.window_identity(hwnd))
            except OSError:
                continue
            if saved.get('exe'):
                matches = (info['exe'].casefold() == saved['exe'].casefold()
                           and info['class'] == saved.get('class'))
            else:
                matches = bool(saved.get('class') and saved.get('title')) and all(
                    info[k] == saved[k] for k in ('class', 'title'))
            if matches:
                candidates.append((hwnd, info))
        exact = [hwnd for hwnd, info in candidates if info['title'] == saved.get('title')]
        if len(exact) == 1:
            return exact[0]
        return candidates[0][0] if len(candidates) == 1 else 0

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

    def foreground_owned_by(self, process_id):
        pid = W.DWORD()
        get_pid(foreground(), C.byref(pid))
        return pid.value == process_id

    def foreground(self):
        return int(foreground() or 0)

    def cursor_screen(self):
        point = W.POINT()
        check(get_cursor(C.byref(point)))
        return point.x, point.y

    def cursor(self, hwnd=None):
        return self.local_point(hwnd, *self.cursor_screen())

    def screen(self, hwnd, x, y):
        if not hwnd:
            display = self.selected_display()
            return (x+display.left, y+display.top) if display else (x, y)
        point = W.POINT(x, y)
        check(to_screen(hwnd, C.byref(point)))
        return point.x, point.y

    def point_belongs_to(self, hwnd, x, y):
        hit = window_from_point(W.POINT(x, y))
        return bool(hit and get_ancestor(hit, 2) == hwnd)

    def create_touch_input(self):
        from touch_input import TouchScriptInput
        return TouchScriptInput(self)

    def move(self, x, y):
        check(set_cursor(x, y))

    def button(self, down):
        packet = Input(type=0, payload=Payload(mi=MouseInput(0, 0, 0, 0x2 if down else 0x4, 0, 0)))
        check(send_input(1, C.byref(packet), C.sizeof(Input)))

    def size(self, hwnd):
        if not hwnd:
            display = self.selected_display()
            return (display.width, display.height) if display else (metrics(78), metrics(79))
        rect = W.RECT()
        check(get_rect(hwnd, C.byref(rect)))
        return rect.right, rect.bottom

    def bounds(self, hwnd):
        if not hwnd:
            display = self.selected_display()
            if display:
                return 0, 0, display.width, display.height
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
