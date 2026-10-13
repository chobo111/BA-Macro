import unittest

from coordinate_cache import _ScaledProgramCache
from script_language import compile_script


class WeightedCacheTests(unittest.TestCase):
    def test_weight_evicts_old_size_variants_and_preserves_active_program(self):
        cache = _ScaledProgramCache(max_bytes=10000)
        script = 'loop 2\nclick ' + ' '.join(f'{i},1' for i in range(30)) + '\nloop_end\nrelease_actions\nclick 50,50'
        original = compile_script(script)
        first = cache(script, (100, 100), (200, 200))
        for i in range(3, 35):
            latest = cache(script, (100, 100), (i * 100, i * 100))
            self.assertLessEqual(cache.estimated_bytes, cache.max_bytes)
            self.assertIs(latest, cache(script, (100, 100), (i * 100, i * 100)))
        self.assertEqual(first.released[0].args, ((100, 100),))
        self.assertEqual(first.pressed[0].body[0].args[1], (2, 2))
        self.assertIsNot(first, cache(script, (100, 100), (200, 200)))
        self.assertIs(original, compile_script(script))
        self.assertEqual(original.released[0].args, ((50, 50),))

    def test_entry_limit_evicts_least_recently_used(self):
        cache = _ScaledProgramCache(max_entries=2)
        first = cache('sleep 1', (100, 100), (200, 200))
        second = cache('sleep 2', (100, 100), (200, 200))
        self.assertIs(first, cache('sleep 1', (100, 100), (200, 200)))
        cache('sleep 3', (100, 100), (200, 200))
        self.assertIs(first, cache('sleep 1', (100, 100), (200, 200)))
        self.assertIsNot(second, cache('sleep 2', (100, 100), (200, 200)))

    def test_oversized_source_is_not_retained_or_evicting_useful_entries(self):
        cache = _ScaledProgramCache(max_bytes=2000)
        first = cache('sleep 1', (100, 100), (200, 200))
        script = '// ' + '한글🙂' * 1000 + '\nclick 10,10'
        result = cache(script, (100, 100), (200, 200))
        self.assertEqual(result.pressed[0].args, ((20, 20),))
        self.assertLessEqual(cache.estimated_bytes, cache.max_bytes)
        self.assertIs(first, cache('sleep 1', (100, 100), (200, 200)))
        self.assertIsNot(result, cache(script, (100, 100), (200, 200)))

    def test_errors_do_not_fill_cache_and_clear_discards_entries(self):
        cache = _ScaledProgramCache()
        for script, size in (('click 100,0', (200, 200)), ('click 1,1', (0, 200)), ('invalid', (200, 200))):
            with self.assertRaises(ValueError):
                cache(script, (100, 100), size)
        self.assertEqual(cache.estimated_bytes, 0)
        result = cache('sleep 1', (100, 100), (200, 200))
        cache.cache_clear()
        self.assertEqual(cache.estimated_bytes, 0)
        self.assertIsNot(result, cache('sleep 1', (100, 100), (200, 200)))
