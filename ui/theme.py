"""Neutral and Alien-themed application palettes, plus Alien-only effects."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QFont
from PyQt6.QtWidgets import QWidget, QLabel, QApplication

THEME_MODES = ("dark", "light", "alien")
DEFAULT_ACCENT = "#9bff9b"


def hex_with_alpha(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha:.2f})"


def theme_colors(mode: str, accent: str = DEFAULT_ACCENT) -> dict[str, str]:
    """Return the palette used by both the app stylesheet and custom panels."""
    mode = mode if mode in THEME_MODES else "dark"
    if mode == "light":
        return {
            "bg": "#edf1f5", "panel": "#f8fafc", "panel2": "#ffffff",
            "panel3": "#e5ebf2", "text": "#263342", "accent": "#2563a6",
            "muted": "#667587", "border": "#c3ceda",
            "border_hot": "#7296bd", "selection": "#d5e5f5",
            "hover": "#e4edf7", "disabled": "#8c99a7",
        }
    if mode == "alien":
        return {
            "bg": "#070a0f", "panel": "#0c1119", "panel2": "#121a26",
            "panel3": "#18222f", "text": accent, "accent": accent,
            "muted": "#6f8a86", "border": hex_with_alpha(accent, 0.35),
            "border_hot": hex_with_alpha(accent, 0.85),
            "selection": hex_with_alpha(accent, 0.22),
            "hover": hex_with_alpha(accent, 0.14), "disabled": "#50635f",
        }
    return {
        "bg": "#151a21", "panel": "#1b222b", "panel2": "#242d38",
        "panel3": "#2d3946", "text": "#dce4ed", "accent": "#69b7f5",
        "muted": "#9aa9b8", "border": "#3d4d5e",
        "border_hot": "#69b7f5", "selection": "#30475b",
        "hover": "#293848", "disabled": "#778594",
    }


def build_stylesheet(accent: str = DEFAULT_ACCENT, text_scale: float = 1.0,
                     mode: str = "alien") -> str:
    """Build the application-wide Qt stylesheet for dark, light, or Alien mode.

    The default mode stays Alien for compatibility with older direct callers;
    the application explicitly starts in neutral dark mode.
    """
    c = theme_colors(mode, accent)
    base = max(8, int(11 * text_scale))
    radius = "5px"
    return f"""
    QWidget {{
        background-color: {c['bg']};
        color: {c['text']};
        font-family: 'Consolas', 'Courier New', monospace;
        font-size: {base}px;
    }}
    QMainWindow, QDialog {{ background-color: {c['bg']}; }}
    QFrame#MenuCard {{
        background-color: {c['panel']};
        border: 1px solid {c['border_hot']};
        border-radius: 10px;
    }}
    QLabel#AssetPreviewPopout {{
        background-color: {c['panel']};
        border: 1px solid {c['border_hot']};
        padding: 4px;
    }}

    QMenuBar {{
        background-color: {c['panel']};
        border-bottom: 1px solid {c['border']};
        padding: 2px;
    }}
    QMenuBar::item {{ padding: 4px 10px; border-radius: {radius}; }}
    QMenuBar::item:selected {{ background: {c['selection']}; }}
    QMenu {{
        background-color: {c['panel']};
        border: 1px solid {c['border_hot']};
        border-radius: {radius};
        padding: 4px;
    }}
    QMenu::item {{ padding: 5px 24px 5px 18px; border-radius: 3px; }}
    QMenu::item:selected {{ background: {c['selection']}; }}
    QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}

    QGroupBox {{
        border: 1px solid {c['border']};
        border-radius: {radius};
        margin-top: 16px;
        padding-top: 10px;
        font-weight: bold;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px; padding: 0 6px;
        color: {c['accent']};
        background: {c['bg']};
    }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {{
        background: {c['panel2']};
        border: 1px solid {c['border']};
        border-radius: {radius};
        padding: 5px 7px;
        color: {c['text']};
        selection-background-color: {c['selection']};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QTextEdit:focus {{
        border: 1px solid {c['border_hot']};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox::down-arrow {{
        image: none; border-left: 4px solid transparent;
        border-right: 4px solid transparent; border-top: 5px solid {c['accent']};
        margin-right: 6px;
    }}
    QComboBox QAbstractItemView {{
        background: {c['panel']}; border: 1px solid {c['border_hot']};
        selection-background-color: {c['selection']};
    }}

    QPushButton {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                    stop:0 {c['panel3']}, stop:1 {c['panel2']});
        border: 1px solid {c['border']};
        border-radius: {radius};
        padding: 4px 8px;
        color: {c['text']};
    }}
    QPushButton#CanvasQuickButton,
    QPushButton#LayerIconButton,
    QPushButton#PanelIconButton,
    QPushButton#PropertyIconButton {{
        padding: 0px;
        min-width: 32px;
        min-height: 30px;
        font-size: 15px;
        font-weight: bold;
    }}
    QPushButton#LayerIconButton:checked {{
        background: {c['selection']};
        border: 1px solid {c['border_hot']};
        color: {c['accent']};
    }}
    QSplitter::handle {{
        background: {c['border']};
    }}
    QSplitter::handle:hover, QSplitter::handle:pressed {{
        background: {c['accent']};
    }}
    QPushButton#StatusZoomButton {{
        padding: 0px 6px; min-width: 26px; border: none; background: transparent;
    }}
    QToolButton#LibraryMenuButton {{
        font-size: 17px; padding: 0px 8px;
        border: 1px solid {c['border']}; border-radius: {radius};
    }}
    QPushButton#LayerNameButton {{
        background: transparent;
        border: none;
        padding: 0px;
        text-align: left;
    }}
    QPushButton#LayerNameButton:hover {{
        color: {c['accent']};
        border: none;
    }}
    QPushButton:hover {{
        background: {c['hover']};
        border: 1px solid {c['border_hot']};
    }}
    QPushButton:pressed {{
        background: {c['selection']};
        padding-top: 5px; padding-bottom: 3px;
    }}
    QPushButton:focus {{ border: 1px solid {c['border_hot']}; }}
    QPushButton:disabled {{ color: {c['disabled']}; border-color: {c['panel3']}; }}
    QPushButton:default {{ border: 2px solid {c['border_hot']}; }}

    QListWidget, QTreeWidget {{
        background: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: {radius};
        padding: 3px;
        outline: 0;
    }}
    QListWidget::item {{
        padding: 4px 6px;
        border-radius: 3px;
        margin: 1px 2px;
    }}
    QListWidget::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
    QListWidget::item:hover {{ background: {c['hover']}; }}

    QTabWidget::pane {{
        border: 1px solid {c['border']};
        border-radius: {radius};
        top: -1px;
    }}
    QTabBar {{ qproperty-drawBase: 0; }}
    QTabBar::tab {{
        background: {c['panel']};
        border: 1px solid {c['border']};
        border-bottom: none;
        border-top-left-radius: {radius};
        border-top-right-radius: {radius};
        padding: 6px 9px;
        margin-right: 2px;
        color: {c['muted']};
    }}
    QTabBar::tab:selected {{
        background: {c['panel3']};
        color: {c['accent']};
        border-bottom: 2px solid {c['accent']};
    }}
    QTabBar::tab:hover:!selected {{ background: {c['hover']}; color: {c['text']}; }}

    QSlider::groove:horizontal {{
        background: {c['panel2']}; height: 5px; border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {c['accent']}; border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {c['accent']}; width: 13px; height: 13px;
        margin: -5px 0; border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {c['accent']}; border: 2px solid {c['border_hot']};
    }}

    QScrollBar:vertical {{
        background: {c['panel']}; width: 11px; border-radius: 5px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c['border']}; border-radius: 5px; min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {c['border_hot']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar:horizontal {{ background: {c['panel']}; height: 11px; border-radius: 5px; }}
    QScrollBar::handle:horizontal {{
        background: {c['border']}; border-radius: 5px; min-width: 28px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {c['border_hot']}; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QCheckBox {{ spacing: 6px; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px; border: 1px solid {c['border']};
        border-radius: 3px; background: {c['panel2']};
    }}
    QCheckBox::indicator:checked {{
        background: {c['accent']}; border: 1px solid {c['accent']};
    }}
    QCheckBox::indicator:hover {{ border-color: {c['border_hot']}; }}
    QRadioButton {{ spacing: 6px; }}
    QRadioButton::indicator {{
        width: 14px; height: 14px; border: 2px solid {c['border_hot']};
        border-radius: 9px; background: {c['panel2']};
    }}
    QRadioButton::indicator:checked {{
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
            stop:0 {c['accent']}, stop:0.55 {c['accent']},
            stop:0.6 {c['panel2']}, stop:1 {c['panel2']});
        border: 2px solid {c['accent']};
    }}

    QLabel {{ color: {c['text']}; background: transparent; }}
    QStatusBar {{ background: {c['panel']}; color: {c['muted']}; border-top: 1px solid {c['border']}; }}
    QStatusBar::item {{ border: none; }}
    QToolTip {{
        background: {c['panel3']}; color: {c['text']};
        border: 1px solid {c['border_hot']}; border-radius: 3px; padding: 4px 8px;
    }}
    QHeaderView::section {{
        background: {c['panel2']}; color: {c['text']};
        border: 1px solid {c['border']}; padding: 3px 6px;
    }}
    QToolBar {{
        background: {c['panel']}; border-bottom: 1px solid {c['border']};
        spacing: 4px; padding: 3px;
    }}
    QToolBar QToolButton {{
        background: {c['panel2']}; border: 1px solid {c['border']};
        border-radius: {radius}; padding: 4px 8px; color: {c['text']};
    }}
    QToolBar QToolButton:hover {{
        background: {c['hover']}; border-color: {c['border_hot']};
    }}
    QToolBar QToolButton:pressed {{ background: {c['selection']}; }}
    QProgressBar {{ background: {c['panel2']}; border: 1px solid {c['border']}; border-radius: {radius}; }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: {radius}; }}
    """


def apply_stylesheet(app: QApplication, accent: str, text_scale: float,
                     mode: str = "alien"):
    app.setStyleSheet(build_stylesheet(accent, text_scale, mode))


class ScanlineOverlay(QWidget):
    def __init__(self, parent=None, accent: str = DEFAULT_ACCENT):
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
        p.setBrush(QColor(c.red(), c.green(), c.blue(), 22))
        for y in range(0, self.height(), 3):
            p.drawRect(0, y, self.width(), 1)
        p.end()
