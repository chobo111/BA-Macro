"""Windows elevation checks and a ready-before-exit restart handshake."""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import sys
import uuid

K = C.WinDLL('kernel32', use_last_error=True)
A = C.WinDLL('advapi32', use_last_error=True)
S = C.WinDLL('shell32', use_last_error=True)
U = C.WinDLL('user32', use_last_error=True)


def bind(dll, name, args, result):
    fn = getattr(dll, name)
    fn.argtypes, fn.restype = args, result
    return fn


open_process = bind(K, 'OpenProcess', [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
close_handle = bind(K, 'CloseHandle', [W.HANDLE], W.BOOL)
open_token = bind(A, 'OpenProcessToken', [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)], W.BOOL)
token_info = bind(A, 'GetTokenInformation', [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)], W.BOOL)
window_pid = bind(U, 'GetWindowThreadProcessId', [W.HWND, C.POINTER(W.DWORD)], W.DWORD)
create_event = bind(K, 'CreateEventW', [C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR], W.HANDLE)
open_event = bind(K, 'OpenEventW', [W.DWORD, W.BOOL, W.LPCWSTR], W.HANDLE)
set_event = bind(K, 'SetEvent', [W.HANDLE], W.BOOL)
wait = bind(K, 'WaitForSingleObject', [W.HANDLE, W.DWORD], W.DWORD)


def is_elevated(pid=None):
    process = open_process(0x1000, False, os.getpid() if pid is None else pid)
    if not process:
        raise C.WinError(C.get_last_error())
    token = W.HANDLE()
    try:
        if not open_token(process, 0x0008, C.byref(token)):  # TOKEN_QUERY
            raise C.WinError(C.get_last_error())
        value, length = W.DWORD(), W.DWORD()
        if not token_info(token, 20, C.byref(value), C.sizeof(value), C.byref(length)):
            raise C.WinError(C.get_last_error())
        return bool(value.value)
    finally:
        if token:
            close_handle(token)
        close_handle(process)


def requires_elevation(hwnd):
    if is_elevated():
        return False
    pid = W.DWORD()
    if not window_pid(hwnd, C.byref(pid)) or not pid.value:
        raise OSError('대상 앱의 실행 권한을 확인할 수 없습니다. 앱을 다시 선택하세요.')
    return is_elevated(pid.value)


class ShellExecuteInfo(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('fMask', W.ULONG), ('hwnd', W.HWND),
                ('lpVerb', W.LPCWSTR), ('lpFile', W.LPCWSTR), ('lpParameters', W.LPCWSTR),
                ('lpDirectory', W.LPCWSTR), ('nShow', C.c_int), ('hInstApp', W.HINSTANCE),
                ('lpIDList', C.c_void_p), ('lpClass', W.LPCWSTR), ('hkeyClass', W.HKEY),
                ('dwHotKey', W.DWORD), ('hIcon', W.HANDLE), ('hProcess', W.HANDLE)]


shell_execute = bind(S, 'ShellExecuteExW', [C.POINTER(ShellExecuteInfo)], W.BOOL)


def restart_command(directory, event_name):
    executable = Path(sys.executable).resolve()
    args = []
    if not getattr(sys, 'frozen', False):
        pythonw = executable.with_name('pythonw.exe')
        if pythonw.is_file():
            executable = pythonw
        args.append(str(Path(__file__).with_name('app.py')))
    args.extend(['--elevated-parent', str(os.getpid()), '--elevated-ready', event_name,
                 '--data-dir', str(Path(directory).resolve())])
    return str(executable), subprocess.list2cmdline(args)


class ElevatedLaunch:
    def __init__(self, directory, owner):
        self.event_name = 'Local\\BA-Macro-elevate-' + uuid.uuid4().hex
        self.event = create_event(None, True, False, self.event_name)
        self.commit_event = None
        self.process = None
        if not self.event:
            raise C.WinError(C.get_last_error())
        try:
            self.commit_event = create_event(None, True, False, self.event_name + '-commit')
            if not self.commit_event:
                raise C.WinError(C.get_last_error())
            executable, args = restart_command(directory, self.event_name)
            info = ShellExecuteInfo(cbSize=C.sizeof(ShellExecuteInfo), fMask=0x40 | 0x100 | 0x400,
                                    hwnd=owner, lpVerb='runas', lpFile=executable,
                                    lpParameters=args, lpDirectory=str(directory), nShow=1)
            if not shell_execute(C.byref(info)):
                raise C.WinError(C.get_last_error())
            self.process = info.hProcess
            if not self.process:
                raise OSError('관리자 앱의 실행 상태를 확인할 수 없습니다.')
        except BaseException:
            self.close()
            raise

    def ready(self):
        result = wait(self.event, 0)
        if result == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        return result == 0

    def finished(self):
        result = wait(self.process, 0)
        if result == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        return result == 0

    def commit(self):
        if not set_event(self.commit_event):
            raise C.WinError(C.get_last_error())

    def close(self):
        for name in ('event', 'commit_event', 'process'):
            handle = getattr(self, name, None)
            if handle:
                close_handle(handle)
                setattr(self, name, None)


def accept_handoff(parent_pid, event_name):
    """Run before taking the singleton lock; a failed handoff never starts the app."""
    if not is_elevated():
        raise OSError('관리자 권한으로 실행되지 않았습니다.')
    if not event_name.startswith('Local\\BA-Macro-elevate-') or parent_pid <= 0:
        raise ValueError('잘못된 재실행 요청입니다.')
    parent = open_process(0x100000, False, parent_pid)  # SYNCHRONIZE
    if not parent:
        raise C.WinError(C.get_last_error())
    event = commit = None
    try:
        event = open_event(0x0002, False, event_name)  # EVENT_MODIFY_STATE
        commit = open_event(0x100000, False, event_name + '-commit')
        if not event or not commit or not set_event(event):
            raise C.WinError(C.get_last_error())
        if wait(commit, 30000) != 0:
            raise OSError('관리자 재실행이 취소되었습니다.')
        # The old process must finish saving, release hooks and drop its lock.
        result = wait(parent, 30000)
        if result != 0:
            raise OSError('기존 앱이 종료되지 않아 관리자 재실행을 취소했습니다.')
    finally:
        if event:
            close_handle(event)
        if commit:
            close_handle(commit)
        close_handle(parent)


def await_ready(launch, parent, timeout_ms=15000):
    """Keep painting while the elevated child imports Qt and acknowledges readiness."""
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop(parent)
    poll, deadline = QTimer(), QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    error = []

    def check():
        try:
            if launch.ready() or launch.finished():
                loop.quit()
        except OSError as exc:
            error.append(exc)
            loop.quit()

    poll.timeout.connect(check)
    try:
        if launch.ready():
            return not launch.finished()
        poll.start(30)
        deadline.start(timeout_ms)
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        if error:
            raise error[0]
        return launch.ready() and not launch.finished()
    finally:
        poll.stop()
        deadline.stop()
        loop.deleteLater()
