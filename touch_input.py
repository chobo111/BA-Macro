"""Experimental synthetic touch input. Never calls SetCursorPos or SendInput."""
import ctypes as C
from ctypes import wintypes as W
import time
from collections import deque
from keyboard_hook import PhysicalHook, PROC, U, K
from mouse_hook import MouseEvent
from script_language import ScriptError

PT_TOUCH = 2
INRANGE, INCONTACT, DOWN, UPDATE, UP, CANCELED = 2, 4, 0x10000, 0x20000, 0x40000, 0x8000


class PointerInfo(C.Structure):
    _fields_ = [('pointerType', W.DWORD), ('pointerId', W.UINT), ('frameId', W.UINT),
                ('pointerFlags', W.DWORD), ('sourceDevice', W.HANDLE), ('hwndTarget', W.HWND),
                ('ptPixelLocation', W.POINT), ('ptHimetricLocation', W.POINT),
                ('ptPixelLocationRaw', W.POINT), ('ptHimetricLocationRaw', W.POINT),
                ('dwTime', W.DWORD), ('historyCount', W.UINT), ('InputData', C.c_int32),
                ('dwKeyStates', W.DWORD), ('PerformanceCount', C.c_uint64), ('ButtonChangeType', C.c_int)]


class TouchInfo(C.Structure):
    _fields_ = [('pointerInfo', PointerInfo), ('touchFlags', W.DWORD), ('touchMask', W.DWORD),
                ('rcContact', W.RECT), ('rcContactRaw', W.RECT), ('orientation', W.UINT), ('pressure', W.UINT)]


class PenInfo(C.Structure):
    _fields_ = [('pointerInfo', PointerInfo), ('penFlags', W.DWORD), ('penMask', W.DWORD),
                ('pressure', W.UINT), ('rotation', W.UINT), ('tiltX', C.c_int32), ('tiltY', C.c_int32)]


class PointerUnion(C.Union):
    _fields_ = [('pointerInfo', PointerInfo), ('touchInfo', TouchInfo), ('penInfo', PenInfo)]


class PointerTypeInfo(C.Structure):
    _fields_ = [('type', C.c_int), ('info', PointerUnion)]


def validate_touch_program(program):
    def visit(instructions):
        for instruction in instructions:
            name = instruction.name
            if name.startswith('key_') or name == 'text' or (name.startswith('mouse_') and instruction.args[0] != 'left'):
                raise ScriptError(instruction.line, '독립 터치는 좌클릭·이동·드래그만 지원합니다. 이 명령은 일반 마우스 방식에서 사용하세요.')
            visit(instruction.body)
    visit(program.pressed)
    visit(program.released)


class TouchPromotionFilter(PhysicalHook):
    """Suppress only touch-generated mouse events at our recent injected points.

    Real mouse movement and unrelated touch/pen events pass through unchanged.
    """
    def __init__(self):
        super().__init__(None)
        self.points = deque(maxlen=128)
        self.thread.name = 'touch-mouse-promotion'

    def expect(self, position):
        self.points.append((position, time.monotonic()))

    def matches(self, event):
        if event.extra & 0xFFFFFF80 != 0xFF515780:
            return False
        point, now = (event.point.x, event.point.y), time.monotonic()
        return any(saved == point and now - stamp < .25 for saved, stamp in tuple(self.points))

    def run(self):
        handle = None
        @PROC
        def callback(code, message, data):
            if code >= 0:
                event = C.cast(data, C.POINTER(MouseEvent)).contents
                if self.matches(event):
                    return 1
            return U.CallNextHookEx(None, code, message, data)
        try:
            self.thread_id = K.GetCurrentThreadId()
            msg = W.MSG()
            U.PeekMessageW(C.byref(msg), None, 0, 0, 0)
            handle = U.SetWindowsHookExW(14, callback, K.GetModuleHandleW(None), 0)
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


