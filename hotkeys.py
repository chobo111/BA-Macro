"""Keyboard hook with conditional suppression and one dispatch per chord press."""
from dataclasses import dataclass, replace
import threading
import keyboard
from shortcuts import hotkey, supported_hotkey, row_hotkey, canonical_conflict, ShortcutError, COMBINATION_MODIFIERS


# Navigation, keypad and special keys share scan codes. The virtual key describes
# the logical key (including NumLock), while the extended flag identifies the
# physical key for matching releases and retaining independently held keys.
LOGICAL_KEYS = {
    'page up': 0x21, 'page down': 0x22, 'end': 0x23, 'home': 0x24,
    'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
    'insert': 0x2D, 'delete': 0x2E, 'clear': 0x0C,
    'pause': 0x13, 'num lock': 0x90, 'print screen': 0x2C,
    'scroll lock': 0x91, 'caps lock': 0x14,
}

KEYPAD_SYMBOLS = {'+': 0x6B, '*': 0x6A, '.': 0x6E, '/': 0x6F, '-': 0x6D}


@dataclass(frozen=True)
class PressedKey:
    scan_code: int
    virtual_key: int | None
    is_extended: bool

    @property
    def identity(self):
        return self.scan_code, self.is_extended


@dataclass(frozen=True)
class KeyGroup:
    scan_codes: frozenset
    virtual_keys: frozenset = frozenset()
    key_codes: frozenset | None = None

    def matches(self, key):
        if self.key_codes is not None:
            return (key.scan_code, key.virtual_key) in self.key_codes
        return (key.scan_code in self.scan_codes
                and (not self.virtual_keys or key.virtual_key in self.virtual_keys))

    def overlaps(self, other):
        """Whether one physical event can satisfy both matchers."""
        if self.key_codes is not None:
            return any(other.matches(PressedKey(scan, vk, False)) for scan, vk in self.key_codes)
        if other.key_codes is not None:
            return other.overlaps(self)
        return (bool(self.scan_codes & other.scan_codes)
                and (not self.virtual_keys or not other.virtual_keys
                     or bool(self.virtual_keys & other.virtual_keys)))

    @classmethod
    def compile(cls, name, scan_codes, modifiers=()):
        name = keyboard.normalize_name(name)
        key_codes = None
        if len(name) == 1 and name in '0123456789':
            virtual_keys = frozenset((ord(name), 0x60 + int(name)))
        elif name in KEYPAD_SYMBOLS:
            from keyboard_hook import character_key, key_scan_code
            # Keypad symbols have independent virtual keys. Main-keyboard
            # symbols also require their layout-specific modifiers.
            keys = {KEYPAD_SYMBOLS[name]}
            mapped = character_key(name)
            if mapped and mapped[1].issubset(modifiers):
                keys.add(mapped[0])
            virtual_keys = frozenset(keys)
            key_codes = frozenset((key_scan_code(vk), vk) for vk in keys)
        elif name in LOGICAL_KEYS:
            virtual_keys = frozenset((LOGICAL_KEYS[name],))
        else:
            if len(name) == 1:
                from keyboard_hook import character_key
                mapped = character_key(name)
                if mapped is None:
                    raise ShortcutError(f'현재 키보드 배열에서 사용할 수 없는 문자입니다: {name}')
                if not mapped[1].issubset(modifiers):
                    from keyboard_hook import base_character
                    required = '+'.join(key.title() for key in COMBINATION_MODIFIERS if key in mapped[1])
                    base = base_character(mapped[0]) or name
                    keys = [key.title() for key in COMBINATION_MODIFIERS if key in set(modifiers) | mapped[1]]
                    suggestion = '+'.join((*keys, base.upper()))
                    raise ShortcutError(f"'{name}' 단축키에는 {required} 조합이 필요합니다. {suggestion}로 지정하세요.")
            virtual_keys = frozenset()
        return cls(frozenset(scan_codes), virtual_keys, key_codes)


@dataclass(frozen=True)
class Binding:
    key: str
    action: str
    row_id: str
    groups: tuple

    def overlaps(self, other):
        return (len(self.groups) == len(other.groups)
                and all(a.overlaps(b) for a, b in zip(self.groups, other.groups)))


def binding_conflict(first, second):
    """Compare compiled bindings without reparsing names or keyboard mappings."""
    if reason := canonical_conflict(first.key, second.key):
        return reason
    if first.overlaps(second):
        return f'실제 입력이 중복된 단축키: {first.key.upper()} / {second.key.upper()}'
    return ''


@dataclass(frozen=True)
class ProfileBindings:
    globals: tuple
    rows: tuple
    issues: dict

    @property
    def keys(self):
        return tuple(binding.key if binding is not None else '' for binding in self.rows)


