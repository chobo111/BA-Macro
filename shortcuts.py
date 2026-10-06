"""Canonical shortcut names shared by JSON, Qt adapters and input bindings."""
MODIFIERS = ('ctrl', 'alt', 'shift', 'win')
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
        raise ValueError('단축키의 키 이름이 비어 있습니다.')
    if len(set(parts)) != len(parts):
        raise ValueError('단축키에 같은 키가 두 번 들어갔습니다.')
    modifiers = [key for key in MODIFIERS if key in parts]
    keys = [key for key in parts if key not in MODIFIERS]
    if not keys and len(modifiers) == 1:
        return modifiers[0]
    if len(keys) != 1:
        raise ValueError('단축키는 키 하나 또는 보조키와 일반 키 조합으로 지정하세요.')
    return '+'.join(modifiers + keys)


def qt_hotkey(value: str) -> str:
    """Translate canonical storage names to Qt PortableText without losing keys."""
    canonical = hotkey(value)
    if canonical == 'ctrl':
        return 'Control'
    return '+'.join(QT_NAMES.get(part, part) for part in canonical.split('+'))
