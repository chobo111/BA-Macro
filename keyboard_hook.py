"""Physical keyboard input only: script-generated SendInput never retriggers macros."""
import ctypes as C
from ctypes import wintypes as W
import threading
from types import SimpleNamespace

U, K = C.WinDLL('user32', use_last_error=True), C.WinDLL('kernel32', use_last_error=True)
PROC = C.WINFUNCTYPE(C.c_ssize_t, C.c_int, W.WPARAM, W.LPARAM)


class KeyEvent(C.Structure):
    _fields_ = [('vk', W.DWORD), ('scan', W.DWORD), ('flags', W.DWORD), ('time', W.DWORD), ('extra', C.c_size_t)]


def physical_key_event(event, message):
    """Keep identity information that scan codes alone cannot distinguish."""
    # LLKHF_INJECTED / LLKHF_LOWER_IL_INJECTED: never dispatch generated input.
    if event.flags & 0x12:
        return None
    return SimpleNamespace(scan_code=event.scan or -event.vk,
                           virtual_key=event.vk, is_extended=bool(event.flags & 0x01),
                           event_type='down' if message in (0x100, 0x104) else 'up')


U.SetWindowsHookExW.argtypes, U.SetWindowsHookExW.restype = [C.c_int, PROC, W.HINSTANCE, W.DWORD], W.HANDLE
U.CallNextHookEx.argtypes, U.CallNextHookEx.restype = [W.HANDLE, C.c_int, W.WPARAM, W.LPARAM], C.c_ssize_t
U.UnhookWindowsHookEx.argtypes, U.UnhookWindowsHookEx.restype = [W.HANDLE], W.BOOL
U.GetMessageW.argtypes, U.GetMessageW.restype = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], C.c_int
U.PostThreadMessageW.argtypes, U.PostThreadMessageW.restype = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM], W.BOOL
U.PeekMessageW.argtypes, U.PeekMessageW.restype = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT], W.BOOL
K.GetModuleHandleW.argtypes, K.GetModuleHandleW.restype = [W.LPCWSTR], W.HMODULE
K.GetCurrentThreadId.argtypes, K.GetCurrentThreadId.restype = [], W.DWORD


U.VkKeyScanW.argtypes, U.VkKeyScanW.restype = [W.WCHAR], C.c_short


def character_key(character):
    """Return the layout's virtual key and modifiers needed to type a character."""
    code = U.VkKeyScanW(character)
    if code == -1:
        return None
    required = frozenset(name for bit, name in ((1, 'shift'), (2, 'ctrl'), (4, 'alt'))
                         if (code >> 8) & bit)
    return code & 0xFF, required


class PhysicalHook:
    def __init__(self, handler):
        self.handler = handler
        self.ready = threading.Event()
        self.error = None
        self.thread_id = 0
        self.thread = threading.Thread(target=self.run, name='physical-keyboard', daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError('키보드 훅 초기화 시간이 초과됐습니다.')
        if self.error:
            raise self.error
        return self.close

    def run(self):
        handle = None
        @PROC
        def callback(code, message, data):
            if code >= 0 and message in (0x100, 0x101, 0x104, 0x105):
                event = C.cast(data, C.POINTER(KeyEvent)).contents
                physical = physical_key_event(event, message)
                if physical is not None:
                    try:
                        accepted = self.handler(physical)
                        if not accepted:
                            return 1
                    except Exception:
                        pass
            return U.CallNextHookEx(None, code, message, data)
        try:
            self.thread_id = K.GetCurrentThreadId()
            msg = W.MSG()
            U.PeekMessageW(C.byref(msg), None, 0, 0, 0)
            handle = U.SetWindowsHookExW(13, callback, K.GetModuleHandleW(None), 0)
            if not handle:
                raise C.WinError(C.get_last_error())
            self.ready.set()
            while U.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                pass
        except Exception as error:
            self.error = error
        finally:
            self.ready.set()
            if handle:
                U.UnhookWindowsHookEx(handle)

    def close(self):
        if self.thread_id:
            U.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
            self.thread.join(2)
