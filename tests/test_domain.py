import tempfile
from pathlib import Path
import unittest
from domain import Profile, Row, Settings, atomic_json, read_json, hotkey


class DomainTests(unittest.TestCase):
    def test_target_identity_accepts_only_filename(self):
        from domain import target_identity
        for exe in (r'C:\Games\1.0\Game.exe', 'C:/Games/Game.exe', 'C:Game.exe'):
            data = Profile.default().export()
            data['target_program'] = {'exe': exe, 'class': 'GameWindow', 'title': 'Game'}
            with self.subTest(exe=exe), self.assertRaisesRegex(ValueError, '실행파일명'):
                Profile.read(data)
        self.assertEqual(target_identity({'exe': 'Game.exe'})['exe'], 'Game.exe')
        self.assertEqual(target_identity({}), {})

    def test_invalid_resolution_is_rejected_instead_of_erasing_the_basis(self):
        for size in ('1920X1080', '1920×1080', 'bad', '', '0x1080', '1920x',
                     '1920x1080x2', '100001x1080', None, 123, True, [1920,1080]):
            data = Profile.default().export()
            data['target_resolution'] = size
            with self.subTest(size=size), self.assertRaises(ValueError):
                Profile.read(data)

    def test_explicit_unknown_and_valid_resolution_are_preserved(self):
        for size, expected in (('알 수 없음', None), ('1920x1080', (1920,1080)), ('1x1', (1,1))):
            data = Profile.default().export()
            data['target_resolution'] = size
            profile = Profile.read(data)
            self.assertEqual(profile.resolution, expected)
            self.assertEqual(profile.export()['target_resolution'], size)

    def test_current_profile_roundtrip(self):
        profile = Profile([Row(move_hk='q', x=123, y=456, memo='스킬', action_mode='move_and_click')], (1920, 1080))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "프로필.json"
            atomic_json(path, profile.export())
            self.assertEqual(Profile.read(read_json(path)), profile)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_missing_old_and_unknown_profile_versions_are_rejected(self):
        for version in (None, 1, 2, 4, '3', 3.0, True):
            data = Profile.default().export()
            if version is None:
                del data['version']
            else:
                data['version'] = version
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'version: 3'):
                Profile.read(data)

    def test_legacy_row_formats_are_rejected(self):
        for mode in ('default', 'target_only', 'target_and_return_click'):
            data = Profile.default().export()
            data['macros'][0]['action_mode'] = mode
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, '실행 모드'):
                Profile.read(data)
        for values in ({'x': 123, 'y': 456}, {}, {'coords': [123, 456]}):
            data = Profile.default().export()
            del data['macros'][0]['coords']
            data['macros'][0].update(values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                Profile.read(data)
        data = Profile.default().export()
        data['macros'][0]['save_hk'] = 'shift+q'
        with self.assertRaisesRegex(ValueError, 'save_hk'):
            Profile.read(data)

    def test_duplicates_include_global_keys_and_modifier_order(self):
        self.assertEqual(hotkey("Shift+Control+Q"), "ctrl+shift+q")
        p = Profile([Row(move_hk="ctrl+shift+q"), Row(move_hk="shift+ctrl+q")])
        with self.assertRaises(ValueError):
            p.validate()
        with self.assertRaises(ValueError):
            Profile.default().validate(["q"])

    def test_bad_values(self):
        for data in ({"click_hold_ms": -1}, {"auto_pause": "yes"},
                     {"capture_hk": "ctrl+s", "pause_hk": "ctrl+s"}, {"move_to_click_delay_ms": 1.5}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Settings.read(data)
        for coords in ("x, y", "1, 2, 3", "100001, 0"):
            with self.subTest(coords=coords), self.assertRaises(ValueError):
                Profile.read({"version": 3, "macros": [{"coords": coords, "action_mode": "move_only"}]})

    def test_conversion_is_transactional(self):
        p = Profile([Row(x=100, y=200)])
        p.convert((1000, 1000), (2000, 2000))
        self.assertEqual((p.rows[0].x, p.rows[0].y), (200, 400))
        before = p.export()
        with self.assertRaises(ValueError):
            p.convert((0, 1), (1, 1))
        self.assertEqual(before, p.export())

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            Profile.read({"version": 3, "macros": [{"coords": "0, 0", "action_mode": "unknown"}]})

    def test_removed_settings_names_are_rejected(self):
        for name in ('click_delay', 'click_duration', 'curve_duration', 'after_action_delay',
                     'return_delay', 'auto_fire', 'show_realtime'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                Settings.read({name: 10})
        from dataclasses import asdict
        values = asdict(Settings(move_to_click_delay_ms=7, click_hold_ms=83, drag_segment_duration_ms=19))
        self.assertEqual(asdict(Settings.read(values)), values)
        self.assertEqual(Settings.read({}), Settings())

    def test_cursor_return_delay_default_and_range(self):
        self.assertEqual(Settings.read({}).cursor_return_delay_ms, 50)
        self.assertEqual(Settings.read({'cursor_return_delay_ms': 0}).cursor_return_delay_ms, 0)
        self.assertEqual(Settings.read({'cursor_return_delay_ms': 99999}).cursor_return_delay_ms, 99999)
        for value in (-1, 100000, 1.5):
            with self.assertRaises(ValueError):
                Settings.read({'cursor_return_delay_ms': value})


if __name__ == "__main__":
    unittest.main()
