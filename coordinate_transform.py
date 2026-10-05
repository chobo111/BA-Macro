"""Coordinate rules shared by file conversion and derived runtime coordinates."""

from math import gcd

COORD_COMMANDS = frozenset({'click', 'move', 'press', 'release', 'switch', 'curve'})


class CoordinateOutOfBounds(ValueError):
    def __init__(self):
        super().__init__('원본 좌표가 프로필 기준 해상도 밖에 있습니다.')


def same_aspect_ratio(source, destination):
    sw, sh = source
    dw, dh = destination
    if min(sw, sh, dw, dh) <= 0:
        raise ValueError('창 크기는 0보다 커야 합니다.')
    return sw * dh == sh * dw


def aspect_label(size):
    width, height = size
    divisor = gcd(width, height)
    return f'{width // divisor}:{height // divisor}'


class AspectRatioMismatch(ValueError):
    def __init__(self, source, destination):
        super().__init__(
            f'기준 창 비율({aspect_label(source)})과 변환할 크기의 비율({aspect_label(destination)})이 다릅니다. '
            '비율이 다르면 좌표를 변환할 수 없습니다. 기준과 같은 비율로 크기를 맞춰 주세요.')


def require_same_aspect_ratio(source, destination):
    if not same_aspect_ratio(source, destination):
        raise AspectRatioMismatch(source, destination)


def transform_point(point, source, destination):
    """Scale from the original resolution, rounding only once and keeping edges inside."""
    x, y = point
    sw, sh = source
    dw, dh = destination
    require_same_aspect_ratio(source, destination)
    if not (0 <= x < sw and 0 <= y < sh):
        raise CoordinateOutOfBounds()
    return min(dw - 1, round(x * dw / sw)), min(dh - 1, round(y * dh / sh))
