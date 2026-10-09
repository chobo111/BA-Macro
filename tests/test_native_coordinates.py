import ctypes as C
from ctypes import wintypes as W
from unittest import TestCase
from unittest.mock import patch
from native import Windows


class NativeCoordinatesTests(TestCase):
    def setUp(self):
        self.native = Windows()

    def test_empty_target_is_rejected_before_native_coordinate_calls(self):
        for target in (None,0):
            for method,args in ((self.native.size,(target,)),(self.native.bounds,(target,)),
                                (self.native.screen,(target,10,20)),(self.native.local_point,(target,10,20))):
                with self.subTest(target=target,method=method.__name__):
                    with self.assertRaisesRegex(ValueError,'앱을 선택'):
                        method(*args)

    def test_app_coordinates_use_window_origin_on_any_monitor(self):
        def transform(hwnd,point):
            p=C.cast(point,C.POINTER(W.POINT)).contents
            p.x-=1920
            p.y-=200
            return True
        with patch('native.to_screen',side_effect=transform):
            self.assertEqual(self.native.screen(99,120,300),(-1800,100))
        def inverse(hwnd,point):
            p=C.cast(point,C.POINTER(W.POINT)).contents
            p.x+=1920
            p.y+=200
            return True
        with patch('native.to_client',side_effect=inverse):
            self.assertEqual(self.native.local_point(99,-1800,100),(120,300))

    def test_minimized_window_has_no_usable_size_and_does_not_read_client_rect(self):
        from domain import WindowSizeUnavailable
        with patch('native.minimized', return_value=True), patch('native.get_rect') as query:
            with self.assertRaises(WindowSizeUnavailable):
                self.native.size(99)
            query.assert_not_called()

    def test_minimize_during_client_query_is_not_a_zero_resolution(self):
        from domain import WindowSizeUnavailable
        with patch('native.minimized', side_effect=[False, True]), patch('native.get_rect', return_value=True):
            with self.assertRaises(WindowSizeUnavailable):
                self.native.size(99)

    def test_empty_restoring_client_rect_is_rejected_then_actual_size_is_read(self):
        from domain import WindowSizeUnavailable
        for size in ((0,0), (1280,0), (0,720), (1280,720)):
            def fill(hwnd, pointer):
                rect = C.cast(pointer,C.POINTER(W.RECT)).contents
                rect.right, rect.bottom = size
                return True
            with self.subTest(size=size), patch('native.minimized', return_value=False), \
                 patch('native.get_rect', side_effect=fill):
                if 0 in size:
                    with self.assertRaises(WindowSizeUnavailable):
                        self.native.size(99)
                else:
                    self.assertEqual(self.native.size(99), size)
