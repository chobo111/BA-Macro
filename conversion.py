"""Transactional profile coordinate conversion, independent of the UI."""
from copy import deepcopy
from dataclasses import dataclass
import re

from domain import bounded_int
from script_language import compile_script

COORD_COMMANDS = {'click', 'move', 'press', 'release', 'switch', 'curve'}
POINT = re.compile(r'(-?\d+)(\s*,\s*)(-?\d+)')


@dataclass
class ConversionPlan:
    profile: object
    changes: list
    errors: list
    warnings: list
    script_points: int = 0


def plan_conversion(profile, source, destination):
    sw, sh, dw, dh = [bounded_int(v, 1, 100000, '해상도') for v in (*source, *destination)]
    plan = ConversionPlan(deepcopy(profile), [], [], [])
    if sw * dh != sh * dw:
        plan.warnings.append('화면 비율이 다릅니다. X/Y를 각각 비례 변환하므로 게임 UI 배치나 검은 여백에 따라 수동 조정이 필요합니다.')

    def point(x, y, location):
        if not (0 <= x < sw and 0 <= y < sh):
            plan.errors.append(f'{location}: {x}, {y} — 원본 해상도 범위 밖입니다.')
            return x, y
        new = min(dw - 1, round(x * dw / sw)), min(dh - 1, round(y * dh / sh))
        plan.changes.append((location, (x, y), new))
        return new

    for index, row in enumerate(plan.profile.rows, 1):
        label = f'{index}행' + (f' · {row.move_hk.upper()}' if row.move_hk else '')
        row.x, row.y = point(row.x, row.y, f'{label} · 표')
        if not row.script.strip() and row.action_mode != 'script':
            continue
        try:
            compile_script(row.script)
        except ValueError as error:
            plan.errors.append(f'{label} · 스크립트 {error}')
            continue
        lines = []
        for line_no, raw in enumerate(row.script.splitlines(keepends=True), 1):
            command = raw.strip().split(maxsplit=1)
            if command and command[0].lower() in COORD_COMMANDS:
                code, separator, comment = raw.partition('//')
                def replace(match):
                    plan.script_points += 1
                    x, y = point(int(match[1]), int(match[3]), f'{label} · 스크립트 {line_no}줄')
                    return f'{x}{match[2]}{y}'
                raw = POINT.sub(replace, code) + separator + comment
            lines.append(raw)
        row.script = ''.join(lines)
    plan.profile.resolution = (dw, dh)
    return plan
