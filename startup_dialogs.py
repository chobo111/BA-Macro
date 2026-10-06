"""Explicit start decisions; input stays stopped while these dialogs are open."""
from coordinate_transform import aspect_warning
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
        self.can_auto = can_resize
        guidance = ('창 크기를 프로필에 맞추거나, 자동 변환으로 실행하세요.\n'
                    '자동 변환은 원본을 유지하며 대체 좌표를 메모리에서만 사용합니다.\n'
                    '창 크기 변경 후에는 다시 시작을 눌러 주세요.' if can_resize else
                    '프로필 좌표를 선택한 디스플레이 크기로 변환할 수 있습니다.\n'
                    '변환을 저장한 후 다시 시작을 눌러 주세요.')
        warning = aspect_warning(saved, current)
        if warning:
            guidance = warning + '\n\n' + guidance
        self.setInformativeText('기준 창 크기: %d × %d\n현재 크기: %d × %d\n\n%s' % (*saved, *current, guidance))
        self.resize_button = self.addButton('창 크기 변경', QMessageBox.ButtonRole.ActionRole)
        self.resize_button.setEnabled(can_resize and all(1 <= v <= 16384 for v in saved))
        self.convert_button = self.addButton('자동 변환으로 시작' if can_resize else '프로필 좌표 변환', QMessageBox.ButtonRole.ActionRole)
        self.skip_warning = None
        if can_resize:
            self.skip_warning = QCheckBox('다음부터 묻지 않기')
            self.setCheckBox(self.skip_warning)
        self.cancel_button = self.addButton('취소', QMessageBox.ButtonRole.RejectRole)
        self.setDefaultButton(self.cancel_button)
        self.setEscapeButton(self.cancel_button)

    def choice(self):
        clicked = self.clickedButton()
        if clicked is self.resize_button:
            return 'resize'
        if clicked is self.convert_button:
            return 'auto' if self.can_auto else 'convert'
        return 'cancel'


class CoordinateAppDialog(QMessageBox):
    def __init__(self, saved, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle('좌표를 저장할 앱 확인')
        self.setIcon(QMessageBox.Icon.Warning)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setText('현재 앱의 좌표를 프로필에 저장할까요?')
        self.setInformativeText(f'프로필 앱: {app_label(saved)}\n좌표를 가져온 앱: {app_label(current)}\n\n저장하면 프로필의 앱 지정도 함께 변경됩니다. 기존의 다른 좌표는 변환하지 않습니다.')
        self.save_button = self.addButton('앱 변경 후 저장', QMessageBox.ButtonRole.AcceptRole)
        self.cancel_button = self.addButton('취소', QMessageBox.ButtonRole.RejectRole)
        self.setDefaultButton(self.cancel_button)
        self.setEscapeButton(self.cancel_button)

    def confirmed(self):
        return self.clickedButton() is self.save_button
