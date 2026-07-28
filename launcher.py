# launcher.py
import sys
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText
import shutil
import re
import json
import tkinter.font as tkfont
from auth import is_admin

class AutoScrollbar(ttk.Scrollbar):
    """Scrollbar que se oculta automáticamente si no es necesaria."""
    def set(self, lo, hi):
        lo = float(lo)
        hi = float(hi)
        if lo <= 0.0 and hi >= 1.0:
            self.grid_remove()  # ocultar
        else:
            self.grid()         # mostrar
        super().set(lo, hi)

def _rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kwargs):
    """
    Dibuja un rectángulo redondeado en un Canvas usando arcos + rectángulos.
    """
    r = max(0, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    fill = kwargs.get("fill", "")
    outline = kwargs.get("outline", "")
    width = kwargs.get("width", 1)

    # 4 esquinas
    canvas.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90,
                      style="pieslice", fill=fill, outline=outline, width=width)
    canvas.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90,
                      style="pieslice", fill=fill, outline=outline, width=width)
    canvas.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90,
                      style="pieslice", fill=fill, outline=outline, width=width)
    canvas.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90,
                      style="pieslice", fill=fill, outline=outline, width=width)

    # centro + bandas
    canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=outline, width=width)
    canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=outline, width=width)

class ClinicButton(tk.Canvas):
    """
    Botón redondeado con color, hover y command.
    Útil para estilo clínico (azul/verde) sin librerías externas.
    """
    def __init__(
        self,
        parent,
        text: str,
        command=None,
        bg="#1d4ed8",
        hover_bg="#2563eb",
        fg="#ffffff",
        radius=14,
        height=42,
        font=("TkDefaultFont", 10, "bold"),
        outline="",
        outline_width=0,
        padx=14,
        cursor="hand2",
        parent_bg=None,
        width=None,
        min_width=220,
        max_width=420,
        shadow=True,
        shadow_offset=2,
        shadow_color="#0b1220",
        shadow_alpha_like=False,  # Tk no soporta alpha real; lo dejamos por si lo quieres ajustar después
        **kwargs
    ):
        # Fondo del canvas: ttk.Frame no soporta cget("background") -> hacemos fallback
        if parent_bg is not None:
            canvas_bg = parent_bg
        else:
            try:
                canvas_bg = parent.cget("background")
            except Exception:
                canvas_bg = "#f3f8ff"

        # Ancho “justo” según texto (si no te pasan width)
        if width is None:
            f = tkfont.Font(font=font)
            text_w = f.measure(text)
            width = max(min_width, min(max_width, text_w + padx * 2))

        self._shadow = shadow
        self._shadow_offset = shadow_offset
        self._shadow_color = shadow_color

        super().__init__(
            parent,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=canvas_bg,
            cursor=cursor,
            **kwargs
        )

        self._text = text
        self._command = command
        self._bg = bg
        self._hover_bg = hover_bg
        self._fg = fg
        self._radius = radius
        self._height = height
        self._font = font
        self._outline = outline
        self._outline_width = outline_width
        self._padx = padx

        self._is_hover = False
        self._enabled = True

        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", self._on_click)

        self._redraw()

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._redraw()

    def _set_hover(self, v: bool):
        if not self._enabled:
            return
        self._is_hover = v
        self._redraw()

    def _on_click(self, event=None):
        if not self._enabled:
            return
        if callable(self._command):
            self._command()

    def _redraw(self):
        self.delete("all")
        w = max(1, self.winfo_width())
        h = max(1, self._height)

        fill = self._hover_bg if (self._enabled and self._is_hover) else self._bg
        if not self._enabled:
            fill = "#9ca3af"

        # sombra (primero)
        if self._shadow:
            _rounded_rect(
                self,
                2 + self._shadow_offset,
                2 + self._shadow_offset,
                w - 2 + self._shadow_offset,
                h - 2 + self._shadow_offset,
                r=self._radius,
                fill=self._shadow_color,
                outline="",
                width=0,
            )

        # botón (encima)
        _rounded_rect(
            self,
            2, 2, w - 2, h - 2,
            r=self._radius,
            fill=fill,
            outline="",
            width=0
        )

        # texto centrado
        self.create_text(
            w // 2, h // 2,
            anchor="center",
            text=self._text,
            fill=self._fg,
            font=self._font
        )

class RoundedCard(ttk.Frame):
    """
    Tarjeta redondeada real: se dibuja en Canvas y el contenido va en un Frame interno,
    inseteado por padding para que NO tape las esquinas redondeadas.
    """
    def __init__(
        self,
        parent,
        bg_card: str,
        bg_parent: str,
        radius: int = 16,
        padding: int = 12,
        shadow: bool = True,
        shadow_offset: int = 2,
        shadow_color: str = "#1e293b",
        border_color: str | None = None,
        border_width: int = 1,
        **kwargs
    ):
        super().__init__(parent, style="App.TFrame", **kwargs)

        self.bg_card = bg_card
        self.bg_parent = bg_parent
        self.radius = radius
        self.padding = padding
        self.shadow = shadow
        self.shadow_offset = shadow_offset
        self.shadow_color = shadow_color
        self.border_color = border_color or ""   # "" = sin borde
        self.border_width = border_width

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=self.bg_parent)
        self.canvas.pack(fill="both", expand=True)

        # Contenido real va aquí
        self.inner = ttk.Frame(self.canvas, style="Card.TFrame")

        # Metemos el inner dentro del canvas con margen real
        self._win = self.canvas.create_window(
            self.padding, self.padding,
            anchor="nw",
            window=self.inner
        )

        self.canvas.bind("<Configure>", self._redraw)

    def _redraw(self, event=None):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 2 or h <= 2:
            return

        self.canvas.delete("card")

        # Sombra (debajo)
        if self.shadow:
            _rounded_rect(
                self.canvas,
                2 + self.shadow_offset,
                2 + self.shadow_offset,
                w - 2 + self.shadow_offset,
                h - 2 + self.shadow_offset,
                r=self.radius,
                fill=self.shadow_color,
                outline="",
                width=0,
                tags="card",
            )

        # Tarjeta (encima)
        # --- Borde SIN outline (2 capas) para evitar artefactos en esquinas ---
        bx = max(0, int(self.border_width))
        has_border = bool(self.border_color) and bx > 0

        if has_border:
            # capa borde (fondo)
            _rounded_rect(
                self.canvas,
                2, 2, w - 2, h - 2,
                r=self.radius,
                fill=self.border_color,
                outline="",
                width=0,
                tags="card",
            )

            # capa interior (relleno)
            _rounded_rect(
                self.canvas,
                2 + bx, 2 + bx, w - 2 - bx, h - 2 - bx,
                r=max(0, self.radius - bx),
                fill=self.bg_card,
                outline="",
                width=0,
                tags="card",
            )
        else:
            # sin borde
            _rounded_rect(
                self.canvas,
                2, 2, w - 2, h - 2,
                r=self.radius,
                fill=self.bg_card,
                outline="",
                width=0,
                tags="card",
            )

        # Ajuste del inner para que NO tape bordes/esquinas
        inner_w = max(1, w - (self.padding * 2))
        inner_h = max(1, h - (self.padding * 2))

        self.canvas.coords(self._win, self.padding, self.padding)
        self.canvas.itemconfigure(self._win, width=inner_w, height=inner_h)

HERE = Path(__file__).resolve().parent

MAP_FILE = HERE / "halcyon_serial_map.json"

def load_halcyon_map() -> dict[str, str]:
    try:
        if MAP_FILE.exists():
            data = json.loads(MAP_FILE.read_text(encoding="utf-8"))
            # Normaliza claves/valores
            out = {}
            for k, v in (data or {}).items():
                kk = str(k).upper().replace(" ", "").replace("-", "")
                vv = str(v).upper()
                if re.fullmatch(r"HALCYON_\d+", vv):
                    out[kk] = vv
            return out
    except Exception:
        pass
    return {}

def save_halcyon_map(m: dict[str, str]) -> None:
    try:
        MAP_FILE.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

# 👇 Mapea seriales HALxxxx a HALCYON_n (ej: HAL1305 -> HALCYON_1)
HALCYON_SERIAL_MAP = load_halcyon_map()

PROGRAMS = [
    {
        "id": "P1",
        "title": "Extraer datos desde PDF",
        "subtitle": "PDF → TXT + Excel (UNIQUE.xlsx)",
        "script": HERE / "P1_ExtraerDatosPDF.py",
        "desc": (
            "Lee una Orden de Trabajo en PDF para OTs de UNIQUE y extrae datos clave cómo revisón y seguimiento de las recomendaciones, horas de filamento, respuesto a solicitar, observaciones, fechas, tiempos, descripción, notas, subtareas, entre otros datos.\n\n"
            "Luego te permite guardar:\n"
            "• Un archivo TXT por cada pdf con el resumen\n"
            "• Un archivo Excel UNIQUE.xlsx (una hoja por OT)"
        ),
    },
    {
        "id": "P2",
        "title": "Visor TXT de estructuras",
        "subtitle": "Órganos / Campos de dosis / Detalles del paciente",
        "script": HERE / "P2_visor_estructuras.py",
        "desc": (
            "Programa que lee archivos TXT con contiene estructuras y nombre órganos de un paciente, funciones principales:\n\n"
            "• Abre un TXT con bloques por estructura y agrupa por órgano.\n"
            "• Contiene un buscador para selecciona un órgano específico\n"
            "• Muestra los detalles del paciente\n"
            "• Muestra datos relevantes de los órganos."
        ),
    },
    {
        "id": "P3",
        "title": "Editor de nombre y visor de imágenes",
        "subtitle": "Herramientas para editar y visualizar archivos DICOM",
        "script": HERE / "P3_editor_dmc_carpeta.py",
        "desc": (
            "Herramienta para editar nombre del paciente de una carpeta DICOM. Revisa imágenes DICOM en visualizador y sistema de clasificación parecido al programa de Eclipse:\n\n"
            "• Permite editar el nombre del paciente.\n"
            "• Útil para ordenar carpetas de las imágenes DICOM.\n"
            "• Guarda nombre editado y datos seleccionados en la Vista DICOM."
        ),
    },
    {
        "id": "P4",
        "title": "Transformar DICOM para Eclipse",
        "subtitle": "Normalización/ajustes para compatibilidad",
        "script": HERE / "P4_1_dicom_eclipse_bulletproof.py",
        "desc": (
            "Programa para transformar archivos DICOM y hacerlos compatibles con el software de planificación Eclipse de Varian. Funciones principales:\n\n"
            "• Procesa carpetas DICOM para hacerlos compatibles con el programa de Eclipse.\n"
            "• Guarda los DICOM transformados en una carpeta nueva.\n"
        ),
    },
]

