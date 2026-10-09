import ctypes as C
import os
import unittest
from unittest.mock import patch
from mouse_hook import CoordinateHook, MouseEvent, U, W

class MouseHookTests(unittest.TestCase):
    def test_only_external_physical_right_click_is_captured(self):
        captured, results = [], []
        hook = CoordinateHook(lambda x,y: captured.append((x,y)))
        callback = None
        own = False
        def install(kind, proc, module, thread):
            nonlocal callback
            self.assertEqual(kind, 14)
            callback = proc
            return 1
        def pid(hwnd, pointer):
            C.cast(pointer, C.POINTER(W.DWORD)).contents.value = os.getpid() if own else 0
            return 1
        def messages(*args):
            nonlocal own
            event = MouseEvent(W.POINT(-123,456),0,0,0,0)
            for message in (0x201,0x202,0x204,0x205):
                results.append(callback(0,message,C.addressof(event)))
            own=True
            results.append(callback(0,0x204,C.addressof(event)))
            own=False
            event.flags=1
            results.append(callback(0,0x204,C.addressof(event)))
            return 0
        with patch.object(U,'SetWindowsHookExW',side_effect=install), patch.object(U,'GetMessageW',side_effect=messages), patch.object(U,'PeekMessageW'), patch.object(U,'WindowFromPoint',return_value=1), patch.object(U,'GetWindowThreadProcessId',side_effect=pid), patch.object(U,'CallNextHookEx',return_value=7), patch.object(U,'UnhookWindowsHookEx') as unhook:
            hook.run()
            unhook.assert_called_once_with(1)
        self.assertIsNone(hook.error)
        self.assertEqual(results,[7,7,1,1,7,7])
        self.assertEqual(captured,[(-123,456)])

    def test_capture_gate_passes_clicks_and_balances_suppressed_releases(self):
        from threading import Event
        enabled = Event()
        captured, results = [], []
        hook = CoordinateHook(lambda x,y: captured.append((x,y)), enabled.is_set)
        callback = None
        def install(kind, proc, module, thread):
            nonlocal callback
            callback = proc
            return 1
        def pid(hwnd, pointer):
            C.cast(pointer, C.POINTER(W.DWORD)).contents.value = 0
            return 1
        def messages(*args):
            event = MouseEvent(W.POINT(123,456),0,0,0,0)
            # A popup is open: neither half of this click is consumed.
            results.append(callback(0,0x204,C.addressof(event)))
            enabled.set()
            results.append(callback(0,0x205,C.addressof(event)))
            # Popup closes: capture resumes. Opening it mid-click must still
            # suppress the release corresponding to the captured press.
            results.append(callback(0,0x204,C.addressof(event)))
            enabled.clear()
            results.append(callback(0,0x205,C.addressof(event)))
            return 0
        with patch.object(U,'SetWindowsHookExW',side_effect=install), patch.object(U,'GetMessageW',side_effect=messages), patch.object(U,'PeekMessageW'), patch.object(U,'WindowFromPoint',return_value=1), patch.object(U,'GetWindowThreadProcessId',side_effect=pid), patch.object(U,'CallNextHookEx',return_value=7), patch.object(U,'UnhookWindowsHookEx'):
            hook.run()
        self.assertIsNone(hook.error)
        self.assertEqual(results,[7,7,1,1])
        self.assertEqual(captured,[(123,456)])
