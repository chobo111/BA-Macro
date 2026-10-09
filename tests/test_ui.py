import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QFileDialog, QPushButton
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint
from domain import Profile, read_json
from app import MainWindow, KeyDialog, SettingsDialog, ResizeDialog, resize_window
from script_editor import ScriptEditor
from domain import MODES
from execution_plan import ExecutionPlan


class UiTests(unittest.TestCase):
    def test_profile_menu_actions_and_rename_preserve_editor_draft(self):
        from profile_dialogs import RenameProfileDialog
        w = self.window
        menus = {action.text(): action.menu() for action in w.menuBar().actions()}
        self.assertIn('프로필 이름 변경…', [a.text() for a in menus['파일'].actions()])
        self.assertIn('현재 프로필 정보', [a.text() for a in menus['정보'].actions()])
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        editor.editor.setPlainText('sleep 123')
        original, profile = w.path, w.profile
        w.set_value(w.profile.rows[1].id, 'memo', 'latest edit')
        def accept(dialog):
            dialog.name.setText('Renamed')
            return QDialog.DialogCode.Accepted
        with patch.object(RenameProfileDialog, 'exec', accept):
            w.rename_profile()
        self.assertEqual(w.path.name, 'Renamed.json')
        self.assertFalse(original.exists())
        self.assertIs(w.profile, profile)
        self.assertIs(w.script_editor, editor)
        self.assertEqual(editor.editor.toPlainText(), 'sleep 123')
        self.assertEqual(read_json(w.path)['macros'][1]['memo'], 'latest edit')
        self.assertEqual(w.settings.last_profile, 'Renamed.json')
        self.assertIn('미저장', dict(w.profile_details())['저장 상태'])
        w.finish_script_editor()
        w.set_value(w.profile.rows[0].id, 'memo', 'after rename')
        w.flush_pending_saves()
        self.assertFalse(original.exists())
        self.assertEqual(read_json(w.path)['macros'][0]['memo'], 'after rename')

    def test_profile_rename_cancel_and_collision_keep_path(self):
        from profile_dialogs import RenameProfileDialog
        w = self.window
        original = w.path
        with patch.object(RenameProfileDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
            w.rename_profile()
        self.assertEqual(w.path, original)
        destination = original.with_name('existing.json')
        destination.write_text('keep', encoding='utf-8')
        def accept(dialog):
            dialog.name.setText('existing')
            return QDialog.DialogCode.Accepted
        with patch.object(RenameProfileDialog, 'exec', accept):
            w.rename_profile()
        self.assertEqual(w.path, original)
        self.assertIn('이름 변경 실패', w.statusBar().currentMessage())
        self.assertEqual(destination.read_text(encoding='utf-8'), 'keep')

    def test_profile_rename_partial_settings_failure_follows_moved_file(self):
        from profile_dialogs import RenameProfileDialog
        w = self.window
        original = w.path
        def accept(dialog):
            dialog.name.setText('renamed')
            return QDialog.DialogCode.Accepted
        with patch.object(RenameProfileDialog, 'exec', accept), patch.object(w.store, '_settings', side_effect=OSError('locked')):
            w.rename_profile()
        self.assertEqual(w.path.name, 'renamed.json')
        self.assertTrue(w.path.exists())
        self.assertFalse(original.exists())
        self.assertEqual(w.settings_error, 'locked')
        self.assertIn('최근 프로필 정보 저장 실패', w.statusBar().currentMessage())

    def test_profile_info_distinguishes_saved_app_and_current_app(self):
        from profile_dialogs import ProfileInfoDialog
        from domain import WindowSizeUnavailable
        w = self.window
        w.profile.target_program = {'title': '<Game>', 'exe': 'Game.exe', 'class': 'Unity'}
        w.profile.resolution = (1920, 1080)
        w.target = 123
        w.session.start(ExecutionPlan(123, (1920,1080), (1280,720), automatic=True))
        plan = w.session.plan
        before = w.profile.export()
        with patch.object(w.native, 'size', return_value=(1280,720)):
            details = dict(w.profile_details())
            self.assertEqual(details['기준 크기'], '1920 × 1080')
            self.assertEqual(details['현재 크기'], '1280 × 720')
            self.assertIn('<Game>', details['지정 앱'])
            self.assertIn('1920 × 1080 → 1280 × 720', details['자동 변환'])
            with patch.object(ProfileInfoDialog, 'exec', return_value=QDialog.DialogCode.Rejected), patch.object(w, 'save') as save:
                w.profile_info_dialog()
            save.assert_not_called()
        self.assertIs(w.session.plan, plan)
        self.assertEqual(w.profile.export(), before)
        with patch.object(w.native, 'size', side_effect=WindowSizeUnavailable()):
            self.assertEqual(dict(w.profile_details())['현재 크기'], '최소화')
        w.target = None
        with patch.object(w.native, 'size') as size:
            self.assertEqual(dict(w.profile_details())['현재 크기'], '—')
        size.assert_not_called()
        w.stop()

    def test_script_coordinate_capture_pauses_for_modal_and_popup_then_resumes(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMenu
        w = self.window
        w.timer.stop()
        w.target = 123
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        self.qt.processEvents()
        self.assertTrue(editor.capture_enabled.is_set())
        states = []
        dialog = QDialog(w)
        def inspect_modal():
            states.append(editor.capture_enabled.is_set())
            dialog.reject()
        QTimer.singleShot(0, inspect_modal)
        dialog.exec()
        self.qt.processEvents()
        self.assertEqual(states, [False])
        self.assertTrue(editor.capture_enabled.is_set())
        dialog.deleteLater()
        menu = QMenu(w)
        menu.addAction('Test')
        menu.popup(w.mapToGlobal(QPoint(20,20)))
        self.qt.processEvents()
        self.assertFalse(editor.capture_enabled.is_set())
        menu.close()
        self.qt.processEvents()
        self.assertTrue(editor.capture_enabled.is_set())
        menu.deleteLater()
        w.finish_script_editor()
        self.assertFalse(editor.capture_enabled.is_set())

    def test_invalid_resolution_open_does_not_replace_current_profile(self):
        from domain import atomic_json
        w = self.window
        original, path = w.profile, w.path
        invalid = path.with_name('invalid.json')
        data = Profile.default().export()
        data['target_resolution'] = '1920×1080'
        atomic_json(invalid, data)
        before = invalid.read_bytes()
        with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(invalid), '')), patch.object(w, 'error') as error:
            w.open_profile()
        self.assertIs(w.profile, original)
        self.assertEqual(w.path, path)
        self.assertIn('해상도', error.call_args.args[0])
        self.assertEqual(invalid.read_bytes(), before)

    def test_target_matches_filename_case_and_rejects_ambiguous_titles(self):
        native = self.window.native
        saved = {'exe': 'ChatGPT.exe',
                 'class': 'Chrome_WidgetWin_1', 'title': 'ChatGPT'}
        updated = {**saved, 'exe': 'CHATGPT.EXE'}
        with patch.object(native, 'window_identity', return_value=updated):
            self.assertEqual(native.find_target(saved, [(1, 'ChatGPT')]), 1)
            self.assertEqual(native.find_target(saved, [(1, 'ChatGPT'), (2, 'ChatGPT')]), 0)
        identities = {1: updated, 2: {**updated, 'title': 'Other window'}}
        with patch.object(native, 'window_identity', side_effect=identities.get):
            self.assertEqual(native.find_target(saved, [(1, 'ChatGPT'), (2, 'Other window')]), 1)

    def test_delete_selects_previous_survivor_and_blank_hotkey(self):
        from PySide6.QtCore import QItemSelectionModel
        w = self.window
        previous = w.profile.rows[1].id
        w.table.selectRow(2)
        w.delete_rows()
        self.assertEqual(w.table.currentRow(), 1)
        self.assertEqual(w.profile.rows[w.table.currentRow()].id, previous)
        w.table.selectRow(0)
        w.delete_rows()
        self.assertEqual(w.table.currentRow(), 0)
        for _ in range(4):
            w.add_row()
        self.assertEqual(w.table.cellWidget(4, 0).text(), '')
        previous = w.profile.rows[1].id
        w.table.clearSelection()
        for index in (2, 4):
            w.table.selectionModel().select(w.table.model().index(index, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        w.delete_rows()
        self.assertEqual(w.profile.rows[w.table.currentRow()].id, previous)

    def test_info_menu_editor_help_and_corner_grip(self):
        from script_help import ScriptHelpDialog, help_html
        from version import VERSION
        from PySide6.QtWidgets import QTextBrowser
        w = self.window
        menus = {action.text(): action.menu() for action in w.menuBar().actions()}
        self.assertIn('정보', menus)
        self.assertNotIn('도움말', menus)
        self.assertEqual(menus['정보'].actions()[0].text(), '프로그램 정보')
        self.assertNotIn('변경 기록', [action.text() for action in menus['정보'].actions()])
        with patch.object(QMessageBox, 'information') as about:
            w.about_dialog()
        self.assertEqual(about.call_args.args[1], '프로그램 정보')
        self.assertIn(VERSION, about.call_args.args[2])
        editor = ScriptEditor('', w.native, 0, w)
        editor.show()
        for width, height in ((500, 430), (700, 520)):
            editor.resize(width, height)
            self.qt.processEvents()
            grip = editor.size_grip.geometry()
            self.assertEqual(grip.right(), editor.width() - 1)
            self.assertEqual(grip.bottom(), editor.height() - 1)
        self.assertIn('?', [button.text() for button in editor.findChildren(QPushButton)])
        help_dialog = ScriptHelpDialog(editor)
        text = help_dialog.findChild(QTextBrowser).toPlainText()
        for command in ('move', 'click', 'press', 'release', 'switch', 'mouse_press',
                        'mouse_release', 'mouse_switch', 'key_press', 'key_release', 'key_switch',
                        'text', 'sleep', 'curve', 'curve_first_time', 'curve_last_time',
                        'curve_release', 'release_all', 'loop', 'loop_end', 'release_actions'):
            self.assertIn(command, text)
        self.assertIn('각 좌표 명령 실행 시', text)
        help_dialog.deleteLater()
        editor.reject()
        editor.deleteLater()

    def test_partial_save_does_not_mark_profile_unsaved(self):
        from domain import atomic_json
        w = self.window
        w.settings.theme = 'Light'
        def fail_settings(path, data):
            if Path(path) == w.settings_path:
                raise OSError('settings unavailable')
            atomic_json(path, data)
        with patch('persistence.atomic_json', side_effect=fail_settings):
            w.set_value(w.profile.rows[0].id, 'memo', 'saved profile')
            w.flush_pending_saves()
        self.assertFalse(w.dirty)
        self.assertIn('프로필 저장 완료', w.statusBar().currentMessage())
        self.assertIn('settings unavailable', w.settings_error)
        self.assertEqual(read_json(w.path)['macros'][0]['memo'], 'saved profile')
        w.persist_settings()
        self.assertEqual(w.settings_error, '')

    def test_autosave_is_nonblocking_and_ignores_stale_completion(self):
        import threading
        from domain import atomic_json
        w = self.window
        entered, release = threading.Event(), threading.Event()
        def delayed(path, data):
            if Path(path) == w.path and not entered.is_set():
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test timeout')
            atomic_json(path, data)
        with patch('persistence.atomic_json', side_effect=delayed):
            w.set_value(w.profile.rows[0].id, 'memo', 'first')
            try:
                self.assertTrue(entered.wait(1))
                old_revision = w.profile_saves.revision
                w.set_value(w.profile.rows[0].id, 'memo', 'intermediate')
                w.set_value(w.profile.rows[0].id, 'memo', 'second')
                self.assertTrue(w.dirty)
                self.qt.processEvents()
                self.assertTrue(w.dirty)
            finally:
                release.set()
            w.flush_pending_saves()
        from persistence import SaveResult
        w.save_completed(old_revision, SaveResult(False, error='old failure'))
        self.assertFalse(w.dirty)
        self.assertEqual(read_json(w.path)['macros'][0]['memo'], 'second')

    def test_dispatch_passes_compiled_script(self):
        from script_language import compile_script
        w = self.window
        row = w.profile.rows[0]
        row.action_mode, row.script = 'script', 'sleep 1'
        w.target = 123
        w.session.start(ExecutionPlan(w.target, w.profile.resolution, None, display_id=w.native.display_id))
        with patch('execution_plan.compile_script', wraps=compile_script) as compile_once, patch.object(w.engine, 'submit', return_value=True) as submit, patch.object(w.native, 'active', return_value=True):
            w.dispatch('move', row.id)
        compile_once.assert_called_once_with(row.script)
        self.assertIsNotNone(submit.call_args.args[0].program)

    def saved(self, path):
        self.window.flush_pending_saves()
        return read_json(path)

    def test_hotkey_dialog_duplicate_cancel_and_save(self):
        w = self.window
        row = w.profile.rows[0]
        with patch('app.KeyDialog') as dialog, patch.object(QMessageBox, 'warning') as warning:
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.value.return_value = 'w'
            w.edit_key(row.id)
            self.assertEqual(row.move_hk, 'q')
            self.assertIn('중복', w.statusBar().currentMessage())
            warning.assert_not_called()
            dialog.return_value.value.return_value = 'ctrl+r'
            w.edit_key(row.id)
            self.assertEqual(Profile.read(self.saved(w.path)).rows[0].move_hk, 'ctrl+r')
            dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
            dialog.return_value.value.return_value = 'ctrl+t'
            w.edit_key(row.id)
            self.assertEqual(row.move_hk, 'ctrl+r')
            dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog.return_value.value.return_value = ''
            w.edit_key(row.id)
            self.assertEqual(row.move_hk, '')
        self.assertIsInstance(w.table.cellWidget(0, 0), QPushButton)
        self.assertFalse(w.modal)

    def test_log_expires_and_new_row_has_empty_memo(self):
        w = self.window
        w.add_row()
        self.assertEqual(w.profile.rows[-1].memo, '')
        self.assertEqual(Profile.read(self.saved(w.path)).rows[-1].memo, '')
        w.statusBar().showMessage('잠시 안내')
        self.assertEqual(w.statusBar().currentMessage(), '잠시 안내')
        QTest.qWait(4200)
        self.assertEqual(w.statusBar().currentMessage(), '')

    def test_editor_covers_body_and_exposes_main_menu(self):
        from PySide6.QtWidgets import QLabel
        w = self.window
        w.statusBar().showMessage('메인 로그')
        dialog = ScriptEditor('', w.native, 0, w)
        dialog.show()
        self.qt.processEvents()
        self.assertEqual(dialog.geometry(), dialog.parent_area())
        self.assertFalse(dialog.isWindow())
        self.assertFalse(hasattr(dialog, "titlebar"))
        self.assertGreater(dialog.y(), w.menuBar().geometry().bottom())
        self.assertTrue(dialog.validation.isHidden())
        self.assertFalse(hasattr(dialog, 'position'))
        self.assertNotIn('드래그로 이동', [label.text() for label in dialog.findChildren(QLabel)])
        dialog.log_timer.setInterval(20)
        dialog.log('오류 안내', error=True)
        self.assertFalse(dialog.validation.isHidden())
        for _ in range(50):
            if dialog.validation.isHidden():
                break
            QTest.qWait(20)
        self.assertTrue(dialog.validation.isHidden())
        dialog.reject()
        dialog.deleteLater()

    def test_current_settings_options(self):
        from dataclasses import asdict
        from domain import Settings
        settings = Settings.read({})
        self.assertTrue(settings.auto_pause)
        self.assertEqual(settings.pause_hk, 'ctrl+`')
        self.assertEqual(Settings.read({'pause_hk': 'ctrl+tab'}).pause_hk, 'ctrl+tab')
        self.assertNotIn('auto_fire', asdict(settings))
        self.assertNotIn('show_realtime', asdict(settings))
        dialog = SettingsDialog(settings, self.window)
        self.assertEqual(set(dialog.checks), {'auto_pause', 'exclusive_hook', 'return_cursor', 'auto_convert_on_start'})
        dialog.checks['auto_pause'].setChecked(False)
        self.assertFalse(dialog.value().auto_pause)
        dialog.deleteLater()

    def test_optional_pause_and_modal_shortcuts(self):
        w = self.window
        for automatic in (True, False):
            w.settings.auto_pause = automatic
            w.armed = True
            with patch('app.SettingsDialog') as dialog:
                dialog.return_value.exec.return_value = QDialog.DialogCode.Rejected
                w.settings_dialog()
            self.assertEqual(w.armed, not automatic)
        w.modal = True
        w.target = 99
        with patch.object(w.native, 'foreground_owned_by', return_value=False), patch.object(w.native, 'active', return_value=True):
            self.assertTrue(w.eligible('move'))
            self.assertFalse(w.eligible('capture_selected'))
            w.dispatch('pause', '')
            self.assertFalse(w.armed)
        w.modal = False
        w.settings.auto_pause = False
        w.armed = True
        w.set_value(w.profile.rows[0].id, 'memo', '계속 준비 상태')
        self.assertTrue(w.armed)

    def test_editor_rejects_unsupported_touch_commands_on_save(self):
        for source in ('click 10,20\nkey_press a', 'text "hello"', 'mouse_press right'):
            with self.subTest(source=source):
                dialog = ScriptEditor(source, self.window.native, 0, self.window)
                self.assertFalse(dialog.validate())
                self.assertTrue(dialog.validation.property('error'))
                dialog.deleteLater()

    def test_editor_templates_replace_and_validate_after_coordinates(self):
        dialog = ScriptEditor('old content', self.window.native, 0, self.window)
        self.assertEqual(dialog.templates.count(), 4)
        for index, curve in ((1, 'curve X,Y mouse'), (2, 'curve mouse X,Y'), (3, 'curve X,Y X,Y')):
            dialog.insert_template(index)
            source = dialog.editor.toPlainText()
            self.assertNotIn('old content', source)
            self.assertEqual(source.count('loop_end'), 1)
            self.assertIn(curve, source)
            self.assertFalse(dialog.validate())
            dialog.editor.setPlainText(source.replace('X,Y', '100,200'))
            self.assertTrue(dialog.validate())
        self.assertIn('저장', [b.text() for b in dialog.findChildren(QPushButton)])
        dialog.deleteLater()

    def test_editor_resize_sync_and_live_coordinates(self):
        w = self.window
        w.target = 0
        w.resize(520, 440)
        self.qt.processEvents()
        self.assertNotEqual(w.minimumWidth(), w.maximumWidth())
        dialog = ScriptEditor('', w.native, 0, w)
        dialog.show()
        self.qt.processEvents()
        self.assertEqual(dialog.size(), dialog.parent_area().size())
        w.resize(650, 480)
        self.qt.processEvents()
        self.assertEqual(dialog.size(), dialog.parent_area().size())
        dialog.resize(580, 450)
        self.qt.processEvents()
        self.assertEqual(dialog.size(), dialog.parent_area().size())
        self.assertEqual(dialog.panel.size(), dialog.size())
        with patch.object(w.native, 'cursor', return_value=(123, 456)):
            w.tick()
        self.assertFalse(hasattr(dialog, 'cursor_label'))
        self.assertIs(w.menuBar().cornerWidget(Qt.Corner.TopRightCorner), w.cursor_label)
        self.assertIn('123', w.cursor_label.text())
        position = w.cursor_label.mapToGlobal(QPoint())
        dialog.reject()
        self.qt.processEvents()
        self.assertTrue(w.timer.isActive())
        self.assertEqual(w.cursor_label.mapToGlobal(QPoint()), position)
        dialog.deleteLater()

    def test_remember_target_restore_and_missing_window(self):
        w = self.window
        identity = {'exe': 'Game.exe', 'class': 'GameWindow', 'title': 'Game'}
        with patch.object(w.native, 'windows', return_value=[(123, 'Game')]), patch.object(w.native, 'window_identity', return_value=identity):
            w.refresh_windows()
            w.targets.setCurrentIndex(w.targets.findData(123))
            self.assertEqual(Profile.read(self.saved(w.path)).target_program, identity)
            self.assertEqual(w.targets.currentText(), 'Game')
        w.target = None
        with patch.object(w.native, 'windows', return_value=[]):
            w.refresh_windows()
        self.assertIsNone(w.target)
        self.assertEqual(w.profile.target_program, identity)
        with patch.object(w.native, 'windows', return_value=[(456, 'Game')]), patch.object(w.native, 'window_identity', return_value=identity):
            w.refresh_windows(restore_saved=True)
            self.assertEqual(w.target, 456)
            self.assertFalse(w.armed)
            w.targets.setCurrentIndex(0)
        self.assertEqual(Profile.read(self.saved(w.path)).target_program, identity)
        # Changing selection alone must not erase the comparison baseline.

    def test_startup_restores_profile_target(self):
        from domain import atomic_json
        identity = {'exe': 'Game.exe', 'class': 'GameWindow', 'title': 'Old title'}
        with tempfile.TemporaryDirectory() as folder:
            profile = Profile.default()
            profile.target_program = identity
            atomic_json(Path(folder) / 'macro.json', profile.export())
            with patch('app.Windows.windows', return_value=[(456, 'New title')]), patch('app.Windows.window_identity', return_value={**identity, 'title': 'New title'}):
                other = MainWindow(data_dir=folder, hook_enabled=False)
                try:
                    self.assertEqual(other.target, 456)
                    self.assertFalse(other.armed)
                finally:
                    other.close()

    def test_target_restore_avoids_ambiguous_or_different_executable(self):
        native = self.window.native
        identity = {'exe': 'Game.exe', 'class': 'GameWindow', 'title': 'Game'}
        with patch.object(native, 'window_identity', return_value=identity):
            self.assertEqual(native.find_target(identity, [(1, 'Game'), (2, 'Game')]), 0)
        with patch.object(native, 'window_identity', return_value={**identity, 'exe': 'Other.exe'}):
            self.assertEqual(native.find_target(identity, [(1, 'Game')]), 0)

    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle("Fusion")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.window = MainWindow(data_dir=self.directory.name, hook_enabled=False)
        self.window.show()
        self.qt.processEvents()

    def tearDown(self):
        self.window.shutdown()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        self.directory.cleanup()

    def test_edit_reorder_save_and_reload(self):
        w = self.window
        self.assertFalse(w.armed)
        self.assertEqual(w.table.rowCount(), 3)
        w.armed = True
        w.table.cellWidget(0, 1).setValue(125)
        self.assertFalse(w.armed)
        w.flush_pending_saves()
        self.assertFalse(w.dirty)
        self.assertEqual(Profile.read(self.saved(w.path)).rows[0].x,125)
        w.table.item(0, 4).setText("첫 번째 스킬")
        rid = w.profile.rows[0].id
        w.move_row(0, 2)
        self.assertEqual(w.profile.rows[2].id, rid)
        w.path = Path(self.directory.name) / "profile.json"
        self.assertTrue(w.save())
        w.flush_pending_saves()
        self.assertFalse(w.dirty)
        saved = Profile.read(self.saved(w.path))
        self.assertEqual(saved.rows[2].x, 125)
        self.assertEqual(saved.rows[2].memo, "첫 번째 스킬")
        w.add_row()
        self.assertEqual(w.table.rowCount(), 4)
        w.delete_rows()
        self.assertEqual(w.table.rowCount(), 3)

    def test_capture_inactive_target_and_stops_run(self):
        w = self.window
        w.target = 99
        with patch.object(w.native, "window_identity", return_value={"exe":"Game.exe","class":"Game","title":"Game"}), patch.object(w.native, "active", return_value=False), patch.object(w.native, "exists", return_value=True), patch.object(w.native, "cursor", return_value=(400, 500)), patch.object(w.native, "bounds", return_value=(0, 0, 1920, 1080)), patch.object(w.native, "size", return_value=(1920, 1080)):
            self.assertTrue(w.eligible('capture_selected'))
            w.armed = True
            w.capture(w.profile.rows[0].id)
            self.assertEqual(w.profile.rows[0].x, 400)
            self.assertEqual(w.profile.resolution, (1920, 1080))
            self.assertFalse(w.armed)

    def test_dialogs_and_theme_render(self):
        w = self.window
        for theme in ("Dark", "Light", "System"):
            w.settings.theme = theme
            w.apply_theme()
            self.qt.processEvents()
            self.assertFalse(w.grab().isNull())
        dialog = SettingsDialog(w.settings, w)
        self.assertEqual(dialog.value().click_hold_ms, 50)
        self.assertEqual(dialog.value().drag_segment_duration_ms, 10)
        self.assertTrue(dialog.value().exclusive_hook)
        dialog.times['drag_segment_duration_ms'].setValue(25)
        self.assertEqual(dialog.value().drag_segment_duration_ms, 25)
        dialog.times["click_hold_ms"].setValue(-1)
        self.assertEqual(dialog.value().click_hold_ms, 1)
        dialog.deleteLater()

    def test_open_legacy_profile_rejected_without_replacing_current(self):
        w = self.window
        original, original_path = w.profile, w.path
        legacy = Path(self.directory.name) / "old.json"
        content = '{"macros":[{"coords":"80, 90","action_mode":"move_only"}]}'
        legacy.write_text(content, encoding="utf-8")
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(legacy), "")), \
             patch.object(w, "error") as error:
            w.open_profile()
        self.assertIs(w.profile, original)
        self.assertEqual(w.path, original_path)
        self.assertEqual(legacy.read_text(encoding="utf-8"), content)
        self.assertIn('version: 3', error.call_args.args[0])

    def test_cancel_close_retains_unsaved_changes(self):
        w = self.window
        with patch('persistence.atomic_json', side_effect=OSError('disk unavailable')):
            w.changed()
            w.flush_pending_saves()
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(w.confirm_changes())
        w.flush_pending_saves()
        self.assertTrue(w.dirty)

    def test_save_dialog_korean_buttons_and_actions(self):
        w = self.window
        with patch('persistence.atomic_json', side_effect=OSError('disk unavailable')):
            w.changed()
            w.flush_pending_saves()
        for answer, expected in ((QMessageBox.StandardButton.Save, True),
                                 (QMessageBox.StandardButton.Discard, True),
                                 (QMessageBox.StandardButton.Cancel, False)):
            def respond(dialog):
                self.assertEqual(dialog.button(QMessageBox.StandardButton.Save).text(), "저장")
                self.assertEqual(dialog.button(QMessageBox.StandardButton.Discard).text(), "저장 안 함")
                self.assertEqual(dialog.button(QMessageBox.StandardButton.Cancel).text(), "취소")
                return answer
            with patch.object(QMessageBox, "exec", respond), patch.object(w, "save", return_value=True) as save:
                self.assertEqual(w.confirm_changes(), expected)
                self.assertEqual(save.call_count, int(answer == QMessageBox.StandardButton.Save))

    def test_popup_refresh_retains_selected_window(self):
        w = self.window
        with patch.object(w.native, "windows", return_value=[(10, "게임")]):
            w.refresh_windows()
        w.targets.setCurrentIndex(w.targets.findData(10))
        with patch.object(w.native, "windows", return_value=[(20, "다른 창"), (10, "게임")]) as windows:
            w.targets.showPopup()
            windows.assert_called_once()
            self.assertEqual(w.targets.currentData(), 10)
            self.assertEqual(w.targets.count(), 4)
            w.targets.hidePopup()

    def test_memo_width_and_pin(self):
        w = self.window
        self.assertEqual([r.move_hk for r in w.profile.rows], ["q", "w", "e"])
        self.assertTrue(all(r.memo == "" for r in w.profile.rows))
        base = w.table.columnWidth(4)
        geometry = (w.size(), w.target_box.size(), w.toolbar_panel.size(),
                    w.pin_button.pos(), w.pause_button.pos())
        text = "긴 메모 내용 " * 30
        w.table.item(0, 4).setText(text)
        self.qt.processEvents()
        self.assertEqual(w.table.columnWidth(4), base)
        self.assertEqual(w.table.item(0, 4).toolTip(), text)
        self.assertEqual((w.size(), w.target_box.size(), w.toolbar_panel.size(),
                          w.pin_button.pos(), w.pause_button.pos()), geometry)
        w.table.item(0, 4).setText("")
        self.assertEqual(w.table.columnWidth(4), base)
        handle = int(w.winId())
        with patch.object(w.native, "topmost") as topmost, patch.object(w, "show") as show:
            QTest.mouseClick(w.pin_button, Qt.MouseButton.LeftButton)
            topmost.assert_called_with(handle, True)
            self.assertTrue(w.pin_button.isChecked())
            QTest.mouseClick(w.pin_button, Qt.MouseButton.LeftButton)
            topmost.assert_called_with(handle, False)
            self.assertFalse(w.pin_button.isChecked())
            show.assert_not_called()
            self.assertEqual(int(w.winId()), handle)

    def test_resize_presets_and_custom_values(self):
        dialog = ResizeDialog((1234, 678), self.window)
        self.assertEqual(dialog.mode, "직접 입력")
        self.assertEqual(dialog.value(), (1234, 678))
        for ratio, aspect in (("16:9", (16, 9)), ("4:3", (4, 3))):
            dialog.change_ratio(ratio)
            self.assertFalse(dialog.width_input.isEnabled())
            for index in range(dialog.presets.count()):
                dialog.presets.setCurrentIndex(index)
                w, h = dialog.value()
                self.assertEqual(w * aspect[1], h * aspect[0])
        dialog.change_ratio("직접 입력")
        self.assertTrue(dialog.width_input.isEnabled())
        self.assertEqual(dialog.value(), (1234, 678))
        dialog.deleteLater()

    def test_pause_key_works_while_paused(self):
        w = self.window
        w.target = 99
        with patch.object(w.native, "size", return_value=(1280,720)), patch.object(w.native, "exists", return_value=True), patch.object(w.native, "window_identity", return_value={"exe": "Game.exe", "class": "Game", "title": "Game"}):
            QTest.mouseClick(w.pause_button, Qt.MouseButton.LeftButton)
            self.assertTrue(w.armed)
            w.dispatch("pause", "")
            self.assertFalse(w.armed)
            w.dispatch("pause", "")
            self.assertTrue(w.armed)

    def test_fullscreen_start_capture_and_fixed_controls(self):
        w = self.window
        self.assertEqual(w.targets.currentText(), "미선택")
        w.targets.setCurrentIndex(w.targets.findData(0))
        self.assertEqual(w.targets.currentText(), "전체화면")
        w.toggle()
        self.assertTrue(w.armed)
        self.assertTrue(w.eligible("move"))
        with patch.object(w.native, "cursor", return_value=(-300, 150)), \
             patch.object(w.native, "bounds", return_value=(-1920, 0, 1920, 1080)), \
             patch.object(w.native, "size", return_value=(3840, 1080)):
            w.capture(w.profile.rows[0].id)
        self.assertEqual(w.profile.rows[0].x, -300)
        w.resize(480, 340)
        self.qt.processEvents()
        positions = (w.pin_button.pos(), w.pause_button.pos())
        w.resize(900, 340)
        w.cursor_label.setText("-99999, -99999")
        self.qt.processEvents()
        self.assertEqual((w.pin_button.pos(), w.pause_button.pos()), positions)

    def test_script_mode_span_and_editor_roundtrip(self):
        w = self.window
        row = w.profile.rows[0]
        w.table.cellWidget(0, 3).setCurrentIndex(list(MODES).index('script'))
        self.qt.processEvents()
        self.assertEqual(w.table.columnSpan(0, 1), 2)
        self.assertEqual(w.table.cellWidget(0, 1).text(), '스크립트 작성')
        dialog = ScriptEditor('', w.native, w.target, w)
        dialog.editor.setPlainText('loop 2\nclick mouse')
        self.assertFalse(dialog.validate())
        self.assertIn('loop_end', dialog.validation.text())
        script = 'click 123,456\nsleep 10\nmove mouse'
        dialog.editor.setPlainText(script)
        self.assertTrue(dialog.validate())
        dialog.save()
        self.assertEqual(dialog.source, script)
        row.script = script
        w.path = Path(self.directory.name) / 'script.json'
        self.assertTrue(w.save())
        loaded = Profile.read(self.saved(w.path))
        self.assertEqual(loaded.rows[0].script, script)
        w.table.cellWidget(0, 3).setCurrentIndex(list(MODES).index('move_only'))
        self.qt.processEvents()
        self.assertEqual(w.table.columnSpan(0, 1), 1)
        self.assertEqual(w.table.cellWidget(0, 1).maximum(), 99999)
        self.assertEqual(w.table.cellWidget(0, 2).minimum(), -99999)
        self.assertEqual(row.script, script)
        dialog.deleteLater()

    def test_editor_follows_main_window_and_captures_external_coordinates(self):
        w = self.window
        dialog = ScriptEditor('', w.native, 0, w)
        dialog.show()
        self.qt.processEvents()
        self.assertEqual(dialog.validation.text(), '')
        self.assertIn('우클릭', dialog.editor.placeholderText())
        self.assertNotIn('click 420', dialog.editor.placeholderText())
        self.assertEqual(dialog.size(), dialog.panel.size())
        self.assertLessEqual(dialog.panel.width(), 600)
        self.assertFalse(hasattr(dialog, 'titlebar'))
        before = dialog.mapToGlobal(QPoint())
        parent_before = w.pos()
        w.move(parent_before + QPoint(20, 20))
        self.qt.processEvents()
        self.assertEqual(dialog.mapToGlobal(QPoint()) - before, w.pos() - parent_before)
        self.assertEqual(dialog.geometry(), dialog.parent_area())
        with patch.object(w.native, 'cursor', return_value=(-123, 456)), \
             patch.object(w.native, 'bounds', return_value=(-1920, 0, 1920, 1080)):
            self.assertEqual(dialog.editor.toPlainText(), '')
            dialog.pick_coordinate()
            self.assertEqual(dialog.editor.toPlainText(), '-123,456')
            QTest.mouseClick(dialog, Qt.MouseButton.RightButton, pos=dialog.panel.geometry().center())
            self.assertEqual(dialog.editor.toPlainText(), '-123,456')
        with patch.object(w.native, 'cursor', return_value=(99999, 456)), \
             patch.object(w.native, 'bounds', return_value=(0, 0, 1920, 1080)):
            dialog.pick_coordinate()
            self.assertEqual(dialog.editor.toPlainText(), '-123,456')
        dialog.editor.setPlainText('click ')
        cursor = dialog.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        dialog.editor.setTextCursor(cursor)
        with patch.object(w.native, 'cursor', return_value=(123, 456)), \
             patch.object(w.native, 'bounds', return_value=(0, 0, 1920, 1080)):
            dialog.pick_coordinate()
            self.assertEqual(dialog.editor.toPlainText(), 'click 123,456')
        dialog.source = dialog.editor.toPlainText()
        dialog.reject()
        dialog.deleteLater()

    def test_portable_startup_and_missing_recent(self):
        from domain import atomic_json
        directory = Path(self.directory.name)
        self.assertTrue((directory / 'macro.json').is_file())
        self.assertEqual(self.saved(directory / 'setting.json')['last_profile'], 'macro.json')
        profile = Profile.default()
        profile.rows[0].memo = '기존 내용 보존'
        atomic_json(directory / 'macro.json', profile.export())
        atomic_json(directory / 'setting.json', {'last_profile': 'missing.json'})
        second = MainWindow(data_dir=directory, hook_enabled=False)
        try:
            self.assertEqual(second.profile.rows[0].memo, '기존 내용 보존')
            self.assertEqual(second.path, directory / 'macro.json')
            second.path = directory / 'recent.json'
            second.profile.rows[0].memo = '최근 프로필'
            self.assertTrue(second.save())
        finally:
            second.shutdown()
            second.close()
        third = MainWindow(data_dir=directory, hook_enabled=False)
        try:
            self.assertEqual(third.path, directory / 'recent.json')
            self.assertEqual(third.profile.rows[0].memo, '최근 프로필')
        finally:
            third.shutdown()
            third.close()

    def test_external_profile_copy_does_not_overwrite_local(self):
        from domain import atomic_json
        with tempfile.TemporaryDirectory() as external:
            w = self.window
            local = Path(self.directory.name) / 'macro.json'
            original = local.read_bytes()
            profile = Profile.default()
            profile.rows[0].memo = '외부 프로필'
            source = Path(external) / 'macro.json'
            atomic_json(source, profile.export())
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(source), '')):
                w.open_profile()
            recent = self.saved(w.settings_path)['last_profile']
            self.assertEqual(recent, 'macro_1.json')
            self.assertEqual(local.read_bytes(), original)
            w.profile.rows[0].memo = '수정'
            self.assertTrue(w.save())
            self.assertEqual(self.saved(w.settings_path)['last_profile'], recent)
            copied = Profile.read(self.saved(Path(self.directory.name) / recent))
            self.assertEqual(copied.rows[0].memo, '수정')

    def test_external_unsaved_edits_are_not_copied_by_settings(self):
        from domain import atomic_json
        with tempfile.TemporaryDirectory() as external:
            w = self.window
            source = Path(external) / 'outside.json'
            atomic_json(source, Profile.default().export())
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(source), '')):
                w.open_profile()
            w.profile.rows[0].memo = '저장하지 않은 변경'
            w.persist_settings()
            copy = Path(self.directory.name) / w.settings.last_profile
            self.assertEqual(Profile.read(self.saved(copy)).rows[0].memo, '')

    def test_script_save_error_log_and_immediate_cancel(self):
        dialog = ScriptEditor('click mouse', self.window.native, 0, self.window)
        dialog.show()
        self.qt.processEvents()
        self.assertNotIn('문법 검사', [b.text() for b in dialog.findChildren(QPushButton)])
        dialog.editor.setPlainText('loop 3\nclick mouse')
        dialog.save()
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog.validation.property('error'))
        self.assertIn('loop_end', dialog.validation.text())
        self.assertEqual(dialog.source, 'click mouse')
        with patch.object(QMessageBox, 'exec') as prompt:
            cancel = next(b for b in dialog.findChildren(QPushButton) if b.text() == '취소')
            QTest.mouseClick(cancel, Qt.MouseButton.LeftButton)
            prompt.assert_not_called()
        self.assertFalse(dialog.isVisible())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(dialog.source, 'click mouse')
        dialog.deleteLater()

    def test_curve_setting_is_dispatched_and_persisted(self):
        w = self.window
        row = w.profile.rows[0]
        row.action_mode, row.script = 'script', 'curve 10,20 30,40'
        w.settings.drag_segment_duration_ms = 25
        w.target = 123
        w.session.start(ExecutionPlan(w.target, w.profile.resolution, None, display_id=w.native.display_id))
        with patch.object(w.engine, 'submit', return_value=True) as submit, patch.object(w.native, 'active', return_value=True):
            w.dispatch('move', row.id)
            self.assertEqual(submit.call_args.args[0].drag_segment_duration_ms, 25)
        w.persist_settings()
        from domain import Settings
        loaded = Settings.read(self.saved(w.settings_path))
        self.assertEqual(loaded.drag_segment_duration_ms, 25)
        self.assertTrue(loaded.exclusive_hook)
        self.assertEqual(Settings.read({}).drag_segment_duration_ms, 10)
        self.assertTrue(Settings.read({}).exclusive_hook)
        self.assertFalse(Settings.read({'exclusive_hook': False}).exclusive_hook)
        for value in (-1, 100000):
            with self.assertRaises(ValueError):
                Settings.read({'drag_segment_duration_ms': value})

    def test_row_drag_hover_preserves_profile_and_drop_saves_once(self):
        from test_row_drag import Drop
        w, table = self.window, self.window.table
        before = [row.id for row in w.profile.rows]
        table.begin_preview(0)
        table.drag_anchor = QPoint(100, table.rowHeight(0)//2)
        position = QPoint(100, table.rowViewportPosition(2)+table.rowHeight(2)//2)
        with patch.object(w, 'save', wraps=w.save) as save:
            table.hover_at(position)
            table.preview.animation.setCurrentTime(160)
            self.assertEqual([row.id for row in w.profile.rows], before)
            save.assert_not_called()
            table.dropEvent(Drop(table, position))
            self.assertEqual(save.call_count, 1)
        expected = [before[1], before[2], before[0]]
        self.assertEqual([row.id for row in w.profile.rows], expected)
        self.assertEqual(table.currentRow(), 2)
        self.assertEqual([row.id for row in Profile.read(self.saved(w.path)).rows], expected)

    def test_autosave_all_edits_and_new_profile_preserves_old(self):
        w=self.window
        self.assertNotIn('저장',[b.text() for b in w.centralWidget().findChildren(QPushButton)])
        row=w.profile.rows[0]
        w.set_value(row.id,'memo','자동 메모')
        w.set_value(row.id,'x',12345)
        w.set_value(row.id,'action_mode','script')
        w.set_value(row.id,'script','click mouse')
        saved=Profile.read(self.saved(w.path)).rows[0]
        self.assertEqual((saved.memo,saved.x,saved.action_mode,saved.script),('자동 메모',12345,'script','click mouse'))
        w.add_row()
        self.assertEqual(len(Profile.read(self.saved(w.path)).rows),4)
        w.move_row(3,0)
        self.assertEqual(Profile.read(self.saved(w.path)).rows[0].id,w.profile.rows[0].id)
        w.table.selectRow(0)
        w.delete_rows()
        self.assertEqual(len(Profile.read(self.saved(w.path)).rows),3)
        old_path,old_bytes=w.path,w.path.read_bytes()
        destination = old_path.with_name('사용자 지정.json')
        with patch.object(QFileDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(QFileDialog, 'selectedFiles', return_value=[str(destination)]):
            w.new_profile()
        self.assertEqual(w.path, destination)
        self.assertNotEqual(w.path,old_path)
        self.assertEqual(old_path.read_bytes(),old_bytes)
        self.assertTrue(w.path.exists())
        self.assertEqual(self.saved(w.settings_path)['last_profile'],w.path.name)
        w.flush_pending_saves()
        self.assertFalse(w.dirty)

    def test_new_profile_dialog_cancel_preserves_file_and_script_draft(self):
        w = self.window
        old_profile, old_path, old_bytes = w.profile, w.path, w.path.read_bytes()
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        editor.editor.setPlainText('sleep 42')
        def cancel(dialog):
            self.assertEqual(dialog.acceptMode(), QFileDialog.AcceptMode.AcceptSave)
            self.assertEqual(dialog.defaultSuffix(), 'json')
            self.assertFalse(dialog.testOption(QFileDialog.Option.DontConfirmOverwrite))
            return QDialog.DialogCode.Rejected
        with patch.object(QFileDialog, 'exec', cancel), patch.object(w, 'confirm_changes') as confirm:
            w.new_profile()
        confirm.assert_not_called()
        self.assertIs(w.profile, old_profile)
        self.assertEqual(w.path, old_path)
        self.assertEqual(w.path.read_bytes(), old_bytes)
        self.assertIs(w.script_editor, editor)
        self.assertEqual(editor.editor.toPlainText(), 'sleep 42')

    def test_new_profile_write_failure_preserves_current_profile(self):
        from concurrent.futures import Future
        from persistence import SaveResult
        w = self.window
        old_profile, old_path, old_bytes = w.profile, w.path, w.path.read_bytes()
        result = Future()
        result.set_result(SaveResult(False, error='disk full'))
        with patch.object(QFileDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(QFileDialog, 'selectedFiles', return_value=[str(w.path.with_name('new.json'))]), \
             patch.object(w.store, 'save', return_value=result):
            w.new_profile()
        self.assertIs(w.profile, old_profile)
        self.assertEqual(w.path, old_path)
        self.assertEqual(w.path.read_bytes(), old_bytes)
        self.assertIn('disk full', w.statusBar().currentMessage())

    def test_new_profile_cannot_overwrite_settings_file(self):
        w = self.window
        old_profile, old_path = w.profile, w.path
        settings_before = w.settings_path.read_bytes()
        with patch.object(QFileDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(QFileDialog, 'selectedFiles', return_value=[str(w.settings_path)]):
            w.new_profile()
        self.assertIs(w.profile, old_profile)
        self.assertEqual(w.path, old_path)
        self.assertEqual(w.settings_path.read_bytes(), settings_before)
        self.assertIn('환경설정 파일', w.statusBar().currentMessage())

    def test_live_memo_is_saved_before_editor_closes(self):
        from PySide6.QtWidgets import QLineEdit
        w=self.window
        w.table.editItem(w.table.item(0,4))
        self.qt.processEvents()
        editor=self.qt.focusWidget()
        self.assertIsInstance(editor,QLineEdit)
        QTest.keyClicks(editor,'live memo')
        self.assertEqual(Profile.read(self.saved(w.path)).rows[0].memo,'live memo')
        w.flush_pending_saves()
        self.assertFalse(w.dirty)

    def test_autosave_failure_preserves_file_and_reports_error(self):
        w=self.window
        original=w.path.read_bytes()
        with patch('persistence.atomic_json',side_effect=OSError('disk unavailable')), patch.object(QMessageBox,'warning') as popup:
            w.set_value(w.profile.rows[0].id,'memo','pending')
            w.flush_pending_saves()
            popup.assert_not_called()
        w.flush_pending_saves()
        self.assertTrue(w.dirty)
        self.assertIn('자동저장 실패',w.statusBar().currentMessage())
        self.assertEqual(w.path.read_bytes(),original)
        self.assertTrue(w.save(automatic=True))
        w.flush_pending_saves()
        self.assertFalse(w.dirty)
        self.assertEqual(Profile.read(self.saved(w.path)).rows[0].memo,'pending')

    def test_script_accept_is_automatically_saved(self):
        w=self.window
        row=w.profile.rows[0]
        row.action_mode='script'
        w.edit_script(row.id)
        w.script_editor.editor.setPlainText('click 100,200')
        w.script_editor.save()
        self.assertEqual(Profile.read(self.saved(w.path)).rows[0].script,'click 100,200')
        w.flush_pending_saves()
        self.assertFalse(w.dirty)

    def test_conversion_dialog_unknown_resolution_presets_and_errors(self):
        from conversion_dialog import ConversionDialog
        w = self.window
        dialog = ConversionDialog(w.profile,w.path,None,lambda *args: False,w)
        self.assertFalse(dialog.copy_button.isEnabled())
        self.assertEqual(dialog.source.value(),(0,0))
        dialog.source.set_value((1920,1080))
        dialog.destination.mode.setCurrentText('4:3')
        dialog.refresh()
        self.assertEqual(dialog.destination.value(),(640,480))
        self.assertTrue(dialog.copy_button.isEnabled())
        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertIn('비율',dialog.notice.text())
        dialog.destination.mode.setCurrentText('16:9')
        dialog.refresh()
        self.assertTrue(dialog.copy_button.isEnabled())
        dialog.profile.rows[0].x=1920
        dialog.refresh()
        self.assertFalse(dialog.copy_button.isEnabled())
        dialog.profile.rows[0].x=0
        dialog.refresh()
        self.assertTrue(dialog.copy_button.isEnabled())
        dialog.close()

    def test_conversion_copy_preserves_original_and_switches_recent_profile(self):
        from conversion import plan_conversion
        w=self.window
        w.profile.resolution=(1920,1080)
        w.profile.rows[0].x=960
        w.profile.rows[0].script='click 960,540'
        w.save()
        original_path,original_bytes=w.path,w.path.read_bytes()
        plan=plan_conversion(w.profile,(1920,1080),(1280,720))
        output=w.data_dir/'converted.json'
        with patch.object(QFileDialog,'getSaveFileName',return_value=(str(output),'')):
            self.assertTrue(w.save_conversion(plan.profile,'copy',w))
        self.assertEqual(original_path.read_bytes(),original_bytes)
        self.assertEqual(w.path,output)
        self.assertEqual(w.profile.rows[0].script,'click 640,360')
        self.assertEqual(read_json(w.settings_path)['last_profile'],output.name)
        self.assertEqual(Profile.read(read_json(output)).resolution,(1280,720))

    def test_conversion_failure_cancel_and_same_path_leave_source(self):
        from conversion import plan_conversion
        w=self.window
        original=w.profile.export()
        original_path=w.path
        original_bytes=w.path.read_bytes()
        plan=plan_conversion(w.profile,(1920,1080),(1280,720))
        with patch.object(QFileDialog,'getSaveFileName',return_value=('','')):
            self.assertFalse(w.save_conversion(plan.profile,'copy',w))
        with patch.object(QFileDialog,'getSaveFileName',return_value=(str(w.path),'')):
            with self.assertRaises(ValueError):
                w.save_conversion(plan.profile,'copy',w)
        with patch('persistence.atomic_json',side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError,'disk full'):
                w.save_conversion(plan.profile,'apply',w)
        self.assertEqual(w.path,original_path)
        self.assertEqual(w.profile.export(),original)
        self.assertEqual(w.path.read_bytes(),original_bytes)

    def test_capture_unknown_basis_and_fullscreen_mismatch_require_conversion(self):
        w=self.window
        for target,resolution in ((99,None),(0,(1920,1080))):
            w.target=target
            w.profile.resolution=resolution
            w.profile.rows[0].x=300
            original=w.profile.export()
            with patch.object(w.native,'exists',return_value=True), \
                 patch.object(w.native,'cursor',return_value=(400,500)), \
                 patch.object(w.native,'bounds',return_value=(0,0,1280,720)), \
                 patch.object(w.native,'size',return_value=(1280,720)), \
                 patch.object(w,'convert_dialog') as dialog:
                w.capture(w.profile.rows[0].id)
                dialog.assert_called_once()
            self.assertEqual(w.profile.export(),original)

    def test_converter_without_target_does_not_use_desktop_resolution(self):
        w=self.window
        w.target=0
        with patch('app.ConversionDialog') as dialog, patch.object(w.native,'size') as size:
            w.convert_dialog()
            self.assertIsNone(dialog.call_args.args[2])
            size.assert_not_called()

    def test_script_capture_converts_mismatched_resolution_to_profile_basis(self):
        w=self.window
        w.profile.resolution=(1920,1080)
        editor=ScriptEditor('',w.native,99,w)
        with patch.object(w.native,'window_identity',return_value={'exe':'Game.exe','class':'Game','title':'Game'}), patch.object(w.native,'size',return_value=(1280,720)), \
             patch.object(w.native,'bounds',return_value=(0,0,1280,720)):
            editor.pick_coordinate((100,200))
        self.assertEqual(editor.editor.toPlainText(),'150,300')
        self.assertEqual(editor.capture_resolution,(1920,1080))
        self.assertEqual(editor.capture_size,(1280,720))
        editor.close()

    def test_script_capture_new_profile_records_basis_only_on_save(self):
        w=self.window
        editor=ScriptEditor('',w.native,99,w)
        with patch.object(w.native,'window_identity',return_value={'exe':'Game.exe','class':'Game','title':'Game'}), patch.object(w.native,'size',return_value=(1280,720)), \
             patch.object(w.native,'bounds',return_value=(0,0,1280,720)):
            editor.pick_coordinate((100,200))
        self.assertEqual(editor.editor.toPlainText(),'100,200')
        self.assertEqual(editor.capture_resolution,(1280,720))
        self.assertIsNone(w.profile.resolution)
        editor.close()

    def test_click_shortcut_box_prompts_without_erasing_saved_key(self):
        from PySide6.QtWidgets import QLineEdit
        dialog=KeyDialog('q',self.window)
        dialog.show()
        self.qt.processEvents()
        field=dialog.editor.findChild(QLineEdit)
        QTest.mouseClick(field,Qt.MouseButton.LeftButton)
        self.assertEqual(field.text(),'입력 대기중...')
        self.assertEqual(dialog.value(),'q')
        QTest.keyClick(dialog.editor,Qt.Key.Key_K,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(dialog.value(),'ctrl+k')
        self.assertEqual(field.text(),'Ctrl+K')
        QTest.mouseClick(field,Qt.MouseButton.LeftButton)
        self.assertEqual(field.text(),'입력 대기중...')
        dialog.editor.clearFocus()
        self.assertEqual(field.text(),'Ctrl+K')
        self.assertEqual(dialog.value(),'ctrl+k')
        dialog.close()

    def test_display_settings_selection_and_missing_device(self):
        from displays import Display
        from domain import Settings
        monitor=Display('test-monitor','Test display',12,'DISPLAY2',-1920,0,1920,1080,False)
        dialog=SettingsDialog(Settings(),self.window,[monitor])
        self.assertEqual(dialog.displays.count(),2)
        dialog.displays.setCurrentIndex(1)
        self.assertEqual(dialog.value().display_id,'test-monitor')
        dialog.close()
        dialog=SettingsDialog(Settings(display_id='missing'),self.window,[monitor])
        self.assertIn('연결되지 않은',dialog.displays.currentText())
        self.assertEqual(dialog.value().display_id,'missing')
        dialog.close()

    def test_disconnected_display_stops_fullscreen_but_not_target_window(self):
        w=self.window
        w.native.display_id='missing'
        with patch.object(w.native,'refresh_displays',return_value=()):
            w.native.displays=()
            w.display_ticks=9
            w.target=0
            w.armed=True
            w.tick()
            self.assertFalse(w.armed)
            w.target=99
            w.armed=True
            w.display_ticks=9
            with patch.object(w.native,'exists',return_value=True), \
                 patch.object(w.native,'size',return_value=(1920,1080)), \
                 patch.object(w.native,'cursor',return_value=(10,20)), \
                 patch.object(w.native,'active',return_value=True):
                w.tick()
                self.assertTrue(w.armed)

    def test_selected_monitor_used_as_conversion_destination(self):
        w=self.window
        w.target=0
        w.native.display_id='test-monitor'
        with patch('app.ConversionDialog') as dialog, patch.object(w.native,'size',return_value=(1920,1080)):
            w.convert_dialog()
            self.assertEqual(dialog.call_args.args[2],(1920,1080))
            self.assertEqual(dialog.call_args.kwargs['target_label'],'선택한 디스플레이')

    def test_settings_units_are_outside_inputs_and_return_option_controls_delay(self):
        dialog = SettingsDialog(self.window.settings, self.window)
        for spin in dialog.times.values():
            self.assertEqual(spin.suffix(), '')
            self.assertNotIn('ms', spin.text())
        self.assertTrue(dialog.value().return_cursor)
        dialog.checks['return_cursor'].setChecked(False)
        self.assertFalse(dialog.times['cursor_return_delay_ms'].isEnabled())
        self.assertFalse(dialog.value().return_cursor)
        dialog.checks['return_cursor'].setChecked(True)
        self.assertTrue(dialog.times['cursor_return_delay_ms'].isEnabled())
        self.assertIn('자동 정지', dialog.checks['auto_pause'].text())
        dialog.close()

    def test_timing_fields_accept_five_digit_keyboard_input(self):
        dialog = SettingsDialog(self.window.settings, self.window)
        dialog.tabs.setCurrentIndex(1)
        dialog.show()
        self.qt.processEvents()
        for name, spin in dialog.times.items():
            for value in (12345, 99999):
                spin.setFocus()
                spin.selectAll()
                QTest.keyClicks(spin, str(value))
                spin.interpretText()
                self.assertEqual(spin.value(), value, name)
                self.assertEqual(getattr(dialog.value(), name), value)
        dialog.close()

    def test_resize_compact_fields_and_aspect_lock(self):
        dialog = ResizeDialog((1920,1080),self.window)
        dialog.show()
        self.qt.processEvents()
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertTrue(dialog.custom_fields.isHidden())
        self.assertFalse(dialog.presets.isHidden())
        dialog.mode_buttons['직접 입력'].click()
        self.assertFalse(dialog.custom_fields.isHidden())
        self.assertTrue(dialog.presets.isHidden())
        dialog.aspect_lock.setChecked(True)
        dialog.width_input.setFocus()
        dialog.width_input.selectAll()
        QTest.keyClicks(dialog.width_input,'1280')
        self.assertEqual(dialog.value(),(1280,720))
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.height_input.setValue(900)
        self.assertEqual(dialog.value(),(1600,900))
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.aspect_lock.setChecked(False)
        dialog.width_input.setValue(1500)
        self.assertEqual(dialog.value(),(1500,900))
        dialog.close()

    def test_resize_checks_actual_size_before_accepting(self):
        from unittest.mock import Mock
        request=Mock()
        sizes=iter([(1920,1080),(1280,720),(1280,720)])
        dialog=ResizeDialog((1920,1080),self.window,request_resize=request,read_size=lambda:next(sizes))
        dialog.presets.setCurrentIndex(dialog.PRESETS['16:9'].index((1280,720)))
        request.assert_not_called()
        dialog.apply()
        dialog.verify_timer.stop()
        request.assert_called_once_with((1280,720))
        dialog.verify_resize()
        self.assertTrue(dialog.pending)
        dialog.verify_resize()
        self.assertTrue(dialog.pending)
        dialog.verify_resize()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.current_size,(1280,720))
        dialog.close()

    def test_resize_rejection_and_closed_target_do_not_accept(self):
        dialog=ResizeDialog((1920,1080),self.window,request_resize=lambda size:None,read_size=lambda:(1920,1080))
        dialog.presets.setCurrentIndex(dialog.PRESETS['16:9'].index((1280,720)))
        dialog.apply()
        dialog.verify_timer.stop()
        for _ in range(10):
            dialog.verify_resize()
        self.assertFalse(dialog.pending)
        self.assertNotEqual(dialog.result(),QDialog.DialogCode.Accepted)
        self.assertIn('실제 1920 × 1080',dialog.message.text())
        def closed():
            raise OSError('대상 창이 닫혔습니다.')
        dialog.read_size=closed
        dialog.apply()
        dialog.verify_timer.stop()
        dialog.verify_resize()
        self.assertFalse(dialog.pending)
        self.assertIn('닫혔습니다',dialog.message.text())
        self.assertNotEqual(dialog.result(),QDialog.DialogCode.Accepted)
        dialog.close()

    def test_resize_conversion_is_optional_and_choice_is_saved(self):
        w=self.window
        w.target=99
        for accepted,convert,expected in ((True,False,False),(True,True,True),(False,False,False)):
            with patch('app.ResizeDialog') as dialog, \
                 patch.object(w.native,'exists',return_value=True), \
                 patch.object(w.native,'size',return_value=(1920,1080)), \
                 patch.object(w,'convert_dialog') as conversion:
                dialog.return_value.exec.return_value=QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
                dialog.return_value.convert_profile.isChecked.return_value=convert
                dialog.return_value.current_size=(1280,720)
                w.resize_dialog()
                self.assertEqual(conversion.called,expected)
                self.assertEqual(w.settings.resize_convert_profile,convert)
                self.assertEqual(read_json(w.settings_path)['resize_convert_profile'],convert)

    def test_script_editor_leaves_menu_accessible_and_settings_preserve_draft(self):
        w=self.window
        w.edit_script(w.profile.rows[0].id)
        editor=w.script_editor
        editor.editor.setPlainText('sleep 123')
        self.qt.processEvents()
        self.assertTrue(w.session.editor_open)
        self.assertFalse(w.modal)
        self.assertFalse(editor.isWindow())
        self.assertEqual(editor.windowModality(),Qt.WindowModality.NonModal)
        action=w.menuBar().actions()[1]
        QTest.mouseClick(w.menuBar(),Qt.MouseButton.LeftButton,pos=w.menuBar().actionGeometry(action).center())
        self.qt.processEvents()
        self.assertTrue(action.menu().isVisible())
        action.menu().hide()
        with patch('app.SettingsDialog') as settings:
            settings.return_value.exec.return_value=QDialog.DialogCode.Rejected
            action.menu().actions()[0].trigger()
            settings.assert_called_once()
        self.assertIs(w.script_editor,editor)
        self.assertEqual(editor.editor.toPlainText(),'sleep 123')
        self.assertFalse(w.modal)
        self.assertFalse(w.eligible('capture_selected'))
        editor.reject()
        self.assertFalse(w.session.editor_open)

    def test_file_save_commits_script_draft_and_validation_keeps_editor_open(self):
        w=self.window
        row=w.profile.rows[0]
        row.action_mode='script'
        w.edit_script(row.id)
        editor=w.script_editor
        editor.editor.setPlainText('bad_command')
        self.assertFalse(w.save())
        self.assertIs(w.script_editor,editor)
        self.assertTrue(editor.validation.isVisible())
        editor.editor.setPlainText('sleep 42')
        self.assertTrue(w.save())
        self.assertIsNone(w.script_editor)
        self.assertEqual(Profile.read(read_json(w.path)).rows[0].script,'sleep 42')

    def test_profile_switch_can_cancel_or_discard_script_draft(self):
        w=self.window
        previous=w.profile
        w.edit_script(previous.rows[0].id)
        editor=w.script_editor
        editor.editor.setPlainText('sleep 42')
        with patch.object(QFileDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(QFileDialog, 'selectedFiles', return_value=[str(w.path.with_name('new.json'))]), \
             patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Cancel):
            w.new_profile()
        self.assertIs(w.profile,previous)
        self.assertIs(w.script_editor,editor)
        with patch.object(QFileDialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
             patch.object(QFileDialog, 'selectedFiles', return_value=[str(w.path.with_name('new.json'))]), \
             patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Discard):
            w.new_profile()
        self.assertIsNot(w.profile,previous)
        self.assertIsNone(w.script_editor)
        self.assertFalse(w.session.editor_open)
        self.assertFalse(w.modal)
        self.assertEqual(previous.rows[0].script,'')

    def test_editor_cancel_does_not_prompt_or_change_script(self):
        w=self.window
        row=w.profile.rows[0]
        row.script='sleep 1'
        w.edit_script(row.id)
        w.script_editor.editor.setPlainText('sleep 2')
        with patch.object(QMessageBox,'exec') as confirmation:
            w.script_editor.reject()
            confirmation.assert_not_called()
        self.assertEqual(row.script,'sleep 1')
        self.assertIsNone(w.script_editor)

    def test_save_as_settings_path_keeps_profile_path_and_both_files(self):
        w = self.window
        path, dirty = w.path, w.dirty
        profile_bytes, settings_bytes = path.read_bytes(), w.settings_path.read_bytes()
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(w.settings_path),'') ):
            self.assertFalse(w.save_as())
        self.assertEqual(w.path, path)
        self.assertEqual(w.dirty, dirty)
        self.assertEqual(path.read_bytes(), profile_bytes)
        self.assertEqual(w.settings_path.read_bytes(), settings_bytes)
        self.assertIn('환경설정 파일', w.statusBar().currentMessage())

    def test_conversion_copy_cannot_target_settings_file(self):
        from copy import deepcopy
        w = self.window
        profile, path = w.profile, w.path
        settings_bytes = w.settings_path.read_bytes()
        converted = deepcopy(profile)
        converted.resolution = (1280,720)
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(w.settings_path),'')):
            with self.assertRaises(ValueError):
                w.save_conversion(converted, 'copy', w)
        self.assertIs(w.profile, profile)
        self.assertEqual(w.path, path)
        self.assertEqual(w.settings_path.read_bytes(), settings_bytes)

    def test_conversion_after_failed_profile_load_preserves_unreadable_source(self):
        for original in (b'{bad json', b'{"version":1,"macros":[]}'):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / 'macro.json'
                source.write_bytes(original)
                window = MainWindow(data_dir=folder, hook_enabled=False)
                try:
                    self.assertIsNone(window.path)
                    converted = Profile.default()
                    converted.resolution = (1280,720)
                    self.assertTrue(window.save_conversion(converted, 'apply', window))
                    self.assertEqual(window.path.name, 'macro_1.json')
                    self.assertEqual(source.read_bytes(), original)
                    self.assertEqual(Profile.read(read_json(window.path)).resolution, (1280,720))
                finally:
                    window.shutdown()
                    window.close()
                    window.deleteLater()

    def test_invalid_settings_survive_unedited_window_close(self):
        with tempfile.TemporaryDirectory() as folder:
            settings_path = Path(folder) / 'setting.json'
            original = b'{"click_hold_ms":9876,"auto_pause":false,"unknown":1}'
            settings_path.write_bytes(original)
            window = MainWindow(data_dir=folder, hook_enabled=False)
            window.close()
            self.assertTrue(window.closing)
            self.assertEqual(settings_path.read_bytes(), original)
            window.deleteLater()

    def test_settings_dialog_explicit_save_backs_up_invalid_original(self):
        from domain import Settings
        with tempfile.TemporaryDirectory() as folder:
            settings_path = Path(folder) / 'setting.json'
            original = b'{bad json'
            settings_path.write_bytes(original)
            window = MainWindow(data_dir=folder, hook_enabled=False)
            try:
                with patch('app.SettingsDialog') as dialog:
                    dialog.return_value.exec.return_value = QDialog.DialogCode.Accepted
                    dialog.return_value.value.return_value = Settings(click_hold_ms=89)
                    window.settings_dialog()
                self.assertEqual(read_json(settings_path)['click_hold_ms'], 89)
                self.assertEqual((Path(folder)/'setting.invalid.bak').read_bytes(), original)
            finally:
                window.shutdown()
                window.close()
                window.deleteLater()

    def test_external_profile_reopen_preserves_edited_local_recent_copy(self):
        from domain import atomic_json
        w = self.window
        with tempfile.TemporaryDirectory() as external:
            source = Path(external) / 'shared.json'
            atomic_json(source, Profile.default().export())
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(source), '')):
                w.open_profile()
            local = w.data_dir / w.settings.last_profile
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(local), '')):
                w.open_profile()
            w.table.item(0, 4).setText('saved local changes')
            w.flush_pending_saves()
            self.assertFalse(w.dirty)
            original = local.read_bytes()
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(source), '')):
                w.open_profile()
            self.assertEqual(local.read_bytes(), original)
            self.assertEqual(read_json(local)['macros'][0]['memo'], 'saved local changes')
            self.assertNotEqual(w.settings.last_profile, local.name)

    def test_failed_settings_save_then_cancel_keeps_runtime_and_disk(self):
        from dataclasses import asdict, replace
        w = self.window
        before = asdict(w.settings)
        bindings = list(w.hooks.bindings)
        disk = w.settings_path.read_bytes()
        candidate = replace(w.settings, click_hold_ms=9876, capture_hk='ctrl+g',
                            theme='Light', display_id='new-monitor')
        with patch('app.SettingsDialog') as factory, \
             patch.object(w.store, 'settings', side_effect=OSError('settings unavailable')), \
             patch.object(w, 'error') as error, patch.object(w, 'apply_theme') as theme:
            dialog = factory.return_value
            dialog.exec.side_effect = [QDialog.DialogCode.Accepted, QDialog.DialogCode.Rejected]
            dialog.value.return_value = candidate
            w.settings_dialog()
            error.assert_called_once_with('settings unavailable')
            theme.assert_not_called()
            dialog.deleteLater.assert_called_once()
        self.assertEqual(asdict(w.settings), before)
        self.assertEqual(w.native.display_id, before['display_id'])
        self.assertEqual(w.hooks.bindings, bindings)
        self.assertEqual(w.settings_path.read_bytes(), disk)
        self.assertTrue(w.save())
        w.persist_settings()
        self.assertEqual(read_json(w.settings_path), before)

    def test_settings_retry_applies_candidate_only_after_success(self):
        from dataclasses import replace
        w = self.window
        old = w.settings
        candidate = replace(old, click_hold_ms=9876, capture_hk='ctrl+g')
        save = w.store.settings
        attempts = []
        def write(*args, **kwargs):
            self.assertIs(w.settings, old)
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError('temporary error')
            return save(*args, **kwargs)
        with patch('app.SettingsDialog') as factory, \
             patch.object(w.store, 'settings', side_effect=write), patch.object(w, 'error') as error:
            dialog = factory.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.value.return_value = candidate
            w.settings_dialog()
            self.assertEqual(dialog.exec.call_count, 2)
            error.assert_called_once_with('temporary error')
            dialog.deleteLater.assert_called_once()
        self.assertEqual(w.settings.click_hold_ms, 9876)
        self.assertEqual(w.settings.capture_hk, 'ctrl+g')
        self.assertEqual(read_json(w.settings_path)['click_hold_ms'], 9876)
        self.assertIn('ctrl+g', [binding.key for binding in w.hooks.bindings])

    def test_resize_preference_does_not_apply_when_save_fails(self):
        w = self.window
        before = w.settings.resize_convert_profile
        with patch('app.ResizeDialog') as factory, \
             patch.object(w.native, 'exists', return_value=True), \
             patch.object(w.native, 'size', return_value=(1920, 1080)), \
             patch.object(w.store, 'settings', side_effect=OSError('write failed')), \
             patch.object(w, 'error') as error:
            dialog = factory.return_value
            dialog.exec.return_value = QDialog.DialogCode.Rejected
            dialog.convert_profile.isChecked.return_value = not before
            w.resize_dialog()
            error.assert_called_once_with('write failed')
            dialog.deleteLater.assert_called_once()
        self.assertEqual(w.settings.resize_convert_profile, before)

    def test_repeated_modal_dialogs_release_qt_children(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        w = self.window
        with patch.object(QDialog, 'exec', return_value=QDialog.DialogCode.Rejected), \
             patch.object(w.native, 'exists', return_value=True), \
             patch.object(w.native, 'size', return_value=(1920, 1080)):
            for _ in range(8):
                w.settings_dialog()
                w.edit_key(w.profile.rows[0].id)
                w.resize_dialog()
                w.convert_dialog()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertEqual(w.findChildren(QDialog), [])
        with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Cancel):
            for _ in range(8):
                w.dirty = True
                self.assertFalse(w.confirm_changes())
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertEqual(w.findChildren(QMessageBox), [])
        w.dirty = False

    def test_modal_dialog_is_disposed_on_exception(self):
        w = self.window
        with patch('app.KeyDialog') as factory:
            dialog = factory.return_value
            dialog.exec.side_effect = ValueError('dialog failed')
            w.edit_key(w.profile.rows[0].id)
            dialog.deleteLater.assert_called_once()
        self.assertIn('dialog failed', w.statusBar().currentMessage())

    def test_special_shortcuts_round_trip_between_qt_settings_and_hooks(self):
        from dataclasses import replace
        from PySide6.QtGui import QKeySequence
        from hotkeys import Hotkeys
        from widgets import managed_dialog
        keys = ((Qt.Key.Key_Print, 'print screen', 55, 0x2C, True),
                (Qt.Key.Key_Pause, 'pause', 69, 0x13, False),
                (Qt.Key.Key_NumLock, 'num lock', 69, 0x90, True))
        from hotkeys import PressedKey
        for qt_key, canonical, scan, vk, extended in keys:
            with self.subTest(key=canonical), managed_dialog(KeyDialog('', self.window)) as dialog:
                dialog.editor.setKeySequence(QKeySequence(qt_key))
                value = dialog.value()
                self.assertEqual(value, canonical)
                binding = Hotkeys.compile(value)
                self.assertTrue(binding.groups[-1].matches(PressedKey(scan, vk, extended)))
                with managed_dialog(KeyDialog(value, self.window)) as reopened:
                    self.assertEqual(reopened.value(), canonical)
                with managed_dialog(SettingsDialog(replace(self.window.settings, pause_hk=value), self.window)) as settings:
                    self.assertEqual(settings.value().pause_hk, canonical)

    def test_memo_escape_restores_table_profile_and_autosaved_file(self):
        from PySide6.QtWidgets import QLineEdit
        w = self.window
        item = w.table.item(0, 4)
        item.setText('original')
        w.flush_pending_saves()
        w.table.editItem(item)
        self.qt.processEvents()
        editor = self.qt.focusWidget()
        self.assertIsInstance(editor, QLineEdit)
        editor.selectAll()
        QTest.keyClicks(editor, 'changed')
        self.assertEqual(w.profile.rows[0].memo, 'changed')
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        self.qt.processEvents()
        w.flush_pending_saves()
        self.assertEqual(item.text(), 'original')
        self.assertEqual(w.profile.rows[0].memo, 'original')
        self.assertEqual(read_json(w.path)['macros'][0]['memo'], 'original')
        w.table.editItem(item)
        self.qt.processEvents()
        self.assertEqual(self.qt.focusWidget().text(), 'original')
        w.flush_pending_saves()
        self.assertEqual(read_json(w.path)['macros'][0]['memo'], 'original')

    def test_memo_enter_keeps_live_edit_in_table_profile_and_file(self):
        w = self.window
        item = w.table.item(0, 4)
        w.table.editItem(item)
        self.qt.processEvents()
        editor = self.qt.focusWidget()
        QTest.keyClicks(editor, 'committed')
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.qt.processEvents()
        w.flush_pending_saves()
        self.assertEqual((item.text(), w.profile.rows[0].memo,
                          read_json(w.path)['macros'][0]['memo']),
                         ('committed',) * 3)

    def test_memo_escape_rollback_is_ordered_after_inflight_autosave(self):
        import threading
        from domain import atomic_json
        w = self.window
        item = w.table.item(0, 4)
        item.setText('original')
        w.flush_pending_saves()
        entered, release = threading.Event(), threading.Event()
        def write(path, data):
            if Path(path) == w.path and not entered.is_set():
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test timeout')
            atomic_json(path, data)
        with patch('persistence.atomic_json', side_effect=write):
            w.table.editItem(item)
            self.qt.processEvents()
            editor = self.qt.focusWidget()
            editor.selectAll()
            try:
                QTest.keyClicks(editor, 'changed')
                self.assertTrue(entered.wait(1))
                QTest.keyClick(editor, Qt.Key.Key_Escape)
            finally:
                release.set()
            w.flush_pending_saves()
        self.assertEqual((item.text(), w.profile.rows[0].memo,
                          read_json(w.path)['macros'][0]['memo']),
                         ('original',) * 3)
        self.assertFalse(w.dirty)

    def test_windows_combination_edit_preserves_previous_key_and_file(self):
        w = self.window
        row = w.profile.rows[0]
        w.save()
        with patch('app.KeyDialog') as factory, patch.object(QMessageBox,'warning') as warning:
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.value.return_value = 'win+q'
            w.edit_key(row.id)
            warning.assert_not_called()
        w.flush_pending_saves()
        self.assertEqual(row.move_hk,'q')
        self.assertEqual(read_json(w.path)['macros'][0]['move_hk'],'q')
        self.assertIn('Win',w.statusBar().currentMessage())

    def test_supported_combinations_and_plus_round_trip_in_both_dialogs(self):
        from dataclasses import replace
        from hotkeys import Hotkeys
        from widgets import managed_dialog
        cases = ((Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier, 'ctrl+alt+shift+q'),
                 (Qt.Key.Key_Plus, Qt.KeyboardModifier.KeypadModifier, 'plus'),
                 (Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier, 'ctrl+plus'),
                 (Qt.Key.Key_Plus, Qt.KeyboardModifier.ShiftModifier, 'shift+plus'),
                 (Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier, 'ctrl+,'),
                 (Qt.Key.Key_PageUp, Qt.KeyboardModifier.ControlModifier, 'ctrl+page up'))
        for key, modifiers, expected in cases:
            with self.subTest(expected=expected), managed_dialog(KeyDialog('', self.window)) as dialog:
                dialog.show()
                self.qt.processEvents()
                QTest.keyClick(dialog.editor, key, modifiers)
                self.assertEqual(dialog.value(), expected)
                self.assertEqual(Hotkeys.compile(dialog.value()).key, expected)
                dialog.close()
                with managed_dialog(KeyDialog(expected, self.window)) as reopened:
                    self.assertEqual(reopened.value(), expected)
                with managed_dialog(SettingsDialog(replace(self.window.settings, pause_hk=expected), self.window)) as settings:
                    self.assertEqual(settings.value().pause_hk, expected)
                dialog.editor.clear()
                self.assertEqual(dialog.value(), '')

    def test_alias_duplicate_edit_keeps_previous_shortcut_and_uses_log(self):
        w = self.window
        w.profile.rows[0].move_hk = 'ctrl+page up'
        w.save()
        w.rebind()
        row = w.profile.rows[1]
        with patch('app.KeyDialog') as factory, patch.object(QMessageBox, 'warning') as warning:
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.value.return_value = 'ctrl+pgup'
            w.edit_key(row.id)
            warning.assert_not_called()
        w.flush_pending_saves()
        self.assertEqual(row.move_hk, 'w')
        self.assertEqual(read_json(w.path)['macros'][1]['move_hk'], 'w')
        self.assertIn('중복', w.statusBar().currentMessage())

    def test_restore_shows_hidden_window_without_changing_profile_or_run_state(self):
        w = self.window
        profile, path = w.profile, w.path
        w.armed = True
        w.update_labels()
        w.hide()
        self.assertFalse(w.isVisible())
        w.restore()
        self.qt.processEvents()
        self.assertTrue(w.isVisible())
        self.assertTrue(w.armed)
        self.assertIs(w.profile, profile)
        self.assertEqual(w.path, path)

    def test_restore_preserves_maximized_state_after_minimizing(self):
        w = self.window
        w.showMaximized()
        self.qt.processEvents()
        w.showMinimized()
        self.qt.processEvents()
        self.assertTrue(w.isMinimized())
        w.restore()
        self.qt.processEvents()
        self.assertFalse(w.isMinimized())
        self.assertTrue(w.isMaximized())

    def test_fullscreen_script_warns_without_submitting_or_stopping_other_actions(self):
        w = self.window
        w.target = 0
        w.session.start(ExecutionPlan(w.target, w.profile.resolution, None, display_id=w.native.display_id))
        row = w.profile.rows[0]
        row.action_mode, row.script = 'script', 'click 10,20'
        with patch.object(w.engine, 'submit', return_value=True) as submit, \
             patch('execution_plan.compile_script') as compile_script, \
             patch.object(QMessageBox, 'warning') as popup:
            w.dispatch('move', row.id)
            self.assertEqual(w.statusBar().currentMessage(), '스크립트를 실행하려면 앱을 선택하세요.')
            self.assertTrue(w.armed)
            submit.assert_not_called()
            compile_script.assert_not_called()
            popup.assert_not_called()
            row.action_mode = 'move_and_click'
            w.dispatch('move', row.id)
            submit.assert_called_once()
            self.assertEqual(submit.call_args.args[0].hwnd, 0)

    def test_selected_app_script_waits_for_focus_then_submits(self):
        w = self.window
        w.target = 123
        w.session.start(ExecutionPlan(w.target, w.profile.resolution, None, display_id=w.native.display_id))
        row = w.profile.rows[0]
        row.action_mode, row.script = 'script', 'click 10,20'
        with patch.object(w.engine, 'submit', return_value=True) as submit, \
             patch.object(w.native, 'active', return_value=False) as active:
            w.dispatch('move', row.id)
            submit.assert_not_called()
            active.return_value = True
            w.dispatch('move', row.id)
            submit.assert_called_once()
            self.assertEqual(submit.call_args.args[0].hwnd, 123)

    def test_conversion_with_blank_script_rows_can_be_saved(self):
        from domain import Row
        from conversion_dialog import ConversionDialog
        w = self.window
        w.profile = Profile([
            Row(move_hk='q', action_mode='script', script='curve 1353,961 mouse'),
            Row(move_hk='1', action_mode='script', script=''),
            Row(move_hk='3', action_mode='script', script=''),
            Row(move_hk='2', action_mode='script', script='')], (1920,1080))
        self.assertTrue(w.save())
        dialog = ConversionDialog(w.profile, w.path, (1280,720), w.save_conversion, w)
        self.assertEqual(dialog.error.text(), '')
        self.assertTrue(dialog.copy_button.isEnabled())
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.save('apply')
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        saved = Profile.read(read_json(w.path))
        self.assertEqual(saved.resolution, (1280,720))
        self.assertEqual(saved.rows[0].script, 'curve 902,641 mouse')
        self.assertEqual([row.script for row in saved.rows[1:]], ['', '', ''])
        self.assertEqual([row.move_hk for row in saved.rows[1:]], ['1', '3', '2'])
        dialog.deleteLater()

    def test_script_to_click_keeps_columns_and_buttons_stable(self):
        w = self.window
        for row in w.profile.rows:
            row.action_mode = 'script'
        w.rebuild()
        self.qt.processEvents()
        before = ([w.table.columnWidth(i) for i in range(5)], w.size(),
                  w.target_box.size(), w.toolbar_panel.size(),
                  w.pin_button.pos(), w.pause_button.pos(),
                  w.table.cellWidget(1, 1).size())
        w.set_value(w.profile.rows[0].id, 'action_mode', 'move_and_click')
        self.qt.processEvents()
        self.qt.processEvents()
        after = ([w.table.columnWidth(i) for i in range(5)], w.size(),
                 w.target_box.size(), w.toolbar_panel.size(),
                 w.pin_button.pos(), w.pause_button.pos(),
                 w.table.cellWidget(1, 1).size())
        self.assertEqual(after, before)
        w.set_value(w.profile.rows[0].id, 'action_mode', 'script')
        self.qt.processEvents()
        self.assertEqual(w.table.cellWidget(1,1).size(), before[-1])

    def test_ratio_change_refreshes_warning_and_saves_latest_conversion(self):
        from conversion_dialog import ConversionDialog
        from unittest.mock import Mock
        w = self.window
        w.profile.resolution = (1920,1080)
        before = w.profile.export()
        saved = Mock(return_value=False)
        dialog = ConversionDialog(w.profile,w.path,(1280,720),saved,w)
        self.assertTrue(dialog.copy_button.isEnabled())
        dialog.destination.set_value((1280,960))
        self.assertFalse(dialog.copy_button.isEnabled())
        for mode in ('copy','apply'):
            dialog.save(mode)
            self.assertEqual(saved.call_args.args[0].resolution,(1280,960))
        self.assertEqual(saved.call_count,2)
        self.assertEqual(w.profile.export(),before)
        self.assertIn('16:9',dialog.notice.text())
        self.assertIn('4:3',dialog.notice.text())
        self.assertEqual(dialog.error.text(),'')
        dialog.destination.set_value((1835,1032))
        dialog.refresh()
        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertEqual(dialog.notice.text(),'')
        dialog.reject()
        dialog.deleteLater()

    def test_capture_navigation_lock_and_modifier_keys_in_both_dialogs(self):
        from dataclasses import replace
        from widgets import managed_dialog
        from hotkeys import Hotkeys
        cases = ((Qt.Key.Key_Tab,Qt.KeyboardModifier.NoModifier,'tab'),
                 (Qt.Key.Key_Tab,Qt.KeyboardModifier.ShiftModifier,'shift+tab'),
                 (Qt.Key.Key_Backtab,Qt.KeyboardModifier.ShiftModifier,'shift+tab'),
                 (Qt.Key.Key_Tab,Qt.KeyboardModifier.ControlModifier,'ctrl+tab'),
                 (Qt.Key.Key_CapsLock,Qt.KeyboardModifier.NoModifier,'caps lock'),
                 (Qt.Key.Key_CapsLock,Qt.KeyboardModifier.ControlModifier,'ctrl+caps lock'),
                 (Qt.Key.Key_Control,Qt.KeyboardModifier.NoModifier,'ctrl'),
                 (Qt.Key.Key_Shift,Qt.KeyboardModifier.NoModifier,'shift'),
                 (Qt.Key.Key_Alt,Qt.KeyboardModifier.NoModifier,'alt'),
                 (Qt.Key.Key_Meta,Qt.KeyboardModifier.NoModifier,'win'),
                 (Qt.Key.Key_Escape,Qt.KeyboardModifier.NoModifier,'esc'),
                 (Qt.Key.Key_Return,Qt.KeyboardModifier.NoModifier,'enter'))
        for key,modifiers,expected in cases:
            with self.subTest(key=expected), managed_dialog(KeyDialog('q',self.window)) as dialog:
                dialog.show()
                self.qt.processEvents()
                field = dialog.editor.field
                QTest.mouseClick(field,Qt.MouseButton.LeftButton)
                focused = dialog.focusWidget()
                QTest.keyClick(field,key,modifiers)
                self.assertIs(dialog.focusWidget(),focused)
                self.assertEqual(dialog.value(),expected)
                self.assertTrue(dialog.isVisible())
                self.assertIsNotNone(Hotkeys.compile(expected))
                with managed_dialog(KeyDialog(expected,self.window)) as reopened:
                    self.assertEqual(reopened.value(),expected)
                with managed_dialog(SettingsDialog(replace(self.window.settings,capture_hk='f12'),self.window)) as settings:
                    settings.show()
                    self.qt.processEvents()
                    target = settings.keys['pause_hk'].field
                    QTest.mouseClick(target,Qt.MouseButton.LeftButton)
                    focused = settings.focusWidget()
                    QTest.keyClick(target,key,modifiers)
                    self.assertIs(settings.focusWidget(),focused)
                    self.assertEqual(settings.value().pause_hk,expected)

    def test_ctrl_is_committed_on_release_without_overwriting_chord(self):
        from widgets import managed_dialog
        with managed_dialog(KeyDialog('q',self.window)) as dialog:
            dialog.show()
            self.qt.processEvents()
            field = dialog.editor.field
            QTest.mouseClick(field,Qt.MouseButton.LeftButton)
            QTest.keyPress(field,Qt.Key.Key_Control)
            self.assertEqual(dialog.value(),'q')
            QTest.keyRelease(field,Qt.Key.Key_Control)
            self.assertEqual(dialog.value(),'ctrl')
            QTest.keyPress(field,Qt.Key.Key_Control)
            QTest.keyPress(field,Qt.Key.Key_K,Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(dialog.value(),'ctrl+k')
            QTest.keyRelease(field,Qt.Key.Key_K,Qt.KeyboardModifier.ControlModifier)
            QTest.keyRelease(field,Qt.Key.Key_Control)
            self.assertEqual(dialog.value(),'ctrl+k')
            QTest.mouseClick(field,Qt.MouseButton.LeftButton)
            QTest.keyPress(field,Qt.Key.Key_Control)
            dialog.editor.clearFocus()
            QTest.keyRelease(field,Qt.Key.Key_Control)
            self.assertEqual(dialog.value(),'ctrl+k')

    def test_modifier_conflict_edit_keeps_key_and_reports_in_log(self):
        w = self.window
        row = w.profile.rows[0]
        w.save()
        with patch('app.KeyDialog') as factory, patch.object(QMessageBox,'warning') as warning:
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.value.return_value = 'ctrl'
            w.edit_key(row.id)
            warning.assert_not_called()
        w.flush_pending_saves()
        self.assertEqual(row.move_hk,'q')
        self.assertEqual(read_json(w.path)['macros'][0]['move_hk'],'q')
        self.assertIn('단독',w.statusBar().currentMessage())

    def test_settings_modifier_conflict_preserves_settings_and_reports_in_log(self):
        from dataclasses import replace
        w = self.window
        original = w.settings
        w.profile.rows[0].move_hk = 'alt+q'
        w.rebind()
        with patch('app.SettingsDialog') as factory, patch.object(w,'error') as error, patch.object(w,'commit_settings') as commit:
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.value.return_value = replace(original,pause_hk='alt')
            w.settings_dialog()
            commit.assert_not_called()
            error.assert_not_called()
        self.assertIs(w.settings,original)
        self.assertIn('단독',w.statusBar().currentMessage())

    def test_resize_verifies_actual_size_even_when_automatic_drag_is_skipped(self):
        w = self.window
        with patch('native.get_long',return_value=0), patch('native.get_menu',return_value=0), \
             patch('native.adjust_dpi',return_value=1), patch('native.window_dpi',return_value=96), \
             patch('native.set_position',return_value=1), patch('native.valid_window',return_value=True), patch('interactive_resize.is_hung',return_value=False), patch('interactive_resize.refresh',return_value='갱신 확인 필요'):
            dialog = ResizeDialog((1280,720),w,
                                  request_resize=lambda size:resize_window(w.native,123,*size),
                                  read_size=lambda:(1920,1080))
            dialog.set_size((1920,1080))
            dialog.apply()
            self.assertTrue(dialog.pending)
            dialog.verify_resize()
            dialog.verify_resize()
            self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
            self.assertEqual(dialog.current_size,(1920,1080))
            dialog.deleteLater()

    def test_resize_refresh_failure_is_shown_in_completion_log(self):
        w=self.window
        w.target=123
        with patch('app.ResizeDialog') as factory, \
             patch.object(w.native,'exists',return_value=True), \
             patch.object(w.native,'size',return_value=(1280,720)), \
             patch.object(w.native,'resize') as resize, \
             patch('native.valid_window',return_value=True), patch('interactive_resize.is_hung',return_value=False), patch('interactive_resize.refresh',return_value='화면 갱신 자동 드래그: 마우스 버튼을 놓고 다시 적용해 주세요.'):
            dialog=factory.return_value
            dialog.current_size=(1920,1080)
            dialog.convert_profile.isChecked.return_value=False
            def execute():
                factory.call_args.kwargs['request_resize']((1920,1080))
                return QDialog.DialogCode.Accepted
            dialog.exec.side_effect=execute
            w.resize_dialog()
            resize.assert_called_once_with(123,1920,1080)
        self.assertIn('크기 변경 완료',w.statusBar().currentMessage())
        self.assertIn('마우스 버튼을 놓고',w.statusBar().currentMessage())

    def test_unselected_start_and_capture_do_not_fall_back_to_desktop(self):
        w = self.window
        self.assertIsNone(w.target)
        self.assertEqual(w.targets.currentText(), '미선택')
        before = w.profile.export()
        with patch.object(w.native, 'windows') as windows, patch.object(w.native, 'cursor') as cursor, patch.object(w, 'convert_dialog') as convert:
            w.toggle()
            self.assertFalse(w.armed)
            self.assertIn('앱을 선택', w.statusBar().currentMessage())
            w.dispatch('capture_selected', '')
            w.tick()
        windows.assert_called_once()
        cursor.assert_not_called()
        convert.assert_not_called()
        self.assertEqual(w.profile.export(), before)
        self.assertEqual(w.cursor_label.text(), '— , —')

    def test_unselecting_fullscreen_stops_and_disables_action_dispatch(self):
        w = self.window
        w.targets.setCurrentIndex(w.targets.findData(0))
        w.toggle()
        self.assertTrue(w.armed)
        w.targets.setCurrentIndex(0)
        self.assertIsNone(w.target)
        self.assertFalse(w.armed)
        self.assertIsNone(w.session.plan)
        with patch.object(w.engine, 'submit') as submit:
            w.dispatch('move', w.profile.rows[0].id)
        submit.assert_not_called()
        with self.assertRaises(ValueError):
            ExecutionPlan(None, None, None)

    def test_unselected_script_editor_does_not_capture_external_clicks(self):
        w = self.window
        editor = ScriptEditor('', w.native, None, w)
        w.hook_enabled = True
        try:
            with patch('script_editor.CoordinateHook') as hook, patch.object(w.native, 'cursor') as cursor:
                editor.show()
                self.qt.processEvents()
                editor.pick_coordinate()
                hook.assert_not_called()
                cursor.assert_not_called()
            self.assertEqual(editor.editor.toPlainText(), '')
            self.assertIn('선택', editor.validation.text())
        finally:
            w.hook_enabled = False
            editor.reject()
            editor.deleteLater()

    def test_missing_saved_app_defaults_to_unselected_and_preserves_identity(self):
        from domain import atomic_json
        identity = {'exe': 'Game.exe', 'class': 'Game', 'title': 'Game'}
        with tempfile.TemporaryDirectory() as folder:
            profile = Profile.default()
            profile.target_program = identity
            atomic_json(Path(folder) / 'macro.json', profile.export())
            with patch('app.Windows.windows', return_value=[]):
                other = MainWindow(data_dir=folder, hook_enabled=False)
            try:
                self.assertIsNone(other.target)
                self.assertEqual(other.targets.currentText(), '미선택')
                self.assertEqual(other.profile.target_program, identity)
            finally:
                other.shutdown()
                other.close()
                other.deleteLater()
                self.qt.processEvents()

    def test_physical_alias_edit_keeps_previous_shortcut_disk_and_bindings(self):
        w = self.window
        w.profile.rows[0].move_hk = 'shift+-'
        self.assertTrue(w.save())
        w.rebind()
        before, bindings = w.path.read_bytes(), list(w.hooks.bindings)
        row = w.profile.rows[1]
        with patch('app.KeyDialog') as factory, patch.object(QMessageBox, 'warning') as warning:
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.value.return_value = 'shift+_'
            w.edit_key(row.id)
            warning.assert_not_called()
        w.flush_pending_saves()
        self.assertEqual(row.move_hk, 'w')
        self.assertEqual(w.path.read_bytes(), before)
        self.assertEqual(w.hooks.bindings, bindings)
        self.assertIn('실제 입력이 중복', w.statusBar().currentMessage())

    def test_physical_settings_conflicts_keep_settings_and_use_log(self):
        from dataclasses import replace
        w = self.window
        w.profile.rows[0].move_hk = 'shift+-'
        self.assertTrue(w.save())
        w.rebind()
        original = w.settings
        before = w.settings_path.read_bytes()
        for changes in ({'pause_hk': 'shift+_'}, {'capture_hk': 'shift+_'},
                        {'pause_hk': 'shift+/', 'capture_hk': 'shift+?'}):
            with self.subTest(changes=changes), patch('app.SettingsDialog') as factory, \
                 patch.object(w, 'error') as error, patch.object(w, 'commit_settings') as commit:
                factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
                factory.return_value.value.return_value = replace(original, **changes)
                w.settings_dialog()
                commit.assert_not_called()
                error.assert_not_called()
            self.assertIs(w.settings, original)
            self.assertEqual(w.settings_path.read_bytes(), before)
            self.assertIn('실제 입력이 중복', w.statusBar().currentMessage())

    def test_physical_conflict_profile_open_preserves_current_profile_and_draft(self):
        from domain import atomic_json
        w = self.window
        original, path = w.profile, w.path
        invalid = path.with_name('conflicting.json')
        profile = Profile.default()
        profile.rows[0].move_hk, profile.rows[1].move_hk = 'shift+-', 'shift+_'
        atomic_json(invalid, profile.export())
        before, settings = invalid.read_bytes(), w.settings_path.read_bytes()
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        with patch.object(w, 'confirm_changes', return_value=True), \
             patch.object(QFileDialog, 'getOpenFileName', return_value=(str(invalid), '')), \
             patch.object(w, 'error') as error:
            w.open_profile()
        self.assertIs(w.profile, original)
        self.assertEqual(w.path, path)
        self.assertIs(w.script_editor, editor)
        self.assertIn('실제 입력이 중복', error.call_args.args[0])
        self.assertEqual(invalid.read_bytes(), before)
        self.assertEqual(w.settings_path.read_bytes(), settings)

    def test_physical_conflict_startup_does_not_bind_or_rewrite_profile(self):
        from domain import atomic_json
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'macro.json'
            profile = Profile.default()
            profile.rows[0].move_hk, profile.rows[1].move_hk = 'shift+-', 'shift+_'
            atomic_json(path, profile.export())
            before = path.read_bytes()
            other = MainWindow(data_dir=folder, hook_enabled=False)
            try:
                self.assertFalse(other.armed)
                self.assertEqual(other.hooks.bindings, [])
                self.assertIn('실제 입력이 중복', other.load_error)
                self.assertEqual(other.profile.rows[0].move_hk, 'shift+-')
                self.assertEqual(path.read_bytes(), before)
            finally:
                other.shutdown()
                other.close()
                other.deleteLater()
                self.qt.processEvents()
            self.assertEqual(path.read_bytes(), before)

    def test_physical_conflict_is_rejected_before_profile_save(self):
        w = self.window
        w.flush_pending_saves()
        before = w.path.read_bytes()
        w.profile.rows[0].move_hk, w.profile.rows[1].move_hk = 'shift+-', 'shift+_'
        with patch.object(w, 'error'), patch.object(w.store, 'save') as save:
            self.assertFalse(w.save())
            save.assert_not_called()
        self.assertEqual(w.path.read_bytes(), before)
        w.profile.rows[0].move_hk, w.profile.rows[1].move_hk = 'q', 'w'

    def test_keypad_plus_and_equals_can_be_registered_and_saved(self):
        w = self.window
        w.profile.rows[0].move_hk = 'plus'
        w.save()
        w.rebind()
        row = w.profile.rows[1]
        with patch('app.KeyDialog') as factory:
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.value.return_value = '='
            w.edit_key(row.id)
        w.flush_pending_saves()
        self.assertEqual(row.move_hk, '=')
        self.assertEqual(read_json(w.path)['macros'][1]['move_hk'], '=')
        self.assertIn('plus', [b.key for b in w.hooks.bindings])
        self.assertIn('=', [b.key for b in w.hooks.bindings])

    def test_multiple_conflicts_can_be_repaired_one_at_a_time(self):
        from domain import Row, atomic_json
        for auto_pause in (False, True):
            with self.subTest(auto_pause=auto_pause), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'macro.json'
                profile = Profile([Row(move_hk=k) for k in ('shift+-', 'shift+_', 'shift+/', 'shift+?')])
                atomic_json(path, profile.export())
                before = path.read_bytes()
                other = MainWindow(data_dir=folder, hook_enabled=False)
                other.settings.auto_pause = auto_pause
                try:
                    with patch('app.KeyDialog') as factory:
                        factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
                        factory.return_value.value.return_value = ''
                        other.edit_key(other.profile.rows[1].id)
                        self.assertEqual(other.profile.rows[1].move_hk, '')
                        self.assertTrue(other.dirty)
                        self.assertFalse(other.armed)
                        self.assertEqual(other.hooks.bindings, [])
                        self.assertIn('남은 충돌', other.statusBar().currentMessage())
                        self.assertFalse(other.save())
                        other.flush_pending_saves()
                        self.assertEqual(path.read_bytes(), before)
                        # Recovery must not allow introducing another conflict.
                        factory.return_value.value.return_value = 'shift+?'
                        other.edit_key(other.profile.rows[1].id)
                        self.assertEqual(other.profile.rows[1].move_hk, '')
                        factory.return_value.value.return_value = ''
                        other.edit_key(other.profile.rows[3].id)
                    other.flush_pending_saves()
                    self.assertFalse(other.dirty)
                    self.assertFalse(other.armed)
                    expected = ['shift+-', '', 'shift+/', '']
                    self.assertEqual([r.move_hk for r in other.profile.rows], expected)
                    self.assertEqual([r.move_hk for r in Profile.read(read_json(path)).rows], expected)
                    self.assertEqual(len(other.hooks.bindings), 4)
                finally:
                    other.shutdown()
                    other.close()
                    other.deleteLater()
                    self.qt.processEvents()

    def test_discarding_partial_shortcut_repair_keeps_original_file(self):
        from domain import Row, atomic_json
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'macro.json'
            atomic_json(path, Profile([Row(move_hk=k) for k in
                                     ('shift+-', 'shift+_', 'shift+/', 'shift+?')]).export())
            before = path.read_bytes()
            other = MainWindow(data_dir=folder, hook_enabled=False)
            try:
                with patch('app.KeyDialog') as factory:
                    factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
                    factory.return_value.value.return_value = 'f1'
                    other.edit_key(other.profile.rows[1].id)
                self.assertEqual(other.profile.rows[1].move_hk, 'f1')
                self.assertTrue(other.dirty)
                with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Discard):
                    self.assertTrue(other.confirm_changes())
                self.assertEqual(path.read_bytes(), before)
            finally:
                other.shutdown()
                other.close()
                other.deleteLater()
                self.qt.processEvents()

    def test_settings_resolution_saves_pending_rows_and_preserves_them_on_failure(self):
        from dataclasses import asdict, replace
        from domain import Row, Settings, atomic_json
        for fail_profile_save in (False, True):
            with self.subTest(fail_profile_save=fail_profile_save), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'macro.json'
                atomic_json(path, Profile([Row(move_hk=k) for k in
                                         ('shift+-', 'shift+/', 'shift+?')]).export())
                atomic_json(Path(folder) / 'setting.json', asdict(Settings(pause_hk='shift+_')))
                before = path.read_bytes()
                other = MainWindow(data_dir=folder, hook_enabled=False)
                try:
                    with patch('app.KeyDialog') as factory:
                        factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
                        factory.return_value.value.return_value = 'f1'
                        other.edit_key(other.profile.rows[2].id)
                    self.assertTrue(other.dirty)
                    self.assertEqual(path.read_bytes(), before)
                    def write(destination, data):
                        if fail_profile_save and Path(destination) == path:
                            raise OSError('profile write unavailable')
                        return atomic_json(destination, data)
                    with patch('app.SettingsDialog') as factory, patch('persistence.atomic_json', side_effect=write):
                        factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
                        factory.return_value.value.return_value = replace(other.settings, pause_hk='f2')
                        other.settings_dialog()
                        other.flush_pending_saves()
                    self.assertEqual(other.settings.pause_hk, 'f2')
                    self.assertEqual(read_json(other.settings_path)['pause_hk'], 'f2')
                    self.assertEqual(other.profile.rows[2].move_hk, 'f1')
                    self.assertEqual(other.dirty, fail_profile_save)
                    if fail_profile_save:
                        self.assertEqual(path.read_bytes(), before)
                        self.assertIn('profile write unavailable', other.statusBar().currentMessage())
                        self.assertTrue(other.save())
                    self.assertEqual(read_json(path)['macros'][2]['move_hk'], 'f1')
                    self.assertFalse(other.dirty)
                finally:
                    other.shutdown()
                    other.dirty = False
                    other.close()
                    other.deleteLater()
                    self.qt.processEvents()

    def test_deleting_conflicting_rows_stays_blocked_until_final_repair(self):
        from domain import Row, atomic_json
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'macro.json'
            atomic_json(path, Profile([Row(move_hk=k) for k in
                                     ('shift+-', 'shift+_', 'shift+/', 'shift+?')]).export())
            before = path.read_bytes()
            other = MainWindow(data_dir=folder, hook_enabled=False)
            try:
                with patch.object(other.hooks, 'start') as start:
                    other.hook_enabled = True
                    other.table.selectRow(1)
                    other.delete_rows()
                    self.assertEqual(other.table.currentRow(), 0)
                    self.assertFalse(other.armed)
                    self.assertEqual(other.hooks.bindings, [])
                    self.assertTrue(other.dirty)
                    self.assertIn('남은 충돌', other.statusBar().currentMessage())
                    start.assert_not_called()
                    self.assertEqual(path.read_bytes(), before)
                    other.table.selectRow(2)
                    other.delete_rows()
                    other.flush_pending_saves()
                    self.assertEqual(other.table.currentRow(), 1)
                    start.assert_called_once()
                    self.assertFalse(other.dirty)
                    self.assertEqual([r['move_hk'] for r in read_json(path)['macros']], ['shift+-', 'shift+/'])
            finally:
                other.hook_enabled = False
                other.shutdown()
                other.close()
                other.deleteLater()
                self.qt.processEvents()

    def test_failed_validation_makes_older_save_completion_stale(self):
        from concurrent.futures import Future
        from persistence import SaveResult
        from shortcuts import ShortcutError
        w = self.window
        w.flush_pending_saves()
        pending = Future()
        with patch.object(w.store, 'save', return_value=pending):
            w.profile.rows[0].memo = 'old snapshot'
            self.assertTrue(w.save(automatic=True))
        w.profile.rows[0].memo = 'new unsaved edit'
        with patch.object(w, 'validate_profile_shortcuts', side_effect=ShortcutError('conflict')):
            self.assertFalse(w.save(automatic=True))
        pending.set_result(SaveResult(True))
        w.flush_pending_saves()
        self.qt.processEvents()
        self.assertTrue(w.dirty)
        self.assertEqual(w.profile.rows[0].memo, 'new unsaved edit')
        self.assertNotEqual(read_json(w.path)['macros'][0]['memo'], 'new unsaved edit')
        self.assertTrue(w.save())
        self.assertEqual(read_json(w.path)['macros'][0]['memo'], 'new unsaved edit')

    def test_wheel_over_script_examples_does_not_replace_draft(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QWheelEvent
        w = self.window
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        editor.editor.setPlainText('sleep 123')
        combo = editor.templates
        combo.setFocus()
        event = QWheelEvent(QPointF(8,8), QPointF(combo.mapToGlobal(QPoint(8,8))),
                            QPoint(), QPoint(0,-120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(combo, event)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertEqual(editor.editor.toPlainText(), 'sleep 123')
        combo.setCurrentIndex(1)
        combo.activated.emit(1)
        self.assertIn('curve X,Y mouse', editor.editor.toPlainText())
        self.assertNotIn('sleep 123', editor.editor.toPlainText())
        w.finish_script_editor()

    def test_system_theme_signal_refreshes_open_editor_without_losing_draft(self):
        from theme import DARK, LIGHT
        w = self.window
        hints = QApplication.styleHints()
        w.settings.theme = 'System'
        with patch.object(hints, 'colorScheme', return_value=Qt.ColorScheme.Light):
            w.apply_theme()
        self.assertIs(w.theme_colors, LIGHT)
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        draft = 'CLICK 10,20 // 좌표 설명 😀'
        editor.editor.setPlainText(draft)
        editor.editor.document().firstBlock().layout().formats()
        position = editor.editor.textCursor().position()
        w.armed = True
        for scheme, colors in ((Qt.ColorScheme.Dark, DARK), (Qt.ColorScheme.Light, LIGHT)):
            with self.subTest(scheme=scheme), patch.object(hints, 'colorScheme', return_value=scheme):
                hints.colorSchemeChanged.emit(scheme)
                self.qt.processEvents()
                self.assertIs(w.theme_colors, colors)
                self.assertIs(w.script_editor, editor)
                self.assertEqual(editor.editor.toPlainText(), draft)
                self.assertEqual(editor.editor.textCursor().position(), position)
                self.assertTrue(w.armed)
                self.assertEqual(editor.highlighter.command.foreground().color().name(), colors.command)
                formats = editor.editor.document().firstBlock().layout().formats()
                self.assertTrue(any(f.format.foreground().color().name() == colors.command for f in formats))
                self.assertTrue(any(f.format.foreground().color().name() == colors.comment for f in formats))

    def test_system_theme_signal_respects_explicit_theme_and_closed_window(self):
        w = self.window
        hints = QApplication.styleHints()
        for theme in ('Light', 'Dark'):
            w.settings.theme = theme
            w.apply_theme()
            with patch.object(w, 'apply_theme') as refresh:
                hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
                hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
                self.qt.processEvents()
                refresh.assert_not_called()
        w.settings.theme = 'System'
        w.shutdown()
        with patch.object(w, 'apply_theme') as refresh:
            hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
            self.qt.processEvents()
            refresh.assert_not_called()

    def test_uppercase_and_mixed_case_commands_keep_command_and_comment_colors(self):
        from PySide6.QtWidgets import QPlainTextEdit
        from script_editor import Highlighter
        from script_language import compile_script
        from theme import DARK, LIGHT
        editor = QPlainTextEdit()
        try:
            for colors in (DARK, LIGHT):
                highlighter = Highlighter(editor.document(), colors)
                for command in ('click', 'CLICK', 'ClIcK'):
                    source = f'{command} 10,20 // 주석 😀'
                    compile_script(source)
                    editor.setPlainText(source)
                    highlighter.rehighlight()
                    self.qt.processEvents()
                    formats = editor.document().firstBlock().layout().formats()
                    commands = [f for f in formats if f.format.foreground().color().name() == colors.command]
                    comments = [f for f in formats if f.format.foreground().color().name() == colors.comment]
                    self.assertEqual([(f.start, f.length) for f in commands], [(0, 5)])
                    start = len(source[:source.index('//')].encode('utf-16-le')) // 2
                    length = len(source[source.index('//'):].encode('utf-16-le')) // 2
                    self.assertEqual([(f.start, f.length) for f in comments], [(start, length)])
                highlighter.setDocument(None)
        finally:
            editor.deleteLater()

    def test_capture_hook_failure_is_reported_in_editor_log(self):
        from unittest.mock import Mock
        w = self.window
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        editor.coordinate_hook = hook = Mock()
        editor.editor.setPlainText('sleep 123')
        editor.capture_failed.emit(hook, 'Windows 오류 5')
        self.qt.processEvents()
        hook.close.assert_called_once_with()
        self.assertIsNone(editor.coordinate_hook)
        self.assertIn('좌표 입력 감시 실패', editor.validation.text())
        self.assertIn('Windows 오류 5', editor.validation.text())
        self.assertEqual(editor.editor.toPlainText(), 'sleep 123')

    def test_stale_capture_hook_error_does_not_close_replacement(self):
        from unittest.mock import Mock
        w = self.window
        w.edit_script(w.profile.rows[0].id)
        editor = w.script_editor
        first, second = Mock(), Mock()
        editor.coordinate_hook = second
        editor.capture_failed.emit(first, 'old error')
        self.qt.processEvents()
        second.close.assert_not_called()
        self.assertIs(editor.coordinate_hook, second)
        self.assertNotIn('old error', editor.validation.text())
