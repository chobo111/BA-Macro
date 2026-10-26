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

    def test_imported_ctrl_alias_never_dispatches_alongside_global_capture(self):
        prepared = Hotkeys.prepare_profile(('ctrl+s', 'ctrl+`'), ('left control', 'right_ctrl+q', 'q'))
        self.assertEqual(prepared.keys, ('', '', 'q'))
        self.assertEqual(set(prepared.issues), {0, 1})
        self.hooks.configure_profile(prepared, ('one', 'two', 'three'), True)
        self.event('ctrl')
        self.event('s')
        self.assertEqual(self.actions, [('capture_selected', '')])

    def test_sided_alias_binding_uses_both_physical_modifiers_without_recompile(self):
        from unittest.mock import patch
        prepared = Hotkeys.prepare_profile(('f8', 'f9'), ('left ctrl',))
        with patch.object(Hotkeys, 'compile', side_effect=AssertionError('must reuse checked binding')):
            self.hooks.configure_profile(prepared, ('row',), True)
        for event in ((29, 0xA2, False), (29, 0xA3, True)):
            self.assertFalse(self.physical_event(*event))
            self.assertTrue(self.hooks.held('right control'))
            self.assertFalse(self.physical_event(*event, kind='up'))
        self.assertEqual(self.actions, [('move', 'row'), ('move', 'row')])

    def test_backend_win_aliases_are_rejected_by_all_registration_paths(self):
        previous = list(self.hooks.bindings)
        for name in ('left windows', 'right_win', 'command', 'cmd'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'Win'):
                    self.hooks.configure([(name, 'pause', '')])
                self.assertEqual(self.hooks.bindings, previous)
                prepared = Hotkeys.prepare_profile(('f8', 'f9'), (name,))
                self.assertEqual(prepared.keys, ('',))
                self.assertIn('Win', prepared.issues[0])

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
        from shortcuts import hotkey
        for qt_name, canonical in (('Print', 'print screen'), ('PrintScreen', 'print screen'),
                                    ('NumLock', 'num lock'), ('ScrollLock', 'scroll lock'),
                                    ('CapsLock', 'caps lock')):
            with self.subTest(key=qt_name):
                self.assertEqual(hotkey(qt_name), canonical)
                self.assertEqual(Hotkeys.compile(qt_name).groups, Hotkeys.compile(canonical).groups)
                with self.assertRaises(ValueError):
                    Hotkeys.validate((qt_name, canonical))

    def test_duplicate_configuration_leaves_existing_bindings_intact(self):
        previous = list(self.hooks.bindings)
        for exclusive in (False, True):
            for first, second in (('ctrl+page up', 'ctrl+pgup'), ('win+q', 'meta+q'), ('+', 'plus')):
                with self.subTest(first=first, exclusive=exclusive), self.assertRaises(ValueError):
                    self.hooks.configure([(first, 'move', '1'), (second, 'move', '2')], exclusive)
                self.assertEqual(self.hooks.bindings, previous)

    def test_pageup_alias_uses_same_hold_and_release_identity(self):
        self.hooks.configure([('ctrl+pgup', 'move', '1')], True)
        self.physical_event(29, 0xA2)
        self.assertFalse(self.physical_event(73, 0x21, True))
        self.assertTrue(self.hooks.held('ctrl+page up'))
        self.assertTrue(self.hooks.held('ctrl+pgup'))
        self.assertEqual(self.actions, [('move', '1')])
        self.assertFalse(self.physical_event(73, 0x21, True, 'up'))
        self.assertFalse(self.hooks.held('ctrl+pgup'))

    def test_windows_combinations_are_rejected_without_replacing_bindings(self):
        previous = list(self.hooks.bindings)
        for key in ('win+q','meta+q','windows+q'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Win'):
                self.hooks.configure([(key,'move','1')],True)
            self.assertEqual(self.hooks.bindings,previous)

    def test_keypad_plus_and_ctrl_plus_do_not_match_unshifted_equals(self):
        from unittest.mock import patch
        with patch('keyboard_hook.character_key', return_value=(0xBB, frozenset(('shift',)))):
            for shortcut in ('+', 'ctrl++'):
                with self.subTest(shortcut=shortcut):
                    self.hooks.close()
                    self.actions.clear()
                    self.hooks.configure([(shortcut, 'move', '1')], True)
                    if shortcut.startswith('ctrl'):
                        self.physical_event(29, 0xA2)
                    self.assertTrue(self.physical_event(13, 0xBB))
                    self.assertTrue(self.physical_event(13, 0xBB, kind='up'))
                    self.assertEqual(self.actions, [])
                    self.assertFalse(self.physical_event(78, 0x6B))
                    self.assertFalse(self.physical_event(78, 0x6B))
                    self.assertTrue(self.hooks.held(shortcut))
                    self.assertEqual(self.actions, [('move', '1')])
                    self.assertFalse(self.physical_event(78, 0x6B, kind='up'))

    def test_shifted_main_keyboard_plus_dispatches_once(self):
        from unittest.mock import patch
        with patch('keyboard_hook.character_key', return_value=(0xBB, frozenset(('shift',)))):
            self.hooks.configure([('ctrl+shift++', 'move', '1')], True)
        self.physical_event(29, 0xA2)
        self.physical_event(42, 0xA0)
        self.assertFalse(self.physical_event(13, 0xBB))
        self.assertFalse(self.physical_event(13, 0xBB))
        self.assertEqual(self.actions, [('move', '1')])
        self.assertFalse(self.physical_event(13, 0xBB, kind='up'))

    def test_main_plus_without_shift_is_supported_when_layout_allows_it(self):
        from unittest.mock import patch
        with patch('keyboard_hook.character_key', return_value=(0xBB, frozenset())):
            self.hooks.configure([('+', 'move', '1')], True)
        self.assertFalse(self.physical_event(13, 0xBB))
        self.assertEqual(self.actions, [('move', '1')])

    def test_tab_caps_lock_and_standalone_control_fire_once_and_release(self):
        for shortcut,event in (('tab',(15,0x09,False)),('caps lock',(58,0x14,False)),
                               ('ctrl',(29,0xA2,False)),('ctrl',(29,0xA3,True))):
            with self.subTest(shortcut=shortcut,event=event):
                self.hooks.close()
                self.actions.clear()
                self.hooks.configure([(shortcut,'move','row')],True)
                self.assertFalse(self.physical_event(*event))
                self.assertFalse(self.physical_event(*event))
                self.assertEqual(self.actions,[('move','row')])
                self.assertTrue(self.hooks.held(shortcut))
                self.assertFalse(self.physical_event(*event,kind='up'))
                self.assertFalse(self.hooks.held(shortcut))
                self.assertFalse(self.physical_event(*event))
                self.assertEqual(len(self.actions),2)
                self.physical_event(*event,kind='up')

    def test_modifier_prefix_conflict_keeps_active_bindings_and_suppression(self):
        previous = list(self.hooks.bindings)
        for modifier in ('ctrl','alt','shift'):
            for keys in ((modifier,modifier+'+q'),(modifier+'+q',modifier)):
                with self.subTest(keys=keys), self.assertRaisesRegex(ValueError,'단독'):
                    self.hooks.configure([(key,'move',str(i)) for i,key in enumerate(keys)],False)
                self.assertEqual(self.hooks.bindings,previous)
                self.assertTrue(self.hooks.exclusive)

    def test_standalone_modifiers_start_on_press_hold_until_release(self):
        for modifier in ('ctrl','alt','shift'):
            with self.subTest(modifier=modifier):
                self.hooks.close()
                self.actions.clear()
                self.hooks.configure([(modifier,'move','script')],True)
                self.assertFalse(self.event(modifier))
                self.assertEqual(self.actions,[('move','script')])
                self.assertTrue(self.hooks.held(modifier))
                self.assertFalse(self.event(modifier))
                self.assertEqual(len(self.actions),1)
                self.assertFalse(self.event(modifier,'up'))
                self.assertFalse(self.hooks.held(modifier))
                self.assertFalse(self.event(modifier))
                self.assertEqual(len(self.actions),2)

    def test_combination_waits_for_primary_key(self):
        for modifier in ('ctrl','alt','shift'):
            with self.subTest(modifier=modifier):
                self.hooks.close()
                self.actions.clear()
                self.hooks.configure([(modifier+'+q','move','script')],True)
                self.event(modifier)
                self.assertEqual(self.actions,[])
                self.assertFalse(self.hooks.held(modifier+'+q'))
                self.event('q')
                self.assertEqual(self.actions,[('move','script')])
                self.assertTrue(self.hooks.held(modifier+'+q'))
                self.event('q','up')
                self.assertFalse(self.hooks.held(modifier+'+q'))

    def test_period_rejects_delete_but_accepts_both_decimal_keys(self):
        from unittest.mock import patch
        with patch('keyboard_hook.character_key', return_value=(0xBE, frozenset())):
            self.hooks.configure([('.', 'move', 'dot')], True)
        for extended in (False, True):
            self.assertTrue(self.physical_event(83, 0x2E, extended))
            self.assertTrue(self.physical_event(83, 0x2E, extended, 'up'))
        self.assertEqual(self.actions, [])
        for scan, vk in ((52, 0xBE), (83, 0x6E)):
            self.assertFalse(self.physical_event(scan, vk))
            self.assertFalse(self.physical_event(scan, vk))
            self.assertTrue(self.hooks.held('.'))
            self.assertFalse(self.physical_event(scan, vk, kind='up'))
            self.assertFalse(self.hooks.held('.'))
        self.assertEqual(self.actions, [('move', 'dot'), ('move', 'dot')])

    def test_multiply_does_not_fire_on_unshifted_eight(self):
        from unittest.mock import patch
        for shortcut in ('*', 'ctrl+*'):
            self.hooks.close()
            self.actions.clear()
            with patch('keyboard_hook.character_key', return_value=(0x38, frozenset(('shift',)))):
                self.hooks.configure([(shortcut, 'move', 'multiply')], True)
            if shortcut.startswith('ctrl'):
                self.physical_event(29, 0xA2)
            self.assertTrue(self.physical_event(9, 0x38))
            self.assertTrue(self.physical_event(9, 0x38, kind='up'))
            self.assertTrue(self.physical_event(55, 0x2C, True))
            self.assertTrue(self.physical_event(55, 0x2C, True, 'up'))
            self.assertEqual(self.actions, [])
            self.assertFalse(self.physical_event(55, 0x6A))
            self.assertFalse(self.physical_event(55, 0x6A, kind='up'))
            self.assertEqual(self.actions, [('move', 'multiply')])

    def test_shifted_main_keyboard_multiply_and_repeat(self):
        from unittest.mock import patch
        with patch('keyboard_hook.character_key', return_value=(0x38, frozenset(('shift',)))):
            self.hooks.configure([('shift+*', 'move', 'multiply')], True)
        self.physical_event(42, 0xA0)
        self.assertFalse(self.physical_event(9, 0x38))
        self.assertFalse(self.physical_event(9, 0x38))
        self.assertEqual(self.actions, [('move', 'multiply')])
        self.assertFalse(self.physical_event(9, 0x38, kind='up'))
        self.assertFalse(self.hooks.held('shift+*'))

    def test_physical_alias_conflicts_preserve_live_bindings_and_held_state(self):
        from shortcuts import ShortcutError
        pairs = (('shift+-', 'shift+_'), ('shift+/', 'shift+?'),
                 ('shift+=', 'shift++'), ('shift+8', 'shift+*'),
                 ('ctrl+shift+-', 'ctrl+shift+_'))
        self.event('q')
        previous = (list(self.hooks.bindings), dict(self.hooks.pressed),
                    set(self.hooks.fired), set(self.hooks.suppressed), self.hooks.exclusive)
        for first, second in pairs:
            for keys in ((first, second), (second, first)):
                for exclusive in (False, True):
                    with self.subTest(keys=keys, exclusive=exclusive):
                        with self.assertRaisesRegex(ShortcutError, '실제 입력이 중복'):
                            self.hooks.configure([(keys[0], 'pause', ''),
                                                  (keys[1], 'move', 'row')], exclusive)
                        self.assertEqual((self.hooks.bindings, self.hooks.pressed,
                                          self.hooks.fired, self.hooks.suppressed,
                                          self.hooks.exclusive), previous)
        self.assertFalse(self.event('q'))
        self.assertEqual(self.actions, [('move', '1')])
        self.assertFalse(self.event('q', 'up'))

    def test_physical_validation_allows_distinct_virtual_keys_and_modifiers(self):
        keys = ('.', 'delete', '4', 'left', '*', '8', 'print screen',
                'pause', 'num lock', 'q', 'shift+q', 'ctrl+q', 'alt+q',
                'shift+-', 'ctrl+shift+_')
        Hotkeys.validate(iter(keys))
        self.hooks.configure([(key, 'move', str(index)) for index, key in enumerate(keys)])
        self.assertEqual(len(self.hooks.bindings), len(keys))

    def test_shift_symbol_works_after_rejecting_conflicting_registration(self):
        from shortcuts import ShortcutError
        self.hooks.configure([('shift+_', 'move', 'underscore')], True)
        with self.assertRaises(ShortcutError):
            Hotkeys.validate(('shift+-', 'shift+_'))
        self.physical_event(42, 0xA0)
        self.assertFalse(self.physical_event(12, 0xBD))
        self.assertTrue(self.hooks.held('shift+_'))
        self.assertFalse(self.physical_event(12, 0xBD))
        self.assertEqual(self.actions, [('move', 'underscore')])
        self.assertFalse(self.physical_event(12, 0xBD, kind='up'))
        self.assertFalse(self.hooks.held('shift+_'))

    def test_keypad_plus_and_equals_register_and_dispatch_independently(self):
        for prefix in ('', 'ctrl+'):
            for exclusive in (False, True):
                with self.subTest(prefix=prefix, exclusive=exclusive):
                    self.hooks.close()
                    self.actions.clear()
                    plus, equals = prefix+'plus', prefix+'='
                    self.hooks.configure([(plus, 'move', 'plus'), (equals, 'move', 'equals')], exclusive)
                    if prefix:
                        self.physical_event(29, 0xA2)
                    self.assertEqual(self.physical_event(78, 0x6B), not exclusive)
                    self.assertEqual(self.physical_event(13, 0xBB), not exclusive)
                    self.assertEqual(self.actions, [('move', 'plus'), ('move', 'equals')])
                    self.assertTrue(self.hooks.held(plus))
                    self.assertTrue(self.hooks.held(equals))
                    self.physical_event(78, 0x6B, kind='up')
                    self.assertFalse(self.hooks.held(plus))
                    self.assertTrue(self.hooks.held(equals))
                    self.physical_event(13, 0xBB, kind='up')
                    self.assertFalse(self.hooks.held(equals))

    def test_symbol_mapping_keeps_scan_and_virtual_key_paired(self):
        from hotkeys import PressedKey
        group = Hotkeys.compile('shift+plus').groups[-1]
        for scan, vk, expected in ((13, 0xBB, True), (78, 0x6B, True),
                                   (13, 0x6B, False), (78, 0xBB, False)):
            with self.subTest(scan=scan, vk=vk):
                self.assertEqual(group.matches(PressedKey(scan, vk, False)), expected)
        with self.assertRaisesRegex(ValueError, '중복'):
            Hotkeys.validate(('shift+plus', 'shift+='))

    def test_unqualified_shift_symbols_cannot_replace_active_bindings(self):
        from unittest.mock import patch
        from keyboard_hook import character_key
        mappings = {'_': (0xBD, frozenset(('shift',))),
                    '!': (0x31, frozenset(('shift',))),
                    '?': (0xBF, frozenset(('shift',)))}
        previous = list(self.hooks.bindings)
        with patch('keyboard_hook.character_key',
                   side_effect=lambda char: mappings[char] if char in mappings else character_key(char)):
            for symbol in mappings:
                with self.subTest(symbol=symbol), self.assertRaisesRegex(ValueError, 'Shift'):
                    self.hooks.configure([(symbol, 'move', 'invalid')], True)
                self.assertEqual(self.hooks.bindings, previous)

    def test_imported_shift_symbols_are_quarantined_without_suppressing_base_keys(self):
        from unittest.mock import patch
        from keyboard_hook import character_key
        mappings = {'_': (0xBD, frozenset(('shift',))),
                    '!': (0x31, frozenset(('shift',))),
                    '?': (0xBF, frozenset(('shift',)))}
        with patch('keyboard_hook.character_key',
                   side_effect=lambda char: mappings[char] if char in mappings else character_key(char)):
            prepared = Hotkeys.prepare_profile(('ctrl+s', 'ctrl+f9'), ('_', '!', '?', 'q'))
        self.assertEqual(prepared.keys, ('', '', '', 'q'))
        self.assertEqual(set(prepared.issues), {0, 1, 2})
        self.hooks.configure_profile(prepared, ('underscore', 'exclamation', 'question', 'valid'), True)
        for scan, vk in ((12, 0xBD), (2, 0x31), (53, 0xBF)):
            self.assertTrue(self.physical_event(scan, vk))
            self.assertTrue(self.physical_event(scan, vk, kind='up'))
        self.assertEqual(self.actions, [])
        self.event('q')
        self.assertEqual(self.actions, [('move', 'valid')])

    def test_qualified_shift_symbols_and_their_plain_keys_remain_distinct(self):
        from unittest.mock import patch
        from keyboard_hook import character_key
        for symbol, plain, scan, vk in (('_', '-', 12, 0xBD), ('!', '1', 2, 0x31), ('?', '/', 53, 0xBF)):
            with self.subTest(symbol=symbol):
                self.hooks.close()
                self.actions.clear()
                with patch('keyboard_hook.character_key', side_effect=lambda char:
                           (vk, frozenset(('shift',))) if char == symbol else character_key(char)):
                    self.hooks.configure([('shift+' + symbol, 'move', 'shifted'),
                                          (plain, 'move', 'plain')], True)
                self.assertFalse(self.physical_event(scan, vk))
                self.assertFalse(self.physical_event(scan, vk, kind='up'))
                self.physical_event(42, 0xA0)
                self.assertFalse(self.physical_event(scan, vk))
                self.assertTrue(self.hooks.held('shift+' + symbol))
                self.assertFalse(self.physical_event(scan, vk, kind='up'))
                self.physical_event(42, 0xA0, kind='up')
                self.assertEqual(self.actions, [('move', 'plain'), ('move', 'shifted')])

    def test_character_mapping_requires_every_layout_modifier(self):
        from unittest.mock import patch
        from keyboard_hook import character_key
        with patch('keyboard_hook.character_key', side_effect=lambda char:
                   (0x51, frozenset(('ctrl', 'alt'))) if char == '@' else character_key(char)):
            for shortcut in ('@', 'alt+@', 'ctrl+@'):
                with self.subTest(shortcut=shortcut), self.assertRaisesRegex(ValueError, 'Ctrl\\+Alt'):
                    Hotkeys.validate((shortcut,))
            Hotkeys.validate(('ctrl+alt+@',))

    def test_unmapped_symbol_keeps_existing_bindings(self):
        from unittest.mock import patch
        previous = list(self.hooks.bindings)
        with patch('keyboard_hook.U.MapVirtualKeyW', return_value=0):
            with self.assertRaises(ValueError):
                self.hooks.configure([('plus', 'move', 'plus')])
        self.assertEqual(self.hooks.bindings, previous)

    def test_failed_start_keeps_ownership_until_cleanup(self):
        from unittest.mock import Mock, patch
        hook = Mock()
        def fail():
            self.assertEqual(self.hooks.unhook, hook.close)
            raise RuntimeError('startup timed out')
        hook.start.side_effect = fail
        with patch('keyboard_hook.PhysicalHook', return_value=hook):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                self.hooks.start()
        hook.close.assert_called_once_with()
        self.assertIsNone(self.hooks.unhook)

    def test_runtime_hook_failure_stops_input_and_reports_error(self):
        from unittest.mock import Mock, patch
        errors = []
        self.hooks.on_error = errors.append
        self.event('q')
        hook = Mock()
        with patch('keyboard_hook.PhysicalHook', return_value=hook) as factory:
            self.hooks.start()
            callback = factory.call_args.args[1]
            callback(OSError('queue unavailable'))
        self.assertIsNone(self.hooks.unhook)
        self.assertFalse(self.hooks.held('q'))
        self.assertEqual(self.stops, [True])
        self.assertIn('단축키 입력 감시 실패', errors[0])
        hook.close.assert_called_once_with()

    def test_old_hook_error_does_not_stop_replacement(self):
        from unittest.mock import Mock, patch
        first, second = Mock(), Mock()
        with patch('keyboard_hook.PhysicalHook', side_effect=[first, second]) as factory:
            self.hooks.start()
            old_callback = factory.call_args.args[1]
            self.hooks.close()
            self.hooks.start()
            old_callback(OSError('old error'))
            self.assertEqual(self.hooks.unhook, second.close)
            second.close.assert_not_called()
            self.assertEqual(self.stops, [])
            self.hooks.close()
