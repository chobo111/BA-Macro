"""Validate and save a converted profile without mutating the source."""
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
    QComboBox, QPushButton, QGroupBox)
from conversion import plan_conversion
from widgets import ResizeDialog


class ResolutionInput(QGroupBox):
    def __init__(self, title, size, changed, parent=None):
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        self.mode = QComboBox()
        self.mode.addItems(['직접 입력', '16:9', '4:3'])
        self.presets = QComboBox()
        self.presets.hide()
        layout.addWidget(self.mode)
        layout.addWidget(self.presets)
        fields = QHBoxLayout()
        self.spins = [QSpinBox(), QSpinBox()]
        for spin, value in zip(self.spins, size or (0, 0)):
            spin.setRange(0, 100000)
            spin.setSpecialValueText('미입력')
            spin.setValue(value)
            spin.valueChanged.connect(changed)
            fields.addWidget(spin)
        fields.insertWidget(1, QLabel('×'))
        layout.addLayout(fields)
        self.mode.currentTextChanged.connect(self.select_mode)
        self.presets.currentIndexChanged.connect(self.select_preset)

    def value(self):
        return tuple(spin.value() for spin in self.spins)

    def set_value(self, size):
        for spin, value in zip(self.spins, size):
            spin.setValue(value)

    def select_mode(self, mode):
        custom = mode == '직접 입력'
        self.presets.setVisible(not custom)
        for spin in self.spins:
            spin.setEnabled(custom)
        self.presets.clear()
        for size in ResizeDialog.PRESETS.get(mode, []):
            self.presets.addItem('%d × %d' % size, size)

    def select_preset(self, index):
        size = self.presets.itemData(index)
        if size:
            self.set_value(size)


class ConversionDialog(QDialog):
    def __init__(self, profile, path, destination, save_callback, parent=None, reason=''):
        super().__init__(parent)
        self.profile, self.path = profile, Path(path)
        self.save_callback = save_callback
        self.plan = None
        self.setWindowTitle('프로필 해상도 변환')
        self.resize(600, 320)
        layout = QVBoxLayout(self)
        title = QLabel(f'프로필: {self.path.name}')
        title.setToolTip(str(self.path))
        layout.addWidget(title)
        baseline = '%d × %d' % profile.resolution if profile.resolution else '알 수 없음'
        target_size = '%d × %d' % destination if destination else '대상 창 미선택'
        layout.addWidget(QLabel(f'프로필 기록: {baseline}  ·  현재 대상 창 내부: {target_size}'))
        if reason:
            notice = QLabel(reason)
            notice.setWordWrap(True)
            layout.addWidget(notice)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.refresh)
        sizes = QHBoxLayout()
        self.source = ResolutionInput('원본 · 좌표를 작성한 해상도' if profile.resolution else '원본 · 제작 당시 해상도 입력 필요', profile.resolution, self.schedule)
        self.destination = ResolutionInput('대상 · 변환할 해상도', destination, self.schedule)
        sizes.addWidget(self.source)
        sizes.addWidget(self.destination)
        layout.addLayout(sizes)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        note = QLabel('표와 보관된 스크립트의 숫자 좌표를 함께 변환합니다. mouse·시간·반복 횟수·주석은 유지됩니다.\n해상도는 게임 화면 내부 크기 기준입니다. 변환본 저장 후 해당 프로필로 전환합니다.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton('변환본으로 저장…')
        self.copy_button.setDefault(True)
        self.apply_button = QPushButton('현재 프로필에 적용')
        self.apply_button.setToolTip('현재 파일의 좌표와 해상도를 덮어씁니다.')
        cancel = QPushButton('취소')
        self.copy_button.clicked.connect(lambda: self.save('copy'))
        self.apply_button.clicked.connect(lambda: self.save('apply'))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.apply_button)
        buttons.addStretch()
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.refresh()

    def schedule(self):
        self.copy_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.timer.start()

    def refresh(self):
        self.plan = None
        self.error.clear()
        try:
            if 0 in (*self.source.value(), *self.destination.value()):
                raise ValueError('원본과 대상의 너비·높이를 모두 입력하세요. 원본은 제작 당시 해상도입니다.')
            self.plan = plan_conversion(self.profile, self.source.value(), self.destination.value())
            plan = self.plan
            changed = sum(before != after for _, before, after in plan.changes)
            self.summary.setText(f'표 {len(plan.profile.rows)}행 · 스크립트 좌표 {plan.script_points}개 · 변경 {changed}개')
            self.notice.setText('\n'.join(plan.warnings))
            if plan.errors:
                details = '\n'.join(plan.errors[:3])
                remaining = f'\n외 {len(plan.errors) - 3}개' if len(plan.errors) > 3 else ''
                self.error.setText(f'오류 {len(plan.errors)}개: 원본 좌표 또는 스크립트를 수정하세요.\n{details}{remaining}')
        except ValueError as error:
            self.summary.clear()
            self.notice.clear()
            self.error.setText(str(error))
        valid = self.plan is not None and not self.plan.errors
        self.copy_button.setEnabled(valid)
        self.apply_button.setEnabled(valid)

    def save(self, mode):
        self.refresh()
        if self.plan is None or self.plan.errors:
            return
        try:
            if self.save_callback(self.plan.profile, mode, self):
                self.accept()
        except Exception as error:
            self.error.setText(f'저장 실패: {error}')
