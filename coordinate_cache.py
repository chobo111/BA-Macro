"""Derived coordinates only; never writes profiles or settings."""
from collections import OrderedDict
from dataclasses import replace
from functools import lru_cache
from sys import getsizeof
from threading import RLock

from coordinate_transform import COORD_COMMANDS, transform_point, validate_sizes
from script_language import Program, compile_script


@lru_cache(maxsize=1024)
def scaled_point(point, source, destination):
    return transform_point(point, source, destination)


class _ScaledProgramCache:
    """LRU bounded by entry count and conservative retained-size estimates.

    Account for source text, nested/release instructions and every argument.
    The budget covers this cache, not parser caches or active runtime references.
    """
    def __init__(self, max_bytes=8 * 1024 * 1024, max_entries=128):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.entries = OrderedDict()
        self.estimated_bytes = 0
        self.lock = RLock()

    def cache_clear(self):
        with self.lock:
            self.entries.clear()
            self.estimated_bytes = 0

    def __call__(self, script, source, destination):
        key = script, source, destination
        with self.lock:
            if key in self.entries:
                self.entries.move_to_end(key)
                return self.entries[key][0]
            validate_sizes(source, destination)
            original = compile_script(script)
            weight = getsizeof(script) + 512

            def scale_instructions(items):
                nonlocal weight
                result = []
                for item in items:
                    args = item.args
                    # Includes dataclass/dict, sequence slots, line/command and
                    # each argument (a coordinate tuple and its two integers).
                    weight += 512 + 128 * len(args)
                    if item.name in COORD_COMMANDS:
                        args = tuple(point if point == 'mouse' else scaled_point(point, source, destination)
                                     for point in args)
                    result.append(replace(item, args=args, body=scale_instructions(item.body)))
                return tuple(result)

            program = Program(scale_instructions(original.pressed), scale_instructions(original.released))
            if weight <= self.max_bytes:
                while self.entries and (len(self.entries) >= self.max_entries
                                        or self.estimated_bytes + weight > self.max_bytes):
                    _, (_, removed_weight) = self.entries.popitem(last=False)
                    self.estimated_bytes -= removed_weight
                self.entries[key] = program, weight
                self.estimated_bytes += weight
            return program


scaled_program = _ScaledProgramCache()
