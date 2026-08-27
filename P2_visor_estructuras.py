import tkinter as tk
from ui_theme import *
from tkinter import ttk, filedialog, messagebox
import re
import unicodedata

DOSE_KEYS = [
    "Dosis mín. [Gy]",
    "Dosis máx. [Gy]",
    "Dosis media [Gy]",
    "Dosis modal [Gy]",
    "Dosis mediano [Gy]",
]

# -----------------------
# Normalización
# -----------------------
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def normalize_token_string(s: str) -> str:
    """
    Convierte separadores típicos a espacios para que los tokens (izq/der/L/R) se detecten bien.
    Ej: 'RINON_IZQ' -> 'rinon izq'
    """
    s = strip_accents(s).lower()
    s = re.sub(r"[_\-\(\)\[\]\{\}\./\\]+", " ", s)
    return normalize_spaces(s)

def singularize_es(s_display: str) -> str:
    """
    Heurística simple: riñones->riñon, pulmones->pulmon, manos->mano, etc.
    (No es lingüísticamente perfecta, pero sirve para agrupar en la UI.)
    """
    s = normalize_spaces(s_display)
    s_noacc = strip_accents(s).lower()

    # Solo singularizamos palabras largas para evitar cosas raras
    if len(s_noacc) <= 4:
        return s

    # riñones -> riñon / pulmones -> pulmon
    if s_noacc.endswith("es"):
        return s[:-2].strip()

    # manos -> mano
    if s_noacc.endswith("s"):
        return s[:-1].strip()

    return s

def canonical_key(display_name: str) -> str:
    """
    Llave interna para agrupar: sin acentos, minúsculas, sin plural, espacios normalizados.
    """
    s = normalize_spaces(display_name)
    s = singularize_es(s)
    s = normalize_token_string(s)
    return s

# -----------------------
# Lateralidad
# -----------------------
LEFT_TOKENS = {"izquierdo", "izq", "izqda", "izqdo", "left", "lt", "l"}
RIGHT_TOKENS = {"derecho", "der", "dcha", "dcho", "right", "rt", "r"}

def split_laterality(name: str):
    """
    Devuelve (base_display, side) donde side es 'L', 'R' o None.
    Preserva acentos en el base_display.
    """
    original = normalize_spaces(name)

    # Para detección robusta (sin acentos y separadores a espacios)
    tok_norm = normalize_token_string(original)
    tokens_norm = tok_norm.split()

    side = None
    if any(t in LEFT_TOKENS for t in tokens_norm):
        side = "L"
    if any(t in RIGHT_TOKENS for t in tokens_norm):
        side = "R"

    # sufijo final tipo ... _L / -R / (L)
    suf = re.search(r"(?:^|[\s_\-])([LR])\s*$", tok_norm.upper())
    if suf:
        side = suf.group(1)

    # Construir base PRESERVANDO acentos: tokenizamos el string "bonito" (con acentos)
    base_pretty = re.sub(r"[_\-\(\)\[\]\{\}\./\\]+", " ", original)
    base_pretty = normalize_spaces(base_pretty)
    tokens_pretty = base_pretty.split()

    # Filtramos tokens laterales comparando una versión normalizada de cada token
    filtered = []
    for t in tokens_pretty:
        t_norm = normalize_token_string(t)  # esto ya quita acentos y símbolos
        if t_norm in LEFT_TOKENS or t_norm in RIGHT_TOKENS:
            continue
        if t_norm in {"l", "r"}:
            continue
        filtered.append(t)

    base_display = " ".join(filtered).strip() or base_pretty

    # singularización (puede quitar la 's', pero no inventa acentos)
    base_display = singularize_es(base_display)
    base_display = normalize_spaces(base_display)

    return base_display, side

# -----------------------
# Parseo TXT
# -----------------------
def parse_structure_block(block_lines):
    # cortar antes de tabla DVH si existe
    table_idx = None
    for i, ln in enumerate(block_lines):
        s = ln.strip()
        if ("Dosis [Gy]" in s) and ("Proporción" in s or "Proporcion" in s):
            table_idx = i
            break

    header_lines = block_lines if table_idx is None else block_lines[:table_idx]
    fields = {}

    for ln in header_lines:
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        k = k.strip()
        v = v.strip()
        if k:
            fields[k] = v

    raw_header = "".join(header_lines).strip()
    return fields, raw_header

