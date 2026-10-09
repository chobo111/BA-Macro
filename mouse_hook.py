"""Capture only physical right-clicks outside this process while editing."""
import ctypes as C
from ctypes import wintypes as W
import os
from keyboard_hook import PhysicalHook, U

class MouseEvent(C.Structure):
    _fields_ = [('point', W.POINT), ('data', W.DWORD), ('flags', W.DWORD),
                ('time', W.DWORD), ('extra', C.c_size_t)]

U.WindowFromPoint.argtypes, U.WindowFromPoint.restype = [W.POINT], W.HWND
U.GetWindowThreadProcessId.argtypes, U.GetWindowThreadProcessId.restype = [W.HWND, C.POINTER(W.DWORD)], W.DWORD

class CoordinateHook(PhysicalHook):
    def __init__(self, handler, can_capture=lambda: True, on_error=None):
        super().__init__(handler, on_error)
        self.can_capture = can_capture

    def run(self):
        suppressed = False
        def callback(code, message, data):
            nonlocal suppressed
            if code >= 0 and message in (0x204, 0x205):
                event = C.cast(data, C.POINTER(MouseEvent)).contents
                if not event.flags & 3:
                    if message == 0x205 and suppressed:
                        suppressed = False
                        return 1
                    if message == 0x204 and self.can_capture():
                        pid = W.DWORD()
                        U.GetWindowThreadProcessId(U.WindowFromPoint(event.point), C.byref(pid))
                        if pid.value != os.getpid():
                            try:
                                self.handler(event.point.x, event.point.y)
                                suppressed = True
                                return 1
                            except Exception:
                                pass
            return U.CallNextHookEx(None, code, message, data)
        self._run_hook(14, callback)
