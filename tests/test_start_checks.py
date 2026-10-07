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

    def test_unselected_saved_app_is_found_and_started(self):
        w = self.window
        w.target = 0
        with patch.object(w.native, 'windows', return_value=[(123, 'Game')]), patch('app.AppMismatchDialog') as prompt:
            w.toggle()
        self.assertTrue(w.armed)
        self.assertEqual(w.target, 123)
        self.assertEqual(w.targets.currentData(), 123)
        self.assertEqual(w.profile.target_program, GAME)
        prompt.assert_not_called()

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
        for choice in ('cancel', 'resize', 'auto'):
            with self.subTest(choice=choice), patch('app.SizeMismatchDialog') as factory, \
                 patch.object(self.window, 'resize_dialog') as resize, \
                 patch.object(self.window, 'convert_dialog') as convert:
                factory.return_value.choice.return_value = choice
                self.window.toggle()
                self.assertEqual(resize.called, choice == 'resize')
                self.assertFalse(convert.called)
                if choice == 'resize':
                    resize.assert_called_once_with(profile_size=(1280, 720))
                self.assertEqual(self.window.armed, choice == 'auto')
                self.assertEqual(self.window.profile.export(), before)
                if choice == 'auto':
                    self.assertEqual(self.window.coordinate_transform, ((1280,720), (1920,1080)))
                    self.window.stop()

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
             patch('interactive_resize.refresh',return_value=''), \
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
        dialog = SizeMismatchDialog((1280,720), (1920,1080), self.window)
        self.assertTrue(dialog.resize_button.isEnabled())
        self.assertIs(dialog.defaultButton(), dialog.cancel_button)
        self.assertTrue(dialog.convert_button.isEnabled())
        QTimer.singleShot(0, dialog.convert_button.click)
        dialog.exec()
        self.assertEqual(dialog.choice(), 'auto')
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

    def test_unselected_without_saved_app_cannot_start(self):
        w = self.window
        w.target = 0
        w.profile.target_program = {}
        with patch.object(w.native, 'windows', return_value=[(123, 'Game')]), patch('app.SizeMismatchDialog') as prompt:
            w.toggle()
        self.assertFalse(w.armed)
        self.assertEqual(w.target, 0)
        self.assertIn('앱을 선택', w.statusBar().currentMessage())
        prompt.assert_not_called()

    def test_oversized_profile_cannot_be_silently_clamped_by_resize_fields(self):
        dialog = SizeMismatchDialog((20000, 10000), (1920, 960), self.window)
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

    def test_dropdown_refresh_preserves_unselected_and_other_app(self):
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

    def test_minimize_preserves_start_size_and_same_size_restore_stays_armed(self):
        from domain import WindowSizeUnavailable
        w = self.window
        w.toggle()
        self.size.side_effect = WindowSizeUnavailable()
        w.tick()
        self.assertTrue(w.armed)
        self.assertEqual(w.run_size, (1280,720))
        self.assertIn('최소화', w.target_info.text())
        self.assertNotIn('0 × 0', w.target_info.text())
        self.size.side_effect = None
        w.tick()
        self.assertTrue(w.armed)
        self.assertNotIn('최소화', w.target_info.text())
        self.assertTrue(w.check_running_size())

    def test_restore_to_different_size_stops_after_minimized_wait(self):
        from domain import WindowSizeUnavailable
        w = self.window
        w.toggle()
        self.size.side_effect = WindowSizeUnavailable()
        w.tick()
        self.assertTrue(w.armed)
        self.size.side_effect = None
        self.size.return_value = (1920,1080)
        w.tick()
        self.assertFalse(w.armed)
        self.assertIn('창 크기', w.statusBar().currentMessage())

    def test_start_while_minimized_does_not_record_zero_or_show_resolution_warning(self):
        from domain import WindowSizeUnavailable
        for resolution in ((1280,720), None):
            with self.subTest(resolution=resolution), patch('app.SizeMismatchDialog') as prompt:
                self.window.profile.resolution = resolution
                self.size.side_effect = WindowSizeUnavailable()
                self.window.toggle()
                prompt.assert_not_called()
                self.assertFalse(self.window.armed)
                self.assertIsNone(self.window.run_size)
                self.assertEqual(self.window.profile.resolution, resolution)
                self.assertIn('복원', self.window.statusBar().currentMessage())

    def test_transient_unavailable_size_blocks_dispatch_without_disarming(self):
        from domain import WindowSizeUnavailable
        w = self.window
        w.toggle()
        self.size.side_effect = WindowSizeUnavailable()
        with patch.object(w.native, 'active', return_value=True), patch.object(w.engine, 'submit') as submit:
            w.dispatch('move', w.profile.rows[0].id)
        submit.assert_not_called()
        self.assertTrue(w.armed)
        self.assertEqual(w.run_size, (1280,720))

    def test_auto_coordinates_dispatch_preserves_profile(self):
        w = self.window
        self.size.return_value = (1920,1080)
        row = w.profile.rows[0]
        row.x, row.y = 640,360
        before = w.profile.export()
        with patch('app.SizeMismatchDialog') as dialog:
            dialog.return_value.choice.return_value = 'auto'
            w.toggle()
        with patch.object(w, 'eligible', return_value=True), patch.object(w.engine, 'submit', return_value=True) as submit:
            w.dispatch('move', row.id)
        command = submit.call_args.args[0]
        self.assertEqual((command.x, command.y), (960,540))
        self.assertEqual(command.expected_size, (1920,1080))
        self.assertEqual(w.profile.export(), before)
        self.size.return_value = (1600,900)
        self.assertFalse(w.check_running_size())
        self.assertFalse(w.armed)
        self.assertIsNone(w.coordinate_transform)

    def test_auto_conversion_survives_program_metadata_save(self):
        w = self.window
        row = w.profile.rows[0]
        row.x, row.y = 640, 360
        self.size.return_value = (1920, 1080)
        for current in ({**GAME, 'title': 'Game updated title'}, OTHER):
            for pause in (True, False):
                with self.subTest(current=current, auto_pause=pause):
                    w.stop()
                    w.profile.target_program = dict(GAME)
                    w.settings.auto_pause = pause
                    self.identity.return_value = current
                    with patch('app.AppMismatchDialog') as app_prompt, patch('app.SizeMismatchDialog') as size_prompt:
                        app_prompt.return_value.confirmed.return_value = True
                        app_prompt.return_value.skip_warning.isChecked.return_value = False
                        size_prompt.return_value.choice.return_value = 'auto'
                        w.toggle()
                    self.assertTrue(w.armed)
                    self.assertEqual(w.coordinate_transform, ((1280,720), (1920,1080)))
                    with patch.object(w.native, 'active', return_value=True), patch.object(w.engine, 'submit', return_value=True) as submit:
                        w.dispatch('move', row.id)
                    command = submit.call_args.args[0]
                    self.assertEqual((command.x, command.y), (960,540))
                    w.flush_pending_saves()
                    saved = Profile.read(read_json(w.path))
                    self.assertEqual(saved.target_program, current)
                    self.assertEqual(saved.resolution, (1280,720))
                    self.assertEqual((saved.rows[0].x, saved.rows[0].y), (640,360))

    def test_saved_app_not_running_keeps_profile_and_blocks_start(self):
        w = self.window
        w.target = 0
        before = w.profile.export()
        with patch.object(w.native, 'windows', return_value=[]), patch.object(w, 'convert_dialog') as convert:
            w.toggle()
        convert.assert_not_called()
        self.assertFalse(w.armed)
        self.assertIsNone(w.session.plan)
        self.assertEqual(w.profile.export(), before)
        self.assertIn('앱을 선택', w.statusBar().currentMessage())

    def test_cancel_and_hook_failure_do_not_publish_prepared_plan(self):
        w = self.window
        self.size.return_value = (1920,1080)
        with patch('app.SizeMismatchDialog') as prompt:
            prompt.return_value.choice.return_value = 'auto'
            w.toggle()
            self.assertIsNotNone(w.session.plan)
            w.stop()
            prompt.return_value.choice.return_value = 'cancel'
            w.toggle()
            self.assertIsNone(w.session.plan)
            prompt.return_value.choice.return_value = 'auto'
            w.hook_enabled = True
            with patch.object(w.hooks, 'start', side_effect=OSError('hook failed')), patch.object(w, 'error') as error:
                w.toggle()
            error.assert_called_once_with('hook failed')
            self.assertFalse(w.armed)
            self.assertIsNone(w.session.plan)
            w.hook_enabled = False

    def test_profile_basis_changed_during_confirmation_rejects_start(self):
        w = self.window
        self.size.return_value = (1920,1080)
        with patch('app.SizeMismatchDialog') as prompt, patch.object(w, 'error') as error:
            prompt.return_value.choice.return_value = 'auto'
            prompt.return_value.exec.side_effect = lambda: setattr(w.profile, 'resolution', (1600,900))
            w.toggle()
        self.assertFalse(w.armed)
        self.assertIsNone(w.session.plan)
        self.assertIn('기준 해상도', error.call_args.args[0])

    def test_edited_source_resolution_cannot_reuse_previous_runtime_transform(self):
        w = self.window
        w.settings.auto_pause = False
        self.size.return_value = (1920,1080)
        with patch('app.SizeMismatchDialog') as prompt:
            prompt.return_value.choice.return_value = 'auto'
            w.toggle()
        w.profile.resolution = (1600,900)
        with patch.object(w.native, 'active', return_value=True), patch.object(w.engine, 'submit') as submit:
            w.dispatch('move', w.profile.rows[0].id)
        submit.assert_not_called()
        self.assertFalse(w.armed)
        self.assertIsNone(w.session.plan)

    def test_confirmation_rejects_replaced_profile_even_at_same_resolution(self):
        from copy import deepcopy
        w = self.window
        self.size.return_value = (1920,1080)
        replacement = deepcopy(w.profile)
        replacement.rows[0].x = 333
        with patch('app.SizeMismatchDialog') as prompt:
            prompt.return_value.choice.return_value = 'auto'
            prompt.return_value.exec.side_effect = lambda: setattr(w, 'profile', replacement)
            w.toggle()
        self.assertFalse(w.armed)
        self.assertIsNone(w.session.plan)
        self.assertIn('프로필', w.statusBar().currentMessage())

    def test_ambiguous_saved_windows_require_explicit_selection(self):
        w = self.window
        w.target = 0
        with patch.object(w.native, 'windows', return_value=[(123,'Game'),(456,'Game')]):
            w.toggle()
        self.assertFalse(w.armed)
        self.assertEqual(w.target, 0)
        self.assertEqual(w.profile.target_program, GAME)
        self.assertIn('앱을 선택', w.statusBar().currentMessage())

    def test_different_ratio_warns_and_keeps_conversion_available(self):
        dialog = SizeMismatchDialog((1280,720),(1280,960),self.window)
        self.assertTrue(dialog.convert_button.isEnabled())
        self.assertTrue(dialog.resize_button.isEnabled())
        self.assertIn('16:9',dialog.informativeText())
        self.assertIn('4:3',dialog.informativeText())
        dialog.deleteLater()

    def test_different_ratio_starts_after_auto_choice_with_original_coordinates_preserved(self):
        w = self.window
        self.size.return_value = (1280,960)
        before = w.profile.export()
        with patch('app.SizeMismatchDialog') as prompt, patch.object(w,'error') as error:
            prompt.return_value.choice.return_value = 'auto'
            w.toggle()
        error.assert_not_called()
        self.assertTrue(w.armed)
        self.assertEqual(w.coordinate_transform,((1280,720),(1280,960)))
        self.assertEqual(w.profile.export(),before)

    def test_different_ratio_resize_then_restart_uses_original_coordinates(self):
        w = self.window
        self.size.return_value = (1280,960)
        def resized(hwnd,width,height):
            self.size.return_value = (width,height)
        with patch('app.SizeMismatchDialog') as prompt, patch.object(w.native,'resize',side_effect=resized) as resize, \
             patch('interactive_resize.refresh',return_value=''):
            prompt.return_value.choice.return_value = 'resize'
            w.toggle()
        resize.assert_called_once_with(123,1280,720)
        self.assertFalse(w.armed)
        w.toggle()
        self.assertTrue(w.armed)
        self.assertIsNone(w.coordinate_transform)

    def test_auto_conversion_preference_persists_and_skips_next_size_prompt(self):
        w = self.window
        self.size.return_value = (1835,1032)
        with patch('app.SizeMismatchDialog') as prompt:
            prompt.return_value.choice.return_value = 'auto'
            prompt.return_value.skip_warning.isChecked.return_value = True
            w.toggle()
        self.assertTrue(w.armed)
        self.assertTrue(w.settings.auto_convert_on_start)
        persisted = Settings.read(read_json(w.settings_path))
        self.assertTrue(persisted.auto_convert_on_start)
        w.stop()
        w.settings = persisted
        self.size.return_value = (1920,1080)
        with patch('app.SizeMismatchDialog') as prompt:
            w.toggle()
        prompt.assert_not_called()
        self.assertTrue(w.armed)
        self.assertEqual(w.coordinate_transform,((1280,720),(1920,1080)))
        self.assertEqual(w.profile.resolution,(1280,720))

    def test_auto_conversion_checkbox_cancel_and_resize_do_not_save_preference(self):
        w = self.window
        self.size.return_value = (1920,1080)
        for choice in ('cancel','resize'):
            with self.subTest(choice=choice), patch('app.SizeMismatchDialog') as prompt, patch.object(w,'resize_dialog'):
                prompt.return_value.choice.return_value = choice
                prompt.return_value.skip_warning.isChecked.return_value = True
                w.toggle()
            self.assertFalse(w.armed)
            self.assertFalse(w.settings.auto_convert_on_start)

    def test_auto_conversion_preference_failure_stops_and_preserves_settings(self):
        w = self.window
        self.size.return_value = (1920,1080)
        with patch('app.SizeMismatchDialog') as prompt, patch.object(w.store,'settings',side_effect=OSError('save failed')), patch.object(w,'error') as error:
            prompt.return_value.choice.return_value = 'auto'
            prompt.return_value.skip_warning.isChecked.return_value = True
            w.toggle()
        error.assert_called_once_with('save failed')
        self.assertFalse(w.armed)
        self.assertIsNone(w.session.plan)
        self.assertFalse(w.settings.auto_convert_on_start)

    def test_auto_preference_keeps_app_warning_and_requires_app(self):
        w = self.window
        w.settings.auto_convert_on_start = True
        self.size.return_value = (1920,1080)
        self.identity.return_value = OTHER
        with patch('app.AppMismatchDialog') as app_prompt, patch('app.SizeMismatchDialog') as size_prompt:
            app_prompt.return_value.confirmed.return_value = False
            w.toggle()
        app_prompt.assert_called_once()
        size_prompt.assert_not_called()
        self.assertFalse(w.armed)
        w.target = 0
        w.profile.target_program = {}
        with patch('app.SizeMismatchDialog') as prompt:
            w.toggle()
        prompt.assert_not_called()
        self.assertFalse(w.armed)

    def test_automatic_ratio_warning_goes_to_log_and_resize_still_stops(self):
        w = self.window
        w.settings.auto_convert_on_start = True
        self.size.return_value = (1280,960)
        with patch('app.SizeMismatchDialog') as prompt:
            w.toggle()
        prompt.assert_not_called()
        self.assertTrue(w.armed)
        self.assertIn('경고',w.statusBar().currentMessage())
        self.size.return_value = (1600,900)
        self.assertFalse(w.check_running_size())
        self.assertFalse(w.armed)

    def test_auto_conversion_checkbox_and_settings_can_restore_prompt(self):
        from widgets import SettingsDialog
        self.assertFalse(Settings.read({}).auto_convert_on_start)
        with self.assertRaises(ValueError):
            Settings.read({'auto_convert_on_start':'true'})
        dialog = SizeMismatchDialog((1280,720),(1920,1080),self.window)
        self.assertEqual(dialog.skip_warning.text(),'다음부터 묻지 않기')
        self.assertFalse(dialog.skip_warning.isChecked())
        dialog.deleteLater()
        w = self.window
        settings = SettingsDialog(Settings(auto_convert_on_start=True),w)
        self.assertTrue(settings.checks['auto_convert_on_start'].isChecked())
        settings.checks['auto_convert_on_start'].setChecked(False)
        w.commit_settings(settings.value())
        settings.deleteLater()
        self.size.return_value = (1920,1080)
        with patch('app.SizeMismatchDialog') as prompt:
            prompt.return_value.choice.return_value = 'cancel'
            w.toggle()
        prompt.assert_called_once()

    def test_script_capture_uses_original_basis_after_automatic_conversion_and_pause(self):
        w = self.window
        w.show()
        self.qt.processEvents()
        w.settings.auto_convert_on_start = True
        row = w.profile.rows[0]
        row.action_mode = 'script'
        row.script = 'click 640,360'
        self.size.return_value = (1920,1080)
        w.toggle()
        self.assertTrue(w.armed)
        w.edit_script(row.id)
        editor = w.script_editor
        self.assertFalse(w.armed)
        editor.editor.setPlainText('click ')
        cursor = editor.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        editor.editor.setTextCursor(cursor)
        with patch.object(w.native,'bounds',return_value=(0,0,1920,1080)), patch.object(w.native,'local_point',return_value=(960,540)):
            editor.capture_screen_point(1200,800)
        self.assertEqual(editor.editor.toPlainText(),'click 640,360')
        self.assertEqual(editor.capture_resolution,(1280,720))
        self.assertEqual(editor.capture_size,(1920,1080))
        editor.save()
        self.assertIsNone(w.script_editor)
        w.flush_pending_saves()
        saved = Profile.read(read_json(w.path))
        self.assertEqual(saved.resolution,(1280,720))
        self.assertEqual(saved.rows[0].script,'click 640,360')

    def test_script_reverse_capture_handles_rounding_and_ratio_warning(self):
        from coordinate_cache import scaled_point
        w = self.window
        row = w.profile.rows[0]
        row.action_mode = 'script'
        w.edit_script(row.id)
        editor = w.script_editor
        for size,point in (((1835,1032),(1834,1031)),((1280,960),(640,480))):
            self.size.return_value = size
            editor.editor.setPlainText('')
            with patch.object(w.native,'bounds',return_value=(0,0,*size)):
                editor.pick_coordinate(point)
            expected = scaled_point(point,size,(1280,720))
            self.assertEqual(editor.editor.toPlainText(),'%d,%d' % expected)
            self.assertEqual(editor.capture_resolution,(1280,720))
            self.assertEqual(editor.capture_size,size)
        self.assertIn('경고',editor.validation.text())
        self.assertFalse(editor.validation.property('error'))

    def test_script_capture_rejects_actual_size_change_before_save(self):
        w = self.window
        row = w.profile.rows[0]
        row.action_mode = 'script'
        w.edit_script(row.id)
        editor = w.script_editor
        self.size.return_value = (1920,1080)
        with patch.object(w.native,'bounds',return_value=(0,0,1920,1080)):
            editor.pick_coordinate((960,540))
        editor.editor.setPlainText('click 640,360')
        self.size.return_value = (1600,900)
        editor.save()
        self.assertIs(w.script_editor,editor)
        self.assertIn('크기가 변경',editor.validation.text())
        self.assertEqual(row.script,'')
        self.assertEqual(w.profile.resolution,(1280,720))

    def test_script_capture_rejects_changed_profile_basis_and_unknown_nonempty_basis(self):
        w = self.window
        row = w.profile.rows[0]
        row.action_mode = 'script'
        w.edit_script(row.id)
        editor = w.script_editor
        self.size.return_value = (1920,1080)
        with patch.object(w.native,'bounds',return_value=(0,0,1920,1080)):
            editor.pick_coordinate((960,540))
        editor.editor.setPlainText('click 640,360')
        w.profile.resolution = (1600,900)
        editor.save()
        self.assertIs(w.script_editor,editor)
        self.assertIn('기준 크기',editor.validation.text())
        editor.reject()
        w.profile.resolution = None
        row.script = 'click 50,50'
        w.edit_script(row.id)
        editor = w.script_editor
        with patch.object(w.native,'bounds',return_value=(0,0,1920,1080)):
            editor.pick_coordinate((960,540))
        self.assertEqual(editor.editor.toPlainText(),'click 50,50')
        self.assertIsNone(editor.capture_resolution)

    def test_auto_selected_app_still_checks_resolution(self):
        w = self.window
        w.target = 0
        self.size.return_value = (1920,1080)
        with patch.object(w.native, 'windows', return_value=[(123,'Game')]), patch('app.SizeMismatchDialog') as prompt:
            prompt.return_value.choice.return_value = 'cancel'
            w.toggle()
        prompt.assert_called_once()
        self.assertEqual(w.target, 123)
        self.assertFalse(w.armed)
        self.assertEqual(w.profile.resolution, (1280,720))

    def test_closed_selection_reacquires_saved_app_after_restart(self):
        w = self.window
        w.target = 999
        self.exists.side_effect = lambda hwnd: hwnd == 123
        with patch.object(w.native, 'windows', return_value=[(123,'Game')]):
            w.toggle()
        self.assertEqual(w.target, 123)
        self.assertTrue(w.armed)

    def test_auto_selected_app_closing_before_start_stays_stopped(self):
        w = self.window
        w.target = 0
        self.exists.return_value = False
        with patch.object(w.native, 'windows', return_value=[(123,'Game')]):
            w.toggle()
        self.assertFalse(w.armed)
        self.assertIsNone(w.session.plan)
        self.assertEqual(w.profile.target_program, GAME)

    def test_manual_app_selection_is_not_replaced_by_saved_app(self):
        w = self.window
        w.target = 456
        self.identity.return_value = OTHER
        with patch.object(w.native, 'windows') as windows, patch('app.AppMismatchDialog') as prompt:
            prompt.return_value.confirmed.return_value = False
            w.toggle()
        windows.assert_not_called()
        self.assertEqual(w.target, 456)
        self.assertFalse(w.armed)
