import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import asdict
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QMimeData, QPointF
from PySide6.QtGui import QInputMethodEvent, QDragEnterEvent, QDropEvent
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

    def save_mouse_only_profile(self):
        self.w.profile.resolution = None
        editor = self.editor('click mouse // 100,200 is only a comment')
        editor.save()
        self.w.flush_pending_saves()
        self.assertIsNone(Profile.read(read_json(self.w.path)).resolution)

    def test_table_capture_after_mouse_only_script_establishes_first_basis(self):
        self.save_mouse_only_profile()
        w = self.w
        with patch.object(w.native, 'cursor', return_value=(960,540)), \
             patch.object(w.native, 'bounds', return_value=(0,0,1920,1080)), \
             patch.object(w, 'convert_dialog') as convert:
            w.capture(w.profile.rows[1].id)
            convert.assert_not_called()
        w.flush_pending_saves()
        saved = Profile.read(read_json(w.path))
        self.assertEqual((saved.resolution, saved.rows[1].x, saved.rows[1].y),
                         ((1920,1080), 960, 540))
        self.assertEqual(saved.rows[0].script, w.profile.rows[0].script)

    def test_manual_row_entry_after_mouse_only_script_establishes_first_basis(self):
        self.save_mouse_only_profile()
        w = self.w
        with patch('app.ManualCoordinateDialog') as prompt:
            w.table.cellWidget(1,1).setValue(960)
            prompt.assert_not_called()
        w.flush_pending_saves()
        saved = Profile.read(read_json(w.path))
        self.assertEqual((saved.resolution, saved.rows[1].x), ((1920,1080), 960))

    def test_script_capture_after_mouse_only_script_establishes_first_basis(self):
        self.save_mouse_only_profile()
        editor = self.editor('click mouse\nclick ')
        with patch.object(self.w.native, 'bounds', return_value=(0,0,1920,1080)):
            editor.pick_coordinate((960,540))
        self.assertEqual(editor.editor.toPlainText(), 'click mouse\nclick 960,540')
        self.assertEqual(editor.capture_resolution, (1920,1080))
        editor.save()
        self.assertIsNone(self.w.script_editor)
        self.w.flush_pending_saves()
        saved = Profile.read(read_json(self.w.path))
        self.assertEqual((saved.resolution, saved.rows[0].script),
                         ((1920,1080), 'click mouse\nclick 960,540'))

    def test_manual_script_entry_after_mouse_only_script_establishes_first_basis(self):
        self.save_mouse_only_profile()
        editor = self.editor('click mouse\nclick ')
        with patch('app.ManualCoordinateDialog') as prompt:
            QTest.keyClicks(editor.editor, '960,540')
            prompt.assert_not_called()
        editor.save()
        self.assertIsNone(self.w.script_editor)
        self.w.flush_pending_saves()
        saved = Profile.read(read_json(self.w.path))
        self.assertEqual((saved.resolution, saved.rows[0].script),
                         ((1920,1080), 'click mouse\nclick 960,540'))

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
        self.assertIsNone(editor.profile_resolution)
        self.assertIsNone(w.profile.resolution)
        self.assertEqual(w.profile.target_program,{})
        self.assertEqual(editor.capture_resolution,(1920,1080))
        self.assertEqual(editor.capture_program,GAME)
        editor.save()
        self.assertIsNone(w.script_editor)
        w.flush_pending_saves()
        saved=Profile.read(read_json(w.path))
        self.assertEqual((saved.resolution,saved.rows[0].script),((1920,1080),'click 960,540'))

    def prepare_empty_script_profile(self):
        w = self.w
        w.profile.resolution = None
        w.profile.target_program = {}
        w.profile.rows[0].action_mode = 'script'
        w.save()
        return w.profile.export(), w.path.read_bytes()

    def test_cancel_initial_manual_script_keeps_profile_and_file_unchanged(self):
        before = self.prepare_empty_script_profile()
        editor = self.editor('click ')
        QTest.keyClicks(editor.editor, '960,540')
        self.w.flush_pending_saves()
        self.assertEqual((self.w.profile.export(), self.w.path.read_bytes()), before)
        editor.reject()
        self.w.flush_pending_saves()
        self.assertEqual((self.w.profile.export(), self.w.path.read_bytes()), before)
        self.size.return_value = (1280,720)
        editor = self.editor('click ')
        with patch('app.ManualCoordinateDialog') as prompt:
            QTest.keyClicks(editor.editor, '640,360')
            prompt.assert_not_called()
        editor.save()
        self.w.flush_pending_saves()
        saved = Profile.read(read_json(self.w.path))
        self.assertEqual((saved.resolution, saved.target_program, saved.rows[0].script),
                         ((1280,720), GAME, 'click 640,360'))

    def test_invalid_initial_manual_script_keeps_metadata_pending_until_saved(self):
        before = self.prepare_empty_script_profile()
        editor = self.editor('click ')
        QTest.keyClicks(editor.editor, '960,')
        editor.save()
        self.assertIs(self.w.script_editor, editor)
        self.assertTrue(editor.validation.property('error'))
        self.w.flush_pending_saves()
        self.assertEqual((self.w.profile.export(), self.w.path.read_bytes()), before)
        cursor = editor.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        editor.editor.setTextCursor(cursor)
        QTest.keyClicks(editor.editor, '540')
        editor.save()
        self.assertIsNone(self.w.script_editor)
        self.w.flush_pending_saves()
        saved = Profile.read(read_json(self.w.path))
        self.assertEqual((saved.resolution, saved.target_program, saved.rows[0].script),
                         ((1920,1080), GAME, 'click 960,540'))

    def test_initial_manual_script_rejects_app_change_before_save(self):
        before = self.prepare_empty_script_profile()
        editor = self.editor('click ')
        QTest.keyClicks(editor.editor, '960,540')
        self.identity.return_value = {'exe':'Other.exe', 'class':'Other', 'title':'Other'}
        editor.save()
        self.assertIs(self.w.script_editor, editor)
        self.assertTrue(editor.validation.property('error'))
        self.w.flush_pending_saves()
        self.assertEqual((self.w.profile.export(), self.w.path.read_bytes()), before)
        editor.reject()

    def test_initial_manual_script_confirms_app_only_on_save_and_preserves_declined_draft(self):
        self.prepare_empty_script_profile()
        w = self.w
        w.profile.target_program = {'exe':'Other.exe', 'class':'Other', 'title':'Other'}
        w.save()
        before = w.profile.export(), w.path.read_bytes()
        editor = self.editor('click ')
        with patch('app.CoordinateAppDialog') as prompt:
            QTest.keyClicks(editor.editor, '960,540')
            prompt.assert_not_called()
            prompt.return_value.confirmed.return_value = False
            editor.save()
            prompt.assert_called_once()
            self.assertIs(w.script_editor, editor)
            self.assertEqual((w.profile.export(), w.path.read_bytes()), before)
            prompt.return_value.confirmed.return_value = True
            editor.save()
            self.assertEqual(prompt.call_count, 2)
        self.assertIsNone(w.script_editor)
        w.flush_pending_saves()
        saved = Profile.read(read_json(w.path))
        self.assertEqual((saved.resolution, saved.target_program, saved.rows[0].script),
                         ((1920,1080), GAME, 'click 960,540'))

    def test_manual_draft_basis_survives_resize_and_reverse_capture(self):
        before = self.prepare_empty_script_profile()
        editor = self.editor('click ')
        QTest.keyClicks(editor.editor, '960,540')
        self.size.return_value = (1280,720)
        with patch('app.ManualCoordinateDialog') as prompt:
            prompt.return_value.choice.return_value = 'cancel'
            QTest.keyClick(editor.editor, Qt.Key.Key_9)
            prompt.assert_called_once()
        self.assertEqual(editor.editor.toPlainText(), 'click 960,540')
        self.assertEqual((self.w.profile.export(), self.w.path.read_bytes()), before)
        QTest.keyClick(editor.editor, Qt.Key.Key_Return)
        QTest.keyClicks(editor.editor, 'click ')
        with patch.object(self.w.native, 'bounds', return_value=(0,0,1280,720)):
            editor.pick_coordinate((640,360))
        editor.save()
        self.assertIsNone(self.w.script_editor)
        self.w.flush_pending_saves()
        saved = Profile.read(read_json(self.w.path))
        self.assertEqual((saved.resolution, saved.rows[0].script),
                         ((1920,1080), 'click 960,540\nclick 960,540'))

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

    def test_context_cut_and_delete_cannot_change_mismatched_coordinates(self):
        clipboard=QApplication.clipboard()
        old_clipboard=clipboard.text()
        try:
            for action_name in ('edit-cut','edit-delete'):
                with self.subTest(action=action_name):
                    editor=self.editor('click 640,360')
                    cursor=editor.editor.textCursor()
                    cursor.setPosition(6)
                    cursor.setPosition(7,cursor.MoveMode.KeepAnchor)
                    editor.editor.setTextCursor(cursor)
                    menu=editor.editor.createStandardContextMenu()
                    with patch('app.ManualCoordinateDialog') as prompt:
                        prompt.return_value.choice.return_value='cancel'
                        next(action for action in menu.actions() if action.objectName()==action_name).trigger()
                        prompt.assert_called_once()
                        self.assertEqual(editor.editor.toPlainText(),'click 640,360')
                        QTest.keyClick(editor.editor,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
                        self.assertEqual(editor.editor.toPlainText(),'click 640,360')
                    editor.save()
                    self.w.flush_pending_saves()
                    self.assertEqual(Profile.read(read_json(self.w.path)).rows[0].script,'click 640,360')
                    menu.deleteLater()
        finally:
            clipboard.setText(old_clipboard)

    def test_word_delete_cannot_uncomment_current_size_coordinates(self):
        editor=self.editor('//click 960,540')
        for key,position in ((Qt.Key.Key_Backspace,2),(Qt.Key.Key_Delete,0)):
            with self.subTest(key=key),patch('app.ManualCoordinateDialog') as prompt:
                cursor=editor.editor.textCursor();cursor.setPosition(position)
                editor.editor.setTextCursor(cursor)
                prompt.return_value.choice.return_value='cancel'
                QTest.keyClick(editor.editor,key,Qt.KeyboardModifier.ControlModifier)
                prompt.assert_called_once()
                self.assertEqual(editor.editor.toPlainText(),'//click 960,540')

    def test_deletion_only_input_method_checks_actual_edit(self):
        editor=self.editor('//click 960,540')
        cursor=editor.editor.textCursor();cursor.setPosition(2)
        editor.editor.setTextCursor(cursor)
        event=QInputMethodEvent();event.setCommitString('',-2,2)
        with patch('app.ManualCoordinateDialog') as prompt:
            prompt.return_value.choice.return_value='cancel'
            QApplication.sendEvent(editor.editor,event)
            prompt.assert_called_once()
        self.assertEqual(editor.editor.toPlainText(),'//click 960,540')

    def test_rejected_edit_preserves_previous_approved_undo_history(self):
        self.size.return_value=(1280,720)
        editor=self.editor('click ')
        QTest.keyClicks(editor.editor,'640,360')
        self.size.return_value=(1920,1080)
        with patch('app.ManualCoordinateDialog') as prompt:
            prompt.return_value.choice.return_value='cancel'
            QTest.keyClick(editor.editor,Qt.Key.Key_9)
            self.assertEqual(editor.editor.toPlainText(),'click 640,360')
            QTest.keyClick(editor.editor,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(editor.editor.toPlainText(),'click 640,360')
            QTest.keyClick(editor.editor,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(editor.editor.toPlainText(),'click 640,36')
            QTest.keyClick(editor.editor,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(editor.editor.toPlainText(),'click 640,360')
            prompt.assert_called_once()

    def test_context_paste_and_non_bmp_text_preserve_original_on_cancel(self):
        clipboard=QApplication.clipboard();old=clipboard.text()
        try:
            editor=self.editor('// 🐈 메모\nclick ')
            clipboard.setText('960,540')
            menu=editor.editor.createStandardContextMenu()
            with patch('app.ManualCoordinateDialog') as prompt:
                prompt.return_value.choice.return_value='cancel'
                next(action for action in menu.actions() if action.objectName()=='edit-paste').trigger()
                prompt.assert_called_once()
            self.assertEqual(editor.editor.toPlainText(),'// 🐈 메모\nclick ')
            menu.deleteLater()
        finally:
            clipboard.setText(old)


    def drop_text(self, editor, text, action):
        field=editor.editor
        self.qt.processEvents()
        point=field.cursorRect().center()
        mime=QMimeData();mime.setText(text)
        modifiers=Qt.KeyboardModifier.ShiftModifier if action==Qt.DropAction.MoveAction else Qt.KeyboardModifier.ControlModifier
        enter=QDragEnterEvent(point,action,mime,Qt.MouseButton.LeftButton,modifiers)
        QApplication.sendEvent(field.viewport(),enter)
        self.assertTrue(enter.isAccepted())
        drop=QDropEvent(QPointF(point),action,mime,Qt.MouseButton.LeftButton,modifiers)
        QApplication.sendEvent(field.viewport(),drop)
        return drop.isAccepted()

    def test_rejected_move_and_copy_drop_report_failure_to_source(self):
        for action in (Qt.DropAction.MoveAction,Qt.DropAction.CopyAction):
            with self.subTest(action=action):
                editor=self.editor('click ')
                with patch('app.ManualCoordinateDialog') as prompt:
                    prompt.return_value.choice.return_value='cancel'
                    accepted=self.drop_text(editor,'960,540',action)
                    prompt.assert_called_once()
                self.assertFalse(accepted)
                self.assertEqual(editor.editor.toPlainText(),'click ')
                editor.editor.redo()
                self.assertEqual(editor.editor.toPlainText(),'click ')

    def test_aligned_move_and_copy_drop_are_accepted_and_can_be_saved(self):
        self.size.return_value=(1280,720)
        for action in (Qt.DropAction.MoveAction,Qt.DropAction.CopyAction):
            with self.subTest(action=action):
                editor=self.editor('click ')
                with patch('app.ManualCoordinateDialog') as prompt:
                    self.assertTrue(self.drop_text(editor,'640,360',action))
                    prompt.assert_not_called()
                self.assertEqual(editor.editor.toPlainText(),'click 640,360')
                editor.save()
                self.w.flush_pending_saves()
                self.assertEqual(Profile.read(read_json(self.w.path)).rows[0].script,'click 640,360')

    def test_noncoordinate_drop_still_allowed_when_app_size_differs(self):
        editor=self.editor('sleep ')
        with patch('app.ManualCoordinateDialog') as prompt:
            self.assertTrue(self.drop_text(editor,'5',Qt.DropAction.MoveAction))
            prompt.assert_not_called()
        self.assertEqual(editor.editor.toPlainText(),'sleep 5')

    def test_failed_drop_guard_restores_draft_and_cannot_be_redone(self):
        editor=self.editor('click ')
        field=editor.editor
        self.qt.processEvents()
        mime=QMimeData();mime.setText('960,540')
        point=field.cursorRect().center()
        enter=QDragEnterEvent(point,Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ShiftModifier)
        QApplication.sendEvent(field.viewport(),enter)
        drop=QDropEvent(QPointF(point),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ShiftModifier)
        with patch.object(field,'can_edit_coordinates',side_effect=RuntimeError('guard failed')):
            with self.assertRaisesRegex(RuntimeError,'guard failed'):
                field.dropEvent(drop)
        self.assertFalse(drop.isAccepted())
        field.redo()
        self.assertEqual(field.toPlainText(),'click ')
        self.assertFalse(field.editing)

    def test_long_numbers_show_save_error_and_preserve_profile_until_corrected(self):
        row=self.w.profile.rows[0]
        row.action_mode='script';row.script='sleep 1'
        self.w.save()
        before=self.w.path.read_bytes()
        for command in ('sleep '+('9'*5000),'click '+('9'*5000)+',1'):
            with self.subTest(command=command.split()[0]):
                editor=self.editor('sleep 1\n'+command)
                editor.save()
                self.assertIs(self.w.script_editor,editor)
                self.assertTrue(editor.validation.property('error'))
                self.assertTrue(editor.validation.text().startswith('오류 · 2행:'))
                self.assertEqual(editor.editor.textCursor().blockNumber(),1)
                self.assertEqual(row.script,'sleep 1')
                self.assertEqual(self.w.path.read_bytes(),before)
        editor.editor.setPlainText('sleep 5')
        editor.save()
        self.assertIsNone(self.w.script_editor)
        self.w.flush_pending_saves()
        self.assertEqual(Profile.read(read_json(self.w.path)).rows[0].script,'sleep 5')