def parse_txt_grouped(path: str):
    """
    meta_global: dict
    groups: dict[canon_key] -> {
        'display': 'Riñon',
        'L': {...}, 'R': {...}, 'B': {...}  (B = bilateral/sin lado)
    }
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    meta_global = {}
    groups = {}

    current_name = None
    current_block = []

    def choose_display(old: str, new: str) -> str:
        """
        Preferimos un display más "limpio": más corto y sin underscores.
        """
        if not old:
            return new
        score_old = (len(old), "_" in old, old.isupper())
        score_new = (len(new), "_" in new, new.isupper())
        return new if score_new < score_old else old

    def flush_current():
        nonlocal current_name, current_block
        if not current_name:
            return
        fields, raw = parse_structure_block(current_block)

        base_display, side = split_laterality(current_name)
        key = canonical_key(base_display)

        groups.setdefault(key, {"display": base_display})
        groups[key]["display"] = choose_display(groups[key].get("display", ""), base_display)

        bucket = "L" if side == "L" else ("R" if side == "R" else "B")  # B = bilateral/sin lado
        groups[key][bucket] = {"name": current_name, "fields": fields, "raw": raw}

        current_name = None
        current_block = []

    for line in lines:
        if line.startswith("Estructura:"):
            flush_current()
            current_name = line.split(":", 1)[1].strip() or "Estructura_SIN_NOMBRE"
            current_block = [line]
            continue

        if current_name is None:
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    meta_global[k] = v
        else:
            current_block.append(line)

    flush_current()
    return meta_global, groups

# -----------------------
# GUI
# -----------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Visor TXT de Estructuras — Centro de Comando Clínico")
        self.geometry("1180x760")
        self.minsize(920, 620)
        apply_medical_theme(self)

        self.meta_global = {}
        self.groups = {}
        self.keys_in_list = []   # mapping listbox index -> canonical key

        self._build_ui()

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

        header = tk.Frame(self, bg=C_BG, padx=24, pady=18)
        header.pack(fill="x")
        title = tk.Frame(header, bg=C_BG)
        title.pack(side="left", fill="x", expand=True)
        tk.Label(
            title, text="Visor TXT de Estructuras", bg=C_BG, fg=C_TEXT,
            font=("TkDefaultFont", 22, "bold"),
        ).pack(anchor="w")
        self.file_label = tk.Label(
            title, text="Sin archivo", bg=C_BG, fg=C_MUTED,
            font=("TkDefaultFont", 9), anchor="w",
        )
        self.file_label.pack(fill="x", pady=(5, 0))
        tk.Label(
            header, text="SOLO LECTURA", bg="#e7e7f3", fg=C_MUTED,
            font=("TkDefaultFont", 8, "bold"), padx=12, pady=7,
        ).pack(side="right", padx=(12, 0))
        ttk.Button(
            header, text="Abrir TXT", style="Accent.TButton", command=self.open_file,
        ).pack(side="right")

        separator = ttk.Separator(self)
        separator.pack(fill="x")

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=C_CARD_INNER, padx=18, pady=18)
        paned.add(left, weight=1)

        tk.Label(
            left, text="ESTRUCTURAS IDENTIFICADAS", bg=C_CARD_INNER,
            fg=C_MUTED, font=("TkDefaultFont", 8, "bold"),
        ).pack(anchor="w")

        search_frame = tk.Frame(left, bg=C_CARD_INNER)
        search_frame.pack(fill="x", pady=(12, 12))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_list())
        ttk.Entry(search_frame, textvariable=self.search_var).pack(side="left", fill="x", expand=True)

        self.listbox = tk.Listbox(
            left, height=18, relief="flat", borderwidth=0,
            bg=C_CARD_INNER, fg=C_TEXT, selectbackground="#dbe1ff",
            selectforeground=C_ACTION_BLUE, activestyle="none",
            font=("TkDefaultFont", 11), highlightthickness=0,
        )
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_select_group)

        right = tk.Frame(paned, bg=C_BG, padx=26, pady=24)
        paned.add(right, weight=3)

        tk.Label(
            right, text="Datos globales del plan", bg=C_BG, fg=C_TEXT,
            font=("TkDefaultFont", 14, "bold"),
        ).pack(anchor="w")
        self.meta_text = tk.Text(
            right, height=6, wrap="word", relief="flat", borderwidth=1,
            bg=C_CARD, fg=C_TEXT, highlightthickness=1,
            highlightbackground=C_BORDER, padx=14, pady=12,
            font=("TkDefaultFont", 10),
        )
        self.meta_text.pack(fill="x", pady=(6, 10))
        self.meta_text.configure(state="disabled")

        self.sel_title = tk.Label(
            right, text="Seleccione un órgano o estructura", bg=C_BG, fg=C_TEXT,
            font=("TkDefaultFont", 16, "bold"),
        )
        self.sel_title.pack(anchor="w", pady=(6, 8))

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True)

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Seleccione el TXT",
            filetypes=[("Archivos de texto", "*.txt")],
            defaultextension=".txt"
        )
        if not path:
            return

        try:
            meta, groups = parse_txt_grouped(path)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo leer/parsear el archivo:\n{e}")
            return

        self.meta_global = meta
        self.groups = groups
        self.file_label.configure(text=path)

        self.render_meta_global()
        self.search_var.set("")
        self.refresh_list()

        if self.listbox.size() > 0:
            self.listbox.selection_set(0)
            self.on_select_group()

    def render_meta_global(self):
        lines = [f"{k}: {v}" for k, v in self.meta_global.items()]
        text = "\n".join(lines) if lines else "(No se detectaron datos globales)"

        self.meta_text.configure(state="normal")
        self.meta_text.delete("1.0", tk.END)
        self.meta_text.insert(tk.END, text)
        self.meta_text.configure(state="disabled")

    def refresh_list(self):
        filtro = normalize_token_string(self.search_var.get() or "")
        items = []

        for key, g in self.groups.items():
            disp = g.get("display", key)
            disp_norm = normalize_token_string(disp)
            if filtro and filtro not in disp_norm:
                continue
            items.append((disp, key))

        items.sort(key=lambda x: normalize_token_string(x[0]))

        self.listbox.delete(0, tk.END)
        self.keys_in_list = []
        for disp, key in items:
            self.listbox.insert(tk.END, disp.upper())

            self.keys_in_list.append(key)

    def clear_notebook(self):
        for tab_id in self.nb.tabs():
            self.nb.forget(tab_id)

    def make_tab(self, title: str, fields: dict, raw: str):
        frame = ttk.Frame(self.nb, padding=14, style="Card.TFrame")

        tree = ttk.Treeview(frame, columns=("campo", "valor"), show="headings", height=8)
        tree.heading("campo", text="Campo")
        tree.heading("valor", text="Valor")
        tree.column("campo", width=260, anchor="w")
        tree.column("valor", width=220, anchor="w")
        tree.pack(fill="x")

        for k in DOSE_KEYS:
            tree.insert("", "end", values=(k, fields.get(k, "(no encontrado)")))

        ttk.Label(
            frame, text="Texto del bloque antes de DVH", style="CardTitle.TLabel",
        ).pack(anchor="w", pady=(16, 7))
        txt = tk.Text(
            frame, wrap="word", height=12, relief="flat", borderwidth=0,
            bg="#2e3039", fg="#f0f0fb", insertbackground="#f0f0fb",
            font=("TkFixedFont", 10), padx=14, pady=12,
        )
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", raw if raw else "(vacío)")
        txt.configure(state="disabled")

        self.nb.add(frame, text=title)

    def on_select_group(self, event=None):
        if not self.keys_in_list:
            return
        sel = self.listbox.curselection()
        if not sel:
            return

        key = self.keys_in_list[sel[0]]
        g = self.groups.get(key, {})
        disp = g.get("display", key)

        self.sel_title.configure(text=f"Órgano/Estructura: {disp}")
        self.clear_notebook()

        # Pestañas según lo que exista
        has_L = "L" in g
        has_R = "R" in g

        if has_L:
            self.make_tab("Izquierdo", g["L"]["fields"], g["L"].get("raw", ""))
        if has_R:
            self.make_tab("Derecho", g["R"]["fields"], g["R"].get("raw", ""))

        # Mostrar "Sin lado" SOLO si NO existen ambos (L y R) a la vez
        if "B" in g and not (has_L and has_R):
            self.make_tab("Sin lado", g["B"]["fields"], g["B"].get("raw", ""))


        if not any(k in g for k in ("L", "R", "B")):
            self.make_tab("Sin datos", {}, "(No se encontró información para esta estructura)")

if __name__ == "__main__":
    app = App()
    try:
        style = ttk.Style(app)
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    app.mainloop()
