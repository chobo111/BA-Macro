"""Opt-in, bounded coordinate traces. Never writes profiles or injects input."""
from datetime import datetime
from threading import RLock, Thread

from version import VERSION


def display(value):
    if value is None:
        return '확인 불가'
    if isinstance(value, (tuple, list)):
        return ','.join(map(str, value))
    return str(value)


def environment(hwnd):
    """Read on the diagnostic worker, only once per requested trace."""
    import ctypes as C
    from ctypes import wintypes as W
    import platform
    import elevation
    from native import bind, get_pid, window_dpi

    class MonitorInfo(C.Structure):
        _fields_ = [('cbSize', W.DWORD), ('monitor', W.RECT), ('work', W.RECT),
                    ('flags', W.DWORD), ('device', W.WCHAR * 32)]

    result = {'Windows': platform.platform()}
    pid = W.DWORD()
    get_pid(hwnd, C.byref(pid))
    result['pid'] = pid.value
    for label, query in (
        ('BA Macro 권한', lambda: elevation.is_elevated()),
        ('대상 앱 권한', lambda: elevation.is_elevated(pid.value)),
    ):
        try:
            result[label] = '관리자' if query() else '일반'
        except OSError:
            result[label] = '확인 불가'
    monitor_from_window = bind('MonitorFromWindow', [W.HWND, W.DWORD], W.HANDLE)
    monitor_info = bind('GetMonitorInfoW', [W.HANDLE, C.POINTER(MonitorInfo)], W.BOOL)
    info = MonitorInfo(cbSize=C.sizeof(MonitorInfo))
    if monitor_info(monitor_from_window(hwnd, 2), C.byref(info)):
        result['대상 모니터'] = info.device
        result['모니터 시작 위치'] = (info.monitor.left, info.monitor.top)
    result['대상 앱 DPI'] = window_dpi(hwnd) if window_dpi else None
    return result


def input_dpi_awareness():
    """This thread-local value must be captured on the input worker."""
    import ctypes as C
    from ctypes import wintypes as W
    from native import U, bind
    if hasattr(U, 'GetThreadDpiAwarenessContext'):
        context = bind('GetThreadDpiAwarenessContext', [], W.HANDLE)()
        awareness = bind('GetAwarenessFromDpiAwarenessContext', [W.HANDLE], C.c_int)(context)
        return {0: '미지원', 1: '시스템', 2: '모니터별'}.get(awareness, '확인 불가')
    return '확인 불가'


class CoordinateTrace:
    LIMIT = 20

    def __init__(self, command, native, screens):
        self.lock = RLock()
        self.command = command
        self.frames = []
        self.origins = {}
        self.origin = None
        self.state = '기록 중'
        self.revision = 0
        self.stopped = False
        self.created = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.context = {'앱 버전': VERSION, '디스플레이 배율': screens,
                        '단축키': command.key,
                        '프로필 기준 크기': command.source_resolution,
                        '실행 기준 크기': command.expected_size,
                        '자동 변환': '사용' if command.automatic else '사용 안 함',
                        '환경 정보': '조회 중'}
        try:
            self.process_id = native.window_process_id(command.hwnd)
            self.identity = native.window_identity(command.hwnd)
            if native.window_process_id(command.hwnd) != self.process_id:
                raise ValueError('진단한 앱이 변경되었습니다. 다시 진단하세요.')
            self.context['대상 앱'] = self.identity.get('title', '')
        except Exception as error:
            self.process_id = None
            self.identity = None
            self.context['앱 확인 오류'] = str(error)
        try:
            self.context['입력 스레드 DPI 인식'] = input_dpi_awareness()
        except Exception as error:
            self.context['입력 스레드 DPI 확인 오류'] = str(error)

    def set_environment(self, values):
        with self.lock:
            self.context.pop('환경 정보', None)
            self.context.update(values)
            self.revision += 1

    def cancel_environment(self):
        with self.lock:
            if self.context.get('환경 정보') == '조회 중':
                self.context['환경 정보'] = '조회 중단'
                self.revision += 1

    @property
    def sampling(self):
        with self.lock:
            return not self.stopped and len(self.frames) < self.LIMIT

    def resolved(self, screen, client):
        if self.sampling:
            origin = (screen[0] - client[0], screen[1] - client[1])
            with self.lock:
                self.origin = origin
                if len(self.origins) >= 256:
                    self.origins.clear()
                self.origins[screen] = origin

    def start_instruction(self):
        # Intermediate curve points may equal a screen point used earlier.
        # Keep endpoint lookups and interpolation origins local to this path.
        with self.lock:
            self.origins.clear()
            self.origin = None

    def sample(self, native, screen, kind):
        with self.lock:
            origin = self.origins.get(screen, self.origin)
        frame = {'kind': kind, 'screen': screen, 'origin': origin,
                 'client': tuple(a - b for a, b in zip(screen, origin)) if origin is not None else None}
        try:
            frame['before'] = native.screen(self.command.hwnd, 0, 0)
            frame['local'] = native.local_point(self.command.hwnd, *screen)
        except Exception as error:
            frame['measurement_error'] = str(error)
        return frame

    def record(self, frame, result):
        with self.lock:
            if not self.sampling:
                return
            self.frames.append({**frame, 'result': result})
            self.revision += 1
            if len(self.frames) == self.LIMIT:
                self.state = '20개 입력 기록 완료 · 이후 입력은 기록하지 않음'

    def finish(self, result='동작 종료'):
        with self.lock:
            if not self.stopped:
                self.stopped = True
                if len(self.frames) < self.LIMIT:
                    self.state = result
                self.revision += 1

    def text(self):
        with self.lock:
            lines = [f'좌표 진단 · {self.created}', self.state,
                     'Windows 입력 요청 성공은 게임의 처리 성공을 보장하지 않습니다.',
                     '게임이 실제 처리한 위치: 확인 불가',
                     '진단 중 추가 측정으로 입력 간격이 달라질 수 있습니다.', '']
            lines.extend(f'{key}: {display(value)}' for key, value in self.context.items() if key != 'pid')
            lines.append('동작: ' + ('스크립트' if self.command.mode == 'script' else '커서 이동'))
            for index, frame in enumerate(self.frames, 1):
                lines.extend(('', f'[{index}] {frame["kind"]} · Windows 요청: {frame["result"]}',
                              f'실행할 앱 내부 좌표: {display(frame["client"])}',
                              f'요청한 화면 좌표: {display(frame["screen"])}',
                              f'계산 시 앱 시작 위치: {display(frame["origin"])}',
                              f'전송 직전 앱 시작 위치: {display(frame.get("before"))}',
                              f'전송 좌표를 현재 앱 기준으로 환산: {display(frame.get("local"))}'))
                if frame.get('measurement_error'):
                    lines.append('측정 오류: ' + frame['measurement_error'])
                if frame.get('before') is not None and frame['origin'] is not None and frame['before'] != frame['origin']:
                    lines.append('주의: 계산 이후 앱 위치가 변경되었습니다.')
            return '\n'.join(lines)

    def cursor_point(self, native, target):
        with self.lock:
            frame = next((f for f in reversed(self.frames) if f['result'] == '성공' and f['client'] is not None), None)
        if frame is None:
            raise ValueError('확인할 입력 기록이 없습니다.')
        if target != self.command.hwnd or not native.exists(target):
            raise ValueError('진단한 앱을 다시 선택하세요.')
        if self.identity is None:
            raise ValueError('진단한 앱 정보를 확인하지 못했습니다. 다시 진단하세요.')
        current = native.window_identity(target)
        if (native.window_process_id(target) != self.process_id
                or current.get('exe', '').casefold() != self.identity.get('exe', '').casefold()
                or current.get('class', '') != self.identity.get('class', '')):
            raise ValueError('진단한 앱이 변경되었습니다. 다시 진단하세요.')
        if tuple(native.size(target)) != self.command.expected_size:
            raise ValueError('앱 크기가 변경되었습니다. 다시 진단하세요.')
        return native.screen(target, *frame['client'])