class TouchDevice:
    """One worker-owned pointer device; destroy also cancels any active contact."""
    def __init__(self):
        self.promotion_filter = None
        api = C.WinDLL('user32', use_last_error=True)
        try:
            self.create = api.CreateSyntheticPointerDevice
            self.inject = api.InjectSyntheticPointerInput
            self.destroy = api.DestroySyntheticPointerDevice
        except AttributeError:
            raise OSError('독립 터치 입력은 Windows 10 1809 이상이 필요합니다.') from None
        self.create.argtypes, self.create.restype = [C.c_int, W.ULONG, C.c_int], W.HANDLE
        self.inject.argtypes, self.inject.restype = [W.HANDLE, C.POINTER(PointerTypeInfo), W.UINT], W.BOOL
        self.destroy.argtypes, self.destroy.restype = [W.HANDLE], None
        self.handle = self.create(PT_TOUCH, 1, 3)  # POINTER_FEEDBACK_NONE
        if not self.handle:
            raise C.WinError(C.get_last_error())
        self.promotion_filter = TouchPromotionFilter()
        try:
            self.promotion_filter.start()
        except Exception:
            self.close()
            raise

    def send(self, position, flags):
        if not self.handle:
            raise OSError('터치 입력 장치가 닫혔습니다.')
        if self.promotion_filter:
            self.promotion_filter.expect(position)
        packet = PointerTypeInfo(type=PT_TOUCH)
        pointer = packet.info.touchInfo.pointerInfo
        pointer.pointerType, pointer.pointerId = PT_TOUCH, 1
        pointer.pointerFlags = flags
        pointer.ptPixelLocation = W.POINT(*position)
        # Let Windows timestamp the frame; retry identical frames if too close.
        for attempt in range(4):
            if self.inject(self.handle, C.byref(packet), 1):
                return
            error = C.get_last_error()
            if error != 21 or attempt == 3:  # ERROR_NOT_READY
                self.close()
                raise OSError(error, f'독립 터치 입력 실패 (Windows 오류 {error})')
            time.sleep(.001)

    def close(self):
        if self.handle:
            self.destroy(self.handle)
            self.handle = None
        if self.promotion_filter:
            self.promotion_filter.close()
            self.promotion_filter = None


class TouchScriptInput:
    # Touch movement leaves the physical cursor available as a live aim point.
    live_mouse = True

    def __init__(self, native, device_factory=TouchDevice):
        self.native = native
        self.device_factory = device_factory
        self.device = None
        self.target = 0
        self.position = None
        self.pressed = False
        self.last_frame = 0

    def begin(self, target, program):
        validate_touch_program(program)  # Reject before delivering any partial script.
        if not target or not self.native.active(target):
            raise ValueError('독립 터치는 활성화된 대상 창을 선택해야 사용할 수 있습니다.')
        if self.target != target:
            self.close()
        self.target = target
        if not self.pressed:
            self.position = self.native.screen(target, *self.native.cursor(target))

    def cursor(self, target):
        return self.native.cursor(target)

    def bounds(self, target):
        return self.native.bounds(target)

    def screen(self, target, x, y):
        return self.native.screen(target, x, y)

    def validate_pointer(self, target):
        if not self.native.active(self.target) or not self.native.point_belongs_to(self.target, *self.position):
            raise ValueError('터치 위치가 대상 창에 가려졌거나 대상 창이 비활성 상태입니다.')

    def _send(self, flags):
        if self.device is None:
            self.device = self.device_factory()
        try:
            self.device.send(self.position, flags)
            self.last_frame = time.monotonic()
        except Exception:
            self.close()
            raise

    def move(self, x, y):
        previous = self.position
        self.position = (x, y)
        if self.pressed:
            try:
                self.validate_pointer(self.target)
            except Exception:
                self.position = previous
                raise
            self._send(UPDATE | INRANGE | INCONTACT)

    def mouse_button(self, name, down):
        if name != 'left':
            raise ValueError('독립 터치는 왼쪽 버튼만 지원합니다.')
        if down:
            self.validate_pointer(self.target)
            self._send((UPDATE if self.pressed else DOWN) | INRANGE | INCONTACT)
            self.pressed = True
        elif self.pressed:
            flags = UP if self.native.active(self.target) else UP | CANCELED
            self._send(flags)
            self.pressed = False

    def keep_alive(self):
        if self.pressed and time.monotonic() - self.last_frame >= .05:
            self.validate_pointer(self.target)
            self._send(UPDATE | INRANGE | INCONTACT)

    def key(self, name, down):
        raise ValueError('독립 터치는 키보드 입력을 지원하지 않습니다.')

    def text_char(self, char):
        raise ValueError('독립 터치는 문자 입력을 지원하지 않습니다.')

    def close(self):
        if self.device is not None:
            self.device.close()
            self.device = None
        self.pressed = False
