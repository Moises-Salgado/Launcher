# launcher.py
import sys
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

HERE = Path(__file__).resolve().parent

# ----------------------------
# Catálogo de programas
# ----------------------------
PROGRAMS = [
    {
        "id": "P1",
        "name": "Programa 1 — Extraer datos desde PDF",
        "short": "PDF → TXT/Excel",
        "script": HERE / "P1_ExtraerDatosPDF.py",
        "desc": (
            "Extrae campos de una Orden de Trabajo desde un PDF (fechas, tiempos, descripción, notas, subtareas, etc.)\n"
            "y permite guardar el resultado en un TXT y en un Excel (UNIQUE.xlsx) con una hoja por OT."
        ),
        "deps": ["fitz (PyMuPDF)", "camelot", "pandas", "openpyxl"],
        "tips": [
            "Si el PDF tiene tablas complejas, Camelot puede requerir dependencias extra (Ghostscript).",
            "El Excel se actualiza en el mismo directorio donde guardas el TXT."
        ],
    },
    {
        "id": "P2",
        "name": "Programa 2 — Visor TXT de órganos/estructuras",
        "short": "Visor TXT (DVH/cabecera)",
        "script": HERE / "P2_visor_estructuras.py",
        "desc": (
            "Abre un TXT con bloques tipo 'Estructura:' y agrupa estructuras por órgano.\n"
            "Detecta lateralidad (izq/der) y muestra pestañas con campos clave de dosis."
        ),
        "deps": ["tkinter (incluido)"],
        "tips": [
            "Usa el buscador para filtrar rápido por órgano.",
            "Si el TXT viene con nombres raros, la app intenta normalizar acentos y separadores."
        ],
    },
    {
        "id": "P3",
        "name": "Programa 3 — Editor de nombres y visor de imágenes",
        "short": "Renombrar + visor",
        "script": HERE / "P3_editor_dmc_carpeta.py",
        "desc": (
            "Herramientas para trabajar con carpetas DICOM: edición/normalización de nombres\n"
            "y visor de imágenes asociado para revisión rápida."
        ),
        "deps": ["pydicom", "Pillow (si aplica)", "matplotlib (si aplica)"],
        "tips": [
            "Si el visor usa matplotlib, asegúrate de tenerlo instalado.",
        ],
    },
    {
        "id": "P4",
        "name": "Programa 4 — Preparar DICOM para Eclipse",
        "short": "DICOM → compatible Eclipse",
        "script": HERE / "P4_1_dicom_eclipse_bulletproof.py",
        "desc": (
            "Procesa DICOM para corregir/normalizar metadatos y/o parámetros que impiden que Eclipse\n"
            "los abra o los muestre correctamente (según tu flujo actual del servicio)."
        ),
        "deps": ["pydicom"],
        "tips": [
            "Úsalo sobre una copia si vas a modificar archivos en lote.",
        ],
    },
]

# ----------------------------
# Utilidades
# ----------------------------
def run_script(path: Path):
    if not path.exists():
        messagebox.showerror("No encontrado", f"No existe:\n{path}")
        return
    try:
        subprocess.Popen([sys.executable, str(path)], cwd=str(HERE))
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir:\n{path}\n\n{e}")

def open_folder(path: Path):
    folder = path.parent
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(folder)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir la carpeta:\n{folder}\n\n{e}")

def copy_to_clipboard(root: tk.Tk, text: str):
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()  # asegura persistencia

def check_imports_for(program: dict):
    """
    Chequeo rápido (no perfecto): intenta importar módulos base por nombre.
    'fitz (PyMuPDF)' lo mapeamos a 'fitz', etc.
    """
    mapping = {
        "fitz (PyMuPDF)": "fitz",
        "camelot": "camelot",
        "pandas": "pandas",
        "openpyxl": "openpyxl",
        "pydicom": "pydicom",
        "Pillow (si aplica)": "PIL",
        "matplotlib (si aplica)": "matplotlib",
        "tkinter (incluido)": "tkinter",
    }
    missing = []
    for d in program.get("deps", []):
        mod = mapping.get(d, d.split()[0])
        try:
            __import__(mod)
        except Exception:
            missing.append(d)
    return missing

