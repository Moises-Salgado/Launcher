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
                if vv in ("HALCYON_1", "HALCYON_2"):
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

# 👇 Rellena esto con los seriales reales de tus 2 equipos:
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
            "Descripción de control de calidad.\n\n"
            "• (Edita esta descripción a tu gusto)\n"
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

def run_script(path: Path):
    if not path.exists():
        messagebox.showerror("No encontrado", f"No existe:\n{path}")
        return
    try:
        subprocess.Popen([sys.executable, str(path)], cwd=str(HERE))
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
            return HALCYON_SERIAL_MAP[serial]  # HALCYON_1 o HALCYON_2
        return "HALCYON"  # si no se puede distinguir cuál

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

        # Enter abre el seleccionado
        self.bind("<Return>", lambda e: self.open_selected())

        # Esc: si está fullscreen, sale; si no, cierra (opcional)
        def on_escape(event=None):
            if getattr(self, "_is_fullscreen", False):
                self._is_fullscreen = False
                self.attributes("-fullscreen", False)
            else:
                self.destroy()

        self.bind("<Escape>", on_escape)

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
        self.C_CARD = "#82b7fc"        # tarjeta levemente tintada
        self.C_CARD_INNER = "#82b7fc"  # blanco puro solo para áreas de lectura
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
        self.C_ACTION_BLUE = "#436ee4"
        self.C_ACTION_BLUE_H = "#2563eb"
        self.C_ACTION_GREEN = "#16a34a"
        self.C_ACTION_GREEN_H = "#22c55e"

        # Tipografías
        self.FONT_TITLE = ("TkDefaultFont", 30, "bold")
        self.FONT_SUB = ("TkDefaultFont", 10)
        self.FONT_H2 = ("TkDefaultFont", 11, "bold")
        self.FONT_SUB_BIG = (self.UI_FONT, 12)   # prueba 11 o 12

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
            text="🗃️ OT",
            parent_bg=self.C_CARD,
            bg=self.C_ACTION_BLUE,
            hover_bg=self.C_ACTION_BLUE_H,
            command=self.open_ot_window,
            radius=16,
            height=44,
            font=self.FONT_BTN_SM,
            width=280,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).pack(pady=(15, 0), anchor="center")

        ClinicButton(
            sidebar,
            text="🗃️ Guardar OT (PDF) en Escritorio/OTs",
            parent_bg=self.C_CARD,
            bg=self.C_ACTION_BLUE,
            hover_bg=self.C_ACTION_BLUE_H,
            command=self.import_ot_pdf,
            radius=16,
            height=44,
            font=self.FONT_BTN_SM,
            width=280,
            shadow=True,
            shadow_offset=1,
            shadow_color="#1e293b",
        ).pack(pady=(10, 0), anchor="center")

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
            columns=("id", "name"),
            show="headings",
            selectmode="browse",
            height=4, #Espacio vertical para 4 programas P1, P2, P3, P4
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

        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Programa")

        # ID fijo; Programa se adapta al ancho disponible
        self.tree.column("id", width=55, minwidth=55, stretch=False, anchor="center")
        self.tree.column("name", width=1, minwidth=220, stretch=True, anchor="w")

        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.open_selected())

        # Detail card
        detail_outer = ttk.Frame(main, style="Card.TFrame", padding=14)
        detail_outer.grid(row=0, column=1, sticky="nsew")

        detail_outer.columnconfigure(0, weight=1)
        detail_outer.rowconfigure(3, weight=1)

        self.detail_title = ttk.Label(detail_outer, text="Seleccione un programa…", style="CardTitle.TLabel")
        self.detail_title.grid(row=0, column=0, sticky="w")

        self.detail_sub = ttk.Label(detail_outer, text="", style="Muted.TLabel")
        self.detail_sub.grid(row=1, column=0, sticky="w", pady=(6, 6))

        self.detail_path = ttk.Label(detail_outer, text="", style="Muted.TLabel")
        self.detail_path.grid(row=2, column=0, sticky="w", pady=(0, 10))

        self.desc = ScrolledText(detail_outer, wrap="word", height=10, bd=1, relief="solid")
        self.desc.grid(row=3, column=0, sticky="nsew")
        self.desc.configure(
            font=self.FONT_BODY,
            spacing1=3,   # espacio antes de párrafo
            spacing2=2,   # espacio entre líneas
            spacing3=3,   # espacio después de párrafo
        )

        self.desc.configure(state="disabled")

        # Status bar
        status = ttk.Frame(root, style="App.TFrame")
        status.pack(fill="x", pady=(12, 0))
        ttk.Separator(status).pack(fill="x", pady=(0, 8))
        ttk.Label(status, textvariable=self.status_var, style="HeaderSub.TLabel").pack(anchor="w")

    def _populate(self):
        q = (self.search_var.get() or "").strip().lower()

        for item in self.tree.get_children():
            self.tree.delete(item)
        self._index_map.clear()

        for idx, p in enumerate(PROGRAMS):
            hay = f"{p['id']} {p['title']} {p['subtitle']}".lower()
            if q and q not in hay:
                continue
            iid = self.tree.insert("", "end", values=(p["id"], p["title"]))
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

        # 2) Identificar tipo
        kind = classify_ot_pdf(src)

        if kind is None:
            # No se pudo determinar nada -> preguntar UNIQUE vs Halcyon
            ans = messagebox.askquestion(
                "Tipo de OT",
                "No pude determinar el tipo automáticamente.\n\n"
                "¿Es UNIQUE?\n\n"
                "Sí = UNIQUE\nNo = HALCYON"
            )
            kind = "UNIQUE" if ans == "yes" else "HALCYON"

        if kind == "HALCYON":
            # Intentar aprender por serial (sin depender del nombre)
            name = (src.name or "").lower()
            text = extract_pdf_text(src).lower()
            blob = f"{name}\n{text}"
            blob = blob.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
            blob = re.sub(r"\s+", " ", blob)

            serial = _find_halcyon_serial(blob)

            if serial:
                # 1) si ya está mapeado: listo
                if serial in HALCYON_SERIAL_MAP:
                    kind = HALCYON_SERIAL_MAP[serial]

                else:
                    # 2) si NO está mapeado: preguntar 1 vez y guardar
                    q = messagebox.askquestion(
                        "Detecté el equipo Halcyon",
                        f"Encontré el número de serie: {serial}\n\n"
                        "¿Este equipo es HALCYON 1?\n\n"
                        "Sí = HALCYON 1\nNo = HALCYON 2"
                    )
                    kind = "HALCYON_1" if q == "yes" else "HALCYON_2"
                    HALCYON_SERIAL_MAP[serial] = kind
                    save_halcyon_map(HALCYON_SERIAL_MAP)

            else:
                # No pude extraer serial -> recién aquí pregunto
                q = messagebox.askquestion(
                    "Halcyon",
                    "No pude detectar el número de serie en el PDF.\n\n"
                    "¿Es Halcyon 1?\n\nSí = Halcyon 1\nNo = Halcyon 2"
                )
                kind = "HALCYON_1" if q == "yes" else "HALCYON_2"

        # 3) Crear carpeta destino: Escritorio/OTs/<TIPO>
        desktop = _desktop_dir()
        base = desktop / "OTs"

        # Regla de carpetas por tipo
        if kind == "UNIQUE":
            dst_dir = base / "ICLINIC" / "UNIQUE"
        elif kind in ("HALCYON_1", "HALCYON_2"):
            dst_dir = base / "ECM" / kind
        else:
            # fallback por si aparece algo nuevo
            dst_dir = base / "OTs_OTROS" / kind

        dst_dir.mkdir(parents=True, exist_ok=True)


        dst = dst_dir / src.name

        if dst.exists():
            ok = messagebox.askyesno(
                "Archivo existe",
                f"Ya existe:\n{dst.name}\n\n¿Quieres reemplazarlo?"
            )
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

        title = ttk.Label(header, text="🗃️ OT", style="AppTitle.TLabel")
        title.pack(anchor="w")
        sub = ttk.Label(
            header,
            text="Selecciona un tipo para ver la descripción.",
            style="Muted.TLabel")
        sub.pack(anchor="w", pady=(4, 0))

        # Botonera horizontal
        btn_card = RoundedCard(
            win,
            bg_card=self.C_CARD,
            bg_parent=self.C_BG,
            radius=18,
            padding=10,
            shadow=False,  # en OT suele verse mejor sin sombra aquí
        )
        btn_card.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        btn_frame = btn_card.inner

        for i in range(len(OT_BUTTONS)):
            btn_frame.columnconfigure(i, weight=1)

        for i, item in enumerate(OT_BUTTONS):
            btn = ClinicButton(
                btn_frame,
                text=item["label"],
                parent_bg=self.C_BG,   # o self.C_CARD si prefieres blanco
                bg=self.C_ACTION_BLUE,
                hover_bg=self.C_ACTION_BLUE_H,
                command=lambda k=item["key"]: self._ot_show_desc(k),
                radius=14,
                height=40,
                font=("TkDefaultFont", 9, "bold"),
            )
            btn.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))

        # Panel descripción
        body = ttk.Frame(win, padding=(12, 0, 12, 12), style="App.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self._ot_title_lbl = ttk.Label(body, text="Seleccione un tipo…", style="CardTitle.TLabel")
        self._ot_title_lbl.grid(row=0, column=0, sticky="w", pady=(0, 10))

        self._ot_desc_box = ScrolledText(body, wrap="word", bd=1, relief="solid")
        self._ot_desc_box.grid(row=1, column=0, sticky="nsew")
        self._ot_desc_box.configure(
            font=self.FONT_BODY,
            spacing1=3,
            spacing2=2,
            spacing3=3,
        )
        self._ot_desc_box.configure(state="disabled")

        # Selección inicial
        self._ot_show_desc("UNIQUE")

        # Si cierran la ventana, limpia referencia
        def _on_close():
            self._ot_win = None
            self._ot_title_lbl = None
            self._ot_desc_box = None
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

    def open_selected(self):
        p = self._get_selected_program()
        if not p:
            self.status_var.set("Selecciona un programa para abrir.")
            return
        self.status_var.set(f"Abriendo {p['id']}…")
        run_script(p["script"])


def main():
    Launcher().mainloop()


if __name__ == "__main__":
    main()
