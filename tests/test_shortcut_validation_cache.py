"""Shortcut validation reuse must not delay saves or conceal new conflicts."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from app import MainWindow
from domain import Profile, read_json
from hotkeys import Hotkeys
from shortcuts import ShortcutError


class ShortcutValidationCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.layout_patch = patch('app.current_keyboard_layout', return_value=1)
        self.layout = self.layout_patch.start()
        self.addCleanup(self.layout_patch.stop)
        self.w = MainWindow(data_dir=self.directory.name, hook_enabled=False)
        self.w.timer.stop()

    def tearDown(self):
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.directory.cleanup()

    def test_memo_and_coordinate_changes_save_immediately_without_revalidation(self):
        w = self.w
        w.validate_profile_shortcuts(w.profile)
        with patch.object(Hotkeys, 'prepare_profile', wraps=Hotkeys.prepare_profile) as validate, \
                patch.object(w.store, 'save', wraps=w.store.save) as save:
            w.live_memo_changed(0, 'one')
            self.assertEqual(save.call_count, 1)
            w.flush_pending_saves()
            self.assertEqual(read_json(w.path)['macros'][0]['memo'], 'one')
            w.live_memo_changed(0, 'two')
            w.set_value(w.profile.rows[0].id, 'x', 123)
            self.assertEqual(save.call_count, 3)
            w.flush_pending_saves()
            row = read_json(w.path)['macros'][0]
            self.assertEqual((row['memo'], row['coords']), ('two', '123, 0'))
            validate.assert_not_called()

    def test_row_conflict_is_cached_without_blocking_saves(self):
        w = self.w
        w.validate_profile_shortcuts(w.profile)
        w.profile.rows[1].move_hk = 'q'
        w.profile.rows[0].memo = 'pending'
        with patch.object(Hotkeys, 'prepare_profile', wraps=Hotkeys.prepare_profile) as validate:
            for _ in range(2):
                self.assertTrue(w.save(automatic=True))
                w.flush_pending_saves()
                w.rebind()
                self.assertFalse(w.dirty)
                self.assertEqual(w.validate_profile_shortcuts(w.profile).keys, ('', '', 'e'))
            self.assertEqual(validate.call_count, 1)
        w.profile.rows[1].move_hk = 'w'
        self.assertTrue(w.save(automatic=True))
        w.flush_pending_saves()
        self.assertEqual(read_json(w.path)['macros'][0]['memo'], 'pending')
        self.assertFalse(w.dirty)

    def test_both_global_shortcuts_and_candidate_settings_are_part_of_validation(self):
        w = self.w
        for field in ('capture_hk', 'pause_hk'):
            with self.subTest(field=field):
                w.validate_profile_shortcuts(w.profile)
                valid = replace(w.settings, **{field: 'f5'})
                w.validate_profile_shortcuts(w.profile, valid)
                conflicting = replace(valid, **{field: 'q'})
                with self.assertRaises(ShortcutError):
                    w.validate_profile_shortcuts(w.profile, conflicting)
                w.validate_profile_shortcuts(w.profile)

    def test_settings_draft_does_not_evict_current_validation_or_recompile_rows(self):
        w = self.w
        candidate = replace(w.settings, pause_hk='f9')
        current = w.validate_profile_shortcuts(w.profile)
        draft = w.validate_profile_shortcuts(w.profile, candidate)
        with patch.object(Hotkeys, 'prepare_profile', wraps=Hotkeys.prepare_profile) as prepare, \
                patch.object(Hotkeys, 'compile', wraps=Hotkeys.compile) as compile_key:
            for _ in range(3):
                self.assertIs(w.validate_profile_shortcuts(w.profile, candidate), draft)
                self.assertIs(w.validate_profile_shortcuts(w.profile), current)
                w.rebind()
            prepare.assert_not_called()
            self.assertEqual(compile_key.call_count, 6)  # only two proposed global keys per check
        for number in range(5, 13):
            w.validate_profile_shortcuts(w.profile, replace(w.settings, pause_hk=f'f{number}'))
            self.assertLessEqual(len(w._shortcut_cache), 2)

    def test_replacing_profile_and_mutating_keys_rechecks_conflicts(self):
        w = self.w
        w.validate_profile_shortcuts(w.profile)
        other = Profile.default()
        with patch.object(Hotkeys, 'prepare_profile', wraps=Hotkeys.prepare_profile) as validate:
            w.validate_profile_shortcuts(other)
            validate.assert_not_called()
            other.rows[2].move_hk = 'ctrl+s'
            prepared = w.validate_profile_shortcuts(other)
            self.assertEqual(prepared.keys, ('q', 'w', ''))
            self.assertEqual(set(prepared.issues), {2})
            validate.assert_called_once()

    def test_layout_change_rechecks_physical_symbol_collisions(self):
        from keyboard_hook import character_key
        w = self.w
        w.profile.rows[0].move_hk = 'plus'
        w.profile.rows[1].move_hk = '='
        required = frozenset(('shift',))
        with patch('keyboard_hook.character_key', side_effect=lambda char:
                   (0xBB, required) if char == '+' else character_key(char)):
            w.validate_profile_shortcuts(w.profile)
            self.layout.return_value = 2
            required = frozenset()
            prepared = w.validate_profile_shortcuts(w.profile)
            self.assertEqual(prepared.keys, ('', '', 'e'))
            self.assertIn('실제 입력', prepared.issues[0])

    def test_ctrl_row_is_saved_but_not_registered_until_repaired(self):
        w = self.w
        w.profile.rows[0].move_hk = 'ctrl+q'
        for _ in range(2):
            self.assertTrue(w.save(automatic=True))
            w.flush_pending_saves()
            w.rebind()
            self.assertFalse(w.dirty)
            self.assertEqual(read_json(w.path)['macros'][0]['move_hk'], 'ctrl+q')
            self.assertNotIn('ctrl+q', [b.key for b in w.hooks.bindings])
        w.profile.rows[0].move_hk = 'alt+q'
        self.assertTrue(w.save(automatic=True))
        w.flush_pending_saves()
        w.rebind()
        self.assertFalse(w.dirty)
        self.assertEqual((w.settings.capture_hk, w.settings.pause_hk), ('ctrl+s', 'ctrl+`'))

    def test_rebind_reuses_matchers_but_uses_new_profile_row_ids(self):
        w = self.w
        previous_ids = {row.id for row in w.profile.rows}
        w.profile = Profile.default()
        with patch.object(Hotkeys, 'compile', wraps=Hotkeys.compile) as compile_key:
            w.rebind()
            w.rebind()
            compile_key.assert_not_called()
        ids = {binding.row_id for binding in w.hooks.bindings if binding.action == 'move'}
        self.assertEqual(ids, {row.id for row in w.profile.rows})
        self.assertFalse(ids & previous_ids)

    def test_layout_change_rebuilds_prepared_matchers_once(self):
        w = self.w
        self.layout.return_value = 2
        with patch.object(Hotkeys, 'compile', wraps=Hotkeys.compile) as compile_key:
            w.rebind()
            self.assertEqual(compile_key.call_count, 5)
            w.rebind()
            self.assertEqual(compile_key.call_count, 5)
