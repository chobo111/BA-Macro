"""Derived coordinates only; never writes profiles or settings."""
from functools import lru_cache
from dataclasses import replace
from script_language import compile_script, Program

COORD_COMMANDS = {'click', 'move', 'press', 'release', 'switch', 'curve'}

@lru_cache(maxsize=1024)
def scaled_point(point, source, destination):
    x, y = point
    sw, sh = source
    dw, dh = destination
    if min(sw, sh, dw, dh) <= 0:
        raise ValueError('해상도는 0보다 커야 합니다.')
    if not (0 <= x < sw and 0 <= y < sh):
        raise ValueError('원본 좌표가 프로필 기준 해상도 밖에 있습니다.')
    return min(dw - 1, round(x * dw / sw)), min(dh - 1, round(y * dh / sh))

@lru_cache(maxsize=128)
def scaled_program(script, source, destination):
    original = compile_script(script)
    def instructions(items):
        return tuple(replace(item,
            args=tuple(scaled_point(point, source, destination) if point != 'mouse' else point
                       for point in item.args) if item.name in COORD_COMMANDS else item.args,
            body=instructions(item.body)) for item in items)
    return Program(instructions(original.pressed), instructions(original.released))
