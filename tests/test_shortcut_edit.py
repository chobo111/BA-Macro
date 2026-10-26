import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from concurrent.futures import Future
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from app import MainWindow
from domain import Profile, Row, read_json
from hotkeys import Hotkeys
from persistence import SaveResult
from shortcut_edit import plan_row_shortcut, validate_global_shortcuts
from widgets import KeyDialog, SettingsDialog, managed_dialog


class ShortcutPlanTests(unittest.TestCase):
    def test_aliases_use_same_policy_in_edit_and_settings_validation(self):
        rows = [Row(move_hk='q'), Row(move_hk='w')]
        for key in ('right_ctrl+q', 'left windows', 'command'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                plan_row_shortcut(rows, ('f8', 'f9'), rows[0].id, key)
        rows[1].move_hk = 'left shift'
        with self.assertRaisesRegex(ValueError, '2행'):
            plan_row_shortcut(rows, ('f8', 'f9'), rows[0].id, 'right_shift+q')
        prepared = Hotkeys.prepare_profile(('f8', 'f9'), (row.move_hk for row in rows))
        with self.assertRaisesRegex(ValueError, '2행'):
            validate_global_shortcuts(('f8', 'right shift+q'), rows, prepared)
        with patch.object(Hotkeys, 'compile', wraps=Hotkeys.compile) as compile_key:
            validate_global_shortcuts(('f8', 'f10'), rows, prepared)
            self.assertEqual(compile_key.call_count, 2)  # reuse checked row matchers

    def plan(self, keys, value, globals=('ctrl+s', 'ctrl+`')):
        rows = [Row(move_hk=key) for key in keys]
        return rows, plan_row_shortcut(rows, globals, rows[0].id, value)

    def test_swap_preview_has_both_changes_and_never_mutates_rows(self):
        rows, edit = self.plan(('q', 'w', 'e'), 'w')
        self.assertEqual(edit.changes, ((rows[0].id, 'q', 'w'), (rows[1].id, 'w', 'q')))
        self.assertEqual(edit.action, '서로 바꾸기')
        self.assertIn('1행: Q → W', edit.message)
        self.assertIn('2행: W → Q', edit.message)
        self.assertEqual([row.move_hk for row in rows], ['q', 'w', 'e'])

    def test_empty_key_moves_binding_and_explicitly_previews_blank(self):
        rows, edit = self.plan(('', 'w'), 'w')
        self.assertEqual(edit.action, '옮기기')
        self.assertEqual(edit.changes[1], (rows[1].id, 'w', ''))
        self.assertIn('2행: W → 공란', edit.message)

    def test_global_conflicts_name_both_functions_and_offer_no_exchange(self):
        with self.assertRaises(ValueError) as caught:
            self.plan(('q', 'w'), 'ctrl')
        message = str(caught.exception)
        for text in ('좌표 캡처', '시작·정지', 'Ctrl+S', 'Ctrl+`', '환경설정'):
            self.assertIn(text, message)

    def test_modifier_and_multiple_owner_conflicts_cannot_be_swapped(self):
        for keys, value in ((('q', 'alt+q'), 'alt'), (('q', 'w', 'w'), 'w')):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                self.plan(keys, value)

    def test_new_conflict_after_exchange_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '교환 후에도'):
            self.plan(('q', 'w', 'q'), 'w')

    def test_physical_alias_can_be_exchanged(self):
        rows, edit = self.plan(('w', 'shift+-'), 'shift+_')
        self.assertEqual(edit.action, '서로 바꾸기')
        self.assertEqual(edit.changes[1], (rows[1].id, 'shift+-', 'w'))

    def test_invalid_old_key_cannot_be_moved_to_another_row(self):
        with self.assertRaisesRegex(ValueError, '기존 단축키를 교환할 수 없습니다'):
            self.plan(('ctrl+q', 'w'), 'w')
        _, edit = self.plan(('ctrl+q', 'w'), 'alt+r')
        self.assertEqual(edit.action, '확인')

    def test_unrelated_imported_errors_do_not_block_repairs(self):
        _, edit = self.plan(('ctrl+q', 'win', 'w', 'w'), 'f8')
        self.assertEqual(len(edit.changes), 1)

    def test_long_memo_is_single_line_and_truncated_in_preview(self):
        rows = [Row(move_hk='q'), Row(move_hk='w', memo='메모\n' * 100)]
        edit = plan_row_shortcut(rows, ('f8', 'f9'), rows[0].id, 'w')
        self.assertIn('2행 · 메모 메모', edit.message)
        self.assertIn('…', edit.message)
        self.assertLess(len(edit.message), 200)

    def test_global_settings_conflict_identifies_row_and_edited_function(self):
        rows = [Row(move_hk='q', memo='카드 선택')]
        with self.assertRaises(ValueError) as caught:
            prepared = Hotkeys.prepare_profile(('f8', 'f9'), ('q',))
            validate_global_shortcuts(('f8', 'q'), rows, prepared)
        self.assertIn('시작·정지', str(caught.exception))
        self.assertIn('1행 · 카드 선택', str(caught.exception))

    def test_win_is_preserved_in_imports_but_never_registered(self):
        for name in ('win', 'Meta', 'windows'):
            with self.subTest(name=name):
                profile = Profile.read(Profile([Row(move_hk=name)]).export())
                self.assertEqual(profile.rows[0].move_hk, 'win')
                prepared = Hotkeys.prepare_profile(('f8', 'f9'), (name, 'q'))
                self.assertEqual(prepared.keys, ('', 'q'))
                self.assertIn('Win', prepared.issues[0])
                with self.assertRaisesRegex(ValueError, 'Win'):
                    Hotkeys.validate((name,))

    def test_symbol_advice_uses_current_layout_and_retains_existing_modifiers(self):
        with patch('keyboard_hook.character_key', return_value=(0x31, frozenset(('shift',)))), \
                patch('keyboard_hook.base_character', return_value='1'):
            for key, expected in (('!', 'Shift+1'), ('alt+!', 'Alt+Shift+1')):
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, expected.replace('+', r'\+')):
                    Hotkeys.compile(key)


class ShortcutEditUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        with patch('app.Windows.windows', return_value=[]):
            self.w = MainWindow(data_dir=self.folder.name, hook_enabled=False)
        self.w.timer.stop()

    def tearDown(self):
        QTest.keyRelease(self.w, Qt.Key.Key_Control)
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.folder.cleanup()

    @staticmethod
    def choose_w(dialog):
        dialog.editor.setKeySequence(QKeySequence('W'))
        dialog.accept()
        return dialog.result()

    def test_swap_applies_two_rows_in_one_save_without_changing_other_data(self):
        w = self.w
        before = w.profile.export()
        with patch.object(KeyDialog, 'exec', self.choose_w), patch.object(w.store, 'save', wraps=w.store.save) as save:
            w.edit_key(w.profile.rows[0].id)
            w.flush_pending_saves()
            self.assertEqual(save.call_count, 1)
        before['macros'][0]['move_hk'], before['macros'][1]['move_hk'] = 'w', 'q'
        self.assertEqual(w.profile.export(), before)
        self.assertEqual(read_json(w.path), before)
        self.assertEqual({b.key: b.row_id for b in w.hooks.bindings if b.action == 'move'},
                         {row.move_hk: row.id for row in w.profile.rows})

    def test_cancel_exchange_leaves_profile_file_and_bindings_unchanged(self):
        w = self.w
        before, bindings = w.path.read_bytes(), list(w.hooks.bindings)
        def cancel(dialog):
            dialog.editor.setKeySequence(QKeySequence('W'))
            self.assertEqual(dialog.confirm_button.text(), '서로 바꾸기')
            self.assertEqual(w.profile.rows[0].move_hk, 'q')
            dialog.reject()
            return dialog.result()
        with patch.object(KeyDialog, 'exec', cancel), patch.object(w.store, 'save') as save:
            w.edit_key(w.profile.rows[0].id)
            save.assert_not_called()
        self.assertEqual(w.path.read_bytes(), before)
        self.assertEqual(w.hooks.bindings, bindings)

    def test_empty_row_moves_key_and_saves_partner_blank(self):
        w = self.w
        w.profile.rows[0].move_hk = ''
        def move(dialog):
            dialog.editor.setKeySequence(QKeySequence('W'))
            self.assertEqual(dialog.confirm_button.text(), '옮기기')
            self.assertIn('W → 공란', dialog.validation.text())
            dialog.accept()
            return dialog.result()
        with patch.object(KeyDialog, 'exec', move):
            w.edit_key(w.profile.rows[0].id)
        w.flush_pending_saves()
        self.assertEqual([r['move_hk'] for r in read_json(w.path)['macros']], ['w', '', 'e'])

    def test_invalid_input_disables_confirmation_immediately_and_recovers(self):
        w = self.w
        with managed_dialog(KeyDialog('q', w)) as dialog:
            dialog.validate_shortcut = lambda value: plan_row_shortcut(w.profile.rows,
                (w.settings.capture_hk, w.settings.pause_hk), w.profile.rows[0].id, value)
            for value, message in (('Control', '좌표 캡처'), ('Ctrl+Q', 'Shift와 Alt'), ('Meta', 'Win'), ('!', 'Shift+1')):
                dialog.editor.setKeySequence(QKeySequence(value))
                self.assertFalse(dialog.confirm_button.isEnabled())
                self.assertIn(message, dialog.validation.text())
            dialog.editor.setKeySequence(QKeySequence('Alt+R'))
            self.assertTrue(dialog.confirm_button.isEnabled())
            self.assertTrue(dialog.validation.isHidden())
            dialog.editor.clear()
            self.assertTrue(dialog.confirm_button.isEnabled())

    def test_changed_exchange_requires_second_confirmation_of_updated_preview(self):
        w = self.w
        def changed(dialog):
            dialog.editor.setKeySequence(QKeySequence('W'))
            w.profile.rows[0].move_hk = 'r'
            dialog.accept()
            self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
            self.assertIn('W → R', dialog.validation.text())
            dialog.accept()
            return dialog.result()
        with patch.object(KeyDialog, 'exec', changed):
            w.edit_key(w.profile.rows[0].id)
        self.assertEqual([r.move_hk for r in w.profile.rows], ['w', 'r', 'e'])

    def test_changed_state_after_acceptance_does_not_apply_stale_exchange(self):
        w = self.w
        before = w.path.read_bytes()
        def changed(dialog):
            dialog.editor.setKeySequence(QKeySequence('W'))
            dialog.accept()
            w.profile.rows[0].move_hk = 'r'
            return dialog.result()
        with patch.object(KeyDialog, 'exec', changed):
            w.edit_key(w.profile.rows[0].id)
        self.assertEqual([r.move_hk for r in w.profile.rows], ['r', 'w', 'e'])
        self.assertEqual(w.path.read_bytes(), before)
        self.assertIn('다시 확인', w.statusBar().currentMessage())

    def test_disappearing_conflict_after_acceptance_still_requires_review(self):
        w = self.w
        def changed(dialog):
            dialog.editor.setKeySequence(QKeySequence('W'))
            dialog.accept()
            w.profile.rows[1].move_hk = 'r'
            return dialog.result()
        with patch.object(KeyDialog, 'exec', changed):
            w.edit_key(w.profile.rows[0].id)
        self.assertEqual([r.move_hk for r in w.profile.rows], ['q', 'r', 'e'])
        self.assertIn('다시 확인', w.statusBar().currentMessage())

    def test_failed_save_retains_both_edits_for_retry_and_original_file(self):
        w = self.w
        before = w.path.read_bytes()
        failed = Future()
        failed.set_result(SaveResult(False, error='disk full'))
        with patch.object(KeyDialog, 'exec', self.choose_w), patch.object(w.store, 'save', return_value=failed):
            w.edit_key(w.profile.rows[0].id)
            w.flush_pending_saves()
        self.assertTrue(w.dirty)
        self.assertEqual(w.path.read_bytes(), before)
        self.assertEqual([r.move_hk for r in w.profile.rows], ['w', 'q', 'e'])
        w.save(automatic=True)
        w.flush_pending_saves()
        self.assertFalse(w.dirty)
        self.assertEqual([r['move_hk'] for r in read_json(w.path)['macros']], ['w', 'q', 'e'])

    def test_failed_rebind_restores_both_keys(self):
        w = self.w
        before = w.path.read_bytes()
        with patch.object(KeyDialog, 'exec', self.choose_w), \
                patch.object(w, 'rebind', side_effect=[RuntimeError('hook failed'), None]) as rebind:
            w.edit_key(w.profile.rows[0].id)
            self.assertEqual(rebind.call_count, 2)
        self.assertEqual([r.move_hk for r in w.profile.rows], ['q', 'w', 'e'])
        self.assertEqual(w.path.read_bytes(), before)

    def test_settings_checks_conflicts_and_win_before_save(self):
        w = self.w
        with managed_dialog(SettingsDialog(w.settings, w)) as dialog:
            dialog.validate_settings = lambda settings: w.validate_profile_shortcuts(w.profile, settings)
            dialog.keys['pause_hk'].setKeySequence(QKeySequence('Q'))
            self.assertFalse(dialog.save_button.isEnabled())
            self.assertIn('1행', dialog.validation.text())
            self.assertIn('시작·정지', dialog.validation.text())
            dialog.keys['pause_hk'].setKeySequence(QKeySequence('Meta'))
            self.assertFalse(dialog.save_button.isEnabled())
            self.assertIn('Win', dialog.validation.text())
            dialog.keys['pause_hk'].setKeySequence(QKeySequence('F9'))
            self.assertTrue(dialog.save_button.isEnabled())
            self.assertTrue(dialog.validation.isHidden())

    def test_imported_win_can_be_opened_and_repaired_without_silent_clear(self):
        with managed_dialog(KeyDialog('win', self.w)) as dialog:
            self.assertIn('Meta', dialog.editor.keySequence().toString())
            self.assertFalse(dialog.confirm_button.isEnabled())
            self.assertIn('Win', dialog.validation.text())
            dialog.editor.setKeySequence(QKeySequence('Q'))
            self.assertTrue(dialog.confirm_button.isEnabled())

    def test_long_preview_fits_fixed_width_in_both_themes(self):
        w = self.w
        w.profile.rows[0].memo = '아주 긴 첫 번째 스킬 메모입니다 ' * 10
        w.profile.rows[1].memo = '아주 긴 두 번째 스킬 메모입니다 ' * 10
        for theme in ('Dark', 'Light'):
            with self.subTest(theme=theme):
                w.settings.theme = theme
                w.apply_theme()
                with managed_dialog(KeyDialog('q', w)) as dialog:
                    dialog.validate_shortcut = lambda value: plan_row_shortcut(w.profile.rows,
                        ('ctrl+s', 'ctrl+`'), w.profile.rows[0].id, value)
                    dialog.editor.setKeySequence(QKeySequence('W'))
                    dialog.show()
                    self.qt.processEvents()
                    self.assertEqual(dialog.width(), 280)
                    self.assertGreaterEqual(dialog.validation.height(),
                                            dialog.validation.heightForWidth(dialog.validation.width()))
                    self.assertTrue(dialog.rect().contains(dialog.confirm_button.geometry()))
                    dialog.reject()

    def test_legacy_global_win_can_be_repaired_without_resetting_other_settings(self):
        w = self.w
        w.settings = replace(w.settings, pause_hk='win', theme='Light', click_hold_ms=123)
        with self.assertRaisesRegex(ValueError, 'Win'):
            w.rebind()
        candidate = replace(w.settings, pause_hk='f9')
        w.validate_profile_shortcuts(w.profile, candidate)
        w.commit_settings(candidate)
        w.rebind()
        saved = read_json(w.settings_path)
        self.assertEqual((saved['pause_hk'], saved['theme'], saved['click_hold_ms']), ('f9', 'Light', 123))
        self.assertNotIn('win', {binding.key for binding in w.hooks.bindings})