class CoordinateDiagnostics:
    def __init__(self, read_environment=environment):
        self.lock = RLock()
        self.read_environment = read_environment
        self.armed = False
        self.trace = None
        self.screens = ''
        self._generation = 0
        self._loading = False
        self._pending_environment = None
        self._environment_worker = None
        self._closed = False

    def arm(self, screens=''):
        with self.lock:
            if self._closed:
                return
            self.stop()
            self.trace = None
            self.screens = screens
            self.armed = True

    def stop(self):
        with self.lock:
            self._generation += 1
            self._loading = False
            self.armed = False
            self._pending_environment = None
            if self.trace is not None:
                self.trace.finish('기록 정지')
                self.trace.cancel_environment()

    def close(self):
        with self.lock:
            self._closed = True
            self.stop()

    def begin(self, command, native):
        with self.lock:
            if self._closed or not self.armed or self._loading:
                return None
            self._loading = True
            generation, screens = self._generation, self.screens
        trace = CoordinateTrace(command, native, screens)
        with self.lock:
            if generation != self._generation:
                trace.finish('기록 정지')
                return None
            self._loading = False
            self.armed = False
            self.trace = trace
            # One worker and one replaceable pending job bound resources even
            # if a system query stalls while the user repeatedly starts traces.
            self._pending_environment = (generation, trace, native)
            if self._environment_worker is None:
                worker = Thread(target=self._read_environments, name='coordinate-diagnostics', daemon=True)
                self._environment_worker = worker
                try:
                    worker.start()
                except Exception as error:
                    self._environment_worker = None
                    self._pending_environment = None
                    trace.set_environment({'환경 확인 오류': str(error)})
            return trace

    def _read_environments(self):
        while True:
            with self.lock:
                job, self._pending_environment = self._pending_environment, None
                if self._closed or job is None:
                    self._environment_worker = None
                    return
                generation, trace, native = job
            try:
                if trace.process_id is None or native.window_process_id(trace.command.hwnd) != trace.process_id:
                    raise ValueError('진단한 앱이 변경되거나 확인되지 않습니다.')
                values = dict(self.read_environment(trace.command.hwnd))
                if native.window_process_id(trace.command.hwnd) != trace.process_id:
                    raise ValueError('환경 조회 중 앱이 변경되었습니다. 다시 진단하세요.')
            except Exception as error:
                values = {'환경 확인 오류': str(error)}
            with self.lock:
                if not self._closed and generation == self._generation and self.trace is trace:
                    trace.set_environment(values)
