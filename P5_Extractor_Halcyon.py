# Motor de extracción ECM reutilizado por P1.
# Extrae campos clave desde OT Halcyon / Control de Calidad / ECM y guarda:
# - PDF ORIGINAL (mismo nombre) en carpeta destino
# - TXT resumen ordenado por secciones en carpeta destino/Resumen
# - Excel HALCYON.xlsx en carpeta destino/Resumen (una hoja por OT)

import sys
import re
import traceback
from config_manager import get_ots_dir
import shutil
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox

import fitz  # PyMuPDF
import pandas as pd


SILENT_MODE = False


def _show_error(title: str, msg: str):
    if SILENT_MODE:
        print(f"[ERROR] {title}: {msg}")
    else:
        messagebox.showerror(title, msg)


def _show_info(title: str, msg: str):
    if SILENT_MODE:
        print(f"[INFO] {title}: {msg}")
    else:
        messagebox.showinfo(title, msg)


def _ask_yes_no(title: str, msg: str, default_yes: bool = True) -> bool:
    if SILENT_MODE:
        return default_yes
    return messagebox.askyesno(title, msg)


def _primer_dir_existente(*candidatos: Path) -> str:
    for c in candidatos:
        if c.exists() and c.is_dir():
            return str(c)
    return str(Path.home())


def _norm_text(s: str) -> str:
    if s is None:
        return ""
    s = str(s).replace("\u00A0", " ")
    s = s.lower()
    s = (s.replace("á", "a").replace("é", "e").replace("í", "i")
           .replace("ó", "o").replace("ú", "u").replace("ñ", "n"))
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _read_pdf_text(ruta_pdf: str) -> str:
    try:
        doc = fitz.open(ruta_pdf)
        parts = []
        for page in doc:
            parts.append(page.get_text("text", sort=True))
        doc.close()
        return "\n".join(parts)
    except Exception:
        return ""


def _clean_value(val: str) -> str:
    if val is None:
        return ""
    val = str(val).replace("\u00A0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in val.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def _collapse_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u00A0", " ")).strip()


def _remove_fieldbeat_footer_lines(text: str) -> str:
    """
    Elimina SOLO pies de página reales:
    - líneas con 'FieldBeat'
    - URLs
    - líneas que son solo paginación tipo '1/3'
    No debe borrar líneas con fechas como 16/02/2026.
    """
    if not text:
        return ""

    out = []
    for ln in text.splitlines():
        s = ln.strip()
        n = _norm_text(ln)

        if "fieldbeat" in n:
            continue
        if "https://teams.fieldbeat.com/" in ln.lower():
            continue
        if re.fullmatch(r"\d+\s*/\s*\d+", s):
            continue

        out.append(ln)

    return "\n".join(out)


def _find_line_index(lines: list[str], needle: str) -> int:
    """
    Busca una línea cuyo inicio corresponda al label.
    Evita falsos positivos como 'Área' dentro de 'Tarea'.
    """
    needle_n = _norm_text(needle)
    for i, ln in enumerate(lines):
        if _norm_text(ln).startswith(needle_n):
            return i
    return -1


def _extract_rightmost_chunk_from_line(line: str, label: str) -> str:
    """
    Extrae el valor que está a la derecha del label en la misma línea.
    Ej:
    'Área    Imagenología y Radioterapia' -> 'Imagenología y Radioterapia'
    """
    if not line:
        return ""

    s = line.rstrip()
    s = re.sub(rf"(?i)^\s*{re.escape(label)}\s*", "", s).strip()

    parts = [p.strip() for p in re.split(r"\s{2,}", s) if p.strip()]
    if parts:
        return _clean_value(parts[-1])

    return _clean_value(s)


def _extract_simple_regex(text: str, patterns: list[str]) -> str:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if not m:
            continue
        try:
            v = (m.group(1) or "").strip()
            if not v:
                v = (m.group(0) or "").strip()
        except IndexError:
            v = (m.group(0) or "").strip()
        if v:
            return _clean_value(v)
    return ""


