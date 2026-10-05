"""Coordinate rules shared by file conversion and derived runtime coordinates."""

COORD_COMMANDS = frozenset({'click', 'move', 'press', 'release', 'switch', 'curve'})


class CoordinateOutOfBounds(ValueError):
    def __init__(self):
        super().__init__('원본 좌표가 프로필 기준 해상도 밖에 있습니다.')


def transform_point(point, source, destination):
    """Scale from the original resolution, rounding only once and keeping edges inside."""
    x, y = point
    sw, sh = source
    dw, dh = destination
    if min(sw, sh, dw, dh) <= 0:
        raise ValueError('해상도는 0보다 커야 합니다.')
    if not (0 <= x < sw and 0 <= y < sh):
        raise CoordinateOutOfBounds()
    return min(dw - 1, round(x * dw / sw)), min(dh - 1, round(y * dh / sh))
