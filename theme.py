"""Shared semantic colors for main UI, embedded editor, dialogs, and icons."""
from dataclasses import dataclass
from PySide6.QtGui import QColor, QPalette


@dataclass(frozen=True)
class ThemeColors:
    background: str
    panel: str
    text: str
    muted: str
    border: str
    alternate: str
    accent: str
    selection: str
    selected_text: str
    disabled_background: str
    disabled_text: str
    disabled_border: str
    editor_background: str
    code_background: str
    command: str
    comment: str
    error_background: str
    error_text: str
    error_border: str
    status_stopped: str
    status_running: str


DARK = ThemeColors(
    background='#101923', panel='#192634', text='#e6eff8', muted='#91a6ba',
    border='#304456', alternate='#1d2d3e', accent='#47b8ed',
    selection='#285275', selected_text='#ffffff',
    disabled_background='#16212c', disabled_text='#718293', disabled_border='#263747',
    editor_background='#182634', code_background='#0e1822',
    command='#66cfff', comment='#8fa0b2',
    error_background='#352327', error_text='#ffb0a6', error_border='#a55450',
    status_stopped='#f27881', status_running='#63d68d',
)
LIGHT = ThemeColors(
    background='#f0f5fa', panel='#ffffff', text='#172b40', muted='#526d85',
    border='#cddbe7', alternate='#f5f8fc', accent='#176b99',
    selection='#d9ebf7', selected_text='#163e5c',
    disabled_background='#e9eef3', disabled_text='#8493a1', disabled_border='#dbe3ea',
    editor_background='#f0f5fa', code_background='#ffffff',
    command='#075f91', comment='#526d85',
    error_background='#fff0ee', error_text='#a12d24', error_border='#dba29c',
    status_stopped='#c63848', status_running='#21834a',
)


def palette(colors):
    result = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, colors.background),
        (QPalette.ColorRole.Base, colors.panel),
        (QPalette.ColorRole.AlternateBase, colors.alternate),
        (QPalette.ColorRole.Button, colors.panel),
        (QPalette.ColorRole.WindowText, colors.text),
        (QPalette.ColorRole.Text, colors.text),
        (QPalette.ColorRole.ButtonText, colors.text),
        (QPalette.ColorRole.PlaceholderText, colors.muted),
        (QPalette.ColorRole.Highlight, colors.selection),
        (QPalette.ColorRole.HighlightedText, colors.selected_text),
        (QPalette.ColorRole.Link, colors.accent),
        (QPalette.ColorRole.ToolTipBase, colors.panel),
        (QPalette.ColorRole.ToolTipText, colors.text),
    ):
        result.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors.disabled_text))
    for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors.disabled_background))
    return result


