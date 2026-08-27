import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont
import sys

# CLINICAL COMMAND CENTER — sistema visual derivado del diseño de Stitch
C_BG = "#faf8ff"
C_CARD = "#ffffff"
C_CARD_INNER = "#f3f3fe"
C_BORDER = "#c3c6d7"
C_TEXT = "#191b23"
C_MUTED = "#434655"
C_TOPBAR = "#ffffff"      # blanco en la cabecera
C_TOPBAR_SUB = "#64748b"  # slate-500 para el subtitulo superior

C_ACTION_BLUE = "#004ac6"
C_ACTION_BLUE_H = "#003ea8"
C_ACTION_GREEN = "#16a34a"    # green-600
C_ACTION_GREEN_H = "#15803d"  # green-700
C_ACTION_CANCEL = "#64748b"   # slate-500 (botón cancelar moderado para contraste texto blanco)
C_ACTION_CANCEL_H = "#475569" # slate-600
C_DANGER = "#dc2626"          # red-600
C_DANGER_H = "#b91c1c"        # red-700


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
    tags = kwargs.get("tags", "")

    # 4 esquinas
    canvas.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90,
                      style="pieslice", fill=fill, outline=outline, width=width, tags=tags)
    canvas.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90,
                      style="pieslice", fill=fill, outline=outline, width=width, tags=tags)
    canvas.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90,
                      style="pieslice", fill=fill, outline=outline, width=width, tags=tags)
    canvas.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90,
                      style="pieslice", fill=fill, outline=outline, width=width, tags=tags)

    # centro + bandas
    canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=outline, width=width, tags=tags)
    canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=outline, width=width, tags=tags)


class ClinicButton(tk.Canvas):
    """
    Botón redondeado con color, hover y command.
    """
    def __init__(
        self,
        parent,
        text: str,
        command=None,
        bg=C_ACTION_BLUE,
        hover_bg=C_ACTION_BLUE_H,
        fg="#ffffff",
        radius=8,
        height=42,
        font=("TkDefaultFont", 10, "bold"),
        outline="",
        outline_width=0,
        padx=14,
        cursor="hand2",
        parent_bg=None,
        width=None,
        min_width=120,
        max_width=420,
        shadow=True,
        shadow_offset=2,
        shadow_color="#cbd5e1",
        **kwargs
    ):
        if parent_bg is not None:
            canvas_bg = parent_bg
        else:
            try:
                canvas_bg = parent.cget("background")
            except Exception:
                canvas_bg = C_BG

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
            fill = "#334155" # disabled color

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

        _rounded_rect(
            self,
            2, 2, w - 2, h - 2,
            r=self._radius,
            fill=fill,
            outline="",
            width=0
        )

        self.create_text(
            w // 2, h // 2,
            anchor="center",
            text=self._text,
            fill=self._fg if self._enabled else "#94a3b8",
            font=self._font
        )


class RoundedCard(ttk.Frame):
    """
    Tarjeta redondeada real: se dibuja en Canvas y el contenido va en un Frame interno.
    """
    def __init__(
        self,
        parent,
        bg_card: str = C_CARD,
        bg_parent: str = C_BG,
        radius: int = 8,
        padding: int = 12,
        shadow: bool = True,
        shadow_offset: int = 2,
        shadow_color: str = "#e2e8f0",
        border_color: str | None = C_BORDER,
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
        self.border_color = border_color or ""
        self.border_width = border_width

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=self.bg_parent)
        self.canvas.pack(fill="both", expand=True)

        self.inner = ttk.Frame(self.canvas)
        
        # Configure the inner frame's style based on bg_card
        if self.bg_card == C_CARD_INNER:
            self.inner.configure(style="CardWhite.TFrame")
        elif self.bg_card == C_CARD:
            self.inner.configure(style="Card.TFrame")
        elif self.bg_card == C_TOPBAR:
            self.inner.configure(style="Topbar.TFrame")

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

        bx = max(0, int(self.border_width))
        has_border = bool(self.border_color) and bx > 0

        if has_border:
            _rounded_rect(
                self.canvas,
                2, 2, w - 2, h - 2,
                r=self.radius,
                fill=self.border_color,
                outline="",
                width=0,
                tags="card",
            )
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
            _rounded_rect(
                self.canvas,
                2, 2, w - 2, h - 2,
                r=self.radius,
                fill=self.bg_card,
                outline="",
                width=0,
                tags="card",
            )

        inner_w = max(1, w - (self.padding * 2))
        inner_h = max(1, h - (self.padding * 2))

        self.canvas.coords(self._win, self.padding, self.padding)
        self.canvas.itemconfigure(self._win, width=inner_w, height=inner_h)


