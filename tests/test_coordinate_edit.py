import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import asdict
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QMimeData
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from app import MainWindow
from domain import Profile, Settings, read_json
from execution_plan import ExecutionPlan
from startup_dialogs import ManualCoordinateDialog

GAME={'exe':'Game.exe','class':'Game','title':'Game'}


class CoordinateEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])
        cls.qt.setStyle('Fusion')

    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.w=MainWindow(data_dir=self.folder.name,hook_enabled=False)
        self.w.timer.stop()
        self.w.target=123
        self.w.profile.resolution=(1280,720)
        self.w.profile.target_program=dict(GAME)
        self.w.show()
        self.qt.processEvents()
        self.exists=patch.object(self.w.native,'exists',return_value=True).start()
        self.size=patch.object(self.w.native,'size',return_value=(1920,1080)).start()
        self.identity=patch.object(self.w.native,'window_identity',return_value=dict(GAME)).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.w.shutdown()
        self.w.dirty=False
        self.w.close()
        self.w.deleteLater()
        self.qt.processEvents()
        self.folder.cleanup()

    def editor(self,source='click '):
        row=self.w.profile.rows[0]
        row.action_mode='script'
        self.w.edit_script(row.id)
        editor=self.w.script_editor
        editor.editor.setPlainText(source)
        cursor=editor.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        editor.editor.setTextCursor(cursor)
        return editor

    def test_cancel_mismatched_row_entry_preserves_point_baseline_and_file(self):
        w=self.w
        w.save()
        before=w.profile.export(),w.path.read_bytes()
        for field,column in (('x',1),('y',2)):
            with self.subTest(field=field),patch('app.ManualCoordinateDialog') as prompt:
                prompt.return_value.choice.return_value='cancel'
                w.table.cellWidget(0,column).setValue(900)
                prompt.assert_called_once()
                self.assertEqual(w.table.cellWidget(0,column).value(),0)
        w.flush_pending_saves()
        self.assertEqual((w.profile.export(),w.path.read_bytes()),before)

    def test_resize_choice_discards_pending_edit_and_allows_retry_after_match(self):
        w=self.w
        with patch('app.ManualCoordinateDialog') as prompt,patch.object(w,'resize_dialog') as resize:
            prompt.return_value.choice.return_value='resize'
            resize.side_effect=lambda **kwargs:setattr(self.size,'return_value',(1280,720))
            w.table.cellWidget(0,1).setValue(900)
            resize.assert_called_once_with(profile_size=(1280,720))
            self.assertEqual(w.profile.rows[0].x,0)
            w.table.cellWidget(0,1).setValue(900)
            self.assertEqual(w.profile.rows[0].x,900)
            self.assertEqual(prompt.call_count,1)
        w.flush_pending_saves()
        saved=Profile.read(read_json(w.path))
        self.assertEqual((saved.resolution,saved.rows[0].x),((1280,720),900))

    def test_conversion_choice_uses_original_points_then_allows_current_coordinates(self):
        from conversion import plan_conversion
        w=self.w
        w.profile.rows[0].x,w.profile.rows[0].y=100,200
        w.rebuild()
        def convert(**kwargs):
            plan=plan_conversion(w.profile,(1280,720),(1920,1080))
            self.assertEqual((plan.profile.rows[0].x,plan.profile.rows[0].y),(150,300))
            w.save_conversion(plan.profile,'apply',w)
        with patch('app.ManualCoordinateDialog') as prompt,patch.object(w,'convert_dialog',side_effect=convert):
            prompt.return_value.choice.return_value='convert'
            w.table.cellWidget(0,1).setValue(900)
            self.assertEqual((w.profile.rows[0].x,w.profile.rows[0].y),(150,300))
            w.table.cellWidget(0,1).setValue(900)
            self.assertEqual(w.profile.rows[0].x,900)
            self.assertEqual(prompt.call_count,1)
        self.assertEqual(w.profile.resolution,(1920,1080))

    def test_script_typing_paste_and_input_method_require_alignment(self):
        editor=self.editor()
        with patch('app.ManualCoordinateDialog') as prompt:
            prompt.return_value.choice.return_value='cancel'
            QTest.keyClick(editor.editor,Qt.Key.Key_9)
            self.assertEqual(editor.editor.toPlainText(),'click ')
            mime=QMimeData();mime.setText('960,540')
            editor.editor.insertFromMimeData(mime)
            self.assertEqual(editor.editor.toPlainText(),'click ')
            event=QInputMethodEvent();event.setCommitString('960,540')
            QApplication.sendEvent(editor.editor,event)
            self.assertEqual(editor.editor.toPlainText(),'click ')
            self.assertEqual(prompt.call_count,3)
        self.assertEqual(self.w.profile.resolution,(1280,720))

    def test_script_actual_clipboard_paste_is_guarded(self):
        editor=self.editor()
        clipboard=QApplication.clipboard()
        old=clipboard.text()
        try:
            clipboard.setText('960,540')
            with patch('app.ManualCoordinateDialog') as prompt:
                prompt.return_value.choice.return_value='cancel'
                QTest.keyClick(editor.editor,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier)
                prompt.assert_called_once()
            self.assertEqual(editor.editor.toPlainText(),'click ')
        finally:
            clipboard.setText(old)

    def test_sleep_and_comment_changes_need_no_size_alignment(self):
        editor=self.editor('sleep 5\n// 메모 ')
        with patch('app.ManualCoordinateDialog') as prompt:
            QTest.keyClicks(editor.editor,'123')
            prompt.assert_not_called()
        self.assertEqual(editor.editor.toPlainText(),'sleep 5\n// 메모 123')
        editor.save()
        self.assertEqual(self.w.profile.rows[0].script,'sleep 5\n// 메모 123')

    def test_manual_script_coordinate_allowed_when_sizes_match(self):
        self.size.return_value=(1280,720)
        editor=self.editor()
        with patch('app.ManualCoordinateDialog') as prompt:
            QTest.keyClicks(editor.editor,'640,360')
            prompt.assert_not_called()
        editor.save()
        self.assertIsNone(self.w.script_editor)
        self.w.flush_pending_saves()
        self.assertEqual(Profile.read(read_json(self.w.path)).rows[0].script,'click 640,360')

    def test_initial_manual_row_coordinate_records_app_and_current_basis(self):
        w=self.w;w.profile.resolution=None;w.profile.target_program={}
        w.table.cellWidget(0,1).setValue(960)
        w.flush_pending_saves()
        saved=Profile.read(read_json(w.path))
        self.assertEqual((saved.resolution,saved.target_program,saved.rows[0].x),((1920,1080),GAME,960))

    def test_initial_manual_script_coordinate_records_basis_without_stale_editor_error(self):
        w=self.w;w.profile.resolution=None;w.profile.target_program={}
        editor=self.editor()
        QTest.keyClicks(editor.editor,'960,540')
        self.assertEqual(editor.profile_resolution,(1920,1080))
        editor.save()
        self.assertIsNone(w.script_editor)
        w.flush_pending_saves()
        saved=Profile.read(read_json(w.path))
        self.assertEqual((saved.resolution,saved.rows[0].script),((1920,1080),'click 960,540'))

    def test_capture_only_script_with_unknown_basis_saves_after_resize(self):
        w=self.w;w.profile.resolution=None
        editor=self.editor()
        with patch.object(w.native,'bounds',return_value=(0,0,1920,1080)):
            editor.pick_coordinate((960,540))
        self.assertEqual(editor.capture_resolution,(1920,1080))
        self.size.return_value=(1280,720)
        with patch('app.ManualCoordinateDialog') as prompt:
            editor.save()
            prompt.assert_not_called()
        self.assertIsNone(w.script_editor)
        w.flush_pending_saves()
        saved=Profile.read(read_json(w.path))
        self.assertEqual((saved.resolution,saved.rows[0].script),((1920,1080),'click 960,540'))

    def test_reverse_capture_remains_available_when_manual_edit_is_blocked(self):
        editor=self.editor()
        with patch('app.ManualCoordinateDialog') as prompt,patch.object(self.w.native,'bounds',return_value=(0,0,1920,1080)):
            editor.pick_coordinate((960,540))
            prompt.assert_not_called()
        self.assertEqual(editor.editor.toPlainText(),'click 640,360')

    def test_unselected_row_and_script_input_cannot_establish_coordinates(self):
        w=self.w;w.target=None
        original=w.profile.export()
        w.table.cellWidget(0,1).setValue(900)
        self.assertEqual(w.profile.export(),original)
        editor=self.editor()
        QTest.keyClick(editor.editor,Qt.Key.Key_9)
        self.assertEqual(editor.editor.toPlainText(),'click ')
        self.assertEqual(w.profile.resolution,(1280,720))

    def test_zero_execution_target_is_rejected_and_fullscreen_is_absent(self):
        w=self.w
        with self.assertRaisesRegex(ValueError,'앱을 선택'):
            ExecutionPlan(0,(1280,720),(1920,1080))
        self.assertEqual(w.targets.findData(0),-1)
        self.assertNotIn('display_id',asdict(Settings()))
        from widgets import SettingsDialog
        dialog=SettingsDialog(w.settings,w)
        self.assertFalse(hasattr(dialog,'displays'))
        dialog.deleteLater()

    def test_direct_edit_dialog_has_no_automatic_conversion_option(self):
        for basis in ((1280,720),None):
            dialog=ManualCoordinateDialog(basis,(1920,1080),self.w)
            texts={button.text() for button in dialog.buttons()}
            self.assertEqual(texts,{'창 크기 변경','프로필 좌표 변환','취소'})
            self.assertEqual(dialog.resize_button.isEnabled(),bool(basis))
            self.assertIs(dialog.defaultButton(),dialog.cancel_button)
            dialog.deleteLater()


    def test_script_conversion_saves_captured_basis_before_converting_and_discards_pending_key(self):
        from conversion import plan_conversion
        w=self.w
        editor=self.editor()
        with patch.object(w.native,'bounds',return_value=(0,0,1920,1080)):
            editor.pick_coordinate((960,540))
        def convert(**kwargs):
            self.assertTrue(w.finish_script_editor(save=True))
            w.flush_pending_saves()
            plan=plan_conversion(w.profile,(1280,720),(1920,1080))
            w.save_conversion(plan.profile,'apply',w)
        with patch('app.ManualCoordinateDialog') as prompt,patch.object(w,'convert_dialog',side_effect=convert):
            prompt.return_value.choice.return_value='convert'
            QTest.keyClick(editor.editor,Qt.Key.Key_9)
        self.assertIsNone(w.script_editor)
        self.assertEqual((w.profile.resolution,w.profile.rows[0].script),((1920,1080),'click 960,540'))
        saved=Profile.read(read_json(w.path))
        self.assertEqual(saved.rows[0].script,'click 960,540')

    def test_basis_change_during_manual_prompt_cannot_resize_with_stale_basis(self):
        w=self.w
        with patch('app.ManualCoordinateDialog') as prompt,patch.object(w,'resize_dialog') as resize:
            prompt.return_value.choice.return_value='resize'
            prompt.return_value.exec.side_effect=lambda:setattr(w.profile,'resolution',(800,600))
            w.table.cellWidget(0,1).setValue(900)
            resize.assert_not_called()
        self.assertEqual((w.profile.resolution,w.profile.rows[0].x),((800,600),0))


    def test_spinbox_keyboard_edit_cannot_commit_mismatched_current_coordinates(self):
        w=self.w
        w.profile.rows[0].x=300
        w.rebuild()
        w.save()
        original=w.profile.export(),w.path.read_bytes()
        field=w.table.cellWidget(0,1).lineEdit()
        field.setFocus()
        field.selectAll()
        with patch('app.ManualCoordinateDialog') as prompt:
            prompt.return_value.choice.return_value='cancel'
            QTest.keyClick(field,Qt.Key.Key_9)
            prompt.assert_called_once()
        self.qt.processEvents()
        w.flush_pending_saves()
        self.assertEqual((w.profile.export(),w.path.read_bytes()),original)
        self.assertEqual(w.table.cellWidget(0,1).value(),300)
