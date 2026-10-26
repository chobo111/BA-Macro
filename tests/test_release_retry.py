import ctypes as C
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_engine import FakeWindows
from touch_input import TouchDevice, TouchScriptInput, PointerTypeInfo, UP, CANCELED


class ReleaseRetryTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeWindows()
        self.origin = [0, 0]
        self.native.screen = lambda hwnd,x,y: (x+self.origin[0], y+self.origin[1])
        self.native.local_point = lambda hwnd,x,y: (x-self.origin[0], y-self.origin[1])
        self.device = TouchDevice.__new__(TouchDevice)
        self.device.handle = 123
        self.device.promotion_filter = None
        self.device.destroy = Mock()
        self.frames = []
        self.successful = []
        self.release_attempts = 0

        def inject(handle, packet, count):
            flags = C.cast(packet, C.POINTER(PointerTypeInfo)).contents.info.touchInfo.pointerInfo.pointerFlags
            self.frames.append(flags)
            if flags & UP:
                self.release_attempts += 1
                if self.release_attempts == 1:
                    return False
            self.successful.append(flags)
            return True
        self.device.inject = inject
        self.adapter = TouchScriptInput(self.native, lambda: self.device)
        self.adapter.begin(1)
        self.adapter.move(800, 600)
        self.adapter.mouse_button('left', True)
        self.addCleanup(self.adapter.close)

    def release(self, change):
        with patch('touch_input.C.get_last_error', return_value=21), \
                patch('touch_input.time.sleep', side_effect=lambda _: change()):
            self.adapter.mouse_button('left', False)
        self.assertFalse(self.adapter.pressed)

    def test_unchanged_window_retries_normal_release(self):
        self.release(lambda: None)
        self.assertEqual(self.frames[-2:], [UP, UP])
        self.assertEqual(self.successful[-1], UP)
        self.device.destroy.assert_not_called()

    def assert_cancelled_by_destruction(self):
        self.device.destroy.assert_called_once_with(123)
        self.assertIsNone(self.adapter.device)
        self.assertFalse(any(flags & UP for flags in self.successful))

    def test_move_during_release_retry_destroys_contact(self):
        self.release(lambda: self.origin.__setitem__(slice(None), [20, 30]))
        self.assertEqual(self.release_attempts, 1)
        self.assert_cancelled_by_destruction()

    def test_focus_loss_during_release_retry_destroys_contact(self):
        self.release(lambda: setattr(self.native, 'active_value', False))
        self.assertEqual(self.release_attempts, 1)
        self.assert_cancelled_by_destruction()

    def test_query_failure_during_release_retry_destroys_contact(self):
        self.release(lambda: setattr(self.native, 'screen', Mock(side_effect=OSError('closed'))))
        self.assert_cancelled_by_destruction()

    def test_already_cancelled_release_is_still_retried(self):
        self.native.active_value = False
        self.release(lambda: None)
        self.assertEqual(self.frames[-2:], [UP | CANCELED, UP | CANCELED])
        self.assertEqual(self.successful[-1], UP | CANCELED)

    def test_release_ignores_contact_guard_after_stop(self):
        self.adapter.check_input = Mock(side_effect=RuntimeError('stopped'))
        self.release(lambda: None)
        self.adapter.check_input.assert_not_called()
        self.assertEqual(self.successful[-1], UP)

    def test_move_during_diagnostic_sample_destroys_contact(self):
        def sample(*args):
            self.origin[:] = [20, 30]
            return {'kind': '해제'}
        trace = SimpleNamespace(sampling=True, sample=sample, record=Mock())
        self.adapter.diagnostic = trace
        self.adapter.mouse_button('left', False)
        self.assertFalse(self.adapter.pressed)
        self.assertEqual(self.release_attempts, 0)
        self.assert_cancelled_by_destruction()
        self.assertIn('취소', trace.record.call_args.args[1])
