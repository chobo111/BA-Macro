"""Execution eligibility and nested editing-session lifecycle, independent of Qt."""
from contextlib import contextmanager
from functools import wraps
import os


class ExecutionSession:
    def __init__(self, native):
        self.native = native
        self.armed = False
        self.modal = False
        self.editor_open = False
        self.closing = False
        self.target = 0
        self.plan = None

    @property
    def run_size(self):
        return self.plan.expected_size if self.plan is not None else None

    @property
    def coordinate_transform(self):
        return self.plan.coordinate_transform if self.plan is not None else None

    def start(self, plan):
        if plan.target != self.target:
            raise ValueError('선택한 앱이 변경되었습니다. 다시 시작하세요.')
        self.plan = plan
        self.armed = True

    def stop(self):
        self.armed = False
        self.plan = None

    def eligible(self, action, auto_pause):
        if self.closing:
            return False
        if self.modal or self.editor_open:
            if auto_pause or action == 'capture_selected':
                return False
            if action != 'pause' and self.native.foreground_owned_by(os.getpid()):
                return False
        if action in ('pause', 'capture_selected'):
            return True
        if action == 'move':
            return self.armed and (not self.target or self.native.active(self.target))
        return not self.target or self.native.active(self.target)

    @contextmanager
    def editing(self, pause, stop, close_hooks=None, restore_hooks=None):
        previous = self.modal
        try:
            if pause:
                stop()
                if close_hooks:
                    close_hooks()
            self.modal = True
            yield
        finally:
            self.modal = previous
            if restore_hooks and not self.closing:
                restore_hooks()


def editing_dialog(*, hooks=False, pause=True):
    """Keep all dialog entry/exit paths balanced, including cancellation/errors."""
    def decorate(method):
        @wraps(method)
        def wrapped(window, *args, **kwargs):
            with window.session.editing(
                pause and window.settings.auto_pause, window.stop,
                window.hooks.close if hooks else None,
                window.restart_hooks if hooks else None,
            ):
                return method(window, *args, **kwargs)
        return wrapped
    return decorate
