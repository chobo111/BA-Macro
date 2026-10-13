"""Cancellable conversion of private profile snapshots; no Qt objects."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

from conversion import plan_conversion


class ConversionJobs:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='coordinate-conversion')
        self.future = None
        self.cancelled = Event()
        self.closed = False

    def cancel(self):
        self.cancelled.set()
        if self.future is not None:
            self.future.cancel()
            self.future = None

    def submit(self, profile, source, destination):
        if self.closed:
            raise RuntimeError('Conversion worker is closed')
        self.cancel()
        self.cancelled = Event()
        # Snapshot on the caller thread; subsequent UI edits cannot race the worker.
        self.future = self.executor.submit(plan_conversion, deepcopy(profile), source, destination,
                                           cancelled=self.cancelled.is_set)
        return self.future

    def close(self):
        if not self.closed:
            self.closed = True
            self.cancel()
            self.executor.shutdown(wait=False, cancel_futures=True)
