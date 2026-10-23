import ctypes as C
from ctypes import wintypes as W
from unittest import TestCase
from unittest.mock import patch

from native import Windows


class NativeIdentityTests(TestCase):
    def setUp(self):
        self.native = Windows()

    def test_window_process_id_returns_process_id_as_int(self):
        def get_process_id(hwnd, pointer):
            C.cast(pointer, C.POINTER(W.DWORD)).contents.value = 1234
            return 5678

        with patch('native.valid_window', return_value=True) as valid, \
             patch('native.get_pid', side_effect=get_process_id) as query:
            result = self.native.window_process_id(99)

        self.assertEqual(result, 1234)
        self.assertIs(type(result), int)
        valid.assert_called_once_with(99)
        self.assertEqual(query.call_args.args[0], 99)
        query.assert_called_once()

    def test_empty_window_is_rejected_before_native_calls(self):
        for hwnd in (None, 0):
            with self.subTest(hwnd=hwnd), patch('native.valid_window') as valid, \
                 patch('native.get_pid') as query:
                with self.assertRaises(OSError):
                    self.native.window_process_id(hwnd)
                valid.assert_not_called()
                query.assert_not_called()

    def test_invalid_window_is_rejected_before_process_query(self):
        with patch('native.valid_window', return_value=False), \
             patch('native.get_pid') as query:
            with self.assertRaises(OSError):
                self.native.window_process_id(99)
            query.assert_not_called()

    def test_process_query_requires_success_and_nonzero_process_id(self):
        for thread_id, process_id in ((0, 0), (0, 1234), (5678, 0)):
            def get_process_id(hwnd, pointer):
                C.cast(pointer, C.POINTER(W.DWORD)).contents.value = process_id
                return thread_id

            with self.subTest(thread_id=thread_id, process_id=process_id), \
                 patch('native.valid_window', return_value=True), \
                 patch('native.get_pid', side_effect=get_process_id):
                with self.assertRaises(OSError):
                    self.native.window_process_id(99)
