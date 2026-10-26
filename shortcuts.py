"""Canonical shortcut names shared by JSON, Qt adapters and input bindings."""
from keyboard import normalize_name

MODIFIERS = ('ctrl', 'alt', 'shift', 'win')
COMBINATION_MODIFIERS = ('ctrl', 'alt', 'shift')
SHORTCUT_GUIDE = '키 하나 또는 Ctrl/Alt/Shift + 키\nWin 키는 사용할 수 없습니다.'
ROW_SHORTCUT_GUIDE = '키 하나 또는 Alt/Shift + 키\nWin 키는 사용할 수 없습니다.'


class ShortcutError(ValueError):
    """An invalid or conflicting shortcut; report without a warning dialog."""

ALIASES = {
    'control': 'ctrl', 'windows': 'win', 'meta': 'win',
    'escape': 'esc', 'return': 'enter',
    'print': 'print screen', 'printscreen': 'print screen',
    'numlock': 'num lock', 'scrolllock': 'scroll lock', 'capslock': 'caps lock',
    'pgup': 'page up', 'pageup': 'page up',
    'pgdown': 'page down', 'pgdn': 'page down', 'pagedown': 'page down',
    'ins': 'insert', 'del': 'delete', 'spacebar': 'space',
}
QT_NAMES = {'win': 'Meta', 'plus': '+'}


def _key_name(part):
    name = ALIASES.get(part.strip(), part.strip())
    if not name:
        return name
    # Use the input backend's modifier aliases too (command, option, left_ctrl).
    # BA Macro deliberately treats left/right modifiers as the same shortcut.
    physical = normalize_name(name)
    if physical.startswith(('left ', 'right ')):
        physical = physical.split(' ', 1)[1]
    modifier = ALIASES.get(physical, physical)
    return modifier if modifier in MODIFIERS else name


def hotkey(value: str) -> str:
    text = str(value).strip().lower()
    if not text:
        return ''
    # Qt represents the actual plus key as "+" or "Ctrl++".
    if text == '+':
        text = 'plus'
    elif text.endswith('++'):
        text = text[:-1] + 'plus'
    parts = [_key_name(part) for part in text.split('+')]
    if any(not part for part in parts):
        raise ShortcutError('단축키의 키 이름이 비어 있습니다.')
    if len(set(parts)) != len(parts):
        raise ShortcutError('단축키에 같은 키가 두 번 들어갔습니다.')
    if len(parts) > 1 and 'win' in parts:
        raise ShortcutError('Win 조합은 지원하지 않습니다.')
    modifiers = [key for key in MODIFIERS if key in parts]
    keys = [key for key in parts if key not in MODIFIERS]
    if not keys and len(modifiers) == 1:
        return modifiers[0]
    if len(keys) != 1:
        raise ShortcutError('단축키는 키 하나 또는 보조키와 일반 키 조합으로 지정하세요.')
    return '+'.join(modifiers + keys)


def qt_hotkey(value: str) -> str:
    """Translate canonical storage names to Qt PortableText without losing keys."""
    canonical = hotkey(value)
    if canonical == 'ctrl':
        return 'Control'
    return '+'.join(QT_NAMES.get(part, part) for part in canonical.split('+'))


def supported_hotkey(value: str) -> str:
    key = hotkey(value)
    if key == 'win':
        raise ShortcutError('Win 키는 사용할 수 없습니다. 다른 키를 지정하세요.')
    return key


def row_hotkey(value: str) -> str:
    key = supported_hotkey(value)
    if 'ctrl' in key.split('+')[:-1]:
        raise ShortcutError('Shift와 Alt 조합키만 사용할 수 있습니다.')
    return key


def canonical_conflict(first, second):
    """Compare already normalized names; physical overlap is checked by bindings."""
    if first == second:
        return f'중복된 단축키: {first.upper()}'
    for single, chord in ((first, second), (second, first)):
        if single in COMBINATION_MODIFIERS and single in chord.split('+')[:-1]:
            return f'{single.upper()} 단독과 {chord.upper()} 조합은 함께 지정할 수 없습니다.'
    return ''


def validate_hotkeys(values):
    """Validate stored names without discarding unsupported imported settings."""
    used = []
    for value in values:
        normalized = hotkey(value)
        if not normalized:
            continue
        for previous in used:
            if reason := canonical_conflict(previous, normalized):
                raise ShortcutError(reason)
        used.append(normalized)