class Hotkeys:
    def __init__(self, dispatch, eligible, emergency_stop, on_error=None):
        self.dispatch, self.eligible, self.emergency_stop = dispatch, eligible, emergency_stop
        self.on_error = on_error
        self.lock = threading.RLock()
        self.bindings = []
        self.pressed = {}
        self.fired = set()
        self.suppressed = set()
        self.exclusive = False
        self.unhook = None
        self.modifiers = set()
        for key in ("ctrl", "alt", "shift", "windows"):
            self.modifiers.update(keyboard.key_to_scan_codes(key))

    @staticmethod
    def compile(key, action="", row_id="", *, row=False):
        normalized = row_hotkey(key) if row else supported_hotkey(key)
        if not normalized:
            return None
        parts = normalized.split("+")
        names = ["windows" if part == "win" else part for part in parts]
        groups = tuple(KeyGroup.compile(name, keyboard.key_to_scan_codes(name), parts[:-1])
                       for name in names)
        return Binding(normalized, action, row_id, groups)

    @classmethod
    def validated_bindings(cls, values):
        """Compile and check before replacing any live input state."""
        bindings = []
        for value in values:
            binding = cls.compile(*value)
            if binding is None:
                continue
            for previous in bindings:
                if reason := binding_conflict(previous, binding):
                    raise ShortcutError(reason)
            bindings.append(binding)
        return bindings

    @classmethod
    def validate(cls, keys):
        cls.validated_bindings((key, '', '') for key in keys)

    @classmethod
    def prepare_profile(cls, global_keys, row_keys):
        """Keep global keys and quarantine every conflicting/unsupported row."""
        row_keys = tuple(row_keys)
        globals_ = cls.validated_bindings((key, action, '') for key, action in
                                        zip(global_keys, ('capture_selected', 'pause')))
        candidates, issues = {}, {}

        for index, key in enumerate(row_keys):
            try:
                binding = cls.compile(key, row=True)
                if binding is None:
                    continue
                issue = next((reason for other in globals_ if (reason := binding_conflict(binding, other))), '')
                if issue:
                    issues[index] = issue
                else:
                    candidates[index] = binding
            except ValueError as error:
                issues[index] = str(error)
        entries = list(candidates.items())
        for offset, (index, binding) in enumerate(entries):
            for other_index, other in entries[offset + 1:]:
                issue = binding_conflict(binding, other)
                if issue:
                    issues.setdefault(index, issue)
                    issues.setdefault(other_index, issue)
        return ProfileBindings(tuple(globals_),
                               tuple(None if i in issues else candidates.get(i) for i in range(len(row_keys))), issues)

    def configure_profile(self, prepared, row_ids, exclusive=False):
        """Install a prepared snapshot with the current profile's row identities."""
        row_ids = tuple(row_ids)
        if len(prepared.rows) != len(row_ids):
            raise ValueError('단축키와 프로필 행 수가 다릅니다.')
        bindings = list(reversed(prepared.globals))
        bindings.extend(replace(binding, action='move', row_id=rid)
                        for binding, rid in zip(prepared.rows, row_ids) if binding is not None)
        self._install(bindings, exclusive)

    def configure(self, values, exclusive=False):
        bindings = self.validated_bindings(values)
        self._install(bindings, exclusive)

    def _install(self, bindings, exclusive):
        with self.lock:
            self.bindings = bindings
            self.exclusive = exclusive
            self.fired.clear()

    def start(self):
        if self.unhook is None:
            from keyboard_hook import PhysicalHook
            hook = PhysicalHook(self.handle, lambda error: self._failed(hook, error))
            # Keep ownership before start() can raise or the hook can report failure.
            self.unhook = hook.close
            try:
                hook.start()
            except Exception:
                self.close()
                raise

    def _failed(self, hook, error):
        if self.unhook != hook.close:
            return
        self.close()
        self.emergency_stop()
        if self.on_error is not None:
            self.on_error(f'단축키 입력 감시 실패: {error}')

    def close(self):
        if self.unhook:
            self.unhook()
            self.unhook = None
        with self.lock:
            self.pressed.clear()
            self.fired.clear()
            self.suppressed.clear()

    def group_held(self, group):
        return any(group.matches(key) for key in self.pressed.values())

    def matches(self, binding):
        return (all(self.group_held(group) for group in binding.groups)
                and all(any(group.matches(key) for group in binding.groups)
                        for key in self.pressed.values() if key.scan_code in self.modifiers))

    def held(self, key):
        key = hotkey(key)
        with self.lock:
            return any(b.key == key and self.matches(b) for b in self.bindings)

    def handle(self, event):
        with self.lock:
            key = PressedKey(event.scan_code, getattr(event, 'virtual_key', None),
                             bool(getattr(event, 'is_extended', False)))
            code = key.identity
            if event.event_type == "up":
                self.pressed.pop(code, None)
                self.fired = {b.key for b in self.bindings
                              if b.key in self.fired and self.group_held(b.groups[-1])}
                blocked = code in self.suppressed
                self.suppressed.discard(code)
                return not blocked
            repeated = code in self.pressed
            if not repeated:
                self.pressed[code] = key
            for binding in self.bindings:
                # A held earlier shortcut must not consume a different key.
                if not binding.groups[-1].matches(key) or not self.matches(binding) or not self.eligible(binding.action):
                    continue
                if not repeated and binding.key not in self.fired:
                    self.fired.add(binding.key)
                    if binding.action == "pause":
                        self.emergency_stop()
                    self.dispatch(binding.action, binding.row_id)
                if self.exclusive:
                    self.suppressed.add(code)
                    return False
            return code not in self.suppressed
