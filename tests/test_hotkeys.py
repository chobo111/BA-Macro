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
