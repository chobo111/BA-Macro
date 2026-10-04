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
from app import MainWindow, KeyDialog, SettingsDialog, ResizeDialog
from script_editor import ScriptEditor
from domain import MODES


class UiTests(unittest.TestCase):
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
        self.assertIn(VERSION, menus['정보'].actions()[0].text())
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
                old_revision = w.save_revision
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
        w.armed = True
        with patch('app.compile_script', wraps=compile_script) as compile_once, patch.object(w.engine, 'submit', return_value=True) as submit, patch.object(w.native, 'active', return_value=True):
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
        QTest.qWait(60)
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
        self.assertEqual(set(dialog.checks), {'auto_pause', 'exclusive_hook', 'return_cursor', 'warn_app_mismatch'})
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
            w.targets.setCurrentIndex(1)
            self.assertEqual(Profile.read(self.saved(w.path)).target_program, identity)
            self.assertEqual(w.targets.currentText(), 'Game')
        w.target = 0
        with patch.object(w.native, 'windows', return_value=[]):
            w.refresh_windows()
        self.assertEqual(w.target, 0)
        self.assertEqual(w.profile.target_program, identity)
        with patch.object(w.native, 'windows', return_value=[(456, 'Game')]), patch.object(w.native, 'window_identity', return_value=identity):
            w.refresh_windows()
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
        with patch.object(w.native, "active", return_value=False), patch.object(w.native, "exists", return_value=True), patch.object(w.native, "cursor", return_value=(400, 500)), patch.object(w.native, "bounds", return_value=(0, 0, 1920, 1080)), patch.object(w.native, "size", return_value=(1920, 1080)):
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
        w.targets.setCurrentIndex(1)
        with patch.object(w.native, "windows", return_value=[(20, "다른 창"), (10, "게임")]) as windows:
            w.targets.showPopup()
            windows.assert_called_once()
            self.assertEqual(w.targets.currentData(), 10)
            self.assertEqual(w.targets.count(), 3)
            w.targets.hidePopup()

    def test_memo_width_and_pin(self):
        w = self.window
        self.assertEqual([r.move_hk for r in w.profile.rows], ["q", "w", "e"])
        self.assertTrue(all(r.memo == "" for r in w.profile.rows))
        base = w.table.columnWidth(4)
        w.table.item(0, 4).setText("긴 메모 내용 " * 30)
        self.assertGreater(w.table.columnWidth(4), base)
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
        with patch.object(w.native, "exists", return_value=True), patch.object(w.native, "window_identity", return_value={"exe": "Game.exe", "class": "Game", "title": "Game"}):
            QTest.mouseClick(w.pause_button, Qt.MouseButton.LeftButton)
            self.assertTrue(w.armed)
            w.dispatch("pause", "")
            self.assertFalse(w.armed)
            w.dispatch("pause", "")
            self.assertTrue(w.armed)

    def test_fullscreen_start_capture_and_fixed_controls(self):
        w = self.window
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
        w.armed = True
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
        with patch.object(QFileDialog,'getSaveFileName') as save_dialog:
            w.new_profile()
            save_dialog.assert_not_called()
        self.assertNotEqual(w.path,old_path)
        self.assertEqual(old_path.read_bytes(),old_bytes)
        self.assertTrue(w.path.exists())
        self.assertEqual(self.saved(w.settings_path)['last_profile'],w.path.name)
        w.flush_pending_saves()
        self.assertFalse(w.dirty)

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
        self.assertIn('비율',dialog.notice.text())
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

    def test_capture_resolution_mismatch_does_not_mix_coordinates(self):
        w=self.window
        w.target=99
        for resolution in ((1920,1080),None):
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

    def test_script_capture_blocks_mismatched_resolution(self):
        w=self.window
        w.profile.resolution=(1920,1080)
        editor=ScriptEditor('',w.native,99,w)
        with patch.object(w.native,'size',return_value=(1280,720)), \
             patch.object(w.native,'bounds',return_value=(0,0,1280,720)):
            editor.pick_coordinate((100,200))
        self.assertEqual(editor.editor.toPlainText(),'')
        self.assertIsNone(editor.capture_resolution)
        editor.close()

    def test_script_capture_new_profile_records_basis_only_on_save(self):
        w=self.window
        editor=ScriptEditor('',w.native,99,w)
        with patch.object(w.native,'size',return_value=(1280,720)), \
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
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Cancel):
            w.new_profile()
        self.assertIs(w.profile,previous)
        self.assertIs(w.script_editor,editor)
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Discard):
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

    def test_reopening_windows_shortcut_does_not_clear_saved_binding(self):
        w = self.window
        row = w.profile.rows[0]
        row.move_hk = 'win+q'
        self.assertTrue(w.save())
        w.rebuild()
        with patch.object(KeyDialog, 'exec', return_value=QDialog.DialogCode.Accepted):
            w.edit_key(row.id)
        w.flush_pending_saves()
        self.assertEqual(row.move_hk, 'win+q')
        self.assertEqual(read_json(w.path)['macros'][0]['move_hk'], 'win+q')
        self.assertIn('win+q', [binding.key for binding in w.hooks.bindings])

    def test_win_and_plus_capture_round_trip_in_both_dialogs(self):
        from dataclasses import replace
        from hotkeys import Hotkeys
        from widgets import managed_dialog
        cases = ((Qt.Key.Key_Q, Qt.KeyboardModifier.MetaModifier, 'win+q'),
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
        w.armed = True
        row = w.profile.rows[0]
        row.action_mode, row.script = 'script', 'click 10,20'
        with patch.object(w.engine, 'submit', return_value=True) as submit, \
             patch('app.compile_script') as compile_script, \
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
        w.armed = True
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
