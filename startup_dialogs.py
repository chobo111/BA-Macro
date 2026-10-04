"""Explicit start decisions; input stays stopped while these dialogs are open."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QCheckBox


def app_label(identity):
    if not identity:
        return '전체화면 · 앱 미선택'
    title, exe = identity.get('title', ''), identity.get('exe', '')
    return f'{title} ({exe})' if title and exe else title or exe or identity.get('class', '알 수 없음')


class AppMismatchDialog(QMessageBox):
    def __init__(self, saved, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle('프로필 앱 확인')
        self.setIcon(QMessageBox.Icon.Warning)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setText('프로필에 지정된 앱과 현재 선택한 앱이 다릅니다.')
        self.setInformativeText(f'프로필 앱: {app_label(saved)}\n현재 선택: {app_label(current)}\n\n계속 시작하면 현재 선택을 프로필에 기억합니다.')
        self.skip_warning = QCheckBox('다음부터 알리지 않음')
        self.setCheckBox(self.skip_warning)
        self.start_button = self.addButton('이 선택으로 시작', QMessageBox.ButtonRole.AcceptRole)
        self.cancel_button = self.addButton('취소', QMessageBox.ButtonRole.RejectRole)
        self.setDefaultButton(self.cancel_button)
        self.setEscapeButton(self.cancel_button)

    def confirmed(self):
        return self.clickedButton() is self.start_button


class SizeMismatchDialog(QMessageBox):
    def __init__(self, saved, current, parent=None, *, can_resize=True):
        super().__init__(parent)
        self.setWindowTitle('프로필 해상도 확인')
        self.setIcon(QMessageBox.Icon.Warning)
        self.setText('프로필 기준 해상도와 현재 화면 크기가 다릅니다.')
        self.setInformativeText('프로필: %d × %d\n현재: %d × %d\n\n창 크기를 프로필에 맞추거나, 프로필 좌표를 현재 크기로 변환하세요.\n변경 후에는 다시 시작을 눌러 주세요.' % (*saved, *current))
        self.resize_button = self.addButton('창 크기 변경', QMessageBox.ButtonRole.ActionRole)
        self.resize_button.setEnabled(can_resize and all(1 <= v <= 16384 for v in saved))
        self.convert_button = self.addButton('프로필 좌표 변환', QMessageBox.ButtonRole.ActionRole)
        self.cancel_button = self.addButton('취소', QMessageBox.ButtonRole.RejectRole)
        self.setDefaultButton(self.cancel_button)
        self.setEscapeButton(self.cancel_button)

    def choice(self):
        clicked = self.clickedButton()
        if clicked is self.resize_button:
            return 'resize'
        if clicked is self.convert_button:
            return 'convert'
        return 'cancel'
