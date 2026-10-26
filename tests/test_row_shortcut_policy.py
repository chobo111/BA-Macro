import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog
from app import MainWindow
from domain import Profile, Row, Settings, atomic_json, read_json
from execution_plan import ExecutionPlan
from hotkeys import Hotkeys
from persistence import ProfileStore
from widgets import KeyDialog


class RowShortcutPolicyTests(unittest.TestCase):
    def test_import_preserves_unsupported_keys_for_repair(self):
        for key, expected in (('ctrl+q', 'ctrl+q'), ('Control+Q', 'ctrl+q'),
                              ('ctrl+alt+q', 'ctrl+alt+q'), ('ctrl+', 'ctrl+'),
                              ('no_such_key', 'no_such_key'), ('win+q', 'win+q'),
                              ('_', '_'), ('!', '!'), ('?', '?')):
            with self.subTest(key=key):
                loaded = Profile.read(Profile([Row(move_hk=key)]).export())
                self.assertEqual(loaded.rows[0].move_hk, expected)
                prepared = Hotkeys.prepare_profile(('ctrl+s', 'ctrl+`'), (expected, 'q'))
                self.assertEqual(prepared.keys, ('', 'q'))
                self.assertEqual(set(prepared.issues), {0})

    def test_allowed_rows_and_global_ctrl_chords_remain_valid(self):
        keys = ('q', 'alt+w', 'shift+e', 'alt+shift+r')
        prepared = Hotkeys.prepare_profile(('ctrl+s', 'ctrl+`'), keys)
        self.assertEqual(prepared.keys, keys)
        self.assertFalse(prepared.issues)

    def test_standalone_ctrl_is_disabled_only_when_conflicting(self):
        prepared = Hotkeys.prepare_profile(('f8', 'f9'), ('ctrl',))
        self.assertEqual(prepared.keys, ('ctrl',))
        self.assertFalse(prepared.issues)
        prepared = Hotkeys.prepare_profile(('ctrl+s', 'ctrl+`'), ('ctrl',))
        self.assertEqual(prepared.keys, ('',))
        self.assertIn('단독', prepared.issues[0])

    def test_duplicate_rows_are_both_disabled(self):
        for keys in (('PgUp', 'Page Up'), ('shift+-', 'shift+_'), ('alt', 'alt+q')):
            with self.subTest(keys=keys):
                prepared = Hotkeys.prepare_profile(('ctrl+s', 'ctrl+`'), (*keys, 'e'))
                self.assertEqual(prepared.keys, ('', '', 'e'))
                self.assertEqual(set(prepared.issues), {0, 1})

    def test_invalid_global_keys_are_still_rejected(self):
        with self.assertRaises(ValueError):
            Hotkeys.prepare_profile(('f8', 'f8'), ('q',))

    def test_startup_loads_ctrl_profile_without_overwriting_personal_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(directory)
            try:
                path = Path(directory) / 'macro.json'
                atomic_json(path, Profile([Row(move_hk='ctrl+q')]).export())
                atomic_json(store.settings_path, asdict(Settings(last_profile='macro.json')))
                original, settings = path.read_bytes(), store.settings_path.read_bytes()
                loaded = store.load()
                self.assertEqual(loaded.path, path)
                self.assertEqual(loaded.profile.rows[0].move_hk, 'ctrl+q')
                self.assertFalse(loaded.warning)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(store.settings_path.read_bytes(), settings)
            finally:
                store.close()


class QuarantinedShortcutUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'macro.json'
        atomic_json(self.path, Profile([Row(move_hk=key) for key in ('ctrl', 'ctrl+', 'q')]).export())
        self.original = self.path.read_bytes()
        self.w = MainWindow(data_dir=self.folder.name, hook_enabled=False)
        self.w.timer.stop()

    def tearDown(self):
        self.w.shutdown()
        self.w.dirty = False
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()
        self.folder.cleanup()

    def test_load_preserves_keys_warns_and_registers_only_usable_rows(self):
        w = self.w
        self.assertEqual([r.move_hk for r in w.profile.rows], ['ctrl', 'ctrl+', 'q'])
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual({b.key for b in w.hooks.bindings}, {'ctrl+s', 'ctrl+`', 'q'})
        self.assertIn('사용하려면 단축키를 변경', w.statusBar().currentMessage())
        for index in (0, 1):
            button = w.table.cellWidget(index, 0)
            self.assertTrue(button.isEnabled())
            self.assertFalse(button.icon().isNull())
            self.assertIn('단축키 비활성', button.toolTip())
        self.assertTrue(w.table.cellWidget(2, 0).icon().isNull())

    def test_partial_repair_and_memo_autosave_keep_other_invalid_keys(self):
        w = self.w
        with patch('app.KeyDialog') as factory:
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.value.return_value = 'alt+q'
            w.edit_key(w.profile.rows[0].id)
        w.live_memo_changed(1, '보존')
        w.flush_pending_saves()
        saved = read_json(self.path)['macros']
        self.assertEqual([r['move_hk'] for r in saved], ['alt+q', 'ctrl+', 'q'])
        self.assertEqual(saved[1]['memo'], '보존')
        self.assertFalse(w.dirty)
        self.assertIn('alt+q', {b.key for b in w.hooks.bindings})
        self.assertEqual(set(w._shortcut_issues), {w.profile.rows[1].id})
        self.assertTrue(w.table.cellWidget(0, 0).icon().isNull())

    def test_malformed_key_can_open_repair_dialog(self):
        dialog = KeyDialog('ctrl+', self.w)
        dialog.reject()
        dialog.deleteLater()

    def test_dispatch_drops_disabled_rows_but_submits_valid_row(self):
        w = self.w
        w.session.plan = ExecutionPlan(1, None, None)
        with patch.object(w, 'eligible', return_value=True), \
                patch.object(w, 'check_running_size', return_value=True), \
                patch.object(w.engine, 'submit', return_value=True) as submit:
            for row in w.profile.rows:
                w.dispatch('move', row.id)
            submit.assert_called_once()
            self.assertEqual(submit.call_args.args[0].key, 'q')

    def test_global_settings_repair_reenables_standalone_ctrl(self):
        from dataclasses import replace
        w = self.w
        settings = replace(w.settings, capture_hk='f8', pause_hk='f9')
        w.validate_profile_shortcuts(w.profile, settings)
        w.commit_settings(settings)
        w.rebind()
        self.assertEqual({b.key for b in w.hooks.bindings}, {'f8', 'f9', 'ctrl', 'q'})
        self.assertEqual(set(w._shortcut_issues), {w.profile.rows[1].id})
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_new_ctrl_chord_is_still_rejected(self):
        w = self.w
        with patch('app.KeyDialog') as factory:
            factory.return_value.exec.return_value = QDialog.DialogCode.Accepted
            factory.return_value.value.return_value = 'ctrl+q'
            w.edit_key(w.profile.rows[2].id)
        self.assertEqual(w.profile.rows[2].move_hk, 'q')
