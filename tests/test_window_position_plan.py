import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import Mock, patch

from domain import Row, Settings, WindowPositionChanged, WindowSizeUnavailable
from execution_plan import ExecutionPlan


class WindowPositionPlanTests(unittest.TestCase):
    def setUp(self):
        self.native = Mock()
        self.native.size.return_value = (1280, 720)
        self.native.screen.return_value = (-1600, -200)
        self.plan = ExecutionPlan(123, (1280, 720), (1280, 720),
                                  expected_origin=(-1600, -200))

    def test_unchanged_negative_origin_is_valid(self):
        self.plan.check_context(self.native, 123, (1280, 720))
        self.native.screen.assert_called_once_with(123, 0, 0)

    def test_movement_stops_even_when_client_size_is_unchanged(self):
        for origin in ((-1599, -200), (-1600, -201), (100, 200)):
            with self.subTest(origin=origin):
                self.native.screen.return_value = origin
                with self.assertRaises(WindowPositionChanged):
                    self.plan.check_context(self.native, 123, (1280, 720),
                                            size=(1280, 720))

    def test_minimized_size_sample_does_not_compare_transient_origin(self):
        self.native.size.side_effect = WindowSizeUnavailable()
        with self.assertRaises(WindowSizeUnavailable):
            self.plan.check_context(self.native, 123, (1280, 720))
        self.native.screen.assert_not_called()

    def test_deferred_plan_has_no_origin_until_restored(self):
        plan = ExecutionPlan(123, (1280, 720), None)
        plan.check_context(self.native, 123, (1280, 720))
        self.assertIsNone(plan.expected_origin)
        self.native.screen.assert_not_called()

    def test_commands_keep_origin_and_positional_automatic_argument(self):
        plan = ExecutionPlan(123, (1280, 720), (1920, 1080), True,
                             expected_origin=(-1600, -200))
        for row in (Row(x=100, y=200), Row(action_mode='script', script='click 100,200')):
            with self.subTest(mode=row.action_mode):
                command = plan.command(row, Settings())
                self.assertEqual(command.expected_origin, (-1600, -200))
                self.assertTrue(command.automatic)

    def test_new_start_plan_accepts_window_at_its_new_position(self):
        self.native.screen.return_value = (100, -500)
        with self.assertRaises(WindowPositionChanged):
            self.plan.check_context(self.native, 123, (1280, 720))
        restarted = ExecutionPlan(123, (1280, 720), (1280, 720),
                                  expected_origin=tuple(self.native.screen(123, 0, 0)))
        restarted.check_context(self.native, 123, (1280, 720))
        self.assertEqual(self.plan.expected_origin, (-1600, -200))


class WindowPositionStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        from app import MainWindow
        from PySide6.QtWidgets import QMessageBox

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine_factory = patch('app.Engine').start()
        self.addCleanup(patch.stopall)
        self.window = MainWindow(data_dir=self.directory.name, hook_enabled=False)
        self.addCleanup(self.close_window)
        self.window.timer.stop()
        self.window.target = 123
        identity = {'exe': 'Game.exe', 'class': 'GameWindow', 'title': 'Game'}
        self.window.profile.target_program = dict(identity)
        self.window.profile.resolution = (1280, 720)
        patch.object(self.window.native, 'exists', return_value=True).start()
        patch.object(self.window.native, 'window_identity', return_value=identity).start()
        patch.object(self.window.native, 'size', return_value=(1280, 720)).start()
        patch.object(self.window.native, 'cursor', return_value=(10, 20)).start()
        self.minimized = patch.object(self.window.native, 'is_minimized', return_value=False).start()
        self.origin = patch.object(self.window.native, 'screen', return_value=(-1600, -200)).start()
        patch('app.elevation.requires_elevation', return_value=False).start()
        self.warning = patch('app.QMessageBox.warning',
                             return_value=QMessageBox.StandardButton.Ok).start()

    def close_window(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        self.window.shutdown()
        self.window.dirty = False
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_movement_stops_ui_and_restart_uses_new_origin(self):
        window = self.window
        before = window.profile.export()
        window.toggle()
        self.assertTrue(window.armed)
        original_plan = window.session.plan
        self.assertEqual(original_plan.expected_origin, (-1600, -200))
        self.origin.return_value = (-1500, -250)
        window.tick()
        self.assertFalse(window.armed)
        self.assertFalse(window.pause_button.running)
        self.assertIn('창 위치', window.statusBar().currentMessage())
        window.engine.stop.assert_called()

        window.toggle()
        self.assertTrue(window.armed)
        self.assertTrue(window.pause_button.running)
        self.assertEqual(window.session.plan.expected_origin, (-1500, -250))
        self.assertEqual(original_plan.expected_origin, (-1600, -200))
        self.assertTrue(window.check_running_size())
        self.assertEqual(window.profile.export(), before)
        window.engine.submit.assert_not_called()
        self.warning.assert_not_called()

    def test_minimized_start_captures_origin_only_after_restore(self):
        window = self.window
        self.minimized.return_value = True
        window.toggle()
        self.assertTrue(window.armed)
        self.assertTrue(window.session.awaiting_restore)
        self.assertIsNone(window.session.plan.expected_origin)
        self.origin.assert_not_called()

        self.minimized.return_value = False
        self.origin.return_value = (100, -300)
        self.assertTrue(window.check_running_size())
        self.assertTrue(window.armed)
        self.assertFalse(window.session.awaiting_restore)
        self.assertEqual(window.session.plan.expected_origin, (100, -300))
        self.warning.assert_not_called()
