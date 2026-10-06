"""Refresh a resized window through an actual Windows sizing loop.

No button/key presses are injected. The system owns the sizing operation;
only the cursor moves, and the original cursor/focus are restored afterwards.
"""
import ctypes as C
from ctypes import wintypes as W
import time
import native as win


class GuiThreadInfo(C.Structure):
    _fields_ = [('cbSize',W.DWORD),('flags',W.DWORD),
                ('hwndActive',W.HWND),('hwndFocus',W.HWND),('hwndCapture',W.HWND),
                ('hwndMenuOwner',W.HWND),('hwndMoveSize',W.HWND),('hwndCaret',W.HWND),
                ('rcCaret',W.RECT)]


get_gui_info = win.bind('GetGUIThreadInfo',[W.DWORD,C.POINTER(GuiThreadInfo)],W.BOOL)
key_state = win.bind('GetAsyncKeyState',[C.c_int],C.c_short)
monitor_from_point = win.bind('MonitorFromPoint',[W.POINT,W.DWORD],W.HANDLE)
is_zoomed = win.bind('IsZoomed',[W.HWND],W.BOOL)
is_hung = win.bind('IsHungAppWindow',[W.HWND],W.BOOL)
WM_SYSCOMMAND, SC_SIZE = 0x0112, 0xF000
WM_LBUTTONUP, WM_CANCELMODE = 0x0202, 0x001F


def frame(hwnd):
    rect = W.RECT()
    win.check(win.get_window_rect(hwnd,C.byref(rect)))
    return rect.left,rect.top,rect.right,rect.bottom


def sizing(hwnd,thread):
    info = GuiThreadInfo()
    info.cbSize = C.sizeof(info)
    win.check(get_gui_info(thread,C.byref(info)))
    return bool(info.flags & 2 and info.hwndMoveSize == hwnd)


def wait_for(predicate,timeout=0.5):
    deadline = time.monotonic()+timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise OSError('크기 조절 응답 시간이 초과되었습니다.')
        time.sleep(0.01)


def corner(rect):
    left,top,right,bottom = rect
    # Prefer the bottom right; fall back to a visible upper corner for tall windows.
    for edge,point,delta in ((8,(right-8,bottom-8),(-4,-4)),
                             (4,(left+8,top+8),(4,4)),
                             (5,(right-8,top+8),(-4,4)),
                             (7,(left+8,bottom-8),(4,-4))):
        destination = (point[0]+delta[0],point[1]+delta[1])
        if monitor_from_point(W.POINT(*point),0) and monitor_from_point(W.POINT(*destination),0):
            return edge,point,delta
    raise OSError('창 모서리가 화면 안에 보이도록 창 위치를 옮겨 주세요.')


def resize(window, hwnd, width, height):
    """Apply the client size, then refresh rendering through system sizing."""
    window.resize(hwnd, width, height)
    return refresh(hwnd)


def refresh(hwnd):
    """Return a diagnostic on failure; base resizing is already complete."""
    original_cursor = previous_focus = original_frame = None
    started = False
    thread = 0
    issue = ''
    try:
        if not win.valid_window(hwnd) or win.minimized(hwnd) or is_zoomed(hwnd) or is_hung(hwnd):
            raise OSError('일반 상태의 응답 중인 창에서만 자동 드래그할 수 있습니다.')
        if not win.get_long(hwnd,-16) & 0x00040000:  # WS_THICKFRAME
            raise OSError('테두리 크기 조절을 지원하지 않는 창입니다.')
        if any(key_state(key) & 0x8000 for key in (1,2,4,5,6)):
            raise OSError('마우스 버튼을 놓고 다시 적용해 주세요.')
        thread = win.get_pid(hwnd,None)
        if not thread or sizing(hwnd,thread):
            raise OSError('이미 창을 이동하거나 크기를 조절하고 있습니다.')
        original_frame = frame(hwnd)
        edge,anchor,delta = corner(original_frame)
        original_cursor,previous_focus = win.cursor_position(),win.foreground()
        win.check(win.set_foreground(hwnd))
        wait_for(lambda:win.foreground() == hwnd)
        win.check(win.set_cursor(*anchor))
        # SC_SIZE enters DefWindowProc's real modal sizing loop. It generates
        # its own WM_ENTERSIZEMOVE/WM_SIZING/WM_EXITSIZEMOVE notifications.
        packed = ((anchor[1]&0xffff)<<16)|(anchor[0]&0xffff)
        win.check(win.post_message(hwnd,WM_SYSCOMMAND,SC_SIZE|edge,packed))
        started = True
        wait_for(lambda:sizing(hwnd,thread))
        anchor = win.cursor_position()  # Windows may reposition the sizing cursor.
        def guarded():
            if win.foreground() != hwnd or not sizing(hwnd,thread):
                raise OSError('자동 드래그 중 포커스 또는 크기 조절 상태가 바뀌었습니다.')
        guarded()
        win.check(win.set_cursor(anchor[0]+delta[0],anchor[1]+delta[1]))
        wait_for(lambda:frame(hwnd) != original_frame)
        guarded()
        win.check(win.set_cursor(*anchor))
        wait_for(lambda:frame(hwnd) == original_frame)
        guarded()
        # Finish the system operation without synthesizing a click in the game.
        win.check(win.post_message(hwnd,WM_LBUTTONUP,0,0))
        wait_for(lambda:not sizing(hwnd,thread))
        started = False
        if frame(hwnd) != original_frame:
            raise OSError('자동 드래그 후 창 크기가 달라졌습니다.')
    except OSError as error:
        issue = str(error)
    finally:
        loop_closed = True
        if started:
            # Queue cancellation even if entering the loop timed out, so a late
            # SC_SIZE cannot leave the target stuck in the sizing loop.
            win.post_message(hwnd,WM_CANCELMODE,0,0)
            try:
                wait_for(lambda:not sizing(hwnd,thread),0.3)
            except OSError:
                loop_closed = False
                issue += ' / 크기 조절 종료를 확인하지 못했습니다. Esc를 눌러 주세요.'
        if issue and loop_closed and original_frame and win.valid_window(hwnd):
            try:
                if frame(hwnd) != original_frame:
                    left,top,right,bottom = original_frame
                    win.check(win.set_position(hwnd,None,left,top,right-left,bottom-top,0x0004|0x0010))
            except OSError:
                issue += ' / 창 크기를 복원하지 못했습니다.'
        if loop_closed and original_cursor is not None and not win.set_cursor(*original_cursor):
            issue += ' / 커서 위치를 복원하지 못했습니다.'
        if loop_closed and previous_focus and win.foreground() == hwnd and win.valid_window(previous_focus):
            win.set_foreground(previous_focus)
    return ('화면 갱신 자동 드래그: '+issue) if issue else ''
