import unittest
from domain import Profile, Row, Settings
from shortcuts import hotkey, qt_hotkey, validate_hotkeys, row_hotkey


class ShortcutTests(unittest.TestCase):
    def test_backend_modifier_aliases_share_policy_and_round_trip(self):
        aliases = {'left control': 'ctrl', 'right_ctrl': 'ctrl',
                   'left alt': 'alt', 'right_alt': 'alt', 'option': 'alt',
                   'left shift': 'shift', 'right_shift': 'shift',
                   'left windows': 'win', 'right_win': 'win',
                   'command': 'win', 'cmd': 'win'}
        for source, expected in aliases.items():
            with self.subTest(source=source):
                self.assertEqual(hotkey(source), expected)
                self.assertEqual(hotkey(qt_hotkey(source)), expected)
                loaded = Profile.read(Profile([Row(move_hk=source)]).export())
                self.assertEqual(loaded.rows[0].move_hk, expected)
                with self.assertRaisesRegex(ValueError, '중복'):
                    validate_hotkeys((source, expected))
                if expected == 'win':
                    with self.assertRaisesRegex(ValueError, 'Win'):
                        row_hotkey(source)
                    with self.assertRaises(ValueError):
                        hotkey(source + '+q')
                elif expected == 'ctrl':
                    with self.assertRaisesRegex(ValueError, 'Shift와 Alt'):
                        row_hotkey(source + '+q')
                else:
                    self.assertEqual(row_hotkey(source + '+q'), expected + '+q')

    def test_sided_aliases_cannot_hide_modifier_prefix_collisions(self):
        for modifier in ('ctrl', 'alt', 'shift'):
            for keys in ((f'left {modifier}', f'right {modifier}+q'),
                         (f'right {modifier}+q', f'left {modifier}')):
                with self.subTest(keys=keys), self.assertRaisesRegex(ValueError, '단독'):
                    validate_hotkeys(keys)
        with self.assertRaisesRegex(ValueError, '같은 키'):
            hotkey('left ctrl+right ctrl+q')

    def test_plus_is_distinct_from_empty_and_modifier_separator(self):
        for source, expected in (('', ''), ('+', 'plus'), ('plus', 'plus'),
                                 ('Ctrl++', 'ctrl+plus'), ('Ctrl+plus', 'ctrl+plus'),
                                 ('Control+Shift++', 'ctrl+shift+plus'),
                                 ('Ctrl+,', 'ctrl+,')):
            with self.subTest(source=source):
                self.assertEqual(hotkey(source), expected)
                self.assertEqual(hotkey(qt_hotkey(expected)), expected)

    def test_malformed_chords_are_rejected_instead_of_cleared(self):
        for value in ('ctrl+', '+q', 'ctrl+++', 'ctrl++q', 'ctrl+ctrl+q', 'q+w'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                hotkey(value)

    def test_alias_duplicates_are_rejected_for_rows_and_global_shortcuts(self):
        aliases = (('Page Up', 'PgUp'), ('PageDown', 'PgDown'), ('PgDn', 'Page Down'),
                   ('Ins', 'Insert'), ('Del', 'Delete'), ('Spacebar', 'Space'),
                   ('Ctrl++', 'Control+plus'))
        for first, second in aliases:
            with self.subTest(first=first, second=second):
                self.assertEqual(hotkey(first), hotkey(second))
                with self.assertRaises(ValueError):
                    validate_hotkeys((first, second))
                with self.assertRaises(ValueError):
                    validate_hotkeys((second, first))
                with self.assertRaises(ValueError):
                    Settings.read({'capture_hk': first, 'pause_hk': second})
                loaded = Profile.read(Profile([Row(move_hk=first), Row(move_hk=second)]).export())
                self.assertEqual([r.move_hk for r in loaded.rows], [hotkey(first), hotkey(second)])

    def test_canonical_shortcuts_survive_profile_and_settings_serialization(self):
        for value in ('plus', 'ctrl+plus', 'ctrl+page up', 'alt+page down', 'tab', 'caps lock', 'ctrl', 'alt', 'shift', 'win'):
            with self.subTest(value=value):
                profile = Profile([Row(move_hk=value)])
                self.assertEqual(Profile.read(profile.export()).rows[0].move_hk, value)
                self.assertEqual(Settings.read({'pause_hk': value, 'capture_hk': 'f12'}).pause_hk, value)

    def test_standalone_modifiers_round_trip_and_reject_alias_duplicates(self):
        for canonical in ('ctrl','alt','shift','win'):
            self.assertEqual(hotkey(qt_hotkey(canonical)),canonical)
        for first,second in (('Control','ctrl'),('Meta','win'),('CapsLock','caps lock')):
            with self.assertRaises(ValueError):
                validate_hotkeys((first, second))
        with self.assertRaises(ValueError):
            hotkey('ctrl+shift')

    def test_only_ctrl_alt_shift_can_prefix_a_chord(self):
        for key in ('win+q', 'Meta++', 'Windows+Tab', 'ctrl+win+x', 'tab+q', 'space+q', 'q+w'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                hotkey(key)
        for key in ('ctrl+q', 'alt+q', 'shift+q', 'ctrl+alt+shift+q', 'space', 'tab'):
            self.assertEqual(hotkey(key), key)

    def test_standalone_modifier_conflicts_in_either_order_and_reserved_keys(self):
        for modifier in ('ctrl', 'alt', 'shift'):
            for chord in (modifier+'+q', 'ctrl+alt+shift+q'):
                for keys in ((modifier,chord), (chord,modifier)):
                    with self.subTest(keys=keys):
                        with self.assertRaisesRegex(ValueError, '단독'):
                            validate_hotkeys(keys)
                        with self.assertRaisesRegex(ValueError, '단독'):
                            validate_hotkeys(reversed(keys))
                        with self.assertRaisesRegex(ValueError, '단독'):
                            Settings.read({'pause_hk':keys[0], 'capture_hk':keys[1]})
        validate_hotkeys(('ctrl', 'alt+q', 'shift+q'))
        with self.assertRaisesRegex(ValueError, 'Shift와 Alt'):
            row_hotkey('ctrl+q')
