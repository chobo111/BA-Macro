import unittest
from domain import Profile, Row, Settings
from shortcuts import hotkey, qt_hotkey


class ShortcutTests(unittest.TestCase):
    def test_plus_is_distinct_from_empty_and_modifier_separator(self):
        for source, expected in (('', ''), ('+', 'plus'), ('plus', 'plus'),
                                 ('Ctrl++', 'ctrl+plus'), ('Ctrl+plus', 'ctrl+plus'),
                                 ('Control+Shift++', 'ctrl+shift+plus'),
                                 ('Meta++', 'win+plus'), ('Ctrl+,', 'ctrl+,')):
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
                   ('Meta+Q', 'Windows+Q'), ('Ctrl++', 'Control+plus'))
        for first, second in aliases:
            with self.subTest(first=first, second=second):
                self.assertEqual(hotkey(first), hotkey(second))
                with self.assertRaises(ValueError):
                    Profile([Row(move_hk=first), Row(move_hk=second)]).validate()
                with self.assertRaises(ValueError):
                    Profile([Row(move_hk=first)]).validate((second,))
                with self.assertRaises(ValueError):
                    Settings.read({'capture_hk': first, 'pause_hk': second})
                with self.assertRaises(ValueError):
                    Profile.read(Profile([Row(move_hk=first), Row(move_hk=second)]).export())

    def test_canonical_shortcuts_survive_profile_and_settings_serialization(self):
        for value in ('win+q', 'plus', 'ctrl+plus', 'ctrl+page up', 'alt+page down'):
            with self.subTest(value=value):
                profile = Profile([Row(move_hk=value)])
                self.assertEqual(Profile.read(profile.export()).rows[0].move_hk, value)
                self.assertEqual(Settings.read({'pause_hk': value}).pause_hk, value)