OT_BUTTONS = [
    {
        "key": "UNIQUE",
        "label": "UNIQUE",
        "desc": (
            "OTs del acelerador UNIQUE (iClinic).\n\n"
            "• Guarda/ordena PDFs en la ruta: Escritorio/OTs/ICLINIC/UNIQUE\n"
            "• Útil para OTs de UNIQUE y su seguimiento."
        ),
    },
    {
        "key": "HALCYON_1",
        "label": "HALCYON1",
        "desc": (
            "OTs del acelerador HALCYON 1 (ECM).\n\n"
            "• Guarda/ordena PDFs en: Escritorio/OTs/ECM/HALCYON_1\n"
            "• Si detecta serial, puede aprender el mapeo automáticamente."
        ),
    },
    {
        "key": "HALCYON_2",
        "label": "HALCYON2",
        "desc": (
            "OTs del acelerador HALCYON 2 (ECM).\n\n"
            "• Guarda/ordena PDFs en: Escritorio/OTs/ECM/HALCYON_2\n"
            "• Ideal cuando ya sabes de qué equipo es la OT."
        ),
    },
    {
        "key": "SIEMENS",
        "label": "SIEMENS",
        "desc": (
            "Reportes de Servicio Técnico Siemens (Healthineers).\n\n"
            "• Guarda/ordena PDFs en: Escritorio/OTs/SIEMENS\n"
            "• Detecta automáticamente por texto “Reporte de Servicio Técnico”, "
            "“Siemens Healthineers”, “Siemens Healthcare Equipos Médicos”, “SOMATOM”, etc."
        ),
    },
    {
        "key": "AIL6",
        "label": "ARIA 16",
        "desc": (
            "OTs del equipo ARIA 16.\n\n"
            "• (Editar esta descripción)\n"
            "• Ej: tipo de OT, destino, notas, etc."
        ),
    },
    {
        "key": "ARIL8",
        "label": "ARIA 8",
        "desc": (
            "OTs del equipo ARIA 8.\n\n"
            "• (Editar esta descripción)\n"
        ),
    },
    {
        "key": "SOMAR",
        "label": "SERVIDOR",
        "desc": (
            "Servidor...\n\n"
            "• (Editar esta descripción)\n"
        ),
    },
    {
        "key": "UPS",
        "label": "UPS",
        "desc": (
            "OTs relacionadas a UPS.\n\n"
            "• (Edita esta descripción a tu gusto)\n"
        ),
    },
    {
        "key": "CONTROL DE CALIDAD",
        "label": "CONTROL DE CALIDAD",
        "desc": (
            "OTs ECM de equipos de Control de Calidad y posicionamiento.\n\n"
            "• Guarda/ordena PDFs en: Escritorio/OTs/ECM/CONTROL_DE_CALIDAD\n"
            "• Usa P5 para extraer PDF + TXT + Excel igual que las OTs ECM/Halcyon.\n"
            "• Pensado para casos como PTWECM / Control de Calidad y Accesorios.\n"
        ),
    },
]

def _find_halcyon_serial(blob: str) -> str | None:
    """
    Busca algo tipo HAL1305 / HAL-1305 / HAL 1305 en nombre + texto.
    Devuelve "HAL1305" (normalizado) o None.
    """
    m = re.search(r"\bHAL[\s\-]?(\d{3,6})\b", blob, flags=re.IGNORECASE)
    if not m:
        return None
    return f"HAL{m.group(1)}".upper()



def _looks_like_control_calidad(blob: str) -> bool:
    """
    Detecta OTs ECM de Control de Calidad / equipos de posicionamiento.
    """
    hits = [
        "equipos de control de calidad y posicionamiento",
        "control de calidad y accesorios",
        "control de calidad",
        "posicionamiento",
        "ptwecm",
        "pmi equipo de control",
    ]
    return any(h in blob for h in hits)
def run_script(path: Path, args: list[str] | None = None):
    if not path.exists():
        messagebox.showerror("No encontrado", f"No existe:\n{path}")
        return
    try:
        cmd = [sys.executable, str(path)]
        if args:
            cmd += args
        subprocess.Popen(cmd, cwd=str(HERE))
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir:\n{path}\n\n{e}")

def _first_existing_dir(home: Path, candidates: list[str]) -> Path:
    for name in candidates:
        p = home / name
        if p.exists() and p.is_dir():
            return p
    return home

def _downloads_dir() -> Path:
    home = Path.home()
    return _first_existing_dir(home, ["Descargas", "Downloads"])

def _desktop_dir() -> Path:
    home = Path.home()
    return _first_existing_dir(home, ["Escritorio", "Desktop"])

def _safe_copy_name(dst_dir: Path, filename: str) -> Path:
    """
    Si ya existe, genera nombre con sufijo _2, _3, ...
    """
    base = Path(filename).stem
    ext = Path(filename).suffix
    cand = dst_dir / f"{base}{ext}"
    i = 2
    while cand.exists():
        cand = dst_dir / f"{base}_{i}{ext}"
        i += 1
    return cand

def _extract_ot_number_from_path_or_text(path: Path, text: str = "") -> str:
    """
    Intenta obtener el número OT desde el nombre del archivo o desde el texto extraído.
    """
    name = path.name if path else ""
    m = re.search(r"OT\s*([0-9]{3,10})", name, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"Informaci[oó]n\s+de\s+la\s+Tarea\s+([0-9]{3,10})", text, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"([0-9]{4,10})", name)
    if m:
        return m.group(1)
    return ""


def _dest_dir_for_kind(kind: str, desktop: Path | None = None) -> Path:
    desktop = desktop or _desktop_dir()
    base = desktop / "OTs"

    if kind == "UNIQUE":
        return base / "ICLINIC" / "UNIQUE"
    if kind.startswith("HALCYON_"):
        return base / "ECM" / kind
    if kind == "CONTROL DE CALIDAD":
        return base / "ECM" / "CONTROL_DE_CALIDAD"
    if kind == "SIEMENS":
        return base / "SIEMENS"
    if kind == "HALCYON":
        return base / "ECM" / "REVISAR_HALCYON"
    if not kind:
        return base / "OTs_OTROS" / "SIN_CLASIFICAR"
    return base / "OTs_OTROS" / kind


def _kind_label_for_batch(kind: str | None) -> str:
    if not kind:
        return "SIN CLASIFICAR"
    if kind == "HALCYON":
        return "HALCYON (REVISAR)"
    return kind


def _destination_pdf_for_kind(src: Path, kind: str) -> Path:
    return _dest_dir_for_kind(kind) / src.name


def _existing_output_conflicts(src: Path, kind: str, ot: str | None = None) -> list[Path]:
    conflicts: list[Path] = []

    dst_pdf = _destination_pdf_for_kind(src, kind)
    if dst_pdf.exists():
        conflicts.append(dst_pdf)

    # TXT resumen: aplica sobre todo a P1/P5.
    if kind == "UNIQUE" or kind.startswith("HALCYON_") or kind == "CONTROL DE CALIDAD":
        resumen_dir = _dest_dir_for_kind(kind) / "Resumen"
        if resumen_dir.exists() and resumen_dir.is_dir():
            seen = {str(x.resolve()) for x in conflicts}
            patterns: list[str] = []
            ot = (ot or "").strip()
            if ot and ot != "—":
                patterns.extend([f"OT{ot}_*.txt", f"*{ot}*.txt"])
            patterns.append(f"{src.stem}.txt")

            for pat in patterns:
                for cand in sorted(resumen_dir.glob(pat)):
                    try:
                        key = str(cand.resolve())
                    except Exception:
                        key = str(cand)
                    if cand.exists() and key not in seen:
                        conflicts.append(cand)
                        seen.add(key)

    return conflicts

def _can_auto_process_kind(kind: str | None) -> bool:
    if not kind:
        return False
    if kind == "HALCYON":
        return False
    return True


