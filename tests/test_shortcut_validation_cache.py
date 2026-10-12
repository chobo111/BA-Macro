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
        with patch.object(Hotkeys, 'validate', wraps=Hotkeys.validate) as validate, \
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

    def test_row_conflict_is_rejected_repeatedly_and_repair_saves_pending_edits(self):
        w = self.w
        w.validate_profile_shortcuts(w.profile)
        original = w.path.read_bytes()
        w.profile.rows[1].move_hk = 'q'
        w.profile.rows[0].memo = 'pending'
        with patch.object(Hotkeys, 'validate', wraps=Hotkeys.validate) as validate:
            for _ in range(2):
                self.assertFalse(w.save(automatic=True))
                self.assertTrue(w.dirty)
                self.assertEqual(w.path.read_bytes(), original)
            self.assertEqual(validate.call_count, 2)
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

    def test_replacing_profile_and_mutating_keys_rechecks_conflicts(self):
        w = self.w
        w.validate_profile_shortcuts(w.profile)
        other = Profile.default()
        with patch.object(Hotkeys, 'validate', wraps=Hotkeys.validate) as validate:
            w.validate_profile_shortcuts(other)
            validate.assert_not_called()
            other.rows[2].move_hk = 'ctrl+s'
            with self.assertRaises(ShortcutError):
                w.validate_profile_shortcuts(other)
            validate.assert_called_once()

    def test_layout_change_rechecks_physical_symbol_collisions(self):
        w = self.w
        w.profile.rows[0].move_hk = 'plus'
        w.profile.rows[1].move_hk = '='
        with patch('keyboard_hook.character_key', return_value=(0xBB, frozenset(('shift',)))) as mapping:
            w.validate_profile_shortcuts(w.profile)
            self.layout.return_value = 2
            mapping.return_value = (0xBB, frozenset())
            with self.assertRaisesRegex(ShortcutError, '실제 입력'):
                w.validate_profile_shortcuts(w.profile)