def _parse_datetime_any(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None

    s2 = s.replace("–", "-").replace("—", "-")
    s2 = re.sub(r"\s+", " ", s2)

    fmts = [
        "%d/%m/%y - %H:%M",
        "%d/%m/%Y - %H:%M",
        "%d/%m/%Y %H:%M",
        "%d/%m/%y %H:%M",
    ]
    for f in fmts:
        try:
            return datetime.strptime(s2, f)
        except Exception:
            pass
    return None


def _safe_sheet_name(name: str) -> str:
    name = name or "OT"
    for ch in r'[]:*?/\\':
        name = name.replace(ch, "_")
    name = name.strip()
    if len(name) > 31:
        name = name[:31]
    return name or "OT"


def _slice_between_markers_raw(text: str, start_markers: list[str], end_markers: list[str]) -> str:
    """
    Igual que _slice_between_markers, pero preserva el texto RAW:
    no colapsa espacios ni limpia líneas.
    """
    raw_lines = text.splitlines()
    norm_lines = [_norm_text(x) for x in raw_lines]

    start_idx = None
    for i, nl in enumerate(norm_lines):
        if any(_norm_text(m) in nl for m in start_markers):
            start_idx = i
            break

    if start_idx is None:
        return ""

    end_idx = len(raw_lines)
    for j in range(start_idx + 1, len(raw_lines)):
        nl = norm_lines[j]
        if any(_norm_text(m) in nl for m in end_markers):
            end_idx = j
            break

    return "\n".join(raw_lines[start_idx:end_idx])


def _leading_spaces(s: str) -> int:
    return len(s) - len(s.lstrip(" "))


def _extract_block_after_label(
    text: str,
    label_variants: list[str],
    stop_variants: list[str],
    include_same_line: bool = True,
) -> str:
    """
    Extrae un bloque completo desde un label hasta antes del siguiente label/sección.

    A diferencia de _extract_vertical_field, no depende de la indentación ni de un
    número máximo de líneas. Es ideal para campos narrativos largos como
    'Descripción del trabajo realizado'.
    """
    raw_lines = _remove_fieldbeat_footer_lines(text).splitlines()
    norm_lines = [_norm_text(ln) for ln in raw_lines]
    stop_norms = [_norm_text(x) for x in stop_variants if _norm_text(x)]

    def is_stop_line(nl: str) -> bool:
        return any(nl.startswith(st) for st in stop_norms if st)

    for i, line in enumerate(raw_lines):
        nline = norm_lines[i]

        matched_label = None
        for lab in label_variants:
            nlab = _norm_text(lab)
            if nlab and nline.startswith(nlab):
                matched_label = lab
                break

        if not matched_label:
            continue

        chunks = []
        same = re.sub(rf"(?i)^\s*{re.escape(matched_label)}\s*", "", line).strip()
        if include_same_line and same:
            chunks.append(same)

        j = i + 1
        while j < len(raw_lines):
            raw = raw_lines[j]
            nr = norm_lines[j]
            s = raw.strip()

            if is_stop_line(nr):
                break
            if s:
                chunks.append(s)
            j += 1

        return _clean_value(" ".join(chunks))

    return ""






def _extract_labeled_value_with_fallback(text: str, label_variants: list[str]) -> str:
    """
    Intenta extraer un valor bien formado desde la misma línea del label.
    Si no encuentra un patrón ideal, devuelve igualmente lo que venga a la derecha
    del label o en la línea siguiente, para evitar perder información útil.
    """
    raw_lines = _remove_fieldbeat_footer_lines(text).splitlines()
    norm_lines = [_norm_text(ln) for ln in raw_lines]

    for i, raw in enumerate(raw_lines):
        nraw = norm_lines[i]
        matched_label = None
        for lab in label_variants:
            nlab = _norm_text(lab)
            if nlab and nraw.startswith(nlab):
                matched_label = lab
                break

        if not matched_label:
            continue

        same = re.sub(rf"(?i)^\s*{re.escape(matched_label)}\s*", "", raw).strip()
        if same:
            return _clean_value(same)

        j = i + 1
        while j < len(raw_lines):
            nxt = raw_lines[j].strip()
            if nxt:
                return _clean_value(nxt)
            j += 1

        return ""

    return ""

def _group_words_by_line(words: list[tuple], y_tol: float = 3.0) -> list[list[tuple]]:
    """Agrupa palabras por línea visual usando coordenada Y."""
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: (w[1], w[0]))
    lines: list[list[tuple]] = []
    current: list[tuple] = []
    current_y = None

    for w in words_sorted:
        x0, y0, x1, y1, text, *_ = w
        y_mid = (y0 + y1) / 2.0
        if current_y is None or abs(y_mid - current_y) <= y_tol:
            current.append(w)
            if current_y is None:
                current_y = y_mid
            else:
                current_y = (current_y * (len(current) - 1) + y_mid) / len(current)
        else:
            lines.append(sorted(current, key=lambda t: t[0]))
            current = [w]
            current_y = y_mid

    if current:
        lines.append(sorted(current, key=lambda t: t[0]))
    return lines


def _line_text_from_words(line_words: list[tuple]) -> str:
    return _collapse_spaces(" ".join(w[4] for w in sorted(line_words, key=lambda t: t[0])))


def _find_line_idx_startswith(lines: list[list[tuple]], phrase: str) -> int:
    target = _norm_text(phrase)
    for i, line_words in enumerate(lines):
        if _norm_text(_line_text_from_words(line_words)).startswith(target):
            return i
    return -1


