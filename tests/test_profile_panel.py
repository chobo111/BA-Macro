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
from profile_catalog import profile_entries, recent_profiles


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
        self.assertIs(self.w.cursor_label.parentWidget(),self.w.target_box)
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

    def test_catalog_filters_unrelated_json_and_deduplicates_recent_paths(self):
        other = self.other()
        (self.directory / 'broken.json').write_text('{broken', encoding='utf-8')
        atomic_json(self.directory / 'other-settings.json', {'theme': 'Dark'})
        entries = profile_entries(self.directory, self.w.path, [str(other), str(other)])
        self.assertEqual([p for p, _ in entries], [self.w.path, other])
        self.assertEqual(recent_profiles([str(other), str(other)], self.w.path), [str(self.w.path), str(other)])

    def test_title_keeps_saved_basis_separate_from_runtime_state(self):
        w = self.w
        w.profile.resolution = (1920,1080)
        w.profile.target_program = {'exe': 'Game.exe', 'class': 'Unity', 'title': 'Game'}
        w._selected_identity = dict(w.profile.target_program)
        w.target = 123
        w.settings.auto_convert_on_start = True
        w.update_labels()
        with patch.object(w.native, 'exists', return_value=True), patch.object(w.native, 'size', return_value=(1280,720)), patch.object(w.native, 'cursor', return_value=(4,5)):
            w.tick()
            self.assertIn('1920×1080', w.windowTitle())
            self.assertEqual(w.cursor_label.text(), '4, 5')
            self.assertEqual(w.target_info.text(), '현재 1280 × 720')
            w.session.start(ExecutionPlan(123, (1920,1080), (1280,720), automatic=True))
            w.tick()
            self.assertIn('자동 변환 사용 중', w.target_info.text())
            w.stop()
            w.tick()
            self.assertNotIn('자동 변환', w.target_info.text())
            with patch.object(w.native, 'size', side_effect=WindowSizeUnavailable()):
                w.tick()
                self.assertEqual(w.target_info.text(), '최소화')
        w._selected_identity = {'exe':'Other.exe', 'class':'Unity', 'title':'Other'}
        w.update_target_info((1280,720))
        self.assertIn('지정 앱과 다름', w.target_info.text())
        self.assertTrue(w.target_info.property('warning'))

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

    def test_history_is_persisted_and_limited(self):
        w = self.w
        other = self.other()
        self.assertTrue(w.switch_profile(other))
        saved = Settings.read(read_json(w.settings_path))
        self.assertEqual(saved.recent_profiles[:2], [str(other), str(self.directory/'macro.json')])
        paths = [str(self.directory/f'{n}.json') for n in range(15)]
        self.assertEqual(len(recent_profiles(paths, other)), 10)
        for value in ('wrong', ['relative.json'], [123], paths):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings.read({'recent_profiles': value})

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
        self.assertIn(other.stem + ' — 1920×1080', w.windowTitle())
        self.assertEqual(editor.geometry(), editor.parent_area())
        w.resize(w.width()+50,w.height()+50)
        self.qt.processEvents()
        self.assertEqual(editor.geometry(), editor.parent_area())
        self.assertIs(w.caption_menu.menu, w.main_menu)

    def test_coordinates_update_target_box_without_changing_title(self):
        w = self.w
        w.profile.resolution = (1920,1080)
        w.update_labels()
        self.assertEqual(w.windowTitle(), 'macro — 1920×1080')
        changes = []
        w.windowTitleChanged.connect(changes.append)
        w.set_cursor_position((-123,456))
        w.set_cursor_position((-123,456))
        self.assertEqual(changes, [])
        self.assertEqual(w.cursor_label.text(), '-123, 456')
        w.profile.resolution = None
        w.dirty = True
        w.update_labels()
        self.assertEqual(w.windowTitle(), '● macro — 알 수 없음')
        w.set_cursor_position()
        self.assertEqual(w.cursor_label.text(), '—, —')

    def test_coordinate_label_stays_at_target_box_bottom_right(self):
        w = self.w
        w.resize(520,480)
        self.qt.processEvents()
        label = w.cursor_label
        margins = w.target_box.layout().contentsMargins()
        self.assertEqual(label.geometry().right(), w.target_box.contentsRect().right()-margins.right())
        self.assertEqual(label.geometry().bottom(), w.target_box.contentsRect().bottom()-margins.bottom())
        before = label.mapTo(w,QPoint())
        width = w.width()
        w.set_cursor_position((-99999,-99999))
        self.qt.processEvents()
        self.assertEqual(w.width(),width)
        self.assertEqual(label.mapTo(w,QPoint()),before)
        w.resize(850,480)
        self.qt.processEvents()
        self.assertEqual(label.mapTo(w,QPoint()),before)
        self.assertGreaterEqual(label.width(),label.fontMetrics().horizontalAdvance(label.text()))

    def test_editor_keeps_coordinates_visible_when_target_status_wraps(self):
        w = self.w
        editor = self.draft()
        self.qt.processEvents()
        bottom = w.target_box.mapTo(w,QPoint(0,w.target_box.height())).y()
        self.assertGreaterEqual(editor.y(),bottom)
        w.target_info.setText('현재 1920 × 1080 · 자동 변환 사용 중')
        w.target_info.show()
        self.qt.processEvents()
        bottom = w.target_box.mapTo(w,QPoint(0,w.target_box.height())).y()
        self.assertGreaterEqual(editor.y(),bottom)
        self.assertEqual(editor.geometry(),editor.parent_area())

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
