"""Read-only file work that never holds up interpreter shutdown."""
from concurrent.futures import Future
from pathlib import Path
from threading import Condition, Thread

from domain import Profile, read_json


def read_profile(path):
    path = Path(path).resolve()
    return path, Profile.read(read_json(path))


class ProfileReadWorker:
    """One daemon reader with at most one waiting request.

    Running OS reads cannot be interrupted safely. They may finish after close,
    but own no Qt objects and perform no writes. Never use this for persistence:
    saving must still finish before the app exits.
    """
    def __init__(self, name):
        self.condition = Condition()
        self.pending = None
        self.closed = False
        self.thread = Thread(target=self._run, name=name, daemon=True)
        self.started = False

    def submit(self, operation, *args, **kwargs):
        future = Future()
        with self.condition:
            if self.closed:
                raise RuntimeError('Profile reader is closed')
            if self.pending is not None:
                self.pending[0].cancel()
            self.pending = future, operation, args, kwargs
            if not self.started:
                self.thread.start()
                self.started = True
            self.condition.notify()
        return future

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                request = self.pending
                self.pending = None
            try:
                self._execute(request)
            finally:
                del request

    @staticmethod
    def _execute(request):
        future, operation, args, kwargs = request
        if not future.set_running_or_notify_cancel():
            return
        try:
            result = operation(*args, **kwargs)
        except BaseException as error:
            future.set_exception(error)
        else:
            future.set_result(result)

    def close(self):
        with self.condition:
            self.closed = True
            if self.pending is not None:
                self.pending[0].cancel()
                self.pending = None
            self.condition.notify()
