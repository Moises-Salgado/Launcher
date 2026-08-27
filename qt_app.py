"""Aplicación de escritorio PySide6 del Centro de Comando Clínico.

La capa visual es Qt. Las rutinas clínicas/de procesamiento continúan en los
módulos P1-P4 existentes y se invocan directamente, sin navegador ni servidor.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import sys
import textwrap
import traceback
import uuid
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pydicom
from pydicom.misc import is_dicom
from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PIL.ImageQt import ImageQt
from pydicom.uid import MediaStorageDirectoryStorage

from P1_ExtractorOTs import OT_KIND_OPTIONS, UNIQUE_SECTIONS, analyze_ot_pdf
from P2_visor_estructuras import parse_txt_grouped
from P3_editor_dmc_carpeta import (
    DATE_TAG_ORDER,
    dicom_to_pil,
    has_non_ascii,
    make_nonconflicting_name,
    normalize_rut_display,
    parse_rut_any,
    pick_date_and_tag_from_ds,
    sanitize_folder_name,
    validate_iso_date,
)
from P4_1_dicom_eclipse_bulletproof import process_all
from config_manager import get_dicom_export_dir, get_ots_dir
from qt_theme import (
    APP_STYLESHEET,
    TOOL_CARD_STYLESHEET,
    COLOR_BG,
    COLOR_MUTED,
    asset_icon,
    button,
    card,
    label,
    logo_mark_label,
    page_header,
    pixmap_label,
)


APP_VERSION = "1.4.1"
P1_CATEGORIES = UNIQUE_SECTIONS


@dataclass(frozen=True)
class ToolDefinition:
    key: str
    code: str
    title: str
    subtitle: str
    description: str
    icon: str
    badge: str = ""


TOOLS = (
    ToolDefinition(
        "p1",
        "P1",
        "Extraer datos desde PDF",
        "UNIQUE + Halcyon + ECM",
        "Extrae y exporta OTs UNIQUE, Halcyon 1/2, Control de Calidad y otros formularios ECM.",
        "icon_pdf.png",
        "MULTIFORMATO",
    ),
    ToolDefinition(
        "p2",
        "P2",
        "Visor TXT de estructuras",
        "Órganos y campos de dosis",
        "Agrupa órganos y lateralidades, y permite revisar campos de dosis y el bloque original.",
        "icon_tools.png",
    ),
    ToolDefinition(
        "p3",
        "P3",
        "Editor de nombres y visualizador DICOM",
        "Visualizar y renombrar estudios",
        "Revisa series e imágenes DICOM y edita únicamente el Nombre del Paciente al exportar.",
        "icon_tools.png",
        "DICOM",
    ),
    ToolDefinition(
        "p4",
        "P4",
        "Compatibilizar CD para Eclipse",
        "HGGB y otras instituciones",
        "Prepara DICOM MR/RT de HGGB, Clínica Los Andes, Clínica Biobío u otros centros para Eclipse.",
        "icon_dicom.png",
        "ECLIPSE",
    ),
)


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)
    finished = Signal()


class FunctionWorker(QRunnable):
    def __init__(self, function: Callable, *args, **kwargs):
        super().__init__()
        self.function = function
        self.args = args
        self.progress_keyword = kwargs.pop("_worker_progress_keyword", None)
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            call_kwargs = dict(self.kwargs)
            if self.progress_keyword:
                call_kwargs[self.progress_keyword] = self.signals.progress.emit
            result = self.function(*self.args, **call_kwargs)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


def _friendly_error(parent: QWidget, title: str, detail: str) -> None:
    message = detail.strip().splitlines()[-1] if detail.strip() else "Error desconocido"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(message)
    box.setDetailedText(detail)
    box.exec()


def _question(parent: QWidget, title: str, text: str) -> bool:
    return (
        QMessageBox.question(
            parent,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        == QMessageBox.StandardButton.Yes
    )


def _p1_analysis_bundle(results: dict[str, Any]) -> dict[str, Any]:
    """Acepta también el diccionario UNIQUE antiguo para no romper integraciones."""
    if "data" in results and "sections" in results:
        return results
    return {
        "engine": "UNIQUE",
        "kind": "UNIQUE",
        "kind_label": "UNIQUE · iClinic",
        "detected_kind": "UNIQUE",
        "detected_label": "UNIQUE · iClinic",
        "classification_warning": False,
        "requires_selection": False,
        "data": results,
        "sections": P1_CATEGORIES,
        "title": "DATOS EXTRAÍDOS DEL PDF",
        "excel_name": "UNIQUE.xlsx",
        "category_column": "Categoría",
        "include_missing": False,
        "preserve_pdf_name": False,
    }


def _safe_p1_filename(value: str) -> str:
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    safe_name = re.sub(r"_+", "_", safe_name).strip(" ._")
    return (safe_name or "OT_sin_numero")[:120]


def _wrapped_tree_text(value: Any, width: int = 72) -> str:
    """Inserta saltos visibles sin recortar ni alterar el dato exportado."""
    raw = str(value if value is not None else "")
    lines = raw.splitlines() or [""]
    wrapped = []
    for line in lines:
        wrapped.append(
            textwrap.fill(
                line,
                width=width,
                break_long_words=True,
                break_on_hyphens=False,
            )
            if line
            else ""
        )
    return "\n".join(wrapped)


def _p1_name_base(results: dict[str, Any]) -> str:
    bundle = _p1_analysis_bundle(results)
    if bundle.get("name_base"):
        return _safe_p1_filename(str(bundle["name_base"]))
    datos = bundle["data"]
    numero_ot = re.sub(r"^\s*OT[\s-]*", "", str(datos.get("N°", "")).strip(), flags=re.I)
    tipo_tarea = str(datos.get("TIPO DE TAREA", "")).upper()
    fecha_inicio = str(datos.get("FECHA Y HORA DE INICIO", "")).strip()
    if "PREVENTIVA" in tipo_tarea:
        codigo = "MP"
    elif "CORRECTIVA" in tipo_tarea:
        codigo = "MC"
    else:
        codigo = "OT"
    mes = fecha_inicio[5:7] if len(fecha_inicio) >= 7 else "00"
    raw_name = f"OT{numero_ot}_{codigo}{mes}" if numero_ot else f"OT_{codigo}{mes}"
    return _safe_p1_filename(raw_name)


def _p1_serialized(results: dict[str, Any]):
    bundle = _p1_analysis_bundle(results)
    datos = bundle["data"]
    title = str(bundle.get("title") or "DATOS EXTRAÍDOS DEL PDF")
    category_column = str(bundle.get("category_column") or "Categoría")
    lines = ["=" * 70, f"{title:^70}", "=" * 70]
    excel_rows: list[dict[str, str]] = []

    def clean(value: Any) -> str:
        value = str(value if value is not None else "").replace("\u00A0", " ")
        return re.sub(r"\s+", " ", value).strip()

    if "TÍTULO" in datos:
        value = clean(datos["TÍTULO"])
        if value and value != "No encontrado":
            lines.extend(["", f"TÍTULO: {value}", "-" * 70])
            excel_rows.append({category_column: "TÍTULO", "Campo": "TÍTULO", "Valor": value})
    include_missing = bool(bundle.get("include_missing"))
    for category, fields in bundle["sections"].items():
        lines.extend(["", category.upper()])
        for field in fields:
            value = clean(datos.get(field, ""))
            missing = not value or value == "No encontrado"
            if missing and not include_missing:
                continue
            if missing:
                value = "No encontrado"
            display_field = "Número de tarea" if field == "_N_TAREA" else field
            lines.append(f"  • {display_field}: {value}")
            excel_rows.append(
                {category_column: category.upper(), "Campo": display_field, "Valor": value}
            )
    return "\n".join(lines) + "\n", excel_rows, [category_column, "Campo", "Valor"]


def _p1_pdf_destination(pdf_path: str, out_dir: str, results: dict[str, Any]) -> Path:
    bundle = _p1_analysis_bundle(results)
    if bundle.get("preserve_pdf_name"):
        return Path(out_dir) / Path(pdf_path).name
    return Path(out_dir) / f"{_p1_name_base(bundle)}.pdf"


def _p1_default_output(results: dict[str, Any]) -> Path:
    kind = str(_p1_analysis_bundle(results).get("kind") or "")
    base = get_ots_dir()
    if kind == "UNIQUE":
        return base / "ICLINIC" / "UNIQUE"
    if kind.startswith("HALCYON_"):
        return base / "ECM" / kind
    if kind == "CONTROL_DE_CALIDAD":
        return base / "ECM" / "CONTROL_DE_CALIDAD"
    return base / "ECM" / "OTRAS_OT"


def export_p1_results(
    pdf_path: str,
    out_dir: str,
    results: dict[str, Any],
    progress_callback: Callable[[int, str], None] | None = None,
) -> dict[str, str]:
    """Exporta PDF/TXT/Excel mediante temporales para evitar resultados parciales."""
    def report(percent: int, message: str) -> None:
        if progress_callback:
            progress_callback(percent, message)

    bundle = _p1_analysis_bundle(results)
    source_pdf = Path(pdf_path)
    if not source_pdf.is_file():
        raise FileNotFoundError(f"El PDF de origen ya no existe: {source_pdf}")

    output = Path(out_dir)
    if output.exists() and not output.is_dir():
        raise NotADirectoryError(f"La ruta de salida no es una carpeta: {output}")
    output.mkdir(parents=True, exist_ok=True)
    resumen = output / "Resumen"
    resumen.mkdir(parents=True, exist_ok=True)
    base = _p1_name_base(bundle)
    pdf_dst = _p1_pdf_destination(pdf_path, out_dir, bundle)
    txt_dst = resumen / f"{base}.txt"
    xlsx_dst = resumen / str(bundle.get("excel_name") or "UNIQUE.xlsx")
    text_output, rows, excel_columns = _p1_serialized(bundle)
    sheet = re.sub(r"[][\\:*?/]", "_", base)[:31]

    # LibreOffice crea este archivo mientras UNIQUE.xlsx está abierto. Es
    # preferible detenerse con un mensaje claro que esperar o arriesgar una
    # sobrescritura silenciosa.
    libreoffice_lock = resumen / f".~lock.{xlsx_dst.name}#"
    if libreoffice_lock.exists():
        raise PermissionError(
            f"{xlsx_dst.name} está abierto en LibreOffice. Ciérrelo y vuelva a intentar la exportación."
        )

    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    pdf_tmp = output / f".{pdf_dst.name}.{token}.tmp"
    txt_tmp = resumen / f".{txt_dst.name}.{token}.tmp"
    xlsx_tmp = resumen / f".{xlsx_dst.stem}.{token}.tmp.xlsx"
    temporary_files = (pdf_tmp, txt_tmp, xlsx_tmp)
    same_pdf = source_pdf.resolve() == pdf_dst.resolve()

    try:
        report(15, "Preparando la copia del PDF…")
        if not same_pdf:
            shutil.copy2(source_pdf, pdf_tmp)

        report(35, "Escribiendo el resumen TXT…")
        txt_tmp.write_text(text_output, encoding="utf-8")

        report(50, f"Leyendo {xlsx_dst.name}…" if xlsx_dst.exists() else f"Creando {xlsx_dst.name}…")
        dataframe = pd.DataFrame(rows, columns=excel_columns)
        if xlsx_dst.exists():
            shutil.copy2(xlsx_dst, xlsx_tmp)
            with pd.ExcelWriter(
                xlsx_tmp,
                engine="openpyxl",
                mode="a",
                if_sheet_exists="replace",
            ) as writer:
                dataframe.to_excel(writer, sheet_name=sheet, index=False)
        else:
            with pd.ExcelWriter(xlsx_tmp, engine="openpyxl") as writer:
                dataframe.to_excel(writer, sheet_name=sheet, index=False)

        report(85, "Verificando y finalizando archivos…")
        if not xlsx_tmp.is_file() or xlsx_tmp.stat().st_size == 0:
            raise OSError(f"No se pudo generar un archivo {xlsx_dst.name} válido.")
        if not same_pdf:
            os.replace(pdf_tmp, pdf_dst)
        os.replace(txt_tmp, txt_dst)
        os.replace(xlsx_tmp, xlsx_dst)
        report(100, "Exportación completada.")
    finally:
        for temporary in temporary_files:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    return {
        "pdf": str(pdf_dst),
        "txt": str(txt_dst),
        "xlsx": str(xlsx_dst),
        "sheet": sheet,
        "kind": str(bundle.get("kind_label") or bundle.get("kind") or "OT"),
    }


def analyze_pdf(path: str, requested_kind: str = "AUTO"):
    return analyze_ot_pdf(path, requested_kind)


class ToolCard(QFrame):
    activated = Signal(str)

    def __init__(self, tool: ToolDefinition):
        super().__init__()
        self.tool = tool
        self.setObjectName("ToolCard")
        self.setStyleSheet(TOOL_CARD_STYLESHEET)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(252)

        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(20)
        self._shadow.setOffset(0, 4)
        self._shadow.setColor(QColor(20, 45, 82, 28))
        self.setGraphicsEffect(self._shadow)
        self._blur_animation = QPropertyAnimation(self._shadow, b"blurRadius", self)
        self._offset_animation = QPropertyAnimation(self._shadow, b"yOffset", self)
        for animation in (self._blur_animation, self._offset_animation):
            animation.setDuration(170)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)
        top = QHBoxLayout()
        icon_tile = QFrame()
        icon_tile.setObjectName("IconTile")
        icon_tile.setFixedSize(62, 62)
        icon_layout = QVBoxLayout(icon_tile)
        icon_layout.setContentsMargins(8, 8, 8, 8)
        icon_layout.addWidget(pixmap_label(tool.icon, 44), 0, Qt.AlignmentFlag.AlignCenter)
        top.addWidget(icon_tile)
        top.addStretch()
        top.addWidget(label(tool.code, "BlueBadge"), 0, Qt.AlignmentFlag.AlignTop)
        if tool.badge:
            top.addWidget(label(tool.badge, "BlueBadge"), 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        layout.addSpacing(2)
        layout.addWidget(label(tool.title, "CardTitle"))
        layout.addWidget(label(tool.subtitle, "Eyebrow"))
        layout.addWidget(label(tool.description, "Muted", True), 1)
        self.open_button = button(f"Abrir {tool.code}   →")
        self.open_button.setObjectName("ToolLaunchButton")
        self.open_button.setToolTip(f"Abrir {tool.title}")
        self.open_button.clicked.connect(lambda: self.activated.emit(self.tool.key))
        layout.addWidget(self.open_button)

    def _animate_shadow(self, blur: float, y_offset: float):
        self._blur_animation.stop()
        self._offset_animation.stop()
        self._blur_animation.setStartValue(self._shadow.blurRadius())
        self._blur_animation.setEndValue(blur)
        self._offset_animation.setStartValue(self._shadow.yOffset())
        self._offset_animation.setEndValue(y_offset)
        self._blur_animation.start()
        self._offset_animation.start()

    def enterEvent(self, event):
        self._shadow.setColor(QColor(7, 87, 201, 48))
        self._animate_shadow(34, 7)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._shadow.setColor(QColor(20, 45, 82, 28))
        self._animate_shadow(20, 4)
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.tool.key)
        super().mouseDoubleClickEvent(event)


class HomePage(QWidget):
    open_tool = Signal(str)

    def __init__(self):
        super().__init__()
        self.cards: dict[str, ToolCard] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        center_scroll = QScrollArea()
        center_scroll.setWidgetResizable(True)
        center_scroll.viewport().setStyleSheet(f"background: {COLOR_BG};")
        center = QWidget()
        center.setObjectName("ScrollContent")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(40, 34, 40, 40)
        center_layout.setSpacing(24)

        hero = QFrame()
        hero.setObjectName("HeroBanner")
        hero.setMinimumHeight(164)
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(30, 25, 30, 25)
        hero_layout.setSpacing(24)
        hero_copy = QVBoxLayout()
        hero_copy.setSpacing(7)
        hero_copy.addStretch()
        hero_copy.addWidget(label("PANEL PRINCIPAL", "Eyebrow"))
        hero_copy.addWidget(label("Herramientas de radioterapia", "HeroTitle"))
        hero_copy.addWidget(
            label(
                "Acceso directo a extracción de OTs, revisión de estructuras y procesamiento DICOM.",
                "HeroSubtitle",
                True,
            )
        )
        hero_copy.addStretch()
        hero_layout.addLayout(hero_copy, 1)
        hero_layout.addWidget(logo_mark_label(112), 0, Qt.AlignmentFlag.AlignCenter)
        hero_shadow = QGraphicsDropShadowEffect(hero)
        hero_shadow.setBlurRadius(28)
        hero_shadow.setOffset(0, 5)
        hero_shadow.setColor(QColor(27, 60, 104, 25))
        hero.setGraphicsEffect(hero_shadow)
        self.hero = hero
        center_layout.addWidget(hero)

        tools_head = QHBoxLayout()
        tools_head.addWidget(label("Programas", "SectionTitle"))
        tools_head.addStretch()
        center_layout.addLayout(tools_head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(22)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for index, tool in enumerate(TOOLS):
            tool_card = ToolCard(tool)
            tool_card.activated.connect(self.open_tool)
            self.cards[tool.key] = tool_card
            grid.addWidget(tool_card, index // 2, index % 2)
        center_layout.addLayout(grid)

        center_layout.addStretch()
        center_scroll.setWidget(center)
        root.addWidget(center_scroll, 1)

class BasePage(QWidget):
    go_home = Signal()

    def __init__(self, code: str, title: str, description: str):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 28)
        root.setSpacing(20)
        root.addWidget(page_header(code, title, description, self.go_home.emit))
        self.content = QVBoxLayout()
        self.content.setSpacing(16)
        root.addLayout(self.content, 1)


class P1Page(BasePage):
    def __init__(self, pool: QThreadPool):
        super().__init__(
            "P1 · Órdenes de trabajo",
            "Extraer datos desde PDF",
            "Detecta y procesa OTs UNIQUE, Halcyon 1/2, Control de Calidad y otros formularios ECM.",
        )
        self.pool = pool
        self.pdf_path = ""
        self.results: dict[str, Any] | None = None
        self._extract_worker: FunctionWorker | None = None
        self._extract_error = ""
        self._export_worker: FunctionWorker | None = None
        self._export_result: dict[str, str] | None = None
        self._export_error = ""

        split = QSplitter(Qt.Orientation.Horizontal)
        left, left_layout = card()
        left.setMinimumWidth(360)
        left_layout.addWidget(label("Orden de trabajo", "SectionTitle"))
        left_layout.addWidget(label("Seleccione un PDF y confirme su formato antes de extraer.", "Muted", True))
        left_layout.addWidget(label("Formato de la OT"))
        self.kind_combo = QComboBox()
        for key, text_value in OT_KIND_OPTIONS:
            self.kind_combo.addItem(text_value, key)
        self.kind_combo.currentIndexChanged.connect(self._format_changed)
        left_layout.addWidget(self.kind_combo)
        self.kind_status = label("El programa intentará reconocer el formato automáticamente.", "Caption", True)
        left_layout.addWidget(self.kind_status)
        self.drop = QFrame()
        self.drop.setObjectName("DropZone")
        self.drop.setProperty("hasFile", False)
        drop_layout = QVBoxLayout(self.drop)
        drop_layout.setContentsMargins(28, 34, 28, 34)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(pixmap_label("icon_pdf.png", 64), 0, Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(label("Seleccionar Orden de Trabajo", "CardTitle"), 0, Qt.AlignmentFlag.AlignCenter)
        self.file_label = label("Ningún archivo seleccionado", "Caption", True)
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(self.file_label)
        self.choose_button = button("Examinar archivos locales")
        self.choose_button.clicked.connect(self.choose_pdf)
        drop_layout.addWidget(self.choose_button, 0, Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.drop, 1)
        self.extract_button = button("Extraer datos", primary=True)
        self.extract_button.setEnabled(False)
        self.extract_button.clicked.connect(self.extract)
        left_layout.addWidget(self.extract_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        left_layout.addWidget(self.progress)
        self.status_label = label("", "Caption", True)
        self.status_label.hide()
        left_layout.addWidget(self.status_label)
        split.addWidget(left)

        right, right_layout = card()
        result_head = QHBoxLayout()
        result_head.addWidget(label("Datos extraídos", "SectionTitle"))
        result_head.addStretch()
        self.export_button = button("Guardar exportación", primary=True)
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export)
        result_head.addWidget(self.export_button)
        right_layout.addLayout(result_head)
        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels(["Campo", "Valor"])
        self.result_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.result_tree.header().resizeSection(0, 250)
        self.result_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.setRootIsDecorated(True)
        self.result_tree.setWordWrap(True)
        self.result_tree.setUniformRowHeights(False)
        self.result_tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.result_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.result_tree.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        right_layout.addWidget(self.result_tree, 1)
        split.addWidget(right)
        split.setSizes([420, 760])
        self.content.addWidget(split)

    def choose_pdf(self):
        start = Path.home() / "Descargas"
        path, _ = QFileDialog.getOpenFileName(self, "Seleccionar Orden de Trabajo", str(start), "PDF (*.pdf)")
        if not path:
            return
        self.pdf_path = path
        self.results = None
        self.kind_combo.setCurrentIndex(0)
        self.kind_status.setText("El programa intentará reconocer el formato automáticamente.")
        self.file_label.setText(path)
        self.drop.setProperty("hasFile", True)
        self.drop.style().unpolish(self.drop)
        self.drop.style().polish(self.drop)
        self.extract_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self.result_tree.clear()
        QTimer.singleShot(0, self.extract)

    def extract(self):
        if not self.pdf_path or self._extract_worker is not None:
            return
        self._extract_error = ""
        self.extract_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.choose_button.setEnabled(False)
        self.kind_combo.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status_label.setText("Analizando el contenido del PDF…")
        self.status_label.show()
        worker = FunctionWorker(analyze_pdf, self.pdf_path, self.kind_combo.currentData() or "AUTO")
        self._extract_worker = worker
        worker.signals.result.connect(self._show_results)
        worker.signals.error.connect(self._capture_extract_error)
        worker.signals.finished.connect(self._extract_finished)
        self.pool.start(worker)

    @Slot(str)
    def _capture_extract_error(self, detail: str):
        self._extract_error = detail

    def _extract_finished(self):
        self.progress.hide()
        self.status_label.hide()
        self.extract_button.setEnabled(bool(self.pdf_path))
        self.choose_button.setEnabled(True)
        self.kind_combo.setEnabled(True)
        self._extract_worker = None
        if self._extract_error:
            detail = self._extract_error
            self._extract_error = ""
            self.export_button.setEnabled(
                bool(self.results and not self.results.get("requires_selection"))
            )
            QTimer.singleShot(0, lambda: _friendly_error(self, "No se pudo procesar el PDF", detail))

    @Slot(object)
    def _show_results(self, results):
        self.results = results
        self.result_tree.clear()
        data = results["data"]
        detected = results.get("detected_label", "Formato no reconocido")
        QTreeWidgetItem(self.result_tree, ["Formato utilizado", _wrapped_tree_text(results.get("kind_label", "OT"))])
        QTreeWidgetItem(self.result_tree, ["Detección automática", _wrapped_tree_text(detected)])
        if results.get("classification_warning"):
            QTreeWidgetItem(
                self.result_tree,
                ["Advertencia", _wrapped_tree_text("El formato elegido manualmente no coincide con la detección automática.")],
            )
        if "TÍTULO" in data and data["TÍTULO"] != "No encontrado":
            title_item = QTreeWidgetItem(self.result_tree, ["Título", _wrapped_tree_text(data["TÍTULO"])])
            title_item.setToolTip(1, str(data["TÍTULO"]))
        for category, fields in results["sections"].items():
            parent = QTreeWidgetItem(self.result_tree, [category, ""])
            parent.setExpanded(True)
            for field in fields:
                value = data.get(field, "No encontrado") or "No encontrado"
                if value != "No encontrado" or results.get("include_missing"):
                    display_field = "Número de tarea" if field == "_N_TAREA" else field
                    item = QTreeWidgetItem(
                        parent,
                        [_wrapped_tree_text(display_field, 30), _wrapped_tree_text(value)],
                    )
                    item.setToolTip(1, str(value))
        if results.get("requires_selection"):
            self.kind_status.setText(
                "Se detectó Halcyon, pero no el número del equipo. Seleccione HALCYON 1 o HALCYON 2 y vuelva a extraer."
            )
            self.export_button.setEnabled(False)
        else:
            self.kind_status.setText(f"Formato reconocido: {results.get('kind_label', 'OT')}.")
            self.export_button.setEnabled(True)

    @Slot(int)
    def _format_changed(self, _index: int):
        if self.results is not None:
            self.results = None
            self.result_tree.clear()
            self.export_button.setEnabled(False)
            self.kind_status.setText("Formato cambiado. Actualizando la vista automáticamente…")
        if self.pdf_path and self._extract_worker is None and self.kind_combo.isEnabled():
            QTimer.singleShot(0, self.extract)

    def export(self):
        if (
            not self.results
            or not self.pdf_path
            or self._export_worker is not None
            or self._extract_worker is not None
            or self.results.get("requires_selection")
        ):
            return
        if self.results.get("classification_warning") and not _question(
            self,
            "El formato no coincide",
            "El formato seleccionado manualmente no coincide con la detección automática. "
            "La extracción podría generar campos incorrectos.\n\n¿Desea exportar de todas maneras?",
        ):
            return
        output = QFileDialog.getExistingDirectory(
            self,
            "Carpeta de la Orden de Trabajo",
            str(_p1_default_output(self.results)),
        )
        if not output:
            return
        destination = _p1_pdf_destination(self.pdf_path, output, self.results)
        source_is_destination = Path(self.pdf_path).resolve() == destination.resolve()
        if destination.exists() and not source_is_destination and not _question(
            self,
            "El PDF ya existe",
            f"Ya existe {destination.name}.\n\n¿Desea sobrescribirlo y actualizar su resumen?",
        ):
            return
        self._export_result = None
        self._export_error = ""
        self.export_button.setEnabled(False)
        self.extract_button.setEnabled(False)
        self.choose_button.setEnabled(False)
        self.kind_combo.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()
        self.status_label.setText("Preparando la exportación…")
        self.status_label.show()
        worker = FunctionWorker(
            export_p1_results,
            self.pdf_path,
            output,
            self.results,
            _worker_progress_keyword="progress_callback",
        )
        self._export_worker = worker
        worker.signals.progress.connect(self._update_export_progress)
        worker.signals.result.connect(self._capture_export_result)
        worker.signals.error.connect(self._capture_export_error)
        worker.signals.finished.connect(self._export_finished)
        self.pool.start(worker)

    @Slot(int, str)
    def _update_export_progress(self, percent: int, message: str):
        self.progress.setValue(max(0, min(percent, 100)))
        self.status_label.setText(message)

    @Slot(object)
    def _capture_export_result(self, paths):
        self._export_result = paths

    @Slot(str)
    def _capture_export_error(self, detail: str):
        self._export_error = detail

    def _export_finished(self):
        result = self._export_result
        error = self._export_error
        self._export_worker = None
        self.progress.hide()
        self.status_label.hide()
        self.export_button.setEnabled(bool(self.results))
        self.extract_button.setEnabled(bool(self.pdf_path))
        self.choose_button.setEnabled(True)
        self.kind_combo.setEnabled(True)
        if error:
            QTimer.singleShot(0, lambda: _friendly_error(self, "No se pudo guardar", error))
        elif result:
            QTimer.singleShot(0, lambda: self._export_done(result))

    def _export_done(self, paths):
        QMessageBox.information(
            self,
            "Exportación completada",
            "Los datos fueron guardados correctamente.\n\n"
            f"Formato: {paths.get('kind', 'OT')}\n"
            f"PDF: {paths['pdf']}\nTXT: {paths['txt']}\nExcel: {paths['xlsx']}\nHoja: {paths['sheet']}",
        )


class P2Page(BasePage):
    def __init__(self):
        super().__init__(
            "P2 · Revisión dosimétrica",
            "Visor TXT de estructuras",
            "Consulta estructuras y lateralidades en modo de solo lectura, sin alterar el archivo original.",
        )
        self.meta: dict[str, str] = {}
        self.groups: dict[str, dict] = {}
        self.keys: list[str] = []

        tools = QHBoxLayout()
        self.path_label = label("Ningún archivo cargado", "Muted", True)
        open_button = button("Abrir TXT", primary=True)
        open_button.clicked.connect(self.open_txt)
        tools.addWidget(open_button)
        tools.addWidget(self.path_label, 1)
        self.content.addLayout(tools)

        split = QSplitter(Qt.Orientation.Horizontal)
        sidebar, side_layout = card()
        sidebar.setMinimumWidth(260)
        side_layout.addWidget(label("Estructuras", "SectionTitle"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar órgano o estructura…")
        self.search.textChanged.connect(self.populate_list)
        side_layout.addWidget(self.search)
        self.structure_list = QListWidget()
        self.structure_list.currentRowChanged.connect(self.show_structure)
        side_layout.addWidget(self.structure_list, 1)
        split.addWidget(sidebar)

        detail, detail_layout = card()
        self.detail_title = label("Seleccione un archivo TXT", "SectionTitle")
        self.detail_subtitle = label("Aquí aparecerán los datos de la estructura.", "Muted", True)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_subtitle)
        self.tabs = QTabWidget()
        self.global_text = QPlainTextEdit()
        self.global_text.setReadOnly(True)
        self.tabs.addTab(self.global_text, "Datos globales")
        self.side_tables: dict[str, QTableWidget] = {}
        for bucket, title in (("L", "Izquierda"), ("R", "Derecha"), ("B", "Sin lateralidad")):
            table = QTableWidget(0, 2)
            table.setHorizontalHeaderLabels(["Campo", "Valor"])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            table.verticalHeader().hide()
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setAlternatingRowColors(True)
            self.side_tables[bucket] = table
            self.tabs.addTab(table, title)
        self.raw_text = QPlainTextEdit()
        self.raw_text.setReadOnly(True)
        self.tabs.addTab(self.raw_text, "Texto original")
        detail_layout.addWidget(self.tabs, 1)
        split.addWidget(detail)
        split.setSizes([300, 900])
        split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.content.addWidget(split, 1)

    def open_txt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Abrir estructuras", str(Path.home()), "Texto (*.txt);;Todos (*)")
        if not path:
            return
        try:
            self.meta, self.groups = parse_txt_grouped(path)
        except Exception:
            _friendly_error(self, "No se pudo leer el TXT", traceback.format_exc())
            return
        self.path_label.setText(path)
        self.global_text.setPlainText("\n".join(f"{key}: {value}" for key, value in self.meta.items()) or "Sin datos globales.")
        self.populate_list()
        if self.structure_list.count():
            self.structure_list.setCurrentRow(0)
        else:
            self._clear_structure_detail(
                "Sin estructuras detectadas",
                "El archivo se abrió correctamente, pero no contiene bloques que comiencen con “Estructura:”.",
            )

    @Slot(str)
    def populate_list(self, _text: str = ""):
        query = self.search.text().strip().casefold()
        self.structure_list.clear()
        self.keys = []
        for key, group in sorted(self.groups.items(), key=lambda item: item[1].get("display", "").casefold()):
            display = group.get("display", key)
            if query and query not in display.casefold():
                continue
            sides = [name for code, name in (("L", "I"), ("R", "D"), ("B", "B")) if code in group]
            self.keys.append(key)
            self.structure_list.addItem(f"{display}   ·   {' / '.join(sides)}")
        if not self.keys and self.groups:
            self._clear_structure_detail(
                "Sin coincidencias",
                "Cambie o borre el texto de búsqueda para volver a ver las estructuras.",
            )

    def _clear_structure_detail(self, title: str, subtitle: str):
        self.detail_title.setText(title)
        self.detail_subtitle.setText(subtitle)
        for table in self.side_tables.values():
            table.setRowCount(0)
        self.raw_text.clear()

    @Slot(int)
    def show_structure(self, row: int):
        if row < 0 or row >= len(self.keys):
            if self.groups and self.structure_list.count() == 0:
                self._clear_structure_detail(
                    "Sin coincidencias",
                    "Cambie o borre el texto de búsqueda para volver a ver las estructuras.",
                )
            return
        group = self.groups[self.keys[row]]
        self.detail_title.setText(group.get("display", "Estructura"))
        available = []
        raw_sections = []
        for bucket, title in (("L", "Izquierda"), ("R", "Derecha"), ("B", "Sin lateralidad")):
            table = self.side_tables[bucket]
            table.setRowCount(0)
            entry = group.get(bucket)
            if not entry:
                continue
            available.append(title)
            fields = entry.get("fields", {})
            table.setRowCount(len(fields))
            for table_row, (field, value) in enumerate(fields.items()):
                table.setItem(table_row, 0, QTableWidgetItem(field))
                table.setItem(table_row, 1, QTableWidgetItem(value))
            raw_sections.append(f"[{title}]\n{entry.get('raw', '')}")
        self.detail_subtitle.setText(" · ".join(available) if available else "Sin bloques reconocidos")
        self.raw_text.setPlainText("\n\n".join(raw_sections))


def _is_dicom_file(path: str) -> bool:
    base = os.path.basename(path)
    if base.upper() == "DICOMDIR":
        return False
    if base.lower().endswith(".dcm"):
        return True
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if os.path.getsize(path) < 132:
                return False
            if is_dicom(path):
                return True
    except Exception:
        pass
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dataset = pydicom.dcmread(path, force=True, stop_before_pixels=True)
        return any(tag in dataset for tag in ("SOPClassUID", "PatientID", "StudyInstanceUID"))
    except Exception:
        return False


def scan_dicom_folder(folder: str):
    seen: set[str] = set()
    paths: list[str] = []
    for root, _, filenames in os.walk(folder, followlinks=False):
        for filename in filenames:
            path = os.path.join(root, filename)
            if not _is_dicom_file(path):
                continue
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                paths.append(real)
    paths.sort()
    if not paths:
        raise RuntimeError("No se encontraron archivos DICOM en la carpeta seleccionada.")

    names: set[str] = set()
    patient_ids: set[str] = set()
    dates: Counter = Counter()
    date_tags: Counter = Counter()
    grouped: dict[str, list[tuple[tuple, str]]] = defaultdict(list)
    image_grouped: dict[str, list[tuple[tuple, str]]] = defaultdict(list)
    series_meta: dict[str, dict[str, str]] = {}
    transfer_syntaxes: Counter = Counter()
    accepted_paths: list[str] = []
    pixel_files = 0
    non_pixel_files = 0
    ignored_technical_files = 0
    read_errors = 0
    read_warnings = 0
    tags = [
        "PatientName",
        "PatientID",
        *DATE_TAG_ORDER,
        "StudyInstanceUID",
        "SeriesInstanceUID",
        "SeriesNumber",
        "SeriesDescription",
        "ProtocolName",
        "Modality",
        "BodyPartExamined",
        "PatientPosition",
        "InstanceNumber",
        "ImagePositionPatient",
        "SOPClassUID",
        "Rows",
        "Columns",
        "NumberOfFrames",
        "PixelData",
        "FloatPixelData",
        "DoubleFloatPixelData",
        ]

    for index, path in enumerate(paths):
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                dataset = pydicom.dcmread(
                    path,
                    force=True,
                    defer_size="1 KB",
                    specific_tags=tags,
                )
            read_warnings += len(caught)
        except Exception:
            read_errors += 1
            continue
        sop_class_uid = str(
            getattr(dataset, "SOPClassUID", "")
            or getattr(getattr(dataset, "file_meta", None), "MediaStorageSOPClassUID", "")
            or ""
        ).strip()
        if sop_class_uid == str(MediaStorageDirectoryStorage):
            ignored_technical_files += 1
            continue
        modality = str(getattr(dataset, "Modality", "") or "").strip()
        series_uid = str(getattr(dataset, "SeriesInstanceUID", "") or "").strip()
        has_pixel_data = any(
            tag in dataset for tag in ("PixelData", "FloatPixelData", "DoubleFloatPixelData")
        )
        # Algunos CD incluyen copias parciales de cabeceras de imagen sin píxeles,
        # modalidad ni serie. No son una serie clínica navegable y su copia completa
        # aparece en otra ubicación del mismo medio, por lo que se omiten del visor.
        if not has_pixel_data and not modality and not series_uid:
            ignored_technical_files += 1
            continue
        accepted_paths.append(path)
        name = str(getattr(dataset, "PatientName", "") or "").strip()
        patient_id = str(getattr(dataset, "PatientID", "") or "").strip()
        if name:
            names.add(name)
        if patient_id:
            patient_ids.add(patient_id)
        tag, date_iso = pick_date_and_tag_from_ds(dataset)
        if date_iso:
            dates[date_iso] += 1
            if tag:
                date_tags[tag] += 1
        else:
            dates["(sin fecha en esos tags)"] += 1

        uid = series_uid or f"NOUID_{index}"
        transfer_syntax = str(
            getattr(getattr(dataset, "file_meta", None), "TransferSyntaxUID", "") or ""
        ).strip()
        if uid not in series_meta:
            series_meta[uid] = {
                "SeriesNumber": str(getattr(dataset, "SeriesNumber", "") or "").strip(),
                "SeriesDescription": str(getattr(dataset, "SeriesDescription", "") or "").strip(),
                "ProtocolName": str(getattr(dataset, "ProtocolName", "") or "").strip(),
                "Modality": modality,
                "BodyPartExamined": str(getattr(dataset, "BodyPartExamined", "") or "").strip(),
                "PatientPosition": str(getattr(dataset, "PatientPosition", "") or "").strip(),
                "SOPClassUID": sop_class_uid,
                "TransferSyntaxUID": transfer_syntax,
            }
        instance = getattr(dataset, "InstanceNumber", None)
        try:
            instance_number = int(instance) if instance is not None else None
        except Exception:
            instance_number = None
        position = getattr(dataset, "ImagePositionPatient", None)
        try:
            z_position = float(position[2]) if position is not None and len(position) >= 3 else None
        except Exception:
            z_position = None
        if instance_number is not None:
            sort_key = (0, instance_number, 0.0, os.path.basename(path))
        elif z_position is not None:
            sort_key = (1, 0, z_position, os.path.basename(path))
        else:
            sort_key = (2, 0, 0.0, os.path.basename(path))
        grouped[uid].append((sort_key, path))
        if has_pixel_data:
            image_grouped[uid].append((sort_key, path))
            pixel_files += 1
            transfer_syntaxes[transfer_syntax or "(no indicada)"] += 1
        else:
            non_pixel_files += 1

    series_map = {
        uid: [path for _, path in sorted(items, key=lambda item: item[0])]
        for uid, items in grouped.items()
    }
    series_image_map = {
        uid: [path for _, path in sorted(image_grouped.get(uid, []), key=lambda item: item[0])]
        for uid in series_map
    }
    if not series_map:
        raise RuntimeError("Los archivos fueron detectados, pero no se pudo formar ninguna serie DICOM.")

    detected_rut = ""
    for patient_id in sorted(patient_ids):
        normalized = normalize_rut_display(patient_id)
        if parse_rut_any(normalized):
            detected_rut = normalized
            break
    if not detected_rut:
        normalized = normalize_rut_display(os.path.basename(folder))
        if parse_rut_any(normalized):
            detected_rut = normalized

    valid_dates = [(date, count) for date, count in dates.items() if not date.startswith("(")]
    valid_dates.sort(key=lambda item: (-item[1], item[0]))
    return {
        "folder": os.path.abspath(folder),
        "files": accepted_paths,
        "series_map": series_map,
        "series_image_map": series_image_map,
        "series_meta": series_meta,
        "names": names,
        "patient_ids": patient_ids,
        "rut": detected_rut,
        "dates": valid_dates,
        "date_tags": date_tags,
        "missing_dates": dates.get("(sin fecha en esos tags)", 0),
        "read_errors": read_errors,
        "read_warnings": read_warnings,
        "pixel_files": pixel_files,
        "non_pixel_files": non_pixel_files,
        "ignored_technical_files": ignored_technical_files,
        "transfer_syntaxes": transfer_syntaxes,
    }


def _series_label(meta: dict[str, str]) -> str:
    parts = []
    number = meta.get("SeriesNumber", "")
    position = meta.get("PatientPosition", "")
    description = meta.get("SeriesDescription", "") or meta.get("ProtocolName", "")
    if number:
        parts.append(f"Serie #{number}")
    else:
        parts.append("Serie DICOM")
    if position:
        parts.append(position)
    if description:
        parts.append(description)
    return " · ".join(parts)


def _non_image_series_message(meta: dict[str, str]) -> str:
    modality = (meta.get("Modality") or "").strip().upper()
    if modality == "SR":
        return "Esta serie es un informe DICOM (SR). Contiene texto estructurado, no cortes de imagen."
    if modality == "PR":
        return "Esta serie es un estado de presentación DICOM (PR). Guarda ajustes de visualización, no píxeles."
    if modality == "RTSTRUCT":
        return "Esta serie contiene estructuras y contornos RTSTRUCT, no una imagen raster para mostrar."
    if modality in {"RTPLAN", "RTRECORD"}:
        return "Este objeto contiene información de planificación de radioterapia, no una imagen raster."
    return (
        f"Esta serie {modality or 'DICOM'} no contiene PixelData; "
        "se conserva para la exportación, pero no tiene una imagen para mostrar."
    )


def _series_folder_name(meta: dict[str, str], index: int) -> str:
    number = (meta.get("SeriesNumber") or "").strip()
    try:
        prefix = f"SR{int(number):03d}"
    except Exception:
        prefix = f"SR{index:03d}"
    description = (
        meta.get("SeriesDescription")
        or meta.get("ProtocolName")
        or meta.get("Modality")
        or "Serie"
    )
    return (sanitize_folder_name(f"{prefix}_{description}")[:80] or prefix)


def export_p3_study(
    scan: dict[str, Any],
    date_iso: str,
    new_name: str,
    base_output: str,
    selected_uids: list[str],
):
    source = os.path.abspath(scan["folder"])
    if not os.path.isdir(source):
        raise FileNotFoundError(f"La carpeta DICOM de origen ya no existe: {source}")
    base_output = os.path.abspath(base_output)
    if os.path.exists(base_output) and not os.path.isdir(base_output):
        raise NotADirectoryError(f"La ruta base de salida no es una carpeta: {base_output}")
    os.makedirs(base_output, exist_ok=True)
    folder_name = sanitize_folder_name(f"{scan['rut']}_{date_iso}")
    final_output = os.path.abspath(os.path.join(base_output, folder_name))
    source_base = os.path.normcase(os.path.basename(source.rstrip(os.sep)))
    if source_base == os.path.normcase(folder_name):
        nested = os.path.normcase(os.path.abspath(os.path.join(source, folder_name)))
        if os.path.normcase(final_output) == nested:
            final_output = source
    if os.path.exists(final_output) and not os.path.isdir(final_output):
        raise NotADirectoryError(f"El destino existe, pero no es una carpeta: {final_output}")

    # Siempre se genera primero una carpeta hermana temporal. Así, una lectura
    # fallida o un disco lleno nunca destruyen una exportación previa a medio
    # proceso. El intercambio por el destino ocurre solo al terminar.
    parent = os.path.dirname(final_output.rstrip(os.sep))
    base = os.path.basename(final_output.rstrip(os.sep))
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    output = os.path.join(parent, f".{base}__TMP__{token}")
    backup = os.path.join(parent, f".{base}__BAK__{token}")
    os.makedirs(output)

    def sort_key(uid: str):
        number = (scan["series_meta"].get(uid, {}).get("SeriesNumber") or "").strip()
        try:
            return 0, int(number)
        except Exception:
            return 1, number, uid

    series_uids = sorted(selected_uids, key=sort_key)
    if not series_uids:
        shutil.rmtree(output, ignore_errors=True)
        raise ValueError("No hay series DICOM seleccionadas para exportar.")

    edited = copied = missing = failed = 0
    backup_left = ""
    try:
        used_directories: set[str] = set()
        for series_index, uid in enumerate(series_uids, start=1):
            files = list(scan["series_map"].get(uid, []))
            if not files:
                continue
            directory_name = make_nonconflicting_name(
                _series_folder_name(scan["series_meta"].get(uid, {}), series_index),
                used_directories,
            )
            used_directories.add(directory_name)
            directory = os.path.join(output, directory_name)
            os.makedirs(directory)
            used_names: set[str] = set()
            for source_file in files:
                if not os.path.exists(source_file):
                    missing += 1
                    continue
                output_name = make_nonconflicting_name(os.path.basename(source_file), used_names)
                destination = os.path.join(directory, output_name)
                try:
                    dataset = pydicom.dcmread(source_file, force=True)
                    dataset.PatientName = new_name
                    if has_non_ascii(new_name):
                        dataset.SpecificCharacterSet = "ISO_IR 192"
                    try:
                        dataset.save_as(destination, write_like_original=True)
                    except TypeError:
                        dataset.save_as(destination)
                    used_names.add(output_name)
                    edited += 1
                except Exception:
                    try:
                        shutil.copy2(source_file, destination)
                        used_names.add(output_name)
                        copied += 1
                    except Exception:
                        failed += 1

        if os.path.exists(final_output):
            os.rename(final_output, backup)
        try:
            os.rename(output, final_output)
        except Exception:
            if not os.path.exists(final_output) and os.path.exists(backup):
                os.rename(backup, final_output)
            raise
        if os.path.isdir(backup):
            try:
                shutil.rmtree(backup)
            except OSError:
                backup_left = backup
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        if not os.path.exists(final_output) and os.path.exists(backup):
            os.rename(backup, final_output)
        raise

    return {
        "output": final_output,
        "series": len(series_uids),
        "processed": sum(len(scan["series_map"].get(uid, [])) for uid in series_uids),
        "edited": edited,
        "copied": copied,
        "missing": missing,
        "failed": failed,
        "backup_left": backup_left,
    }


class DicomGraphicsView(QGraphicsView):
    slice_requested = Signal(int)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene().addItem(self.pixmap_item)
        self.setBackgroundBrush(QColor("#11131a"))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(self.renderHints())
        self.setMinimumHeight(235)
        self._fit = True

    def set_pixmap(self, pixmap: QPixmap):
        self.pixmap_item.setPixmap(pixmap)
        self.scene().setSceneRect(self.pixmap_item.boundingRect())
        self.fit_image()

    def fit_image(self):
        if not self.pixmap_item.pixmap().isNull():
            self.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._fit = True

    def actual_size(self):
        self.resetTransform()
        self._fit = False

    def zoom(self, factor: float):
        if not self.pixmap_item.pixmap().isNull():
            self.scale(factor, factor)
            self._fit = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit:
            self.fit_image()

    def wheelEvent(self, event):
        """La rueda recorre cortes solo cuando el puntero está sobre la imagen."""
        delta = event.angleDelta().y()
        if delta:
            self.slice_requested.emit(-1 if delta > 0 else 1)
            event.accept()
            return
        super().wheelEvent(event)


class P3Page(BasePage):
    def __init__(self, pool: QThreadPool):
        super().__init__(
            "P3 · DICOM",
            "Editor de nombres y visualizador DICOM",
            "Visualice las series y exporte una copia cambiando exclusivamente PatientName. Los archivos de origen no se modifican.",
        )
        self.pool = pool
        self.scan: dict[str, Any] | None = None
        self.current_uid = ""
        self.current_slice = 0
        self._scan_worker: FunctionWorker | None = None
        self._scan_error = ""
        self._scan_previous_label = ""
        self._save_worker: FunctionWorker | None = None
        self._save_result: dict[str, Any] | None = None
        self._save_error = ""

        toolbar = QHBoxLayout()
        self.choose_folder_button = button("Seleccionar carpeta DICOM", primary=True)
        self.choose_folder_button.clicked.connect(self.choose_folder)
        self.folder_label = label("Ninguna carpeta cargada", "Muted", True)
        toolbar.addWidget(self.choose_folder_button)
        toolbar.addWidget(self.folder_label, 1)
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setFixedWidth(150)
        self.scan_progress.hide()
        toolbar.addWidget(self.scan_progress)
        self.content.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.content.addWidget(self.tabs, 1)

        viewer_tab = QWidget()
        viewer_layout = QHBoxLayout(viewer_tab)
        viewer_layout.setContentsMargins(16, 16, 16, 16)
        viewer_layout.setSpacing(14)
        series_card, series_layout = card(margins=(16, 16, 16, 16))
        series_card.setMinimumWidth(420)
        series_layout.addWidget(label("Series del estudio", "SectionTitle"))
        series_layout.addWidget(
            label(
                "La modalidad (MR, CT, RTSTRUCT…) se muestra separada. Pase el puntero "
                "sobre una descripción para verla completa.",
                "Caption",
                True,
            )
        )
        self.series_tree = QTreeWidget()
        self.series_tree.setHeaderLabels(["Modalidad", "Serie / descripción", "Imágenes"])
        self.series_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.series_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.series_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.series_tree.currentItemChanged.connect(self.select_series)
        series_layout.addWidget(self.series_tree, 1)
        self.series_detail = label("Seleccione una serie para ver su nombre completo.", "SeriesDetail", True)
        series_layout.addWidget(self.series_detail)
        mark_row = QHBoxLayout()
        mark_all = button("Marcar todo")
        unmark_all = button("Desmarcar todo")
        mark_all.clicked.connect(lambda: self.mark_series(Qt.CheckState.Checked))
        unmark_all.clicked.connect(lambda: self.mark_series(Qt.CheckState.Unchecked))
        mark_row.addWidget(mark_all)
        mark_row.addWidget(unmark_all)
        series_layout.addLayout(mark_row)
        viewer_layout.addWidget(series_card, 5)

        image_card, image_layout = card(margins=(14, 14, 14, 14))
        image_head = QHBoxLayout()
        self.patient_banner = label("Sin paciente", "SectionTitle", True)
        self.image_counter = label("0 / 0", "BlueBadge")
        image_head.addWidget(self.patient_banner, 1)
        image_head.addWidget(self.image_counter)
        image_layout.addLayout(image_head)
        self.graphics = DicomGraphicsView()
        self.graphics.setToolTip("Rueda del mouse: corte anterior o siguiente")
        self.graphics.slice_requested.connect(self.step_slice)
        image_layout.addWidget(
            label("Pase el puntero sobre la imagen y use la rueda para recorrer los cortes.", "Caption", True)
        )
        self.viewer_status = label(
            "Seleccione una serie que contenga imágenes.",
            "SeriesDetail",
            True,
        )
        image_layout.addWidget(self.viewer_status)
        image_layout.addWidget(self.graphics, 1)
        image_actions = QHBoxLayout()
        self.previous_button = button("← Anterior")
        self.next_button = button("Siguiente →")
        self.previous_button.clicked.connect(lambda: self.step_slice(-1))
        self.next_button.clicked.connect(lambda: self.step_slice(1))
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        zoom_out = button("−")
        zoom_in = button("+")
        fit = button("Ajustar")
        actual = button("100 %")
        zoom_out.clicked.connect(lambda: self.graphics.zoom(0.82))
        zoom_in.clicked.connect(lambda: self.graphics.zoom(1.22))
        fit.clicked.connect(self.graphics.fit_image)
        actual.clicked.connect(self.graphics.actual_size)
        image_actions.addWidget(self.previous_button)
        image_actions.addWidget(self.next_button)
        image_actions.addStretch()
        image_actions.addWidget(zoom_out)
        image_actions.addWidget(zoom_in)
        image_actions.addWidget(fit)
        image_actions.addWidget(actual)
        image_layout.addLayout(image_actions)
        viewer_layout.addWidget(image_card, 8)
        self.tabs.addTab(viewer_tab, "Vista DICOM")

        edit_scroll = QScrollArea()
        edit_scroll.setWidgetResizable(True)
        edit_scroll.viewport().setStyleSheet(f"background: {COLOR_BG};")
        edit_tab = QWidget()
        edit_tab.setObjectName("ScrollContent")
        edit_layout = QVBoxLayout(edit_tab)
        edit_layout.setContentsMargins(18, 18, 18, 18)
        edit_layout.setSpacing(15)
        identity, identity_layout = card()
        identity_layout.addWidget(label("Identificación detectada", "SectionTitle"))
        identity_grid = QGridLayout()
        identity_grid.addWidget(label("RUT", "Muted"), 0, 0)
        self.rut_value = label("—", "CardTitle")
        identity_grid.addWidget(self.rut_value, 0, 1)
        identity_grid.addWidget(label("PatientName original", "Muted"), 1, 0)
        self.original_name = label("—", "CardTitle", True)
        identity_grid.addWidget(self.original_name, 1, 1)
        identity_layout.addLayout(identity_grid)
        edit_layout.addWidget(identity)

        export_card, export_layout = card()
        export_layout.addWidget(label("Editar y guardar una copia", "SectionTitle"))
        export_layout.addWidget(label("Solo se escribirá la etiqueta PatientName.", "Muted"))

        self.name_warning_card = QFrame()
        self.name_warning_card.setObjectName("PatientNameWarning")
        self.name_warning_card.setProperty("invalid", False)
        name_warning_layout = QVBoxLayout(self.name_warning_card)
        name_warning_layout.setContentsMargins(15, 12, 15, 12)
        name_warning_layout.setSpacing(5)
        self.name_warning_title = label("⚠  NO ELIMINE EL SÍMBOLO ^", "PatientNameWarningTitle")
        self.name_warning_text = label(
            "En DICOM, ^ separa los apellidos de los nombres. Ejemplo: "
            "MARTINEZ MORALES^MARIELA CRISTINA. Manténgalo al editar.",
            "PatientNameWarningText",
            True,
        )
        name_warning_layout.addWidget(self.name_warning_title)
        name_warning_layout.addWidget(self.name_warning_text)
        export_layout.addWidget(self.name_warning_card)

        fields = QGridLayout()
        fields.setHorizontalSpacing(14)
        fields.setVerticalSpacing(12)
        fields.addWidget(label("Nuevo PatientName"), 0, 0)
        self.new_name = QLineEdit()
        self.new_name.textChanged.connect(self._update_patient_name_warning)
        fields.addWidget(self.new_name, 0, 1)
        fields.addWidget(label("Fecha del estudio"), 1, 0)
        self.date_combo = QComboBox()
        self.date_combo.currentTextChanged.connect(self.update_output_preview)
        fields.addWidget(self.date_combo, 1, 1)
        fields.addWidget(label("Carpeta base de salida"), 2, 0)
        output_row = QHBoxLayout()
        self.output_base = QLineEdit()
        self.output_base.textChanged.connect(self.update_output_preview)
        output_choose = button("Elegir…")
        output_choose.clicked.connect(self.choose_output)
        output_row.addWidget(self.output_base, 1)
        output_row.addWidget(output_choose)
        fields.addLayout(output_row, 2, 1)
        export_layout.addLayout(fields)
        export_layout.addWidget(label("VISTA PREVIA DE SALIDA", "Eyebrow"))
        self.output_preview = label("Seleccione una carpeta de salida", "Muted", True)
        export_layout.addWidget(self.output_preview)
        self.save_button = button("Guardar copia DICOM", primary=True)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_study)
        export_layout.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignRight)
        edit_layout.addWidget(export_card)
        edit_layout.addStretch()
        edit_scroll.setWidget(edit_tab)
        self.tabs.addTab(edit_scroll, "Editar y guardar")

        details_tab = QWidget()
        details_layout = QVBoxLayout(details_tab)
        details_layout.setContentsMargins(18, 18, 18, 18)
        self.details_text = QPlainTextEdit()
        self.details_text.setReadOnly(True)
        details_layout.addWidget(self.details_text)
        self.tabs.addTab(details_tab, "Detalles")

    def choose_folder(self):
        if self._scan_worker is not None or self._save_worker is not None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta DICOM", str(Path.home()))
        if not folder:
            return
        self._scan_error = ""
        self._scan_previous_label = self.folder_label.text()
        self.scan_progress.show()
        self.save_button.setEnabled(False)
        self.choose_folder_button.setEnabled(False)
        self.folder_label.setText("Analizando archivos DICOM…")
        worker = FunctionWorker(scan_dicom_folder, folder)
        self._scan_worker = worker
        worker.signals.result.connect(self.load_scan)
        worker.signals.error.connect(self._capture_scan_error)
        worker.signals.finished.connect(self._scan_finished)
        self.pool.start(worker)

    @Slot(str)
    def _capture_scan_error(self, detail: str):
        self._scan_error = detail

    def _scan_finished(self):
        error = self._scan_error
        self._scan_worker = None
        self.scan_progress.hide()
        self.choose_folder_button.setEnabled(True)
        if error:
            self.folder_label.setText(self._scan_previous_label or "Ninguna carpeta cargada")
        self.save_button.setEnabled(
            bool(self.scan and self.scan.get("rut") and self.scan.get("dates"))
        )
        if error:
            self._scan_error = ""
            QTimer.singleShot(0, lambda: _friendly_error(self, "No se pudo cargar la carpeta", error))

    @Slot(object)
    def load_scan(self, scan):
        self.scan = scan
        self.folder_label.setText(f"{scan['folder']}  ·  {len(scan['files'])} archivos")
        self.series_tree.clear()
        first_item = None
        first_image_item = None
        for uid, files in scan["series_map"].items():
            meta = scan["series_meta"].get(uid, {})
            modality = (meta.get("Modality") or "No indicada").strip()
            series_text = _series_label(meta)
            image_count = len(scan.get("series_image_map", {}).get(uid, []))
            image_text = str(image_count) if image_count else "—"
            item = QTreeWidgetItem([modality, series_text, image_text])
            item.setToolTip(0, f"Modalidad DICOM: {modality}")
            item.setToolTip(1, series_text)
            item.setToolTip(
                2,
                f"{image_count} imagen(es) de {len(files)} objeto(s) DICOM"
                if image_count
                else f"Sin PixelData · {len(files)} objeto(s) DICOM",
            )
            item.setData(0, Qt.ItemDataRole.UserRole, uid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            self.series_tree.addTopLevelItem(item)
            first_item = first_item or item
            if image_count and first_image_item is None:
                first_image_item = item
        rut = scan["rut"] or "No detectado"
        original = sorted(scan["names"])[0] if scan["names"] else ""
        patient_id = sorted(scan["patient_ids"])[0] if scan["patient_ids"] else ""
        self.rut_value.setText(rut)
        self.original_name.setText(original or "No informado")
        self.new_name.setText(original)
        self._update_patient_name_warning()
        readable_name = re.sub(r"\s+", " ", original.replace("^", " ")).strip()
        self.patient_banner.setText(" · ".join(value for value in (rut or patient_id, readable_name) if value) or "Paciente DICOM")
        self.date_combo.clear()
        for date, count in scan["dates"]:
            self.date_combo.addItem(f"{date} (n={count})", date)
        self.output_base.setText(str(get_dicom_export_dir()))
        self.save_button.setEnabled(False)
        self.details_text.setPlainText(self._scan_summary(scan))
        if first_image_item or first_item:
            self.series_tree.setCurrentItem(first_image_item or first_item)
        self.tabs.setCurrentIndex(0)
        self.update_output_preview()

    @staticmethod
    def _scan_summary(scan: dict[str, Any]) -> str:
        lines = [
            f"Carpeta: {scan['folder']}",
            f"Archivos DICOM detectados: {len(scan['files'])}",
            f"Series: {len(scan['series_map'])}",
            f"Objetos con imagen: {scan.get('pixel_files', 0)}",
            f"Objetos sin PixelData: {scan.get('non_pixel_files', 0)}",
            f"Archivos técnicos incompletos omitidos: {scan.get('ignored_technical_files', 0)}",
            f"RUT detectado: {scan['rut'] or '(no válido)'}",
            "",
            "PatientName encontrados:",
        ]
        lines.extend(f"  - {value}" for value in sorted(scan["names"]))
        lines.extend(["", "Tags de fecha utilizados:"])
        lines.extend(f"  - {tag}: {count}" for tag, count in scan["date_tags"].most_common())
        lines.extend(["", "Transfer Syntax de las imágenes:"])
        lines.extend(
            f"  - {syntax}: {count}"
            for syntax, count in Counter(scan.get("transfer_syntaxes", {})).most_common()
        )
        lines.extend(["", "Fechas detectadas:"])
        lines.extend(f"  - {date}: {count}" for date, count in scan["dates"])
        lines.extend(
            [
                "",
                f"Sin fecha válida: {scan['missing_dates']}",
                f"Errores de lectura de cabecera: {scan['read_errors']}",
                f"Advertencias de lectura toleradas: {scan.get('read_warnings', 0)}",
                "",
                "Regla de edición: únicamente PatientName; el RUT solo nombra la carpeta de salida.",
            ]
        )
        return "\n".join(lines)

    @Slot(QTreeWidgetItem, QTreeWidgetItem)
    def select_series(self, current, _previous):
        if current is None or not self.scan:
            return
        self.current_uid = current.data(0, Qt.ItemDataRole.UserRole)
        meta = self.scan["series_meta"].get(self.current_uid, {})
        modality = (meta.get("Modality") or "No indicada").strip()
        image_count = len(self.scan.get("series_image_map", {}).get(self.current_uid, []))
        image_summary = f"{image_count} imagen(es)" if image_count else "sin PixelData"
        self.series_detail.setText(
            f"Serie seleccionada: {modality} · {_series_label(meta)} · {image_summary}"
        )
        self.current_slice = 0
        self.show_slice()

    def show_slice(self):
        if not self.scan or not self.current_uid:
            return
        files = self.scan.get("series_image_map", {}).get(self.current_uid, [])
        if not files:
            meta = self.scan["series_meta"].get(self.current_uid, {})
            self.graphics.pixmap_item.setPixmap(QPixmap())
            self.graphics.scene().setSceneRect(0, 0, 1, 1)
            self.image_counter.setText("Sin imagen")
            self.viewer_status.setText(_non_image_series_message(meta))
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        self.current_slice = max(0, min(self.current_slice, len(files) - 1))
        self.image_counter.setText(f"{self.current_slice + 1} / {len(files)}")
        can_step = len(files) > 1
        self.previous_button.setEnabled(can_step)
        self.next_button.setEnabled(can_step)
        try:
            dataset = pydicom.dcmread(files[self.current_slice], force=True)
            image = dicom_to_pil(dataset).convert("RGBA")
            pixmap = QPixmap.fromImage(ImageQt(image))
            self.graphics.set_pixmap(pixmap)
            self.viewer_status.setText("Imagen DICOM disponible.")
        except Exception as exc:
            self.graphics.pixmap_item.setPixmap(QPixmap())
            self.graphics.scene().setSceneRect(0, 0, 1, 1)
            detail = (
                str(exc).strip().splitlines()[0][:240]
                if str(exc).strip()
                else type(exc).__name__
            )
            self.viewer_status.setText(
                "No se pudo mostrar este archivo DICOM. Puede estar incompleto, dañado o usar "
                f"una compresión no compatible. Detalle: {detail}"
            )

    def step_slice(self, delta: int):
        if not self.scan or not self.current_uid:
            return
        files = self.scan.get("series_image_map", {}).get(self.current_uid, [])
        if not files:
            return
        self.current_slice = max(0, min(self.current_slice + delta, len(files) - 1))
        self.show_slice()

    def mark_series(self, state: Qt.CheckState):
        for index in range(self.series_tree.topLevelItemCount()):
            self.series_tree.topLevelItem(index).setCheckState(0, state)

    def choose_output(self):
        selected = QFileDialog.getExistingDirectory(
            self,
            "Carpeta base de salida",
            self.output_base.text().strip() or str(get_dicom_export_dir()),
        )
        if selected:
            self.output_base.setText(selected)

    def selected_series(self) -> list[str]:
        selected = []
        for index in range(self.series_tree.topLevelItemCount()):
            item = self.series_tree.topLevelItem(index)
            if item.checkState(0) == Qt.CheckState.Checked:
                selected.append(item.data(0, Qt.ItemDataRole.UserRole))
        return selected

    def update_output_preview(self):
        if not self.scan:
            self.output_preview.setText("Seleccione una carpeta DICOM")
            return
        date = self.date_combo.currentData() or "(fecha)"
        rut = self.scan["rut"] or "(RUT)"
        base = self.output_base.text().strip() or "(carpeta de salida)"
        self.output_preview.setText(os.path.join(base, sanitize_folder_name(f"{rut}_{date}")))

    def _patient_name_separator_removed(self, candidate: str | None = None) -> bool:
        """Indica si el separador PN original fue eliminado durante la edición."""
        original = self.original_name.text().strip()
        edited = self.new_name.text().strip() if candidate is None else candidate.strip()
        return "^" in original and "^" not in edited

    @Slot()
    @Slot(str)
    def _update_patient_name_warning(self, _text: str = ""):
        separator_removed = self._patient_name_separator_removed()
        if separator_removed:
            title = "⚠  SE ELIMINÓ EL SEPARADOR DICOM ^"
            message = (
                "Restáurelo antes de guardar. Sin ^, otros sistemas pueden interpretar "
                "incorrectamente los apellidos y los nombres del paciente."
            )
        else:
            title = "⚠  NO ELIMINE EL SÍMBOLO ^"
            message = (
                "En DICOM, ^ separa los apellidos de los nombres. Ejemplo: "
                "MARTINEZ MORALES^MARIELA CRISTINA. Manténgalo al editar."
            )

        self.name_warning_title.setText(title)
        self.name_warning_text.setText(message)
        for widget in (self.name_warning_card, self.name_warning_title, self.name_warning_text):
            widget.setProperty("invalid", separator_removed)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.name_warning_card.update()

    def save_study(self):
        if not self.scan or self._save_worker is not None or self._scan_worker is not None:
            return
        new_name = self.new_name.text().strip()
        date = self.date_combo.currentData() or ""
        base = self.output_base.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Nombre vacío", "El nuevo PatientName no puede estar vacío.")
            return
        if self._patient_name_separator_removed(new_name):
            if not _question(
                self,
                "Advertencia: falta el separador DICOM ^",
                "El nombre original contiene el símbolo ^, pero el nombre editado no.\n\n"
                f"Original: {self.original_name.text()}\n"
                f"Editado: {new_name}\n\n"
                "Sin ese separador, otros sistemas pueden interpretar todo el texto como "
                "apellidos o mostrar el nombre incorrectamente.\n\n"
                "¿Está seguro de que desea guardar de todas maneras?",
            ):
                return
        if not validate_iso_date(date):
            QMessageBox.warning(self, "Fecha inválida", "Seleccione una fecha válida del estudio.")
            return
        if not self.scan["rut"]:
            QMessageBox.warning(self, "RUT no detectado", "Se necesita un RUT válido para nombrar la carpeta de salida.")
            return
        if not base:
            self.choose_output()
            base = self.output_base.text().strip()
            if not base:
                return
        selected = self.selected_series()
        if not selected:
            if not _question(self, "Sin series marcadas", "No hay series marcadas. ¿Desea guardar todas las series?"):
                return
            selected = list(self.scan["series_map"])

        folder_name = sanitize_folder_name(f"{self.scan['rut']}_{date}")
        source = os.path.abspath(self.scan["folder"])
        output = os.path.abspath(os.path.join(base, folder_name))
        if os.path.normcase(os.path.basename(source.rstrip(os.sep))) == os.path.normcase(folder_name):
            if os.path.normcase(output) == os.path.normcase(os.path.join(source, folder_name)):
                output = source
        same = os.path.normcase(source) == os.path.normcase(output)
        try:
            inside_source = os.path.commonpath([source, output]) == source
            source_inside_output = os.path.commonpath([source, output]) == output
        except ValueError:
            inside_source = False
            source_inside_output = False
        if inside_source and not same:
            QMessageBox.critical(self, "Destino inválido", "La salida no puede estar dentro de la carpeta DICOM de origen.")
            return
        if source_inside_output and not same:
            QMessageBox.critical(
                self,
                "Destino inválido",
                "La carpeta de salida no puede contener la carpeta DICOM de origen, porque al reemplazarla también borraría el original.",
            )
            return
        if same:
            if not _question(
                self,
                "Reemplazar carpeta actual",
                "La salida coincide con la carpeta cargada. Se creará una copia temporal y, solo si termina correctamente, reemplazará la carpeta actual.\n\n¿Continuar?",
            ):
                return
        elif os.path.exists(output) and not os.path.isdir(output):
            QMessageBox.critical(self, "Destino inválido", "La ruta de salida existe, pero no es una carpeta.")
            return
        elif os.path.exists(output):
            if not _question(
                self,
                "La carpeta ya existe",
                f"Para evitar mezclar estudios se reemplazará por completo:\n{output}\n\n¿Continuar?",
            ):
                return

        self._save_result = None
        self._save_error = ""
        self.save_button.setEnabled(False)
        self.choose_folder_button.setEnabled(False)
        self.scan_progress.show()
        worker = FunctionWorker(export_p3_study, self.scan, date, new_name, base, selected)
        self._save_worker = worker
        worker.signals.result.connect(self._capture_save_result)
        worker.signals.error.connect(self._capture_save_error)
        worker.signals.finished.connect(self._save_finished)
        self.pool.start(worker)

    @Slot(object)
    def _capture_save_result(self, result):
        self._save_result = result

    @Slot(str)
    def _capture_save_error(self, detail: str):
        self._save_error = detail

    def _save_finished(self):
        result = self._save_result
        error = self._save_error
        self._save_worker = None
        self.scan_progress.hide()
        self.choose_folder_button.setEnabled(True)
        self.save_button.setEnabled(
            bool(self.scan and self.scan.get("rut") and self.scan.get("dates"))
        )
        if error:
            QTimer.singleShot(0, lambda: _friendly_error(self, "No se pudo guardar el estudio", error))
        elif result:
            QTimer.singleShot(0, lambda: self.save_complete(result))

    @Slot(object)
    def save_complete(self, result):
        message = (
            f"Carpeta: {result['output']}\n\n"
            f"Series: {result['series']}\nArchivos procesados: {result['processed']}\n"
            f"Editados: {result['edited']}\nCopiados sin editar: {result['copied']}\n"
            f"Faltantes: {result['missing']}\nFallidos: {result['failed']}"
        )
        warnings = result["copied"] + result["missing"] + result["failed"]
        if result.get("backup_left"):
            warnings += 1
            message += f"\n\nNo se pudo borrar el respaldo anterior:\n{result['backup_left']}"
        if warnings:
            QMessageBox.warning(
                self,
                "Estudio guardado con advertencias",
                message + "\n\nRevise estos conteos antes de utilizar la copia.",
            )
        else:
            QMessageBox.information(self, "Estudio guardado", message)


def run_p4_pipeline(
    source: str,
    destination: str,
    messages: queue.Queue,
    replace_nonempty_confirmed: bool,
) -> None:
    """Ejecuta P4 en una carpeta temporal y publica la salida solo al finalizar."""
    source = os.path.abspath(source)
    destination = os.path.abspath(destination)
    if not os.path.isdir(source):
        raise FileNotFoundError(f"La carpeta de origen ya no existe: {source}")
    if os.path.islink(destination):
        raise ValueError("Por seguridad, el destino no puede ser un enlace simbólico.")
    if os.path.exists(destination) and not os.path.isdir(destination):
        raise NotADirectoryError(f"La ruta de destino no es una carpeta: {destination}")

    source_real = os.path.realpath(source)
    destination_real = os.path.realpath(destination)
    try:
        related = (
            os.path.commonpath([source_real, destination_real]) == source_real
            or os.path.commonpath([source_real, destination_real]) == destination_real
        )
    except ValueError:
        related = False
    if related:
        raise ValueError("Las carpetas de origen y destino no pueden contenerse entre sí.")

    if os.path.isdir(destination):
        destination_not_empty = any(os.scandir(destination))
        if destination_not_empty and not replace_nonempty_confirmed:
            raise RuntimeError(
                "El destino recibió archivos después de la revisión. No se borró nada; vuelva a iniciar para confirmarlo."
            )
    parent = os.path.dirname(destination.rstrip(os.sep))
    name = os.path.basename(destination.rstrip(os.sep))
    os.makedirs(parent, exist_ok=True)
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    staging = os.path.join(parent, f".{name}__TMP__{token}")
    backup = os.path.join(parent, f".{name}__BAK__{token}")

    class MessageProxy:
        def __init__(self):
            self.done = False
            self.error = False

        def put(self, message):
            if isinstance(message, tuple) and message:
                kind, payload = message[0], message[1]
                if kind == "DONE":
                    self.done = True
                    payload = dict(payload)
                    payload["dst_root"] = destination
                    messages.put((kind, payload))
                    return
                if kind == "ERROR":
                    self.error = True
            if isinstance(message, str):
                message = message.replace(staging, destination)
            messages.put(message)

    proxy = MessageProxy()
    process_all(
        source,
        staging,
        True,   # GDCM siempre activo (si la herramienta está disponible)
        True,   # conservar siempre los no DICOM en __NO_DICOM__
        False,  # anonimización retirada por no ser confiable en este flujo
        proxy,
    )
    if proxy.error:
        shutil.rmtree(staging, ignore_errors=True)
        return
    if not proxy.done:
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("El procesamiento terminó sin entregar un resultado verificable.")

    try:
        for report_name in ("reporte_archivos.csv", "reporte_series.csv"):
            report = Path(staging) / report_name
            content = report.read_text(encoding="utf-8")
            report.write_text(content.replace(staging, destination), encoding="utf-8")

        # Se vuelve a revisar justo antes del intercambio para no borrar datos
        # que hayan aparecido después de la confirmación visual.
        if os.path.exists(destination):
            if os.path.islink(destination) or not os.path.isdir(destination):
                raise RuntimeError("El destino cambió durante el proceso y ya no es una carpeta segura.")
            if any(os.scandir(destination)) and not replace_nonempty_confirmed:
                raise RuntimeError(
                    "El destino recibió archivos durante el proceso. La salida anterior se conservó sin cambios."
                )
            os.rename(destination, backup)
        try:
            os.rename(staging, destination)
        except Exception:
            if not os.path.exists(destination) and os.path.exists(backup):
                os.rename(backup, destination)
            raise
        if os.path.isdir(backup):
            try:
                shutil.rmtree(backup)
            except OSError:
                messages.put(f"[AVISO] No se pudo eliminar el respaldo temporal: {backup}")
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if not os.path.exists(destination) and os.path.exists(backup):
            os.rename(backup, destination)
        raise


class P4Page(BasePage):
    def __init__(self, pool: QThreadPool):
        super().__init__(
            "P4 · Compatibilidad Eclipse",
            "Compatibilizar CD para Eclipse",
            "Admite CDs del Hospital Regional de Concepción (HGGB) y de otros centros, como Clínica Los Andes o Clínica Biobío.",
        )
        self.pool = pool
        self.messages: queue.Queue = queue.Queue()
        self.finished_payload: dict[str, Any] | None = None
        self._process_worker: FunctionWorker | None = None
        self._process_error = ""
        self._queue_error = ""
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll_messages)

        split = QSplitter(Qt.Orientation.Horizontal)
        settings, settings_layout = card()
        settings.setMinimumWidth(440)
        settings_layout.addWidget(label("Origen y destino", "SectionTitle"))
        settings_layout.addWidget(label("Los resultados se preparan aparte y se publican solo cuando el proceso termina correctamente.", "Muted", True))
        settings_layout.addWidget(label("Carpeta origen del CD"))
        src_row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setPlaceholderText("Carpeta montada o copia local del CD")
        self.source_pick = button("Elegir…")
        self.source_pick.clicked.connect(self.pick_source)
        src_row.addWidget(self.source, 1)
        src_row.addWidget(self.source_pick)
        settings_layout.addLayout(src_row)
        settings_layout.addWidget(label("Carpeta destino para Eclipse"))
        dst_row = QHBoxLayout()
        self.destination = QLineEdit()
        self.destination.setPlaceholderText("Carpeta de trabajo vacía")
        self.destination_pick = button("Elegir…")
        self.destination_pick.clicked.connect(self.pick_destination)
        dst_row.addWidget(self.destination, 1)
        dst_row.addWidget(self.destination_pick)
        settings_layout.addLayout(dst_row)
        settings_layout.addSpacing(8)
        settings_layout.addWidget(label("Procesamiento automático", "SectionTitle"))
        automatic, automatic_layout = card(margins=(14, 14, 14, 14))
        automatic_layout.addWidget(label("✓  GDCM activo", "CardTitle"))
        automatic_layout.addWidget(
            label(
                "Se utilizará automáticamente para descomprimir DICOM cuando corresponda.",
                "Caption",
                True,
            )
        )
        automatic_layout.addWidget(label("✓  Archivos no DICOM separados", "CardTitle"))
        automatic_layout.addWidget(
            label(
                "Siempre se conservarán dentro de la carpeta __NO_DICOM__, sin mezclarlos con Eclipse.",
                "Caption",
                True,
            )
        )
        settings_layout.addWidget(automatic)
        warning, warning_layout = card(margins=(14, 14, 14, 14))
        warning_layout.addWidget(label("CONTROL DE SEGURIDAD", "Eyebrow"))
        warning_layout.addWidget(
            label(
                "Si el destino contiene archivos, el programa mostrará la ruta exacta y pedirá confirmación antes de reemplazarlo.",
                "Muted",
                True,
            )
        )
        settings_layout.addWidget(warning)
        settings_layout.addStretch()
        self.run_button = button("Revisar y comenzar", primary=True)
        self.run_button.clicked.connect(self.run_process)
        settings_layout.addWidget(self.run_button)
        split.addWidget(settings)

        log_card, log_layout = card()
        log_head = QHBoxLayout()
        log_head.addWidget(label("Registro del proceso", "SectionTitle"))
        log_head.addStretch()
        self.state_badge = label("ESPERANDO", "Badge")
        log_head.addWidget(self.state_badge)
        log_layout.addLayout(log_head)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("El detalle de clasificación, conversión y reportes aparecerá aquí.")
        log_layout.addWidget(self.log, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        log_layout.addWidget(self.progress)
        split.addWidget(log_card)
        split.setSizes([470, 730])
        self.content.addWidget(split)

    def pick_source(self):
        path = QFileDialog.getExistingDirectory(self, "Carpeta origen del CD", str(Path.home()))
        if path:
            self.source.setText(path)

    def pick_destination(self):
        path = QFileDialog.getExistingDirectory(self, "Carpeta destino", str(get_dicom_export_dir()))
        if path:
            self.destination.setText(path)

    def run_process(self):
        if self._process_worker is not None:
            return
        source = self.source.text().strip()
        destination = self.destination.text().strip()
        if not source or not os.path.isdir(source):
            QMessageBox.warning(self, "Origen inválido", "Seleccione una carpeta de origen válida.")
            return
        if not destination:
            QMessageBox.warning(self, "Destino inválido", "Seleccione una carpeta de destino.")
            return
        source_abs = os.path.abspath(source)
        destination_abs = os.path.abspath(destination)
        source_real = os.path.realpath(source_abs)
        destination_real = os.path.realpath(destination_abs)
        if os.path.normcase(source_real) == os.path.normcase(destination_real):
            QMessageBox.critical(self, "Destino inválido", "El destino no puede ser la misma carpeta que el origen.")
            return
        try:
            destination_inside_source = os.path.commonpath([source_real, destination_real]) == source_real
            source_inside_destination = os.path.commonpath([source_real, destination_real]) == destination_real
        except ValueError:
            destination_inside_source = False
            source_inside_destination = False
        if destination_inside_source:
            QMessageBox.critical(self, "Destino inválido", "El destino no puede estar dentro de la carpeta de origen.")
            return
        if source_inside_destination:
            QMessageBox.critical(
                self,
                "Destino inválido",
                "El origen no puede estar dentro del destino: al preparar la salida también se borraría el CD de origen.",
            )
            return
        if os.path.islink(destination_abs):
            QMessageBox.critical(self, "Destino inválido", "Por seguridad, el destino no puede ser un enlace simbólico.")
            return
        if os.path.exists(destination_abs) and not os.path.isdir(destination_abs):
            QMessageBox.critical(self, "Destino inválido", "La ruta de destino existe, pero no es una carpeta.")
            return

        replace_nonempty_confirmed = False
        if os.path.isdir(destination_abs):
            try:
                not_empty = any(os.scandir(destination_abs))
            except Exception:
                not_empty = True
            if not_empty:
                if not _question(
                    self,
                    "Confirmar reemplazo del destino",
                    "Para evitar mezclar pacientes, esta carpeta se reemplazará por completo solo si el procesamiento termina correctamente:\n\n"
                    f"{destination_abs}\n\n¿Desea continuar?",
                ):
                    return
                replace_nonempty_confirmed = True

        self.log.clear()
        self.messages = queue.Queue()
        self.finished_payload = None
        self._process_error = ""
        self._queue_error = ""
        self.run_button.setEnabled(False)
        self.source.setEnabled(False)
        self.destination.setEnabled(False)
        self.source_pick.setEnabled(False)
        self.destination_pick.setEnabled(False)
        self.progress.show()
        self.state_badge.setText("PROCESANDO")
        self.timer.start()
        worker = FunctionWorker(
            run_p4_pipeline,
            source_abs,
            destination_abs,
            self.messages,
            replace_nonempty_confirmed,
        )
        self._process_worker = worker
        worker.signals.error.connect(self._capture_process_error)
        worker.signals.finished.connect(self.worker_finished)
        self.pool.start(worker)

    @Slot(str)
    def _capture_process_error(self, detail: str):
        self._process_error = detail

    def poll_messages(self):
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            if isinstance(message, tuple) and message:
                if message[0] == "DONE":
                    self.finished_payload = message[1]
                elif message[0] == "ERROR":
                    self.log.appendPlainText(f"[ERROR] {message[1]}")
                    self._queue_error = str(message[1])
                    self.state_badge.setText("ERROR")
            else:
                self.log.appendPlainText(str(message))

    def worker_finished(self):
        self.poll_messages()
        self.timer.stop()
        self.progress.hide()
        self._process_worker = None
        self.run_button.setEnabled(True)
        self.source.setEnabled(True)
        self.destination.setEnabled(True)
        self.source_pick.setEnabled(True)
        self.destination_pick.setEnabled(True)
        error = self._process_error or self._queue_error
        if error:
            self.state_badge.setText("ERROR")
            QTimer.singleShot(0, lambda: _friendly_error(self, "Error de procesamiento", error))
        elif self.finished_payload:
            self.state_badge.setText("COMPLETADO")
            payload = self.finished_payload
            QTimer.singleShot(
                0,
                lambda: QMessageBox.information(
                    self,
                    "Proceso completado",
                    f"Destino: {payload.get('dst_root', '')}\n\n"
                    f"Copiados: {payload.get('cnt_ok', 0)}\n"
                    f"Convertidos: {payload.get('cnt_conv', 0)}\n"
                    f"Rechazados: {payload.get('cnt_rej', 0)}\n"
                    f"No DICOM: {payload.get('cnt_nd', 0)}",
                ),
            )
        elif self.state_badge.text() != "ERROR":
            self.state_badge.setText("FINALIZADO")


class ClinicalCommandCenter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Centro de Comando Clínico")
        self.setMinimumSize(1050, 700)
        self.resize(1360, 850)
        self.setWindowIcon(asset_icon("radiotherapy_logo.png"))
        self.pool = QThreadPool.globalInstance()
        self._page_animation: QPropertyAnimation | None = None
        self._page_effect: QGraphicsOpacityEffect | None = None
        self._animated_page: QWidget | None = None
        self.navigation_buttons: dict[str, QPushButton] = {}

        root = QWidget()
        root.setObjectName("AppRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_topbar())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_sidebar())
        self.pages = QStackedWidget()
        body_layout.addWidget(self.pages, 1)
        layout.addWidget(body, 1)
        self.setCentralWidget(root)

        self.home = HomePage()
        self.p1 = P1Page(self.pool)
        self.p2 = P2Page()
        self.p3 = P3Page(self.pool)
        self.p4 = P4Page(self.pool)
        self.page_map = {"home": self.home, "p1": self.p1, "p2": self.p2, "p3": self.p3, "p4": self.p4}
        for page in self.page_map.values():
            self.pages.addWidget(page)
        self.home.open_tool.connect(self.navigate)
        for page in (self.p1, self.p2, self.p3, self.p4):
            page.go_home.connect(lambda: self.navigate("home"))

        home_action = QAction("Inicio", self)
        home_action.setShortcut("Alt+Home")
        home_action.triggered.connect(lambda: self.navigate("home"))
        self.addAction(home_action)

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(238)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 22, 16, 18)
        side.setSpacing(7)

        side.addWidget(label("NAVEGACIÓN", "Eyebrow"))
        navigation = (
            ("home", "⌂   Inicio"),
            ("p1", "P1   Extraer datos PDF"),
            ("p2", "P2   Visor de estructuras"),
            ("p3", "P3   Editor DICOM"),
            ("p4", "P4   Compatibilidad Eclipse"),
        )
        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        for key, text_value in navigation:
            nav_button = QPushButton(text_value)
            nav_button.setObjectName("NavButton")
            nav_button.setCheckable(True)
            nav_button.setCursor(Qt.CursorShape.PointingHandCursor)
            nav_button.clicked.connect(lambda _checked=False, page_key=key: self.navigate(page_key))
            self.navigation_group.addButton(nav_button)
            self.navigation_buttons[key] = nav_button
            side.addWidget(nav_button)
        self.navigation_buttons["home"].setChecked(True)

        side.addStretch()
        privacy = QFrame()
        privacy.setObjectName("PrivacyCard")
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.setContentsMargins(13, 13, 13, 13)
        privacy_layout.setSpacing(6)
        privacy_layout.addWidget(label("PROCESAMIENTO LOCAL", "Eyebrow"))
        privacy_layout.addWidget(
            label(
                "Los archivos clínicos se procesan localmente y no se envían a servicios externos.",
                "Caption",
                True,
            )
        )
        side.addWidget(privacy)
        version = label(f"Versión {APP_VERSION}", "Caption")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(version)
        return sidebar

    def _build_topbar(self):
        topbar = QFrame()
        topbar.setObjectName("TopBar")
        topbar.setFixedHeight(78)
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(22, 10, 22, 10)
        layout.setSpacing(14)
        layout.addWidget(logo_mark_label(52))
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(label("Centro de Comando Clínico", "AppName"))
        title_box.addWidget(label("Flujo de trabajo para radioterapia", "Caption"))
        layout.addLayout(title_box)
        layout.addStretch()
        return topbar

    def _animate_page_entry(self, page: QWidget):
        if self._page_animation is not None:
            self._page_animation.stop()
        if self._animated_page is not None and self._page_effect is not None:
            if self._animated_page.graphicsEffect() is self._page_effect:
                self._animated_page.setGraphicsEffect(None)

        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.18)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(190)
        animation.setStartValue(0.18)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._page_animation = animation
        self._page_effect = effect
        self._animated_page = page

        def finish_animation():
            if page.graphicsEffect() is effect:
                page.setGraphicsEffect(None)
            if self._page_animation is animation:
                self._page_animation = None
                self._page_effect = None
                self._animated_page = None

        animation.finished.connect(finish_animation)
        animation.start()

    @Slot(str)
    def navigate(self, key: str):
        page = self.page_map.get(key)
        if page is not None:
            for page_key, nav_button in self.navigation_buttons.items():
                nav_button.setChecked(page_key == key)
            self.pages.setCurrentWidget(page)
            self._animate_page_entry(page)
            if not self.isMaximized():
                self.showMaximized()

    def closeEvent(self, event):
        active = []
        if self.p1._extract_worker is not None:
            active.append("extracción PDF")
        if self.p1._export_worker is not None:
            active.append("exportación PDF")
        if self.p3._scan_worker is not None:
            active.append("lectura DICOM")
        if self.p3._save_worker is not None:
            active.append("guardado DICOM")
        if self.p4._process_worker is not None:
            active.append("compatibilización para Eclipse")
        if active:
            QMessageBox.warning(
                self,
                "Proceso en curso",
                "Espere a que termine antes de cerrar el programa para evitar una salida incompleta.\n\n"
                f"En ejecución: {', '.join(active)}.",
            )
            event.ignore()
            return
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Centro de Comando Clínico")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Centro Clínico")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    window = ClinicalCommandCenter()
    # GNOME puede ignorar el maximizado solicitado antes de terminar de mapear
    # la ventana. Primero se muestra y, cuando Mutter ya la registró, se envía
    # la orden real. Esto cubre también el botón Run de los editores Python.
    window.show()
    QTimer.singleShot(150, window.showMaximized)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
