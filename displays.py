"""Physical Windows monitor enumeration, separate from Qt logical geometry."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass

user32 = C.WinDLL('user32', use_last_error=True)

class MonitorInfo(C.Structure):
    _fields_ = [('size', W.DWORD), ('monitor', W.RECT), ('work', W.RECT),
                ('flags', W.DWORD), ('device', W.WCHAR * 32)]

class DisplayDevice(C.Structure):
    _fields_ = [('size', W.DWORD), ('name', W.WCHAR * 32), ('description', W.WCHAR * 128),
                ('flags', W.DWORD), ('device_id', W.WCHAR * 128), ('key', W.WCHAR * 128)]

CALLBACK = C.WINFUNCTYPE(W.BOOL, W.HANDLE, W.HDC, C.POINTER(W.RECT), W.LPARAM)
user32.EnumDisplayMonitors.argtypes = [W.HDC, C.POINTER(W.RECT), CALLBACK, W.LPARAM]
user32.EnumDisplayMonitors.restype = W.BOOL
user32.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(MonitorInfo)]
user32.GetMonitorInfoW.restype = W.BOOL
user32.EnumDisplayDevicesW.argtypes = [W.LPCWSTR, W.DWORD, C.POINTER(DisplayDevice), W.DWORD]
user32.EnumDisplayDevicesW.restype = W.BOOL

@dataclass(frozen=True)
class Display:
    id: str
    name: str
    handle: int
    device: str
    left: int
    top: int
    width: int
    height: int
    primary: bool


def monitor_info(handle):
    info = MonitorInfo()
    info.size = C.sizeof(info)
    if not user32.GetMonitorInfoW(handle, C.byref(info)):
        raise OSError('선택한 디스플레이가 연결되어 있지 않습니다. 환경설정에서 다시 선택하세요.')
    return info


def enumerate_displays():
    result = []
    errors = []
    @CALLBACK
    def visit(handle, dc, rect, data):
        try:
            info = monitor_info(handle)
            device = DisplayDevice()
            device.size = C.sizeof(device)
            found = user32.EnumDisplayDevicesW(info.device, 0, C.byref(device), 1)
            identity = device.device_id if found and device.device_id else info.device
            r = info.monitor
            result.append(Display(identity, device.description if found else info.device,
                                  int(handle), info.device, r.left, r.top,
                                  r.right-r.left, r.bottom-r.top, bool(info.flags & 1)))
        except OSError as error:
            errors.append(error)
        return True
    if not user32.EnumDisplayMonitors(None, None, visit, 0):
        raise C.WinError(C.get_last_error())
    if errors:
        raise errors[0]
    return sorted(result, key=lambda d: (not d.primary, d.device))
