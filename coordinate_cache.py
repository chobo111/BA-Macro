"""Derived coordinates only; never writes profiles or settings."""
from dataclasses import replace
from functools import lru_cache

from coordinate_transform import COORD_COMMANDS, transform_point, require_same_aspect_ratio
from script_language import Program, compile_script


@lru_cache(maxsize=1024)
def scaled_point(point, source, destination):
    return transform_point(point, source, destination)


@lru_cache(maxsize=128)
def scaled_program(script, source, destination):
    require_same_aspect_ratio(source, destination)
    original = compile_script(script)

    def scale_instructions(items):
        result = []
        for item in items:
            args = item.args
            if item.name in COORD_COMMANDS:
                args = tuple(point if point == 'mouse' else scaled_point(point, source, destination)
                             for point in args)
            result.append(replace(item, args=args, body=scale_instructions(item.body)))
        return tuple(result)

    return Program(scale_instructions(original.pressed), scale_instructions(original.released))
