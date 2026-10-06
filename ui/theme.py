"""MUTHER-style sci-fi theme: dark CRT look, glowing accent, scanlines, boot text."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QFont, QLinearGradient
from PyQt6.QtWidgets import QWidget, QLabel, QApplication


def hex_with_alpha(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha:.2f})"


def build_stylesheet(accent: str, text_scale: float) -> str:
    base = int(11 * text_scale)
    bg = "#070a0f"
    panel = "#0c1119"
    panel2 = "#121a26"
    panel3 = "#18222f"
    border = hex_with_alpha(accent, 0.35)
    border_hot = hex_with_alpha(accent, 0.85)
    muted = "#6f8a86"
    sel = hex_with_alpha(accent, 0.22)
    hover = hex_with_alpha(accent, 0.14)
    radius = "5px"
    return f"""
    QWidget {{
        background-color: {bg};
        color: {accent};
        font-family: 'Consolas', 'Courier New', monospace;
        font-size: {base}px;
    }}
    QMainWindow, QDialog {{ background-color: {bg}; }}

    QMenuBar {{
        background-color: {panel};
        border-bottom: 1px solid {border};
        padding: 2px;
    }}
    QMenuBar::item {{ padding: 4px 10px; border-radius: {radius}; }}
    QMenuBar::item:selected {{ background: {sel}; }}
    QMenu {{
        background-color: {panel};
        border: 1px solid {border_hot};
        border-radius: {radius};
        padding: 4px;
    }}
    QMenu::item {{ padding: 5px 24px 5px 18px; border-radius: 3px; }}
    QMenu::item:selected {{ background: {sel}; }}
    QMenu::separator {{ height: 1px; background: {border}; margin: 4px 8px; }}

    QGroupBox {{
        border: 1px solid {border};
        border-radius: {radius};
        margin-top: 16px;
        padding-top: 10px;
        font-weight: bold;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px; padding: 0 6px;
        color: {accent};
        background: {bg};
    }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {{
        background: {panel2};
        border: 1px solid {border};
        border-radius: {radius};
        padding: 5px 7px;
        color: {accent};
        selection-background-color: {sel};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QTextEdit:focus {{
        border: 1px solid {border_hot};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox::down-arrow {{
        image: none; border-left: 4px solid transparent;
        border-right: 4px solid transparent; border-top: 5px solid {accent};
        margin-right: 6px;
    }}
    QComboBox QAbstractItemView {{
        background: {panel}; border: 1px solid {border_hot};
        selection-background-color: {sel};
    }}

    QPushButton {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                    stop:0 {panel3}, stop:1 {panel2});
        border: 1px solid {border};
        border-radius: {radius};
        padding: 6px 12px;
        color: {accent};
    }}
    QPushButton:hover {{
        background: {hover};
        border: 1px solid {border_hot};
    }}
    QPushButton:pressed {{
        background: {sel};
        padding-top: 7px; padding-bottom: 5px;
    }}
    QPushButton:focus {{ border: 1px solid {border_hot}; }}
    QPushButton:disabled {{ color: {muted}; border-color: {panel3}; }}
    QPushButton:default {{ border: 2px solid {border_hot}; }}

    QListWidget, QTreeWidget {{
        background: {panel};
        border: 1px solid {border};
        border-radius: {radius};
        padding: 3px;
        outline: 0;
    }}
    QListWidget::item {{
        padding: 4px 6px;
        border-radius: 3px;
        margin: 1px 2px;
    }}
    QListWidget::item:selected {{ background: {sel}; color: {accent}; }}
    QListWidget::item:hover {{ background: {hover}; }}

    QTabWidget::pane {{
        border: 1px solid {border};
        border-radius: {radius};
        top: -1px;
    }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        background: {panel};
        border: 1px solid {border};
        border-bottom: none;
        border-top-left-radius: {radius};
        border-top-right-radius: {radius};
        padding: 6px 14px;
        margin-right: 2px;
        color: {muted};
    }}
    QTabBar::tab:selected {{
        background: {panel3};
        color: {accent};
        border-bottom: 2px solid {accent};
    }}
    QTabBar::tab:hover:!selected {{ background: {hover}; color: {accent}; }}

    QSlider::groove:horizontal {{
        background: {panel2};
        height: 5px;
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {hex_with_alpha(accent, 0.55)};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {accent};
        width: 13px; height: 13px;
        margin: -5px 0;
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{ background: {accent}; border: 2px solid {border_hot}; }}

    QScrollBar:vertical {{
        background: {panel};
        width: 11px;
        border-radius: 5px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {border};
        border-radius: 5px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {border_hot}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar:horizontal {{
        background: {panel}; height: 11px; border-radius: 5px;
    }}
    QScrollBar::handle:horizontal {{
        background: {border}; border-radius: 5px; min-width: 28px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {border_hot}; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QCheckBox {{ spacing: 6px; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {border};
        border-radius: 3px;
        background: {panel2};
    }}
    QCheckBox::indicator:checked {{
        background: {accent};
        border: 1px solid {accent};
    }}
    QCheckBox::indicator:hover {{ border-color: {border_hot}; }}

    QLabel {{ color: {accent}; background: transparent; }}
    QStatusBar {{ background: {panel}; color: {muted}; border-top: 1px solid {border}; }}
    QStatusBar::item {{ border: none; }}
    QToolTip {{
        background: {panel3};
        color: {accent};
        border: 1px solid {border_hot};
        border-radius: 3px;
        padding: 4px 8px;
    }}
    QHeaderView::section {{
        background: {panel2}; color: {accent};
        border: 1px solid {border};
        padding: 3px 6px;
    }}
    QToolBar {{ background: {panel}; border-bottom: 1px solid {border}; spacing: 4px; padding: 3px; }}
    QProgressBar {{ background: {panel2}; border: 1px solid {border}; border-radius: {radius}; }}
    QProgressBar::chunk {{ background: {accent}; border-radius: {radius}; }}
    """


def apply_stylesheet(app: QApplication, accent: str, text_scale: float):
    app.setStyleSheet(build_stylesheet(accent, text_scale))


class ScanlineOverlay(QWidget):
    def __init__(self, parent=None, accent: str = "#9bff9b"):
        super().__init__(parent)
        self.accent = accent
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

    def set_accent(self, accent: str):
        self.accent = accent
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        c = QColor(self.accent)
        for y in range(0, self.height(), 3):
            p.setBrush(QColor(c.red(), c.green(), c.blue(), 22))
            p.drawRect(0, y, self.width(), 1)
        p.end()


class BootOverlay(QWidget):
    def __init__(self, parent=None, accent: str = "#9bff9b"):
        super().__init__(parent)
        self.accent = accent
        self.label = QLabel("MU-TH-UR 6000\n\n> INITIALIZING SHIP SYSTEMS…", self)
        self.label.setStyleSheet(f"color: {accent}; background: transparent;")
        self.label.setFont(QFont("Consolas", 20))
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: rgba(4,6,9,235);")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.fade)

    def show_boot(self, ms: int = 1600):
        if self.parent():
            self.resize(self.parent().size())
        self.raise_()
        self.show()
        self.timer.start(ms)

    def fade(self):
        self.timer.stop()
        self.hide()