def apply_medical_theme(root: tk.Tk | tk.Toplevel):
    """Aplica el sistema visual Clinical Command Center a una ventana Tk."""
    root.configure(background=C_BG)
    
    style = ttk.Style(root)
    try:
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    # Escoger tipografía segura
    fams = set(tkfont.families(root))
    if sys.platform.startswith("win"):
        preferred = ["Segoe UI", "Inter", "Noto Sans", "Arial", "TkDefaultFont"]
    else:
        preferred = ["Noto Sans", "Inter", "DejaVu Sans", "Ubuntu", "TkDefaultFont"]

    ui_font = "TkDefaultFont"
    for f in preferred:
        if f in fams:
            ui_font = f
            break

    FONT_TITLE = ("TkDefaultFont", 24, "bold")
    FONT_H2 = ("TkDefaultFont", 11, "bold")
    FONT_BODY = (ui_font, 10)
    FONT_MUTED = (ui_font, 10, "bold")
    FONT_TREE = (ui_font, 11, "bold")
    FONT_TREE_HEAD = (ui_font, 10, "bold")

    # Frames base
    style.configure("App.TFrame", background=C_BG)
    style.configure("Card.TFrame", background=C_CARD)
    style.configure("CardWhite.TFrame", background=C_CARD_INNER)
    style.configure("Panel.TFrame", background=C_CARD) # Alias

    # Topbar
    style.configure("Topbar.TFrame", background=C_TOPBAR)
    style.configure("TopbarTitle.TLabel", background=C_TOPBAR, foreground=C_TEXT, font=FONT_TITLE)
    style.configure("TopbarSub.TLabel", background=C_TOPBAR, foreground=C_TOPBAR_SUB, font=FONT_BODY)
    style.configure("TopbarSubBig.TLabel", background=C_TOPBAR, foreground=C_TOPBAR_SUB, font=(ui_font, 12))

    # Labels en fondo App
    style.configure("AppTitle.TLabel", background=C_BG, foreground=C_TEXT, font=FONT_H2)
    style.configure("AppMuted.TLabel", background=C_BG, foreground=C_MUTED, font=FONT_BODY)
    style.configure("Title.TLabel", background=C_BG, foreground=C_TEXT, font=FONT_TITLE)

    # Labels en Tarjeta (Card)
    style.configure("CardTitle.TLabel", background=C_CARD, foreground=C_TEXT, font=FONT_H2)
    style.configure("Body.TLabel", background=C_CARD, foreground=C_MUTED, font=FONT_BODY)
    style.configure("Muted.TLabel", background=C_CARD, foreground=C_MUTED, font=FONT_MUTED)

    # Labels en Tarjeta Inner
    style.configure("CardWhiteTitle.TLabel", background=C_CARD_INNER, foreground=C_TEXT, font=FONT_H2)
    style.configure("CardWhiteMuted.TLabel", background=C_CARD_INNER, foreground=C_MUTED, font=FONT_BODY)
    
    # Textos comunes
    style.configure("TLabel", background=C_BG, foreground=C_TEXT, font=FONT_BODY)

    # Entradas (Entry)
    style.configure("TEntry", fieldbackground=C_CARD_INNER, foreground=C_TEXT, insertcolor=C_TEXT)
    
    # Combobox
    style.configure("TCombobox", padding=6)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", C_CARD_INNER)],
        background=[("readonly", C_CARD_INNER)],
        foreground=[("readonly", C_TEXT)],
    )
    style.configure("OT.TCombobox", padding=6)
    style.map(
        "OT.TCombobox",
        fieldbackground=[("readonly", C_CARD_INNER)],
        background=[("readonly", C_CARD_INNER)],
        foreground=[("readonly", C_TEXT)],
    )

    # Treeview
    style.configure(
        "Treeview",
        font=FONT_TREE,
        rowheight=30,
        bordercolor=C_BORDER,
        relief="flat",
        background=C_CARD_INNER,
        fieldbackground=C_CARD_INNER,
        foreground=C_TEXT,
    )
    style.configure(
        "Treeview.Heading",
        font=FONT_TREE_HEAD,
        foreground=C_TEXT,
        background=C_CARD,
    )
    style.map(
        "Treeview",
        background=[("selected", C_ACTION_BLUE)],
        foreground=[("selected", "#ffffff")],
    )
    
    # Botones Nativos (Fallback por si no usan ClinicButton)
    style.configure(
        "TButton",
        font=(ui_font, 10, "bold"),
        background=C_ACTION_BLUE,
        foreground=C_TEXT
    )
    style.map(
        "TButton",
        background=[("active", C_ACTION_BLUE_H)],
        foreground=[("disabled", "#737686")],
    )

    style.configure(
        "Accent.TButton",
        font=(ui_font, 10, "bold"),
        padding=(14, 9),
        background=C_ACTION_BLUE,
        foreground="#ffffff",
    )
    style.map(
        "Accent.TButton",
        background=[("active", C_ACTION_BLUE_H), ("disabled", "#d9d9e5")],
        foreground=[("disabled", "#737686")],
    )

    style.configure(
        "Ghost.TButton",
        font=(ui_font, 10, "bold"),
        padding=(12, 8),
        background=C_CARD_INNER,
        foreground=C_TEXT,
    )
    style.map("Ghost.TButton", background=[("active", "#e1e2ed")])

    style.configure(
        "Ok.TButton",
        font=(ui_font, 10, "bold"),
        padding=(14, 9),
        background=C_ACTION_GREEN,
        foreground="#ffffff",
    )
    style.map("Ok.TButton", background=[("active", C_ACTION_GREEN_H)])

    style.configure(
        "Danger.TButton",
        font=(ui_font, 10, "bold"),
        padding=(14, 9),
        background=C_DANGER,
        foreground="#ffffff",
    )
    style.map("Danger.TButton", background=[("active", C_DANGER_H)])

    style.configure("TCheckbutton", background=C_CARD, foreground=C_TEXT, font=FONT_BODY)
    style.map("TCheckbutton", background=[("active", C_CARD)])
