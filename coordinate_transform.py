"""Coordinate rules shared by file conversion and derived runtime coordinates."""

from math import gcd

COORD_COMMANDS = frozenset({'click', 'move', 'press', 'release', 'switch', 'curve'})


class CoordinateOutOfBounds(ValueError):
    def __init__(self):
        super().__init__('원본 좌표가 프로필 기준 해상도 밖에 있습니다.')


def validate_sizes(source, destination):
    if min(*source, *destination) <= 0:
        raise ValueError('창 크기는 0보다 커야 합니다.')


def aspect_label(size):
    width, height = size
    divisor = gcd(width, height)
    return f'{width // divisor}:{height // divisor}'


def aspect_warning(source, destination):
    """Allow one pixel of width quantization in each height-based ratio.

    Compare the two possible ratio intervals with integer arithmetic. This
    accounts for a rounded source as well as a rounded destination, even when
    converting back to a larger size.
    """
    validate_sizes(source, destination)
    sw, sh = source
    dw, dh = destination
    if abs(sw * dh - dw * sh) <= sh + dh:
        return ''
    return (f'경고: 기준 창 비율({aspect_label(source)})과 변환할 크기의 비율({aspect_label(destination)})이 다릅니다. '
            '변환은 가능하지만 게임 화면이나 UI 배치에 따라 좌표가 어긋날 수 있습니다.')


def transform_point(point, source, destination):
    """Scale from the original resolution, rounding only once and keeping edges inside."""
    x, y = point
    sw, sh = source
    dw, dh = destination
    validate_sizes(source, destination)
    if not (0 <= x < sw and 0 <= y < sh):
        raise CoordinateOutOfBounds()
    return min(dw - 1, round(x * dw / sw)), min(dh - 1, round(y * dh / sh))
