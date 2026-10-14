"""Validate and save a converted profile without mutating the source."""
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox,
    QComboBox, QPushButton, QGroupBox, QDialogButtonBox)
from conversion_jobs import ConversionJobs
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


class BaselineResolutionDialog(QDialog):
    """Ask for the author's coordinate basis without guessing or scaling data."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('프로필 기준 해상도 지정')
        self.resize(360, 220)
        layout = QVBoxLayout(self)
        notice = QLabel('저장된 좌표의 기준 해상도가 없습니다.\n'
                        '좌표를 작성했을 당시의 앱 내부 크기를 입력하세요.\n'
                        '좌표는 그대로 유지됩니다. 저장한 뒤 다시 시작해 주세요.')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.resolution = ResolutionInput('좌표를 작성한 해상도', None, self.update_save_button)
        layout.addWidget(self.resolution)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText('저장')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('취소')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_save_button()

    def value(self):
        return self.resolution.value()

    def update_save_button(self):
        self.save_button.setEnabled(all(value > 0 for value in self.value()))

    def accept(self):
        if all(value > 0 for value in self.value()):
            super().accept()


class ConversionDialog(QDialog):
    def __init__(self, profile, path, destination, save_callback, parent=None, reason='', target_label='대상 창 내부'):
        super().__init__(parent)
        self.profile, self.path = profile, Path(path)
        self.save_callback = save_callback
        self.plan = None
        self.future = None
        self.pending_save = None
        self.jobs = jobs = ConversionJobs()
        # This fallback owns only plain Python worker state, never a deleted widget.
        self.destroyed.connect(lambda *_: jobs.close())
        self.finished.connect(self.finish_conversion)
        self.poll = QTimer(self)
        self.poll.setInterval(25)
        self.poll.timeout.connect(self.check_result)
        self.setWindowTitle('프로필 해상도 변환')
        self.resize(600, 320)
        layout = QVBoxLayout(self)
        title = QLabel(f'프로필: {self.path.name}')
        title.setToolTip(str(self.path))
        layout.addWidget(title)
        baseline = '%d × %d' % profile.resolution if profile.resolution else '알 수 없음'
        target_size = '%d × %d' % destination if destination else '크기 확인 불가'
        layout.addWidget(QLabel(f'프로필 기록: {baseline}  ·  현재 {target_label}: {target_size}'))
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

    def show_error(self, text):
        self.error.setText(text)
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, 'record_error'):
            parent.record_error('좌표 변환', text)

    def invalidate(self):
        self.jobs.cancel()
        self.future = None
        self.plan = None
        self.pending_save = None
        self.poll.stop()
        self.copy_button.setEnabled(False)
        self.apply_button.setEnabled(False)

    def schedule(self):
        if self.jobs.closed:
            return
        self.invalidate()
        self.timer.start()

    def refresh(self, *, save_mode=None):
        if self.jobs.closed:
            return
        self.timer.stop()
        self.invalidate()
        self.error.clear()
        self.notice.clear()
        self.summary.clear()
        try:
            source, destination = self.source.value(), self.destination.value()
            if 0 in (*source, *destination):
                raise ValueError('원본과 대상의 너비·높이를 모두 입력하세요. 원본은 제작 당시 해상도입니다.')
            self.future = self.jobs.submit(self.profile, source, destination)
            self.pending_save = save_mode
            self.summary.setText('좌표를 변환하고 있습니다…')
            self.poll.start()
        except Exception as error:
            self.show_error(str(error))

    def check_result(self):
        # Only the latest request is observed; cancelled work has no GUI callback.
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        save_mode, self.pending_save = self.pending_save, None
        self.poll.stop()
        try:
            self.plan = plan = future.result()
            self.summary.setText(f'표 {len(plan.profile.rows)}행 · 스크립트 좌표 {plan.script_points}개 · 변경 {plan.changed_points}개')
            self.notice.setText('\n'.join(plan.warnings))
            if plan.error_count:
                details = '\n'.join(plan.errors)
                omitted = plan.error_count - len(plan.errors)
                remaining = f'\n외 {omitted}개' if omitted else ''
                self.show_error(f'오류 {plan.error_count}개: 원본 좌표 또는 스크립트를 수정하세요.\n{details}{remaining}')
        except Exception as error:
            self.plan = None
            self.summary.clear()
            self.show_error(str(error))
        valid = self.plan is not None and not self.plan.error_count
        self.copy_button.setEnabled(valid)
        self.apply_button.setEnabled(valid)
        if valid and save_mode is not None:
            try:
                if self.save_callback(self.plan.profile, save_mode, self):
                    self.accept()
            except Exception as error:
                self.show_error(f'저장 실패: {error}')

    def save(self, mode):
        if self.pending_save is None:
            # Recheck a fresh snapshot before saving, including any pending edits.
            self.refresh(save_mode=mode)

    def finish_conversion(self, *_):
        self.timer.stop()
        self.poll.stop()
        self.future = None
        self.pending_save = None
        self.jobs.close()
