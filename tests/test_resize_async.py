import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import threading
import unittest
from unittest.mock import Mock
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtTest import QTest
from widgets import ResizeDialog


class ResizeAsyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QApplication.instance() or QApplication([])

    def dialog(self,request,read=lambda:(1280,720)):
        dialog=ResizeDialog((1920,1080),request_resize=request,read_size=read)
        dialog.set_size((1280,720))
        def cleanup():
            dialog.wait_for_request()
            dialog.verify_timer.stop()
            dialog.set_pending(False)
            dialog.close()
            dialog.deleteLater()
            self.qt.processEvents()
        self.addCleanup(cleanup)
        return dialog

    def test_slow_resize_keeps_ui_responsive_and_prevents_duplicate_requests(self):
        entered,release=threading.Event(),threading.Event()
        workers=[]
        main=threading.get_ident()
        def request(size):
            workers.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return '화면 갱신 안내'
        dialog=self.dialog(request)
        self.addCleanup(release.set)
        dialog.show()
        ticks=[]
        timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(True))
        self.addCleanup(timer.stop)
        timer.start()
        dialog.apply()
        self.assertTrue(entered.wait(1))
        self.assertNotEqual(workers,[main])
        dialog.apply()
        dialog.reject()
        self.assertTrue(dialog.isVisible())
        QTest.qWait(80)
        self.assertTrue(ticks)
        self.assertTrue(dialog.pending)
        self.assertEqual(dialog.attempts,0)
        self.assertEqual(len(workers),1)
        release.set()
        dialog.wait_for_request()
        dialog.verify_timer.stop()
        dialog.verify_resize()
        self.assertTrue(dialog.pending)
        dialog.verify_resize()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.refresh_notice,'화면 갱신 안내')
        self.assertIsNone(dialog.resize_executor)

    def test_worker_failure_is_reported_and_retry_can_succeed(self):
        request=Mock(side_effect=OSError('앱이 닫혔습니다.'))
        dialog=self.dialog(request)
        dialog.apply()
        dialog.wait_for_request()
        dialog.verify_resize()
        self.assertFalse(dialog.pending)
        self.assertIn('앱이 닫혔습니다',dialog.message.text())
        self.assertIsNone(dialog.resize_executor)
        request.side_effect=None;request.return_value=''
        dialog.apply()
        dialog.wait_for_request()
        dialog.verify_resize();dialog.verify_resize()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
        self.assertEqual(request.call_count,2)

    def test_start_and_result_checks_stay_on_gui_thread(self):
        main=threading.get_ident();events=[]
        def request(size):
            events.append(('worker',threading.get_ident()))
        def read():
            events.append(('read',threading.get_ident()))
            return 1280,720
        dialog=self.dialog(request,read)
        dialog.resizing.connect(lambda:events.append(('stop',threading.get_ident())))
        dialog.apply()
        dialog.wait_for_request()
        dialog.verify_resize();dialog.verify_resize()
        self.assertEqual(events[0],('stop',main))
        self.assertNotEqual(events[1][1],main)
        self.assertEqual(events[2:],[('read',main),('read',main)])
