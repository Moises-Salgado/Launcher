"""Sistema visual Qt del Centro de Comando Clínico.

Los colores, espacios y jerarquías se basan en la propuesta entregada por
Google Stitch, adaptados a widgets nativos de escritorio (PySide6).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


APP_DIR = Path(__file__).resolve().parent
ASSETS_DIR = APP_DIR / "assets"

COLOR_BG = "#f5f7fb"
COLOR_SURFACE = "#ffffff"
COLOR_SURFACE_SOFT = "#f1f5f9"
COLOR_BORDER = "#dbe3ed"
COLOR_BORDER_STRONG = "#b9c5d4"
COLOR_TEXT = "#111827"
COLOR_MUTED = "#5f6f82"
COLOR_PRIMARY = "#0757c9"
COLOR_PRIMARY_HOVER = "#0647a5"
COLOR_PRIMARY_SOFT = "#e8f0ff"
COLOR_SUCCESS = "#177245"
COLOR_SUCCESS_SOFT = "#e3f5ea"
COLOR_WARNING = "#9a5a00"
COLOR_WARNING_SOFT = "#fff1d5"
COLOR_DANGER = "#b42318"
COLOR_DANGER_SOFT = "#feeceb"


APP_STYLESHEET = f"""
* {{
    font-family: "Inter", "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 13px;
    color: {COLOR_TEXT};
}}
QMainWindow, QWidget#AppRoot {{ background: {COLOR_BG}; }}
QWidget {{ outline: none; }}
QFrame#TopBar {{
    background: {COLOR_SURFACE};
    border-bottom: 1px solid {COLOR_BORDER};
}}
QFrame#Sidebar {{
    background: {COLOR_SURFACE};
    border-right: 1px solid {COLOR_BORDER};
}}
QFrame#Card, QFrame#ToolCard, QFrame#DropZone, QFrame#MetricCard {{
    background: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 16px;
}}
QFrame#ToolCard:hover {{
    border: 1px solid {COLOR_PRIMARY};
    background: #fbfdff;
}}
QFrame#HeroBanner {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #ffffff, stop: 0.58 #f4f8ff, stop: 1 #e8f1ff
    );
    border: 1px solid #cdddf5;
    border-radius: 20px;
}}
QFrame#IconTile {{
    background: {COLOR_PRIMARY_SOFT};
    border: 1px solid #cddcff;
    border-radius: 12px;
}}
QFrame#PrivacyCard {{
    background: #f7faff;
    border: 1px solid #d8e4f5;
    border-radius: 12px;
}}
QFrame#DropZone {{
    background: #f7f7ff;
    border: 2px dashed {COLOR_BORDER_STRONG};
}}
QFrame#DropZone[hasFile="true"] {{
    border: 2px solid {COLOR_PRIMARY};
    background: {COLOR_PRIMARY_SOFT};
}}
QFrame#PatientNameWarning {{
    background: {COLOR_WARNING_SOFT};
    border: 2px solid #e2a33a;
    border-radius: 11px;
}}
QFrame#PatientNameWarning[invalid="true"] {{
    background: {COLOR_DANGER_SOFT};
    border-color: {COLOR_DANGER};
}}
QLabel#PatientNameWarningTitle {{
    color: {COLOR_WARNING};
    font-size: 14px;
    font-weight: 800;
}}
QLabel#PatientNameWarningText {{ color: #704608; }}
QLabel#PatientNameWarningTitle[invalid="true"],
QLabel#PatientNameWarningText[invalid="true"] {{ color: {COLOR_DANGER}; }}
QLabel#SeriesDetail {{
    color: {COLOR_TEXT};
    background: {COLOR_SURFACE_SOFT};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 8px 10px;
}}
QLabel#AppName {{ font-size: 17px; font-weight: 750; }}
QLabel#PageTitle {{ font-size: 28px; font-weight: 750; }}
QLabel#HeroTitle {{ font-size: 32px; font-weight: 800; color: #10233f; }}
QLabel#HeroSubtitle {{ font-size: 14px; color: {COLOR_MUTED}; }}
QLabel#SectionTitle {{ font-size: 17px; font-weight: 700; }}
QLabel#CardTitle {{ font-size: 16px; font-weight: 750; }}
QLabel#Eyebrow {{
    color: {COLOR_PRIMARY};
    font-size: 11px;
    font-weight: 700;
}}
QLabel#Muted, QLabel#Caption {{ color: {COLOR_MUTED}; }}
QLabel#Caption {{ font-size: 11px; }}
QLabel#Badge {{
    color: #484b5a;
    background: #e9eaf2;
    border-radius: 10px;
    padding: 3px 8px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel#BlueBadge {{
    color: {COLOR_PRIMARY};
    background: {COLOR_PRIMARY_SOFT};
    border-radius: 10px;
    padding: 3px 8px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel#DangerBadge {{
    color: {COLOR_DANGER};
    background: {COLOR_DANGER_SOFT};
    border-radius: 10px;
    padding: 3px 8px;
    font-size: 10px;
    font-weight: 700;
}}
QPushButton {{
    min-height: 38px;
    padding: 0 16px;
    border: 1px solid {COLOR_BORDER_STRONG};
    border-radius: 10px;
    background: {COLOR_SURFACE};
    color: {COLOR_TEXT};
    font-weight: 650;
}}
QPushButton:hover {{ background: {COLOR_SURFACE_SOFT}; border-color: #a8acbf; }}
QPushButton:pressed {{ background: #e7e8f2; }}
QPushButton:disabled {{ color: #999dac; background: #eff0f5; border-color: #e3e4eb; }}
QPushButton#PrimaryButton {{
    color: white;
    background: {COLOR_PRIMARY};
    border: 1px solid {COLOR_PRIMARY};
}}
QPushButton#PrimaryButton:hover {{ background: {COLOR_PRIMARY_HOVER}; }}
QPushButton#PrimaryButton:disabled {{
    color: #6f7382;
    background: #e2e4ec;
    border: 1px solid #c8cad5;
}}
QPushButton#DangerButton {{
    color: white;
    background: {COLOR_DANGER};
    border: 1px solid {COLOR_DANGER};
}}
QPushButton#GhostButton {{ border-color: transparent; background: transparent; }}
QPushButton#GhostButton:hover {{ color: {COLOR_PRIMARY}; background: {COLOR_PRIMARY_SOFT}; }}
QPushButton#BackButton {{
    min-width: 180px;
    min-height: 44px;
    padding: 0 18px;
    color: white;
    background: {COLOR_PRIMARY};
    border: 2px solid {COLOR_PRIMARY};
    border-radius: 10px;
    font-size: 14px;
    font-weight: 750;
}}
QPushButton#BackButton:hover {{
    color: white;
    background: {COLOR_PRIMARY_HOVER};
    border-color: {COLOR_PRIMARY_HOVER};
}}
QPushButton#BackButton:pressed {{ background: #002f82; border-color: #002f82; }}
QPushButton#ToolLaunchButton {{
    min-height: 42px;
    color: white;
    background: {COLOR_PRIMARY};
    border: 1px solid {COLOR_PRIMARY};
    border-radius: 10px;
    font-size: 13px;
    font-weight: 750;
}}
QPushButton#ToolLaunchButton:hover {{
    color: white;
    background: {COLOR_PRIMARY_HOVER};
    border-color: {COLOR_PRIMARY_HOVER};
}}
QPushButton#ToolLaunchButton:pressed {{ background: #033983; }}
QPushButton#NavButton {{
    min-height: 44px;
    text-align: left;
    padding: 0 13px;
    border: 0;
    border-radius: 10px;
    background: transparent;
    color: {COLOR_MUTED};
    font-weight: 650;
}}
QPushButton#NavButton:hover {{ background: {COLOR_SURFACE_SOFT}; color: {COLOR_TEXT}; }}
QPushButton#NavButton:checked {{
    background: {COLOR_PRIMARY_SOFT};
    color: {COLOR_PRIMARY};
    font-weight: 750;
}}
QLineEdit, QComboBox, QSpinBox {{
    min-height: 40px;
    padding: 0 11px;
    background: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER_STRONG};
    border-radius: 10px;
    selection-background-color: {COLOR_PRIMARY};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {COLOR_PRIMARY}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: #777b89;
    background: #eceef4;
    border-color: #d5d7e0;
}}
QComboBox QAbstractItemView {{
    color: {COLOR_TEXT};
    background: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER_STRONG};
    border-radius: 7px;
    outline: 0;
    padding: 4px;
    selection-color: {COLOR_PRIMARY};
    selection-background-color: {COLOR_PRIMARY_SOFT};
}}
QComboBox QAbstractItemView::item {{
    color: {COLOR_TEXT};
    background: {COLOR_SURFACE};
    min-height: 34px;
    padding: 5px 9px;
}}
QComboBox QAbstractItemView::item:hover,
QComboBox QAbstractItemView::item:selected {{
    color: {COLOR_PRIMARY};
    background: {COLOR_PRIMARY_SOFT};
}}
QCheckBox {{
    min-height: 32px;
    spacing: 10px;
    color: {COLOR_TEXT};
}}
QCheckBox:hover {{ color: {COLOR_PRIMARY}; }}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 1px solid {COLOR_BORDER_STRONG};
    border-radius: 5px;
    background: {COLOR_SURFACE};
}}
QCheckBox::indicator:hover {{ border: 2px solid {COLOR_PRIMARY}; }}
QCheckBox::indicator:checked {{
    background: {COLOR_PRIMARY};
    border: 1px solid {COLOR_PRIMARY};
    image: url("{ASSETS_DIR / 'check_white.svg'}");
}}
QCheckBox::indicator:disabled {{
    background: #e4e6ed;
    border-color: #cfd1da;
}}
QTextEdit, QPlainTextEdit, QListWidget, QTreeWidget, QTableWidget {{
    background: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 11px;
    selection-background-color: {COLOR_PRIMARY_SOFT};
    selection-color: {COLOR_TEXT};
    alternate-background-color: #f8f8fc;
}}
QListWidget::item, QTreeWidget::item {{ padding: 8px; border-radius: 5px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ color: {COLOR_PRIMARY}; background: {COLOR_PRIMARY_SOFT}; }}
QHeaderView::section {{
    background: {COLOR_SURFACE_SOFT};
    color: {COLOR_MUTED};
    border: 0;
    border-bottom: 1px solid {COLOR_BORDER};
    padding: 8px;
    font-weight: 650;
}}
QTabWidget::pane {{ border: 1px solid {COLOR_BORDER}; border-radius: 12px; background: {COLOR_SURFACE}; }}
QTabBar::tab {{
    background: transparent;
    color: {COLOR_MUTED};
    padding: 10px 16px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {COLOR_PRIMARY}; border-bottom-color: {COLOR_PRIMARY}; font-weight: 700; }}
