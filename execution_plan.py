"""Immutable start decisions and input commands, independent of the UI."""
from dataclasses import dataclass

from coordinate_cache import scaled_point, scaled_program
from coordinate_transform import validate_sizes
from domain import Row, Settings, WindowPositionChanged
from engine import Command, WINDOW_SIZE_CHANGED, WindowSizeChanged
from script_language import compile_script


@dataclass(frozen=True)
class ExecutionPlan:
    target: int
    source_resolution: tuple[int, int] | None
    expected_size: tuple[int, int] | None
    automatic: bool = False
    expected_origin: tuple[int, int] | None = None

    def __post_init__(self):
        if not self.target:
            raise ValueError('앱을 선택하세요.')
        if self.automatic and (self.source_resolution is None or self.expected_size is None):
            raise ValueError('자동 변환에는 앱과 기준·실행 해상도가 필요합니다.')
        if self.automatic:
            validate_sizes(self.source_resolution, self.expected_size)

    @property
    def coordinate_transform(self):
        if self.automatic:
            return self.source_resolution, self.expected_size
        return None

    def check_context(self, native, target, resolution, *, size=None):
        if target != self.target:
            raise ValueError('선택한 앱이 변경되었습니다. 다시 시작하세요.')
        if resolution != self.source_resolution:
            raise ValueError('프로필 기준 해상도가 변경되었습니다. 다시 시작하세요.')
        if self.expected_size is not None:
            actual = tuple(size) if size is not None else tuple(native.size(target))
            if actual != self.expected_size:
                raise WindowSizeChanged(WINDOW_SIZE_CHANGED)
        if self.expected_origin is not None:
            if tuple(native.screen(target, 0, 0)) != self.expected_origin:
                raise WindowPositionChanged()

    def command(self, row: Row, settings: Settings) -> Command:
        program = None
        x, y = row.x, row.y
        transform = self.coordinate_transform
        if row.action_mode == 'script':
            program = scaled_program(row.script, *transform) if transform else compile_script(row.script)
        elif transform:
            x, y = scaled_point((x, y), *transform)
        return Command(
            hwnd=self.target, x=x, y=y, mode=row.action_mode, key=row.move_hk,
            click_hold_ms=settings.click_hold_ms, script=row.script,
            drag_segment_duration_ms=settings.drag_segment_duration_ms, program=program,
            expected_size=self.expected_size,
            source_resolution=self.source_resolution, automatic=self.automatic,
            expected_origin=self.expected_origin,
        )
