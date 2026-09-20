"""Keyboard hook with conditional suppression and one dispatch per chord press."""
from dataclasses import dataclass
import threading
import keyboard
from domain import hotkey


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
        self.pressed = set()
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
        parsed = keyboard.parse_hotkey(normalized.replace("win", "windows"))
        if len(parsed) != 1:
            raise ValueError("연속 입력 단축키는 지원하지 않습니다.")
        return Binding(normalized, action, row_id, tuple(frozenset(group) for group in parsed[0]))

    def configure(self, values, exclusive=False):
        bindings = [self.compile(*value) for value in values if value[0]]
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

    def matches(self, binding):
        allowed = set().union(*binding.groups)
        return (all(group & self.pressed for group in binding.groups)
                and not ((self.pressed & self.modifiers) - allowed))

    def held(self, key):
        with self.lock:
            return any(b.key == key and self.matches(b) for b in self.bindings)

    def handle(self, event):
        with self.lock:
            code = event.scan_code
            if event.event_type == "up":
                self.pressed.discard(code)
                self.fired = {b.key for b in self.bindings if b.key in self.fired and (b.groups[-1] & self.pressed)}
                blocked = code in self.suppressed
                self.suppressed.discard(code)
                return not blocked
            repeated = code in self.pressed
            self.pressed.add(code)
            for binding in self.bindings:
                # A held earlier shortcut must not consume a different key.
                if code not in binding.groups[-1] or not self.matches(binding) or not self.eligible(binding.action):
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
