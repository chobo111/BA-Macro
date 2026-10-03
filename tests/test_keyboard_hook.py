import unittest
from keyboard_hook import KeyEvent, physical_key_event


class KeyboardHookEventTests(unittest.TestCase):
    def test_preserves_virtual_key_and_extended_flag(self):
        arrow = physical_key_event(KeyEvent(vk=0x25, scan=75, flags=1), 0x100)
        keypad = physical_key_event(KeyEvent(vk=0x64, scan=75, flags=0), 0x100)
        self.assertEqual(arrow.scan_code, keypad.scan_code)
        self.assertEqual(arrow.virtual_key, 0x25)
        self.assertEqual(keypad.virtual_key, 0x64)
        self.assertTrue(arrow.is_extended)
        self.assertFalse(keypad.is_extended)

    def test_generated_input_is_never_dispatched(self):
        for flag in (0x10, 0x02, 0x12, 0x11, 0x13):
            with self.subTest(flag=flag):
                self.assertIsNone(physical_key_event(KeyEvent(vk=0x51, scan=16, flags=flag), 0x100))

    def test_preserves_system_key_release_and_scanless_keys(self):
        down = physical_key_event(KeyEvent(vk=0xAF, scan=0), 0x104)
        up = physical_key_event(KeyEvent(vk=0xAF, scan=0, flags=0x80), 0x105)
        self.assertEqual(down.scan_code, -0xAF)
        self.assertEqual(down.event_type, 'down')
        self.assertEqual(up.virtual_key, 0xAF)
        self.assertEqual(up.event_type, 'up')