def classify_ot_pdf(path: Path) -> str | None:
    name = (path.name or "").lower()
    text = extract_pdf_text(path).lower()
    blob = f"{name}\n{text}"

    # normalización simple
    blob = blob.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
    blob = re.sub(r"\s+", " ", blob)

    # ---------------- UNIQUE ----------------
    unique_hits = [
        "unique",
        "acelerador unique",
        "varian unique",
        "iclinic",
        # agrega frases que vengan dentro de tus PDFs UNIQUE si las ves
    ]
    if any(k in blob for k in unique_hits):
        return "UNIQUE"
    if re.search(r"\bu[nm][i1l]que\b", blob, flags=re.IGNORECASE):
        return "UNIQUE"

    # ---------------- ECM: Control de Calidad ----------------
    if _looks_like_control_calidad(blob) and "halcyon" not in blob:
        return "CONTROL DE CALIDAD"

    # ---------------- HALCYON ----------------
    halcyon_hits = [
        "acelerador lineal halcyon",
        "varian - halcyon",
        "varian halcyon",
        "halcyon bgm",
        "halcyon spv",
        # opcional (si esto solo aparece en OTs de ECM):
        "fieldbeat",
        "informacion de la tarea",
        "ingenieria en electronica, computacion y medicina",
    ]

    # si el PDF lo trae explícito
    if "halcyon 1" in blob or "halcyon_1" in blob or "halcyon-1" in blob:
        return "HALCYON_1"
    if "halcyon 2" in blob or "halcyon_2" in blob or "halcyon-2" in blob:
        return "HALCYON_2"

    if any(k in blob for k in halcyon_hits) or "halcyon" in blob:
        serial = _find_halcyon_serial(blob)
        if serial and serial in HALCYON_SERIAL_MAP:
            return HALCYON_SERIAL_MAP[serial]  # HALCYON_1, HALCYON_2, HALCYON_3, etc.
        return "HALCYON"  # si no se puede distinguir cuál

    # ---------------- SIEMENS ----------------
    siemens_hits = [
        "siemens healthineers",
        "siemens healthcare",
        "siemens healthcare equipos medicos",
        "reporte de servicio tecnico",
        "centro de atencion al cliente",
        "somatom",
        "teamplay fleet",
    ]

    if any(k in blob for k in siemens_hits):
        return "SIEMENS"

    return None

def extract_pdf_text(path: Path, max_pages: int = 2, max_chars: int = 6000) -> str:
    # 1) pypdf / PyPDF2
    try:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception:
            from PyPDF2 import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        out = []
        for page in reader.pages[:max_pages]:
            try:
                out.append(page.extract_text() or "")
            except Exception:
                pass
        text = "\n".join(out).strip()
        if len(text) >= 80:   # umbral simple: si hay “suficiente” texto, listo
            return text[:max_chars]
    except Exception:
        pass

    # 2) pdfplumber
    try:
        import pdfplumber  # type: ignore
        out = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages[:max_pages]:
                try:
                    out.append(page.extract_text() or "")
                except Exception:
                    pass
        text = "\n".join(out).strip()
        if len(text) >= 80:
            return text[:max_chars]
    except Exception:
        pass

    # 3) OCR fallback (escaneado)
    text = ocr_pdf_first_page(path, max_chars=max_chars).strip()
    return text[:max_chars]

def ocr_pdf_first_page(path: Path, max_chars: int = 6000) -> str:
    """
    OCR de la primera página (para PDFs escaneados).
    Requiere: pymupdf + pytesseract + tesseract-ocr (sistema).
    """
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image

        doc = fitz.open(str(path))
        page = doc.load_page(0)

        # Render a 2x para mejor OCR
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)

        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        text = pytesseract.image_to_string(img, lang="spa") or ""
        return text[:max_chars]
    except Exception:
        return ""

class LoginWindow(tk.Tk):
    """
    Ventana de login que se muestra antes del launcher principal.
    Solo permite acceso a usuarios con rol de administrador.
    """
    def __init__(self):
        super().__init__()
        self.title("Acceso al Sistema")
        self.configure(background="#eaf2ff")
        
        # Centrar ventana
        self.geometry("480x450")      # más alto para que quepa el botón + texto
        self.minsize(480, 440)        # evita que el SO lo deje más chico
        self.resizable(True, True)  # si quieres permitir resize, pon True, True

        
        # Estilo clínico consistente con el launcher
        style = ttk.Style(self)
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass
        
        self.C_BG = "#eaf2ff"
        self.C_CARD = "#dbeafe"
        self.C_TOPBAR = "#013999"
        self.C_ACTION_BLUE = "#1d4ed8"
        self.C_ACTION_BLUE_H = "#2563eb"
        self.C_TEXT = "#0f172a"
        self.C_MUTED = "#334155"
        self.C_BORDER = "#c9d9f2"
        
        self.FONT_TITLE = ("TkDefaultFont", 24, "bold")
        self.FONT_BODY = ("TkDefaultFont", 10)
        self.FONT_BTN = ("TkDefaultFont", 10, "bold")
        
        self._build_ui()
        
        # Enter hace login (desde cualquier campo)
        self.entry_user.bind("<Return>", self._on_enter_login)
        self.entry_pass.bind("<Return>", self._on_enter_login)
        self.bind("<Return>", self._on_enter_login)  # fallback por si el foco queda fuera

        
        # Resultado del login
        self.login_successful = False
        
    def _build_ui(self):
        # Contenedor principal
        main = ttk.Frame(self, padding=30, style="App.TFrame")
        main.pack(fill="both", expand=True)
        
        style = ttk.Style(self)
        style.configure("App.TFrame", background=self.C_BG)
        style.configure("Card.TFrame", background=self.C_CARD)
        style.configure("Title.TLabel", background=self.C_BG, foreground=self.C_TEXT, font=self.FONT_TITLE)
        style.configure("Body.TLabel", background=self.C_CARD, foreground=self.C_MUTED, font=self.FONT_BODY)
        style.configure("CardTitle.TLabel", background=self.C_CARD, foreground=self.C_TEXT, font=("TkDefaultFont", 11, "bold"))
        
        # Título
        ttk.Label(
            main,
            text="🔐 Acceso al Sistema",
            style="Title.TLabel"
        ).pack(pady=(0, 20))
        
        # Tarjeta de login (redondeada)
        card = RoundedCard(
            main,
            bg_card=self.C_CARD,
            bg_parent=self.C_BG,
            radius=20,
            padding=24,
            shadow=True,
            shadow_offset=2,
            shadow_color="#1e293b",
            border_color=self.C_BORDER,
            border_width=2,
        )
        card.pack(fill="x", pady=(0, 20))
        
        inner = card.inner
        inner.configure(style="Card.TFrame")
        
        ttk.Label(
            inner,
            text="Ingresa tus credenciales",
            style="CardTitle.TLabel"
        ).pack(anchor="w", pady=(0, 16))
        
        # Usuario
        ttk.Label(inner, text="Usuario:", style="Body.TLabel").pack(anchor="w", pady=(0, 4))
        self.entry_user = ttk.Entry(inner, font=self.FONT_BODY, width=28)
        self.entry_user.pack(fill="x", pady=(0, 12))
        self.entry_user.focus()
        
        # Contraseña
        ttk.Label(inner, text="Contraseña:", style="Body.TLabel").pack(anchor="w", pady=(0, 4))
        self.entry_pass = ttk.Entry(inner, font=self.FONT_BODY, width=28, show="•")
        self.entry_pass.pack(fill="x", pady=(0, 16))
        
        # Botón de login
        ClinicButton(
            inner,
            text="Iniciar sesión",
            parent_bg=self.C_CARD,
            bg=self.C_ACTION_BLUE,
            hover_bg=self.C_ACTION_BLUE_H,
            command=self._do_login,
            radius=14,
            height=42,
            font=self.FONT_BTN,
            width=200,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).pack(pady=(4, 0))
        
        # Mensaje de estado
        self.status_label = ttk.Label(
            main,
            text="Solo usuarios con rol de administrador pueden acceder.",
            style="Body.TLabel",
            foreground=self.C_MUTED
        )
        self.status_label.pack(pady=(8, 0))
        
    def _on_enter_login(self, event=None):
        self._do_login()
        return "break"  # evita propagación del evento

    def _do_login(self):
        username = self.entry_user.get().strip()
        password = self.entry_pass.get()
        
        if not username or not password:
            self.status_label.configure(
                text="⚠️ Por favor completa todos los campos.",
                foreground="#d97706"
            )
            return
        
        # Verificar si es administrador
        if is_admin(username, password):
            self.login_successful = True
            self.destroy()
        else:
            self.status_label.configure(
                text="❌ Credenciales incorrectas o sin permisos de administrador.",
                foreground="#dc2626"
            )
            self.entry_pass.delete(0, tk.END)
            self.entry_user.focus()

