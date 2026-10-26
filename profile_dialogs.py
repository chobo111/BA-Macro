"""Profile file naming and read-only details."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)


class RenameProfileDialog(QDialog):
    def __init__(self, name, parent=None, *, creating=False):
        super().__init__(parent)
        self.setWindowTitle('새 프로필 생성' if creating else '프로필 이름 변경')
        self.validate_name = None
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('프로필 이름' if creating else '새 이름'))
        self.name = QLineEdit(name)
        self.name.selectAll()
        layout.addWidget(self.name)
        self.validation = QLabel()
        self.validation.setTextFormat(Qt.TextFormat.PlainText)
        self.validation.setWordWrap(True)
        self.validation.setObjectName('scriptLog')
        self.validation.setProperty('error', True)
        self.validation.hide()
        layout.addWidget(self.validation)
        self.name.textChanged.connect(self.validation.hide)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('생성' if creating else '변경')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('취소')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name.setFocus()

    def value(self):
        return self.name.text().strip()

    def accept(self):
        try:
            if self.validate_name is not None:
                self.validate_name(self.value())
        except (ValueError, OSError) as error:
            self.validation.setText(str(error))
            self.validation.show()
            return
        super().accept()


class NewProfileDialog(RenameProfileDialog):
    def __init__(self, name, parent=None):
        super().__init__(name, parent, creating=True)


class DuplicateProfileDialog(NewProfileDialog):
    def __init__(self, name, parent=None):
        super().__init__(name, parent)
        self.setWindowTitle('프로필 복제')
        self.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).setText('복제')


class ProfileInfoDialog(QDialog):
    def __init__(self, details, parent=None):
        super().__init__(parent)
        self.setWindowTitle('현재 프로필 정보')
        self.resize(460, 360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.values = {}
        for title, text in details:
            value = QLabel(text)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(title, value)
            self.values[title] = value
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('닫기')
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
