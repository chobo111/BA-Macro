import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch, Mock

import elevation as e
from domain import Settings


class ElevationTests(unittest.TestCase):
    def test_failed_commit_after_shutdown_really_exits_qt(self):
        # Exercise the real event loop and shutdown, without UAC or game input.
        source = textwrap.dedent('''\
            import sys
            from unittest.mock import patch
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication
            from app import MainWindow
            from domain import Profile, read_json
            import elevation as e
            app = QApplication([])
            app.setQuitOnLastWindowClosed(False)
            window = MainWindow(data_dir=sys.argv[1], hook_enabled=False)
            app.aboutToQuit.connect(window.shutdown)
            window.show()
            results = []
            def restart():
                window.profile.rows[0].memo = 'saved before failed handoff'
                with patch.object(e, 'ElevatedLaunch') as factory, \\
                     patch.object(e, 'await_ready', return_value=True):
                    launch = factory.return_value
                    launch.commit.side_effect = OSError('handoff failed')
                    results.append(window.request_admin_restart(prompt=False))
                    launch.close.assert_called_once()
            QTimer.singleShot(0, restart)
            QTimer.singleShot(5000, lambda: app.exit(93))
            result = app.exec()
            assert results == [False], results
            assert window.closing
            assert not window.isVisible()
            assert Profile.read(read_json(window.path)).rows[0].memo == 'saved before failed handoff'
            raise SystemExit(result)
        ''')
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [sys.executable, '-B', '-X', 'utf8', '-c', source, folder],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                capture_output=True, text=True, encoding='utf-8', timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_restart_really_exits_qt_and_hands_lock_to_child(self):
        # Use real processes/events/Qt shutdown. Only UAC is replaced with an
        # ordinary child, so this never elevates or accesses a user profile.
        source = textwrap.dedent('''\
            import os, re, subprocess, sys
            from pathlib import Path
            from unittest.mock import patch
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication
            from app import MainWindow
            from single_instance import SingleInstance
            import elevation as e
            app = QApplication([])
            app.setQuitOnLastWindowClosed(False)
            directory = Path(sys.argv[1])
            instance = SingleInstance(directory)
            assert instance.acquire()
            window = MainWindow(data_dir=directory, hook_enabled=False)
            app.aboutToQuit.connect(window.shutdown)
            window.show()
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
                name = re.search(r'--elevated-ready (\\S+)', info.lpParameters).group(1)
                with (directory / 'child.log').open('w', encoding='utf-8') as log:
                    child = subprocess.Popen([sys.executable, '-B', '-c', child_source,
                                              str(os.getpid()), name, str(directory)],
                                             stdout=log, stderr=subprocess.STDOUT,
                                             creationflags=subprocess.CREATE_NO_WINDOW)
                children.append(child)
                (directory / 'child.pid').write_text(str(child.pid), encoding='utf-8')
                info.hProcess = e.open_process(0x100000, False, child.pid)
                assert info.hProcess
                return True
            def restart():
                with patch.object(e, 'shell_execute', launch):
                    assert window.request_admin_restart(prompt=False)
            QTimer.singleShot(0, restart)
            QTimer.singleShot(5000, lambda: app.exit(93))
            try:
                result = app.exec()
            finally:
                instance.close()
            raise SystemExit(result)
        ''')
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [sys.executable, '-B', '-X', 'utf8', '-c', source, folder],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                capture_output=True, text=True, encoding='utf-8', timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            pid = int(Path(folder, 'child.pid').read_text(encoding='utf-8'))
            handle = e.open_process(0x100000, False, pid)
            if handle:
                try:
                    self.assertEqual(e.wait(handle, 5000), 0, 'handoff child did not exit')
                finally:
                    e.close_handle(handle)
            marker = Path(folder, 'handoff-ok')
            self.assertTrue(marker.exists(), Path(folder, 'child.log').read_text(encoding='utf-8'))
            self.assertEqual(marker.read_text(encoding='utf-8'), 'ready')

    def test_setting_default_and_strict_boolean(self):
        self.assertFalse(Settings.read({}).always_run_as_admin)
        self.assertTrue(Settings.read({'always_run_as_admin': True}).always_run_as_admin)
        with self.assertRaises(ValueError):
            Settings.read({'always_run_as_admin': 'true'})

    def test_current_process_token_is_readable(self):
        self.assertIsInstance(e.is_elevated(), bool)

    def test_token_handles_closed_on_query_failure(self):
        def opened(process, access, token):
            token._obj.value = 22
            return True
        with patch.object(e, 'open_process', return_value=11), \
             patch.object(e, 'open_token', side_effect=opened), \
             patch.object(e, 'token_info', return_value=False), \
             patch.object(e, 'close_handle') as close:
            with self.assertRaises(OSError):
                e.is_elevated(123)
        self.assertEqual([call.args[0].value if hasattr(call.args[0], 'value') else call.args[0]
                          for call in close.call_args_list], [22, 11])

    def test_privilege_comparison(self):
        def pid(hwnd, output):
            output._obj.value = 123
            return 1
        for own, target in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(own=own, target=target), \
                 patch.object(e, 'is_elevated', side_effect=[own, target]) as elevated, \
                 patch.object(e, 'window_pid', side_effect=pid):
                self.assertEqual(e.requires_elevation(77), target and not own)
                self.assertEqual(elevated.call_count, 1 if own else 2)

    def test_missing_window_does_not_request_elevation(self):
        with patch.object(e, 'is_elevated', return_value=False), patch.object(e, 'window_pid', return_value=0):
            with self.assertRaises(OSError):
                e.requires_elevation(0)

    def test_restart_command_source_and_frozen_preserve_directory(self):
        directory = Path('folder with spaces').resolve()
        for frozen in (False, True):
            with self.subTest(frozen=frozen), patch.object(e.sys, 'frozen', frozen, create=True):
                exe, args = e.restart_command(directory, 'Local\\BA-Macro-elevate-test')
                self.assertIn('"' + str(directory) + '"', args)
                self.assertIn('--elevated-parent ' + str(os.getpid()), args)
                self.assertEqual('app.py' in args, not frozen)
                self.assertTrue(Path(exe).is_absolute())

    def test_cancelled_uac_closes_events_and_preserves_error(self):
        with patch.object(e, 'shell_execute', return_value=False), \
             patch.object(e.C, 'get_last_error', return_value=1223):
            with self.assertRaises(OSError) as caught:
                e.ElevatedLaunch(Path.cwd(), 0)
        self.assertEqual(caught.exception.winerror, 1223)

    def test_launch_handshake_events_and_handles(self):
        def launch(info):
            # A real synchronizable handle, without launching or elevating anything.
            info._obj.hProcess = e.create_event(None, True, False, None)
            return True
        with patch.object(e, 'shell_execute', side_effect=launch):
            launch = e.ElevatedLaunch(Path.cwd(), 0)
        try:
            self.assertFalse(launch.ready())
            self.assertFalse(launch.finished())
            self.assertEqual(e.wait(launch.commit_event, 0), 258)
            e.set_event(launch.event)
            self.assertTrue(launch.ready())
            launch.commit()
            self.assertEqual(e.wait(launch.commit_event, 0), 0)
        finally:
            launch.close()
        self.assertIsNone(launch.event)
        self.assertIsNone(launch.process)
        self.assertIsNone(launch.commit_event)

    def test_child_requires_commit_and_parent_exit_before_singleton_start(self):
        for results, success in (([0, 0], True), ([258], False), ([0, 258], False)):
            with self.subTest(results=results), \
                 patch.object(e, 'is_elevated', return_value=True), \
                 patch.object(e, 'open_process', return_value=11), \
                 patch.object(e, 'open_event', side_effect=[22, 33]), \
                 patch.object(e, 'set_event', return_value=True), \
                 patch.object(e, 'close_handle') as close, \
                 patch.object(e, 'wait', side_effect=results) as wait:
                if success:
                    e.accept_handoff(123, 'Local\\BA-Macro-elevate-test')
                else:
                    with self.assertRaises(OSError):
                        e.accept_handoff(123, 'Local\\BA-Macro-elevate-test')
                self.assertEqual(wait.call_args_list[0].args, (33, 30000))
                if len(results) == 2:
                    self.assertEqual(wait.call_args_list[1].args, (11, 30000))
                self.assertEqual(close.call_count, 3)

    def test_child_never_acknowledges_without_admin_rights(self):
        with patch.object(e, 'is_elevated', return_value=False), patch.object(e, 'set_event') as signal:
            with self.assertRaises(OSError):
                e.accept_handoff(123, 'Local\\BA-Macro-elevate-test')
        signal.assert_not_called()

    def test_readiness_wait_handles_ready_exit_and_timeout_without_blocking_qt(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QObject, QTimer
        from PySide6.QtWidgets import QApplication
        qt = QApplication.instance() or QApplication([])
        parent = QObject()
        for outcome in ('ready', 'exit', 'timeout'):
            with self.subTest(outcome=outcome):
                launch = Mock()
                launch.ready.return_value = False
                launch.finished.return_value = outcome == 'exit'
                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(lambda: setattr(launch.ready, 'return_value', True))
                if outcome == 'ready':
                    timer.start(5)
                try:
                    self.assertEqual(e.await_ready(launch, parent, timeout_ms=80), outcome == 'ready')
                finally:
                    timer.stop()
        parent.deleteLater()
        qt.processEvents()

    def test_immediately_ready_child_must_still_be_running_without_entering_wait(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QObject, QEventLoop
        from PySide6.QtWidgets import QApplication
        qt = QApplication.instance() or QApplication([])
        parent = QObject()
        try:
            for finished in (False, True):
                launch = Mock()
                launch.ready.return_value = True
                launch.finished.return_value = finished
                with self.subTest(finished=finished), patch.object(
                        QEventLoop, 'exec', side_effect=AssertionError('already-ready child must not wait')):
                    self.assertEqual(e.await_ready(launch, parent), not finished)
                launch.finished.assert_called_once_with()
        finally:
            parent.deleteLater()
            qt.processEvents()
