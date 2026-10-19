"""Wait for file workers while allowing paint/cancel events on the GUI thread."""
from PySide6.QtCore import Qt, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QProgressDialog
from widgets import managed_dialog


def wait_for_saves(futures, parent):
    if all(future.done() for future in futures):
        return True
    with managed_dialog(QProgressDialog('프로필을 저장하고 있습니다…', '취소', 0, 0, parent)) as dialog:
        dialog.setWindowTitle('프로필 전환')
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAutoClose(False)
        dialog.setMinimumDuration(250)
        widgets = [parent.centralWidget(), parent.main_menu]
        if parent.script_editor is not None:
            widgets.append(parent.script_editor)
        enabled = [(widget, widget.isEnabled()) for widget in widgets]
        loop = QEventLoop(dialog)
        timer = QTimer(dialog)
        timer.setInterval(15)
        show_timer = QTimer(dialog)
        show_timer.setSingleShot(True)
        show_timer.timeout.connect(dialog.show)
        def complete():
            if parent.closing or all(future.done() for future in futures):
                loop.quit()
        timer.timeout.connect(complete)
        dialog.canceled.connect(loop.quit)
        QApplication.instance().aboutToQuit.connect(loop.quit)
        try:
            for widget, _ in enabled:
                widget.setEnabled(False)
            timer.start()
            show_timer.start(250)
            loop.exec()
            return not parent.closing and not dialog.wasCanceled() and all(future.done() for future in futures)
        finally:
            timer.stop()
            show_timer.stop()
            dialog.hide()
            for widget, was_enabled in enabled:
                widget.setEnabled(was_enabled)


class _WriteProgress(QProgressDialog):
    """A submitted write cannot safely be abandoned or cancelled from the UI."""
    def reject(self):
        pass

    def closeEvent(self, event):
        event.ignore()


def wait_for_write(future, parent, *, protect_ui=True):
    """Finish a committed write without blocking Qt or allowing state changes."""
    if future.done():
        return future.result()
    owner = QApplication.activeModalWidget() or parent
    with managed_dialog(_WriteProgress('저장하고 있습니다…', '', 0, 0, owner)) as dialog:
        dialog.setWindowTitle('저장')
        dialog.setCancelButton(None)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        dialog.setAutoClose(False)
        dialog.setMinimumDuration(250)
        widgets = [parent.centralWidget(), parent.main_menu]
        if parent.script_editor is not None:
            widgets.append(parent.script_editor)
        if owner is not parent:
            widgets.append(owner)
        enabled = [(widget, widget.isEnabled()) for widget in widgets] if protect_ui else []
        previous = parent._waiting_for_write
        loop = QEventLoop(dialog)
        timer = QTimer(dialog)
        timer.setInterval(15)
        timer.timeout.connect(lambda: loop.quit() if future.done() else None)
        show_timer = QTimer(dialog)
        show_timer.setSingleShot(True)
        show_timer.timeout.connect(dialog.show)
        try:
            parent._waiting_for_write = True
            for widget, _ in enabled:
                widget.setEnabled(False)
            timer.start()
            if protect_ui:
                show_timer.start(250)
            # A flush may run while a delegate editor still owns focus. Keep
            # painting and completions alive without committing that edit.
            loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            return future.result()
        finally:
            timer.stop()
            show_timer.stop()
            dialog.hide()
            for widget, was_enabled in enabled:
                widget.setEnabled(was_enabled)
            parent._waiting_for_write = previous
