"""Orquestador multiformato para las órdenes de trabajo de P1.

Conserva el extractor UNIQUE existente y reincorpora el motor ECM que antes
utilizaba el launcher para Halcyon y Control de Calidad.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from P1_ExtraerDatosPDF import ExtractorPDF
from P5_Extractor_Halcyon import ExtractorHalcyon, _read_pdf_text


HERE = Path(__file__).resolve().parent
HALCYON_MAP_FILE = HERE / "halcyon_serial_map.json"

OT_KIND_OPTIONS = (
    ("AUTO", "Detectar automáticamente"),
    ("UNIQUE", "UNIQUE · iClinic"),
    ("HALCYON_1", "HALCYON 1 · ECM"),
    ("HALCYON_2", "HALCYON 2 · ECM"),
    ("CONTROL_DE_CALIDAD", "Control de Calidad · ECM"),
    ("ECM", "Otra OT de ECM"),
)

OT_KIND_LABELS = dict(OT_KIND_OPTIONS) | {
    "HALCYON": "HALCYON · equipo por confirmar",
    "SIEMENS": "SIEMENS · sin extractor estructurado",
    "UNKNOWN": "Formato no reconocido",
}

UNIQUE_SECTIONS = {
    "Información general": ["N°", "FECHA", "FECHA PROGRAMADA"],
    "Tiempos": [
        "FECHA Y HORA DE INICIO",
        "FECHA Y HORA DE FINALIZACIÓN",
        "DURACIÓN ESTIMADA",
        "TIEMPO DE EJECUCIÓN",
        "TIEMPO REAL DE PARO DEL ACTIVO",
    ],
    "Detalles": ["DESCRIPCIÓN", "TIPO DE TAREA", "NOTAS"],
    "Subtareas": [
        "DESCRIPCIÓN DE LA FALLA O SINTOMA",
        "ACCIONES REALIZADAS",
        "ACCIONES PENDIENTES",
        "RESPUESTOS SOLICITADOS",
        "REVISION DE LAS TAREAS DE BAJA FRECUENCIA",
        "REVISION Y SEGUIMIENTO DE LAS RECOMENDACIONES",
        "HORAS DE FILAMENTO Y BEAM",
        "REPUESTOS A SOLICITAR",
        "OBSERVACIONES",
    ],
    "Otros": ["DURACION REGISTRADA"],
}

ECM_SECTIONS = {
    "Metadatos": ["_N_TAREA"],
    "Datos de la tarea": [
        "Trabajador",
        "Fecha y Hora Programada",
        "Duración Estimada",
        "Prioridad",
        "Tipo de Tarea",
        "Descripción",
    ],
    "Equipos a revisar": [
        "ID",
        "Nombre",
        "Marca y Modelo",
        "Número de Serie",
        "Comentario",
        "Formulario",
        "Piezas",
    ],
    "Informe de trabajo": [
        "Área",
        "Tipo de Servicio",
        "Descripción del trabajo realizado",
        "Estuvo detenido el equipo",
        "Clasificación de la falla",
        "Equipo queda operativo",
        "Ha solicitado repuestos desde alguna bodega",
        "Indique bodega de origen",
        "Tiempo de respuesta telefónica con cliente",
        "Tiempo de repuesta en sitio",
        "Fecha y hora de inicio de trabajo",
        "Fecha y hora de término de trabajo",
        "Tiempo de detención de equipo(hh:mm)",
        "Se trata de un trabajo remoto o con asistencia telefónica",
        "Importante",
    ],
}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _load_halcyon_map() -> dict[str, str]:
    try:
        payload = json.loads(HALCYON_MAP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result = {}
    for serial, kind in (payload or {}).items():
        serial = re.sub(r"[^A-Z0-9]", "", str(serial).upper())
        kind = str(kind).upper()
        if serial and re.fullmatch(r"HALCYON_\d+", kind):
            result[serial] = kind
    return result


def find_halcyon_serial(text: str) -> str:
    match = re.search(r"\bHAL[\s-]?(\d{3,6})\b", text or "", flags=re.IGNORECASE)
    return f"HAL{match.group(1)}".upper() if match else ""


def classify_ot_text(text: str, filename: str = "") -> str | None:
    """Clasifica solo cuando existen señales suficientemente específicas."""
    blob = _normalize(f"{filename}\n{text}")

    if any(token in blob for token in ("acelerador unique", "varian unique", "iclinic", " unique ")):
        return "UNIQUE"
    if re.search(r"\bu[nm][i1l]que\b", blob, flags=re.IGNORECASE):
        return "UNIQUE"

    quality_tokens = (
        "equipos de control de calidad y posicionamiento",
        "control de calidad y accesorios",
        "control de calidad",
        "ptwecm",
        "pmi equipo de control",
    )
    if any(token in blob for token in quality_tokens) and "halcyon" not in blob:
        return "CONTROL_DE_CALIDAD"

    if any(token in blob for token in ("halcyon 1", "halcyon_1", "halcyon-1")):
        return "HALCYON_1"
    if any(token in blob for token in ("halcyon 2", "halcyon_2", "halcyon-2")):
        return "HALCYON_2"

    serial = find_halcyon_serial(blob)
    mapped = _load_halcyon_map().get(serial)
    if mapped:
        return mapped
    if "halcyon" in blob or serial:
        return "HALCYON"

    siemens_tokens = (
        "siemens healthineers",
        "siemens healthcare",
        "reporte de servicio tecnico",
        "somatom",
        "teamplay fleet",
    )
    if any(token in blob for token in siemens_tokens):
        return "SIEMENS"

    # FieldBeat e "Información de la Tarea" identifican el formulario ECM,
    # pero no necesariamente un Halcyon.
    if any(
        token in blob
        for token in (
            "fieldbeat",
            "informacion de la tarea",
            "ingenieria en electronica, computacion y medicina",
            "equipos a revisar",
        )
    ):
        return "ECM"
    return None


def _month_from_ecm(data: dict[str, Any]) -> str:
    raw = str(
        data.get("Fecha y Hora Programada")
        or data.get("Fecha y hora de inicio de trabajo")
        or ""
    ).strip()
    for fmt in ("%d/%m/%y - %H:%M", "%d/%m/%Y - %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%y %H:%M"):
        try:
            return f"{datetime.strptime(raw, fmt).month:02d}"
        except ValueError:
            pass
    match = re.search(r"\b\d{1,2}/(\d{1,2})/\d{2,4}\b", raw)
    if match:
        month = int(match.group(1))
        if 1 <= month <= 12:
            return f"{month:02d}"
    return "00"


def _ecm_output_metadata(kind: str, data: dict[str, Any]) -> tuple[str, str, str]:
    number = re.sub(r"\D", "", str(data.get("_N_TAREA", "")))
    month = _month_from_ecm(data)
    if kind in {"HALCYON", "HALCYON_1", "HALCYON_2"}:
        sigla, excel, title = "HAL", "HALCYON.xlsx", "DATOS EXTRAÍDOS - OT HALCYON (ECM)"
    elif kind == "CONTROL_DE_CALIDAD":
        sigla, excel, title = "CC", "CONTROL_DE_CALIDAD.xlsx", "DATOS EXTRAÍDOS - OT CONTROL DE CALIDAD (ECM)"
    else:
        sigla, excel, title = "ECM", "ECM.xlsx", "DATOS EXTRAÍDOS - OT ECM"
    base = f"OT{number}_{sigla}{month}" if number else f"OT_{sigla}{month}"
    return base, excel, title


def analyze_ot_pdf(path: str, requested_kind: str = "AUTO") -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"El PDF seleccionado ya no existe: {source}")
    requested_kind = str(requested_kind or "AUTO").upper()
    allowed = {key for key, _ in OT_KIND_OPTIONS}
    if requested_kind not in allowed:
        raise ValueError(f"Formato de OT no admitido: {requested_kind}")

    classification_text = _read_pdf_text(str(source))
    if not classification_text:
        raise RuntimeError("No fue posible extraer texto desde el PDF seleccionado.")
    detected = classify_ot_text(classification_text, source.name)
    if requested_kind == "AUTO":
        if detected == "SIEMENS":
            raise RuntimeError(
                "Se detectó un reporte SIEMENS. La versión antigua solo lo archivaba y no tenía un extractor de campos; seleccione un formato compatible si corresponde."
            )
        if detected is None:
            raise RuntimeError(
                "No se pudo reconocer el formato de esta OT. Seleccione manualmente UNIQUE, Halcyon, Control de Calidad u otra OT ECM y vuelva a extraer."
            )
        effective_kind = detected
    else:
        effective_kind = requested_kind

    if effective_kind == "UNIQUE":
        extractor = ExtractorPDF()
        unique_text = extractor.extraer_texto_pdf(str(source))
        if not unique_text:
            raise RuntimeError("No fue posible extraer texto UNIQUE desde el PDF seleccionado.")
        data = extractor.extraer_todos_los_datos(unique_text, ruta_pdf=str(source))
        return {
            "engine": "UNIQUE",
            "kind": "UNIQUE",
            "kind_label": OT_KIND_LABELS["UNIQUE"],
            "detected_kind": detected or "UNKNOWN",
            "detected_label": OT_KIND_LABELS.get(detected or "UNKNOWN", detected or "UNKNOWN"),
            "classification_warning": bool(detected and detected != "UNIQUE"),
            "requires_selection": False,
            "data": data,
            "sections": UNIQUE_SECTIONS,
            "title": "DATOS EXTRAÍDOS DEL PDF",
            "excel_name": "UNIQUE.xlsx",
            "category_column": "Categoría",
            "include_missing": False,
            "preserve_pdf_name": False,
        }

    extractor = ExtractorHalcyon()
    data = extractor.extraer_campos(classification_text, ruta_pdf=str(source))
    if requested_kind == "AUTO" and effective_kind == "ECM":
        functional = extractor._detect_ot_category(data)
        if functional == "CONTROL_DE_CALIDAD":
            effective_kind = "CONTROL_DE_CALIDAD"
        elif functional == "HALCYON":
            serial = find_halcyon_serial(classification_text)
            effective_kind = _load_halcyon_map().get(serial, "HALCYON")

    base, excel_name, title = _ecm_output_metadata(effective_kind, data)
    compatible_detection = (
        detected == effective_kind
        or detected == "ECM"
        or (detected == "HALCYON" and effective_kind.startswith("HALCYON_"))
    )
    return {
        "engine": "ECM",
        "kind": effective_kind,
        "kind_label": OT_KIND_LABELS.get(effective_kind, effective_kind),
        "detected_kind": detected or "UNKNOWN",
        "detected_label": OT_KIND_LABELS.get(detected or "UNKNOWN", detected or "UNKNOWN"),
        "classification_warning": bool(requested_kind != "AUTO" and detected and not compatible_detection),
        "requires_selection": effective_kind == "HALCYON",
        "data": data,
        "sections": ECM_SECTIONS,
        "title": title,
        "excel_name": excel_name,
        "category_column": "Sección",
        "include_missing": True,
        "preserve_pdf_name": True,
        "name_base": base,
    }