QProgressBar {{
    min-height: 8px;
    max-height: 8px;
    border: 0;
    border-radius: 4px;
    background: #e6e7ef;
    text-align: center;
}}
QProgressBar::chunk {{ background: {COLOR_PRIMARY}; border-radius: 4px; }}
QScrollArea {{ border: 0; background: transparent; }}
QWidget#ScrollContent {{ background: {COLOR_BG}; }}
QScrollBar:vertical {{ width: 11px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #c8cad6; min-height: 34px; border-radius: 5px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QSplitter::handle {{ background: {COLOR_BORDER}; width: 1px; height: 1px; }}
QToolTip {{ color: {COLOR_TEXT}; background: {COLOR_SURFACE}; border: 1px solid {COLOR_BORDER_STRONG}; padding: 6px; }}
QDialog, QMessageBox {{ background: {COLOR_SURFACE}; }}
QMessageBox QLabel, QDialog QLabel {{
    color: {COLOR_TEXT};
    background: transparent;
}}
QMessageBox QLabel#qt_msgbox_label {{
    min-width: 360px;
    color: {COLOR_TEXT};
}}
QMessageBox QPushButton {{
    min-width: 92px;
    color: {COLOR_TEXT};
    background: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER_STRONG};
}}
QMessageBox QPushButton:hover {{
    color: {COLOR_PRIMARY};
    background: {COLOR_PRIMARY_SOFT};
    border-color: {COLOR_PRIMARY};
}}
"""

# Qt/Fusion puede perder el relleno de los controles que están dentro de un
# QFrame con efecto gráfico. Esta hoja local mantiene el fondo y el CTA de las
# tarjetas incluso cuando la sombra animada está activa.
TOOL_CARD_STYLESHEET = f"""
QFrame#ToolCard {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 16px;
}}
QFrame#ToolCard:hover {{
    background-color: #fbfdff;
    border-color: {COLOR_PRIMARY};
}}
QFrame#ToolCard QLabel {{ background-color: transparent; }}
QFrame#IconTile {{
    background-color: {COLOR_PRIMARY_SOFT};
    border: 1px solid #cddcff;
    border-radius: 12px;
}}
QPushButton#ToolLaunchButton {{
    min-height: 42px;
    color: white;
    background-color: {COLOR_PRIMARY};
    border: 1px solid {COLOR_PRIMARY};
    border-radius: 10px;
    font-size: 13px;
    font-weight: 750;
}}
QPushButton#ToolLaunchButton:hover {{
    color: white;
    background-color: {COLOR_PRIMARY_HOVER};
    border-color: {COLOR_PRIMARY_HOVER};
}}
QPushButton#ToolLaunchButton:pressed {{ background-color: #033983; }}
"""


def asset_icon(name: str) -> QIcon:
    path = ASSETS_DIR / name
    return QIcon(str(path)) if path.exists() else QIcon()


def pixmap_label(asset_name: str, size: int, object_name: str = "") -> QLabel:
    label = QLabel()
    if object_name:
        label.setObjectName(object_name)
    label.setFixedSize(size, size)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pixmap = QPixmap(str(ASSETS_DIR / asset_name))
    if not pixmap.isNull():
        label.setPixmap(
            pixmap.scaled(
                QSize(size, size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
    return label


def logo_mark_label(size: int = 48) -> QLabel:
    """Muestra el isotipo de radioterapia completo y preserva su transparencia."""
    widget = QLabel()
    widget.setFixedSize(size, size)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    source = QPixmap(str(ASSETS_DIR / "radiotherapy_logo.png"))
    if not source.isNull():
        widget.setPixmap(
            source.scaled(
                QSize(size, size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
    return widget


def label(text: str, object_name: str = "", word_wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(word_wrap)
    return widget


def button(text: str, *, primary: bool = False, ghost: bool = False) -> QPushButton:
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("PrimaryButton")
    elif ghost:
        widget.setObjectName("GhostButton")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def card(parent: QWidget | None = None, margins: tuple[int, int, int, int] = (20, 20, 20, 20)):
    frame = QFrame(parent)
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(12)
    return frame, layout


def page_header(code: str, title: str, description: str, on_back):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(16)

    back = button("←  VOLVER AL INICIO")
    back.setObjectName("BackButton")
    back.setFixedWidth(220)
    back.setToolTip("Regresar al Centro de Comando Clínico")
    back.clicked.connect(on_back)
    layout.addWidget(back, 0, Qt.AlignmentFlag.AlignTop)

    text_layout = QVBoxLayout()
    text_layout.setSpacing(5)
    text_layout.addWidget(label(code.upper(), "Eyebrow"))
    text_layout.addWidget(label(title, "PageTitle"))
    description_label = label(description, "Muted", True)
    description_label.setMaximumWidth(880)
    text_layout.addWidget(description_label)
    layout.addLayout(text_layout, 1)
    return widget


def h_spacer() -> QWidget:
    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return spacer