def _extract_descripcion_trabajo_from_pdf(ruta_pdf: str) -> str:
    """
    Extrae 'Descripción del trabajo realizado' por coordenadas.

    Se basa en el bloque visual comprendido entre:
    - la línea 'Tipo de Servicio'
    - la línea 'Estado de funcionamiento del equipo'

    y toma solo la columna de valores (lado derecho), porque en varios PDFs
    FieldBeat deja parte del texto narrativo por encima del rótulo
    'Descripción del trabajo realizado'.
    """
    try:
        doc = fitz.open(ruta_pdf)
    except Exception:
        return ""

    try:
        for page in doc:
            words = page.get_text("words")
            if not words:
                continue

            lines = _group_words_by_line(words, y_tol=3.0)
            idx_tipo = _find_line_idx_startswith(lines, "Tipo de Servicio")
            idx_estado = _find_line_idx_startswith(lines, "Estado de funcionamiento del equipo")
            idx_desc = _find_line_idx_startswith(lines, "Descripción del trabajo realizado")
            if idx_desc < 0:
                idx_desc = _find_line_idx_startswith(lines, "Descripcion del trabajo realizado")

            if idx_tipo < 0 or idx_estado < 0:
                continue

            tipo_words = lines[idx_tipo]
            estado_words = lines[idx_estado]
            desc_words = lines[idx_desc] if idx_desc >= 0 else []

            tipo_y1 = max(w[3] for w in tipo_words)
            estado_y0 = min(w[1] for w in estado_words)

            # Determina el inicio de la columna de valores usando la línea Tipo de Servicio.
            value_x_candidates = [w[0] for w in tipo_words if w[0] > 150]
            if not value_x_candidates and desc_words:
                value_x_candidates = [w[0] for w in desc_words if w[0] > 150]
            value_x_min = min(value_x_candidates) if value_x_candidates else 250.0
            value_x_min = max(220.0, value_x_min - 35.0)

            block_words = []
            for w in words:
                x0, y0, x1, y1, text, *_ = w
                if x0 < value_x_min:
                    continue
                if y0 < tipo_y1 + 3.0:
                    continue
                if y1 > estado_y0 - 3.0:
                    continue
                block_words.append(w)

            if not block_words:
                continue

            block_lines = _group_words_by_line(block_words, y_tol=3.0)
            text_lines = [_line_text_from_words(ln) for ln in block_lines]
            text_lines = [ln for ln in text_lines if ln.strip()]
            if text_lines:
                return _clean_value(" ".join(text_lines))

        return ""
    finally:
        doc.close()

def _extract_vertical_field(
    text: str,
    label_variants: list[str],
    stop_variants: list[str],
    max_up: int = 4,
    max_down: int = 6,
    min_indent_gap: int = 8,
) -> str:
    """
    Extrae campos cuyo valor puede estar:
    - en la misma línea del label (a la derecha),
    - en líneas inmediatamente anteriores,
    - en líneas inmediatamente posteriores.

    Usa la indentación para decidir si una línea pertenece al valor.
    """
    raw_lines = _remove_fieldbeat_footer_lines(text).splitlines()
    norm_lines = [_norm_text(ln) for ln in raw_lines]
    stop_norms = [_norm_text(x) for x in stop_variants if _norm_text(x)]

    def is_stop_line(nl: str) -> bool:
        return any(nl.startswith(st) for st in stop_norms if st)

    for i, line in enumerate(raw_lines):
        nline = norm_lines[i]

        matched_label = None
        for lab in label_variants:
            if _norm_text(lab) and nline.startswith(_norm_text(lab)):
                matched_label = lab
                break

        if not matched_label:
            continue

        label_indent = _leading_spaces(line)
        chunks_before = []
        chunks_after = []

        same = re.sub(rf"(?i)^\s*{re.escape(matched_label)}\s*", "", line).strip()
        if same:
            chunks_after.append(same)

        taken = 0
        j = i - 1
        while j >= 0 and taken < max_up:
            raw = raw_lines[j]
            nr = norm_lines[j]
            s = raw.strip()

            if not s:
                break
            if is_stop_line(nr):
                break

            indent = _leading_spaces(raw)
            if indent >= label_indent + min_indent_gap:
                chunks_before.append(s)
                taken += 1
                j -= 1
                continue
            break

        chunks_before.reverse()

        taken = 0
        j = i + 1
        while j < len(raw_lines) and taken < max_down:
            raw = raw_lines[j]
            nr = norm_lines[j]
            s = raw.strip()

            if not s:
                break
            if is_stop_line(nr):
                break

            indent = _leading_spaces(raw)
            if indent >= label_indent + min_indent_gap:
                chunks_after.append(s)
                taken += 1
                j += 1
                continue
            break

        return _clean_value(" ".join(chunks_before + chunks_after))

    return ""


def _group_words_into_rows(words: list[tuple], y_tol: float = 3.0) -> list[dict]:
    """
    Agrupa words de PyMuPDF por filas aproximadas usando y0.
    Se usa SOLO para localizar filas header/stop; la asignación final a columnas
    se hace por coordenada X de cada palabra.
    """
    filtered = []
    for w in words:
        x0, y0, x1, y1, text, *_ = w
        if not str(text).strip():
            continue
        filtered.append((float(x0), float(y0), float(x1), float(y1), str(text)))

    filtered.sort(key=lambda t: (round(t[1], 1), t[0]))

    rows: list[dict] = []
    for x0, y0, x1, y1, text in filtered:
        placed = False
        for row in rows:
            if abs(y0 - row["y"]) <= y_tol:
                row["words"].append((x0, y0, x1, y1, text))
                row["ys"].append(y0)
                row["y"] = sum(row["ys"]) / len(row["ys"])
                placed = True
                break
        if not placed:
            rows.append({"y": y0, "ys": [y0], "words": [(x0, y0, x1, y1, text)]})

    for row in rows:
        row["words"].sort(key=lambda t: t[0])

    rows.sort(key=lambda r: r["y"])
    return rows


def _row_text(row: dict) -> str:
    return " ".join(w[4] for w in row["words"]).strip()


