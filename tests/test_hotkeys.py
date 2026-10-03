from types import SimpleNamespace
import unittest
import keyboard
from hotkeys import Hotkeys


class HotkeyTests(unittest.TestCase):
    def setUp(self):
        self.actions = []
        self.enabled = True
        self.stops = []
        self.hooks = Hotkeys(lambda *args: self.actions.append(args),
                             lambda action: action == "pause" or self.enabled,
                             lambda: self.stops.append(True))
        self.hooks.configure([("q", "move", "1"), ("shift+q", "capture", "1"), ("ctrl+tab", "pause", "")], True)

    def event(self, key, kind="down"):
        return self.hooks.handle(SimpleNamespace(scan_code=keyboard.key_to_scan_codes(key)[0], event_type=kind))

    def test_repeat_and_release(self):
        self.assertFalse(self.event("q"))
        self.assertFalse(self.event("q"))
        self.assertEqual(len(self.actions), 1)
        self.assertTrue(self.hooks.held("q"))
        self.assertFalse(self.event("q", "up"))
        self.assertFalse(self.hooks.held("q"))
        self.event("q")
        self.assertEqual(len(self.actions), 2)

    def test_modifier_does_not_trigger_plain_key(self):
        self.event("shift")
        self.event("q")
        self.assertEqual(self.actions, [("capture", "1")])

    def test_inactive_passes_through_but_pause_survives(self):
        self.enabled = False
        self.assertTrue(self.event("q"))
        self.event("q", "up")
        self.assertEqual(self.actions, [])
        self.event("ctrl")
        self.event("tab")
        self.assertEqual(self.actions, [("pause", "")])
        self.assertEqual(len(self.stops), 1)

    def test_disabled_suppression(self):
        self.hooks.exclusive = False
        self.assertTrue(self.event("q"))
        self.assertEqual(self.actions, [("move", "1")])

    def test_latest_key_while_previous_is_held_and_no_auto_resume(self):
        for exclusive in (True, False):
            with self.subTest(exclusive=exclusive):
                self.hooks.close()
                self.actions.clear()
                self.hooks.configure([('q', 'move', '1'), ('w', 'move', '2')], exclusive)
                self.assertEqual(self.event('q'), not exclusive)
                self.assertEqual(self.event('w'), not exclusive)
                self.assertEqual(self.actions, [('move', '1'), ('move', '2')])
                self.event('w', 'up')
                self.event('q')  # Windows repeat of the still-held first key.
                self.assertEqual(len(self.actions), 2)
                self.event('q', 'up')
                self.event('q')
                self.assertEqual(self.actions[-1], ('move', '1'))
                self.assertEqual(len(self.actions), 3)

    def test_unrelated_key_is_not_swallowed_by_held_shortcut(self):
        self.event('q')
        self.assertTrue(self.event('a'))
        self.assertTrue(self.event('a', 'up'))
        self.assertEqual(self.actions, [('move', '1')])

    def test_modifier_release_does_not_retrigger_held_primary(self):
        self.event('q')
        self.event('ctrl')
        self.event('tab')
        self.event('tab', 'up')
        self.event('ctrl', 'up')
        self.event('q')
        self.assertEqual(self.actions, [('move', '1'), ('pause', '')])


    def physical_event(self, scan, vk, extended=False, kind="down"):
        return self.hooks.handle(SimpleNamespace(scan_code=scan, virtual_key=vk,
                                                is_extended=extended, event_type=kind))

    def test_numbers_do_not_match_navigation_keys_or_numlock_off(self):
        pairs = ((0, 82, 0x2D), (1, 79, 0x23), (2, 80, 0x28),
                 (3, 81, 0x22), (4, 75, 0x25), (5, 76, 0x0C),
                 (6, 77, 0x27), (7, 71, 0x24), (8, 72, 0x26), (9, 73, 0x21))
        for digit, scan, navigation_vk in pairs:
            with self.subTest(digit=digit):
                self.hooks.close()
                self.actions.clear()
                self.hooks.configure([(str(digit), 'move', 'number')], True)
                # Dedicated navigation key and NumLock-off keypad navigation.
                for extended in (True, False):
                    self.assertTrue(self.physical_event(scan, navigation_vk, extended))
                    self.assertTrue(self.physical_event(scan, navigation_vk, extended, 'up'))
                    self.assertEqual(self.actions, [])
                # Both top-row numbers and NumLock-on keypad numbers still work.
                top_scan = keyboard.key_to_scan_codes(str(digit))[0]
                for key_scan, vk in ((top_scan, 0x30 + digit), (scan, 0x60 + digit)):
                    self.assertFalse(self.physical_event(key_scan, vk))
                    self.assertTrue(self.hooks.held(str(digit)))
                    self.assertFalse(self.physical_event(key_scan, vk, kind='up'))
                self.assertEqual(self.actions, [('move', 'number'), ('move', 'number')])

    def test_arrow_binding_does_not_match_numlock_on_keypad_number(self):
        for name, scan, vk, digit in (('left', 75, 0x25, 4), ('right', 77, 0x27, 6),
                                      ('up', 72, 0x26, 8), ('down', 80, 0x28, 2)):
            with self.subTest(name=name):
                self.hooks.close()
                self.actions.clear()
                self.hooks.configure([(name, 'move', name)], True)
                self.assertTrue(self.physical_event(scan, 0x60 + digit))
                self.assertTrue(self.physical_event(scan, 0x60 + digit, kind='up'))
                self.assertEqual(self.actions, [])
                # NumLock-off keypad and dedicated arrow both represent arrows.
                for extended in (False, True):
                    self.assertFalse(self.physical_event(scan, vk, extended))
                    self.assertFalse(self.physical_event(scan, vk, extended, 'up'))
                self.assertEqual(self.actions, [('move', name), ('move', name)])

    def test_keypad_and_arrow_keep_independent_hold_and_suppression(self):
        self.hooks.configure([('4', 'move', 'number'), ('left', 'move', 'arrow')], True)
        self.assertFalse(self.physical_event(75, 0x64))
        self.assertFalse(self.physical_event(75, 0x25, True))
        self.assertEqual(self.actions, [('move', 'number'), ('move', 'arrow')])
        self.assertFalse(self.physical_event(75, 0x25, True))  # auto-repeat
        self.assertEqual(len(self.actions), 2)
        self.assertFalse(self.physical_event(75, 0x64, kind='up'))
        self.assertFalse(self.hooks.held('4'))
        self.assertTrue(self.hooks.held('left'))
        self.assertFalse(self.physical_event(75, 0x25, True, 'up'))
        self.assertEqual(self.hooks.pressed, {})
        self.assertEqual(self.hooks.suppressed, set())

    def test_arrow_release_cannot_rearm_a_held_number(self):
        self.hooks.configure([('4', 'move', 'number')], True)
        self.assertFalse(self.physical_event(75, 0x64))
        self.assertTrue(self.physical_event(75, 0x25, True))
        self.assertTrue(self.physical_event(75, 0x25, True, 'up'))
        self.assertTrue(self.hooks.held('4'))
        self.assertFalse(self.physical_event(75, 0x64))
        self.assertEqual(self.actions, [('move', 'number')])
        self.assertFalse(self.physical_event(75, 0x64, kind='up'))

    def test_changed_numlock_virtual_key_on_release_does_not_leave_key_held(self):
        self.hooks.configure([('4', 'move', 'number')], True)
        self.assertFalse(self.physical_event(75, 0x64))
        self.assertFalse(self.physical_event(75, 0x25, kind='up'))
        self.assertFalse(self.hooks.held('4'))
        self.assertEqual(self.hooks.pressed, {})
        self.assertEqual(self.hooks.suppressed, set())
        self.assertFalse(self.physical_event(75, 0x64))
        self.assertEqual(len(self.actions), 2)

    def test_both_control_keys_survive_releasing_one_side(self):
        self.physical_event(29, 0xA2)
        self.physical_event(29, 0xA3, True)
        self.physical_event(29, 0xA2, kind='up')
        self.assertFalse(self.physical_event(15, 0x09))
        self.assertEqual(self.actions, [('pause', '')])

    def test_pause_numlock_and_printscreen_multiply_do_not_cross_trigger(self):
        pairs = (
            ('pause', (69, 0x13, False), (69, 0x90, True)),
            ('numlock', (69, 0x90, True), (69, 0x13, False)),
            ('print', (55, 0x2C, True), (55, 0x6A, False)),
            ('*', (55, 0x6A, False), (55, 0x2C, True)),
        )
        for key, own, other in pairs:
            with self.subTest(key=key):
                self.hooks.close()
                self.actions.clear()
                self.hooks.configure([(key, 'move', 'special')], True)
                self.assertTrue(self.physical_event(*other))
                self.assertTrue(self.physical_event(*other, kind='up'))
                self.assertEqual(self.actions, [])
                self.assertFalse(self.physical_event(*own))
                self.assertFalse(self.physical_event(*own))
                self.assertEqual(self.actions, [('move', 'special')])
                self.assertFalse(self.physical_event(*own, kind='up'))

    def test_pause_and_numlock_remain_independently_held(self):
        self.hooks.configure([('pause', 'move', 'pause'), ('num lock', 'move', 'lock')], True)
        self.assertFalse(self.physical_event(69, 0x13))
        self.assertFalse(self.physical_event(69, 0x90, True))
        self.assertEqual(self.actions, [('move', 'pause'), ('move', 'lock')])
        self.assertFalse(self.physical_event(69, 0x90, True, 'up'))
        self.assertTrue(self.hooks.held('pause'))
        self.assertFalse(self.hooks.held('num lock'))
        self.assertFalse(self.physical_event(69, 0x13, kind='up'))
        self.assertFalse(self.hooks.pressed)
        self.assertFalse(self.hooks.suppressed)

    def test_special_key_aliases_have_one_canonical_binding(self):
        from domain import hotkey, Profile, Row
        for qt_name, canonical in (('Print', 'print screen'), ('PrintScreen', 'print screen'),
                                    ('NumLock', 'num lock'), ('ScrollLock', 'scroll lock'),
                                    ('CapsLock', 'caps lock')):
            with self.subTest(key=qt_name):
                self.assertEqual(hotkey(qt_name), canonical)
                self.assertEqual(Hotkeys.compile(qt_name).groups, Hotkeys.compile(canonical).groups)
                with self.assertRaises(ValueError):
                    Profile([Row(move_hk=qt_name), Row(move_hk=canonical)]).validate(())
