"""Ownership checks for direct coordinates and priority of save notifications."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from concurrent.futures import Future
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication
from app import MainWindow
from domain import Profile, atomic_json, read_json
from persistence import SaveResult

GAME = {'exe': 'Game.exe', 'class': 'Game', 'title': 'Game'}
OTHER = {'exe': 'Other.exe', 'class': 'Other', 'title': 'Other'}
THIRD = {'exe': 'Third.exe', 'class': 'Third', 'title': 'Third'}


class CoordinateOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setStyle('Fusion')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        profile = Profile.default()
        profile.target_program = dict(GAME)
        profile.resolution = (1280, 720)
        profile.rows[0].x, profile.rows[0].y = 640, 360
        atomic_json(Path(self.directory.name) / 'macro.json', profile.export())
        self.w = MainWindow(data_dir=self.directory.name, hook_enabled=False)
        self.w.timer.stop()
        self.w.target = 123
        self.patches = ExitStack()
        native = self.w.native
        self.patches.enter_context(patch.object(native, 'exists', return_value=True))
        self.size = self.patches.enter_context(patch.object(native, 'size', return_value=(1280, 720)))
        self.identity = self.patches.enter_context(patch.object(native, 'window_identity', return_value=dict(OTHER)))
        self.patches.enter_context(patch.object(self.w.engine, 'submit'))
        self.w.show()
        self.qt.processEvents()

    def tearDown(self):
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.patches.close()
        self.directory.cleanup()

    def original(self):
        return self.w.profile.export(), self.w.path.read_bytes()

    def editor(self, source='click '):
        row = self.w.profile.rows[0]
        row.action_mode = 'script'
        row.script = source
        self.w.save()
        self.w.edit_script(row.id)
        editor = self.w.script_editor
        cursor = editor.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        editor.editor.setTextCursor(cursor)
        return editor

    def test_same_size_table_cancel_keeps_app_coordinates_and_file(self):
        before = self.original()
        for column, value in ((1, 321), (2, 123)):
            with self.subTest(column=column), patch('app.CoordinateAppDialog') as prompt:
                prompt.return_value.confirmed.return_value = False
                self.w.table.cellWidget(0, column).setValue(value)
                prompt.assert_called_once()
                self.w.flush_pending_saves()
                self.assertEqual(self.original(), before)
                self.assertEqual(self.w.table.cellWidget(0, column).value(), (640, 360)[column - 1])

    def test_same_size_table_accept_changes_app_once_and_preserves_other_rows(self):
        before = self.w.profile.export()['macros'][1:]
        with patch('app.CoordinateAppDialog') as prompt:
            prompt.return_value.confirmed.return_value = True
            self.w.table.cellWidget(0, 1).setValue(321)
            self.w.table.cellWidget(0, 2).setValue(123)
            prompt.assert_called_once()
        self.w.flush_pending_saves()
        saved = Profile.read(read_json(self.w.path))
        self.assertEqual((saved.target_program, saved.resolution, saved.rows[0].x, saved.rows[0].y),
                         (OTHER, (1280, 720), 321, 123))
        self.assertEqual(saved.export()['macros'][1:], before)

    def test_same_program_title_change_needs_no_confirmation(self):
        current = {**GAME, 'title': 'Game - 60 FPS'}
        self.identity.return_value = current
        with patch('app.CoordinateAppDialog') as prompt:
            self.w.table.cellWidget(0, 1).setValue(321)
        prompt.assert_not_called()
        self.w.flush_pending_saves()
        self.assertEqual(Profile.read(read_json(self.w.path)).target_program, current)

    def test_same_size_table_rechecks_context_after_confirmation(self):
        w = self.w
        original_path = w.path
        old_size = w.profile.resolution
        before_file = original_path.read_bytes()
        cases = ('size', 'target', 'basis', 'identity')
        for case in cases:
            with self.subTest(case=case):
                w.target = 123
                w.profile.resolution = old_size
                self.size.return_value = old_size
                self.identity.return_value = dict(OTHER)
                def change():
                    if case == 'size':
                        self.size.return_value = (1920, 1080)
                    elif case == 'target':
                        w.target = 456
                    elif case == 'basis':
                        w.profile.resolution = (800, 600)
                    else:
                        self.identity.return_value = dict(THIRD)
                with patch('app.CoordinateAppDialog') as prompt:
                    prompt.return_value.exec.side_effect = change
                    prompt.return_value.confirmed.return_value = True
                    w.table.cellWidget(0, 1).setValue(321)
                w.flush_pending_saves()
                self.assertEqual(w.profile.rows[0].x, 640)
                self.assertEqual(w.profile.target_program, GAME)
                self.assertEqual(original_path.read_bytes(), before_file)
                self.assertIn('변경', w.statusBar().currentMessage())

    def test_same_size_script_confirms_on_save_and_keeps_declined_draft(self):
        editor = self.editor()
        before = self.original()
        with patch('app.CoordinateAppDialog') as prompt:
            QTest.keyClicks(editor.editor, '100,200')
            prompt.assert_not_called()
            self.assertEqual(self.original(), before)
            prompt.return_value.confirmed.return_value = False
            editor.save()
            prompt.assert_called_once()
            self.assertIs(self.w.script_editor, editor)
            self.assertEqual(editor.editor.toPlainText(), 'click 100,200')
            self.assertEqual(self.original(), before)
            prompt.return_value.confirmed.return_value = True
            editor.save()
            self.assertEqual(prompt.call_count, 2)
        self.w.flush_pending_saves()
        saved = Profile.read(read_json(self.w.path))
        self.assertEqual((saved.target_program, saved.resolution, saved.rows[0].script),
                         (OTHER, (1280, 720), 'click 100,200'))

    def test_invalid_script_and_cancel_never_commit_draft_app(self):
        editor = self.editor()
        before = self.original()
        with patch('app.CoordinateAppDialog') as prompt:
            QTest.keyClicks(editor.editor, '100,')
            editor.save()
            prompt.assert_not_called()
            self.assertIs(self.w.script_editor, editor)
            self.assertTrue(editor.validation.property('error'))
            editor.reject()
        self.w.flush_pending_saves()
        self.assertEqual(self.original(), before)

    def test_same_size_script_rejects_app_replacement_during_edit_and_save(self):
        editor = self.editor()
        before = self.original()
        QTest.keyClicks(editor.editor, '100,200')
        self.identity.return_value = dict(THIRD)
        QTest.keyClick(editor.editor, Qt.Key.Key_9)
        self.assertEqual(editor.editor.toPlainText(), 'click 100,200')
        self.assertEqual(editor.capture_program, OTHER)
        self.assertIn('앱이 변경', editor.validation.text())
        with patch('app.CoordinateAppDialog') as prompt:
            editor.save()
        prompt.assert_not_called()
        self.assertIs(self.w.script_editor, editor)
        self.assertEqual(self.original(), before)

    def test_direct_edit_and_capture_cannot_mix_apps_in_one_draft(self):
        editor = self.editor()
        QTest.keyClicks(editor.editor, '100,200')
        self.identity.return_value = dict(THIRD)
        with patch.object(self.w.native, 'bounds', return_value=(0, 0, 1280, 720)):
            editor.pick_coordinate((300, 400))
        self.assertEqual(editor.editor.toPlainText(), 'click 100,200')
        self.assertEqual(editor.capture_program, OTHER)
        self.assertIn('앱이 변경', editor.validation.text())

    def test_aligned_manual_script_keeps_basis_after_later_resize(self):
        editor = self.editor()
        QTest.keyClicks(editor.editor, '100,200')
        self.size.return_value = (1920, 1080)
        with patch('app.CoordinateAppDialog') as prompt:
            prompt.return_value.confirmed.return_value = True
            editor.save()
        self.w.flush_pending_saves()
        saved = Profile.read(read_json(self.w.path))
        self.assertEqual((saved.target_program, saved.resolution, saved.rows[0].script),
                         (OTHER, (1280, 720), 'click 100,200'))

    def test_time_and_comment_edits_do_not_reassign_profile_app(self):
        editor = self.editor('sleep 5\n// ')
        with patch('app.CoordinateAppDialog') as prompt:
            QTest.keyClicks(editor.editor, '100,200')
            editor.save()
        prompt.assert_not_called()
        self.w.flush_pending_saves()
        self.assertEqual(Profile.read(read_json(self.w.path)).target_program, GAME)

    def test_reverse_capture_warning_survives_save_completion(self):
        self.identity.return_value = dict(GAME)
        self.size.return_value = (1024, 768)
        with patch.object(self.w.native, 'cursor', return_value=(512, 384)), \
             patch.object(self.w.native, 'bounds', return_value=(0, 0, 1024, 768)):
            self.w.capture(self.w.profile.rows[0].id)
        warning = self.w.statusBar().currentMessage()
        self.assertIn('경고', warning)
        self.assertIn('역변환', warning)
        self.w.flush_pending_saves()
        self.qt.processEvents()
        self.assertEqual(self.w.statusBar().currentMessage(), warning)
        self.assertFalse(self.w.dirty)
        saved = Profile.read(read_json(self.w.path))
        self.assertEqual((saved.rows[0].x, saved.rows[0].y), (640, 360))

    def test_save_failure_replaces_notice_and_successful_retry_clears_failure(self):
        w = self.w
        before_file = w.path.read_bytes()
        w.profile.rows[0].memo = 'retry'
        w.statusBar().showMessage('좌표 경고')
        future = Future()
        future.set_result(SaveResult(False, error='disk full'))
        with patch.object(w.store, 'save', return_value=future):
            w.save(automatic=True)
            w.flush_pending_saves()
        self.assertIn('disk full', w.statusBar().currentMessage())
        self.assertTrue(w.dirty)
        self.assertEqual(w.path.read_bytes(), before_file)
        w.save(automatic=True)
        w.flush_pending_saves()
        self.qt.processEvents()
        self.assertIn('저장 완료', w.statusBar().currentMessage())
        self.assertNotIn('disk full', w.statusBar().currentMessage())
        self.assertFalse(w.dirty)
        self.assertEqual(Profile.read(read_json(w.path)).rows[0].memo, 'retry')

    def test_settings_save_failure_is_visible_until_successful_retry(self):
        w = self.w
        w.statusBar().showMessage('좌표 경고')
        with patch.object(w.store, '_settings', side_effect=OSError('settings full')):
            w.save(automatic=True)
            w.flush_pending_saves()
        self.assertIn('settings full', w.statusBar().currentMessage())
        self.assertFalse(w.dirty)
        self.assertEqual(w.settings_error, 'settings full')
        w.save(automatic=True)
        w.flush_pending_saves()
        self.assertIn('저장 완료', w.statusBar().currentMessage())
        self.assertEqual(w.settings_error, '')

    def test_save_submission_failure_is_visible_and_recoverable(self):
        w = self.w
        w.statusBar().showMessage('좌표 경고')
        with patch.object(w.store, 'save', side_effect=RuntimeError('closed writer')):
            self.assertFalse(w.save(automatic=True))
        self.assertIn('closed writer', w.statusBar().currentMessage())
        self.assertTrue(w.dirty)
        self.assertTrue(w.save())
        self.assertIn('저장 완료', w.statusBar().currentMessage())

    def test_success_does_not_restart_notice_timeout(self):
        bar = self.w.statusBar()
        bar.showMessage('좌표 경고', 100)
        changes = QSignalSpy(bar.messageChanged)
        bar.show_save_result('저장 완료')
        self.assertEqual(bar.currentMessage(), '좌표 경고')
        self.assertEqual(changes.count(), 0)
        self.assertTrue(changes.wait(1000))
        self.assertEqual(bar.currentMessage(), '')
        bar.show_save_result('새 저장 완료')
        self.assertEqual(bar.currentMessage(), '새 저장 완료')

    def test_new_action_notice_survives_success_after_previous_save_failure(self):
        bar = self.w.statusBar()
        bar.show_save_result('저장 실패', failed=True)
        bar.showMessage('새 좌표 안내')
        bar.show_save_result('재시도 성공')
        self.assertEqual(bar.currentMessage(), '새 좌표 안내')