def _find_header_row_index(rows: list[dict]) -> int:
    target_tokens = ["id", "nombre", "marca", "modelo", "numero", "serie", "comentario", "formulario", "piezas"]

    best_idx = -1
    best_score = -1
    for i, row in enumerate(rows):
        norm = _norm_text(_row_text(row))
        score = sum(1 for tok in target_tokens if tok in norm)
        if score > best_score:
            best_idx = i
            best_score = score

    if best_score >= 8:
        return best_idx
    return -1


def _header_column_starts(header_row: dict) -> list[tuple[str, float]]:
    """
    Detecta el X inicial de cada columna a partir del header.
    """
    cols = {}
    for x0, y0, x1, y1, text in header_row["words"]:
        t = _norm_text(text)
        if t == "id":
            cols["ID"] = x0
        elif t == "nombre":
            cols["Nombre"] = x0
        elif t == "marca":
            cols["Marca y Modelo"] = x0
        elif t == "numero":
            cols["Número de Serie"] = x0
        elif t == "comentario":
            cols["Comentario"] = x0
        elif t == "formulario":
            cols["Formulario"] = x0
        elif t == "piezas":
            cols["Piezas"] = x0

    ordered_names = [
        "ID", "Nombre", "Marca y Modelo", "Número de Serie",
        "Comentario", "Formulario", "Piezas"
    ]
    if not all(name in cols for name in ordered_names):
        return []

    return [(name, cols[name]) for name in ordered_names]


def _extract_equipo_row_from_pdf(ruta_pdf: str) -> dict[str, str]:
    """
    Extrae la tabla 'Equipos a Revisar' usando coordenadas (page.get_text("words")).

    Estrategia robusta:
    1) Buscar la fila header de columnas.
    2) Obtener X inicial de cada columna desde el header.
    3) Delimitar verticalmente el bloque entre el header y el siguiente inicio de sección
       ('Formulario:' / 'Detalles del Servicio').
    4) Asignar cada palabra a columna por su x0 respecto al inicio de columnas.
    5) Colapsar el contenido multilinea por columna.

    Esta estrategia evita:
    - depender de 'Varian - Halcyon HAL1305'
    - buscar IDs globalmente en todo el documento
    - capturar RUTs/firmas del final del PDF
    """
    out = {
        "ID": "",
        "Nombre": "",
        "Marca y Modelo": "",
        "Número de Serie": "",
        "Comentario": "",
        "Formulario": "",
        "Piezas": "",
    }

    try:
        doc = fitz.open(ruta_pdf)
    except Exception:
        return out

    try:
        for page in doc:
            page_words = page.get_text("words")
            rows = _group_words_into_rows(page_words)
            if not rows:
                continue

            header_idx = _find_header_row_index(rows)
            if header_idx < 0:
                continue

            header_cols = _header_column_starts(rows[header_idx])
            if not header_cols:
                continue

            header_y = rows[header_idx]["y"]

            stop_y = None
            for row in rows[header_idx + 1:]:
                row_txt_norm = _norm_text(_row_text(row))
                if row_txt_norm.startswith("formulario:") or row_txt_norm.startswith("detalles del servicio"):
                    stop_y = row["y"]
                    break

            if stop_y is None:
                continue

            data_words = [
                w for w in page_words
                if float(w[1]) > header_y + 3.0 and float(w[1]) < stop_y - 3.0 and str(w[4]).strip()
            ]
            if not data_words:
                continue

            cells = {name: [] for name, _ in header_cols}

            for x0, y0, x1, y1, text, *_ in data_words:
                assigned_col = header_cols[0][0]
                for name, start_x in reversed(header_cols):
                    if float(x0) >= float(start_x) - 1.0:
                        assigned_col = name
                        break
                cells[assigned_col].append((float(y0), float(x0), str(text)))

            for name in out.keys():
                parts = [t for _, _, t in sorted(cells.get(name, []), key=lambda z: (round(z[0], 1), z[1]))]
                out[name] = _clean_value(_collapse_spaces(" ".join(parts)))

            # Limpieza defensiva
            for k, v in list(out.items()):
                if _norm_text(v) == _norm_text(k):
                    out[k] = ""

            # Si detectamos algo consistente, devolvemos
            if out["ID"] and (out["Nombre"] or out["Número de Serie"] or out["Formulario"]):
                return out

    finally:
        doc.close()

    return out


