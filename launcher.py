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
            "Lee archivos TXT con bloques de estructuras y dosis.\n\n"
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
            "• Permite editar nombres.\n"
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
            "Procesa carpetas DICOM para hacerlos compatibles con el programa de Eclipse.\n\n"
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

    def _apply_style(self):
        # Estilo claro, limpio y “profesional” sin librerías externas
        style = ttk.Style(self)

        # Elegimos un tema decente si está disponible
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        # Colores suaves
        self.C_BG = "#f5f7fb"
        self.C_CARD = "#ffffff"
        self.C_BORDER = "#d9dee8"
        self.C_TEXT = "#1f2a37"
        self.C_MUTED = "#6b7280"
        self.C_PRIMARY = "#2563eb"  # azul (solo para hover/indicaciones leves)

        self.configure(background=self.C_BG)

        # Tipografías
        self.FONT_TITLE = ("TkDefaultFont", 15, "bold")
        self.FONT_SUB = ("TkDefaultFont", 10)
        self.FONT_H2 = ("TkDefaultFont", 11, "bold")

        # Frames
        style.configure("App.TFrame", background=self.C_BG)
        style.configure("Card.TFrame", background=self.C_CARD)
        style.configure("CardTitle.TLabel", background=self.C_CARD, foreground=self.C_TEXT, font=self.FONT_H2)
        style.configure("Muted.TLabel", background=self.C_CARD, foreground=self.C_MUTED)

        style.configure("HeaderTitle.TLabel", background=self.C_BG, foreground=self.C_TEXT, font=self.FONT_TITLE)
        style.configure("HeaderSub.TLabel", background=self.C_BG, foreground=self.C_MUTED, font=self.FONT_SUB)

        # Botones
        style.configure("TButton", padding=(10, 8))
        # Botón principal: texto más contrastado
        style.configure("Primary.TButton", padding=(12, 10), foreground="#111827")
        style.map(
            "Primary.TButton",
            foreground=[
                ("disabled", "#9ca3af"),
                ("pressed", "#111827"),
                ("active", "#111827"),
                ("!disabled", "#111827"),
            ],
        )
        # Nota: ttk no permite setear background del botón de forma consistente cross-platform sin hacks.
        # Igual se verá “pro” por layout; el botón primary se distingue por texto y tamaño.

        # Treeview
        style.configure("Treeview", rowheight=26, bordercolor=self.C_BORDER, relief="solid")
        style.configure("Treeview.Heading", font=("TkDefaultFont", 10, "bold"))

        # Entry
        style.configure("TEntry", padding=(8, 6))

    def _build_ui(self):
        root = ttk.Frame(self, padding=14, style="App.TFrame")
        root.pack(fill="both", expand=True)

        # Header
        header = ttk.Frame(root, style="App.TFrame")
        header.pack(fill="x", pady=(0, 12))

        ttk.Label(header, text="Suite de Programas", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Selecciona una herramienta a la izquierda para ver su descripción. Doble click (o Enter) para abrir.",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        # Main
        main = ttk.Frame(root, style="App.TFrame")
        main.pack(fill="both", expand=True)

        main.columnconfigure(0, weight=1, uniform="a")
        main.columnconfigure(1, weight=2, uniform="a")
        main.rowconfigure(0, weight=1)

        # Sidebar card
        sidebar_outer = ttk.Frame(main, style="Card.TFrame", padding=12)
        sidebar_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        # “Borde” sutil simulando tarjeta (frame interno)
        sidebar = ttk.Frame(sidebar_outer, style="Card.TFrame")
        sidebar.pack(fill="both", expand=True)

        ttk.Label(sidebar, text="Programas", style="CardTitle.TLabel").pack(anchor="w")

        ttk.Label(sidebar, text="Buscar", style="Muted.TLabel").pack(anchor="w", pady=(10, 4))
        search = ttk.Entry(sidebar, textvariable=self.search_var)
        search.pack(fill="x")
        self.search_var.trace_add("write", lambda *_: self._populate())

        ttk.Label(sidebar, text="Lista", style="Muted.TLabel").pack(anchor="w", pady=(10, 4))

        # --- Treeview con scrolls (reemplaza tu bloque actual de self.tree) ---
        tree_frame = ttk.Frame(sidebar)
        tree_frame.pack(fill="both", expand=True)

        xscroll = AutoScrollbar(tree_frame, orient="horizontal")
        yscroll = AutoScrollbar(tree_frame, orient="vertical")

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("id", "name"),
            show="headings",
            selectmode="browse",
            height=14,
            xscrollcommand=xscroll.set,
            yscrollcommand=yscroll.set,
        )
        xscroll.config(command=self.tree.xview)
        yscroll.config(command=self.tree.yview)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Programa")

        # ID fijo; Programa se adapta al ancho disponible
        self.tree.column("id", width=55, minwidth=55, stretch=False, anchor="center")
        self.tree.column("name", width=1, minwidth=220, stretch=True, anchor="w")

        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.open_selected())


        ttk.Button(sidebar, text="Abrir programa", style="Primary.TButton", command=self.open_selected).pack(
            fill="x", pady=(12, 0)
        )

        ttk.Button(
            sidebar,
            text="OT (Guardar PDF en Escritorio/OTs)",
            style="Primary.TButton",
            command=self.import_ot_pdf
        ).pack(fill="x", pady=(10, 0))


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

        # Estilo del área de texto (claro y legible)
        self.desc.configure(
            background="#ffffff",
            foreground=self.C_TEXT,
            insertbackground=self.C_TEXT,
            padx=10,
            pady=10,
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
