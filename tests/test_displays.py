import ctypes as C
from ctypes import wintypes as W
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from displays import Display
from native import Windows
from domain import Settings


LEFT = Display('monitor-left','Secondary',12,'DISPLAY2',-1920,-200,1920,1080,False)

class DisplayTests(TestCase):
    def setUp(self):
        self.native = Windows()
        self.native.displays = (LEFT,)
        self.native.display_id = LEFT.id

    def test_negative_desktop_origin_uses_local_positive_coordinates(self):
        self.assertEqual(self.native.local_point(0,-1800,100),(120,300))
        self.assertEqual(self.native.screen(0,120,300),(-1800,100))
        self.assertEqual(self.native.bounds(0),(0,0,1920,1080))
        self.assertEqual(self.native.size(0),(1920,1080))
        def cursor(point):
            p=C.cast(point,C.POINTER(W.POINT)).contents
            p.x,p.y=-1800,100
            return True
        with patch('native.get_cursor',side_effect=cursor):
            self.assertEqual(self.native.cursor(0),(120,300))

    def test_target_window_ignores_missing_selected_monitor(self):
        self.native.displays=()
        self.native.validate_context(99)
        self.native.validate_pointer(99)
        def transform(hwnd,point):
            p=C.cast(point,C.POINTER(W.POINT)).contents
            p.x+=500
            p.y+=600
            return True
        with patch('native.to_screen',side_effect=transform):
            self.assertEqual(self.native.screen(99,10,20),(510,620))
        with patch('native.to_client',side_effect=transform):
            self.assertEqual(self.native.local_point(99,10,20),(510,620))

    def test_disconnected_or_changed_display_cannot_execute(self):
        self.native.displays=()
        with self.assertRaises(OSError):
            self.native.validate_context(0)
        self.native.displays=(LEFT,)
        info=SimpleNamespace(device=LEFT.device,monitor=W.RECT(-1920,-200,0,880))
        with patch('native.monitor_info',return_value=info):
            self.native.validate_context(0)
            info.monitor.left=-1600
            with self.assertRaises(OSError):
                self.native.validate_context(0)

    def test_pointer_outside_selected_monitor_rejected(self):
        with patch.object(self.native,'cursor',return_value=(1921,10)):
            with self.assertRaises(OSError):
                self.native.validate_pointer(0)
            self.native.validate_pointer(99)

    def test_legacy_virtual_desktop_coordinates_remain_unchanged(self):
        self.native.display_id=''
        with patch('native.metrics',side_effect=lambda n:{76:-1920,77:0,78:3840,79:1080}[n]):
            self.assertEqual(self.native.bounds(0),(-1920,0,1920,1080))
            self.assertEqual(self.native.size(0),(3840,1080))
            self.assertEqual(self.native.screen(0,-100,20),(-100,20))
            self.assertEqual(self.native.local_point(0,-100,20),(-100,20))

    def test_display_setting_roundtrip_and_validation(self):
        self.assertEqual(Settings.read({}).display_id,'')
        self.assertEqual(Settings.read({'display_id':LEFT.id}).display_id,LEFT.id)
        with self.assertRaises(ValueError):
            Settings.read({'display_id':123})

    def test_coordinate_action_uses_monitor_origin_but_script_requires_target(self):
        from engine import Engine, Command
        from script_language import compile_script
        command = Command(0, 120, 300, 'move_and_click', 'q', 0, 1)
        info = SimpleNamespace(device=LEFT.device, monitor=W.RECT(-1920,-200,0,880))
        with patch('native.monitor_info', return_value=info), \
             patch.object(self.native, 'cursor', return_value=(120,300)), \
             patch.object(self.native, 'cursor_screen', return_value=(-2300,50)), \
             patch.object(self.native, 'move') as move, \
             patch.object(self.native, 'button') as button:
            engine = Engine(self.native, lambda key: False)
            try:
                engine.execute(command)
                self.assertEqual(move.call_args_list[0].args, (-1800,100))
                move.assert_called_with(-2300,50)
                self.assertEqual(button.call_args_list[0].args, (True,))
                with self.assertRaises(ValueError):
                    engine.runtime.native.begin(0, compile_script('click 120,300'))
            finally:
                engine.shutdown()

    def test_monitor_model_name_and_unavailable_edid_fallback(self):
        from displays import monitor_name
        from unittest.mock import MagicMock
        edid=bytearray(128)
        edid[:8]=b'\x00\xff\xff\xff\xff\xff\xff\x00'
        edid[54:72]=b'\x00\x00\x00\xfc\x00PHL 279M1RV\n '
        identity=r'\\?\DISPLAY#PHLC254#instance#guid'
        with patch('displays.winreg.OpenKey',return_value=MagicMock()), \
             patch('displays.winreg.QueryValueEx',return_value=(bytes(edid),3)):
            self.assertEqual(monitor_name(identity,'Generic PnP Monitor'),'PHL 279M1RV')
        with patch('displays.winreg.OpenKey',side_effect=OSError):
            self.assertEqual(monitor_name(identity,'Generic PnP Monitor'),'Generic PnP Monitor')
