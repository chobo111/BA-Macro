"""Explicit start decisions; input stays stopped while these dialogs are open."""
from coordinate_transform import aspect_warning
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QMessageBox, QCheckBox, QDialog, QVBoxLayout, QLabel,
                               QRadioButton, QButtonGroup, QDialogButtonBox)


def app_label(identity):
    if not identity:
        return '지정된 앱 없음'
    title, exe = identity.get('title', ''), identity.get('exe', '')
    return f'{title} ({exe})' if title and exe else title or exe or identity.get('class', '알 수 없음')


class AppMismatchDialog(QDialog):
    def __init__(self, saved, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle('프로필 앱 확인')
        self.setMinimumWidth(410)
        layout = QVBoxLayout(self)
        for text in ('프로필에 지정된 앱과 현재 선택한 앱이 다릅니다.',
                     f'프로필 앱: {app_label(saved)}\n현재 선택: {app_label(current)}'):
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.options = {}
        self.group = QButtonGroup(self)
        for name, text in (
                ('new', '새 프로필 만들기 · 기본 Q/W/E 3행'),
                ('copy', '새 프로필 만들기 · 현재 내용 복사'),
                ('change', '현재 프로필의 지정 앱 변경')):
            option = QRadioButton(text)
            self.group.addButton(option)
            self.options[name] = option
            layout.addWidget(option)
        hint = QLabel('새 프로필은 별도 파일로 만들고 정지 상태로 엽니다.\n현재 프로필 변경은 좌표와 스크립트를 유지하며 시작을 계속합니다.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText('적용')
        self.apply_button.setEnabled(False)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setText('취소')
        self.cancel_button.setDefault(True)
        self.group.buttonToggled.connect(lambda *_: self.apply_button.setEnabled(self.group.checkedButton() is not None))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choice(self):
        if self.result() == QDialog.DialogCode.Accepted:
            return next((name for name, option in self.options.items() if option.isChecked()), 'cancel')
        return 'cancel'


class SizeMismatchDialog(QMessageBox):
    def __init__(self, saved, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle('프로필 해상도 확인')
        self.setIcon(QMessageBox.Icon.Warning)
        self.setText('프로필 기준 해상도와 현재 화면 크기가 다릅니다.')
        guidance = ('창 크기를 프로필에 맞추거나, 자동 변환으로 실행하세요.\n'
                    '자동 변환은 원본을 유지하며 대체 좌표를 메모리에서만 사용합니다.\n'
                    '창 크기 변경이 완료되면 바로 시작합니다.')
        warning = aspect_warning(saved, current)
        if warning:
            guidance = warning + '\n\n' + guidance
        self.setInformativeText('기준 창 크기: %d × %d\n현재 크기: %d × %d\n\n%s' % (*saved, *current, guidance))
        self.resize_button = self.addButton('창 크기 변경', QMessageBox.ButtonRole.ActionRole)
        self.resize_button.setEnabled(all(1 <= v <= 16384 for v in saved))
        self.convert_button = self.addButton('자동 변환으로 시작', QMessageBox.ButtonRole.ActionRole)
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
            return 'auto'
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


class ManualCoordinateDialog(QMessageBox):
    def __init__(self, saved, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle('좌표 직접 입력')
        self.setIcon(QMessageBox.Icon.Warning)
        self.setText('좌표를 직접 입력하려면 앱과 프로필의 기준 크기를 맞춰 주세요.')
        basis = '%d × %d' % saved if saved else '알 수 없음'
        self.setInformativeText(f'프로필 기준 크기: {basis}\n현재 앱 크기: {current[0]} × {current[1]}\n\n'
                                '창 크기를 변경하거나 프로필 좌표를 변환한 뒤 다시 입력하세요.\n'
                                '좌표 캡처는 기준 좌표로 역변환하므로 바로 사용할 수 있습니다.')
        self.resize_button = self.addButton('창 크기 변경', QMessageBox.ButtonRole.ActionRole)
        self.resize_button.setEnabled(bool(saved) and all(1 <= v <= 16384 for v in saved))
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