# ----------------------------
# GUI
# ----------------------------
class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Suite de Programas")
        self.geometry("980x560")
        self.minsize(900, 520)

        # Tema (si existe)
        try:
            style = ttk.Style(self)
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        self.status_var = tk.StringVar(value="Listo.")

        self._build_ui()
        self._populate()
        self._select_first()

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # Top header
        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="Suite de Programas", font=("TkDefaultFont", 14, "bold")).pack(side="left")
        ttk.Label(
            header,
            text="(cada programa se abre en su propia ventana)",
            foreground="#666",
        ).pack(side="left", padx=10)

        # Main content
        paned = ttk.Panedwindow(root, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # Left: list
        left = ttk.Frame(paned, padding=8)
        paned.add(left, weight=1)

        ttk.Label(left, text="Programas disponibles", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._populate())
        search_row = ttk.Frame(left)
        search_row.pack(fill="x", pady=(8, 8))
        ttk.Label(search_row, text="Buscar:").pack(side="left")
        ttk.Entry(search_row, textvariable=self.search_var).pack(side="left", fill="x", expand=True, padx=6)

        self.listbox = tk.Listbox(left, height=16)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-1>", lambda e: self._open_selected())

        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_row, text="Abrir", command=self._open_selected).pack(side="left")
        ttk.Button(btn_row, text="Ver carpeta", command=self._open_folder_selected).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Revisar librerías", command=self._check_deps_selected).pack(side="left")

        # Right: details
        right = ttk.Frame(paned, padding=8)
        paned.add(right, weight=2)

        self.title_lbl = ttk.Label(right, text="Seleccione un programa...", font=("TkDefaultFont", 12, "bold"))
        self.title_lbl.pack(anchor="w", pady=(0, 6))

        self.path_lbl = ttk.Label(right, text="", foreground="#555")
        self.path_lbl.pack(anchor="w", pady=(0, 10))

        ttk.Label(right, text="Descripción", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.desc_txt = tk.Text(right, height=7, wrap="word")
        self.desc_txt.pack(fill="x", pady=(6, 10))
        self.desc_txt.configure(state="disabled")

        ttk.Label(right, text="Dependencias", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.deps_txt = tk.Text(right, height=4, wrap="word")
        self.deps_txt.pack(fill="x", pady=(6, 10))
        self.deps_txt.configure(state="disabled")

        ttk.Label(right, text="Tips", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.tips_txt = tk.Text(right, height=6, wrap="word")
        self.tips_txt.pack(fill="both", expand=True, pady=(6, 0))
        self.tips_txt.configure(state="disabled")

        # Bottom status bar
        status = ttk.Frame(root)
        status.pack(fill="x", pady=(10, 0))
        ttk.Separator(status).pack(fill="x", pady=(0, 6))
        ttk.Label(status, textvariable=self.status_var).pack(anchor="w")

        self._index_map = []  # listbox index -> PROGRAMS index

    def _populate(self):
        q = (self.search_var.get() or "").strip().lower()

        self.listbox.delete(0, tk.END)
        self._index_map = []

        for i, p in enumerate(PROGRAMS):
            text = f"{p['id']} — {p['short']}"
            hay = (p["id"] + " " + p["name"] + " " + p["short"]).lower()
            if q and q not in hay:
                continue
            self.listbox.insert(tk.END, text)
            self._index_map.append(i)

        self.status_var.set(f"{len(self._index_map)} programa(s) en la lista.")

    def _select_first(self):
        if self.listbox.size() > 0:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(0)
            self._on_select()

    def _get_selected_program(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        idx = sel[0]
        if idx >= len(self._index_map):
            return None
        return PROGRAMS[self._index_map[idx]]

    def _on_select(self, event=None):
        p = self._get_selected_program()
        if not p:
            return

        self.title_lbl.configure(text=p["name"])
        self.path_lbl.configure(text=str(p["script"]))

        self._set_text(self.desc_txt, p.get("desc", ""))
        self._set_text(self.deps_txt, " • " + "\n • ".join(p.get("deps", [])) if p.get("deps") else "(sin datos)")
        self._set_text(self.tips_txt, " • " + "\n • ".join(p.get("tips", [])) if p.get("tips") else "(sin tips)")

    def _set_text(self, widget: tk.Text, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)
        widget.configure(state="disabled")

    def _open_selected(self):
        p = self._get_selected_program()
        if not p:
            self.status_var.set("Seleccione un programa para abrir.")
            return
        self.status_var.set(f"Abrindo {p['id']}...")
        run_script(p["script"])

    def _open_folder_selected(self):
        p = self._get_selected_program()
        if not p:
            self.status_var.set("Seleccione un programa.")
            return
        open_folder(p["script"])
        self.status_var.set(f"Carpeta abierta: {p['script'].parent}")

    def _check_deps_selected(self):
        p = self._get_selected_program()
        if not p:
            self.status_var.set("Seleccione un programa.")
            return
        missing = check_imports_for(p)
        if not missing:
            messagebox.showinfo("OK", "✅ No se detectaron librerías faltantes (import básico).")
            self.status_var.set(f"{p['id']}: dependencias OK (import básico).")
        else:
            messagebox.showwarning(
                "Faltan librerías",
                "⚠️ Posibles librerías faltantes:\n\n- " + "\n- ".join(missing) +
                "\n\nNota: esto es un chequeo básico por import; algunas dependencias pueden ser opcionales."
            )
            self.status_var.set(f"{p['id']}: faltan {len(missing)} dependencia(s) (import básico).")

def main():
    Launcher().mainloop()

if __name__ == "__main__":
    main()
