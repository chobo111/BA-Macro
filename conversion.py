"""Transactional profile coordinate conversion, independent of the UI."""
from copy import deepcopy
from concurrent.futures import CancelledError
from dataclasses import dataclass
import re

from coordinate_transform import COORD_COMMANDS, CoordinateOutOfBounds, transform_point, aspect_warning
from domain import bounded_int
from script_language import compile_script

POINT = re.compile(r'(-?\d+)(\s*,\s*)(-?\d+)')


@dataclass
class ConversionPlan:
    profile: object
    changed_points: int
    errors: list
    warnings: list
    script_points: int = 0
    error_count: int = 0

    def add_error(self, message, *args):
        """Count every error, formatting and retaining only the first three."""
        self.error_count += 1
        if len(self.errors) < 3:
            self.errors.append(message.format(*args))


def plan_conversion(profile, source, destination, *, cancelled=None):
    def check_cancelled():
        if cancelled is not None and cancelled():
            raise CancelledError()

    check_cancelled()
    sw, sh, dw, dh = [bounded_int(v, 1, 100000, '해상도') for v in (*source, *destination)]
    warning = aspect_warning((sw, sh), (dw, dh))
    plan = ConversionPlan(deepcopy(profile), 0, [], [warning] if warning else [])

    def point(x, y, location):
        try:
            new = transform_point((x, y), (sw, sh), (dw, dh))
        except CoordinateOutOfBounds:
            plan.add_error('{}: {}, {} — 원본 해상도 범위 밖입니다.', location, x, y)
            return x, y
        plan.changed_points += (x, y) != new
        return new

    for index, row in enumerate(plan.profile.rows, 1):
        check_cancelled()
        label = f'{index}행' + (f' · {row.move_hk.upper()}' if row.move_hk else '')
        row.x, row.y = point(row.x, row.y, f'{label} · 표')
        # Empty script rows have no script coordinates to validate or convert.
        if not row.script.strip():
            continue
        try:
            compile_script(row.script)
        except ValueError as error:
            plan.add_error('{} · 스크립트 {}', label, error)
            continue
        lines = []
        for line_no, raw in enumerate(row.script.splitlines(keepends=True), 1):
            check_cancelled()
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
        try:
            # Larger coordinates can expand a valid source beyond the load limit.
            compile_script(row.script)
        except ValueError as error:
            plan.add_error('{} · 변환 후 스크립트 {}', label, error)
    check_cancelled()
    plan.profile.resolution = (dw, dh)
    return plan
