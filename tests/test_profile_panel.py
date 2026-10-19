import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from concurrent.futures import Future
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer, QCoreApplication, QEvent, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton, QProgressDialog

from app import MainWindow
from domain import Profile, Settings, atomic_json, read_json, WindowSizeUnavailable
from execution_plan import ExecutionPlan
from persistence import SaveResult
from profile_catalog import profile_entries


class ProfileMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.patches = ExitStack()
        self.patches.enter_context(patch('app.Windows.windows', return_value=[]))
        self.w = MainWindow(data_dir=self.directory, hook_enabled=False)
        self.w.timer.stop()
        self.w.settings.auto_pause = False
        self.w.show()
        self.qt.processEvents()

    def tearDown(self):
        self.w.shutdown()
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.patches.close()
        self.folder.cleanup()

    def other(self, name='other.json'):
        path = self.directory / name
        profile = Profile.default()
        profile.rows[0].memo = 'other profile'
        atomic_json(path, profile.export())
        return path

    def draft(self, text='sleep 12'):
        self.w.edit_script(self.w.profile.rows[0].id)
        self.w.script_editor.editor.setPlainText(text)
        return self.w.script_editor

    def test_profile_actions_are_in_titlebar_menu(self):
        self.assertEqual([a.text() for a in self.w.main_menu.actions() if not a.isSeparator()],
                         ['프로필 선택', '새 프로필…', '불러오기…', '복제…', '이름 변경…', '폴더 열기',
                          '환경설정', '프로필 해상도 변환…', '현재 프로필 정보', '앱 정보', '오류 로그'])
        self.assertFalse(hasattr(self.w, 'profile_panel'))
        self.assertFalse(hasattr(self.w,'cursor_label'))
        self.assertTrue(self.w.target_box.isAncestorOf(self.w.profile_name))
        self.assertTrue(self.w.target_box.isAncestorOf(self.w.profile_resolution))
        self.assertFalse(self.w.menuBar().isVisible())

    def test_menu_refreshes_on_open_and_marks_current_profile(self):
        w = self.w
        w.refresh_profiles()
        other = self.other()
        self.assertFalse(any(a.data() == str(other) for a in w.profile_menu.actions()))
        w.profile_menu.aboutToShow.emit()
        current = next(a for a in w.profile_menu.actions() if a.data() == str(w.path))
        destination = next(a for a in w.profile_menu.actions() if a.data() == str(other))
        self.assertTrue(current.isChecked())
        self.assertFalse(destination.isChecked())
        destination.trigger()
        self.assertEqual(w.path, other)
        self.assertTrue(destination.isChecked())
        self.assertFalse(current.isChecked())
        self.assertFalse(w.armed)

    def test_catalog_filters_unrelated_json_and_uses_only_current_folder(self):
        other = self.other()
        (self.directory / 'broken.json').write_text('{broken', encoding='utf-8')
        atomic_json(self.directory / 'other-settings.json', {'theme': 'Dark'})
        nested = self.directory / "nested"
        nested.mkdir()
        atomic_json(nested / "hidden.json", Profile.default().export())
        entries = profile_entries(self.directory)
        self.assertEqual(entries, [self.w.path, other])

    def test_profile_summary_keeps_saved_basis_separate_from_runtime_state(self):
        w = self.w
        w.profile.resolution = (1920,1080)
        w.profile.target_program = {'exe': 'Game.exe', 'class': 'Unity', 'title': 'Game'}
        w._selected_identity = dict(w.profile.target_program)
        w.target = 123
        w.settings.auto_convert_on_start = True
        w.update_labels()
        with patch.object(w.native, 'exists', return_value=True), patch.object(w.native, 'size', return_value=(1280,720)), patch.object(w.native, 'cursor', return_value=(4,5)):
            w.tick()
            self.assertEqual(w.profile_resolution.text(), '기준 1920×1080')
            self.assertEqual(w.windowTitle(), '4, 5')
            self.assertEqual(w.resize_button.text(), '현재 1280×720')
            self.assertTrue(w.target_info.isHidden())
            w.session.start(ExecutionPlan(123, (1920,1080), (1280,720), automatic=True))
            w.tick()
            self.assertIn('자동 변환 사용 중', w.target_info.text())
            w.target_toggle.setChecked(False)
            self.qt.processEvents()
            self.assertTrue(w.collapsed_status.isVisible())
            self.assertEqual(w.collapsed_status.text(), '자동 변환')
            self.assertEqual(w.collapsed_status.toolTip(), '자동 변환 사용 중')
            self.assertFalse(w.collapsed_status.property('warning'))
            w.stop()
            w.tick()
            self.assertNotIn('자동 변환', w.target_info.text())
            self.assertFalse(w.collapsed_status.isVisible())
            with patch.object(w.native, 'size', side_effect=WindowSizeUnavailable()):
                w.tick()
                self.assertEqual(w.resize_button.text(), '최소화')
        w._selected_identity = {'exe':'Other.exe', 'class':'Unity', 'title':'Other'}
        w.update_target_info((1280,720))
        self.assertIn('지정 앱과 다름', w.target_info.text())
        self.assertTrue(w.target_info.property('warning'))
        self.assertTrue(w.collapsed_status.isVisible())
        self.assertEqual(w.collapsed_status.text(), '앱 불일치')
        self.assertTrue(w.collapsed_status.property('warning'))
        w.target_toggle.setChecked(True)
        self.assertFalse(w.collapsed_status.isVisible())

    def test_failed_save_prevents_switch_and_keeps_unsaved_edits(self):
        w = self.w
        other = self.other()
        old_path, old_profile, original = w.path, w.profile, w.path.read_bytes()
        w.profile.rows[0].memo, w.dirty, w.armed = 'unsaved', True, True
        failed = Future()
        failed.set_result(SaveResult(False, error='disk full'))
        with patch.object(w.store, 'save', return_value=failed):
            self.assertFalse(w.switch_profile(other))
        self.assertIs(w.profile, old_profile)
        self.assertEqual(w.path, old_path)
        self.assertEqual(w.path.read_bytes(), original)
        self.assertEqual(w.profile.rows[0].memo, 'unsaved')
        self.assertTrue(w.dirty)
        self.assertFalse(w.armed)
        w.refresh_profiles()
        self.assertEqual([a.data() for a in w.profile_menu.actions() if a.isChecked()], [str(old_path)])
        self.assertIn('disk full', w.statusBar().currentMessage())

    def test_switch_waits_for_autosave_without_blocking_gui_or_allowing_reentry(self):
        w = self.w
        old_path, other, third = w.path, self.other(), self.other('third.json')
        entered, release = threading.Event(), threading.Event()
        observed = []
        def slow_write(path, data):
            if Path(path) == old_path and not entered.is_set():
                entered.set()
                if not release.wait(2):
                    raise OSError('GUI did not release writer')
            atomic_json(path, data)
        def heartbeat():
            observed.append((w.eligible('pause'), w.centralWidget().isEnabled(), w.switch_profile(third)))
            release.set()
        with patch('persistence.atomic_json', side_effect=slow_write):
            w.set_value(w.profile.rows[0].id, 'memo', 'latest edit')
            self.assertTrue(entered.wait(1))
            QTimer.singleShot(30, heartbeat)
            try:
                self.assertTrue(w.switch_profile(other))
            finally:
                release.set()
        self.assertEqual(observed, [(False, False, False)])
        self.assertEqual(read_json(old_path)['macros'][0]['memo'], 'latest edit')
        self.assertEqual(w.path, other)
        self.assertTrue(w.centralWidget().isEnabled())
        self.assertFalse(w.armed)

    def test_cancel_while_saving_keeps_current_profile(self):
        w = self.w
        old_path, other = w.path, self.other()
        pending = Future()
        w.dirty = True
        def cancel():
            dialog = w.findChild(QProgressDialog)
            dialog.findChild(QPushButton).click()
        with patch.object(w.store, 'save', return_value=pending):
            QTimer.singleShot(30, cancel)
            try:
                self.assertFalse(w.switch_profile(other))
            finally:
                pending.set_result(SaveResult(False, error='save unavailable'))
        w.flush_pending_saves()
        self.assertEqual(w.path, old_path)
        self.assertTrue(w.centralWidget().isEnabled())
        self.assertTrue(w.dirty)

    def test_cancel_script_prompt_preserves_draft(self):
        other, old = self.other(), self.w.path
        editor = self.draft()
        with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(self.w.switch_profile(other))
        self.assertEqual(self.w.path, old)
        self.assertIs(self.w.script_editor, editor)
        self.assertEqual(editor.editor.toPlainText(), 'sleep 12')

    def test_script_save_before_switch_updates_original_profile(self):
        other, old = self.other(), self.w.path
        self.draft()
        with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Save):
            self.assertTrue(self.w.switch_profile(other))
        self.assertEqual(read_json(old)['macros'][0]['script'], 'sleep 12')
        self.assertIsNone(self.w.script_editor)

    def test_discard_script_before_switch_preserves_original_file(self):
        other, old = self.other(), self.w.path
        before = old.read_bytes()
        self.draft()
        with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Discard):
            self.assertTrue(self.w.switch_profile(other))
        self.assertEqual(old.read_bytes(), before)

    def test_invalid_script_prevents_switch_after_save_choice(self):
        other, old = self.other(), self.w.path
        editor = self.draft('invalid_command')
        with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Save):
            self.assertFalse(self.w.switch_profile(other))
        self.assertEqual(self.w.path, old)
        self.assertIs(self.w.script_editor, editor)
        self.assertTrue(editor.validation.isVisible())

    def test_invalid_destination_does_not_ask_to_discard_draft(self):
        path = self.directory / 'invalid.json'
        path.write_text('{invalid', encoding='utf-8')
        editor = self.draft()
        with patch.object(QMessageBox, 'exec') as prompt:
            self.assertFalse(self.w.switch_profile(path))
        prompt.assert_not_called()
        self.assertIs(self.w.script_editor, editor)

    def test_duplicate_preserves_source_and_switches_to_independent_copy(self):
        w = self.w
        source, before = w.path, w.path.read_bytes()
        destination = self.directory / 'copy.json'
        w.armed = True
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(destination), '')):
            self.assertTrue(w.duplicate_profile())
        self.assertEqual(w.path, destination)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(read_json(source), read_json(destination))
        self.assertFalse(w.armed)
        w.set_value(w.profile.rows[0].id, 'memo', 'copy only')
        w.flush_pending_saves()
        self.assertEqual(source.read_bytes(), before)

    def test_duplicate_rejects_source_path_and_failed_write_keeps_current(self):
        w = self.w
        source, before = w.path, w.path.read_bytes()
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(source), '')):
            self.assertFalse(w.duplicate_profile())
        failed = Future()
        failed.set_result(SaveResult(False, error='copy failed'))
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(self.directory/'copy.json'), '')), patch.object(w.store, 'save', return_value=failed):
            self.assertFalse(w.duplicate_profile())
        self.assertEqual(w.path, source)
        self.assertEqual(source.read_bytes(), before)

    def test_removed_history_is_discarded_without_resetting_settings(self):
        for history in (['old.json'], 'invalid old history'):
            saved = Settings.read({'recent_profiles': history, 'theme': 'Light', 'click_hold_ms': 7})
            self.assertEqual(saved.theme, 'Light')
            self.assertEqual(saved.click_hold_ms, 7)
            self.assertNotIn('recent_profiles', asdict(saved))
        self.assertTrue(self.w.switch_profile(self.other()))
        self.assertNotIn('recent_profiles', read_json(self.w.settings_path))

    def test_menu_only_lists_selected_folder_after_switch(self):
        w = self.w
        with tempfile.TemporaryDirectory() as other_folder:
            directory = Path(other_folder)
            other = directory / 'external.json'
            atomic_json(other, Profile.default().export())
            self.assertTrue(w.switch_profile(other))
            w.refresh_profiles()
            actions = w.profile_menu.actions()
            self.assertEqual(actions[0].text(), '이 폴더')
            self.assertFalse(actions[0].isSeparator())
            self.assertFalse(actions[0].isEnabled())
            self.assertGreaterEqual(w.profile_menu.actionGeometry(actions[0]).height(),
                                    w.profile_menu.fontMetrics().height())
            self.assertEqual([a.data() for a in actions if a.data()], [str(other)])
            self.assertTrue(actions[1].isChecked())

    def test_empty_folder_still_has_folder_heading(self):
        with tempfile.TemporaryDirectory() as empty:
            self.w.path = Path(empty) / 'unsaved.json'
            self.w.refresh_profiles()
            actions = self.w.profile_menu.actions()
            self.assertEqual([a.text() for a in actions], ['이 폴더', '프로필 없음'])
            self.assertFalse(actions[0].isSeparator())
            self.assertFalse(actions[0].isEnabled())
            self.assertFalse(actions[1].isEnabled())

    def test_long_names_keep_width_and_editor_covers_body(self):
        w = self.w
        self.assertTrue(w.switch_profile(self.other('short.json')))
        self.qt.processEvents()
        width = w.width()
        other = self.other('긴 프로필 이름' * 10 + '.json')
        self.assertTrue(w.switch_profile(other))
        w.profile.target_program = {'exe':'Game.exe', 'class':'Unity', 'title':'아주 긴 게임 이름'*20}
        w.profile.resolution = (1920,1080)
        w.update_labels()
        editor = self.draft()
        self.qt.processEvents()
        self.assertEqual(w.width(), width)
        self.assertEqual(w.profile_name.text(), other.stem)
        self.assertEqual(w.profile_resolution.text(), '기준 1920×1080')
        self.assertEqual(editor.geometry(), editor.parent_area())
        w.resize(w.width()+50,w.height()+50)
        self.qt.processEvents()
        self.assertEqual(editor.geometry(), editor.parent_area())
        self.assertIs(w.caption_menu.menu, w.main_menu)

    def test_title_contains_only_coordinates_and_avoids_redundant_updates(self):
        w = self.w
        w.profile.resolution = (1920,1080)
        w.update_labels()
        self.assertEqual(w.profile_name.text(),'macro')
        self.assertEqual(w.profile_resolution.text(),'기준 1920×1080')
        changes = []
        w.windowTitleChanged.connect(changes.append)
        w.set_cursor_position((-123,456))
        w.set_cursor_position((-123,456))
        self.assertEqual(changes,['-123, 456'])
        w.profile.resolution = None
        w.dirty = True
        w.update_labels()
        self.assertEqual(w.windowTitle(),'-123, 456')
        self.assertEqual(w.profile_name.text(),'macro')
        self.assertEqual(w.profile_resolution.text(),'기준 알 수 없음')
        w.set_cursor_position()
        self.assertEqual(w.windowTitle(),'—, —')

    def test_profile_summary_stays_inside_fixed_target_box(self):
        w = self.w
        w.resize(520,480)
        self.qt.processEvents()
        name, basis = w.profile_name, w.profile_resolution
        before = basis.mapTo(w,QPoint())
        width = w.width()
        w.set_cursor_position((-99999,-99999))
        self.qt.processEvents()
        self.assertEqual(w.width(),width)
        self.assertEqual(basis.mapTo(w,QPoint()),before)
        w.resize(850,480)
        self.qt.processEvents()
        self.assertEqual(basis.mapTo(w,QPoint()),before)
        self.assertLess(name.geometry().right(),basis.geometry().left())
        self.assertGreaterEqual(basis.width(),basis.fontMetrics().horizontalAdvance(basis.text()))
        self.assertFalse(hasattr(w,'cursor_label'))

    def test_editor_covers_body_and_coordinates_continue_in_title(self):
        w = self.w
        w.target = 123
        editor = self.draft()
        self.qt.processEvents()
        self.assertEqual(editor.geometry(),w.rect())
        with patch.object(w.native,'exists',return_value=True), \
             patch.object(w.native,'size',return_value=(1280,720)), \
             patch.object(w.native,'cursor',return_value=(30,40)):
            w.tick()
        self.assertEqual(w.windowTitle(),'30, 40')
        self.assertFalse(hasattr(editor,'cursor_label'))
        w.resize(w.width()+100,w.height()+50)
        self.qt.processEvents()
        self.assertEqual(editor.geometry(),w.rect())
        self.assertIs(w.caption_menu.menu,w.main_menu)
        editor.reject()
        with patch.object(w.native,'exists',return_value=True), \
             patch.object(w.native,'size',return_value=(1280,720)), \
             patch.object(w.native,'cursor',return_value=(123,456)):
            w.tick()
        self.assertEqual(w.windowTitle(),'123, 456')

    def test_menu_switch_failure_keeps_current_check(self):
        w = self.w
        old, other = w.path, self.other()
        w.refresh_profiles()
        destination = next(a for a in w.profile_menu.actions() if a.data() == str(other))
        with patch.object(w, 'prepare_profile_change', return_value=False):
            destination.trigger()
        self.assertEqual(w.path,old)
        self.assertEqual([a.data() for a in w.profile_menu.actions() if a.isChecked()], [str(old)])

    def test_folder_action_uses_current_profile_directory(self):
        with patch('app.QDesktopServices.openUrl', return_value=True) as open_url:
            self.w.open_profile_folder()
        self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), self.directory)

    def test_profile_checks_resolve_current_path_once_and_restore_selection(self):
        w = self.w
        other = self.other()
        w.refresh_profiles()
        destination = next(a for a in w.profile_menu.actions() if a.data() == str(other))
        destination.setChecked(True)
        original = Path.resolve
        with patch.object(Path, 'resolve', autospec=True, side_effect=original) as resolve:
            w.update_profile_checks()
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual([a.data() for a in w.profile_menu.actions() if a.isChecked()], [str(w.path)])
        w.path = None
        w.update_profile_checks()
        self.assertFalse(any(a.isChecked() for a in w.profile_menu.actions()))

    def test_menu_reuses_catalog_but_refreshes_after_external_change(self):
        import profile_catalog
        other = self.other()
        with patch('profile_catalog.read_json', wraps=profile_catalog.read_json) as reads:
            self.w.refresh_profiles()
            count = reads.call_count
            self.w.refresh_profiles()
            self.assertEqual(reads.call_count, count)
            other.write_text('{"other": true}', encoding='utf-8')
            self.w.refresh_profiles()
            self.assertEqual(reads.call_count, count + 1)
        self.assertFalse(any(a.data() == str(other) for a in self.w.profile_menu.actions()))
