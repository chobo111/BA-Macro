from contextlib import ExitStack
import unittest
from unittest.mock import patch
import interactive_resize as r


class InteractiveResizeTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.point=(50,50)
        self.rect=(100,100,900,600)
        self.original_rect=self.rect
        self.focus=22
        self.active=False
        self.anchor=None
        self.posts=[]
        self.moves=[]
        self.clock=0
        def tick():
            self.clock+=0.05
            return self.clock
        for name,value in (('valid_window',1),('minimized',0),('get_long',0x00040000),('get_pid',12)):
            self.stack.enter_context(patch.object(r.win,name,return_value=value))
        self.stack.enter_context(patch.object(r,'is_zoomed',return_value=0))
        self.stack.enter_context(patch.object(r,'is_hung',return_value=0))
        self.buttons=self.stack.enter_context(patch.object(r,'key_state',return_value=0))
        self.monitors=self.stack.enter_context(patch.object(r,'monitor_from_point',return_value=1))
        self.stack.enter_context(patch.object(r,'frame',side_effect=lambda h:self.rect))
        self.stack.enter_context(patch.object(r,'cursor',side_effect=lambda:self.point))
        self.stack.enter_context(patch.object(r,'sizing',side_effect=lambda h,t:self.active))
        self.stack.enter_context(patch.object(r.win,'foreground',side_effect=lambda:self.focus))
        self.foreground=self.stack.enter_context(patch.object(r.win,'set_foreground',side_effect=self.set_focus))
        self.cursor=self.stack.enter_context(patch.object(r.win,'set_cursor',side_effect=self.set_cursor))
        self.post=self.stack.enter_context(patch.object(r.win,'post_message',side_effect=self.post_message))
        self.restore=self.stack.enter_context(patch.object(r.win,'set_position',return_value=1))
        self.stack.enter_context(patch.object(r.time,'sleep'))
        self.stack.enter_context(patch.object(r.time,'monotonic',side_effect=tick))

    def set_focus(self,hwnd):
        self.focus=hwnd
        return 1

    def set_cursor(self,x,y):
        self.moves.append((x,y))
        self.point=(x,y)
        if self.active:
            left,top,right,bottom=self.original_rect
            self.rect=(left,top,right+x-self.anchor[0],bottom+y-self.anchor[1])
        return 1

    def post_message(self,hwnd,msg,wparam,lparam):
        self.posts.append((hwnd,msg,wparam,lparam))
        if msg==r.WM_SYSCOMMAND:
            self.active=True
            self.anchor=self.point
        elif msg in (r.WM_LBUTTONUP,r.WM_CANCELMODE):
            self.active=False
        return 1

    def test_real_loop_nudge_return_and_cursor_focus_restoration(self):
        self.assertEqual(r.refresh(123),'')
        self.assertEqual([p[1] for p in self.posts],[r.WM_SYSCOMMAND,r.WM_LBUTTONUP])
        self.assertEqual(self.posts[0][2],r.SC_SIZE|8)
        self.assertIn((888,588),self.moves)
        self.assertEqual(self.rect,self.original_rect)
        self.assertEqual(self.point,(50,50))
        self.assertEqual(self.focus,22)
        self.assertFalse(self.active)
        self.restore.assert_not_called()

    def test_mouse_button_held_skips_automatic_drag(self):
        self.buttons.return_value=0x8000
        self.assertIn('마우스 버튼',r.refresh(123))
        self.post.assert_not_called()
        self.cursor.assert_not_called()

    def test_offscreen_corners_do_not_move_cursor(self):
        self.monitors.return_value=0
        self.assertIn('모서리',r.refresh(123))
        self.cursor.assert_not_called()

    def test_enter_timeout_queues_cancel_and_restores_cursor(self):
        self.post.side_effect=lambda *a:self.posts.append(a) or 1
        self.assertIn('초과',r.refresh(123))
        self.assertEqual([p[1] for p in self.posts],[r.WM_SYSCOMMAND,r.WM_CANCELMODE])
        self.assertEqual(self.point,(50,50))
        self.assertEqual(self.focus,22)

    def test_focus_change_cancels_without_stealing_focus_back(self):
        def move(x,y):
            result=self.set_cursor(x,y)
            if self.active:
                self.focus=77
            return result
        self.cursor.side_effect=move
        self.assertIn('포커스',r.refresh(123))
        self.assertEqual(self.focus,77)
        self.assertFalse(self.active)
        self.assertEqual(self.point,(50,50))
        self.restore.assert_called_once_with(123,None,100,100,800,500,0x14)

    def test_cannot_exit_loop_does_not_warp_cursor_across_screen(self):
        def post(*args):
            if args[1]==r.WM_SYSCOMMAND:
                return self.post_message(*args)
            return 1
        self.post.side_effect=post
        notice=r.refresh(123)
        self.assertIn('Esc',notice)
        self.assertNotEqual(self.point,(50,50))
        self.assertEqual(self.focus,123)
        self.restore.assert_not_called()

    def test_cursor_move_failure_cancels_operation(self):
        def move(x,y):
            if self.active:
                return 0
            return self.set_cursor(x,y)
        self.cursor.side_effect=move
        self.assertIn('자동 드래그',r.refresh(123))
        self.assertFalse(self.active)
        self.assertEqual(self.point,(50,50))

    def test_visible_top_corner_is_available_for_offscreen_bottom(self):
        self.monitors.side_effect=lambda p,f:p.y<500
        edge,point,delta=r.corner((100,100,900,1100))
        self.assertEqual((edge,point,delta),(4,(108,108),(4,4)))
