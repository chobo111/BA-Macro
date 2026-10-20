"""Canonical shortcut names shared by JSON, Qt adapters and input bindings."""
MODIFIERS = ('ctrl', 'alt', 'shift', 'win')
COMBINATION_MODIFIERS = ('ctrl', 'alt', 'shift')
SHORTCUT_GUIDE = 'Ctrl, Alt, Shift + 키만 가능합니다.'
ROW_SHORTCUT_GUIDE = '조합키는 Alt, Shift + 키만 가능합니다.'


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


def hotkey(value: str) -> str:
    text = str(value).strip().lower()
    if not text:
        return ''
    # Qt represents the actual plus key as "+" or "Ctrl++".
    if text == '+':
        text = 'plus'
    elif text.endswith('++'):
        text = text[:-1] + 'plus'
    parts = [ALIASES.get(part.strip(), part.strip()) for part in text.split('+')]
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


def row_hotkey(value: str) -> str:
    key = hotkey(value)
    if 'ctrl' in key.split('+')[:-1]:
        raise ShortcutError('Shift와 Alt 조합키만 사용할 수 있습니다.')
    return key


def validate_hotkeys(values):
    """Reject duplicates and standalone modifiers used as another chord's prefix."""
    used = set()
    for value in values:
        normalized = hotkey(value)
        if not normalized:
            continue
        if normalized in used:
            raise ShortcutError(f'중복된 단축키: {normalized.upper()}')
        used.add(normalized)
    for modifier in COMBINATION_MODIFIERS:
        if modifier not in used:
            continue
        conflict = next((key for key in sorted(used) if modifier in key.split('+')[:-1]), None)
        if conflict:
            raise ShortcutError(f'{modifier.upper()} 단독과 {conflict.upper()} 조합은 함께 지정할 수 없습니다.')
