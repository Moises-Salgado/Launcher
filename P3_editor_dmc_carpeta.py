import os
import re
import shutil
import tkinter as tk
from ui_theme import *
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from datetime import datetime
from collections import Counter
from typing import Optional, Tuple, List, Set
import numpy as np
from PIL import Image, ImageTk, ImageDraw
from collections import Counter, defaultdict
import pydicom
from pydicom.misc import is_dicom

# Orden de prioridad para fecha (mejor → peor)
DATE_TAG_ORDER = ["AcquisitionDateTime", "AcquisitionDate", "ContentDate", "SeriesDate", "StudyDate"]

# -------------------------
# Helpers RUT / Fecha
# -------------------------
def dicom_to_pil(ds) -> Image.Image:
    """
    Convierte ds.pixel_array a PIL.Image aplicando:
    - multi-frame: usa primer frame
    - rescale slope/intercept (CT)
    - windowing si existe (WindowCenter/Width)
    - normalización a 8-bit
    """
    if "PixelData" not in ds:
        raise ValueError("Este DICOM no contiene PixelData (no hay imagen).")

    arr = ds.pixel_array

    # Multi-frame
    if arr.ndim == 3 and arr.shape[-1] not in (3, 4):
        # (frames, rows, cols)
        arr = arr[0]
    elif arr.ndim == 4:
        # (frames, rows, cols, channels)
        arr = arr[0]

    # Color (RGB)
    if arr.ndim == 3 and arr.shape[-1] in (3, 4):
        if arr.shape[-1] == 4:
            arr = arr[:, :, :3]
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        return Image.fromarray(arr, mode="RGB")

    # Monocromo
    arr = arr.astype(np.float32)

    slope = float(getattr(ds, "RescaleSlope", 1.0) or 1.0)
    intercept = float(getattr(ds, "RescaleIntercept", 0.0) or 0.0)
    arr = arr * slope + intercept

    # Windowing
    wc = getattr(ds, "WindowCenter", None)
    ww = getattr(ds, "WindowWidth", None)

    def _first(v):
        # pydicom puede traer MultiValue
        try:
            return float(v[0])
        except Exception:
            return float(v)

    if wc is not None and ww is not None:
        wc = _first(wc)
        ww = _first(ww)
        lo = wc - ww / 2.0
        hi = wc + ww / 2.0
    else:
        # fallback robusto
        lo, hi = np.percentile(arr, (1, 99))
        if lo == hi:
            lo, hi = float(np.min(arr)), float(np.max(arr))
            if lo == hi:
                lo, hi = lo - 1.0, hi + 1.0

    arr = np.clip(arr, lo, hi)
    arr = (arr - lo) / (hi - lo) * 255.0
    img8 = arr.astype(np.uint8)

    # MONOCHROME1 se ve invertido
    if str(getattr(ds, "PhotometricInterpretation", "")).upper() == "MONOCHROME1":
        img8 = 255 - img8

    return Image.fromarray(img8, mode="L")

def parse_rut_any(s: str) -> Optional[Tuple[str, str]]:
    if not s:
        return None
    t = str(s).strip().replace(" ", "").replace(".", "")

    m = re.search(r"(\d{7,8})-([0-9Kk])", t)
    if m:
        return m.group(1), m.group(2).upper()

    m = re.search(r"(\d{7,8})([0-9Kk])$", t)
    if m:
        return m.group(1), m.group(2).upper()

    return None

def format_rut_with_dots(digits: str, dv: str) -> str:
    rev = digits[::-1]
    groups = [rev[i:i+3] for i in range(0, len(rev), 3)]
    dotted = ".".join(g[::-1] for g in groups[::-1])
    return f"{dotted}-{dv.upper()}"

def normalize_rut_display(s: str) -> str:
    parsed = parse_rut_any(s)
    if not parsed:
        return (s or "").strip()
    digits, dv = parsed
    return format_rut_with_dots(digits, dv)

def yyyymmdd_to_iso(d: str) -> str:
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"

def pick_date_and_tag_from_ds(ds) -> Tuple[Optional[str], Optional[str]]:
    """
    Devuelve (tag_usado, fecha_iso) usando DATE_TAG_ORDER.
    Soporta AcquisitionDateTime (DT): toma los primeros 8 dígitos YYYYMMDD.
    """
    for key in DATE_TAG_ORDER:
        val = getattr(ds, key, None)
        if not val:
            continue
        s = str(val).strip()
        m = re.match(r"(\d{8})", s)
        if m:
            return key, yyyymmdd_to_iso(m.group(1))
    return None, None

def validate_iso_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except Exception:
        return False

def has_non_ascii(s: str) -> bool:
    return any(ord(ch) > 127 for ch in (s or ""))

