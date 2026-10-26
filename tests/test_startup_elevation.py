"""Startup elevation never creates a transient window or rewrites user data."""
from contextlib import ExitStack
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch

import app as studio


class StartupElevationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.directory = Path(self.folder.name).resolve()
        self.settings = self.directory / 'setting.json'
        self.parent = Mock()

    def write_settings(self, value):
        self.settings.write_text(json.dumps(value), encoding='utf-8')

    def test_enabled_launch_finishes_handoff_without_loading_or_saving_profile(self):
        self.write_settings({'always_run_as_admin': True, 'last_profile': 'user.json'})
        profile = self.directory / 'user.json'
        profile.write_text('preserve user data', encoding='utf-8')
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        order = []
        launch = Mock()
        launch.commit.side_effect = lambda: order.append('commit')
        launch.close.side_effect = lambda: order.append('close')
        with patch.object(studio.elevation, 'is_elevated', return_value=False), \
             patch.object(studio.elevation, 'ElevatedLaunch', return_value=launch) as factory, \
             patch.object(studio.elevation, 'await_ready',
                          side_effect=lambda *args: order.append('ready') or True) as ready, \
             patch.object(studio.ProfileStore, 'load') as load:
            self.assertTrue(studio.startup_elevation(self.directory, self.parent))
        factory.assert_called_once_with(self.directory, 0)
        ready.assert_called_once_with(launch, self.parent)
        self.assertEqual(order, ['ready', 'commit', 'close'])
        load.assert_not_called()
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.directory.iterdir()})

    def test_missing_settings_does_not_create_files_or_request_elevation(self):
        with patch.object(studio.elevation, 'ElevatedLaunch') as launch:
            self.assertFalse(studio.startup_elevation(self.directory, self.parent))
        launch.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_invalid_settings_are_left_for_normal_load_error_handling(self):
        for content in ('{', '[]', '{"always_run_as_admin":"true"}',
                        '{"always_run_as_admin":true,"theme":"invalid"}',
                        '{"always_run_as_admin":true,"click_hold_ms":Infinity}'):
            with self.subTest(content=content):
                self.settings.write_text(content, encoding='utf-8')
                with patch.object(studio.elevation, 'ElevatedLaunch') as launch:
                    self.assertFalse(studio.startup_elevation(self.directory, self.parent))
                launch.assert_not_called()
                self.assertEqual(self.settings.read_text(encoding='utf-8'), content)

    def test_disabled_option_does_not_request_elevation(self):
        self.write_settings({'always_run_as_admin': False})
        with patch.object(studio.elevation, 'ElevatedLaunch') as launch:
            self.assertFalse(studio.startup_elevation(self.directory, self.parent))
        launch.assert_not_called()

    def test_already_elevated_does_not_relaunch(self):
        self.write_settings({'always_run_as_admin': True})
        with patch.object(studio.elevation, 'is_elevated', return_value=True), \
             patch.object(studio.elevation, 'ElevatedLaunch') as launch:
            self.assertFalse(studio.startup_elevation(self.directory, self.parent))
        launch.assert_not_called()

    def test_not_ready_keeps_launch_uncommitted_and_closes_handles(self):
        self.write_settings({'always_run_as_admin': True})
        with patch.object(studio.elevation, 'is_elevated', return_value=False), \
             patch.object(studio.elevation, 'ElevatedLaunch') as factory, \
             patch.object(studio.elevation, 'await_ready', return_value=False):
            with self.assertRaises(OSError):
                studio.startup_elevation(self.directory, self.parent)
        factory.return_value.commit.assert_not_called()
        factory.return_value.close.assert_called_once()

    def test_commit_failure_closes_handles_and_propagates(self):
        self.write_settings({'always_run_as_admin': True})
        with patch.object(studio.elevation, 'is_elevated', return_value=False), \
             patch.object(studio.elevation, 'ElevatedLaunch') as factory, \
             patch.object(studio.elevation, 'await_ready', return_value=True):
            factory.return_value.commit.side_effect = OSError('commit failure')
            with self.assertRaisesRegex(OSError, 'commit failure'):
                studio.startup_elevation(self.directory, self.parent)
        factory.return_value.close.assert_called_once()


class StartupMainTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.directory = Path(self.folder.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.qt = self.stack.enter_context(patch.object(studio, 'QApplication')).return_value
        self.qt.exec.return_value = 0
        self.instance = self.stack.enter_context(patch.object(studio, 'SingleInstance')).return_value
        self.instance.acquire.return_value = True
        self.window_factory = self.stack.enter_context(patch.object(studio, 'MainWindow'))
        self.window = self.window_factory.return_value
        self.startup = self.stack.enter_context(patch.object(studio, 'startup_elevation', return_value=False))
        self.retry = self.stack.enter_context(patch.object(studio.QTimer, 'singleShot'))
        self.critical = self.stack.enter_context(patch.object(studio.QMessageBox, 'critical'))
        self.stack.enter_context(patch.object(studio.QStandardPaths, 'writableLocation',
                                            return_value=str(self.directory)))
        self.stack.enter_context(patch.object(studio, 'BASE', self.directory))
        self.stack.enter_context(patch.object(studio.sys, 'argv', ['app.py']))

    def test_successful_startup_relaunch_does_not_create_window_or_run_event_loop(self):
        self.startup.return_value = True
        self.assertEqual(studio.main(), 0)
        self.startup.assert_called_once_with(self.directory, self.qt)
        self.window_factory.assert_not_called()
        self.qt.exec.assert_not_called()
        self.instance.close.assert_called_once()

    def test_existing_instance_skips_elevation_and_window_creation(self):
        self.instance.acquire.return_value = False
        self.assertEqual(studio.main(), 0)
        self.startup.assert_not_called()
        self.window_factory.assert_not_called()
        self.instance.close.assert_called_once()

    def test_normal_start_checks_elevation_before_constructing_window(self):
        order = []
        self.instance.acquire.side_effect = lambda: order.append('acquire') or True
        self.startup.side_effect = lambda *args: order.append('elevation') or False
        self.window_factory.side_effect = lambda **kwargs: order.append('window') or self.window
        self.window.show.side_effect = lambda: order.append('show')
        self.qt.exec.side_effect = lambda: order.append('exec') or 0
        self.instance.close.side_effect = lambda: order.append('close')
        self.assertEqual(studio.main(), 0)
        self.assertEqual(order, ['acquire', 'elevation', 'window', 'show', 'exec', 'close'])
        self.retry.assert_not_called()

    def test_custom_profile_directory_is_used_for_precheck_and_window(self):
        directory = self.directory / 'profiles with spaces'
        self.stack.enter_context(patch.object(studio.sys, 'argv',
                                            ['app.py', '--data-dir', str(directory)]))
        self.assertEqual(studio.main(), 0)
        self.startup.assert_called_once_with(directory.resolve(), self.qt)
        self.window_factory.assert_called_once_with(data_dir=directory.resolve())

    def test_cancelled_uac_opens_normal_window_once_and_shows_cancel_notice(self):
        cancelled = OSError('cancelled')
        cancelled.winerror = 1223
        self.startup.side_effect = cancelled
        self.assertEqual(studio.main(), 0)
        self.startup.assert_called_once()
        self.window_factory.assert_called_once()
        self.window.show.assert_called_once()
        self.window.statusBar.return_value.showMessage.assert_called_once()
        self.window.statusBar.return_value.show_error.assert_not_called()
        self.retry.assert_not_called()
        self.critical.assert_not_called()

    def test_failed_elevation_opens_normal_window_and_reports_failure(self):
        self.startup.side_effect = OSError('child failed')
        self.assertEqual(studio.main(), 0)
        self.window.show.assert_called_once()
        notice = self.window.statusBar.return_value.show_error.call_args.args[0]
        self.assertIn('child failed', notice)
        self.retry.assert_not_called()
        self.critical.assert_not_called()
        self.instance.close.assert_called_once()

    def test_elevated_child_accepts_handoff_before_taking_singleton(self):
        order = []
        self.stack.enter_context(patch.object(studio.sys, 'argv',
                                            ['app.py', '--elevated-parent', '123',
                                             '--elevated-ready', 'Local\\BA-Macro-elevate-test']))
        accept = self.stack.enter_context(patch.object(
            studio.elevation, 'accept_handoff', side_effect=lambda *args: order.append('handoff')))
        self.instance.acquire.side_effect = lambda: order.append('acquire') or True
        self.startup.side_effect = lambda *args: order.append('elevation') or False
        self.assertEqual(studio.main(), 0)
        self.assertEqual(order, ['handoff', 'acquire', 'elevation'])
        accept.assert_called_once_with(123, 'Local\\BA-Macro-elevate-test')


class StartupHandoffProcessTests(unittest.TestCase):
    def test_early_main_exit_hands_real_events_and_lock_to_child_without_window(self):
        # Replace only the UAC launch with an ordinary owned child. Actual Qt
        # startup, readiness events, process exit and singleton ownership remain.
        source = textwrap.dedent('''\
            import os, re, subprocess, sys
            from pathlib import Path
            from unittest.mock import patch
            import app as studio
            import elevation as e
            directory = Path(sys.argv[1])
            lock_directory = directory / 'instance'
            child_source = """
            import sys
            from pathlib import Path
            from PySide6.QtCore import QCoreApplication
            from single_instance import SingleInstance
            import elevation as e
            app = QCoreApplication([])
            e.is_elevated = lambda: True
            e.accept_handoff(int(sys.argv[1]), sys.argv[2])
            instance = SingleInstance(sys.argv[3])
            assert instance.acquire(), 'old singleton still owns the lock'
            Path(sys.argv[3], 'handoff-ok').write_text('ready', encoding='utf-8')
            instance.close()
            """
            children = []
            def launch(info_pointer):
                info = info_pointer._obj
                assert not info.hwnd, 'startup created a window for elevation'
                name = re.search(r'--elevated-ready (\\S+)', info.lpParameters).group(1)
                with (directory / 'child.log').open('w', encoding='utf-8') as log:
                    child = subprocess.Popen([sys.executable, '-B', '-c', child_source,
                                              str(os.getpid()), name, str(lock_directory)],
                                             stdout=log, stderr=subprocess.STDOUT,
                                             creationflags=subprocess.CREATE_NO_WINDOW)
                children.append(child)
                (directory / 'child.pid').write_text(str(child.pid), encoding='utf-8')
                info.hProcess = e.open_process(0x100000, False, child.pid)
                assert info.hProcess
                return True
            with patch.object(studio.sys, 'argv', ['app.py', '--data-dir', str(directory)]), \\
                 patch.object(studio.QStandardPaths, 'writableLocation', return_value=str(lock_directory)), \\
                 patch.object(e, 'is_elevated', return_value=False), \\
                 patch.object(e, 'shell_execute', side_effect=launch), \\
                 patch.object(studio, 'MainWindow', side_effect=AssertionError('transient main window')), \\
                 patch.object(studio.QMessageBox, 'critical', side_effect=AssertionError('unexpected dialog')):
                result = studio.main()
            raise SystemExit(result)
        ''')
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            settings = directory / 'setting.json'
            content = '{"always_run_as_admin":true}'
            settings.write_text(content, encoding='utf-8')
            result = subprocess.run(
                [sys.executable, '-B', '-X', 'utf8', '-c', source, folder],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                capture_output=True, text=True, encoding='utf-8', timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            pid = int((directory / 'child.pid').read_text(encoding='utf-8'))
            handle = studio.elevation.open_process(0x100000, False, pid)
            if handle:
                try:
                    self.assertEqual(studio.elevation.wait(handle, 5000), 0, 'handoff child did not exit')
                finally:
                    studio.elevation.close_handle(handle)
            self.assertTrue((directory / 'instance' / 'handoff-ok').exists(),
                            (directory / 'child.log').read_text(encoding='utf-8'))
            self.assertFalse((directory / 'macro.json').exists())
            self.assertEqual(settings.read_text(encoding='utf-8'), content)


if __name__ == '__main__':
    unittest.main()
