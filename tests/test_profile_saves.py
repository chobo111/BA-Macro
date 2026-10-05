from concurrent.futures import Future
from pathlib import Path
import unittest

from profile_saves import ProfileSaves


class ProfileSavesTests(unittest.TestCase):
    def setUp(self):
        self.saves = ProfileSaves()
        self.path = Path('macro.json')

    def submit(self, path=None, future=None):
        revision = self.saves.begin()
        future = Future() if future is None else future
        self.saves.register(revision, self.path if path is None else path, future)
        return revision, future

    def test_only_latest_completion_applies_in_either_delivery_order(self):
        for newest_first in (False, True):
            with self.subTest(newest_first=newest_first):
                self.saves = ProfileSaves()
                first, _ = self.submit()
                latest, _ = self.submit()
                revisions = (latest, first) if newest_first else (first, latest)
                accepted = [revision for revision in revisions
                            if self.saves.complete(revision, self.path)]
                self.assertEqual(accepted, [latest])
                self.assertEqual(self.saves.pending(), ())
                self.assertFalse(self.saves.complete(latest, self.path))

    def test_failed_submission_keeps_prior_completion_stale(self):
        first, _ = self.submit()
        failed = self.saves.begin()
        # A store submission can raise before a Future is available to register.
        self.assertEqual(self.saves.revision, failed)
        self.assertFalse(self.saves.complete(first, self.path))
        self.assertEqual(self.saves.pending(), ())
        retried, _ = self.submit()
        self.assertTrue(self.saves.complete(retried, self.path))

    def test_coalesced_future_keeps_each_revision_until_consumed(self):
        shared = Future()
        first, _ = self.submit(future=shared)
        latest, _ = self.submit(future=shared)
        shared.set_result('latest snapshot saved')
        results = [(revision, future.result(), self.saves.complete(revision, self.path))
                   for revision, future in self.saves.pending()]
        self.assertEqual(results, [
            (first, 'latest snapshot saved', False),
            (latest, 'latest snapshot saved', True),
        ])
        self.assertEqual(self.saves.pending(), ())

    def test_completion_for_previous_profile_cannot_change_current_profile(self):
        previous, _ = self.submit()
        self.assertFalse(self.saves.complete(previous, Path('other.json')))
        # A consumed callback must not apply after reopening that old profile.
        self.assertFalse(self.saves.complete(previous, self.path))
        latest, _ = self.submit(Path('other.json'))
        self.assertTrue(self.saves.complete(latest, Path('other.json')))

    def test_no_current_profile_rejects_pending_completion(self):
        revision, _ = self.submit()
        self.assertFalse(self.saves.complete(revision, None))
        self.assertEqual(self.saves.pending(), ())

    def test_flush_snapshot_preserves_submission_order_across_paths(self):
        first, first_future = self.submit()
        other, other_future = self.submit(Path('other.json'))
        snapshot = self.saves.pending()
        latest, latest_future = self.submit()
        self.assertEqual(snapshot, ((first, first_future), (other, other_future)))
        for revision, future in snapshot:
            future.set_result('saved')
            self.assertFalse(self.saves.complete(revision, self.path))
        self.assertEqual(self.saves.pending(), ((latest, latest_future),))
        self.assertTrue(self.saves.complete(latest, self.path))


if __name__ == '__main__':
    unittest.main()