def sanitize_folder_name(s: str) -> str:
    # permite . _ - para soportar 11.111.111-4 y yyyy-mm-dd
    s = (s or "").strip().replace(" ", "_")
    s = re.sub(r"[^A-Za-z0-9\.\_\-]", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")

def make_nonconflicting_name(filename: str, used_names: Set[str]) -> str:
    if filename not in used_names:
        return filename
    base, ext = os.path.splitext(filename)
    i = 2
    while True:
        cand = f"{base}_{i}{ext}"
        if cand not in used_names:
            return cand
        i += 1


class DicomImageViewer(tk.Toplevel):
    def __init__(self, master: "DicomPatientFolderEditor"):
        super().__init__(master)
        self.master_app = master
        self.configure(bg=master.C_BG)
        self.title("Visor DICOM")
        self.geometry("1100x800")
        self.img_original = None
        self.photo = None
        self.zoom = 1.0
        self.fit_mode = True
        self.base_fit_zoom = 1.0  # zoom base cuando está “ajustada a la vista”
        self._is_fullscreen = False
        self._canvas_img_id = None
        self.series_paths = []
        self.series_index = 0

        # Barra superior (solo información de la imagen)
        top = ttk.Frame(self, padding=10, style="Panel.TFrame")
        top.pack(fill="x")

        self.var_info = tk.StringVar(value="(sin imagen)")
        ttk.Label(top, textvariable=self.var_info, style="Muted.TLabel", background=master.C_PANEL).pack(side="left")

        # Zona central: canvas + scrollbars + barra de controles a la derecha
        wrap = ttk.Frame(self, padding=10, style="Panel.TFrame")
        wrap.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(wrap, bg="#ffffff", highlightthickness=1, highlightbackground=master.C_BORDER)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        vbar = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar = ttk.Scrollbar(wrap, orient="horizontal", command=self.canvas.xview)
        hbar.grid(row=1, column=0, sticky="ew")

        # Barra vertical de controles a la derecha
        controls = ttk.Frame(wrap, padding=(8, 4), style="Panel.TFrame")
        controls.grid(row=0, column=2, rowspan=2, sticky="ns", padx=(10, 0))

        ttk.Label(controls, text="Vista DICOM", style="Muted.TLabel").pack(pady=(0, 6), anchor="w")

        # Botones de navegación
        ttk.Button(
            controls, text="◀ Anterior",
            style="Ghost.TButton",
            command=lambda: self.next_image(-1)
        ).pack(fill="x", pady=(0, 4))

        ttk.Button(
            controls, text="Siguiente ▶",
            style="Ghost.TButton",
            command=lambda: self.next_image(1)
        ).pack(fill="x", pady=(0, 10))

        # Botones de zoom / ajuste
        ttk.Button(
            controls, text="Ajustar",
            style="Ghost.TButton",
            command=self.fit_to_view
        ).pack(fill="x", pady=(0, 4))

        ttk.Button(
            controls, text="100%",
            style="Ghost.TButton",
            command=lambda: self.set_zoom(1.0)
        ).pack(fill="x", pady=(0, 4))

        ttk.Button(
            controls, text="Zoom +",
            style="Ghost.TButton",
            command=lambda: self.zoom_step(1.15)
        ).pack(fill="x", pady=(0, 4))

        ttk.Button(
            controls, text="Zoom -",
            style="Ghost.TButton",
            command=lambda: self.zoom_step(1/1.15)
        ).pack(fill="x", pady=(0, 10))

        # Botón para “Expandir”: agranda visiblemente la IMAGEN dentro de la Vista DICOM
        ttk.Button(
            controls, text="🔍 Expandir imagen (F11)",
            style="Ghost.TButton",
            command=self._expand_image_once
        ).pack(fill="x")

        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        # Eventos: redimensionar / zoom / pan
        self.canvas.bind("<Configure>", self._on_resize)

        # Rueda = navegar
        self.canvas.bind("<MouseWheel>", self._on_wheel_next_prev)
        self.canvas.bind("<Button-4>", self._on_wheel_linux_next_prev)
        self.canvas.bind("<Button-5>", self._on_wheel_linux_next_prev)


        self.canvas.bind("<ButtonPress-1>", self._start_pan)
        self.canvas.bind("<B1-Motion>", self._do_pan)

        # F11 agranda solo la IMAGEN (no cambia la ventana)
        self.bind("<F11>", lambda e: self._expand_image_once())

        # Cerrar: no destruir la app, solo esta ventana
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

    def show_image(self, pil_img: Image.Image, title: str = ""):
        self.img_original = pil_img
        if title:
            self.title(f"Visor DICOM — {title}")
            self.var_info.set(title)
        # Si estamos en modo "ajustar", se recalcula el zoom; si el usuario
        # ya hizo zoom manual (fit_mode=False), se mantiene el nivel de zoom.
        if self.fit_mode:
            self.after_idle(self.fit_to_view)
        else:
            self.after_idle(self._render)

    def _render(self):
        if self.img_original is None:
            return
        z = max(0.05, min(self.zoom, 20.0))
        new_w = max(1, int(self.img_original.width * z))
        new_h = max(1, int(self.img_original.height * z))

        resized = self.img_original.resize((new_w, new_h), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(resized)

        # Centramos la imagen en el canvas
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        cx = cw // 2
        cy = ch // 2

        if self._canvas_img_id is None:
            self.canvas.delete("all")
            self._canvas_img_id = self.canvas.create_image(cx, cy, anchor="center", image=self.photo)
        else:
            self.canvas.itemconfig(self._canvas_img_id, image=self.photo)
            self.canvas.coords(self._canvas_img_id, cx, cy)

        # Scrollregion cubre al menos la imagen, permitiendo desplazamiento cuando es grande
        scroll_w = max(cw, new_w)
        scroll_h = max(ch, new_h)
        self.canvas.configure(scrollregion=(0, 0, scroll_w, scroll_h))

    def set_zoom(self, value: float):
        self.fit_mode = False
        self.zoom = max(0.05, min(float(value), 20.0))
        self._render()

    def zoom_step(self, factor: float):
        if self.img_original is None:
            return
        self.fit_mode = False
        self.set_zoom(self.zoom * float(factor))

    def fit_to_view(self):
        if self.img_original is None:
            return
        self.fit_mode = True
        cw = max(1, self.canvas.winfo_width() - 10)
        ch = max(1, self.canvas.winfo_height() - 10)
        z = min(cw / self.img_original.width, ch / self.img_original.height)
        self.base_fit_zoom = max(0.05, min(z, 20.0))
        self.zoom = self.base_fit_zoom
        self._render()

    def _on_resize(self, _evt=None):
        if self.fit_mode:
            self.fit_to_view()

    def _on_zoom_wheel(self, event):
        if self.img_original is None:
            return
        if getattr(event, "delta", 0) > 0:
            self.zoom_step(1.12)
        else:
            self.zoom_step(1/1.12)

    def _on_zoom_wheel_linux(self, event):
        if self.img_original is None:
            return
        if event.num == 4:
            self.zoom_step(1.12)
        elif event.num == 5:
            self.zoom_step(1/1.12)

    def _start_pan(self, event):
        self.canvas.scan_mark(event.x, event.y)

    def _do_pan(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _expand_image_once(self):
        """
        Expande claramente la imagen respecto al tamaño “ajustar a vista”.
        No toca el tamaño de la ventana ni crea otra interfaz.
        """
        if self.img_original is None:
            return
        # Recalcula el tamaño “ajustado” actual
        self.fit_to_view()
        base = self.base_fit_zoom
        # Aumentamos la imagen al doble del tamaño ajustado
        new_zoom = max(0.05, min(base * 2.0, 20.0))
        self.fit_mode = False
        self.set_zoom(new_zoom)


    def set_series(self, paths: list[str], start_index: int = 0):
        self.series_paths = list(paths or [])
        self.series_index = max(0, min(int(start_index), len(self.series_paths) - 1)) if self.series_paths else 0
        # Al cargar una nueva serie, por defecto ajustamos a la vista
        self.fit_mode = True
        self._load_current()

    def _load_current(self):
        if not self.series_paths:
            self.img_original = None
            self.var_info.set("(sin imagen)")
            self.canvas.delete("all")
            self._canvas_img_id = None
            return

        path = self.series_paths[self.series_index]
        try:
            ds = pydicom.dcmread(path, force=True)
            img = dicom_to_pil(ds)
        except Exception as e:
            self.var_info.set(f"Error: {os.path.basename(path)} — {e}")
            return

        title = f"{os.path.basename(path)}  ({self.series_index+1}/{len(self.series_paths)})"
        self.show_image(img, title=title)

    def next_image(self, step: int):
        if not self.series_paths:
            return
        self.series_index = max(0, min(self.series_index + step, len(self.series_paths) - 1))
        self._load_current()

    def _on_wheel_next_prev(self, event):
        # delta > 0 arriba = anterior
        step = -1 if event.delta > 0 else 1
        self.next_image(step)

    def _on_wheel_linux_next_prev(self, event):
        if event.num == 4:
            self.next_image(-1)
        elif event.num == 5:
            self.next_image(1)


# -------------------------
# App
# -------------------------

class DicomPatientFolderEditor(tk.Tk):
    def __init__(self):
        super().__init__()

        self._fit_mode = True
        self.viewer = None

        apply_medical_theme(self)

        # Mapeo a las variables de ui_theme para compatibilidad con el resto del código P3
        self.C_BG = C_BG
        self.C_PANEL = C_CARD
        self.C_CARD = C_CARD_INNER
        self.C_TEXT = C_TEXT
        self.C_MUTED = C_MUTED
        self.C_ACCENT = C_ACTION_BLUE
        self.C_OK = C_ACTION_GREEN
        self.C_WARN = "#d97706"
        self.C_DANGER = C_DANGER
        self.C_BORDER = C_BORDER

        # --- Checkbox icons (persistentes) ---
        self._img_cb_off = self._make_checkbox_icon(checked=False)
        self._img_cb_on  = self._make_checkbox_icon(checked=True)

        # Si no existe aún
        self.selected_series_uids = set()

        self.title("Editor de nombres y visualizador DICOM")
        self.geometry("1040x720")
        self.minsize(980, 680)
        self.configure(bg=self.C_BG)

        self.folder: Optional[str] = None
        self.dcm_files: List[str] = []

        self.date_counter: Counter = Counter()
        self.tag_counter: Counter = Counter()
        self.name_set: Set[str] = set()
        self.pid_set: Set[str] = set()

        # RUT detectado (solo para nombre de carpeta)
        self.rut_detected: str = ""

        # Vars GUI
        self.var_folder = tk.StringVar(value="(sin seleccionar)")
        self.var_count = tk.StringVar(value="0")
        self.var_rut_display = tk.StringVar(value="(se detectará automáticamente)")
        self.var_date = tk.StringVar(value="")
        self.var_name_orig = tk.StringVar(value="")
        self.var_name_new = tk.StringVar(value="")
        self.var_base_out = tk.StringVar(value="")
        self.var_out_preview = tk.StringVar(value="(elige carpeta destino)")
        self.var_status = tk.StringVar(value="")
        
        # --- Viewer: paciente + series (filtros) ---
        self.var_patient_banner = tk.StringVar(value="(sin paciente)")
        self.series_map = {}          # uid -> [paths ordenados]
        self.series_meta = {}         # uid -> dict meta (mod, num, pos, desc, n)
        self._series_iid_to_uid = {}  # iid treeview -> uid

        # --- Series (filtros) ---
        self.series_map = {}          # series_uid -> [paths ordenados]
        self.series_meta = {}         # series_uid -> dict meta
        self._series_iid_to_uid = {}  # iid treeview -> series_uid
        self.selected_series_uids = set()

        # para el visor
        self.current_series_files = []
        self.slice_index = 0

        # texto para mostrar selección (opcional, en Save o View)
        self.var_series_selected = tk.StringVar(value="Series seleccionadas: (todas)")

        self.current_series_uid = None
        self.current_series_files = []
        self.current_slice_idx = 0

        # --- Selección de series para GUARDAR (checkboxes) ---
        self.selected_series_uids: Set[str] = set()
        self._series_iid_to_uid = {}

        # icons checkbox (se crean en _init_checkbox_icons)
        self._img_cb_on = None
        self._img_cb_off = None

        self._apply_theme()
        self._init_checkbox_icons()
        self._build_ui()
        self._wire_traces()
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        # Ajustar ventana a un tamaño cómodo (no pantalla completa)
        self.after(50, self._maximize_window)

    def _is_dicom_file(self, path: str) -> bool:
        # Evita cosas típicas que no quieres tratar como “imagen”
        base = os.path.basename(path)
        if base.upper() == "DICOMDIR":
            return False

        # Atajo por extensión (cubre .dcm y .DCM)
        if base.lower().endswith(".dcm"):
            return True

        # Archivos muy chicos no pueden ser DICOM razonable
        try:
            if os.path.getsize(path) < 132:
                return False
        except Exception:
            return False

        # Chequeo rápido (preamble + "DICM" en offset 128)
        try:
            if is_dicom(path):
                return True
        except Exception:
            pass

        # Fallback: algunos DICOM válidos no tienen preámbulo -> intentar leer header sin pixeles
        try:
            ds = pydicom.dcmread(path, force=True, stop_before_pixels=True)
            # Si se pudo leer algo con pinta DICOM, lo aceptamos
            return ("SOPClassUID" in ds) or ("PatientID" in ds) or ("StudyInstanceUID" in ds)
        except Exception:
            return False

    def _maximize_window(self):
        """
        Ajusta la ventana principal a un tamaño amplio pero NO pantalla completa.
        Se centra y ocupa aprox. el 80–85% de la pantalla.
        """
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()

        # Ocupa un porcentaje de la pantalla, con límites razonables
        w = min(1400, int(sw * 0.85))
        h = min(900, int(sh * 0.85))

        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)

        self.geometry(f"{w}x{h}+{x}+{y}")

    def _on_tab_changed(self, _evt=None):
        # Si el usuario entra al tab de vista, reajusta con el tamaño real del canvas
        try:
            current_tab = self.nb.select()
            if current_tab == str(self.tab_view) and self.preview_pil_original is not None:
                self.after_idle(self._fit_to_view)
        except Exception:
            pass

    def _make_checkbox_icon(self, checked: bool) -> ImageTk.PhotoImage:
        size = 16
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        # Caja
        d.rounded_rectangle((1, 1, size-2, size-2), radius=3,
                            outline=self.C_MUTED, width=2, fill=(255, 255, 255, 255))

        # Check
        if checked:
            d.line((4, 9, 7, 12), fill=self.C_ACCENT, width=2)
            d.line((7, 12, 12, 5), fill=self.C_ACCENT, width=2)

        return ImageTk.PhotoImage(img)

    def _init_checkbox_icons(self):
        from PIL import Image, ImageDraw
        size = 14

        def make_icon(checked: bool):
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)

            # caja
            d.rounded_rectangle((1, 1, size-2, size-2), radius=3, outline="#9ca3af", width=2, fill="#ffffff")

            if checked:
                # check
                d.line((3, 7, 6, 10), fill="#16a34a", width=2)
                d.line((6, 10, 11, 4), fill="#16a34a", width=2)

            return ImageTk.PhotoImage(img)

        self._img_cb_on = make_icon(True)
        self._img_cb_off = make_icon(False)

    # ---------- THEME ----------
    def _apply_theme(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TFrame", background=self.C_BG)
        style.configure("Panel.TFrame", background=self.C_PANEL)
        style.configure("Card.TFrame", background=self.C_CARD)

        style.configure("TLabel", background=self.C_BG, foreground=self.C_TEXT)
        style.configure("Muted.TLabel", background=self.C_BG, foreground=self.C_MUTED)

        style.configure("Header.TLabel", background=self.C_PANEL, foreground=self.C_TEXT, font=("Segoe UI", 16, "bold"))
        style.configure("Subheader.TLabel", background=self.C_PANEL, foreground=self.C_MUTED, font=("Segoe UI", 10))

        style.configure("TNotebook", background=self.C_BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8), background=self.C_BG, foreground=self.C_TEXT)
        style.map("TNotebook.Tab",
                  background=[("selected", self.C_PANEL)],
                  foreground=[("selected", self.C_TEXT)])

        style.configure("TEntry", padding=6)
        style.configure("TCombobox", padding=6)

        style.configure("Accent.TButton", padding=10, background=self.C_ACCENT, foreground="white")
        style.map("Accent.TButton", background=[("active", "#1d4ed8")])

        style.configure("Ok.TButton", padding=10, background=self.C_OK, foreground="white")
        style.map("Ok.TButton", background=[("active", "#15803d")])

        style.configure("Ghost.TButton", padding=10, background="#eef2ff", foreground=self.C_TEXT)
        style.map("Ghost.TButton", background=[("active", "#e0e7ff")])

        style.configure("TProgressbar", troughcolor="#eef2ff")

    def _open_viewer(self):
        if not self.dcm_files:
            messagebox.showinfo("Sin archivos", "Carga una carpeta primero.")
            return

        sel = self.lst_files.curselection() if hasattr(self, "lst_files") else ()
        idx = sel[0] if sel else 0

        if self.viewer is None or not self.viewer.winfo_exists():
            self.viewer = DicomImageViewer(self)

        self.viewer.deiconify()
        self.viewer.lift()
        self.viewer.focus_force()

        # pasar lista + índice actual
        self.viewer.set_series(self.dcm_files, idx)

    def _build_tab_view(self):
        card = self._build_card(
            self.tab_view,
            "Vista previa DICOM",
            "Rueda: imagen anterior/siguiente • Zoom: solo con botones • Arrastrar: mover",
            fill="both",
            expand=True
        )


        layout = ttk.Frame(card, style="Card.TFrame")
        layout.pack(fill="both", expand=True, pady=(12, 0))

# ---------------- LEFT: series (filtros con checkbox) ----------------
        left = ttk.Frame(layout, style="Card.TFrame")
        left.pack(side="left", fill="y", padx=(0, 12))

        ttk.Label(left, text="Series (selecciona para guardar):", background=self.C_CARD, foreground=self.C_MUTED).pack(anchor="w")

        self.series_tree = ttk.Treeview(left, columns=("n",), show="tree headings", height=18)
        self.series_tree.heading("#0", text="Serie")
        self.series_tree.heading("n", text="Imgs")
        self.series_tree.column("n", width=60, anchor="e")
        self.series_tree.pack(fill="y", expand=False, pady=(6, 0))

        # Click = marcar/desmarcar checkbox
        self.series_tree.bind("<Button-1>", self._on_series_tree_click)

        # Select (highlight) = solo para ver esa serie en el visor/lista
        self.series_tree.bind("<<TreeviewSelect>>", self._on_series_select)

        btns_left = ttk.Frame(left, style="Card.TFrame")
        btns_left.pack(fill="x", pady=(10, 0))

        left.configure(width=360)
        left.pack_propagate(False)

        self.series_tree.column("#0", width=290, stretch=False)  # texto serie
        self.series_tree.column("n", width=50, stretch=False)    # imgs

        ttk.Button(btns_left, text="Marcar todo", style="Ghost.TButton", command=self._series_check_all).pack(side="left")
        ttk.Button(btns_left, text="Desmarcar todo", style="Ghost.TButton", command=self._series_uncheck_all).pack(side="left", padx=(8, 0))

        # ---------------- RIGHT: header + controles + canvas ----------------
        right = ttk.Frame(layout, style="Card.TFrame")
        right.pack(side="left", fill="both", expand=True)

        topbar = ttk.Frame(right, style="Card.TFrame")
        topbar.pack(fill="x")

        # IMPORTANT: crear vars ANTES de usar
        if not hasattr(self, "var_img_info"):
            self.var_img_info = tk.StringVar(value="(sin selección)")

        ttk.Label(
            topbar, textvariable=self.var_patient_banner,
            background=self.C_CARD, foreground=self.C_TEXT
        ).pack(side="top", anchor="w")

        ttk.Label(
            topbar, textvariable=self.var_img_info,
            background=self.C_CARD, foreground=self.C_MUTED, wraplength=720
        ).pack(side="top", anchor="w", pady=(4, 0))

        controls = ttk.Frame(right, style="Card.TFrame")
        controls.pack(fill="x", pady=(8, 0))

        ttk.Button(controls, text="Pantalla completa (F11)", style="Ghost.TButton", command=self._toggle_fullscreen)\
            .pack(side="left")
        ttk.Button(controls, text="Ajustar", style="Ghost.TButton", command=self._fit_to_view)\
            .pack(side="left", padx=(10, 0))
        ttk.Button(controls, text="100%", style="Ghost.TButton", command=lambda: self._set_zoom(1.0))\
            .pack(side="left", padx=(10, 0))
        ttk.Button(controls, text="Zoom +", style="Ghost.TButton", command=lambda: self._zoom_step(1.15))\
            .pack(side="left", padx=(10, 0))
        ttk.Button(controls, text="Zoom -", style="Ghost.TButton", command=lambda: self._zoom_step(1/1.15))\
            .pack(side="left", padx=(10, 0))

        ttk.Button(controls, text="◀ Prev", style="Ghost.TButton", command=lambda: self._step_slice(-1))\
            .pack(side="left", padx=(10, 0))
        ttk.Button(controls, text="Next ▶", style="Ghost.TButton", command=lambda: self._step_slice(+1))\
            .pack(side="left", padx=(10, 0))

        # Canvas con scrollbars
        canvas_wrap = ttk.Frame(right, style="Card.TFrame")
        canvas_wrap.pack(fill="both", expand=True, pady=(8, 0))

        self.canvas = tk.Canvas(
            canvas_wrap, bg="#ffffff", highlightthickness=1, highlightbackground=self.C_BORDER
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        vbar = ttk.Scrollbar(canvas_wrap, orient="vertical", command=self.canvas.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar = ttk.Scrollbar(canvas_wrap, orient="horizontal", command=self.canvas.xview)
        hbar.grid(row=1, column=0, sticky="ew")

        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        canvas_wrap.rowconfigure(0, weight=1)
        canvas_wrap.columnconfigure(0, weight=1)

        # Estado de preview/zoom
        self.preview_photo = None
        self.preview_path = None
        self.preview_pil_original = None
        self.zoom = 1.0
        self._is_fullscreen = False
        self._prev_geometry = None
        self._canvas_img_id = None

        # Bindings
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        # Rueda = siguiente/anterior imagen (slice)
        self.canvas.bind("<MouseWheel>", self._on_wheel_slice)
        self.canvas.bind("<Button-4>", self._on_wheel_slice_linux)
        self.canvas.bind("<Button-5>", self._on_wheel_slice_linux)

        # Pan (arrastrar)
        self.canvas.bind("<ButtonPress-1>", self._start_pan)
        self.canvas.bind("<B1-Motion>", self._do_pan)

        # Atajos
        self.bind("<F11>", lambda e: self._toggle_fullscreen())
        self.bind("<Escape>", lambda e: self._exit_fullscreen())

    def _on_series_tree_click(self, event):
        iid = self.series_tree.identify_row(event.y)
        if not iid:
            return

        col = self.series_tree.identify_column(event.x)
        region = self.series_tree.identify_region(event.x, event.y)

        if col == "#0" and region in ("tree", "cell"):
            uid = self._series_iid_to_uid.get(iid)
            if not uid:
                return "break"

            if uid in self.selected_series_uids:
                self.selected_series_uids.remove(uid)
            else:
                self.selected_series_uids.add(uid)

            self.series_tree.item(
                iid,
                image=self._img_cb_on if uid in self.selected_series_uids else self._img_cb_off
            )
            return "break"  # <- importante

    def _series_check_all(self):
        self.selected_series_uids = set(self.series_map.keys())
        for iid, uid in self._series_iid_to_uid.items():
            self.series_tree.item(iid, image=self._img_cb_on)

    def _series_uncheck_all(self):
        self.selected_series_uids.clear()
        for iid, uid in self._series_iid_to_uid.items():
            self.series_tree.item(iid, image=self._img_cb_off)

    def _on_series_select(self, _evt=None):
        # series seleccionadas (para guardar)
        sel_iids = list(self.series_tree.selection())
        self.selected_series_uids = set(self._series_iid_to_uid[iid] for iid in sel_iids if iid in self._series_iid_to_uid)

        if self.selected_series_uids:
            self.var_series_selected.set(f"Series seleccionadas: {len(self.selected_series_uids)}")
        else:
            self.var_series_selected.set("Series seleccionadas: (todas)")

        # para el visor: usa la primera serie seleccionada como “activa”
        if not sel_iids:
            return

        first_uid = self._series_iid_to_uid.get(sel_iids[0])
        if not first_uid:
            return

        self.current_series_files = self.series_map.get(first_uid, [])
        self.slice_index = 0

        if self.current_series_files:
            self._show_dicom_preview(self.current_series_files[self.slice_index])

    def _populate_series_tree(self):
        if not hasattr(self, "series_tree"):
            return

        self.series_tree.delete(*self.series_tree.get_children())
        self._series_iid_to_uid = {}

        series_uids = list(self.series_map.keys())

        def _sort_key(uid):
            sn = (self.series_meta.get(uid, {}).get("SeriesNumber") or "").strip()
            try:
                return (0, int(sn))
            except Exception:
                return (1, sn, uid)

        series_uids.sort(key=_sort_key)

        for idx, uid in enumerate(series_uids, start=1):
            meta = self.series_meta.get(uid, {})
            n = len(self.series_map.get(uid, []))
            mod = meta.get("Modality") or ""
            desc = meta.get("SeriesDescription") or meta.get("ProtocolName") or ""
            folder = self._series_folder_name(meta, idx)

            text = f"{folder} • {mod} • {desc}".strip(" •")
            iid = f"S{idx}"
            self._series_iid_to_uid[iid] = uid

            checked = uid in getattr(self, "selected_series_uids", set())
            icon = self._img_cb_on if checked else self._img_cb_off

            self.series_tree.insert("", "end", iid=iid, text=text, image=icon, values=(n,))

    def _populate_preview_list(self):
        if not hasattr(self, "lst_files"):
            return
        self.lst_files.delete(0, "end")

        if not self.dcm_files:
            self.var_img_info.set("(sin archivos)")
            self.preview_photo = None
            self.preview_path = None
            self.preview_pil_original = None
            if hasattr(self, "canvas"):
                self.canvas.delete("all")
                self._canvas_img_id = None
                self.canvas.configure(scrollregion=(0, 0, 1, 1))
            return

        for p in self.dcm_files:
            rel = os.path.relpath(p, self.folder) if self.folder else os.path.basename(p)
            self.lst_files.insert("end", rel)

        self.lst_files.selection_set(0)
        self.lst_files.event_generate("<<ListboxSelect>>")

    def _on_preview_select(self, _evt=None):
        if not self.dcm_files:
            return
        sel = self.lst_files.curselection()
        if not sel:
            return
        idx = sel[0]
        path = self.dcm_files[idx]
        self._show_dicom_preview(path)

    def _show_dicom_preview(self, path: str, slice_idx: int = None, slice_total: int = None):
        try:
            ds = pydicom.dcmread(path, force=True)
            img = dicom_to_pil(ds)

            self.preview_pil_original = img
            self.preview_path = path

            rows = getattr(ds, "Rows", "?")
            cols = getattr(ds, "Columns", "?")
            pi = getattr(ds, "PhotometricInterpretation", "")

            extra = ""
            if slice_idx is not None and slice_total is not None and slice_total > 0:
                extra = f" • {slice_idx+1}/{slice_total}"

            self.var_img_info.set(f"{os.path.basename(path)}{extra} • {rows}x{cols} • {pi}")

            self.after_idle(self._fit_to_view)

        except Exception as e:
            self.preview_pil_original = None
            self.preview_path = None
            self.preview_photo = None
            self.var_img_info.set(f"No se pudo mostrar este DICOM: {e}")
            if hasattr(self, "canvas"):
                self.canvas.delete("all")
                self._canvas_img_id = None

# ---------- UI ----------

    def _build_ui(self):
        topbar = tk.Frame(self, bg=C_CARD, padx=24, pady=13)
        topbar.pack(fill="x")
        tk.Label(
            topbar, text="Centro de Comando Clínico", bg=C_CARD, fg=C_TEXT,
            font=("TkDefaultFont", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            topbar, text="●  EJECUCIÓN LOCAL", bg=C_CARD_INNER,
            fg=C_ACTION_BLUE, font=("TkDefaultFont", 9, "bold"), padx=14, pady=6,
        ).pack(side="right")

        # Header
        header = ttk.Frame(self, padding=(18, 16), style="Panel.TFrame")
        header.pack(fill="x", padx=14, pady=(14, 10))

        ttk.Label(header, text="Editor de nombres y visualizador DICOM", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Editor del Nombre del Paciente y visualizador DICOM básico",
            style="Subheader.TLabel"
        ).pack(anchor="w", pady=(6, 0))

        # Notebook tabs
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        self.tab_load = ttk.Frame(self.nb, style="TFrame")
        self.tab_edit = ttk.Frame(self.nb, style="TFrame")
        self.tab_save = ttk.Frame(self.nb, style="TFrame")
        self.tab_details = ttk.Frame(self.nb, style="TFrame")
        self.tab_view = ttk.Frame(self.nb, style="TFrame")


        self.nb.add(self.tab_load, text=" Cargar ")
        self.nb.add(self.tab_edit, text=" Editar ")
        self.nb.add(self.tab_view, text=" Vista DICOM ")
        self.nb.add(self.tab_details, text=" Detalles ")
        self.nb.add(self.tab_save, text=" Guardar como ")

        self._build_tab_load()
        self._build_tab_edit()
        self._build_tab_details()
        self._build_tab_view()
        self._build_tab_save()

        # Status bar
        status = ttk.Frame(self, padding=(14, 10), style="Panel.TFrame")
        status.pack(fill="x", padx=14, pady=(0, 14))

        self.pbar = ttk.Progressbar(status, mode="determinate")
        self.pbar.pack(side="left", fill="x", expand=True, padx=(0, 12))

        ttk.Label(status, textvariable=self.var_status, style="Muted.TLabel", background=self.C_PANEL).pack(side="left")

    def _build_card(self, parent, title: str, subtitle: str = "", *, fill="x", expand=False):
        card = ttk.Frame(parent, padding=16, style="Card.TFrame")
        card.pack(fill=fill, expand=expand, padx=14, pady=(12, 0))

        ttk.Label(card, text=title, font=("Segoe UI", 12, "bold"),
                background=self.C_CARD, foreground=self.C_TEXT).pack(anchor="w")
        if subtitle:
            ttk.Label(card, text=subtitle, background=self.C_CARD,
                    foreground=self.C_MUTED).pack(anchor="w", pady=(4, 0))
        return card


    def _build_tab_load(self):
        card = self._build_card(
            self.tab_load,
            "Cargar carpeta del paciente",
            "Selecciona una carpeta que contenga archivos .dcm (puede tener subcarpetas)."
        )

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x", pady=(12, 0))

        ttk.Button(row, text="Seleccionar carpeta…", style="Accent.TButton", command=self.select_folder).pack(side="left")
        ttk.Button(row, text="Limpiar", style="Ghost.TButton", command=self._clear_fields).pack(side="left", padx=(10, 0))

        info = ttk.Frame(card, style="Card.TFrame")
        info.pack(fill="x", pady=(14, 0))

        ttk.Label(info, text="Carpeta:", background=self.C_CARD, foreground=self.C_MUTED).grid(row=0, column=0, sticky="w")
        ttk.Label(info, textvariable=self.var_folder, background=self.C_CARD, foreground=self.C_TEXT, wraplength=760).grid(row=0, column=1, sticky="w", padx=(10, 0))

        ttk.Label(info, text="Archivos .dcm:", background=self.C_CARD, foreground=self.C_MUTED).grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Label(info, textvariable=self.var_count, background=self.C_CARD, foreground=self.C_TEXT).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(10, 0))

    def _build_tab_edit(self):
        card = self._build_card(
            self.tab_edit,
            "Editar Nombre del Paciente"
        )

        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="x", pady=(12, 0))

        ttk.Label(grid, text="Nombre detectado:", background=self.C_CARD, foreground=self.C_MUTED)\
            .grid(row=0, column=0, sticky="w")
        ttk.Label(grid, textvariable=self.var_name_orig, background=self.C_CARD, foreground=self.C_TEXT)\
            .grid(row=0, column=1, sticky="w", padx=(10, 0))

        ttk.Label(grid, text="Editar Nombre:", background=self.C_CARD, foreground=self.C_MUTED)\
            .grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.ent_name_new = ttk.Entry(grid, textvariable=self.var_name_new, width=70, state="disabled")
        self.ent_name_new.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(12, 0))

        # ttk.Label(
        #     card,
        #     text="Nota: Si el nombre contiene Ñ/ñ, se ajusta el charset a UTF-8 (ISO_IR 192) para guardarlo correctamente.",
        #     background=self.C_CARD,
        #     foreground=self.C_MUTED,
        #     wraplength=900
        # ).pack(anchor="w", pady=(12, 0))

    def _build_tab_save(self):
        card = self._build_card(
            self.tab_save,
            "Guardar",
            "El RUT se detecta automáticamente y solo se usa para el nombre de carpeta."
        )

        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="x", pady=(12, 0))

        ttk.Label(grid, text="  El Archivo se guarda con el formato RUT_FECHA:", background=self.C_CARD, foreground=self.C_MUTED)\
            .grid(row=0, column=0, sticky="w", columnspan=2)

        ttk.Label(grid, text="  - RUT :", background=self.C_CARD, foreground=self.C_MUTED).grid(row=1, column=0, sticky="w")
        ttk.Label(grid, textvariable=self.var_rut_display, background=self.C_CARD, foreground=self.C_TEXT).grid(row=1, column=1, sticky="w", padx=(10, 0))

        ttk.Label(grid, text="  - Fecha :", background=self.C_CARD, foreground=self.C_MUTED).grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.cmb_date = ttk.Combobox(grid, textvariable=self.var_date, width=30, state="disabled")
        self.cmb_date.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(12, 0))

        out = ttk.Frame(card, style="Card.TFrame")
        out.pack(fill="x", pady=(12, 0))

        row = ttk.Frame(out, style="Card.TFrame")
        row.pack(fill="x")

        #ttk.Button(row, text="Elegir carpeta destino…", style="Accent.TButton", command=self._choose_base_out).pack(side="left")
        ttk.Label(row, textvariable=self.var_base_out, background=self.C_CARD, foreground=self.C_TEXT, wraplength=680).pack(side="left", padx=(12, 0))

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.pack(fill="x", pady=(16, 0))

        ttk.Button(actions, text="Guardar cómo", style="Ok.TButton", command=self.save_as).pack(side="left")

        prev = ttk.Frame(card, style="Card.TFrame")
        prev.pack(fill="x", pady=(12, 0))
        ttk.Label(prev, text="Vista previa salida:", background=self.C_CARD, foreground=self.C_MUTED).pack(anchor="w")
        ttk.Label(prev, textvariable=self.var_out_preview, background=self.C_CARD, foreground=self.C_TEXT, wraplength=920).pack(anchor="w", pady=(6, 0))


        #ttk.Button(actions, text="Ir a Detalles", style="Ghost.TButton", command=lambda: self.nb.select(self.tab_details)).pack(side="left", padx=(10, 0))

    def _build_tab_details(self):
        card = self._build_card(
            self.tab_details,
            "Detalles y verificación",
            "Aquí ves qué tags de fecha se usaron y cuántas fechas distintas existen."
        )

        self.txt_summary = ScrolledText(card, height=18, wrap="word")
        self.txt_summary.pack(fill="both", expand=True, pady=(12, 0))

        self.txt_summary.configure(
            bg="#ffffff",
            fg=self.C_TEXT,
            insertbackground=self.C_TEXT,
            highlightthickness=1,
            highlightbackground=self.C_BORDER,
            padx=10,
            pady=10
        )

    # ---------- UX helpers ----------
    def _wire_traces(self):
        self.var_date.trace_add("write", lambda *_: self._update_previews())
        self.var_base_out.trace_add("write", lambda *_: self._update_previews())

    def _choose_base_out(self):
        folder = filedialog.askdirectory(title="Elige carpeta destino (se creará dentro RUT_Fecha)")
        if folder:
            self.var_base_out.set(folder)

    def _update_previews(self):
        base_out = (self.var_base_out.get() or "").strip()
        date_raw = (self.var_date.get() or "").strip()
        date_iso = date_raw[:10] if len(date_raw) >= 10 else ""

        if not base_out:
            self.var_out_preview.set("(elige carpeta destino)")
            return

        if not self.rut_detected or not date_iso:
            self.var_out_preview.set(os.path.join(base_out, "(RUT_Fecha)"))
            return

        out_folder_name = sanitize_folder_name(f"{self.rut_detected}_{date_iso}")
        self.var_out_preview.set(os.path.join(base_out, out_folder_name))

    # ---------- Core logic ----------
    def _clear_fields(self):
        self.folder = None
        self.dcm_files = []
        self.date_counter = Counter()
        self.tag_counter = Counter()
        self.name_set = set()
        self.pid_set = set()
        self.rut_detected = ""

        self.var_folder.set("(sin seleccionar)")
        self.var_count.set("0")
        self.var_rut_display.set("(se detectará automáticamente)")
        self.var_date.set("")
        self.var_name_orig.set("")
        self.var_name_new.set("")
        self.var_status.set("")
        self.var_base_out.set("")
        self.var_out_preview.set("(elige carpeta destino)")
        self.var_browser_path.set(os.path.expanduser("~"))
        if hasattr(self, "browser_tree"):
            self._browser_go(self.var_browser_path.get())

        self.series_map = {}
        self.series_meta = {}
        self._series_iid_to_uid = {}
        self.selected_series_uids = set()
        self.current_series_files = []
        self.slice_index = 0
        self.var_series_selected.set("Series seleccionadas: (todas)")

        self.cmb_date["values"] = []
        self.cmb_date.configure(state="disabled")
        self.ent_name_new.configure(state="disabled")

        self.txt_summary.delete("1.0", "end")
        self.pbar["value"] = 0
        self.pbar["maximum"] = 1

        if hasattr(self, "lst_files"):
            self._populate_preview_list()

    def select_folder(self):
        folder = filedialog.askdirectory(title="Selecciona la carpeta que contiene los .dcm del paciente")
        if not folder:
            return

        # Recursivo, evitando duplicados por symlinks
        seen = set()
        files = []
        for root, _, fnames in os.walk(folder, followlinks=False):
            for fn in fnames:
                p = os.path.join(root, fn)

                if not self._is_dicom_file(p):
                    continue

                rp = os.path.realpath(p)
                if rp not in seen:
                    seen.add(rp)
                    files.append(rp)

        files.sort()

        if not files:
            messagebox.showwarning("Sin DICOM", "No encontré archivos .dcm en esa carpeta.")
            self._clear_fields()
            return

        self.folder = folder
        self.dcm_files = files
        self.var_folder.set(folder)
        self.var_count.set(str(len(files)))

        self._detect_from_all_files()
        self._update_patient_banner()
        self._build_series_index()
        # ✅ Por defecto: todas las series marcadas
        self.selected_series_uids = set(self.series_map.keys())
        self._populate_series_tree()

        # opcional: auto-seleccionar primera serie para que muestre algo
        if hasattr(self, "series_tree"):
            kids = self.series_tree.get_children()
            if kids:
                self.series_tree.selection_set(kids[0])
                self.series_tree.focus(kids[0])
                self.series_tree.event_generate("<<TreeviewSelect>>")

        self.ent_name_new.configure(state="normal")
        self.cmb_date.configure(state="normal")
        #self.nb.select(self.tab_edit)
        self.nb.select(self.tab_view)
        self._update_previews()

    def _detect_from_all_files(self):
        self.date_counter = Counter()
        self.tag_counter = Counter()
        self.name_set = set()
        self.pid_set = set()
        self.rut_detected = ""

        total = len(self.dcm_files)
        self.pbar["value"] = 0
        self.pbar["maximum"] = max(1, total)
        self.var_status.set("Analizando TODOS los DICOM (sin cargar píxeles)…")
        self.update_idletasks()

        tags = ["PatientName", "PatientID", "StudyDate", "SeriesDate", "ContentDate", "AcquisitionDate", "AcquisitionDateTime"]

        for i, f in enumerate(self.dcm_files, start=1):
            try:
                ds = pydicom.dcmread(
                    f,
                    force=True,
                    stop_before_pixels=True,
                    specific_tags=tags
                )

                name = str(getattr(ds, "PatientName", "") or "").strip()
                pid = str(getattr(ds, "PatientID", "") or "").strip()

                if name:
                    self.name_set.add(name)
                if pid:
                    self.pid_set.add(pid)

                tag, date_iso = pick_date_and_tag_from_ds(ds)
                if date_iso:
                    self.date_counter[date_iso] += 1
                    if tag:
                        self.tag_counter[tag] += 1
                else:
                    self.date_counter["(sin fecha en esos tags)"] += 1

            except Exception:
                self.date_counter["(error leyendo)"] += 1

            if i % 25 == 0 or i == total:
                self.pbar["value"] = i
                self.var_status.set(f"Analizando {i}/{total}…")
                self.update_idletasks()

        self.var_status.set("")

        # RUT detectado (solo para nombre de carpeta)
        rut_guess = ""
        for pid in sorted(self.pid_set):
            rd = normalize_rut_display(pid)
            if parse_rut_any(rd):
                rut_guess = rd
                break
        if not rut_guess and self.folder:
            rd = normalize_rut_display(os.path.basename(self.folder))
            if parse_rut_any(rd):
                rut_guess = rd

        if rut_guess:
            self.rut_detected = rut_guess
            self.var_rut_display.set(rut_guess)
        else:
            self.rut_detected = ""
            self.var_rut_display.set("(no se detectó RUT válido)")

        # PatientName sugerido
        name_guess = sorted(self.name_set)[0] if self.name_set else ""
        self.var_name_orig.set(name_guess)
        self.var_name_new.set(name_guess)

        # Dropdown fechas
        date_items = [(d, c) for d, c in self.date_counter.items()
                    if d not in ("(sin fecha en esos tags)", "(error leyendo)")]
        date_items.sort(key=lambda x: x[1], reverse=True)

        values = [f"{d} (n={c})" for d, c in date_items]
        self.cmb_date["values"] = values

        if date_items:
            top_date, top_count = date_items[0]
            self.var_date.set(f"{top_date} (n={top_count})")
        else:
            self.var_date.set("")

        self._write_summary(date_items)
        self._update_previews()

    def _write_summary(self, date_items_sorted: List[Tuple[str, int]]):
        self.txt_summary.delete("1.0", "end")
        total = len(self.dcm_files)

        lines = []
        lines.append(f"Carpeta: {self.folder}")
        lines.append(f"Total .dcm analizados: {total}")
        lines.append("")
        lines.append(f"RUT detectado: {self.rut_detected or '(no válido)'}")
        lines.append("")
        lines.append("Tags de fecha usados (conteo, según prioridad):")
        if self.tag_counter:
            for k, v in self.tag_counter.most_common():
                lines.append(f"  - {k}: {v}")
        else:
            lines.append("  - (ninguno)")

        missing = self.date_counter.get("(sin fecha en esos tags)", 0)
        err = self.date_counter.get("(error leyendo)", 0)
        if missing or err:
            lines.append("")
            if missing:
                lines.append(f"Sin fecha en esos tags: {missing}")
            if err:
                lines.append(f"Errores leyendo: {err}")

        lines.append("")
        lines.append("Fechas detectadas — ordenadas por frecuencia (top 25):")
        if not date_items_sorted:
            lines.append("  - (no se detectaron fechas válidas)")
        else:
            for d, c in date_items_sorted[:25]:
                lines.append(f"  - {d}: {c}")
            if len(date_items_sorted) > 25:
                lines.append(f"  ... y {len(date_items_sorted) - 25} más")

        lines.append("")
        lines.append("Notas:")
        lines.append("- El RUT solo se usa para nombrar la carpeta de salida.")
        lines.append("- Solo se edita el Nombre del Paciente.")
        lines.append("- Modo “misma carpeta”: se exporta a TEMP, se reemplaza la carpeta original y al final se borra el respaldo (si todo salió bien).")

        self.txt_summary.insert("1.0", "\n".join(lines))

    def save_as(self):
        if not self.folder or not self.dcm_files:
            messagebox.showwarning("Falta carpeta", "Primero selecciona una carpeta con .dcm.")
            return

        date_raw = (self.var_date.get() or "").strip()
        new_name = (self.var_name_new.get() or "").strip()

        date_iso = date_raw[:10]
        if not date_iso or not validate_iso_date(date_iso):
            messagebox.showwarning("Fecha inválida", "Elige una fecha válida del dropdown (yyyy-mm-dd).")
            return

        if not new_name:
            messagebox.showwarning("Nombre vacío", "El nuevo PatientName no puede estar vacío.")
            return

        if not self.rut_detected:
            messagebox.showerror(
                "RUT no detectado",
                "No se detectó un RUT válido (ni en PatientID ni en el nombre de la carpeta).\n\n"
                "Para guardar como RUT_Fecha necesitas que PatientID contenga el RUT o que la carpeta se llame con el RUT."
            )
            return

        base_out = (self.var_base_out.get() or "").strip()
        if not base_out:
            base_out = filedialog.askdirectory(title="Elige carpeta destino (se creará dentro RUT_Fecha)")
            if not base_out:
                return
            self.var_base_out.set(base_out)

        out_folder_name = sanitize_folder_name(f"{self.rut_detected}_{date_iso}")
        out_folder = os.path.join(base_out, out_folder_name)

        # ---------------------------
        # PROTECCIÓN + MODO SOBREESCRIBIR
        # ---------------------------
        src_abs = os.path.abspath(self.folder)
        out_abs = os.path.abspath(out_folder)

        # Caso típico: base_out == carpeta cargada Y carpeta cargada ya se llama RUT_Fecha
        # => el usuario quiere sobreescribir, no crear RUT_Fecha dentro de RUT_Fecha
        src_base = os.path.normcase(os.path.basename(src_abs.rstrip(os.sep)))
        desired_base = os.path.normcase(out_folder_name)

        if src_base == desired_base:
            nested_candidate = os.path.normcase(os.path.abspath(os.path.join(src_abs, out_folder_name)))
            if os.path.normcase(out_abs) == nested_candidate:
                # reinterpretar como overwrite del mismo folder
                out_folder = src_abs
                out_abs = src_abs

        same_folder = (os.path.normcase(out_abs) == os.path.normcase(src_abs))

        try:
            inside_src = (os.path.commonpath([out_abs, src_abs]) == src_abs)
            if inside_src and (not same_folder):
                messagebox.showerror(
                    "Destino inválido",
                    "La carpeta de salida está dentro de la carpeta de entrada.\n\n"
                    "Elige un destino fuera de la carpeta original."
                )
                return
        except ValueError:
            pass

        # Índice de series (si no existe)
        if not getattr(self, "series_map", None):
            self._build_series_index()

        if not self.series_map:
            messagebox.showwarning("Sin series", "No pude agrupar por series (SeriesInstanceUID).")
            return

        # Series a guardar: seleccionadas o (si ninguna) preguntar si guardar todas
        selected = getattr(self, "selected_series_uids", set()) or set()
        if len(selected) > 0:
            series_to_save = list(selected)
        else:
            ans = messagebox.askyesno(
                "Sin selección",
                "No has seleccionado series.\n\n¿Deseas guardar TODAS las series?"
            )
            if not ans:
                return
            series_to_save = list(self.series_map.keys())

        # Orden estable: por SeriesNumber si existe
        def _series_sort_key(uid: str):
            meta = self.series_meta.get(uid, {}) if hasattr(self, "series_meta") else {}
            sn = (meta.get("SeriesNumber") or "").strip()
            try:
                return (0, int(sn))
            except Exception:
                return (1, sn, uid)

        series_to_save.sort(key=_series_sort_key)

        # ---------------------------
        # Si es overwrite sobre la MISMA carpeta: guardar a TEMP y luego swap
        # ---------------------------
        final_out_folder = out_folder
        tmp_out_folder = None

        if same_folder:
            ok = messagebox.askyesno(
                "Sobrescribir carpeta actual",
                "Estás guardando SOBRE la misma carpeta que cargaste.\n\n"
                "Se exportará a una carpeta temporal y luego se reemplazará la carpeta original.\n"
                "¿Continuar?"
            )
            if not ok:
                return

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            parent = os.path.dirname(final_out_folder.rstrip(os.sep))
            base = os.path.basename(final_out_folder.rstrip(os.sep))
            tmp_out_folder = os.path.join(parent, f".{base}__TMP__{stamp}")

            out_folder = tmp_out_folder  # <-- export real
            out_abs = os.path.abspath(out_folder)

        # Crear / recrear carpeta de salida (NOTA: si same_folder, aquí siempre es TEMP)
        if os.path.exists(out_folder):
            if not same_folder:
                if not messagebox.askyesno(
                    "Carpeta existe",
                    f"La carpeta ya existe:\n{out_folder}\n\n¿Deseas reemplazarla (borrarla y recrearla)?"
                ):
                    return
            shutil.rmtree(out_folder, ignore_errors=True)

        os.makedirs(out_folder, exist_ok=True)

        # Progreso
        total_files = sum(len(self.series_map.get(uid, [])) for uid in series_to_save)
        self.pbar["value"] = 0
        self.pbar["maximum"] = max(1, total_files)
        self.var_status.set("Guardando…")
        self.update_idletasks()

        edited = 0
        copied_as_is = 0
        missing = 0
        failed = 0
        processed = 0

        used_names_per_dir: dict[str, Set[str]] = {}

        for sidx, series_uid in enumerate(series_to_save, start=1):
            files_in_series = list(self.series_map.get(series_uid, []) or [])
            if not files_in_series:
                continue

            files_in_series.sort()

            meta = self.series_meta.get(series_uid, {}) if hasattr(self, "series_meta") else {}
            series_dirname = self._series_folder_name(meta, sidx)
            series_out_dir = os.path.join(out_folder, series_dirname)
            os.makedirs(series_out_dir, exist_ok=True)

            used_names = used_names_per_dir.setdefault(series_out_dir, set())

            for src in files_in_series:
                processed += 1

                if not os.path.exists(src):
                    missing += 1
                    self.pbar["value"] = processed
                    self.var_status.set(f"Falta archivo {processed}/{total_files}…")
                    self.update_idletasks()
                    continue

                base = os.path.basename(src)
                out_name = make_nonconflicting_name(base, used_names)
                dst = os.path.join(series_out_dir, out_name)

                try:
                    ds = pydicom.dcmread(src, force=True)
                    ds.PatientName = new_name
                    if has_non_ascii(new_name):
                        ds.SpecificCharacterSet = "ISO_IR 192"

                    try:
                        ds.save_as(dst, write_like_original=True)
                    except TypeError:
                        ds.save_as(dst)

                    used_names.add(out_name)
                    edited += 1

                except Exception:
                    try:
                        shutil.copy2(src, dst)
                        used_names.add(out_name)
                        copied_as_is += 1
                    except Exception:
                        failed += 1

                self.pbar["value"] = processed
                self.var_status.set(f"Procesando {processed}/{total_files}…")
                self.update_idletasks()

        # ---------------------------
        # SWAP final si era overwrite sobre la misma carpeta
        # ---------------------------
        if same_folder and tmp_out_folder and os.path.isdir(tmp_out_folder):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = f"{final_out_folder.rstrip(os.sep)}__BAK__{stamp}"

            try:
                os.rename(final_out_folder, backup)
                os.rename(tmp_out_folder, final_out_folder)
                shutil.rmtree(backup, ignore_errors=True)

                out_folder = final_out_folder  # para el mensaje final

                # MUY importante: refrescar lista de archivos, porque self.dcm_files apunta al backup antiguo
                try:
                    if hasattr(self, "_use_folder_as_patient"):
                        self._use_folder_as_patient(final_out_folder)
                        self._build_series_index()
                        self._populate_series_tree()
                except Exception:
                    pass

            except Exception as e:
                messagebox.showerror(
                    "Error reemplazando carpeta",
                    f"Se exportó a:\n{tmp_out_folder}\n\n"
                    f"Pero falló el reemplazo automático.\n\nDetalle:\n{e}"
                )
                return

        self.var_status.set("")
        self._update_previews()

        messagebox.showinfo(
            "Listo",
            f"Carpeta creada:\n{out_folder}\n\n"
            f"Series guardadas: {len(series_to_save)}\n"
            f"Archivos procesados: {total_files}\n"
            f"Editados (PatientName): {edited}\n"
            f"Copiados sin editar (fallo DICOM): {copied_as_is}\n"
            f"Faltantes: {missing}\n"
            f"Fallidos: {failed}"
        )

    def _render_current_zoom(self):
        """Renderiza preview_pil_original en el canvas con el zoom actual."""
        if self.preview_pil_original is None or not hasattr(self, "canvas"):
            return

        img = self.preview_pil_original
        z = max(0.05, min(self.zoom, 20.0))

        new_w = max(1, int(img.width * z))
        new_h = max(1, int(img.height * z))

        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(resized)

        if self._canvas_img_id is None:
            self.canvas.delete("all")
            self._canvas_img_id = self.canvas.create_image(0, 0, anchor="nw", image=self.preview_photo)
        else:
            self.canvas.itemconfig(self._canvas_img_id, image=self.preview_photo)

        self.canvas.configure(scrollregion=(0, 0, new_w, new_h))

    def _set_zoom(self, value: float):
        self._fit_mode = False  # si el usuario fija zoom, deja de auto-ajustar
        self.zoom = max(0.05, min(float(value), 20.0))
        self._render_current_zoom()

    def _zoom_step(self, factor: float):
        if self.preview_pil_original is None:
            return
        self._fit_mode = False
        self._set_zoom(self.zoom * float(factor))

    def _fit_to_view(self):
        self._fit_mode = True
        """Ajusta zoom para que la imagen quepa completa en el canvas."""
        if self.preview_pil_original is None or not hasattr(self, "canvas"):
            return

        # Necesitamos tamaños actuales del canvas
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())

        # margen
        cw -= 10
        ch -= 10

        img = self.preview_pil_original
        if img.width <= 0 or img.height <= 0:
            return

        z = min(cw / img.width, ch / img.height)
        z = max(0.05, min(z, 20.0))
        self.zoom = z
        self._render_current_zoom()

    def _on_canvas_resize(self, _evt=None):
        if getattr(self, "_fit_mode", False):
            self._fit_to_view()

    def _on_zoom_wheel(self, event):
        # Windows/macOS: event.delta
        if self.preview_pil_original is None:
            return
        direction = 1 if getattr(event, "delta", 0) > 0 else -1
        if direction > 0:
            self._zoom_step(1.12)
        else:
            self._zoom_step(1/1.12)

    def _on_zoom_wheel_linux(self, event):
        # Linux X11: Button-4 (arriba) y Button-5 (abajo)
        if self.preview_pil_original is None:
            return
        if event.num == 4:
            self._zoom_step(1.12)
        elif event.num == 5:
            self._zoom_step(1/1.12)

    def _start_pan(self, event):
        if hasattr(self, "canvas"):
            self.canvas.scan_mark(event.x, event.y)

    def _do_pan(self, event):
        if hasattr(self, "canvas"):
            self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _toggle_fullscreen(self):
        self._is_fullscreen = not getattr(self, "_is_fullscreen", False)
        if self._is_fullscreen:
            self._prev_geometry = self.geometry()
            self.attributes("-fullscreen", True)
        else:
            self.attributes("-fullscreen", False)
            if self._prev_geometry:
                self.geometry(self._prev_geometry)

    def _exit_fullscreen(self):        # --- Explorador integrado ---
        self.var_browser_path = tk.StringVar(value=os.path.expanduser("~"))
        self._browser_iid_to_path = {}
        self._browser_counter = 0

        if getattr(self, "_is_fullscreen", False):
            self._is_fullscreen = False
            self.attributes("-fullscreen", False)
            if self._prev_geometry:
                self.geometry(self._prev_geometry)

    def _select_preview_index(self, idx: int):
        if not hasattr(self, "lst_files"):
            return
        if not self.dcm_files:
            return
        idx = max(0, min(idx, len(self.dcm_files) - 1))
        self.lst_files.selection_clear(0, "end")
        self.lst_files.selection_set(idx)
        self.lst_files.see(idx)
        self.lst_files.event_generate("<<ListboxSelect>>")

    def _on_wheel_next_prev(self, event):
        # En muchos sistemas: delta > 0 = arriba, delta < 0 = abajo
        if not hasattr(self, "lst_files") or not self.dcm_files:
            return
        sel = self.lst_files.curselection()
        cur = sel[0] if sel else 0
        step = -1 if event.delta > 0 else 1
        self._select_preview_index(cur + step)

    def _on_wheel_linux_next_prev(self, event):
        # Linux X11: Button-4 = arriba, Button-5 = abajo
        if not hasattr(self, "lst_files") or not self.dcm_files:
            return
        sel = self.lst_files.curselection()
        cur = sel[0] if sel else 0
        if event.num == 4:
            self._select_preview_index(cur - 1)
        elif event.num == 5:
            self._select_preview_index(cur + 1)

        # -------------------------

    def _use_folder_as_patient(self, folder: str):
        folder = os.path.abspath(folder)
        if not os.path.isdir(folder):
            messagebox.showwarning("Ruta inválida", "Selecciona una carpeta válida.")
            return

        # reutiliza tu lógica actual de select_folder, pero sin diálogo
        seen = set()
        files = []
        for root, _, fnames in os.walk(folder, followlinks=False):
            for fn in fnames:
                p = os.path.join(root, fn)

                if not self._is_dicom_file(p):
                    continue

                rp = os.path.realpath(p)
                if rp not in seen:
                    seen.add(rp)
                    files.append(rp)

        files.sort()

        if not files:
            messagebox.showwarning("Sin DICOM", "No encontré archivos .dcm en esa carpeta.")
            return

        self.folder = folder
        self.dcm_files = files
        self.var_folder.set(folder)
        self.var_count.set(str(len(files)))

        self._detect_from_all_files()
        self._populate_preview_list()

        self.ent_name_new.configure(state="normal")
        self.cmb_date.configure(state="normal")
        self.nb.select(self.tab_edit)
        self._update_previews()

    def _open_dicom_from_explorer(self, path: str):
        # Abre el archivo directamente en el visor grande (sin cargar carpeta completa)
        try:
            ds = pydicom.dcmread(path, force=True)
            img = dicom_to_pil(ds)
        except Exception as e:
            messagebox.showerror("No se pudo abrir", f"No se pudo cargar este DICOM:\n{e}")
            return

        if getattr(self, "viewer", None) is None or not self.viewer.winfo_exists():
            self.viewer = DicomImageViewer(self)

        self.viewer.deiconify()
        self.viewer.lift()
        self.viewer.focus_force()
        self.viewer.show_image(img, title=os.path.basename(path))

    def _fmt_patient_name(self, raw: str) -> str:
        s = str(raw or "").strip()
        # DICOM suele venir como "APELLIDO^NOMBRE"
        s = s.replace("^", " ")
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _update_patient_banner(self):
        name = self._fmt_patient_name(self.var_name_orig.get())
        rut = (self.rut_detected or "").strip()

        # Si no hay rut, muestra PatientID "crudo" si existe en pid_set
        pid_raw = ""
        if not rut and getattr(self, "pid_set", None):
            pid_raw = sorted(self.pid_set)[0] if self.pid_set else ""

        left = rut if rut else pid_raw
        if left and name:
            self.var_patient_banner.set(f"{left} - {name}")
        elif name:
            self.var_patient_banner.set(name)
        elif left:
            self.var_patient_banner.set(left)
        else:
            self.var_patient_banner.set("(sin paciente)")

    def _series_label(self, meta: dict) -> str:
        mod = meta.get("mod", "")
        num = meta.get("num", "")
        pos = meta.get("pos", "")
        desc = meta.get("desc", "") or meta.get("proto", "")

        parts = []
        if mod:
            parts.append(mod)
        if num not in ("", None, "?"):
            parts.append(f"#{num}")
        if pos:
            parts.append(pos)

        label = " ".join(parts) if parts else "(serie)"
        desc = (desc or "").strip()
        if desc:
            label += f" [{desc}]"
        return label

    def _build_series_index(self):
        """Agrupa archivos por SeriesInstanceUID y arma los 'filtros' tipo visor."""
        self.series_map = {}
        self.series_meta = {}

        # Counters por serie para quedarnos con el valor más común
        per_uid = defaultdict(lambda: {
            "mod": Counter(), "num": Counter(), "pos": Counter(),
            "desc": Counter(), "proto": Counter(),
        })
        items = defaultdict(list)  # uid -> list[(instance, path)]

        tags = [
            "SeriesInstanceUID", "SeriesNumber", "SeriesDescription", "ProtocolName",
            "Modality", "PatientPosition", "InstanceNumber"
        ]

        for p in self.dcm_files:
            try:
                ds = pydicom.dcmread(p, force=True, stop_before_pixels=True, specific_tags=tags)
            except Exception:
                continue

            uid = str(getattr(ds, "SeriesInstanceUID", "") or "").strip()
            if not uid:
                # fallback: uid único por archivo si falta (raro, pero pasa)
                uid = f"NOUID::{p}"

            inst = getattr(ds, "InstanceNumber", None)
            try:
                inst_i = int(inst) if inst is not None else 0
            except Exception:
                inst_i = 0

            items[uid].append((inst_i, p))

            per_uid[uid]["mod"][str(getattr(ds, "Modality", "") or "").strip()] += 1
            per_uid[uid]["num"][str(getattr(ds, "SeriesNumber", "") or "").strip()] += 1
            per_uid[uid]["pos"][str(getattr(ds, "PatientPosition", "") or "").strip()] += 1
            per_uid[uid]["desc"][str(getattr(ds, "SeriesDescription", "") or "").strip()] += 1
            per_uid[uid]["proto"][str(getattr(ds, "ProtocolName", "") or "").strip()] += 1

        # Consolidar
        for uid, lst in items.items():
            lst_sorted = sorted(lst, key=lambda t: (t[0], t[1]))
            self.series_map[uid] = [p for _, p in lst_sorted]

            def top(counter: Counter) -> str:
                if not counter:
                    return ""
                k, _ = counter.most_common(1)[0]
                return k

            meta = {
                "mod": top(per_uid[uid]["mod"]) or "",
                "num": top(per_uid[uid]["num"]) or "?",
                "pos": top(per_uid[uid]["pos"]) or "",
                "desc": top(per_uid[uid]["desc"]) or "",
                "proto": top(per_uid[uid]["proto"]) or "",
                "n": len(self.series_map[uid]),
            }
            self.series_meta[uid] = meta

    def _load_series(self, uid: str):
        self.current_series_uid = uid
        self.current_series_files = self.series_map.get(uid, [])
        self.current_slice_idx = 0
        self._show_current_slice()

    def _show_current_slice(self):
        if not self.current_series_files:
            return
        idx = max(0, min(self.current_slice_idx, len(self.current_series_files) - 1))
        self.current_slice_idx = idx
        path = self.current_series_files[idx]
        self._show_dicom_preview(path, slice_idx=idx, slice_total=len(self.current_series_files))

    def _step_slice(self, delta: int):
        if not self.current_series_files:
            return
        self.slice_index = max(0, min(self.slice_index + delta, len(self.current_series_files) - 1))
        self._show_dicom_preview(self.current_series_files[self.slice_index])

    def _on_series_select(self, _evt=None):
        sel = self.series_tree.selection()
        if not sel:
            return
        iid = sel[0]
        uid = self._series_iid_to_uid.get(iid)
        if not uid:
            return
        self._load_series(uid)

    def _on_wheel_slice(self, event):
        # Windows/macOS: event.delta
        if getattr(event, "delta", 0) > 0:
            self._step_slice(-1)
        else:
            self._step_slice(+1)

    def _on_wheel_slice_linux(self, event):
        # Linux: Button-4 arriba, Button-5 abajo
        if event.num == 4:
            self._step_slice(-1)
        elif event.num == 5:
            self._step_slice(+1)

    def _build_series_index(self):
        self.series_map = {}
        self.series_meta = {}

        tags = [
            "StudyInstanceUID", "SeriesInstanceUID",
            "SeriesNumber", "SeriesDescription", "ProtocolName",
            "Modality", "BodyPartExamined",
            "InstanceNumber", "ImagePositionPatient"
        ]

        tmp = {}  # series_uid -> list of (sortkey, path)

        for i, f in enumerate(self.dcm_files):
            try:
                ds = pydicom.dcmread(f, force=True, stop_before_pixels=True, specific_tags=tags)
            except Exception:
                continue

            series_uid = str(getattr(ds, "SeriesInstanceUID", "") or "").strip()
            if not series_uid:
                series_uid = f"NOUID_{i}"

            # meta (una vez por serie)
            if series_uid not in self.series_meta:
                self.series_meta[series_uid] = {
                    "SeriesNumber": str(getattr(ds, "SeriesNumber", "") or "").strip(),
                    "SeriesDescription": str(getattr(ds, "SeriesDescription", "") or "").strip(),
                    "ProtocolName": str(getattr(ds, "ProtocolName", "") or "").strip(),
                    "Modality": str(getattr(ds, "Modality", "") or "").strip(),
                    "BodyPartExamined": str(getattr(ds, "BodyPartExamined", "") or "").strip(),
                }

            # orden: InstanceNumber si existe, si no, z (ImagePositionPatient[2]), si no, por nombre
            inst = getattr(ds, "InstanceNumber", None)
            try:
                inst = int(inst) if inst is not None else None
            except Exception:
                inst = None

            z = None
            ipp = getattr(ds, "ImagePositionPatient", None)
            try:
                if ipp is not None and len(ipp) >= 3:
                    z = float(ipp[2])
            except Exception:
                z = None

            if inst is not None:
                key = (0, inst, 0.0, os.path.basename(f))
            elif z is not None:
                key = (1, 0, z, os.path.basename(f))
            else:
                key = (2, 0, 0.0, os.path.basename(f))

            tmp.setdefault(series_uid, []).append((key, f))

        # ordenar y convertir a lista final
        for series_uid, items in tmp.items():
            items.sort(key=lambda x: x[0])
            self.series_map[series_uid] = [p for _, p in items]
        
    def _series_folder_name(self, meta: dict, idx: int) -> str:
        sn = (meta.get("SeriesNumber") or "").strip()
        try:
            prefix = f"SR{int(sn):03d}"
        except Exception:
            prefix = f"SR{idx:03d}"

        desc = meta.get("SeriesDescription") or meta.get("ProtocolName") or meta.get("Modality") or "Serie"
        name = sanitize_folder_name(f"{prefix}_{desc}")
        return name[:80] or prefix


if __name__ == "__main__":
    app = DicomPatientFolderEditor()
    app.mainloop()
