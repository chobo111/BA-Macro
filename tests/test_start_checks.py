import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QFileDialog
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

    def test_different_app_cancel_preserves_profile(self):
        self.identity.return_value = OTHER
        with patch('app.AppMismatchDialog') as factory:
            dialog = factory.return_value
            dialog.choice.return_value = 'cancel'
            self.window.toggle()
        self.assertFalse(self.window.armed)
        self.assertEqual(self.window.profile.target_program, GAME)

    def test_current_profile_change_saves_identity_and_preserves_actions(self):
        w = self.window
        w.profile.rows[0].x = 321
        w.save()
        old_path = w.path
        self.identity.return_value = OTHER
        with patch('app.AppMismatchDialog') as factory:
            factory.return_value.choice.return_value = 'change'
            w.toggle()
        self.assertTrue(w.armed)
        self.assertEqual(w.path, old_path)
        saved = Profile.read(read_json(w.path))
        self.assertEqual(saved.target_program, OTHER)
        self.assertEqual(saved.rows[0].x, 321)
        self.assertEqual(saved.resolution, (1280,720))

    def test_profile_change_save_failure_preserves_original_identity(self):
        from concurrent.futures import Future
        from persistence import SaveResult
        w = self.window
        w.save()
        old_path, old_bytes = w.path, w.path.read_bytes()
        self.identity.return_value = OTHER
        future = Future()
        future.set_result(SaveResult(False, error='disk full'))
        with patch('app.AppMismatchDialog') as factory, patch.object(w.store, 'save', return_value=future), patch.object(w, 'error') as error:
            factory.return_value.choice.return_value = 'change'
            w.toggle()
        self.assertIn('disk full', error.call_args.args[0])
        self.assertFalse(w.armed)
        self.assertEqual(w.profile.target_program, GAME)
        self.assertEqual(w.path, old_path)
        self.assertEqual(w.path.read_bytes(), old_bytes)

    def test_different_app_and_size_require_both_decisions(self):
        self.identity.return_value = OTHER
        self.size.return_value = (1920,1080)
        with patch('app.AppMismatchDialog') as app, patch('app.SizeMismatchDialog') as size:
            app.return_value.choice.return_value = 'change'
            size.return_value.choice.return_value = 'cancel'
            self.window.toggle()
        app.assert_called_once()
        size.assert_called_once()
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
            factory.return_value.choice.return_value = 'cancel'
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
            factory.return_value.choice.return_value = 'change'
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
             patch('native.valid_window',return_value=True), patch('interactive_resize.is_hung',return_value=False), patch('interactive_resize.refresh',return_value=''), \
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
        self.assertTrue(dialog.cancel_button.isDefault())
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertEqual(dialog.choice(), 'cancel')
        self.assertEqual(set(dialog.options), {'new', 'copy', 'change'})
        dialog.options['copy'].setChecked(True)
        self.assertTrue(dialog.apply_button.isEnabled())
        QTimer.singleShot(0, dialog.apply_button.click)
        dialog.exec()
        self.assertEqual(dialog.choice(), 'copy')
        dialog.deleteLater()
        dialog = SizeMismatchDialog((1280,720), (1920,1080), self.window)
        self.assertTrue(dialog.resize_button.isEnabled())
        self.assertIs(dialog.defaultButton(), dialog.cancel_button)
        self.assertTrue(dialog.convert_button.isEnabled())
        QTimer.singleShot(0, dialog.convert_button.click)
        dialog.exec()
        self.assertEqual(dialog.choice(), 'auto')
        dialog.deleteLater()

    def test_settings_dialog_roundtrip_preserves_existing_values(self):
        from widgets import SettingsDialog
        settings = Settings(auto_pause=False, click_hold_ms=7, theme='Light')
        dialog = SettingsDialog(settings, self.window)
        self.assertEqual(dialog.value(), settings)
        dialog.deleteLater()

    def test_app_replaced_during_confirmation_cannot_start_under_old_approval(self):
        self.identity.side_effect = [OTHER, {**OTHER, 'exe': 'New.exe'}]
        with patch('app.AppMismatchDialog') as factory:
            factory.return_value.choice.return_value = 'change'
            self.window.toggle()
        self.assertFalse(self.window.armed)
        self.assertEqual(self.window.profile.target_program, GAME)


    def test_oversized_profile_cannot_be_silently_clamped_by_resize_fields(self):
        dialog = SizeMismatchDialog((20000, 10000), (1920, 960), self.window)
        self.assertFalse(dialog.resize_button.isEnabled())
        self.assertTrue(dialog.convert_button.isEnabled())
        dialog.deleteLater()

    def test_new_profile_always_stops_input_even_when_auto_pause_is_disabled(self):
        w = self.window
        w.settings.auto_pause = False
        w.armed = True
        with patch.object(QFileDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(QFileDialog, 'selectedFiles', return_value=[str(w.path.with_name('new.json'))]), \
             patch.object(w.native, 'windows', return_value=[]), \
             patch.object(w.engine, 'stop', wraps=w.engine.stop) as stop:
            w.new_profile()
        self.assertFalse(w.armed)
        self.assertIsNone(w.target)
        stop.assert_called()
        self.assertEqual(w.profile.target_program, {})

    def test_cancel_new_profile_preserves_existing_profile(self):
        w = self.window
        before = w.profile
        with patch.object(QFileDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(QFileDialog, 'selectedFiles', return_value=[str(w.path.with_name('new.json'))]), \
             patch.object(w, 'prepare_profile_change', return_value=False):
            w.new_profile()
        self.assertIs(w.profile, before)
        self.assertEqual(w.target, 123)

    def test_dropdown_refresh_preserves_unselected_and_other_app(self):
        w = self.window
        self.identity.side_effect = lambda hwnd: GAME if hwnd == 123 else OTHER
        with patch.object(w.native, 'windows', return_value=[(123,'Game'),(456,'Other')]):
            for target in (None, 456):
                w.target = target
                w.refresh_windows()
                self.assertEqual(w.target, target)
                self.assertEqual(w.targets.currentData(), target)
                self.assertEqual(w.profile.target_program, GAME)
            w.target = None
            w.refresh_windows(restore_saved=True)
            self.assertEqual(w.target, 123)

    def test_missing_selected_app_does_not_restore_another_app_on_dropdown_open(self):
        w = self.window
        w.target = 456
        w.armed = True
        with patch.object(w.native, 'windows', return_value=[(123,'Game')]):
            w.refresh_windows()
        self.assertIsNone(w.target)
        self.assertFalse(w.armed)
        self.assertEqual(w.profile.target_program, GAME)

    def test_coordinate_capture_confirms_and_saves_app_and_point_together(self):
        w = self.window
        self.identity.return_value = OTHER
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
        self.assertIn('최소화', w.resize_button.text())
        self.assertNotIn('0 × 0', w.resize_button.text())
        self.size.side_effect = None
        w.tick()
        self.assertTrue(w.armed)
        self.assertNotIn('최소화', w.resize_button.text())
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
                        app_prompt.return_value.choice.return_value = 'change'
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
             patch('native.valid_window',return_value=True), patch('interactive_resize.is_hung',return_value=False), patch('interactive_resize.refresh',return_value=''):
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
        with patch('app.SizeMismatchDialog') as prompt, patch.object(w.store,'settings_async',side_effect=OSError('save failed')), patch.object(w,'error') as error:
            prompt.return_value.choice.return_value = 'auto'
            prompt.return_value.skip_warning.isChecked.return_value = True
            w.toggle()
        error.assert_called_once_with('save failed')
        self.assertFalse(w.armed)
        self.assertIsNone(w.session.plan)
        self.assertFalse(w.settings.auto_convert_on_start)

    def test_auto_preference_keeps_app_warning_(self):
        w = self.window
        w.settings.auto_convert_on_start = True
        self.size.return_value = (1920,1080)
        self.identity.return_value = OTHER
        with patch('app.AppMismatchDialog') as app_prompt, patch('app.SizeMismatchDialog') as size_prompt:
            app_prompt.return_value.choice.return_value = 'cancel'
            w.toggle()
        app_prompt.assert_called_once()
        size_prompt.assert_not_called()
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
        self.assertIn('경고',editor.validation.text())
        self.assertFalse(editor.validation.property('error'))

    def test_script_reverse_capture_can_save_after_actual_size_change(self):
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
        self.assertIsNone(w.script_editor)
        self.assertEqual(row.script,'click 640,360')
        w.flush_pending_saves()
        saved = Profile.read(read_json(w.path))
        self.assertEqual(saved.rows[0].script,'click 640,360')
        self.assertEqual(saved.resolution,(1280,720))
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

    def test_unselected_start_finds_saved_app_and_checks_size(self):
        w = self.window
        w.target = None
        with patch.object(w.native, 'windows', return_value=[(123,'Game')]), patch('app.AppMismatchDialog') as mismatch:
            w.dispatch('pause', '')
        self.assertTrue(w.armed)
        self.assertEqual(w.target, 123)
        self.assertEqual(w.targets.currentData(), 123)
        mismatch.assert_not_called()
        w.stop()
        w.target = None
        self.size.return_value = (1920,1080)
        with patch.object(w.native, 'windows', return_value=[(123,'Game')]), patch('app.SizeMismatchDialog') as size:
            size.return_value.choice.return_value = 'cancel'
            w.toggle()
        size.assert_called_once()
        self.assertFalse(w.armed)

    def test_unselected_missing_or_ambiguous_app_does_not_start(self):
        w = self.window
        for windows in ([], [(123,'Game'),(456,'Game')]):
            w.target = None
            with patch.object(w.native, 'windows', return_value=windows):
                w.toggle()
            self.assertIsNone(w.target)
            self.assertFalse(w.armed)
            self.assertEqual(w.profile.target_program, GAME)
            self.assertIn('앱을 선택', w.statusBar().currentMessage())


    def test_new_and_copied_profiles_keep_original_file_and_stay_stopped(self):
        w = self.window
        w.profile.rows[0].x = 321
        w.profile.rows[0].memo = 'keep memo'
        w.profile.rows[1].action_mode = 'script'
        w.profile.rows[1].script = 'click 100,200'
        w.save()
        original, old_path, old_bytes = w.profile, w.path, w.path.read_bytes()
        self.identity.return_value = OTHER
        for choice in ('new', 'copy'):
            w.profile, w.path = original, old_path
            with patch('app.AppMismatchDialog') as prompt:
                prompt.return_value.choice.return_value = choice
                w.toggle()
            self.assertFalse(w.armed)
            self.assertIsNone(w.session.plan)
            self.assertNotEqual(w.path, old_path)
            self.assertEqual(old_path.read_bytes(), old_bytes)
            saved = Profile.read(read_json(w.path))
            self.assertEqual(saved.target_program, OTHER)
            self.assertEqual(w.settings.last_profile, w.path.name)
            if choice == 'new':
                self.assertEqual([r.move_hk for r in saved.rows], ['q','w','e'])
                self.assertTrue(all(r.x == 0 and r.y == 0 and not r.memo and not r.script for r in saved.rows))
                self.assertIsNone(saved.resolution)
            else:
                self.assertEqual(saved.resolution, (1280,720))
                self.assertEqual(saved.rows[0].x, 321)
                self.assertEqual(saved.rows[0].memo, 'keep memo')
                self.assertEqual(saved.rows[1].script, 'click 100,200')
                saved.rows[0].x = 999
                self.assertEqual(original.rows[0].x, 321)

    def test_new_profile_save_failure_keeps_original_state_and_file(self):
        from concurrent.futures import Future
        from persistence import SaveResult
        w = self.window
        w.save()
        original, old_path, old_bytes = w.profile, w.path, w.path.read_bytes()
        self.identity.return_value = OTHER
        result = Future()
        result.set_result(SaveResult(False, error='disk full'))
        with patch('app.AppMismatchDialog') as prompt, patch.object(w.store, 'save', return_value=result), patch.object(w, 'error') as error:
            prompt.return_value.choice.return_value = 'copy'
            w.toggle()
        self.assertIs(w.profile, original)
        self.assertEqual(w.path, old_path)
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertFalse(w.armed)
        self.assertIn('disk full', error.call_args.args[0])

    def test_new_profile_selection_change_during_prompt_cannot_write(self):
        w = self.window
        self.identity.return_value = OTHER
        before = w.profile.export()
        with patch('app.AppMismatchDialog') as prompt, patch.object(w.store, 'save') as save:
            prompt.return_value.choice.return_value = 'new'
            prompt.return_value.exec.side_effect = lambda: setattr(w, 'target', None)
            w.toggle()
        save.assert_not_called()
        self.assertEqual(w.profile.export(), before)
        self.assertFalse(w.armed)

    def test_new_profile_cancel_unsaved_changes_preserves_current_profile(self):
        w = self.window
        self.identity.return_value = OTHER
        original = w.profile
        with patch('app.AppMismatchDialog') as prompt, patch.object(w, 'confirm_changes', return_value=False), patch.object(w.store, 'save') as save:
            prompt.return_value.choice.return_value = 'new'
            w.toggle()
        self.assertIs(w.profile, original)
        self.assertEqual(w.profile.target_program, GAME)
        self.assertFalse(w.armed)
        save.assert_not_called()


    def test_row_capture_reverse_scales_during_automatic_run_and_after_pause(self):
        w = self.window
        w.settings.auto_convert_on_start = True
        self.size.return_value = (1920,1080)
        untouched = w.profile.rows[2]
        untouched.x, untouched.y, untouched.script = 120,240,'click 320,180'
        original = asdict(untouched)
        w.toggle()
        self.assertTrue(w.armed)
        self.assertEqual(w.coordinate_transform, ((1280,720),(1920,1080)))
        with patch.object(w.native,'bounds',return_value=(0,0,1920,1080)), \
             patch.object(w.native,'cursor',side_effect=[(900,600),(300,450)]), \
             patch.object(w,'convert_dialog') as conversion:
            for row,mode,expected in zip(w.profile.rows[:2],
                    ('move_only','move_only'), ((600,400),(200,300))):
                row.action_mode = mode
                w.capture(row.id)
                self.assertEqual((row.x,row.y), expected)
                self.assertEqual(w.profile.resolution, (1280,720))
                self.assertFalse(w.armed)
                self.assertIsNone(w.coordinate_transform)
            conversion.assert_not_called()
        self.assertEqual(asdict(untouched), original)
        w.flush_pending_saves()
        saved = Profile.read(read_json(w.path))
        self.assertEqual(saved.resolution, (1280,720))
        self.assertEqual([(row.x,row.y) for row in saved.rows[:2]], [(600,400),(200,300)])
        self.assertEqual(asdict(saved.rows[2]), original)

    def test_reverse_row_capture_keeps_running_plan_when_auto_pause_is_off(self):
        w = self.window
        w.settings.auto_convert_on_start = True
        w.settings.auto_pause = False
        self.size.return_value = (1920,1080)
        w.toggle()
        plan = w.session.plan
        row = w.profile.rows[0]
        with patch.object(w.native,'cursor',return_value=(900,600)), \
             patch.object(w.native,'bounds',return_value=(0,0,1920,1080)):
            w.capture(row.id)
        self.assertTrue(w.armed)
        self.assertIs(w.session.plan, plan)
        self.assertEqual(w.profile.resolution, (1280,720))
        command = plan.command(row,w.settings)
        self.assertEqual((command.x,command.y), (900,600))
        w.flush_pending_saves()
        self.assertEqual(Profile.read(read_json(w.path)).resolution, (1280,720))

    def test_reverse_row_capture_rounds_edges_and_allows_ratio_warning(self):
        w = self.window
        row = w.profile.rows[0]
        for size,point,expected,warning in (
                ((1835,1032),(1834,1031),(1279,719),False),
                ((1280,960),(640,480),(640,360),True)):
            with self.subTest(size=size):
                self.size.return_value = size
                with patch.object(w.native,'cursor',return_value=point), \
                     patch.object(w.native,'bounds',return_value=(0,0,*size)), \
                     patch.object(w,'convert_dialog') as conversion:
                    w.capture(row.id)
                conversion.assert_not_called()
                self.assertEqual((row.x,row.y), expected)
                self.assertEqual(w.profile.resolution, (1280,720))
                self.assertEqual('경고:' in w.statusBar().currentMessage(), warning)

    def test_reverse_row_capture_cancel_preserves_baseline_points_and_file(self):
        w = self.window
        w.save()
        before, before_file = w.profile.export(), w.path.read_bytes()
        self.size.return_value = (1920,1080)
        self.identity.return_value = OTHER
        with patch('app.CoordinateAppDialog') as prompt, \
             patch.object(w.native,'cursor',return_value=(900,600)), \
             patch.object(w.native,'bounds',return_value=(0,0,1920,1080)), \
             patch.object(w,'convert_dialog') as conversion:
            prompt.return_value.confirmed.return_value = False
            w.capture(w.profile.rows[0].id)
        prompt.assert_called_once()
        conversion.assert_not_called()
        w.flush_pending_saves()
        self.assertEqual(w.profile.export(), before)
        self.assertEqual(w.path.read_bytes(), before_file)

    def test_reverse_row_capture_resize_during_confirmation_does_not_save(self):
        w = self.window
        w.save()
        before, before_file = w.profile.export(), w.path.read_bytes()
        self.size.side_effect = [(1920,1080),(1600,900)]
        self.identity.return_value = OTHER
        with patch('app.CoordinateAppDialog') as prompt, \
             patch.object(w.native,'cursor',return_value=(900,600)), \
             patch.object(w.native,'bounds',return_value=(0,0,1920,1080)):
            prompt.return_value.confirmed.return_value = True
            w.capture(w.profile.rows[0].id)
        w.flush_pending_saves()
        self.assertEqual(w.profile.export(), before)
        self.assertEqual(w.path.read_bytes(), before_file)
        self.assertIn('화면 크기가 변경',w.statusBar().currentMessage())

    def test_reverse_row_capture_basis_change_during_confirmation_does_not_write(self):
        w = self.window
        w.save()
        before_file = w.path.read_bytes()
        self.size.return_value = (1920,1080)
        self.identity.return_value = OTHER
        row = w.profile.rows[0]
        before_point = row.x,row.y
        with patch('app.CoordinateAppDialog') as prompt, \
             patch.object(w.native,'cursor',return_value=(900,600)), \
             patch.object(w.native,'bounds',return_value=(0,0,1920,1080)), \
             patch.object(w,'changed') as changed:
            prompt.return_value.confirmed.return_value = True
            prompt.return_value.exec.side_effect = lambda: setattr(w.profile,'resolution',(800,600))
            w.capture(row.id)
        changed.assert_not_called()
        self.assertEqual((row.x,row.y), before_point)
        self.assertEqual(w.profile.target_program, GAME)
        self.assertEqual(w.profile.resolution,(800,600))
        self.assertEqual(w.path.read_bytes(),before_file)
        self.assertIn('프로필이 변경',w.statusBar().currentMessage())

    def test_reverse_row_capture_outside_app_does_not_change_profile(self):
        w = self.window
        self.size.return_value = (1920,1080)
        for point in ((1920,500),(-1,500),(300,1080)):
            with self.subTest(point=point):
                before = w.profile.export()
                with patch.object(w.native,'cursor',return_value=point), \
                     patch.object(w.native,'bounds',return_value=(0,0,1920,1080)), \
                     patch.object(w,'changed') as changed:
                    w.capture(w.profile.rows[0].id)
                changed.assert_not_called()
                self.assertEqual(w.profile.export(), before)
                self.assertIn('대상 영역 안',w.statusBar().currentMessage())