class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        # --- Abrir maximizado (Windows / Linux) ---
        try:
            self.state("zoomed")  # Windows
        except Exception:
            try:
                self.attributes("-zoomed", True)  # Linux (algunas distros)
            except Exception:
                pass

        # --- Fullscreen toggle ---
        self._is_fullscreen = False

        self._ot_win = None
        self._ot_title_lbl = None
        self._ot_desc_box = None
        self._pending_ot_src: Path | None = None
        self._ot_file_lbl = None

        def toggle_fullscreen(event=None):
            self._is_fullscreen = not self._is_fullscreen
            self.attributes("-fullscreen", self._is_fullscreen)

        def on_escape(event=None):
            if self._is_fullscreen:
                self._is_fullscreen = False
                self.attributes("-fullscreen", False)
            else:
                self.destroy()

        self.bind("<F11>", toggle_fullscreen)
        self.bind("<Escape>", on_escape)
        self.bind("<Return>", lambda e: self.open_selected())

        self.title("Suite de Programas")

        self._apply_style()

        self.status_var = tk.StringVar(value="Listo.")
        self.search_var = tk.StringVar()
        self._index_map = []

        self._build_ui()
        self._populate()
        self._select_first()

        # Asegurar ventana “maximizada” también en escritorios donde zoomed/-zoomed no funcione
        self.after(50, self._ensure_maximized_like)

        # Enter abre el seleccionado
        self.bind("<Return>", lambda e: self.open_selected())

        # Esc: si está fullscreen (F11), sale; si no, cierra
        def on_escape(event=None):
            if getattr(self, "_is_fullscreen", False):
                self._is_fullscreen = False
                self.attributes("-fullscreen", False)
            else:
                self.destroy()

        self.bind("<Escape>", on_escape)

    def _ensure_maximized_like(self):
        """
        Fallback para que la ventana de launcher se abra lo más grande posible
        cuando state('zoomed') / -zoomed no tienen efecto en el sistema.
        """
        # Si ya está en modo fullscreen (F11), no tocar
        if getattr(self, "_is_fullscreen", False):
            return

        # Calcula tamaño de pantalla y ocupa prácticamente todo
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            # Dejamos un pequeño margen para que se vean bien los bordes del WM
            w = int(sw * 0.98)
            h = int(sh * 0.96)
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _pick_font_family(self) -> str:
        """
        Elige una fuente moderna disponible (Linux/Windows) con fallback seguro.
        """
        fams = set(tkfont.families(self))

        # Preferencias por plataforma
        if sys.platform.startswith("win"):
            preferred = ["Segoe UI", "Inter", "Noto Sans", "Arial", "TkDefaultFont"]
        else:
            preferred = ["Noto Sans", "Inter", "DejaVu Sans", "Liberation Sans", "Ubuntu", "Cantarell", "TkDefaultFont"]

        for f in preferred:
            if f in fams:
                return f

        return "TkDefaultFont"

    def _apply_style(self):
        # Estilo claro, limpio y “profesional” sin librerías externas
        style = ttk.Style(self)

        # Elegimos un tema decente si está disponible
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        self.UI_FONT = self._pick_font_family()

        # Tipografías (consistentes)
        # self.FONT_TITLE = (self.UI_FONT, 15, "bold")
        # self.FONT_SUB   = (self.UI_FONT, 10)
        # self.FONT_H2    = (self.UI_FONT, 11, "bold")
        self.FONT_BTN   = (self.UI_FONT, 10, "bold")
        self.FONT_BTN_SM = (self.UI_FONT, 9, "bold")
        self.FONT_BODY  = (self.UI_FONT, 10)
        self.FONT_TREE = (self.UI_FONT, 11, "bold")   # prueba 11 o 12
        self.FONT_TREE_HEAD = (self.UI_FONT, 10, "bold")


        # Paleta clínica (más “software real”)
        self.C_BG = "#eaf2ff"          # fondo general (azul muy suave)
        self.C_CARD = "#dbeafe"        # tarjeta levemente tintada (azul muy claro)
        self.C_CARD_INNER = "#ffffff"  # interior blanco para mejor lectura
        self.C_BORDER = "#c9d9f2"      # borde suave
        self.C_TEXT = "#0f172a"        # texto principal
        self.C_MUTED = "#334155"       # texto secundario

        # Barra superior
        self.C_TOPBAR = "#013999"      # azul institucional
        self.C_TOPBAR_SUB = "#dbeafe"  # subtítulo claro

        # Acciones clínicas
        self.C_ACTION_BLUE = "#1d4ed8"
        self.C_ACTION_BLUE_H = "#2563eb"
        self.C_ACTION_GREEN = "#16a34a"
        self.C_ACTION_GREEN_H = "#22c55e"

        self.configure(background=self.C_BG)

        # Acciones clínicas
        self.C_ACTION_BLUE = "#3449ff"
        self.C_ACTION_BLUE_H = "#2563eb"
        self.C_ACTION_GREEN = "#16a34a"
        self.C_ACTION_GREEN_H = "#22c55e"

        # Tipografías
        self.FONT_TITLE = ("TkDefaultFont", 30, "bold")
        self.FONT_SUB = ("TkDefaultFont", 10)
        self.FONT_H2 = ("TkDefaultFont", 11, "bold")
        self.FONT_SUB_BIG = (self.UI_FONT, 12)   # prueba 11 o 12

        style.configure("CardWhite.TFrame", background="#ffffff")
        style.configure("CardWhiteTitle.TLabel", background="#ffffff", foreground=self.C_TEXT, font=self.FONT_H2)
        # Texto secundario sobre tarjetas blancas
        style.configure("CardWhiteMuted.TLabel", background="#ffffff", foreground=self.C_MUTED, font=self.FONT_BODY)

        # Frames base
        style.configure("App.TFrame", background=self.C_BG)

        # Tarjetas con tinte (no blanco puro)
        style.configure("Card.TFrame", background=self.C_CARD)

        # Topbar (barra clínica)
        style.configure("Topbar.TFrame", background=self.C_TOPBAR)
        style.configure("TopbarTitle.TLabel", background=self.C_TOPBAR, foreground="#ffffff", font=self.FONT_TITLE)
        style.configure("TopbarSub.TLabel", background=self.C_TOPBAR, foreground=self.C_TOPBAR_SUB, font=self.FONT_SUB)

        # Labels normales (sobre tarjeta)
        style.configure("CardTitle.TLabel", background=self.C_CARD, foreground=self.C_TEXT, font=self.FONT_H2)
        style.configure("Muted.TLabel", background=self.C_CARD, foreground=self.C_MUTED, font=(self.UI_FONT, 10, "bold"))  # Adjusted to bold
        # + agrega versiones para fondo App si las usas en header OT
        style.configure("AppTitle.TLabel", background=self.C_BG, foreground=self.C_TEXT, font=self.FONT_H2)
        style.configure("AppMuted.TLabel", background=self.C_BG, foreground=self.C_MUTED)

        # Treeview (tabla) + selección clínica
        style.configure(
            "Treeview",
            font=self.FONT_TREE,
            rowheight=30,
            bordercolor=self.C_BORDER,
            relief="solid",
            background=self.C_CARD_INNER,
            fieldbackground=self.C_CARD_INNER,
            foreground=self.C_TEXT,
        )
        style.configure(
            "Treeview.Heading",
            font=self.FONT_TREE_HEAD,
            foreground=self.C_TEXT,
            background=self.C_CARD,
        )
        style.configure(
            "TopbarSubBig.TLabel",
            background=self.C_TOPBAR,
            foreground=self.C_TOPBAR_SUB,
            font=self.FONT_SUB_BIG,
        )

        style.map(
            "Treeview",
            background=[("selected", self.C_ACTION_BLUE)],
            foreground=[("selected", "#ffffff")],
        )

        # --- Combobox OT (más "clínico") ---
        style.configure(
            "OT.TCombobox",
            padding=6,
        )
        style.map(
            "OT.TCombobox",
            fieldbackground=[("readonly", "#ffffff")],
            background=[("readonly", "#ffffff")],
            foreground=[("readonly", self.C_TEXT)],
        )

    def _build_ui(self):
        root = ttk.Frame(self, padding=14, style="App.TFrame")
        root.pack(fill="both", expand=True)

        # Top bar clínico
        topbar = ttk.Frame(root, style="Topbar.TFrame", padding=(14, 12))
        topbar.pack(fill="x", pady=(0, 12))

        ttk.Label(topbar, text="🗂️ Suite de Programas", style="TopbarTitle.TLabel").pack(anchor="w")
        ttk.Label(
            topbar,
            text="Cómo funciona el programa:\n" \
            "𖣐 Selecciona un programa a la izquierda para ver su descripción y apretar el botón verde \"Abrir programa\" o hacer doble click (o Enter) para abrir.\n" \
            "   ▹ Puedes buscar programas escribiendo en el cuadro de búsqueda.\n" \
            "𖣐 Botón OT para guardar una OT de formato PDF de la carpeta de Descarga en una carpeta esctrurada en Escritorio.",
            style="TopbarSubBig.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        # Main
        main = ttk.Frame(root, style="App.TFrame")
        main.pack(fill="both", expand=True)

        # Sidebar fijo/angosto; detalle se expande
        main.columnconfigure(0, weight=0, minsize=400)  # prueba 320–380
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        # Sidebar card (REDONDEADA real con Canvas)
        sidebar_outer = RoundedCard(
            main,
            bg_card=self.C_CARD,
            bg_parent=self.C_BG,
            radius=20,
            padding=12,
            shadow=True,
            shadow_offset=2,
            shadow_color="#1e293b",
            border_color=self.C_BORDER,
            border_width=2,
        )
        sidebar_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        # Frame interno real (aquí van tus widgets)
        sidebar = sidebar_outer.inner

        sidebar.configure(style="Card.TFrame")
        
        # --- Acciones clínicas (botones redondeados) ---
        ClinicButton(
            sidebar,
            text="🗃️ Importar OT(s) PDF",
            parent_bg=self.C_CARD,
            bg=self.C_ACTION_BLUE,
            hover_bg=self.C_ACTION_BLUE_H,
            command=self.import_ot_pdfs_batch,
            radius=16,
            height=44,
            font=self.FONT_BTN_SM,
            width=320,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).pack(pady=(15, 10), anchor="center")

        ClinicButton(
            sidebar,
            text="▶ Abrir programa",
            parent_bg=self.C_CARD,
            bg=self.C_ACTION_GREEN,
            hover_bg=self.C_ACTION_GREEN_H,
            command=self.open_selected,
            radius=16,
            height=46,
            font=self.FONT_BTN_SM,
            width=280,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).pack(pady=(10, 10), anchor="center")

        ttk.Label(sidebar, text="💾 Programas", style="CardTitle.TLabel").pack(anchor="w")

        ttk.Label(sidebar, text="🔎 Buscar", style="Muted.TLabel").pack(anchor="w", pady=(10, 4))
        search = ttk.Entry(sidebar, textvariable=self.search_var)
        search.pack(fill="x")
        self.search_var.trace_add("write", lambda *_: self._populate())
        ttk.Label(sidebar, text="📋 Lista de programas    –     Doble click para abrir", style="Muted.TLabel").pack(anchor="w", pady=(10, 4))

        # --- Treeview con scrolls (reemplaza tu bloque actual de self.tree) ---
        tree_frame = ttk.Frame(sidebar, style="Card.TFrame")
        tree_frame.pack(fill="x", expand=False)

        xscroll = AutoScrollbar(tree_frame, orient="horizontal")
        yscroll = AutoScrollbar(tree_frame, orient="vertical")

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("name",),
            show="headings",
            selectmode="browse",
            height=4, #Espacio vertical aproximado
            xscrollcommand=xscroll.set,
            yscrollcommand=yscroll.set,
        )
        xscroll.config(command=self.tree.xview)
        yscroll.config(command=self.tree.yview)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        tree_frame.rowconfigure(0, weight=0)
        tree_frame.columnconfigure(0, weight=1)

        self.tree.heading("name", text="Programa")

        # Solo se muestra el nombre del programa
        self.tree.column("name", width=1, minwidth=260, stretch=True, anchor="w")

        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.open_selected())

        # Detail card (también con borde redondeado, igual que la columna izquierda)
        detail_outer = RoundedCard(
            main,
            bg_card=self.C_CARD,
            bg_parent=self.C_BG,
            radius=20,
            padding=14,
            shadow=True,
            shadow_offset=2,
            shadow_color="#1e293b",
            border_color=self.C_BORDER,
            border_width=2,
        )
        detail_outer.grid(row=0, column=1, sticky="nsew")

        detail = detail_outer.inner
        detail.configure(style="Card.TFrame")

        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(3, weight=1)

        self.detail_title = ttk.Label(detail, text="Seleccione un programa…", style="CardTitle.TLabel")
        self.detail_title.grid(row=0, column=0, sticky="w")

        self.detail_sub = ttk.Label(detail, text="", style="Muted.TLabel")
        self.detail_sub.grid(row=1, column=0, sticky="w", pady=(6, 6))

        self.detail_path = ttk.Label(detail, text="", style="Muted.TLabel")
        self.detail_path.grid(row=2, column=0, sticky="w", pady=(0, 10))

        self.desc = ScrolledText(detail, wrap="word", height=10, bd=0, relief="flat")
        self.desc.grid(row=3, column=0, sticky="nsew")
        self.desc.configure(
            font=self.FONT_BODY,
            spacing1=3,   # espacio antes de párrafo
            spacing2=2,   # espacio entre líneas
            spacing3=3,   # espacio después de párrafo
            background=self.C_CARD_INNER,
            highlightthickness=0,
        )

        self.desc.configure(state="disabled")

        # Status bar
        status = ttk.Frame(root, style="App.TFrame")
        status.pack(fill="x", pady=(12, 0))
        ttk.Separator(status).pack(fill="x", pady=(0, 8))
        ttk.Label(status, textvariable=self.status_var, style="AppMuted.TLabel").pack(anchor="w")

    def _populate(self):
        q = (self.search_var.get() or "").strip().lower()

        for item in self.tree.get_children():
            self.tree.delete(item)
        self._index_map.clear()

        for idx, p in enumerate(PROGRAMS):
            # P1 se maneja desde el botón de OT, no se muestra en la lista
            if p.get("id") == "P1":
                continue
            hay = f"{p['id']} {p['title']} {p['subtitle']}".lower()
            if q and q not in hay:
                continue
            iid = self.tree.insert("", "end", values=(p["title"],))
            self._index_map.append((iid, idx))

        self.status_var.set(f"{len(self._index_map)} programa(s) disponible(s).")

        # Ajusta altura del Treeview según cantidad de items (mín 4, máx 10)
        n = len(self._index_map)
        self.tree.configure(height=max(4, min(10, n)))

    def _select_first(self):
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
            self.on_select()

    def _get_selected_program(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        for stored_iid, prog_idx in self._index_map:
            if stored_iid == iid:
                return PROGRAMS[prog_idx]
        return None

    def on_select(self, event=None):
        p = self._get_selected_program()
        if not p:
            return

        self.detail_title.configure(text=f"{p['id']} — {p['title']}")
        self.detail_sub.configure(text=p.get("subtitle", ""))
        # Si no quieres mostrar ruta, comenta la línea de abajo:

        self.desc.configure(state="normal")
        self.desc.delete("1.0", tk.END)
        self.desc.insert(tk.END, p.get("desc", ""))
        self.desc.configure(state="disabled")

        self.status_var.set(f"Seleccionado: {p['id']}")

    def import_ot_menu(self):
        """
        Ventanita de selección de flujo para OT:
        - Usar PDF desde Descargas (flujo actual import_ot_pdf).
        - Abrir P1 (selección avanzada desde el propio P1).
        """
        win = tk.Toplevel(self)
        win.title("Importar OT")
        win.configure(background=self.C_BG)
        # Ventana un poco más grande y cómoda
        win.geometry("780x420")

        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        header = ttk.Frame(win, padding=12, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")

        ttk.Label(header, text="🗃️ Importar OT", style="AppTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Elige el flujo que prefieras para trabajar con tus OTs.\n"
                "Puedes seguir usando el flujo guiado desde Descargas o abrir directamente el Programa 1 (P1)."
            ),
            style="AppMuted.TLabel",
            wraplength=740,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        body = ttk.Frame(win, padding=12, style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        # Tarjeta con las dos opciones
        card = RoundedCard(
            body,
            bg_card="#ffffff",
            bg_parent=self.C_BG,
            radius=18,
            padding=14,
            shadow=False,
            border_color=self.C_BORDER,
            border_width=1,
        )
        card.grid(row=0, column=0, sticky="nsew")

        inner = card.inner
        inner.configure(style="CardWhite.TFrame")
        inner.columnconfigure(0, weight=1)

        ttk.Label(
            inner,
            text="¿Cómo quieres importar la OT?",
            style="CardWhiteTitle.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        # Botón: flujo actual (Descargas + clasificar OT)
        def _use_downloads():
            win.destroy()
            self.import_ot_pdf()

        ClinicButton(
            inner,
            text="📥 Usar PDF desde Descargas y guardar en Escritorio/OTs",
            parent_bg="#ffffff",
            bg=self.C_ACTION_BLUE,
            hover_bg=self.C_ACTION_BLUE_H,
            command=_use_downloads,
            radius=16,
            height=44,
            font=self.FONT_BTN_SM,
            width=380,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).grid(row=1, column=0, sticky="w", pady=(4, 4))

        ttk.Label(
            inner,
            text=(
                "Este flujo te guía para seleccionar el PDF desde Descargas,\n"
                "e importarlo a una carpeta estructurada en Escritorio/OTs."
            ),
            style="CardWhiteMuted.TLabel",
            wraplength=720,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(0, 14))

    def _process_ot_by_kind(self, src: Path, kind: str, batch_mode: bool = False, overwrite_existing: bool = False) -> tuple[bool, str]:
        if not src.exists():
            return (False, f"No existe: {src.name}")

        dst_dir = _dest_dir_for_kind(kind)
        dst_dir.mkdir(parents=True, exist_ok=True)

        if kind == "UNIQUE":
            p1_script = HERE / "P1_ExtraerDatosPDF.py"
            try:
                args = [sys.executable, str(p1_script), str(src), str(dst_dir)]
                if batch_mode:
                    args.append("--silent")
                subprocess.run(args, cwd=str(HERE), check=False)
                return (True, f"Procesado UNIQUE → {dst_dir}")
            except Exception as e:
                return (False, f"Error UNIQUE: {e}")

        if kind.startswith("HALCYON_") or kind == "CONTROL DE CALIDAD":
            p5_script = (HERE / "P5_Extractor_Halcyon_corregido_v9_silent.py") if (HERE / "P5_Extractor_Halcyon_corregido_v9_silent.py").exists() else (HERE / "P5_Extractor_Halcyon.py")
            try:
                args = [sys.executable, str(p5_script), str(src), str(dst_dir)]
                if batch_mode:
                    args.append("--silent")
                subprocess.run(args, cwd=str(HERE), check=False)
                return (True, f"Procesado {kind} → {dst_dir}")
            except Exception as e:
                return (False, f"Error {kind}: {e}")

        dst = dst_dir / src.name
        try:
            if dst.exists() and not overwrite_existing:
                return (False, f"Omitido: ya existe {dst.name}")
            shutil.copy2(src, dst)
            return (True, f"Copiado → {dst}")
        except Exception as e:
            return (False, f"Error copiando {src.name}: {e}")

    def import_ot_pdfs_batch(self):
        start_dir = str(_downloads_dir())
        paths = filedialog.askopenfilenames(
            title="Selecciona una o varias OTs en PDF",
            initialdir=start_dir,
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")]
        )
        if not paths:
            self.status_var.set("Lote cancelado.")
            return

        rows = []
        for p in paths:
            src = Path(p)
            try:
                txt = extract_pdf_text(src)
            except Exception:
                txt = ""
            try:
                kind = classify_ot_pdf(src)
            except Exception:
                kind = None
            ot = _extract_ot_number_from_path_or_text(src, txt)
            shown_kind = _kind_label_for_batch(kind)
            destino = _dest_dir_for_kind(kind or "").relative_to(_desktop_dir())
            rows.append({
                "src": src,
                "ot": ot or "—",
                "kind": kind,
                "shown_kind": shown_kind,
                "dest": str(destino).replace(chr(92), "/"),
                "can_process": _can_auto_process_kind(kind),
            })

        self._open_batch_preview(rows)

    def _open_batch_preview(self, rows: list[dict]):
        win = tk.Toplevel(self)
        win.title("Vista previa lote OTs")
        win.configure(background=self.C_BG)
        win.geometry("1220x720")
        win.minsize(1100, 620)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(1, weight=1)

        header = ttk.Frame(win, padding=12, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="🗂️ Vista previa lote de OTs", style="AppTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Revisa la clasificación antes de procesar. "
                "Puedes cambiar manualmente el tipo/destino por fila. "
                "Si eliges solo un PDF, este mismo flujo también sirve para una sola OT."
            ),
            style="AppMuted.TLabel",
            wraplength=1180,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        body = ttk.Frame(win, padding=(12, 0, 12, 12), style="App.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        table_frame = ttk.Frame(body, style="App.TFrame")
        table_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        yscroll = AutoScrollbar(table_frame, orient="vertical")
        xscroll = AutoScrollbar(table_frame, orient="horizontal")

        tree = ttk.Treeview(
            table_frame,
            columns=("sel", "archivo", "ot", "tipo", "destino", "estado"),
            show="headings",
            selectmode="extended",
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set,
        )
        yscroll.config(command=tree.yview)
        xscroll.config(command=tree.xview)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        for col, title, width in [
            ("sel", "✓", 44),
            ("archivo", "Archivo", 260),
            ("ot", "OT", 90),
            ("tipo", "Tipo", 190),
            ("destino", "Destino", 360),
            ("estado", "Estado", 170),
        ]:
            tree.heading(col, text=title)
            tree.column(col, width=width, minwidth=width, anchor=("center" if col == "sel" else "w"), stretch=(col in ("archivo", "destino")))

        edit_card = RoundedCard(
            body,
            bg_card="#ffffff",
            bg_parent=self.C_BG,
            radius=18,
            padding=12,
            shadow=False,
            border_color=self.C_BORDER,
            border_width=1,
        )
        edit_card.grid(row=0, column=1, sticky="nsew")
        edit = edit_card.inner
        edit.configure(style="CardWhite.TFrame")
        edit.columnconfigure(0, weight=1)

        ttk.Label(edit, text="Edición manual", style="CardWhiteTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            edit,
            text="Selecciona una fila y, si quieres, cambia el tipo antes de procesar.",
            style="CardWhiteMuted.TLabel",
            wraplength=360,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        selected_file_var = tk.StringVar(value="Archivo: —")
        selected_ot_var = tk.StringVar(value="OT: —")
        selected_detected_var = tk.StringVar(value="Detectado: —")
        ttk.Label(edit, textvariable=selected_file_var, style="CardWhiteMuted.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Label(edit, textvariable=selected_ot_var, style="CardWhiteMuted.TLabel").grid(row=3, column=0, sticky="w", pady=(2, 0))
        ttk.Label(edit, textvariable=selected_detected_var, style="CardWhiteMuted.TLabel").grid(row=4, column=0, sticky="w", pady=(2, 10))

        ttk.Label(edit, text="Tipo / destino", style="CardWhiteTitle.TLabel").grid(row=5, column=0, sticky="w", pady=(0, 6))

        batch_options = [item["key"] for item in OT_BUTTONS]
        batch_options += ["HALCYON", "SIN CLASIFICAR"]
        batch_options = list(dict.fromkeys(batch_options))
        batch_kind_var = tk.StringVar(value="")
        combo = ttk.Combobox(edit, textvariable=batch_kind_var, values=batch_options, state="readonly", style="OT.TCombobox")
        combo.grid(row=6, column=0, sticky="ew")

        destino_var = tk.StringVar(value="Destino: —")
        estado_var = tk.StringVar(value="Estado: —")
        ttk.Label(edit, textvariable=destino_var, style="CardWhiteMuted.TLabel", wraplength=360, justify="left").grid(row=7, column=0, sticky="w", pady=(10, 0))
        ttk.Label(edit, textvariable=estado_var, style="CardWhiteMuted.TLabel", wraplength=360, justify="left").grid(row=8, column=0, sticky="w", pady=(4, 12))

        help_var = tk.StringVar(value="Consejo: HALCYON genérico y SIN CLASIFICAR puedes corregirlos aquí antes de procesar.")
        ttk.Label(edit, textvariable=help_var, style="CardWhiteMuted.TLabel", wraplength=360, justify="left").grid(row=9, column=0, sticky="w", pady=(0, 12))

        iid_to_row = {}
        allowed_manual = set(batch_options)

        def _is_checked(iid: str) -> bool:
            return bool(iid_to_row[iid].get("checked"))

        def _set_checked(iid: str, checked: bool):
            iid_to_row[iid]["checked"] = bool(checked)
            vals = list(tree.item(iid, "values"))
            if vals:
                vals[0] = "☑" if checked else "☐"
                tree.item(iid, values=tuple(vals))

        def _sync_tree_selection_from_checks():
            tree.selection_remove(*tree.selection())
            for _iid in iid_to_row:
                if _is_checked(_iid):
                    tree.selection_add(_iid)

        def _normalize_kind_for_manual(kind: str | None) -> str:
            if not kind:
                return "SIN CLASIFICAR"
            return kind

        def _refresh_row(iid: str):
            row = iid_to_row[iid]
            kind = row.get("manual_kind") or row.get("kind")
            if kind == "SIN CLASIFICAR":
                shown_kind = "SIN CLASIFICAR"
                dest = _dest_dir_for_kind("")
                can_process = False
            else:
                shown_kind = _kind_label_for_batch(kind)
                dest = _dest_dir_for_kind(kind)
                can_process = _can_auto_process_kind(kind)
            row["shown_kind"] = shown_kind
            row["dest"] = str(dest.relative_to(_desktop_dir())).replace(chr(92), "/")
            row["can_process"] = can_process
            row["manual_kind"] = kind
            estado = "Listo para procesar" if can_process else "Revisión manual"
            tree.item(iid, values=("☑" if row.get("checked") else "☐", row["src"].name, row["ot"], shown_kind, row["dest"], estado))

        for row in rows:
            row["manual_kind"] = _normalize_kind_for_manual(row.get("kind"))
            row["checked"] = bool(row.get("can_process"))
            iid = tree.insert("", "end", values=("☑" if row["checked"] else "☐", row["src"].name, row["ot"], row["shown_kind"], row["dest"], "Listo para procesar" if row["can_process"] else "Revisión manual"))
            iid_to_row[iid] = row
        _sync_tree_selection_from_checks()

        def _update_editor_from_selection(event=None):
            selected = tree.selection()
            if not selected:
                selected_file_var.set("Archivo: —")
                selected_ot_var.set("OT: —")
                selected_detected_var.set("Detectado: —")
                batch_kind_var.set("")
                destino_var.set("Destino: —")
                estado_var.set("Estado: —")
                return
            iid = selected[0]
            row = iid_to_row[iid]
            selected_file_var.set(f"Archivo: {row['src'].name}")
            selected_ot_var.set(f"OT: {row['ot']}")
            detected = row.get('kind') or 'SIN CLASIFICAR'
            selected_detected_var.set(f"Detectado: {detected}")
            batch_kind_var.set(row.get("manual_kind") or _normalize_kind_for_manual(row.get("kind")))
            destino_var.set(f"Destino: {row['dest']}")
            estado_var.set("Estado: Listo para procesar" if row["can_process"] else "Estado: Revisión manual")

        tree.bind("<<TreeviewSelect>>", _update_editor_from_selection)

        def _toggle_checked_from_event(event=None):
            iid = tree.identify_row(event.y)
            col = tree.identify_column(event.x)
            if not iid or col != "#1":
                return
            _set_checked(iid, not _is_checked(iid))
            _sync_tree_selection_from_checks()
            _update_editor_from_selection()
            checked_count = sum(1 for _iid in iid_to_row if _is_checked(_iid))
            status_var.set(f"Marcadas {checked_count} fila(s) para procesar.")
            return "break"

        tree.bind("<Button-1>", _toggle_checked_from_event, add=False)

        def _apply_manual_kind():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Nada seleccionado", "Selecciona una o varias filas para aplicar el cambio.")
                return
            new_kind = (batch_kind_var.get() or "").strip()
            if new_kind not in allowed_manual:
                messagebox.showwarning("Tipo inválido", "Selecciona un tipo válido.")
                return
            for iid in selected:
                iid_to_row[iid]["manual_kind"] = new_kind
                _refresh_row(iid)
                if iid_to_row[iid]["can_process"] and not _is_checked(iid):
                    _set_checked(iid, True)
            _update_editor_from_selection()
            status_var.set(f"Actualizado manualmente el tipo de {len(selected)} fila(s).")

        footer = ttk.Frame(body, style="App.TFrame")
        footer.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        footer.columnconfigure(1, weight=1)

        status_var = tk.StringVar(value=f"{len(rows)} archivo(s) analizado(s).")
        ttk.Label(footer, textvariable=status_var, style="AppMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))

        def _select_auto():
            for iid, row in iid_to_row.items():
                _set_checked(iid, bool(row["can_process"]))
            _sync_tree_selection_from_checks()
            _update_editor_from_selection()
            status_var.set("Marcadas todas las OTs procesables automáticamente.")

        def _check_all():
            for iid in iid_to_row:
                _set_checked(iid, True)
            _sync_tree_selection_from_checks()
            _update_editor_from_selection()
            status_var.set("Marcadas todas las filas.")

        def _uncheck_all():
            for iid in iid_to_row:
                _set_checked(iid, False)
            _sync_tree_selection_from_checks()
            _update_editor_from_selection()
            status_var.set("Desmarcadas todas las filas.")

        def _process_selected():
            selected = [iid for iid in iid_to_row if _is_checked(iid)]
            if not selected:
                messagebox.showwarning("Nada seleccionado", "Marca una o varias filas para procesar.")
                return

            ok_count = 0
            fail_count = 0
            skipped = 0
            detalles = []
            overwrite_all = False
            skip_all_existing = False
            cancelled = False

            for iid in selected:
                row = iid_to_row[iid]
                kind = row.get("manual_kind") or row.get("kind")
                if kind == "SIN CLASIFICAR" or not row["can_process"]:
                    skipped += 1
                    detalles.append(f"• {row['src'].name}: requiere revisión manual ({row['shown_kind']})")
                    continue

                conflicts = _existing_output_conflicts(row["src"], kind, row.get("ot"))
                overwrite_existing = False

                if conflicts:
                    if skip_all_existing:
                        skipped += 1
                        detalles.append(f"• {row['src'].name}: omitido (ya existen PDF/TXT en destino)")
                        continue

                    if overwrite_all:
                        overwrite_existing = True
                    else:
                        lines = [str(x) for x in conflicts[:8]]
                        extra = "" if len(conflicts) <= 8 else f"\n... y {len(conflicts)-8} salida(s) más."
                        resp = messagebox.askyesnocancel(
                            "Salida ya existe",
                            "Ya existen estas salidas para esta OT:\n\n"
                            + "\n".join(lines)
                            + extra
                            + "\n\nSí = sobrescribir esta OT\n"
                              "No = omitir esta OT\n"
                              "Cancelar = cancelar todo el lote"
                        )
                        if resp is None:
                            cancelled = True
                            detalles.append("• Lote cancelado por el usuario.")
                            break
                        if resp:
                            overwrite_existing = True
                            if messagebox.askyesno(
                                "Sobrescribir siguientes",
                                "¿Quieres sobrescribir automáticamente las próximas OTs que ya tengan PDF/TXT sin volver a preguntar?"
                            ):
                                overwrite_all = True
                        else:
                            skipped += 1
                            detalles.append(f"• {row['src'].name}: omitido (ya existen PDF/TXT en destino)")
                            if messagebox.askyesno(
                                "Omitir siguientes",
                                "¿Quieres omitir automáticamente las próximas OTs que ya tengan PDF/TXT sin volver a preguntar?"
                            ):
                                skip_all_existing = True
                            continue

                ok, msg = self._process_ot_by_kind(
                    row["src"],
                    kind,
                    batch_mode=True,
                    overwrite_existing=overwrite_existing,
                )
                if ok:
                    ok_count += 1
                else:
                    fail_count += 1
                detalles.append(f"• {row['src'].name}: {msg}")

            status_var.set(f"Procesadas: {ok_count} | Con error: {fail_count} | Omitidas: {skipped}")
            resumen = "\n".join(detalles[:20])
            if len(detalles) > 20:
                resumen += f"\n... y {len(detalles)-20} más."
            if cancelled:
                resumen = (resumen + "\n\nEl lote fue cancelado antes de terminar.").strip()
            messagebox.showinfo("Resultado lote", resumen or "Sin resultados.")
            self.status_var.set(status_var.get())

        btns = ttk.Frame(footer, style="App.TFrame")
        btns.grid(row=0, column=2, sticky="e")

        action_row = ttk.Frame(edit, style="CardWhite.TFrame")
        action_row.grid(row=10, column=0, sticky="ew", pady=(0, 10))
        action_row.columnconfigure(0, weight=1)
        action_row.columnconfigure(1, weight=1)

        ClinicButton(
            action_row,
            text="Marcar todas",
            parent_bg="#ffffff",
            bg=self.C_ACTION_BLUE,
            hover_bg=self.C_ACTION_BLUE_H,
            command=_check_all,
            radius=14,
            height=40,
            font=self.FONT_BTN_SM,
            width=170,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        ClinicButton(
            action_row,
            text="Desmarcar todas",
            parent_bg="#ffffff",
            bg="#475569",
            hover_bg="#64748b",
            command=_uncheck_all,
            radius=14,
            height=40,
            font=self.FONT_BTN_SM,
            width=170,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).grid(row=0, column=1, sticky="e")

        ClinicButton(
            edit,
            text="Aplicar tipo a seleccionadas",
            parent_bg="#ffffff",
            bg=self.C_ACTION_BLUE,
            hover_bg=self.C_ACTION_BLUE_H,
            command=_apply_manual_kind,
            radius=14,
            height=40,
            font=self.FONT_BTN_SM,
            width=260,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).grid(row=11, column=0, sticky="w", pady=(0, 8))

        ClinicButton(
            btns,
            text="Seleccionar automáticas",
            parent_bg=self.C_BG,
            bg=self.C_ACTION_BLUE,
            hover_bg=self.C_ACTION_BLUE_H,
            command=_select_auto,
            radius=14,
            height=40,
            font=self.FONT_BTN_SM,
            width=220,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).pack(side="left", padx=(0, 8))

        ClinicButton(
            btns,
            text="Procesar seleccionadas",
            parent_bg=self.C_BG,
            bg=self.C_ACTION_GREEN,
            hover_bg=self.C_ACTION_GREEN_H,
            command=_process_selected,
            radius=14,
            height=40,
            font=self.FONT_BTN_SM,
            width=220,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).pack(side="left", padx=(0, 8))

        ClinicButton(
            btns,
            text="Cerrar",
            parent_bg=self.C_BG,
            bg="#64748b",
            hover_bg="#475569",
            command=win.destroy,
            radius=14,
            height=40,
            font=self.FONT_BTN_SM,
            width=120,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).pack(side="left")

        if rows:
            first = tree.get_children()[0]
            tree.selection_set(first)
            tree.focus(first)
            _update_editor_from_selection()

    def import_ot_pdf(self):
        # 1) Abrir selector en Descargas
        start_dir = str(_downloads_dir())
        path = filedialog.askopenfilename(
            title="Selecciona OT en PDF",
            initialdir=start_dir,
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")]
        )
        if not path:
            self.status_var.set("Cancelado.")
            return

        src = Path(path)
        if not src.exists():
            messagebox.showerror("No encontrado", f"No existe:\n{src}")
            return

        # 2) Guardar como OT “pendiente”
        self._pending_ot_src = src

        # ✅ Autodetección del tipo de OT (por texto/nombre)
        detected = None
        try:
            detected = classify_ot_pdf(src)  # "UNIQUE", "HALCYON_1", "HALCYON_2", "HALCYON", "SIEMENS", etc.
        except Exception:
            detected = None

        self._pending_ot_detected_kind = detected

        # 3) Abrir ventana OT (ahora sirve para elegir tipo y guardar)
        self.open_ot_window()

    def open_ot_window(self):
        # Si ya está abierta, solo enfoca
        if self._ot_win is not None and self._ot_win.winfo_exists():
            self._ot_win.deiconify()
            self._ot_win.lift()
            self._ot_win.focus_force()
            return

        win = tk.Toplevel(self)
        win.configure(background=self.C_BG)
        self._ot_win = win
        win.title("OT")
        win.geometry("900x520")

        # Permite que el texto crezca
        win.columnconfigure(0, weight=1)
        win.rowconfigure(2, weight=1)

        # Encabezado
        header = ttk.Frame(win, padding=12, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")

        ttk.Label(header, text="🗃️ OT", style="AppTitle.TLabel").pack(anchor="w")

        ttk.Label(
            header,
            text="Selecciona el equipo/tipo y presiona “Guardar OT”.",
            style="AppMuted.TLabel"
        ).pack(anchor="w", pady=(4, 0))

        help_txt = (
            "Flujo:\n"
            "• Primero eliges el PDF desde Descargas.\n"
            "• Aquí seleccionas el equipo/tipo.\n"
            "• UNIQUE además genera Resumen (TXT + UNIQUE.xlsx) en la subcarpeta “Resumen”."
        )
        ttk.Label(
            header,
            text=help_txt,
            style="AppMuted.TLabel",
            wraplength=860,
            justify="left"
        ).pack(anchor="w", pady=(8, 0))

        pdf_name = (self._pending_ot_src.name if self._pending_ot_src else "Ninguno")
        self._ot_file_lbl = ttk.Label(
            header,
            text=f"PDF seleccionado: {pdf_name}",
            style="AppMuted.TLabel"
        )
        self._ot_file_lbl.pack(anchor="w", pady=(6, 0))

        # ----- Selector (Combo) + Botón Guardar -----
        select_card = RoundedCard(
            win,
            bg_card="#ffffff",
            # bg_card=self.C_CARD,
            bg_parent=self.C_BG,
            radius=18,
            padding=12,
            shadow=False,
        )
        select_card.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        selector = select_card.inner

        selector.configure(style="CardWhite.TFrame")
        ttk.Label(selector, text="Equipo / Tipo de OT", style="CardWhiteTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )

        # selector.columnconfigure(0, weight=1)
        # selector.columnconfigure(1, weight=0)

        # Asegura que existan (por si no los inicializaste en __init__)
        if not hasattr(self, "_ot_kind_var") or self._ot_kind_var is None:
            self._ot_kind_var = tk.StringVar()
        if not hasattr(self, "_ot_kind_map") or self._ot_kind_map is None:
            self._ot_kind_map = {}

        # Mapa label -> key
        self._ot_kind_map = {item["label"]: item["key"] for item in OT_BUTTONS}

        combo = ttk.Combobox(
            selector,
            textvariable=self._ot_kind_var,
            values=[item["label"] for item in OT_BUTTONS],
            state="readonly",
            style="OT.TCombobox",
            font=self.FONT_BODY,
        )
        combo.grid(row=1, column=0, sticky="ew", padx=(0, 10), ipady=4)

        def _on_combo_change(event=None):
            label = (self._ot_kind_var.get() or "").strip()
            key = self._ot_kind_map.get(label)

            if key:
                self._ot_show_desc(key)

            # ✅ regla de habilitación del botón Guardar
            if self._pending_ot_src is None:
                self._ot_save_btn.set_enabled(False)
                return

            detected = getattr(self, "_pending_ot_detected_kind", None)

            # Si detectamos HALCYON genérico, obligar a elegir un HALCYON_n
            if detected == "HALCYON":
                if key and str(key).startswith("HALCYON_"):
                    self._ot_save_btn.set_enabled(True)
                else:
                    self._ot_save_btn.set_enabled(False)
                return

            # Caso normal: si hay PDF, permitir guardar
            self._ot_save_btn.set_enabled(True)

        combo.bind("<<ComboboxSelected>>", _on_combo_change)

        # Botón grande Guardar (guardamos referencia para habilitar/deshabilitar)
        self._ot_save_btn = ClinicButton(
            selector,
            text="💾 Guardar OT",
            parent_bg="#ffffff",
            bg=self.C_ACTION_GREEN,
            hover_bg=self.C_ACTION_GREEN_H,
            command=self._ot_save_selected_kind,
            radius=16,
            height=44,
            font=self.FONT_BTN_SM,
            width=220,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        )
        self._ot_save_btn.grid(row=1, column=1, sticky="e")

        # ✅ Selección inicial (autodetectada si se puede)
        detected = getattr(self, "_pending_ot_detected_kind", None)

        # Map key -> label (porque el combo usa labels)
        key_to_label = {item["key"]: item["label"] for item in OT_BUTTONS}

        if detected == "HALCYON":
            # Caso especial: sabemos que es Halcyon, pero no su número
            self._ot_kind_var.set("")
            self._ot_show_desc("HALCYON_1")
            self._ot_save_btn.set_enabled(False)

            messagebox.showinfo(
                "HALCYON detectado",
                "Detecté que este PDF corresponde a HALCYON, pero no pude distinguir si es HALCYON1, HALCYON2 u otro.\n\n"
                "Por favor selecciona manualmente el equipo HALCYON correspondiente antes de guardar."
            )
        elif detected in key_to_label:
            self._ot_kind_var.set(key_to_label[detected])
            self._ot_save_btn.set_enabled(True)
            self.status_var.set(f"OT detectada automáticamente: {detected}")
        else:
            # fallback
            self._ot_kind_var.set("UNIQUE")
            self._ot_save_btn.set_enabled(True)
            if detected is None:
                self.status_var.set("No se pudo detectar el tipo de OT (selecciona manualmente).")
            else:
                self.status_var.set(f"OT detectada: {detected} (no mapeada al combo).")

        _on_combo_change()

        # Panel descripción
        body = ttk.Frame(win, padding=(12, 0, 12, 12), style="App.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self._ot_title_lbl = ttk.Label(body, text="Seleccione un tipo…", style="CardTitle.TLabel")
        self._ot_title_lbl.grid(row=0, column=0, sticky="w", pady=(0, 10))

        self._ot_desc_box = ScrolledText(body, wrap="word", bd=0, relief="flat")
        self._ot_desc_box.grid(row=1, column=0, sticky="nsew")
        self._ot_desc_box.configure(
            font=self.FONT_BODY,
            spacing1=3,
            spacing2=2,
            spacing3=3,
            background=self.C_CARD_INNER,
            highlightthickness=0,
        )
        self._ot_desc_box.configure(state="disabled")

        # Si no hay PDF seleccionado, deshabilita Guardar y avisa
        if self._pending_ot_src is None:
            self._ot_save_btn.set_enabled(False)
            messagebox.showinfo("OT", "Primero selecciona un PDF desde el botón principal.")
        else:
            self._ot_save_btn.set_enabled(True)

        # Si cierran la ventana, limpia referencia
        def _on_close():
            self._ot_win = None
            self._ot_title_lbl = None
            self._ot_desc_box = None
            self._ot_file_lbl = None
            self._ot_save_btn = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _ot_show_desc(self, key: str):
        if not (self._ot_win and self._ot_win.winfo_exists()):
            return
        if self._ot_title_lbl is None or self._ot_desc_box is None:
            return

        item = next((x for x in OT_BUTTONS if x["key"] == key), None)
        if not item:
            return

        self._ot_title_lbl.configure(text=f"{item['label']}")

        self._ot_desc_box.configure(state="normal")
        self._ot_desc_box.delete("1.0", tk.END)
        self._ot_desc_box.insert(tk.END, item.get("desc", ""))
        self._ot_desc_box.configure(state="disabled")

    def _ot_save_selected_kind(self):
        # Lee lo elegido en el combo y llama al guardado real
        label = (self._ot_kind_var.get() or "").strip()
        key = self._ot_kind_map.get(label)

        if not key:
            messagebox.showwarning("Tipo no seleccionado", "Selecciona un tipo/equipo antes de guardar.")
            return
        
        detected = getattr(self, "_pending_ot_detected_kind", None)
        if key == "UNIQUE" and detected and detected != "UNIQUE":
            ok = messagebox.askyesno(
                "Advertencia",
                f"Este PDF parece ser: {detected}\n\n"
                "Si lo guardas como UNIQUE se ejecutará P1 y puede generar TXT/Excel incorrectos.\n\n"
                "¿Quieres guardarlo igual como UNIQUE?"
            )
            if not ok:
                return


        self._ot_select_and_save(key)

    def _ot_select_and_save(self, key: str):
        self._ot_show_desc(key)

        src = self._pending_ot_src
        if src is None:
            messagebox.showwarning("Sin PDF", "Primero selecciona un PDF desde el botón principal.")
            return
        if not src.exists():
            messagebox.showerror("No encontrado", f"El PDF ya no existe:\n{src}")
            return

        desktop = _desktop_dir()
        base = desktop / "OTs"
        kind = key

        # Regla de carpetas por tipo
        if kind == "UNIQUE":
            dst_dir = base / "ICLINIC" / "UNIQUE"
        elif kind.startswith("HALCYON_"):
            dst_dir = base / "ECM" / kind
        elif kind == "CONTROL DE CALIDAD":
            dst_dir = base / "ECM" / "CONTROL_DE_CALIDAD"
        elif kind == "SIEMENS":
            dst_dir = base / "SIEMENS"
        else:
            dst_dir = base / "OTs_OTROS" / kind

        dst_dir.mkdir(parents=True, exist_ok=True)

        # Validar conflictos visibles (PDF/TXT) antes de ejecutar extractores.
        try:
            txt_preview = extract_pdf_text(src)
        except Exception:
            txt_preview = ""
        ot_preview = _extract_ot_number_from_path_or_text(src, txt_preview)
        conflicts = _existing_output_conflicts(src, kind, ot_preview)
        if conflicts:
            lines = [str(x) for x in conflicts[:8]]
            extra = "" if len(conflicts) <= 8 else f"\n... y {len(conflicts)-8} salida(s) más."
            resp = messagebox.askyesno(
                "Salida existente",
                "Ya existen estas salidas para la OT seleccionada:\n\n"
                + "\n".join(lines)
                + extra
                + "\n\n¿Quieres sobrescribirlas?"
            )
            if not resp:
                self.status_var.set("No se sobrescribió la OT seleccionada.")
                return

        # ✅ CASO UNIQUE: NO COPIAMOS el PDF aquí.
        # Dejamos que P1 lo guarde con SU formato (y así queda solo 1 PDF en la carpeta).
        if kind == "UNIQUE":
            p1_script = HERE / "P1_ExtraerDatosPDF.py"
            run_script(p1_script, args=[str(src), str(dst_dir)])
            self.status_var.set("UNIQUE: ejecutando extracción + guardado desde P1…")
            return

        # ✅ CASO HALCYON_n o CONTROL DE CALIDAD: usa P5
        if kind.startswith("HALCYON_") or kind == "CONTROL DE CALIDAD":
            p5_script = (HERE / "P5_Extractor_Halcyon_corregido_v9_silent.py") if (HERE / "P5_Extractor_Halcyon_corregido_v9_silent.py").exists() else (HERE / "P5_Extractor_Halcyon.py")
            run_script(p5_script, args=[str(src), str(dst_dir)])
            self.status_var.set(f"{kind}: ejecutando extracción + guardado desde P5…")
            return
        
        # ✅ RESTO (SIEMENS, etc.): copiamos normalmente (porque no usan P1)
        dst = dst_dir / src.name

        if dst.exists():
            ok = messagebox.askyesno("Archivo existe", f"Ya existe:\n{dst.name}\n\n¿Quieres reemplazarlo?")
            if not ok:
                self.status_var.set("No se reemplazó (cancelado).")
                return

        try:
            shutil.copy2(src, dst)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo copiar:\n{src}\n→ {dst}\n\n{e}")
            return

        try:
            rel = dst.relative_to(_desktop_dir())
            pretty = str(rel).replace("\\", "/")
        except Exception:
            pretty = str(dst)

        self.status_var.set(f"OT guardada en: {pretty}")
        messagebox.showinfo("Listo", f"Guardado en:\n{pretty}")

        self._pending_ot_src = None
        if self._ot_file_lbl is not None:
            self._ot_file_lbl.configure(text="PDF seleccionado: Ninguno")

    def open_selected(self):
        p = self._get_selected_program()
        if not p:
            self.status_var.set("Selecciona un programa para abrir.")
            return
        self.status_var.set(f"Abriendo {p['id']}…")
        run_script(p["script"])

def main():
    # Primero mostrar ventana de login
    login = LoginWindow()
    login.mainloop()
    
    # Solo abrir el launcher si el login fue exitoso
    if login.login_successful:
        app = Launcher()
        app.mainloop()
    else:
        # Si el usuario cerró la ventana sin hacer login, salir
        sys.exit(0)

if __name__ == "__main__":
    main()