class ExtractorHalcyon:
    def __init__(self):
        pass

    def _extraer_bloque_superior(self, texto: str) -> dict[str, str]:
        """
        Extrae la cabecera superior preservando los espacios de columnas.
        """
        out = {
            "Trabajador": "",
            "Fecha y Hora Programada": "",
            "Duración Estimada": "",
            "Prioridad": "",
            "Tipo de Tarea": "",
            "Descripción": "",
        }

        bloque = _slice_between_markers_raw(
            texto,
            start_markers=["Trabajador"],
            end_markers=["Información de la Tarea", "Informacion de la Tarea"],
        )
        bloque = _remove_fieldbeat_footer_lines(bloque)
        raw_lines = [ln.rstrip("\n") for ln in bloque.splitlines() if ln.strip()]

        def split_big_spaces(line: str) -> list[str]:
            return [p.strip() for p in re.split(r"\s{2,}", line.strip()) if p.strip()]

        idx = _find_line_index(raw_lines, "Trabajador")
        if idx >= 0 and idx + 1 < len(raw_lines):
            vals = split_big_spaces(raw_lines[idx + 1])
            if len(vals) >= 1:
                out["Trabajador"] = vals[0]
            if len(vals) >= 2 and re.search(r"\d{2}/\d{2}/\d{2,4}\s*-\s*\d{2}:\d{2}", vals[1]):
                out["Fecha y Hora Programada"] = vals[1]

        idx = _find_line_index(raw_lines, "Duración Estimada")
        if idx < 0:
            idx = _find_line_index(raw_lines, "Duracion Estimada")
        if idx >= 0 and idx + 1 < len(raw_lines):
            vals = split_big_spaces(raw_lines[idx + 1])
            if len(vals) >= 1:
                out["Duración Estimada"] = vals[0]
            if len(vals) >= 2:
                out["Prioridad"] = vals[1]

        idx_tipo = _find_line_index(raw_lines, "Tipo de Tarea")
        idx_desc = _find_line_index(raw_lines, "Descripción")
        if idx_desc < 0:
            idx_desc = _find_line_index(raw_lines, "Descripcion")

        if idx_tipo >= 0:
            j = idx_tipo + 1
            while j < len(raw_lines) and (idx_desc < 0 or j < idx_desc):
                val = raw_lines[j].strip()
                if not val:
                    j += 1
                    continue

                nval = _norm_text(val)
                if (
                    "@" in val
                    or "email del contacto" in nval
                    or "nombre del contacto" in nval
                    or "telefono del contacto" in nval
                    or "ssconcepcion" in nval
                    or "cliente" in nval
                    or "rut" in nval
                ):
                    j += 1
                    continue

                out["Tipo de Tarea"] = val
                break

        idx = _find_line_index(raw_lines, "Descripción")
        if idx < 0:
            idx = _find_line_index(raw_lines, "Descripcion")
        if idx >= 0:
            desc_parts = []
            stop_prefixes = [
                "direccion", "dirección", "piso oficina referencia", "ingenieria en electronica",
                "equipamiento medico y servicio tecnico", "eliodoro yañez", "eliodoro yanez",
                "informacion de la tarea", "información de la tarea", "equipos a revisar",
            ]
            j = idx + 1
            while j < len(raw_lines):
                line_raw = raw_lines[j]
                line = line_raw.strip()
                nline = _norm_text(line)
                if not line:
                    break
                if any(nline.startswith(p) for p in stop_prefixes):
                    break

                parts = split_big_spaces(line_raw)
                left = parts[0].strip() if parts else ""

                if left:
                    left_norm = _norm_text(left)
                    if not (
                        "@" in left
                        or "ssconcepcion" in left_norm
                        or left_norm in {"cliente", "sucursal", "rut", "email del contacto", "nombre del contacto", "telefono del contacto", "teléfono del contacto"}
                    ):
                        desc_parts.append(left)

                j += 1

            if desc_parts:
                desc = _clean_value(" ".join(desc_parts))
                desc = re.sub(r"\s+\S+@\S+$", "", desc).strip()
                if "@" not in desc and "ssconcepcion" not in _norm_text(desc):
                    out["Descripción"] = desc

        return {k: _clean_value(v) for k, v in out.items()}

    def extraer_campos(self, texto: str, ruta_pdf: str | None = None) -> dict[str, str]:
        out: dict[str, str] = {}

        out["_N_TAREA"] = _extract_simple_regex(texto, [
            r"Información\s+de\s+la\s+Tarea\s+(\d{3,10})",
            r"Informacion\s+de\s+la\s+Tarea\s+(\d{3,10})",
        ])

        out.update(self._extraer_bloque_superior(texto))

        if ruta_pdf:
            out.update(_extract_equipo_row_from_pdf(ruta_pdf))
        else:
            out.update({
                "ID": "",
                "Nombre": "",
                "Marca y Modelo": "",
                "Número de Serie": "",
                "Comentario": "",
                "Formulario": "",
                "Piezas": "",
            })

        texto_limpio = _remove_fieldbeat_footer_lines(texto)
        lines_form = [ln.rstrip() for ln in texto_limpio.splitlines()]

        idx = _find_line_index(lines_form, "Área")
        if idx >= 0:
            out["Área"] = _extract_rightmost_chunk_from_line(lines_form[idx], "Área")
        else:
            idx = _find_line_index(lines_form, "Area")
            if idx >= 0:
                out["Área"] = _extract_rightmost_chunk_from_line(lines_form[idx], "Area")

        idx = _find_line_index(lines_form, "Tipo de Servicio")
        if idx >= 0:
            out["Tipo de Servicio"] = _extract_rightmost_chunk_from_line(
                lines_form[idx], "Tipo de Servicio"
            )

        descripcion_trabajo = ""
        if ruta_pdf:
            descripcion_trabajo = _extract_descripcion_trabajo_from_pdf(ruta_pdf)
        if not descripcion_trabajo:
            descripcion_trabajo = _extract_block_after_label(
                texto,
                ["Descripción del trabajo realizado", "Descripcion del trabajo realizado"],
                [
                    "Estado de funcionamiento del equipo",
                    "Estuvo detenido el equipo",
                    "Clasificación de la falla",
                    "Clasificacion de la falla",
                ],
            )
        out["Descripción del trabajo realizado"] = descripcion_trabajo

        out["Estuvo detenido el equipo"] = _extract_simple_regex(texto_limpio, [
            r"Estuvo detenido el equipo\s+(.*?)\s+Clasificación de la falla\b",
            r"Estuvo detenido el equipo\s+(.*?)\s+Clasificacion de la falla\b",
        ])

        out["Clasificación de la falla"] = _extract_simple_regex(texto_limpio, [
            r"Clasificación de la falla\s+(.*?)\s+Equipo queda operativo\b",
            r"Clasificacion de la falla\s+(.*?)\s+Equipo queda operativo\b",
        ])

        out["Equipo queda operativo"] = _extract_simple_regex(texto_limpio, [
            r"Equipo queda operativo\s+(.*?)\s+Situación de repuestos\b",
            r"Equipo queda operativo\s+(.*?)\s+Situacion de repuestos\b",
        ])

        out["Ha solicitado repuestos desde alguna bodega"] = _extract_simple_regex(texto_limpio, [
            r"Ha solicitado repuestos desde alguna bodega\s+(.*?)\s+Indique bodega de origen\b",
            r"Ha solicitado repuestos desde alguna bodega\s+(No|Si)\s+Tiempos de respuesta\b",
        ])

        idx = _find_line_index(lines_form, "Indique bodega de origen")
        if idx >= 0:
            out["Indique bodega de origen"] = _extract_rightmost_chunk_from_line(
                lines_form[idx], "Indique bodega de origen"
            )

        out["Tiempo de respuesta telefónica con cliente"] = _extract_simple_regex(texto_limpio, [
            r"Tiempo de respuesta telefónica con cliente\s+([0-9]{2}:[0-9]{2})",
            r"Tiempo de respuesta telefonica con cliente\s+([0-9]{2}:[0-9]{2})",
        ])

        out["Tiempo de repuesta en sitio"] = _extract_simple_regex(texto_limpio, [
            r"Tiempo de repuesta en sitio\s+([0-9]{2}:[0-9]{2})",
            r"Tiempo de respuesta en sitio\s+([0-9]{2}:[0-9]{2})",
        ])

        out["Fecha y hora de inicio de trabajo"] = _extract_simple_regex(texto, [
            r"Fecha y hora de inicio de trabajo\s+(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})",
        ])
        if not out["Fecha y hora de inicio de trabajo"]:
            out["Fecha y hora de inicio de trabajo"] = _extract_labeled_value_with_fallback(
                texto, ["Fecha y hora de inicio de trabajo"]
            )

        out["Fecha y hora de término de trabajo"] = _extract_simple_regex(texto, [
            r"Fecha y hora de término de trabajo\s+(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})",
            r"Fecha y hora de termino de trabajo\s+(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})",
        ])
        if not out["Fecha y hora de término de trabajo"]:
            out["Fecha y hora de término de trabajo"] = _extract_labeled_value_with_fallback(
                texto, ["Fecha y hora de término de trabajo", "Fecha y hora de termino de trabajo"]
            )

        out["Tiempo de detención de equipo(hh:mm)"] = _extract_vertical_field(
            texto,
            ["Tiempo de detención de equipo(hh:mm)", "Tiempo de detencion de equipo(hh:mm)"],
            [
                "Recepción conforme del trabajo",
                "Recepcion conforme del trabajo",
                "Se trata de un trabajo remoto o con asistencia telefónica",
                "Se trata de un trabajo remoto o con asistencia telefonica",
            ],
            max_up=3,
            max_down=4,
            min_indent_gap=8,
        )

        idx = _find_line_index(lines_form, "Se trata de un trabajo remoto o con asistencia telefónica")
        if idx < 0:
            idx = _find_line_index(lines_form, "Se trata de un trabajo remoto o con asistencia telefonica")
            label_rem = "Se trata de un trabajo remoto o con asistencia telefonica"
        else:
            label_rem = "Se trata de un trabajo remoto o con asistencia telefónica"

        if idx >= 0:
            out["Se trata de un trabajo remoto o con asistencia telefónica"] = _extract_rightmost_chunk_from_line(
                lines_form[idx], label_rem
            )

        idx = _find_line_index(lines_form, "Importante")
        if idx >= 0:
            partes = []
            if idx - 1 >= 0 and lines_form[idx - 1].strip():
                partes.append(lines_form[idx - 1].strip())
            if idx + 1 < len(lines_form) and lines_form[idx + 1].strip():
                partes.append(lines_form[idx + 1].strip())
            out["Importante"] = _clean_value(" ".join(partes))

        for k, v in list(out.items()):
            if isinstance(v, str):
                out[k] = _clean_value(v)

        return out

    def _detect_ot_category(self, campos: dict[str, str]) -> str:
        """
        Devuelve una categoría funcional para nombrar salidas y títulos.
        Valores esperados:
        - HALCYON
        - CONTROL_DE_CALIDAD
        - ECM
        """
        nombre = _norm_text(campos.get("Nombre", ""))
        marca = _norm_text(campos.get("Marca y Modelo", ""))
        serie = _norm_text(campos.get("Número de Serie", ""))
        descripcion = _norm_text(campos.get("Descripción", ""))
        formulario = _norm_text(campos.get("Formulario", ""))

        if (
            "control de calidad" in nombre
            or "control de calidad" in formulario
            or "accesorios red nueva" in formulario
            or serie.startswith("ptwecm")
            or "equipo de control" in descripcion
        ):
            return "CONTROL_DE_CALIDAD"

        if (
            "halcyon" in marca
            or serie.startswith("hal")
            or "halcyon" in nombre
            or "halcyon" in descripcion
        ):
            return "HALCYON"

        return "ECM"

    def _build_output_labels(self, campos: dict[str, str]) -> dict[str, str]:
        categoria = self._detect_ot_category(campos)

        if categoria == "HALCYON":
            return {
                "categoria": categoria,
                "titulo_txt": "DATOS EXTRAÍDOS - OT HALCYON (ECM)",
                "sigla_archivo": "HAL",
                "excel_nombre": "HALCYON.xlsx",
            }

        if categoria == "CONTROL_DE_CALIDAD":
            return {
                "categoria": categoria,
                "titulo_txt": "DATOS EXTRAÍDOS - OT CONTROL DE CALIDAD (ECM)",
                "sigla_archivo": "CC",
                "excel_nombre": "CONTROL_DE_CALIDAD.xlsx",
            }

        return {
            "categoria": categoria,
            "titulo_txt": "DATOS EXTRAÍDOS - OT ECM",
            "sigla_archivo": "ECM",
            "excel_nombre": "ECM.xlsx",
        }

    def construir_nombre_base(self, campos: dict[str, str]) -> str:
        n = (campos.get("_N_TAREA") or "").strip()
        labels = self._build_output_labels(campos)
        sigla = labels["sigla_archivo"]

        dt = (
            _parse_datetime_any(campos.get("Fecha y Hora Programada", "")) or
            _parse_datetime_any(campos.get("Fecha y hora de inicio de trabajo", ""))
        )

        if dt is None:
            raw = campos.get("Fecha y Hora Programada", "") or campos.get("Fecha y hora de inicio de trabajo", "")
            m = re.search(r"(\d{2})/(\d{2})/(\d{2,4})", raw or "")
            if m:
                try:
                    day = int(m.group(1))
                    month = int(m.group(2))
                    year = int(m.group(3))
                    if year < 100:
                        year += 2000
                    dt = datetime(year, month, day)
                except Exception:
                    dt = None

        mes = f"{dt.month:02d}" if dt else "00"

        if n:
            return f"OT{n}_{sigla}{mes}"
        return f"OT_{sigla}{mes}"

    def guardar_salidas(
        self,
        ruta_pdf: str,
        out_dir: Path,
        campos: dict[str, str]
    ) -> tuple[str | None, str | None, str | None]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        resumen_dir = out_dir / "Resumen"
        resumen_dir.mkdir(parents=True, exist_ok=True)

        src_pdf = Path(ruta_pdf)
        if not src_pdf.exists():
            _show_error("Error", "No existe el PDF seleccionado.")
            return (None, None, None)

        dst_pdf = out_dir / src_pdf.name

        if dst_pdf.exists():
            ok = _ask_yes_no(
                "Archivo existe",
                f"Ya existe el PDF:\n{dst_pdf.name}\n\n¿Quieres sobrescribirlo?"
            )
            if not ok:
                _show_info("Cancelado", "No se guardó (no se sobrescribió el PDF).")
                return (None, None, None)

        try:
            shutil.copy2(src_pdf, dst_pdf)
            pdf_guardado = str(dst_pdf)
        except Exception as e:
            _show_error("Error", f"No se pudo copiar el PDF:\n{e}")
            return (None, None, None)

        nombre_base = self.construir_nombre_base(campos)
        ruta_txt = str(resumen_dir / f"{nombre_base}.txt")

        secciones = [
            ("METADATOS", [
                ("Número de tarea", campos.get("_N_TAREA", "")),
            ]),
            ("DATOS DE LA TAREA", [
                ("Trabajador", campos.get("Trabajador", "")),
                ("Fecha y Hora Programada", campos.get("Fecha y Hora Programada", "")),
                ("Duración Estimada", campos.get("Duración Estimada", "")),
                ("Prioridad", campos.get("Prioridad", "")),
                ("Tipo de Tarea", campos.get("Tipo de Tarea", "")),
                ("Descripción", campos.get("Descripción", "")),
            ]),
            ("EQUIPOS A REVISAR", [
                ("ID", campos.get("ID", "")),
                ("Nombre", campos.get("Nombre", "")),
                ("Marca y Modelo", campos.get("Marca y Modelo", "")),
                ("Número de Serie", campos.get("Número de Serie", "")),
                ("Comentario", campos.get("Comentario", "")),
                ("Formulario", campos.get("Formulario", "")),
                ("Piezas", campos.get("Piezas", "")),
            ]),
            ("FORMULARIO: INFORME DE TRABAJO IMAGENOLOGÍA Y RADIOTERAPIA", [
                ("Área", campos.get("Área", "")),
                ("Tipo de Servicio", campos.get("Tipo de Servicio", "")),
                ("Descripción del trabajo realizado", campos.get("Descripción del trabajo realizado", "")),
                ("Estuvo detenido el equipo", campos.get("Estuvo detenido el equipo", "")),
                ("Clasificación de la falla", campos.get("Clasificación de la falla", "")),
                ("Equipo queda operativo", campos.get("Equipo queda operativo", "")),
                ("Ha solicitado repuestos desde alguna bodega", campos.get("Ha solicitado repuestos desde alguna bodega", "")),
                ("Indique bodega de origen", campos.get("Indique bodega de origen", "")),
                ("Tiempo de respuesta telefónica con cliente", campos.get("Tiempo de respuesta telefónica con cliente", "")),
                ("Tiempo de repuesta en sitio", campos.get("Tiempo de repuesta en sitio", "")),
                ("Fecha y hora de inicio de trabajo", campos.get("Fecha y hora de inicio de trabajo", "")),
                ("Fecha y hora de término de trabajo", campos.get("Fecha y hora de término de trabajo", "")),
                ("Tiempo de detención de equipo(hh:mm)", campos.get("Tiempo de detención de equipo(hh:mm)", "")),
                ("Se trata de un trabajo remoto o con asistencia telefónica", campos.get("Se trata de un trabajo remoto o con asistencia telefónica", "")),
                ("Importante", campos.get("Importante", "")),
            ]),
        ]

        labels = self._build_output_labels(campos)
        titulo = labels["titulo_txt"]
        texto = []
        texto.append("=" * 70)
        texto.append(f"{titulo:^70}")
        texto.append("=" * 70)
        texto.append("")

        filas = []

        for seccion, items in secciones:
            texto.append(seccion)
            texto.append("-" * len(seccion))
            for campo, valor in items:
                valor = _clean_value(valor or "")
                if not valor:
                    valor = "No encontrado"
                texto.append(f"• {campo}: {valor}")
                filas.append({
                    "Sección": seccion,
                    "Campo": campo,
                    "Valor": valor
                })
            texto.append("")

        try:
            with open(ruta_txt, "w", encoding="utf-8") as f:
                f.write("\n".join(texto))
        except Exception as e:
            _show_error("Error", f"No se pudo guardar TXT:\n{e}")
            return (pdf_guardado, None, None)

        ruta_excel = resumen_dir / labels["excel_nombre"]
        sheet = _safe_sheet_name(nombre_base)

        df = pd.DataFrame(filas, columns=["Sección", "Campo", "Valor"])

        try:
            if ruta_excel.exists():
                with pd.ExcelWriter(ruta_excel, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
                    df.to_excel(writer, sheet_name=sheet, index=False)
            else:
                with pd.ExcelWriter(ruta_excel, engine="openpyxl") as writer:
                    df.to_excel(writer, sheet_name=sheet, index=False)
        except Exception as e:
            _show_error("Error", f"No se pudo guardar Excel:\n{e}")
            return (pdf_guardado, ruta_txt, None)

        return (pdf_guardado, ruta_txt, str(ruta_excel))

    def _is_halcyon_ot(self, campos: dict[str, str]) -> bool:
        return self._detect_ot_category(campos) == "HALCYON"


def main():
    global SILENT_MODE
    pdf_arg = None
    out_dir_arg = None

    positional = []
    for arg in sys.argv[1:]:
        if arg == "--silent":
            SILENT_MODE = True
        else:
            positional.append(arg)

    if len(positional) >= 1:
        pdf_arg = Path(positional[0])
    if len(positional) >= 2:
        out_dir_arg = Path(positional[1])

    root = tk.Tk()
    root.withdraw()
    if not SILENT_MODE:
        root.update()

    try:
        if pdf_arg is not None and pdf_arg.exists():
            ruta_pdf = str(pdf_arg)
        else:
            home = Path.home()
            descargas_dir = _primer_dir_existente(home / "Descargas", home / "Downloads")
            ruta_pdf = filedialog.askopenfilename(
                title="Seleccione OT Halcyon (PDF)",
                filetypes=[("PDF", "*.pdf")],
                initialdir=descargas_dir
            )
            if not ruta_pdf:
                return

        if out_dir_arg is not None:
            out_dir = Path(out_dir_arg)
        else:
            out_dir = get_ots_dir() / "ECM" / "HALCYON"

        texto = _read_pdf_text(ruta_pdf)
        if not texto:
            _show_error("Error", "No se pudo extraer texto del PDF.")
            return

        extractor = ExtractorHalcyon()
        campos = extractor.extraer_campos(texto, ruta_pdf=ruta_pdf)

        pdf_guardado, ruta_txt, ruta_excel = extractor.guardar_salidas(ruta_pdf, out_dir, campos)
        if not (pdf_guardado and ruta_txt and ruta_excel):
            return

        _show_info(
            "✅ Exportación completada",
            "Se guardaron los archivos:\n\n"
            f"📄 PDF:\n{pdf_guardado}\n\n"
            f"📝 TXT:\n{ruta_txt}\n\n"
            f"📊 Excel:\n{ruta_excel}\n"
        )

    except Exception as e:
        msg = f"Ocurrió un error:\n\n{e}\n\nDetalle:\n{traceback.format_exc()}"
        print(msg)
        _show_error("Error", msg)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
