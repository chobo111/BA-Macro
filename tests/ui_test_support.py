"""Scoped Windows stubs and non-blocking failure reporting for Qt UI tests."""
from contextlib import contextmanager
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox


@contextmanager
def mock_target_context(window, *, origin=(0, 0)):
    """Complete a fake HWND's position/permission setup without real WinAPI calls.

    Size, identity and existence remain owned by the individual test so their
    expected transitions are not hidden by a global Windows replacement.
    """
    with patch.object(window.native, 'screen', return_value=origin), \
         patch('app.elevation.requires_elevation', return_value=False):
        yield


def fail_on_unexpected_error_dialogs(test_case):
    """Fail at cleanup instead of hanging or throwing inside a Qt callback.

    Tests that intentionally exercise a warning can override these patches
    locally and assert on their own mock as usual.
    """
    messages = []

    def record(parent, title, text, *args, **kwargs):
        messages.append(f'{title}: {text}')
        return QMessageBox.StandardButton.Ok

    patches = [patch.object(QMessageBox, name, side_effect=record)
               for name in ('warning', 'critical')]
    for patcher in patches:
        patcher.start()

    def finish():
        for patcher in reversed(patches):
            patcher.stop()
        if messages:
            test_case.fail('Unexpected error dialog(s):\n' + '\n'.join(messages))

    test_case.addCleanup(finish)
