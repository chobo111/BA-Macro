"""A small, validated macro language. Never evaluates Python or shell code."""
from dataclasses import dataclass, field
import json
import re

COORD_LIMIT = 99999
KEYS = {**{chr(c): c - 32 for c in range(ord('a'), ord('z') + 1)},
        **{str(i): 0x30 + i for i in range(10)}, **{f'f{i}': 0x6f + i for i in range(1, 25)},
        'space': 0x20, 'enter': 0x0d, 'tab': 0x09, 'esc': 0x1b, 'backspace': 0x08,
        'ctrl': 0x11, 'shift': 0x10, 'alt': 0x12, 'left': 0x25, 'up': 0x26,
        'right': 0x27, 'down': 0x28, 'home': 0x24, 'end': 0x23, 'pageup': 0x21,
        'pagedown': 0x22, 'insert': 0x2d, 'delete': 0x2e, 'capslock': 0x14,
        'minus': 0xbd, 'equal': 0xbb, 'comma': 0xbc, 'period': 0xbe, 'slash': 0xbf,
        'semicolon': 0xba, 'quote': 0xde, 'backslash': 0xdc, 'leftbracket': 0xdb,
        'rightbracket': 0xdd, 'grave': 0xc0}
ALIASES = {'escape': 'esc', 'return': 'enter', 'control': 'ctrl', 'spacebar': 'space',
           'page_up': 'pageup', 'page_down': 'pagedown'}
BUTTONS = {'left', 'right', 'middle', 'back', 'forward'}


class ScriptError(ValueError):
    def __init__(self, line, message):
        self.line = line
        super().__init__(f'{line}행: {message}')


@dataclass(frozen=True)
class Instruction:
    name: str
    args: tuple
    line: int
    body: tuple = ()


@dataclass(frozen=True)
class Program:
    pressed: tuple
    released: tuple


def strip_comment(line):
    quoted = False
    escape = False
    for i, c in enumerate(line):
        if escape:
            escape = False
        elif quoted and c == '\\':
            escape = True
        elif c == '"':
            quoted = not quoted
        elif not quoted and line[i:i+2] == '//':
            return line[:i].strip()
    return line.strip()


def number(text, line, minimum=0, maximum=600000):
    if not re.fullmatch(r'\d+', text) or not minimum <= int(text) <= maximum:
        raise ScriptError(line, f'{minimum}~{maximum} 범위의 정수가 필요합니다.')
    return int(text)


def points(text, line):
    text = re.sub(r'\s*,\s*', ',', text)
    result = []
    for token in text.split():
        if token == 'mouse':
            result.append('mouse')
        elif re.fullmatch(r'-?\d+,-?\d+', token):
            point = tuple(map(int, token.split(',')))
            if any(abs(v) > COORD_LIMIT for v in point):
                raise ScriptError(line, '좌표는 부호를 제외하고 최대 5자리입니다.')
            result.append(point)
        else:
            raise ScriptError(line, '좌표는 x,y 또는 mouse로 입력하세요.')
    if not result:
        raise ScriptError(line, '좌표를 입력하세요.')
    if len(result) > 256:
        raise ScriptError(line, '한 명령의 좌표는 256개 이하로 지정하세요.')
    return tuple(result)


