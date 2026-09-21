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
        self.assertFalse(w.dirty)
        self.assertEqual(Profile.read(read_json(w.path)).rows[0].x,125)
        w.table.item(0, 4).setText("첫 번째 스킬")
        rid = w.profile.rows[0].id
        w.move_row(0, 2)
        self.assertEqual(w.profile.rows[2].id, rid)
        w.path = Path(self.directory.name) / "profile.json"
        self.assertTrue(w.save())
        self.assertFalse(w.dirty)
        saved = Profile.read(read_json(w.path))
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
        key = KeyDialog("ctrl+q", w)
        self.assertEqual(key.value(), "ctrl+q")
        dialog = SettingsDialog(w.settings, w)
        self.assertEqual(dialog.value().click_duration, 50)
        self.assertEqual(dialog.value().curve_duration, 10)
        self.assertTrue(dialog.value().exclusive_hook)
        dialog.times['curve_duration'].setValue(25)
        self.assertEqual(dialog.value().curve_duration, 25)
        dialog.times["click_duration"].setValue(-1)
        self.assertEqual(dialog.value().click_duration, 1)
        key.deleteLater()
        dialog.deleteLater()

    def test_open_legacy_profile(self):
        w = self.window
        legacy = Path(self.directory.name) / "old.json"
        legacy.write_text('{"target_resolution":"1280x720","macros":[{"move_hk":"Q","save_hk":"SHIFT+Q","coords":"80, 90","action_mode":"move_only"}]}', encoding="utf-8")
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(legacy), "")):
            w.open_profile()
        self.assertEqual(w.table.rowCount(), 1)
        self.assertEqual(w.profile.rows[0].x, 80)

    def test_cancel_close_retains_unsaved_changes(self):
        w = self.window
        with patch('app.atomic_json', side_effect=OSError('disk unavailable')):
            w.changed()
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(w.confirm_changes())
        self.assertTrue(w.dirty)

    def test_save_dialog_korean_buttons_and_actions(self):
        w = self.window
        with patch('app.atomic_json', side_effect=OSError('disk unavailable')):
            w.changed()
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
        self.assertEqual(dialog.ratio.currentText(), "직접 입력")
        self.assertEqual(dialog.value(), (1234, 678))
        for ratio, aspect in (("16:9", (16, 9)), ("4:3", (4, 3))):
            dialog.ratio.setCurrentText(ratio)
            self.assertFalse(dialog.width_input.isEnabled())
            for index in range(dialog.presets.count()):
                dialog.presets.setCurrentIndex(index)
                w, h = dialog.value()
                self.assertEqual(w * aspect[1], h * aspect[0])
        dialog.ratio.setCurrentText("직접 입력")
        self.assertTrue(dialog.width_input.isEnabled())
        self.assertEqual(dialog.value(), (1234, 678))
        dialog.deleteLater()

    def test_pause_key_works_while_paused(self):
        w = self.window
        w.target = 99
        with patch.object(w.native, "exists", return_value=True):
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
        loaded = Profile.read(read_json(w.path))
        self.assertEqual(loaded.rows[0].script, script)
        w.table.cellWidget(0, 3).setCurrentIndex(list(MODES).index('move_only'))
        self.qt.processEvents()
        self.assertEqual(w.table.columnSpan(0, 1), 1)
        self.assertEqual(w.table.cellWidget(0, 1).maximum(), 99999)
        self.assertEqual(w.table.cellWidget(0, 2).minimum(), -99999)
        self.assertEqual(row.script, script)
        dialog.deleteLater()

    def test_editor_drag_and_background_right_click(self):
        w = self.window
        dialog = ScriptEditor('', w.native, 0, w)
        dialog.show()
        self.qt.processEvents()
        self.assertEqual(dialog.validation.text(), '')
        self.assertIn('우클릭', dialog.editor.placeholderText())
        self.assertNotIn('click 420', dialog.editor.placeholderText())
        self.assertEqual(dialog.size(), dialog.panel.size())
        self.assertLessEqual(dialog.panel.width(), 600)
        self.assertEqual(dialog.titlebar.height(), 42)
        before = dialog.pos()
        parent_before = w.pos()
        self.assertEqual(dialog.panel.mapToGlobal(QPoint()), w.frameGeometry().topLeft())
        QTest.mousePress(dialog.titlebar, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
        QTest.mouseMove(dialog.titlebar, QPoint(22, 22))
        QTest.mouseRelease(dialog.titlebar, Qt.MouseButton.LeftButton, pos=QPoint(22, 22))
        self.assertNotEqual(dialog.pos(), before)
        self.assertEqual(dialog.size(), dialog.panel.size())
        self.assertEqual(w.pos() - parent_before, dialog.pos() - before)
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
        self.assertEqual(read_json(directory / 'setting.json')['last_profile'], 'macro.json')
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
            recent = read_json(w.settings_path)['last_profile']
            self.assertEqual(recent, 'macro_1.json')
            self.assertEqual(local.read_bytes(), original)
            w.profile.rows[0].memo = '수정'
            self.assertTrue(w.save())
            self.assertEqual(read_json(w.settings_path)['last_profile'], recent)
            copied = Profile.read(read_json(Path(self.directory.name) / recent))
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
            self.assertEqual(Profile.read(read_json(copy)).rows[0].memo, '')

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
        w.settings.curve_duration = 25
        w.armed = True
        with patch.object(w.engine, 'submit', return_value=True) as submit:
            w.dispatch('move', row.id)
            self.assertEqual(submit.call_args.args[0].curve_duration, 25)
        w.persist_settings()
        from domain import Settings
        loaded = Settings.read(read_json(w.settings_path))
        self.assertEqual(loaded.curve_duration, 25)
        self.assertTrue(loaded.exclusive_hook)
        self.assertEqual(Settings.read({}).curve_duration, 10)
        self.assertTrue(Settings.read({}).exclusive_hook)
        self.assertFalse(Settings.read({'exclusive_hook': False}).exclusive_hook)
        for value in (-1, 10001):
            with self.assertRaises(ValueError):
                Settings.read({'curve_duration': value})

    def test_autosave_all_edits_and_new_profile_preserves_old(self):
        w=self.window
        self.assertNotIn('저장',[b.text() for b in w.centralWidget().findChildren(QPushButton)])
        row=w.profile.rows[0]
        w.set_value(row.id,'memo','자동 메모')
        w.set_value(row.id,'x',12345)
        w.set_value(row.id,'action_mode','script')
        w.set_value(row.id,'script','click mouse')
        saved=Profile.read(read_json(w.path)).rows[0]
        self.assertEqual((saved.memo,saved.x,saved.action_mode,saved.script),('자동 메모',12345,'script','click mouse'))
        w.add_row()
        self.assertEqual(len(Profile.read(read_json(w.path)).rows),4)
        w.move_row(3,0)
        self.assertEqual(Profile.read(read_json(w.path)).rows[0].id,w.profile.rows[0].id)
        w.table.selectRow(0)
        w.delete_rows()
        self.assertEqual(len(Profile.read(read_json(w.path)).rows),3)
        old_path,old_bytes=w.path,w.path.read_bytes()
        with patch.object(QFileDialog,'getSaveFileName') as save_dialog:
            w.new_profile()
            save_dialog.assert_not_called()
        self.assertNotEqual(w.path,old_path)
        self.assertEqual(old_path.read_bytes(),old_bytes)
        self.assertTrue(w.path.exists())
        self.assertEqual(read_json(w.settings_path)['last_profile'],w.path.name)
        self.assertFalse(w.dirty)

    def test_live_memo_is_saved_before_editor_closes(self):
        from PySide6.QtWidgets import QLineEdit
        w=self.window
        w.table.editItem(w.table.item(0,4))
        self.qt.processEvents()
        editor=self.qt.focusWidget()
        self.assertIsInstance(editor,QLineEdit)
        QTest.keyClicks(editor,'live memo')
        self.assertEqual(Profile.read(read_json(w.path)).rows[0].memo,'live memo')
        self.assertFalse(w.dirty)

    def test_autosave_failure_preserves_file_and_reports_error(self):
        w=self.window
        original=w.path.read_bytes()
        with patch('app.atomic_json',side_effect=OSError('disk unavailable')), patch.object(QMessageBox,'warning') as popup:
            w.set_value(w.profile.rows[0].id,'memo','pending')
            popup.assert_not_called()
        self.assertTrue(w.dirty)
        self.assertIn('자동저장 실패',w.statusBar().currentMessage())
        self.assertEqual(w.path.read_bytes(),original)
        self.assertTrue(w.save(automatic=True))
        self.assertFalse(w.dirty)
        self.assertEqual(Profile.read(read_json(w.path)).rows[0].memo,'pending')

    def test_script_accept_is_automatically_saved(self):
        w=self.window
        row=w.profile.rows[0]
        row.action_mode='script'
        with patch('app.ScriptEditor') as editor:
            editor.return_value.exec.return_value=QDialog.DialogCode.Accepted
            editor.return_value.source='click 100,200'
            w.edit_script(row.id)
        self.assertEqual(Profile.read(read_json(w.path)).rows[0].script,'click 100,200')
        self.assertFalse(w.dirty)
