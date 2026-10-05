import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from app import MainWindow
from domain import Profile, Settings, read_json, same_program
from startup_dialogs import AppMismatchDialog, SizeMismatchDialog

GAME = {'exe': 'Game.exe', 'class': 'GameWindow', 'title': 'Game'}
OTHER = {'exe': 'Other.exe', 'class': 'OtherWindow', 'title': 'Other'}


class StartChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.window = MainWindow(data_dir=self.folder.name, hook_enabled=False)
        self.window.timer.stop()
        self.window.target = 123
        self.window.profile.target_program = dict(GAME)
        self.window.profile.resolution = (1280, 720)
        self.exists = patch.object(self.window.native, 'exists', return_value=True).start()
        self.identity = patch.object(self.window.native, 'window_identity', return_value=dict(GAME)).start()
        self.size = patch.object(self.window.native, 'size', return_value=(1280, 720)).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.window.shutdown()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        self.folder.cleanup()

    def test_matching_app_and_size_start_without_prompts(self):
        with patch('app.AppMismatchDialog') as app, patch('app.SizeMismatchDialog') as size:
            self.window.toggle()
        self.assertTrue(self.window.armed)
        app.assert_not_called()
        size.assert_not_called()

    def test_changed_title_and_executable_case_are_same_app(self):
        current = {**GAME, 'exe': 'GAME.EXE', 'title': 'Game 60 FPS'}
        self.identity.return_value = current
        with patch('app.AppMismatchDialog') as warning:
            self.window.toggle()
        warning.assert_not_called()
        self.assertTrue(self.window.armed)
        self.assertEqual(self.window.profile.target_program, current)
        self.assertFalse(same_program(GAME, OTHER))
        self.assertFalse(same_program({'class': 'X', 'title': 'A'}, {'class': 'X', 'title': 'B'}))

    def test_different_app_cancel_preserves_profile_and_preference(self):
        self.identity.return_value = OTHER
        with patch('app.AppMismatchDialog') as factory:
            dialog = factory.return_value
            dialog.confirmed.return_value = False
            dialog.skip_warning.isChecked.return_value = True
            self.window.toggle()
        self.assertFalse(self.window.armed)
        self.assertEqual(self.window.profile.target_program, GAME)
        self.assertTrue(self.window.settings.warn_app_mismatch)

    def test_confirmed_app_and_opt_out_persist(self):
        self.identity.return_value = OTHER
        with patch('app.AppMismatchDialog') as factory:
            dialog = factory.return_value
            dialog.confirmed.return_value = True
            dialog.skip_warning.isChecked.return_value = True
            self.window.toggle()
        self.assertTrue(self.window.armed)
        self.assertFalse(self.window.settings.warn_app_mismatch)
        self.window.flush_pending_saves()
        self.assertFalse(Settings.read(read_json(self.window.settings_path)).warn_app_mismatch)
        self.assertEqual(Profile.read(read_json(self.window.path)).target_program, OTHER)

    def test_opt_out_failure_does_not_start_or_replace_profile(self):
        self.identity.return_value = OTHER
        with patch('app.AppMismatchDialog') as factory, \
             patch.object(self.window.store, 'settings', side_effect=OSError('save failed')), \
             patch.object(self.window, 'error') as error:
            factory.return_value.confirmed.return_value = True
            factory.return_value.skip_warning.isChecked.return_value = True
            self.window.toggle()
        error.assert_called_once_with('save failed')
        self.assertFalse(self.window.armed)
        self.assertTrue(self.window.settings.warn_app_mismatch)
        self.assertEqual(self.window.profile.target_program, GAME)

    def test_opt_out_does_not_skip_resolution_warning(self):
        self.identity.return_value = OTHER
        self.size.return_value = (1920, 1080)
        self.window.settings.warn_app_mismatch = False
        with patch('app.AppMismatchDialog') as app, patch('app.SizeMismatchDialog') as size:
            size.return_value.choice.return_value = 'cancel'
            self.window.toggle()
        app.assert_not_called()
        size.assert_called_once()
        self.assertFalse(self.window.armed)

    def test_no_app_selection_warns_against_saved_app(self):
        self.window.target = 0
        with patch('app.AppMismatchDialog') as factory:
            factory.return_value.confirmed.return_value = False
            self.window.toggle()
        self.assertEqual(factory.call_args.args[:2], (GAME, {}))
        self.assertFalse(self.window.armed)
        self.assertEqual(self.window.profile.target_program, GAME)

    def test_selection_does_not_overwrite_saved_app_before_confirmation(self):
        self.identity.return_value = OTHER
        w = self.window
        w.targets.blockSignals(True)
        w.targets.addItem('Other', 456)
        w.targets.setCurrentIndex(w.targets.findData(456))
        w.targets.blockSignals(False)
        w.change_target()
        self.assertEqual(w.target, 456)
        self.assertEqual(w.profile.target_program, GAME)

    def test_modal_warning_prevents_reentrant_start_even_without_auto_pause(self):
        self.identity.return_value = OTHER
        self.window.settings.auto_pause = False
        with patch('app.AppMismatchDialog') as factory:
            factory.return_value.exec.side_effect = self.window.toggle
            factory.return_value.confirmed.return_value = False
            self.window.toggle()
        factory.assert_called_once()
        self.assertFalse(self.window.modal)
        self.assertFalse(self.window.armed)

    def test_window_closes_during_confirmation(self):
        self.identity.return_value = OTHER
        with patch('app.AppMismatchDialog') as factory:
            def close_window():
                self.exists.return_value = False
            factory.return_value.exec.side_effect = close_window
            factory.return_value.confirmed.return_value = True
            factory.return_value.skip_warning.isChecked.return_value = False
            self.window.toggle()
        self.assertFalse(self.window.armed)
        self.assertEqual(self.window.profile.target_program, GAME)
        self.assertIn('닫혔습니다', self.window.statusBar().currentMessage())

    def test_resolution_choices_route_without_starting_or_changing_coordinates(self):
        self.size.return_value = (1920, 1080)
        before = self.window.profile.export()
        for choice in ('cancel', 'resize', 'convert'):
            with self.subTest(choice=choice), patch('app.SizeMismatchDialog') as factory, \
                 patch.object(self.window, 'resize_dialog') as resize, \
                 patch.object(self.window, 'convert_dialog') as convert:
                factory.return_value.choice.return_value = choice
                self.window.toggle()
                self.assertEqual(resize.called, choice == 'resize')
                self.assertEqual(convert.called, choice == 'convert')
                if choice == 'resize':
                    resize.assert_called_once_with(profile_size=(1280, 720))
                self.assertFalse(self.window.armed)
                self.assertEqual(self.window.profile.export(), before)

    def test_unknown_profile_resolution_is_not_guessed(self):
        self.window.profile.resolution = None
        with patch('app.SizeMismatchDialog') as warning:
            self.window.toggle()
        warning.assert_not_called()
        self.assertTrue(self.window.armed)

    def test_approved_resize_uses_profile_size_and_verifies_actual_window(self):
        self.size.return_value = (1920, 1080)
        def resized(hwnd, width, height):
            self.size.return_value = (width, height)
        before = self.window.profile.export()
        preference = self.window.settings.resize_convert_profile
        with patch.object(self.window.native, 'resize', side_effect=resized) as resize, \
             patch.object(self.window, 'convert_dialog') as convert:
            self.window.resize_dialog(profile_size=(1280, 720))
        resize.assert_called_once_with(123, 1280, 720)
        convert.assert_not_called()
        self.assertEqual(self.window.profile.export(), before)
        self.assertEqual(self.window.settings.resize_convert_profile, preference)
        self.assertFalse(self.window.armed)
        self.window.toggle()
        self.assertTrue(self.window.armed)

    def test_warning_dialog_buttons_and_cancel_defaults(self):
        dialog = AppMismatchDialog(GAME, OTHER, self.window)
        self.assertIs(dialog.defaultButton(), dialog.cancel_button)
        self.assertEqual(dialog.skip_warning.text(), '다음부터 알리지 않음')
        self.assertFalse(dialog.skip_warning.isChecked())
        dialog.skip_warning.setChecked(True)
        QTimer.singleShot(0, dialog.start_button.click)
        dialog.exec()
        self.assertTrue(dialog.confirmed())
        dialog.deleteLater()
        for can_resize in (True, False):
            dialog = SizeMismatchDialog((1280,720), (1920,1080), self.window, can_resize=can_resize)
            self.assertEqual(dialog.resize_button.isEnabled(), can_resize)
            self.assertIs(dialog.defaultButton(), dialog.cancel_button)
            QTimer.singleShot(0, dialog.convert_button.click)
            dialog.exec()
            self.assertEqual(dialog.choice(), 'convert')
            dialog.deleteLater()

    def test_warning_preference_validation_and_settings_restore(self):
        from widgets import SettingsDialog
        self.assertTrue(Settings.read({}).warn_app_mismatch)
        with self.assertRaises(ValueError):
            Settings.read({'warn_app_mismatch': 'false'})
        settings = Settings(warn_app_mismatch=False)
        self.assertEqual(Settings.read(asdict(settings)), settings)
        dialog = SettingsDialog(settings, self.window)
        dialog.checks['warn_app_mismatch'].setChecked(True)
        self.assertTrue(dialog.value().warn_app_mismatch)
        dialog.deleteLater()

    def test_app_replaced_during_confirmation_cannot_start_under_old_approval(self):
        self.identity.side_effect = [OTHER, {**OTHER, 'exe': 'New.exe'}]
        with patch('app.AppMismatchDialog') as factory:
            factory.return_value.confirmed.return_value = True
            factory.return_value.skip_warning.isChecked.return_value = False
            self.window.toggle()
        self.assertFalse(self.window.armed)
        self.assertEqual(self.window.profile.target_program, GAME)

    def test_selected_display_mismatch_offers_conversion_without_window_resize(self):
        self.window.target = 0
        self.window.profile.target_program = {}
        self.window.native.display_id = 'display-test'
        self.size.return_value = (1920,1080)
        with patch('app.SizeMismatchDialog') as factory:
            factory.return_value.choice.return_value = 'cancel'
            self.window.toggle()
        self.assertFalse(factory.call_args.kwargs['can_resize'])
        self.assertFalse(self.window.armed)

    def test_oversized_profile_cannot_be_silently_clamped_by_resize_fields(self):
        dialog = SizeMismatchDialog((20000, 10000), (1920, 1080), self.window)
        self.assertFalse(dialog.resize_button.isEnabled())
        self.assertTrue(dialog.convert_button.isEnabled())
        dialog.deleteLater()

    def test_new_profile_always_stops_input_even_when_auto_pause_is_disabled(self):
        w = self.window
        w.settings.auto_pause = False
        w.armed = True
        with patch.object(w.native, 'windows', return_value=[]), \
             patch.object(w.engine, 'stop', wraps=w.engine.stop) as stop:
            w.new_profile()
        self.assertFalse(w.armed)
        self.assertEqual(w.target, 0)
        stop.assert_called()
        self.assertEqual(w.profile.target_program, {})

    def test_cancel_new_profile_preserves_existing_profile(self):
        w = self.window
        before = w.profile
        with patch.object(w, 'confirm_changes', return_value=False):
            w.new_profile()
        self.assertIs(w.profile, before)
        self.assertEqual(w.target, 123)

    def test_dropdown_refresh_preserves_explicit_fullscreen_and_other_app(self):
        w = self.window
        self.identity.side_effect = lambda hwnd: GAME if hwnd == 123 else OTHER
        with patch.object(w.native, 'windows', return_value=[(123,'Game'),(456,'Other')]):
            for target in (0, 456):
                w.target = target
                w.refresh_windows()
                self.assertEqual(w.target, target)
                self.assertEqual(w.targets.currentData(), target)
                self.assertEqual(w.profile.target_program, GAME)
            w.target = 0
            w.refresh_windows(restore_saved=True)
            self.assertEqual(w.target, 123)

    def test_missing_selected_app_does_not_restore_another_app_on_dropdown_open(self):
        w = self.window
        w.target = 456
        w.armed = True
        with patch.object(w.native, 'windows', return_value=[(123,'Game')]):
            w.refresh_windows()
        self.assertEqual(w.target, 0)
        self.assertFalse(w.armed)
        self.assertEqual(w.profile.target_program, GAME)

    def test_coordinate_capture_confirms_and_saves_app_and_point_together(self):
        w = self.window
        self.identity.return_value = OTHER
        w.settings.warn_app_mismatch = False
        with patch('app.CoordinateAppDialog') as prompt, \
             patch.object(w.native, 'cursor', return_value=(700,600)), \
             patch.object(w.native, 'bounds', return_value=(0,0,1280,720)):
            prompt.return_value.confirmed.return_value = True
            w.capture(w.profile.rows[0].id)
        prompt.assert_called_once()
        w.flush_pending_saves()
        saved = Profile.read(read_json(w.path))
        self.assertEqual(saved.target_program, OTHER)
        self.assertEqual((saved.rows[0].x, saved.rows[0].y), (700,600))

    def test_coordinate_capture_cancel_preserves_app_point_and_file(self):
        w = self.window
        w.save()
        before = w.path.read_bytes()
        self.identity.return_value = OTHER
        with patch('app.CoordinateAppDialog') as prompt, \
             patch.object(w.native, 'cursor', return_value=(700,600)), \
             patch.object(w.native, 'bounds', return_value=(0,0,1280,720)):
            prompt.return_value.confirmed.return_value = False
            w.capture(w.profile.rows[0].id)
        w.flush_pending_saves()
        self.assertEqual(w.path.read_bytes(), before)
        self.assertEqual(w.profile.target_program, GAME)
        self.assertEqual(w.profile.rows[0].x, 0)

    def test_capture_cannot_commit_if_app_disappears_during_confirmation(self):
        w = self.window
        before = w.profile.export()
        self.identity.return_value = OTHER
        with patch('app.CoordinateAppDialog') as prompt, \
             patch.object(w.native, 'cursor', return_value=(700,600)), \
             patch.object(w.native, 'bounds', return_value=(0,0,1280,720)):
            prompt.return_value.confirmed.return_value = True
            prompt.return_value.exec.side_effect = lambda: setattr(w, 'target', 0)
            w.capture(w.profile.rows[0].id)
        self.assertEqual(w.profile.export(), before)
        self.assertFalse(w.armed)

    def test_script_captured_coordinates_update_app_only_on_confirmed_save(self):
        w = self.window
        self.identity.return_value = OTHER
        row = w.profile.rows[0]
        row.action_mode = 'script'
        w.edit_script(row.id)
        editor = w.script_editor
        editor.editor.setPlainText('click ')
        cursor = editor.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        editor.editor.setTextCursor(cursor)
        with patch.object(w.native, 'bounds', return_value=(0,0,1280,720)):
            editor.pick_coordinate((700,600))
        self.assertEqual(w.profile.target_program, GAME)
        with patch('app.CoordinateAppDialog') as prompt:
            prompt.return_value.confirmed.return_value = False
            editor.save()
            self.assertIs(w.script_editor, editor)
            self.assertEqual(row.script, '')
            self.assertEqual(w.profile.target_program, GAME)
            prompt.return_value.confirmed.return_value = True
            editor.save()
        self.assertIsNone(w.script_editor)
        w.flush_pending_saves()
        saved = Profile.read(read_json(w.path))
        self.assertEqual(saved.target_program, OTHER)
        self.assertEqual(saved.rows[0].script, 'click 700,600')

    def test_script_capture_rejects_changed_app_before_save(self):
        w = self.window
        row = w.profile.rows[0]
        row.action_mode = 'script'
        w.edit_script(row.id)
        editor = w.script_editor
        with patch.object(w.native, 'bounds', return_value=(0,0,1280,720)):
            editor.pick_coordinate((100,200))
        editor.editor.setPlainText('click 100,200')
        self.identity.return_value = OTHER
        with patch('app.CoordinateAppDialog') as prompt:
            editor.save()
        prompt.assert_not_called()
        self.assertIs(w.script_editor, editor)
        self.assertIn('변경', editor.validation.text())
        self.assertEqual(w.profile.target_program, GAME)

    def test_coordinate_app_warning_defaults_to_cancel(self):
        from startup_dialogs import CoordinateAppDialog
        dialog = CoordinateAppDialog(GAME, OTHER, self.window)
        self.assertIs(dialog.defaultButton(), dialog.cancel_button)
        QTimer.singleShot(0, dialog.cancel_button.click)
        dialog.exec()
        self.assertFalse(dialog.confirmed())
        dialog.deleteLater()

    def test_window_resize_stops_armed_state_even_with_auto_pause_disabled(self):
        w = self.window
        w.settings.auto_pause = False
        w.toggle()
        self.assertTrue(w.armed)
        self.assertEqual(w.run_size, (1280,720))
        self.size.return_value = (1920,1080)
        with patch.object(w.engine, 'stop', wraps=w.engine.stop) as stop:
            w.tick()
        self.assertFalse(w.armed)
        self.assertIsNone(w.run_size)
        self.assertIn('창 크기', w.statusBar().currentMessage())
        stop.assert_called_once()
        self.size.return_value = (1280,720)
        w.tick()
        self.assertFalse(w.armed)
        w.toggle()
        self.assertTrue(w.armed)
        self.assertEqual(w.run_size, (1280,720))

    def test_resize_between_ui_ticks_cannot_submit_a_new_action(self):
        w = self.window
        w.toggle()
        self.size.return_value = (1920,1080)
        with patch.object(w.native, 'active', return_value=True), \
             patch.object(w.engine, 'submit') as submit:
            w.dispatch('move', w.profile.rows[0].id)
        submit.assert_not_called()
        self.assertFalse(w.armed)

    def test_unchanged_size_keeps_running_and_is_attached_to_commands(self):
        w = self.window
        w.toggle()
        for active in (False, True):
            with patch.object(w.native, 'active', return_value=active), \
                 patch.object(w.native, 'cursor', return_value=(321,432)):
                w.tick()
            self.assertTrue(w.armed)
        with patch.object(w.native, 'active', return_value=True), \
             patch.object(w.engine, 'submit', return_value=True) as submit:
            w.dispatch('move', w.profile.rows[0].id)
        self.assertEqual(submit.call_args.args[0].expected_size, (1280,720))

    def test_unknown_profile_resolution_still_monitors_start_size(self):
        w = self.window
        w.profile.resolution = None
        w.toggle()
        self.size.return_value = (1920,1080)
        w.tick()
        self.assertFalse(w.armed)
        self.assertIsNone(w.profile.resolution)
