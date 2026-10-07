"""Keyboard hook with conditional suppression and one dispatch per chord press."""
from dataclasses import dataclass
import threading
import keyboard
from shortcuts import hotkey, validate_hotkeys, ShortcutError


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

    def matches(self, key):
        return (key.scan_code in self.scan_codes
                and (not self.virtual_keys or key.virtual_key in self.virtual_keys))

    def overlaps(self, other):
        """Whether one physical event can satisfy both matchers."""
        return (bool(self.scan_codes & other.scan_codes)
                and (not self.virtual_keys or not other.virtual_keys
                     or bool(self.virtual_keys & other.virtual_keys)))

    @classmethod
    def compile(cls, name, scan_codes, modifiers=()):
        name = keyboard.normalize_name(name)
        if len(name) == 1 and name in '0123456789':
            virtual_keys = frozenset((ord(name), 0x60 + int(name)))
        elif name in KEYPAD_SYMBOLS:
            from keyboard_hook import character_key
            # Keypad symbols have independent virtual keys. Main-keyboard
            # symbols also require their layout-specific modifiers.
            keys = {KEYPAD_SYMBOLS[name]}
            mapped = character_key(name)
            if mapped and mapped[1].issubset(modifiers):
                keys.add(mapped[0])
            virtual_keys = frozenset(keys)
        elif name in LOGICAL_KEYS:
            virtual_keys = frozenset((LOGICAL_KEYS[name],))
        else:
            virtual_keys = frozenset()
        return cls(frozenset(scan_codes), virtual_keys)


@dataclass(frozen=True)
class Binding:
    key: str
    action: str
    row_id: str
    groups: tuple


class Hotkeys:
    def __init__(self, dispatch, eligible, emergency_stop):
        self.dispatch, self.eligible, self.emergency_stop = dispatch, eligible, emergency_stop
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
    def compile(key, action="", row_id=""):
        normalized = hotkey(key)
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
        values = tuple(values)
        validate_hotkeys(value[0] for value in values)
        bindings = []
        for value in values:
            binding = cls.compile(*value)
            if binding is None:
                continue
            for previous in bindings:
                # Canonical modifiers are ordered. Different modifier counts
                # cannot match together because runtime matching is exact.
                if (len(binding.groups) == len(previous.groups)
                        and all(first.overlaps(second)
                                for first, second in zip(binding.groups, previous.groups))):
                    raise ShortcutError(
                        f'실제 입력이 중복된 단축키: {previous.key.upper()} / {binding.key.upper()}')
            bindings.append(binding)
        return bindings

    @classmethod
    def validate(cls, keys):
        cls.validated_bindings((key, '', '') for key in keys)

    def configure(self, values, exclusive=False):
        bindings = self.validated_bindings(values)
        with self.lock:
            self.bindings = bindings
            self.exclusive = exclusive
            self.fired.clear()

    def start(self):
        if self.unhook is None:
            from keyboard_hook import PhysicalHook
            self.unhook = PhysicalHook(self.handle).start()

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
