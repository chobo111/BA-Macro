import subprocess
import gc
import time
import weakref
import sys
import threading
import unittest
from pathlib import Path

from profile_reads import ProfileReadWorker


class ProfileReadWorkerTests(unittest.TestCase):
    def test_idle_reader_releases_completed_request_references(self):
        class Payload:
            pass

        for fails in (False, True):
            with self.subTest(fails=fails):
                reader = ProfileReadWorker('test-retention')
                def operation(value):
                    if fails:
                        raise ValueError('failed')
                    return value
                value = Payload()
                reference = weakref.ref(value)
                try:
                    future = reader.submit(operation, value)
                    if fails:
                        with self.assertRaises(ValueError):
                            future.result(timeout=2)
                    else:
                        self.assertIs(future.result(timeout=2), value)
                    del future, value
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        gc.collect()
                        if reference() is None:
                            break
                        threading.Event().wait(0.01)
                    self.assertIsNone(reference())
                    self.assertTrue(reader.thread.is_alive())
                finally:
                    reader.close()
                    reader.thread.join(2)

    def test_only_latest_waiting_request_runs(self):
        reader = ProfileReadWorker('test-reader')
        started, release = threading.Event(), threading.Event()
        calls = []
        def blocked():
            started.set()
            release.wait(2)
        try:
            first = reader.submit(blocked)
            self.assertTrue(started.wait(1))
            stale = reader.submit(calls.append, 'stale')
            latest = reader.submit(calls.append, 'latest')
            self.assertTrue(stale.cancelled())
            release.set()
            first.result(timeout=2)
            latest.result(timeout=2)
            self.assertEqual(calls, ['latest'])
        finally:
            release.set()
            reader.close()
            reader.thread.join(2)

    def test_failure_does_not_kill_reader(self):
        reader = ProfileReadWorker('test-reader')
        try:
            def fail():
                raise OSError('offline')
            with self.assertRaisesRegex(OSError, 'offline'):
                reader.submit(fail).result(timeout=2)
            self.assertEqual(reader.submit(lambda: 42).result(timeout=2), 42)
        finally:
            reader.close()
            reader.thread.join(2)

    def test_close_cancels_pending_and_rejects_new_requests(self):
        reader = ProfileReadWorker('test-reader')
        started, release = threading.Event(), threading.Event()
        def blocked():
            started.set()
            release.wait(2)
        try:
            running = reader.submit(blocked)
            self.assertTrue(started.wait(1))
            pending = reader.submit(lambda: 42)
            reader.close()
            reader.close()
            self.assertTrue(pending.cancelled())
            self.assertFalse(running.done())
            with self.assertRaises(RuntimeError):
                reader.submit(lambda: 0)
        finally:
            release.set()
            reader.close()
            reader.thread.join(2)

    def test_blocked_catalog_and_loader_do_not_keep_process_alive(self):
        script = '''
from threading import Event
from unittest.mock import patch
from profile_catalog import ProfileCatalogJobs
from profile_reads import ProfileReadWorker
catalog_started, loader_started = Event(), Event()
never = Event()
def catalog_read(*args, **kwargs):
    catalog_started.set()
    never.wait(30)
def profile_read():
    loader_started.set()
    never.wait(30)
with patch('profile_catalog.profile_entries', side_effect=catalog_read):
    catalog = ProfileCatalogJobs()
    loader = ProfileReadWorker('test-load')
    catalog.submit('.')
    loader.submit(profile_read)
    assert catalog_started.wait(2) and loader_started.wait(2)
    catalog.close()
    loader.close()
    print('closed', flush=True)
'''
        result = subprocess.run([sys.executable, '-B', '-c', script],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(result.stdout.strip(), 'closed')
