"""A single input worker, cancellable waits, and no Qt dependencies."""
from dataclasses import dataclass
import threading
from time import monotonic
from script_language import compile_script, ScriptRuntime, Program
from domain import WindowSizeUnavailable, WindowPositionChanged

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
    click_hold_ms: int
    script: str = ''
    drag_segment_duration_ms: int = 10
    program: Program | None = None
    expected_size: tuple[int, int] | None = None
    error_context: str = ''
    source_resolution: tuple[int, int] | None = None
    automatic: bool = False
    expected_origin: tuple[int, int] | None = None


class Cancelled(Exception):
    pass


class Engine:
    def __init__(self, native, held, report=lambda text: None, *, diagnostics=None):
        self.native, self.held, self.report = native, held, report
        self.diagnostics = diagnostics
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
        if command.expected_size is not None:
            if tuple(self.native.size(command.hwnd)) != command.expected_size:
                raise WindowSizeChanged(WINDOW_SIZE_CHANGED)

    def check_geometry(self, command):
        self.check_size(command)
        if command.expected_origin is not None:
            if tuple(self.native.screen(command.hwnd, 0, 0)) != command.expected_origin:
                raise WindowPositionChanged()

    def check_input(self, command):
        if not command.hwnd:
            raise ValueError('앱을 선택하세요.')
        if self.cancel.is_set() or not self.native.active(command.hwnd):
            raise Cancelled()
        self.check_geometry(command)

    def guard(self, command):
        self.check_input(command)
        self.runtime.native.keep_alive()

    def wait(self, milliseconds, command):
        deadline = monotonic() + milliseconds / 1000
        self.guard(command)
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return
            if self.cancel.wait(min(remaining, .01)):
                raise Cancelled()
            self.guard(command)

    def execute(self, c):
        self.guard(c)
        if c.mode != "move_only":
            raise ValueError('지원하지 않는 실행 모드입니다.')
        left, top, right, bottom = self.native.bounds(c.hwnd)
        if not left <= c.x < right or not top <= c.y < bottom:
            raise ValueError("저장 좌표가 대상 영역 밖에 있습니다. 좌표를 다시 캡처하세요.")
        self.guard(c)
        position = self.native.screen(c.hwnd, c.x, c.y)
        trace = getattr(self.runtime.native, 'diagnostic', None)
        frame = None
        if trace is not None and trace.sampling:
            trace.resolved(position, (c.x, c.y))
            frame = trace.sample(self.native, position, '커서 이동')
        try:
            # Coordinate resolution/diagnostic sampling can outlive a move.
            self.check_input(c)
            self.native.move(*position)
        except Exception as error:
            if frame is not None:
                trace.record(frame, f'실패: {error}')
            raise
        if frame is not None:
            trace.record(frame, '성공')

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
            trace = None
            outcome = '동작 종료'
            try:
                if reset or (self.runtime.context and not self.native.active(self.runtime.context)):
                    self.runtime.release_all()
                    self.close_script_input()
                if closing:
                    return
                if c is None:
                    if self.runtime.buttons and self.input_context is not None:
                        self.check_geometry(self.input_context)
                    self.runtime.native.keep_alive()
                    continue
                if self.diagnostics is not None:
                    trace = self.diagnostics.begin(c, self.native)
                self.runtime.native.diagnostic = trace
                if c.mode == 'script':
                    self.guard(c)
                    program = c.program if c.program is not None else compile_script(c.script)
                    self.check_geometry(c)
                    self.input_context = c
                    # Bind this command, including while a latched contact is idle.
                    # The injection check must not generate another heartbeat.
                    self.runtime.native.begin(c.hwnd, check_input=lambda command=c: self.check_input(command))
                    self.runtime.run(program, c)
                    continue
                # A coordinate action must not inherit a script's latched inputs.
                self.runtime.release_all()
                self.execute(c)
            except Cancelled:
                outcome = '정지 또는 포커스 이탈로 중단'
            except WindowSizeUnavailable:
                outcome = '앱 최소화로 중단'
                # Cancel the current contact without disarming the app on minimization.
                self.close_script_input()
            except Exception as error:
                outcome = f'실행 실패: {error}'
                if isinstance(error, (WindowSizeChanged, WindowPositionChanged)):
                    self.stop()
                context = c or self.input_context
                message = f'{context.error_context}\n{error}' if context and context.error_context else str(error)
                self.close_script_input()
                self.report(message)
            finally:
                if trace is not None:
                    trace.finish(outcome)
                self.runtime.native.diagnostic = None
                # Covers a switch arriving just after a script's last guard.
                if self.cancel.is_set():
                    try:
                        self.runtime.release_all()
                        self.close_script_input()
                    except Exception as error:
                        self.report(str(error))
                with self.condition:
                    self.busy = False
