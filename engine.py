"""A single input worker, cancellable waits, and no Qt dependencies."""
from dataclasses import dataclass
import threading
from script_language import compile_script, ScriptRuntime, Program
from domain import WindowSizeUnavailable

WINDOW_SIZE_CHANGED = '앱 창 크기가 변경되었습니다. 해상도를 확인한 뒤 다시 시작하세요.'


class WindowSizeChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class Command:
    hwnd: int
    x: int
    y: int
    mode: str
    key: str
    move_to_click_delay_ms: int
    click_hold_ms: int
    script: str = ''
    drag_segment_duration_ms: int = 10
    program: Program | None = None
    cursor_return_delay_ms: int = 50
    return_cursor: bool = True
    expected_size: tuple[int, int] | None = None


class Cancelled(Exception):
    pass


class Engine:
    def __init__(self, native, held, report=lambda text: None):
        self.native, self.held, self.report = native, held, report
        self.condition = threading.Condition()
        self.cancel = threading.Event()
        self.pending = None
        self.busy = False
        self.closed = False
        self.reset_requested = False
        self.input_context = None
        self.runtime = ScriptRuntime(native.create_touch_input(), self.guard, self.wait, held)
        self.worker = threading.Thread(target=self._run, name="macro-input", daemon=True)
        self.worker.start()

    def submit(self, command):
        with self.condition:
            if self.closed:
                return False
            # Wake any in-flight wait; only the worker may clear cancellation,
            # after the old action has finished releasing its held inputs.
            self.cancel.set()
            if self.busy:
                self.reset_requested = True
            self.pending = command
            self.condition.notify()
            return True

    def stop(self):
        with self.condition:
            self.cancel.set()
            self.pending = None
            self.reset_requested = True
            self.condition.notify()

    def shutdown(self):
        with self.condition:
            self.closed = True
            self.cancel.set()
            self.pending = None
            self.condition.notify()
        self.worker.join(timeout=2)

    def check_size(self, command):
        if command.hwnd and command.expected_size is not None:
            if tuple(self.native.size(command.hwnd)) != command.expected_size:
                raise WindowSizeChanged(WINDOW_SIZE_CHANGED)

    def guard(self, command):
        if not command.hwnd:
            raise ValueError('실행할 앱을 선택하세요.')
        if hasattr(self.native, "validate_context"):
            self.native.validate_context(command.hwnd)
        if self.cancel.is_set() or (command.hwnd and not self.native.active(command.hwnd)):
            raise Cancelled()
        self.check_size(command)
        self.runtime.native.keep_alive()

    def wait(self, milliseconds, command):
        remaining = milliseconds / 1000
        self.guard(command)
        while remaining > 0:
            step = min(remaining, .01)
            if self.cancel.wait(step):
                raise Cancelled()
            remaining -= step
            self.guard(command)

    def click(self, command):
        self.guard(command)
        try:
            if hasattr(self.native, "validate_pointer"):
                self.native.validate_pointer(command.hwnd)
            self.native.button(True)
            self.wait(command.click_hold_ms, command)
        finally:
            self.native.button(False)

    def execute(self, c):
        self.guard(c)
        left, top, right, bottom = self.native.bounds(c.hwnd)
        if not left <= c.x < right or not top <= c.y < bottom:
            raise ValueError("저장 좌표가 대상 영역 밖에 있습니다. 좌표를 다시 캡처하세요.")
        self.guard(c)
        destination = self.native.screen(c.hwnd, c.x, c.y)
        if c.mode != "move_and_click":
            self.native.move(*destination)
            return
        origin = self.native.cursor_screen() if c.return_cursor else None
        try:
            self.native.move(*destination)
            self.wait(c.move_to_click_delay_ms, c)
            self.click(c)
            if c.return_cursor:
                self.wait(c.cursor_return_delay_ms, c)
        finally:
            # Restore physical screen coordinates even after cancellation or focus loss.
            # click() releases the button before this cleanup runs.
            if origin is not None:
                self.native.move(*origin)

    def close_script_input(self):
        self.runtime.native.close()
        self.runtime.buttons.clear()
        self.runtime.latched_buttons.clear()
        self.input_context = None

    def _run(self):
        while True:
            with self.condition:
                # Only held inputs need periodic focus-loss checks. New work,
                # stop and shutdown all notify this condition immediately.
                watch_focus = self.runtime.context and self.runtime.buttons
                self.condition.wait_for(lambda: self.closed or self.reset_requested or self.pending is not None,
                                        timeout=.05 if watch_focus else None)
                reset = self.reset_requested or self.closed
                self.reset_requested = False
                closing = self.closed
                c, self.pending = self.pending, None
                self.busy = c is not None or reset
                if c is not None and not closing:
                    self.cancel.clear()
            try:
                if reset or (self.runtime.context and not self.native.active(self.runtime.context)):
                    self.runtime.release_all()
                    self.close_script_input()
                if closing:
                    return
                if c is None:
                    if self.runtime.buttons and self.input_context is not None:
                        self.check_size(self.input_context)
                    self.runtime.native.keep_alive()
                    continue
                self.guard(c)
                if c.mode == 'script':
                    program = c.program if c.program is not None else compile_script(c.script)
                    self.check_size(c)
                    self.input_context = c
                    self.runtime.native.begin(c.hwnd)
                    self.runtime.run(program, c)
                    continue
                # A coordinate action must not inherit a script's latched inputs.
                self.runtime.release_all()
                self.execute(c)
            except Cancelled:
                pass
            except WindowSizeUnavailable:
                # Cancel the current contact without disarming the app on minimization.
                self.close_script_input()
            except Exception as error:
                if isinstance(error, WindowSizeChanged):
                    self.stop()
                self.close_script_input()
                self.report(str(error))
            finally:
                # Covers a switch arriving just after a script's last guard.
                if self.cancel.is_set():
                    try:
                        self.runtime.release_all()
                        self.close_script_input()
                    except Exception as error:
                        self.report(str(error))
                with self.condition:
                    self.busy = False