def stylesheet(c):
    return f"""
        QMainWindow, QDialog {{background: {c.background}; color: {c.text};}}
        QWidget {{font-family: 'Segoe UI', 'Malgun Gothic'; font-size: 13px; color: {c.text};}}
        QLabel {{background: transparent;}}
        QLabel#muted {{color: {c.muted};}}
        QLabel#targetStatus {{color: {c.muted};}}
        QLabel#targetStatus[warning="true"] {{color: {c.error_text};}}
        QFrame#toolbarSeparator {{background: {c.border}; border: none;}}
        QLabel#cursor {{color: {c.accent}; font-family: Consolas; font-weight: 600;}}
        QTabWidget::pane {{border: 1px solid {c.border}; border-radius: 4px;}}
        QTabBar::tab {{background: {c.panel}; color: {c.muted}; padding: 7px 18px; border: 1px solid {c.border};}}
        QTabBar::tab:selected {{color: {c.text}; border-bottom: 2px solid {c.accent};}}
        QGroupBox {{background: {c.panel}; border: 1px solid {c.border}; border-radius: 6px; margin-top: 8px; padding: 4px 2px 2px;}}
        QGroupBox::title {{subcontrol-origin: margin; left: 15px; padding: 0 5px; color: {c.muted};}}
        QPushButton {{background: {c.panel}; border: 1px solid {c.border}; border-radius: 5px; padding: 4px 7px;}}
        QPushButton:hover:enabled {{border-color: {c.accent}; background: {c.alternate};}}
        QPushButton:pressed:enabled {{background: {c.border};}}
        QPushButton:checked {{background: {c.selection}; color: {c.selected_text}; border-color: {c.accent};}}
        QPushButton#profileManage::menu-indicator {{image: none; width: 0px;}}
        QPushButton#profileSummary {{background: transparent; border: none; text-align: left; color: {c.muted}; padding: 0 3px;}}
        QPushButton#profileSummary:hover {{color: {c.accent};}}
        QPushButton#profileSummary:checked {{color: {c.text};}}
        RunButton {{padding-right: 25px; font-weight: 600;}}
        QTableWidget {{background: {c.panel}; alternate-background-color: {c.alternate}; border: 1px solid {c.border}; border-radius: 8px; selection-background-color: {c.selection}; selection-color: {c.selected_text};}}
        QTableWidget::item {{padding: 4px;}}
        QHeaderView {{background: {c.background};}}
        QHeaderView::section {{background: {c.background}; color: {c.muted}; padding: 6px 3px; border: none;}}
        QTableCornerButton::section {{background: {c.background}; border: none;}}
        QLineEdit, QSpinBox, QComboBox, QKeySequenceEdit {{background: {c.panel}; border: 1px solid {c.border}; border-radius: 4px; padding: 3px; selection-background-color: {c.selection}; selection-color: {c.selected_text};}}
        QComboBox QAbstractItemView {{background: {c.panel}; color: {c.text}; selection-background-color: {c.selection}; selection-color: {c.selected_text};}}
        QMenuBar, QMenu, QStatusBar {{background: {c.background}; color: {c.muted};}}
        QMenu::item:selected, QMenuBar::item:selected {{background: {c.selection}; color: {c.selected_text};}}
        QMenu#appMenu {{background: {c.panel}; color: {c.text}; border: 1px solid {c.border}; border-radius: 6px; padding: 4px;}}
        QMenu#appMenu::item {{padding: 6px 16px; border-radius: 3px;}}
        QMenu#appMenu::item:selected {{background: {c.selection}; color: {c.selected_text};}}
        QMenu#appMenu::separator {{height: 1px; background: {c.border}; margin: 4px 8px;}}
        QToolTip {{background: {c.panel}; color: {c.text}; border: 1px solid {c.border};}}
        QFrame#editorPanel {{background: {c.editor_background}; border: 1px solid {c.border}; border-radius: 0;}}
        QLabel#scriptLog {{background: {c.code_background}; color: {c.muted}; border: 1px solid {c.border}; border-radius: 4px; padding: 6px 8px;}}
        QLabel#scriptLog[error="true"] {{background: {c.error_background}; color: {c.error_text}; border-color: {c.error_border};}}
        QPlainTextEdit#codeEditor, QPlainTextEdit#errorLog, QTextBrowser#scriptHelp {{background: {c.code_background}; color: {c.text}; border: 1px solid {c.border}; padding: 10px; selection-background-color: {c.selection}; selection-color: {c.selected_text};}}
        QPlainTextEdit#codeEditor {{font-family: Consolas, 'Malgun Gothic'; font-size: 15px;}}
        QDialog#scriptOverlay QPushButton, QDialog#scriptOverlay QComboBox {{padding: 7px 12px; border-radius: 5px;}}
        QDialog#scriptOverlay QPushButton#scriptHelpButton {{padding: 0; border-radius: 16px; font-weight: 700;}}
        QWidget:disabled, QLabel:disabled, QMenu::item:disabled {{color: {c.disabled_text};}}
        QPushButton:disabled, QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QKeySequenceEdit:disabled {{background: {c.disabled_background}; color: {c.disabled_text}; border-color: {c.disabled_border};}}
    """