def compile_script(source):
    if not isinstance(source, str) or len(source) > 100000:
        raise ScriptError(1, '스크립트는 100,000자 이하의 문자열이어야 합니다.')
    sections = [[], []]
    section = 0
    current = sections[0]
    stack = []
    for line_no, raw in enumerate(source.splitlines(), 1):
        text = strip_comment(raw)
        if not text:
            continue
        command, _, arg = text.partition(' ')
        # Tabs between the command and arguments are also accepted.
        parts = text.split(None, 1)
        command, arg = parts[0].lower(), parts[1].strip() if len(parts) > 1 else ''
        if command.startswith(('joystick_', 'controller_', 'gamepad_')):
            raise ScriptError(line_no, '컨트롤러 명령은 지원하지 않습니다.')
        if command in ('enter_staring', 'exit_staring'):
            raise ScriptError(line_no, 'MuMu 전용 조준 모드는 지원하지 않습니다.')
        if command == 'release_actions':
            if arg or stack or section:
                raise ScriptError(line_no, 'release_actions는 루프 밖에서 한 번만 사용하세요.')
            section, current = 1, sections[1]
            continue
        if command == 'loop':
            if len(stack) >= 8:
                raise ScriptError(line_no, '루프 중첩은 8단계까지 지원합니다.')
            if arg == 'until_release':
                raise ScriptError(line_no, 'loop until_release는 삭제된 문법입니다. loop만 입력하세요.')
            count = 'until_release' if not arg else number(arg, line_no, 1, 10000)
            if count == 'until_release' and section:
                raise ScriptError(line_no, '키를 뗀 뒤에는 loop에 반복 횟수를 지정하세요.')
            body = []
            stack.append((current, count, line_no))
            current = body
            continue
        if command == 'loop_end':
            if arg or not stack:
                raise ScriptError(line_no, '대응하는 loop가 필요합니다.')
            parent, count, start = stack.pop()
            if not current:
                raise ScriptError(start, '빈 루프는 실행할 수 없습니다.')
            parent.append(Instruction('loop', (count,), start, tuple(current)))
            current = parent
            continue
        if command in ('click', 'press', 'release', 'switch', 'curve', 'move'):
            args = points(arg.lower(), line_no)
            if command in ('press', 'release', 'switch', 'move') and len(args) != 1:
                raise ScriptError(line_no, 'Windows 마우스는 동시에 한 위치만 누를 수 있습니다.')
            if command == 'curve' and len(args) < 2:
                raise ScriptError(line_no, 'curve에는 최소 두 좌표가 필요합니다.')
        elif command in ('sleep', 'curve_first_time', 'curve_last_time'):
            args = ('untilcmd',) if command == 'curve_last_time' and arg == 'untilcmd' else (number(arg, line_no),)
        elif command in ('curve_release', 'release_all'):
            if arg:
                raise ScriptError(line_no, '이 명령에는 인수가 없습니다.')
            args = ()
        elif command in ('key_press', 'key_release', 'key_switch'):
            key = ALIASES.get(arg.lower(), arg.lower())
            if key not in KEYS:
                raise ScriptError(line_no, '지원하는 키 이름 하나를 입력하세요. 예: a, space, ctrl, f1')
            args = (key,)
        elif command in ('mouse_press', 'mouse_release', 'mouse_switch'):
            button = {'backward': 'back', 'x1': 'back', 'x2': 'forward'}.get(arg, arg)
            if button not in BUTTONS:
                raise ScriptError(line_no, 'left / right / middle / back / forward 중 하나를 입력하세요.')
            args = (button,)
        elif command == 'text':
            try:
                value = json.loads(arg)
                if not isinstance(value, str) or len(value) > 10000:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ScriptError(line_no, 'text 뒤에 큰따옴표로 감싼 문자열을 입력하세요(최대 10,000자).') from None
            args = (value,)
        else:
            raise ScriptError(line_no, f'알 수 없는 명령: {command}')
        current.append(Instruction(command, args, line_no))
    if stack:
        raise ScriptError(stack[-1][2], 'loop_end가 필요합니다.')
    if not any(sections):
        raise ScriptError(1, '실행할 명령을 작성하세요.')
    return Program(tuple(sections[0]), tuple(sections[1]))


