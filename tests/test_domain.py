import tempfile
from pathlib import Path
import unittest
from domain import Profile, Row, Settings, atomic_json, read_json, hotkey


class DomainTests(unittest.TestCase):
    def test_legacy_target_path_migrates_without_changing_macros(self):
        from domain import target_identity
        data = Profile.default().export()
        data['target_program'] = {'exe': r'C:\Games\1.0\Game.exe', 'class': 'GameWindow', 'title': 'Game'}
        profile = Profile.read(data)
        self.assertEqual(profile.target_program['exe'], 'Game.exe')
        self.assertEqual(profile.export()['macros'], data['macros'])
        self.assertEqual(profile.export()['target_program']['exe'], 'Game.exe')
        self.assertEqual(target_identity({}), {})

    def test_old_profile_and_roundtrip(self):
        old = {"target_resolution": "1920x1080", "macros": [
            {"move_hk": "Q", "save_hk": "SHIFT+Q", "coords": "123, 456", "memo": "스킬", "action_mode": "target_and_return_click"}]}
        profile = Profile.read(old)
        self.assertEqual((profile.rows[0].x, profile.rows[0].y), (123, 456))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "프로필.json"
            atomic_json(path, profile.export())
            self.assertEqual(Profile.read(read_json(path)), profile)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

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
                Profile.read({"macros": [{"coords": coords}]})

    def test_conversion_is_transactional(self):
        p = Profile([Row(x=100, y=200)])
        p.convert((1000, 1000), (2000, 500))
        self.assertEqual((p.rows[0].x, p.rows[0].y), (200, 100))
        before = p.export()
        with self.assertRaises(ValueError):
            p.convert((0, 1), (1, 1))
        self.assertEqual(before, p.export())

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            Profile.read({"macros": [{"action_mode": "unknown"}]})

    def test_removed_completion_delay_is_ignored_in_older_settings(self):
        from dataclasses import asdict
        settings = Settings.read({"after_action_delay": 10000, "click_hold_ms": 75})
        self.assertEqual(settings.click_hold_ms, 75)
        self.assertNotIn("after_action_delay", asdict(settings))

    def test_legacy_timing_names_migrate_without_losing_values(self):
        from dataclasses import asdict
        legacy = {"click_delay": 7, "click_duration": 83, "curve_duration": 19}
        result = asdict(Settings.read(legacy))
        self.assertEqual((result['move_to_click_delay_ms'], result['click_hold_ms'],
                          result['drag_segment_duration_ms']), (7, 83, 19))
        self.assertFalse(set(legacy) & set(result))
        self.assertEqual(Settings.read({**legacy, 'click_hold_ms': 30}).click_hold_ms, 30)
        self.assertEqual(legacy, {"click_delay": 7, "click_duration": 83, "curve_duration": 19})


if __name__ == "__main__":
    unittest.main()
