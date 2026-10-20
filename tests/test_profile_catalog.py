import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from domain import read_json
from profile_catalog import ProfileCatalog, profile_entries


class ProfileCatalogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name).resolve()
        self.cache = ProfileCatalog()

    def tearDown(self):
        self.folder.cleanup()

    def write(self, name, content='{"format": "ba-macro-profile-v1", "macros": []}'):
        path = self.directory / name
        path.write_text(content, encoding='utf-8')
        return path

    def entries(self):
        return profile_entries(self.directory, cache=self.cache)

    def test_only_exact_profile_format_is_listed(self):
        import json
        valid = self.write('valid.json')
        self.write('legacy.json', '{"version": 3, "macros": []}')
        self.write('missing.json', '{"macros": []}')
        for i, value in enumerate(('v1', 'ba-macro-profile-v2', None, 1, [], {})):
            self.write(f'invalid_{i}.json', json.dumps({'format': value, 'macros': []}))
        self.assertEqual(self.entries(), [valid])

    def test_unchanged_valid_invalid_and_unrelated_files_are_read_only_once(self):
        valid = self.write('a.json')
        broken = self.write('b.json', '{broken')
        self.write('c.json', '{"theme": "Dark"}')
        with patch('profile_catalog.read_json', wraps=read_json) as reads:
            for _ in range(3):
                self.assertEqual(self.entries(), [valid])
            self.assertEqual(reads.call_count, 3)

    def test_deeply_nested_json_does_not_hide_valid_profiles(self):
        first = self.write('a.json')
        nested = self.write('b.json', '{"macros":' + '[' * 5000 + ']' * 5000 + '}')
        last = self.write('c.json')
        with self.assertRaises(RecursionError):
            read_json(nested)
        self.assertEqual(self.entries(), [first, last])

    def test_deeply_nested_json_is_cached_and_repair_is_visible(self):
        valid = self.write('a.json')
        nested = self.write('b.json', '{"macros":' + '[' * 5000 + ']' * 5000 + '}')
        with patch('profile_catalog.read_json', wraps=read_json) as reads:
            for _ in range(3):
                self.assertEqual(self.entries(), [valid])
            self.assertEqual(reads.call_count, 2)
            nested.write_text('{"format": "ba-macro-profile-v1", "macros": []}', encoding='utf-8')
            self.assertEqual(self.entries(), [valid, nested])
            self.assertEqual(reads.call_count, 3)

    def test_add_delete_rename_and_repair_are_visible_on_next_open(self):
        first = self.write('a.json')
        broken = self.write('b.json', '{broken')
        self.assertEqual(self.entries(), [first])
        broken.write_text('{"format": "ba-macro-profile-v1", "macros": []}', encoding='utf-8')
        added = self.write('c.json')
        self.assertEqual(self.entries(), [first, broken, added])
        renamed = added.with_name('d.json')
        added.rename(renamed)
        first.unlink()
        self.assertEqual(self.entries(), [broken, renamed])

    def test_same_size_edit_invalidates_by_timestamp(self):
        path = self.write('a.json', '{"format": "ba-macro-profile-v1", "macros": []}')
        self.assertEqual(self.entries(), [path])
        info = path.stat()
        path.write_text('{"format": "ba-macro-profile-v1", "macros": {}}', encoding='utf-8')
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
        self.assertEqual(self.entries(), [])

    def test_atomic_replacement_with_same_size_and_mtime_invalidates(self):
        path = self.write('a.json', '{"format": "ba-macro-profile-v1", "macros": []}')
        self.assertEqual(self.entries(), [path])
        info = path.stat()
        replacement = self.write('replacement.tmp', '{"format": "ba-macro-profile-v1", "macros": {}}')
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.replace(replacement, path)
        self.assertEqual(self.entries(), [])

    def test_transient_read_error_is_retried_without_metadata_change(self):
        path = self.write('a.json')
        with patch('profile_catalog.read_json', side_effect=PermissionError('busy')):
            self.assertEqual(self.entries(), [])
        self.assertEqual(self.entries(), [path])

    def test_write_during_read_is_not_cached(self):
        path = self.write('a.json')
        def changed_read(candidate):
            result = read_json(candidate)
            candidate.write_text('{"other": true}', encoding='utf-8')
            return result
        with patch('profile_catalog.read_json', side_effect=changed_read):
            self.assertEqual(self.entries(), [path])
        self.assertNotIn(path, self.cache._entries)
        self.assertEqual(self.entries(), [])

    def test_cache_is_bounded_and_does_not_retain_profile_contents(self):
        self.cache = ProfileCatalog(max_entries=2)
        for name in ('a.json', 'b.json', 'c.json'):
            self.write(name, '{"format": "ba-macro-profile-v1", "macros": [], "script": "large source"}')
        self.assertEqual(len(self.entries()), 3)
        self.assertEqual(len(self.cache._entries), 2)
        for signature, valid in self.cache._entries.values():
            self.assertTrue(all(isinstance(value, int) for value in signature))
            self.assertIs(valid, True)

    def test_directory_with_json_suffix_is_excluded(self):
        (self.directory / 'folder.json').mkdir()
        self.assertEqual(self.entries(), [])

    def test_over_capacity_folder_reuses_cached_files_without_changing_menu_order(self):
        paths = [self.write(f'{index:03}.json') for index in range(257)]
        self.assertEqual(self.entries(), paths)
        with patch('profile_catalog.read_json', wraps=read_json) as reads:
            for _ in range(3):
                reads.reset_mock()
                self.assertEqual(self.entries(), paths)
                self.assertEqual(reads.call_count, 1)
                self.assertEqual(len(self.cache._entries), 256)

    def test_over_capacity_cache_detects_changes_and_removes_deleted_entries(self):
        self.cache = ProfileCatalog(max_entries=2)
        paths = [self.write(f'{index}.json') for index in range(3)]
        self.assertEqual(self.entries(), paths)
        paths[1].write_text('{"other": true}', encoding='utf-8')
        self.assertEqual(self.entries(), [paths[0], paths[2]])
        paths[2].unlink()
        with patch('profile_catalog.read_json', wraps=read_json) as reads:
            self.assertEqual(self.entries(), [paths[0]])
            self.assertNotIn(paths[2], self.cache._entries)
            reads.reset_mock()
            self.assertEqual(self.entries(), [paths[0]])
            reads.assert_not_called()