class ScriptRuntime:
    """Owned only by the input worker. Toggle state survives subsequent triggers."""
    def __init__(self, native, guard, wait, held):
        self.native, self.guard, self.wait, self.held = native, guard, wait, held
        self.keys = set()
        self.buttons = set()
        self.latched_keys = set()
        self.latched_buttons = set()
        self.context = None

    def key(self, key, down):
        if down:
            self.keys.add(key)
        self.native.key(key, down)
        if not down:
            self.keys.discard(key)
            self.latched_keys.discard(key)

    def button(self, button, down):
        if down:
            self.buttons.add(button)
        if down and hasattr(self.native, "validate_pointer"):
            self.native.validate_pointer(self.context)
        self.native.mouse_button(button, down)
        if not down:
            self.buttons.discard(button)
            self.latched_buttons.discard(button)

    def release_all(self, transient=False):
        errors = []
        for key in list(self.keys - self.latched_keys if transient else self.keys):
            try:
                self.key(key, False)
            except Exception as error:
                errors.append(error)
        for button in list(self.buttons - self.latched_buttons if transient else self.buttons):
            try:
                self.button(button, False)
            except Exception as error:
                errors.append(error)
        if not transient:
            self.latched_keys.clear()
            self.latched_buttons.clear()
        if errors:
            raise errors[0]

    def resolve(self, point, command):
        x, y = self.mouse if point == 'mouse' else point
        l, t, r, b = self.native.bounds(command.hwnd)
        if not l <= x < r or not t <= y < b:
            raise ValueError('스크립트 좌표가 대상 영역 밖에 있습니다.')
        return self.native.screen(command.hwnd, x, y)

    def run(self, program, command):
        if self.context != command.hwnd:
            self.release_all()
        self.context = command.hwnd
        self.mouse = self.native.cursor(command.hwnd)
        self.first_time, self.last_time = 0, 0
        self.steps = 0
        try:
            self.execute(program.pressed, command)
            if program.released:
                while self.held(command.key):
                    self.wait(10, command)
                self.execute(program.released, command)
            self.release_all(transient=True)
        except BaseException:
            self.release_all()
            raise

    def execute(self, instructions, c):
        for instruction in instructions:
            self.guard(c)
            self.steps += 1
            name, args = instruction.name, instruction.args
            try:
                if name == 'loop':
                    count = args[0]
                    iterations = 0
                    while (self.held(c.key) if count == 'until_release' else iterations < count):
                        self.execute(instruction.body, c)
                        iterations += 1
                        self.wait(1, c)
                    continue
                if self.steps % 100 == 0:
                    self.wait(1, c)
                if name == 'sleep':
                    self.wait(args[0], c)
                elif name == 'move':
                    self.native.move(*self.resolve(args[0], c))
                elif name in ('click', 'press', 'release', 'switch'):
                    for point in args:
                        position = self.resolve(point, c)
                        self.guard(c)
                        self.native.move(*position)
                        if name == 'click':
                            self.button('left', True)
                            self.wait(c.click_duration, c)
                            self.button('left', False)
                        elif name == 'switch':
                            down = 'left' not in self.buttons
                            self.button('left', down)
                            if down:
                                self.latched_buttons.add('left')
                        else:
                            self.button('left', name == 'press')
                elif name.startswith('key_'):
                    key = args[0]
                    down = key not in self.keys if name == 'key_switch' else name == 'key_press'
                    self.key(key, down)
                    if name == 'key_switch' and down:
                        self.latched_keys.add(key)
                elif name.startswith('mouse_'):
                    button = args[0]
                    down = button not in self.buttons if name == 'mouse_switch' else name == 'mouse_press'
                    self.button(button, down)
                    if name == 'mouse_switch' and down:
                        self.latched_buttons.add(button)
                elif name == 'release_all':
                    self.release_all()
                elif name == 'text':
                    for char in args[0]:
                        self.guard(c)
                        self.native.text_char(char)
                elif name == 'curve_first_time':
                    self.first_time = args[0]
                elif name == 'curve_last_time':
                    self.last_time = args[0]
                elif name == 'curve_release':
                    self.button('left', False)
                elif name == 'curve':
                    # Resolve mouse before moving; it refers to the current aim point.
                    path = [self.resolve(point, c) for point in args]
                    self.native.move(*path[0])
                    self.button('left', True)
                    self.wait(self.first_time, c)
                    # Avoid many tiny waits for short drags; duration is per segment.
                    segments = max(1, min(10, c.curve_duration // 10))
                    for start, finish in zip(path, path[1:]):
                        for step in range(1, segments + 1):
                            self.guard(c)
                            self.native.move(*(round(a + (b-a)*step/segments) for a, b in zip(start, finish)))
                            self.wait(c.curve_duration / segments, c)
                    if self.last_time != 'untilcmd':
                        self.wait(self.last_time, c)
                        self.button('left', False)
            except ValueError as error:
                if isinstance(error, ScriptError):
                    raise
                raise ScriptError(instruction.line, str(error)) from error
