from contextlib import ExitStack
from types import SimpleNamespace
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
        self.border=(16,39)
        self.window=SimpleNamespace(size=lambda h:(self.rect[2]-self.rect[0]-self.border[0],
                                                 self.rect[3]-self.rect[1]-self.border[1]))
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
        self.stack.enter_context(patch.object(r.win,'cursor_position',side_effect=lambda:self.point))
        self.stack.enter_context(patch.object(r,'sizing',side_effect=lambda h,t:self.active))
        self.stack.enter_context(patch.object(r.win,'foreground',side_effect=lambda:self.focus))
        self.foreground=self.stack.enter_context(patch.object(r.win,'set_foreground',side_effect=self.set_focus))
        self.cursor=self.stack.enter_context(patch.object(r.win,'set_cursor',side_effect=self.set_cursor))
        self.post=self.stack.enter_context(patch.object(r.win,'post_message',side_effect=self.post_message))
        self.restore=self.stack.enter_context(patch.object(r.win,'set_position',side_effect=self.restore_frame))
        self.stack.enter_context(patch.object(r.time,'sleep'))
        self.stack.enter_context(patch.object(r.time,'monotonic',side_effect=tick))

    def refresh(self):
        return r.refresh(self.window,123,(784,461))

    def restore_frame(self,hwnd,after,left,top,width,height,flags):
        self.rect=(left,top,left+width,top+height)
        return 1

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
        self.assertEqual(self.refresh(),'')
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
        self.assertIn('마우스 버튼',self.refresh())
        self.post.assert_not_called()
        self.cursor.assert_not_called()

    def test_offscreen_corners_do_not_move_cursor(self):
        self.monitors.return_value=0
        self.assertIn('모서리',self.refresh())
        self.cursor.assert_not_called()

    def test_enter_timeout_queues_cancel_and_restores_cursor(self):
        self.post.side_effect=lambda *a:self.posts.append(a) or 1
        self.assertIn('초과',self.refresh())
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
        self.assertIn('포커스',self.refresh())
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
        notice=self.refresh()
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
        self.assertIn('자동 드래그',self.refresh())
        self.assertFalse(self.active)
        self.assertEqual(self.point,(50,50))

    def test_visible_top_corner_is_available_for_offscreen_bottom(self):
        self.monitors.side_effect=lambda p,f:p.y<500
        edge,point,delta=r.corner((100,100,900,1100))
        self.assertEqual((edge,point,delta),(4,(108,108),(4,4)))

    def test_position_change_after_finish_does_not_warn_or_restore(self):
        def post(*args):
            result=self.post_message(*args)
            if args[1]==r.WM_LBUTTONUP:
                left,top,right,bottom=self.rect
                self.rect=(left+1,top+2,right+1,bottom+2)
            return result
        self.post.side_effect=post
        self.assertEqual(self.refresh(),'')
        self.assertEqual(self.window.size(123),(784,461))
        self.restore.assert_not_called()

    def test_border_change_with_same_client_size_is_accepted(self):
        def post(*args):
            result=self.post_message(*args)
            if args[1]==r.WM_LBUTTONUP:
                left,top,right,bottom=self.rect
                self.rect=(left,top,right+2,bottom+2)
                self.border=(18,41)
            return result
        self.post.side_effect=post
        self.assertEqual(self.refresh(),'')
        self.restore.assert_not_called()

    def test_delayed_client_size_settles_without_false_warning(self):
        original_size=self.window.size
        remaining=[]
        def post(*args):
            result=self.post_message(*args)
            if args[1]==r.WM_LBUTTONUP:
                remaining.extend([(783,461),(784,461),(783,461)])
            return result
        self.window.size=lambda h:remaining.pop(0) if remaining else original_size(h)
        self.post.side_effect=post
        self.assertEqual(self.refresh(),'')
        self.restore.assert_not_called()

    def test_real_size_difference_is_restored_and_reported_as_restored(self):
        def post(*args):
            result=self.post_message(*args)
            if args[1]==r.WM_LBUTTONUP:
                left,top,right,bottom=self.rect
                self.rect=(left,top,right-1,bottom)
            return result
        self.post.side_effect=post
        notice=self.refresh()
        self.assertIn('784 × 461로 복원했습니다',notice)
        self.assertNotIn('크기가 다릅니다',notice)
        self.assertEqual(self.window.size(123),(784,461))
        self.restore.assert_called_once()

    def test_unrestored_real_difference_keeps_requested_and_actual_sizes(self):
        def post(*args):
            result=self.post_message(*args)
            if args[1]==r.WM_LBUTTONUP:
                left,top,right,bottom=self.rect
                self.rect=(left,top,right-1,bottom)
            return result
        self.post.side_effect=post
        self.restore.side_effect=None
        self.restore.return_value=0
        notice=self.refresh()
        self.assertIn('요청 784 × 461 / 실제 783 × 461',notice)
        self.assertIn('복원하지 못했습니다',notice)
