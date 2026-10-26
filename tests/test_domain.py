import tempfile
from pathlib import Path
import unittest
from domain import Profile, Row, Settings, atomic_json, read_json, hotkey


class DomainTests(unittest.TestCase):
    def test_coordinate_free_scripts_do_not_require_a_saved_basis(self):
        for source in ('', '  \n', '// click 100,200', 'click mouse // 100,200',
                       'sleep 5', 'curve_first_time 5\ncurve_last_time 5\ncurve mouse mouse',
                       'loop 2\nclick mouse\nsleep 5\nloop_end',
                       'mouse_press left\nrelease_actions\nmouse_release left'):
            with self.subTest(source=source):
                profile = Profile([Row(action_mode='script', script=source)])
                self.assertFalse(profile.has_fixed_coordinates())

    def test_fixed_script_coordinates_require_a_saved_basis_in_all_modes(self):
        sources = [f'{command} 0,0' for command in ('click', 'move', 'press', 'release', 'switch')]
        sources += ['curve mouse 0,0', 'CLICK\t-10, 20 // note',
                    'loop 2\nloop 3\nclick 0,0\nloop_end\nloop_end',
                    'click mouse\nrelease_actions\nrelease 0,0']
        for mode in ('script', 'move_only'):
            for source in sources:
                with self.subTest(mode=mode, source=source):
                    self.assertTrue(Profile([Row(action_mode=mode, script=source)]).has_fixed_coordinates())

    def test_retained_table_coordinates_require_a_saved_basis(self):
        self.assertFalse(Profile.default().has_fixed_coordinates())
        for point in ((0, 1), (1, 0), (-1, -1)):
            with self.subTest(point=point):
                row = Row(x=point[0], y=point[1], action_mode='script', script='click mouse')
                self.assertTrue(Profile([row]).has_fixed_coordinates())

    def test_incomplete_coordinate_scripts_preserve_unknown_basis_protection(self):
        for source in ('click 10,', 'curve mouse -4,', 'loop 2\nclick 0,0'):
            with self.subTest(source=source):
                self.assertTrue(Profile([Row(script=source)]).has_fixed_coordinates())

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
        profile = Profile([Row(move_hk='q', x=123, y=456, memo='스킬', action_mode='move_only')], (1920, 1080))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "프로필.json"
            atomic_json(path, profile.export())
            self.assertEqual(Profile.read(read_json(path)), profile)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_missing_and_unknown_profile_formats_are_rejected(self):
        for value in (None, 1, True, {}, [], 'v1', 'ba-macro-profile-v2'):
            data = Profile.default().export()
            data['version'] = 3
            if value is None:
                del data['format']
            else:
                data['format'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'ba-macro-profile-v1'):
                Profile.read(data)

    def test_profile_export_uses_format_without_version(self):
        data = Profile.default().export()
        self.assertEqual(data['format'], 'ba-macro-profile-v1')
        self.assertNotIn('version', data)
        self.assertEqual(Profile.read(data).export(), data)

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
            Settings.read({"capture_hk": p.rows[0].move_hk, "pause_hk": p.rows[1].move_hk})
        with self.assertRaises(ValueError):
            Settings.read({"capture_hk": "q", "pause_hk": "q"})

    def test_bad_values(self):
        for data in ({"click_hold_ms": -1}, {"auto_pause": "yes"},
                     {"capture_hk": "ctrl+s", "pause_hk": "ctrl+s"}, {"drag_segment_duration_ms": 1.5}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Settings.read(data)
        for coords in ("x, y", "1, 2, 3", "100001, 0"):
            with self.subTest(coords=coords), self.assertRaises(ValueError):
                Profile.read({"format": "ba-macro-profile-v1", "macros": [{"coords": coords, "action_mode": "move_only"}]})

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            Profile.read({"format": "ba-macro-profile-v1", "macros": [{"coords": "0, 0", "action_mode": "unknown"}]})

    def test_removed_settings_names_are_rejected(self):
        for name in ('click_delay', 'click_duration', 'curve_duration', 'after_action_delay',
                     'return_delay', 'auto_fire', 'show_realtime', 'warn_app_mismatch',
                     'move_to_click_delay_ms', 'cursor_return_delay_ms', 'return_cursor'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                Settings.read({name: 10})
        from dataclasses import asdict
        values = asdict(Settings(click_hold_ms=83, drag_segment_duration_ms=19))
        self.assertEqual(asdict(Settings.read(values)), values)
        self.assertEqual(Settings.read({}), Settings())

    def test_removed_coordinate_click_mode_is_rejected(self):
        data = Profile.default().export()
        data['macros'][0]['action_mode'] = 'move_and_click'
        with self.assertRaisesRegex(ValueError, '실행 모드'):
            Profile.read(data)



if __name__ == "__main__":
    unittest.main()
