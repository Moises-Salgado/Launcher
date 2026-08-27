import os
import re
import csv
import shutil
import subprocess
import threading
import queue
from dataclasses import dataclass
from collections import defaultdict
import sys
import tkinter as tk
from tkinter import Tk, Toplevel, filedialog, StringVar, BooleanVar, ttk, messagebox, PhotoImage
from tkinter.scrolledtext import ScrolledText
from ui_theme import C_ACTION_BLUE, C_BG, C_BORDER, C_CARD, C_CARD_INNER, C_MUTED, C_TEXT, apply_medical_theme

import pydicom
from pydicom.errors import InvalidDicomError


# -----------------------------
# Constantes DICOM útiles
# -----------------------------
UNCOMPRESSED_TS = {
    "1.2.840.10008.1.2",    # Implicit VR Little Endian
    "1.2.840.10008.1.2.1",  # Explicit VR Little Endian
    "1.2.840.10008.1.2.2",  # Explicit VR Big Endian (raro)
}

# SOP Classes de imagen (lo típico para Eclipse)
IMAGE_SOP = {
    "1.2.840.10008.5.1.4.1.1.2",    # CT Image Storage
    "1.2.840.10008.5.1.4.1.1.4",    # MR Image Storage
    "1.2.840.10008.5.1.4.1.1.2.1",  # Enhanced CT Image Storage
    "1.2.840.10008.5.1.4.1.1.4.1",  # Enhanced MR Image Storage
}

# Objetos RT y relacionados que conviene COPIAR TAL CUAL (NO “convertir”)
RT_AND_RELATED_SOP = {
    "1.2.840.10008.5.1.4.1.1.481.1",  # RT Image Storage
    "1.2.840.10008.5.1.4.1.1.481.2",  # RT Dose Storage
    "1.2.840.10008.5.1.4.1.1.481.3",  # RT Structure Set Storage
    "1.2.840.10008.5.1.4.1.1.481.5",  # RT Plan Storage
    "1.2.840.10008.5.1.4.1.1.481.8",  # RT Ion Plan Storage (si aparece)
    "1.2.840.10008.5.1.4.1.1.481.9",  # RT Ion Beams Treatment Record Storage
    "1.2.840.10008.5.1.4.1.1.481.6",  # RT Brachy Treatment Record Storage
    "1.2.840.10008.5.1.4.1.1.66.1",   # Spatial Registration Storage
    "1.2.840.10008.5.1.4.1.1.67",     # Spatial Fiducials Storage
    "1.2.840.10008.5.1.4.1.1.88.11",  # Basic Text SR (a veces)
    "1.2.840.10008.5.1.4.1.1.88.22",  # Enhanced SR (a veces)
    "1.2.840.10008.5.1.4.1.1.11.1",   # Grayscale Softcopy Presentation State Storage (lo viste)
}

# Regex simple para detectar números en InstanceNumber que venga como string raro
_re_int = re.compile(r"^-?\d+$")


@dataclass
class FileRow:
    src: str
    dst: str
    group: str          # DICOM_ECLIPSE / __RECHAZADOS__ / __NO_DICOM__
    action: str         # COPIADO / CONVERTIDO / RECHAZADO / NO_DICOM
    detail: str
    mime: str
    transfer_syntax: str
    sop_class_uid: str
    modality: str
    series_desc: str
    study_uid: str
    series_uid: str
    instance_number: str


# -----------------------------
# Helpers de sistema
# -----------------------------
def have_tool(name: str) -> bool:
    return shutil.which(name) is not None

def run_cmd(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def ensure_parent_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)

def safe_add_dcm_ext(path: str) -> str:
    # Si ya termina con .dcm/.DCM, no agregar.
    base = os.path.basename(path)
    if base.lower().endswith(".dcm"):
        return path
    return path + ".dcm"

def file_mime(path: str) -> str:
    # Usa `file` si existe, si no, fallback “desconocido”
    if not have_tool("file"):
        return ""
    r = run_cmd(["file", "-b", "--mime-type", path])
    return (r.stdout or "").strip()

def looks_like_dicom_preamble(path: str) -> bool:
    # Chequeo clásico "DICM" en offset 128 (no siempre está)
    try:
        with open(path, "rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except Exception:
        return False


# -----------------------------
# Lectura DICOM robusta (sin forzar primero)
# -----------------------------
def read_meta_strict_then_force(path: str):
    """
    Devuelve (ds, strict_ok, err_msg)
    - strict_ok=True si se pudo leer SIN force.
    - Si strict falla, intenta force=True.
    """
    try:
        ds = pydicom.dcmread(path, stop_before_pixels=True, force=False)
        return ds, True, ""
    except Exception as e_strict:
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True, force=True)
            return ds, False, f"strict_failed: {type(e_strict).__name__}"
        except Exception as e_force:
            return None, False, f"force_failed: {type(e_force).__name__}"

def get_tag_str(ds, tag_name: str) -> str:
    try:
        v = ds.get(tag_name, None)
        if v is None:
            return ""
        return str(v)
    except Exception:
        return ""

def get_transfer_syntax(ds) -> str:
    try:
        ts = ds.file_meta.get("TransferSyntaxUID", None)
        return str(ts) if ts else ""
    except Exception:
        return ""


# -----------------------------
# Decisión de enrutamiento
# -----------------------------
def classify_dicom(ds) -> str:
    """
    Retorna:
      - "IMAGE" (MR/CT/Enhanced MR/CT)
      - "RT" (RTSTRUCT/RTPLAN/RTDOSE/REG/PR/SR etc)
      - "OTHER_DICOM" (DICOM válido pero no clave para Eclipse)
    """
    sop = get_tag_str(ds, "SOPClassUID")
    mod = get_tag_str(ds, "Modality")
    if sop in RT_AND_RELATED_SOP:
        return "RT"
    if sop in IMAGE_SOP:
        return "IMAGE"
    # Si mod es CT/MR pero sop no es el típico, igual lo tratamos con cuidado
    if mod in ("MR", "CT"):
        return "IMAGE"
    return "OTHER_DICOM"


def get_instance_number(ds) -> str:
    inst = ds.get("InstanceNumber", None)
    if inst is None:
        return ""
    s = str(inst).strip()
    return s


# -----------------------------
# Conversión (solo para imágenes comprimidas)
# -----------------------------
def convert_image_to_uncompressed(src_path: str, dst_path: str, use_gdcm: bool):
    """
    Intenta:
      1) dcmdjpeg
      2) gdcmconv --raw
    Devuelve (ok:bool, detail:str)
    """
    # 1) DCMTK dcmdjpeg
    if have_tool("dcmdjpeg"):
        r = run_cmd(["dcmdjpeg", src_path, dst_path])
        if r.returncode == 0 and os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
            return True, "dcmdjpeg"
        # si falló, limpia posible archivo basura
        if os.path.exists(dst_path):
            try: os.remove(dst_path)
            except: pass

    # 2) GDCM fallback
    if use_gdcm and have_tool("gdcmconv"):
        r = run_cmd(["gdcmconv", "--raw", src_path, dst_path])
        if r.returncode == 0 and os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
            return True, "gdcmconv --raw"
        if os.path.exists(dst_path):
            try: os.remove(dst_path)
            except: pass

    return False, "no_se_pudo_descomprimir"


# -----------------------------
# Recorrido
# -----------------------------
def walk_files(root_dir: str):
    for root, _, files in os.walk(root_dir):
        for fn in files:
            yield os.path.join(root, fn)


# -----------------------------
# Proceso principal
# -----------------------------
def process_all(
    src_root: str,
    dst_root: str,
    use_gdcm: bool,
    copy_nondicom: bool,
    anonimize_test: bool,
    q: queue.Queue
):
    try:
        """
        Genera:
        dst_root/DICOM_ECLIPSE/...
        dst_root/__RECHAZADOS__/...
        dst_root/__NO_DICOM__/...
        reportes CSV en dst_root/
        """
        out_ok_root = os.path.join(dst_root, "DICOM_ECLIPSE")
        out_rej_root = os.path.join(dst_root, "__RECHAZADOS__")
        out_nd_root  = os.path.join(dst_root, "__NO_DICOM__")

        os.makedirs(out_ok_root, exist_ok=True)
        os.makedirs(out_rej_root, exist_ok=True)
        if copy_nondicom:
            os.makedirs(out_nd_root, exist_ok=True)

        files = list(walk_files(src_root))
        q.put(f"Origen: {src_root}")
        q.put(f"Destino: {dst_root}")
        q.put(f"Archivos encontrados (total): {len(files)}")
        q.put("---- Iniciando clasificación/conversión ----")

        rows = []
        series_map = defaultdict(list)  # (study, series, modality) -> [instance ints]

        cnt_ok = cnt_conv = cnt_rej = cnt_nd = 0

        for idx, src_path in enumerate(files, 1):
            rel = os.path.relpath(src_path, src_root)

            mime = file_mime(src_path)
            # Atajo duro: text/plain → NO DICOM (esto te evita meter “basura” a Eclipse)
            if mime == "text/plain":
                if copy_nondicom:
                    dst_path = os.path.join(out_nd_root, rel)
                    ensure_parent_dir(dst_path)
                    shutil.copy2(src_path, dst_path)
                    rows.append(FileRow(
                        src=src_path, dst=dst_path, group="__NO_DICOM__",
                        action="NO_DICOM", detail="mime=text/plain",
                        mime=mime, transfer_syntax="", sop_class_uid="", modality="",
                        series_desc="", study_uid="", series_uid="", instance_number=""
                    ))
                cnt_nd += 1
                continue

            # Chequeo DICOM: preámbulo o pydicom strict/force
            ds, strict_ok, err_msg = read_meta_strict_then_force(src_path)
            if ds is None:
                # No se pudo leer como DICOM
                if copy_nondicom:
                    dst_path = os.path.join(out_nd_root, rel)
                    ensure_parent_dir(dst_path)
                    shutil.copy2(src_path, dst_path)
                    rows.append(FileRow(
                        src=src_path, dst=dst_path, group="__NO_DICOM__",
                        action="NO_DICOM", detail=f"no_dicom ({err_msg})",
                        mime=mime, transfer_syntax="", sop_class_uid="", modality="",
                        series_desc="", study_uid="", series_uid="", instance_number=""
                    ))
                cnt_nd += 1
                continue

            # Extraer meta
            ts = get_transfer_syntax(ds)
            sop = get_tag_str(ds, "SOPClassUID")
            mod = get_tag_str(ds, "Modality")
            sdesc = get_tag_str(ds, "SeriesDescription")
            study = get_tag_str(ds, "StudyInstanceUID")
            series = get_tag_str(ds, "SeriesInstanceUID")
            inst = get_instance_number(ds)

            kind = classify_dicom(ds)

            # Definir rutas destino (preservando estructura)
            ok_dst = safe_add_dcm_ext(os.path.join(out_ok_root, rel))
            rej_dst = safe_add_dcm_ext(os.path.join(out_rej_root, rel))

            # Regla: si faltan tags esenciales y el file parece raro → RECHAZADO
            # (pero lo copiamos igual para no perder nada)
            if not sop and not mod and not looks_like_dicom_preamble(src_path):
                ensure_parent_dir(rej_dst)
                shutil.copy2(src_path, rej_dst)
                rows.append(FileRow(
                    src=src_path, dst=rej_dst, group="__RECHAZADOS__",
                    action="RECHAZADO", detail="sin SOPClassUID/Modality (DICOM dudoso)",
                    mime=mime, transfer_syntax=ts, sop_class_uid=sop, modality=mod,
                    series_desc=sdesc, study_uid=study, series_uid=series, instance_number=inst
                ))
                cnt_rej += 1
                continue

            # Si es RT u “related”: copiar tal cual a DICOM_ECLIPSE (Eclipse lo necesita)
            if kind == "RT":
                ensure_parent_dir(ok_dst)
                shutil.copy2(src_path, ok_dst)
                rows.append(FileRow(
                    src=src_path, dst=ok_dst, group="DICOM_ECLIPSE",
                    action="COPIADO", detail="RT/related (copiado tal cual)",
                    mime=mime, transfer_syntax=ts, sop_class_uid=sop, modality=mod,
                    series_desc=sdesc, study_uid=study, series_uid=series, instance_number=inst
                ))
                cnt_ok += 1
                continue

            # Si es imagen MR/CT (o parecido):
            if kind == "IMAGE":
                # Si no hay TransferSyntax → no confiable → RECHAZADO
                if not ts:
                    ensure_parent_dir(rej_dst)
                    shutil.copy2(src_path, rej_dst)
                    rows.append(FileRow(
                        src=src_path, dst=rej_dst, group="__RECHAZADOS__",
                        action="RECHAZADO", detail="sin TransferSyntaxUID",
                        mime=mime, transfer_syntax=ts, sop_class_uid=sop, modality=mod,
                        series_desc=sdesc, study_uid=study, series_uid=series, instance_number=inst
                    ))
                    cnt_rej += 1
                    continue

                # Si ya está sin compresión: copiar
                if ts in UNCOMPRESSED_TS:
                    ensure_parent_dir(ok_dst)
                    shutil.copy2(src_path, ok_dst)
                    rows.append(FileRow(
                        src=src_path, dst=ok_dst, group="DICOM_ECLIPSE",
                        action="COPIADO", detail="imagen sin compresión",
                        mime=mime, transfer_syntax=ts, sop_class_uid=sop, modality=mod,
                        series_desc=sdesc, study_uid=study, series_uid=series, instance_number=inst
                    ))
                    cnt_ok += 1

                else:
                    # Intentar descomprimir
                    ensure_parent_dir(ok_dst)
                    ok, detail = convert_image_to_uncompressed(src_path, ok_dst, use_gdcm=use_gdcm)
                    if ok:
                        rows.append(FileRow(
                            src=src_path, dst=ok_dst, group="DICOM_ECLIPSE",
                            action="CONVERTIDO", detail=f"descomprimido ({detail})",
                            mime=mime, transfer_syntax=ts, sop_class_uid=sop, modality=mod,
                            series_desc=sdesc, study_uid=study, series_uid=series, instance_number=inst
                        ))
                        cnt_conv += 1
                    else:
                        # No se pudo descomprimir → cuarentena
                        if os.path.exists(ok_dst):
                            try: os.remove(ok_dst)
                            except: pass
                        ensure_parent_dir(rej_dst)
                        shutil.copy2(src_path, rej_dst)
                        rows.append(FileRow(
                            src=src_path, dst=rej_dst, group="__RECHAZADOS__",
                            action="RECHAZADO", detail="no se pudo descomprimir (posible corrupción PixelData o TS no soportado)",
                            mime=mime, transfer_syntax=ts, sop_class_uid=sop, modality=mod,
                            series_desc=sdesc, study_uid=study, series_uid=series, instance_number=inst
                        ))
                        cnt_rej += 1

                # Para reporte de series (solo si tenemos IDs y un InstanceNumber numérico)
                key = (study, series, mod)
                if study and series and mod and inst and _re_int.match(inst):
                    try:
                        series_map[key].append(int(inst))
                    except Exception:
                        pass

                # --- FIX ECLIPSE MR FUSION PIXELATION & ANONIMIZATION ---
                # Si MR tiene cortes solapados, forzamos Thickness = Spacing para engañar a Eclipse.
                # Además, si se activó la anonimización, cambiamos PatientName y PatientID.
                if (anonimize_test or mod == "MR") and os.path.exists(ok_dst):
                    try:
                        ds_mod = pydicom.dcmread(ok_dst)
                        modified = False

                        if anonimize_test:
                            ds_mod.PatientName = "TEST CYC"
                            ds_mod.PatientID = "000000000"
                            modified = True

                        if mod == "MR":
                            # 1) Eliminar etiquetas Rescale/Window que corrompen el brillo en Eclipse
                            for tag in [(0x0028, 0x1052), (0x0028, 0x1053), (0x0028, 0x1050), (0x0028, 0x1051)]:
                                if tag in ds_mod:
                                    del ds_mod[tag]
                                    modified = True

                            # 2) Forzar Thickness = Spacing si hay solapamiento (Evita pixelado 3D)
                            spacing = None
                            if (0x0018, 0x0088) in ds_mod:
                                spacing = ds_mod[0x0018, 0x0088].value

                            if spacing is not None and (0x0018, 0x0050) in ds_mod:
                                thickness = ds_mod[0x0018, 0x0050].value
                                try:
                                    sp_val = float(spacing)
                                    th_val = float(thickness)
                                    if abs(sp_val - th_val) > 0.001:
                                        ds_mod[0x0018, 0x0050].value = spacing
                                        modified = True
                                except ValueError:
                                    pass

                        if modified:
                            pydicom.dcmwrite(ok_dst, ds_mod)
                    except Exception:
                        pass
                # ----------------------------------------

                continue

            # OTHER_DICOM: por defecto NO lo metas a Eclipse, pero NO lo pierdas:
            ensure_parent_dir(rej_dst)
            shutil.copy2(src_path, rej_dst)
            rows.append(FileRow(
                src=src_path, dst=rej_dst, group="__RECHAZADOS__",
                action="RECHAZADO", detail="DICOM no imagen/RT (cuarentena por seguridad)",
                mime=mime, transfer_syntax=ts, sop_class_uid=sop, modality=mod,
                series_desc=sdesc, study_uid=study, series_uid=series, instance_number=inst
            ))
            cnt_rej += 1

        # Reporte archivo-a-archivo
        rep_files = os.path.join(dst_root, "reporte_archivos.csv")
        with open(rep_files, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "src","dst","group","action","detail","mime",
                "transfer_syntax","sop_class_uid","modality","series_desc",
                "study_uid","series_uid","instance_number"
            ])
            for r in rows:
                w.writerow([
                    r.src, r.dst, r.group, r.action, r.detail, r.mime,
                    r.transfer_syntax, r.sop_class_uid, r.modality, r.series_desc,
                    r.study_uid, r.series_uid, r.instance_number
                ])

        # Reporte por serie (faltantes por InstanceNumber)
        rep_series = os.path.join(dst_root, "reporte_series.csv")
        with open(rep_series, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["study_uid","series_uid","modality","count","min_inst","max_inst","missing_instances_sample"])
            for (study, series, mod), insts in sorted(series_map.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])):
                if not insts:
                    continue
                insts_sorted = sorted(set(insts))
                mi, ma = insts_sorted[0], insts_sorted[-1]
                missing = [i for i in range(mi, ma+1) if i not in set(insts_sorted)]
                miss_sample = ",".join(map(str, missing[:50])) + (",..." if len(missing) > 50 else "")
                w.writerow([study, series, mod, len(insts_sorted), mi, ma, miss_sample])

        q.put("---- Resumen ----")
        q.put(f"DICOM_ECLIPSE (copiados sin compresión): {cnt_ok}")
        q.put(f"DICOM_ECLIPSE (convertidos/descomprimidos): {cnt_conv}")
        q.put(f"__RECHAZADOS__ (cuarentena): {cnt_rej}")
        q.put(f"__NO_DICOM__ (no DICOM): {cnt_nd}")
        q.put(f"Reporte archivos: {rep_files}")
        q.put(f"Reporte series:  {rep_series}")
        # Enviar payload para ventana final bonita
        q.put(("DONE", {
            "dst_root": dst_root,
            "cnt_ok": cnt_ok,
            "cnt_conv": cnt_conv,
            "cnt_rej": cnt_rej,
            "cnt_nd": cnt_nd,
        }))
    except Exception as e:
        q.put(("ERROR", f"{type(e).__name__}: {e}"))



# -----------------------------
# GUI
# -----------------------------
class App:
    def __init__(self, master: Tk):
        self.master = master
        master.title("Compatibilizar CD para Eclipse — Centro de Comando Clínico")
        master.geometry("1240x780")
        master.minsize(980, 680)

        self._set_app_icon()
        self.src_var = StringVar()
        self.dst_var = StringVar()
        self.use_gdcm_var = BooleanVar(value=True)
        self.copy_nondicom_var = BooleanVar(value=True)
        self.anonimize_test_var = BooleanVar(value=False)

        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        shell = tk.Frame(master, bg=C_BG)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=5)
        shell.columnconfigure(1, weight=7)
        shell.rowconfigure(1, weight=1)

        topbar = tk.Frame(shell, bg=C_CARD, padx=24, pady=13)
        topbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(
            topbar, text="Centro de Comando Clínico", bg=C_CARD, fg=C_TEXT,
            font=("TkDefaultFont", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            topbar, text="●  EJECUCIÓN LOCAL", bg=C_CARD_INNER,
            fg=C_ACTION_BLUE, font=("TkDefaultFont", 9, "bold"), padx=14, pady=6,
        ).pack(side="right")

        left = tk.Frame(shell, bg=C_BG, padx=20, pady=22)
        left.grid(row=1, column=0, sticky="nsew")
        right = tk.Frame(shell, bg=C_BG, padx=0, pady=22)
        right.grid(row=1, column=1, sticky="nsew", padx=(10, 20))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        tk.Label(
            left, text="Compatibilizar CD para Eclipse", bg=C_BG, fg=C_TEXT,
            font=("TkDefaultFont", 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            left,
            text=(
                "Acondicionamiento de archivos DICOM provenientes del HGGB, "
                "Clínica Los Andes, Clínica Biobío y otras instituciones."
            ),
            bg=C_BG, fg=C_MUTED, font=("TkDefaultFont", 10),
            justify="left", wraplength=470,
        ).pack(anchor="w", pady=(7, 20))

        locations_border = tk.Frame(left, bg=C_BORDER, padx=1, pady=1)
        locations_border.pack(fill="x")
        locations = tk.Frame(locations_border, bg=C_CARD_INNER, padx=18, pady=17)
        locations.pack(fill="x")
        locations.columnconfigure(0, weight=1)
        tk.Label(
            locations, text="UBICACIONES", bg=C_CARD_INNER, fg=C_ACTION_BLUE,
            font=("TkDefaultFont", 9, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 13))

        tk.Label(
            locations, text="Carpeta de origen (CD/DVD o directorio)",
            bg=C_CARD_INNER, fg=C_TEXT, font=("TkDefaultFont", 9),
        ).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Entry(locations, textvariable=self.src_var).grid(
            row=2, column=0, sticky="ew", pady=(5, 14), ipady=6,
        )
        ttk.Button(
            locations, text="Elegir…", style="Ghost.TButton", command=self.pick_src,
        ).grid(row=2, column=1, padx=(8, 0), pady=(5, 14))

        tk.Label(
            locations, text="Carpeta de destino (directorio de trabajo Eclipse)",
            bg=C_CARD_INNER, fg=C_TEXT, font=("TkDefaultFont", 9),
        ).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Entry(locations, textvariable=self.dst_var).grid(
            row=4, column=0, sticky="ew", pady=(5, 0), ipady=6,
        )
        ttk.Button(
            locations, text="Elegir…", style="Ghost.TButton", command=self.pick_dst,
        ).grid(row=4, column=1, padx=(8, 0), pady=(5, 0))

        options_border = tk.Frame(left, bg=C_BORDER, padx=1, pady=1)
        options_border.pack(fill="x", pady=(16, 0))
        options = tk.Frame(options_border, bg=C_CARD_INNER, padx=18, pady=17)
        options.pack(fill="x")
        tk.Label(
            options, text="OPCIONES DE PROCESAMIENTO", bg=C_CARD_INNER,
            fg=C_ACTION_BLUE, font=("TkDefaultFont", 9, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        check_config = dict(
            bg=C_CARD_INNER, activebackground=C_CARD_INNER, fg=C_TEXT,
            activeforeground=C_TEXT, selectcolor=C_CARD, anchor="w",
            justify="left", font=("TkDefaultFont", 9), bd=0,
            highlightthickness=0,
        )
        tk.Checkbutton(
            options,
            text="Usar conversión alternativa si es necesario (recomendado)",
            variable=self.use_gdcm_var, **check_config,
        ).pack(fill="x", pady=4)
        tk.Checkbutton(
            options,
            text="Guardar archivos no DICOM en una carpeta separada (recomendado)",
            variable=self.copy_nondicom_var, **check_config,
        ).pack(fill="x", pady=4)
        ttk.Separator(options).pack(fill="x", pady=8)
        tk.Checkbutton(
            options,
            text="Anonimizar como paciente TEST CYC (000000000) para pruebas",
            variable=self.anonimize_test_var, **check_config,
        ).pack(fill="x", pady=4)

        self.btn_run = ttk.Button(
            left, text="Revisar y procesar", style="Accent.TButton", command=self.run,
        )
        self.btn_run.pack(side="bottom", fill="x", pady=(18, 0))

        log_border = tk.Frame(right, bg=C_BORDER, padx=1, pady=1)
        log_border.grid(row=0, column=0, sticky="nsew")
        log_border.columnconfigure(0, weight=1)
        log_border.rowconfigure(1, weight=1)
        log_header = tk.Frame(log_border, bg=C_CARD, padx=16, pady=12)
        log_header.grid(row=0, column=0, sticky="ew")
        log_header.columnconfigure(1, weight=1)
        tk.Label(
            log_header, text="Registro de operación", bg=C_CARD, fg=C_TEXT,
            font=("TkDefaultFont", 14, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.status = tk.Label(
            log_header, text="Esperando inicio", bg="#e1e2ed", fg=C_MUTED,
            font=("TkDefaultFont", 9, "bold"), padx=10, pady=4,
        )
        self.status.grid(row=0, column=2, sticky="e")

        self.pb = ttk.Progressbar(log_header, mode="indeterminate", length=150)
        self.pb.grid(row=0, column=1, sticky="e", padx=12)
        self.pb.grid_remove()

        self.log = ScrolledText(
            log_border, height=20, relief="flat", borderwidth=0,
            bg="#2e3039", fg="#f0f0fb", insertbackground="#f0f0fb",
            font=("TkFixedFont", 10), padx=16, pady=14,
        )
        self.log.grid(row=1, column=0, sticky="nsew")
        self.log.insert(
            "end",
            "El análisis de la carpeta de origen y la clasificación de archivos se mostrarán aquí.\n",
        )

        self.q = queue.Queue()
        self.master.after(100, self.poll_queue)

        self._check_tools()

    def _check_tools(self):
        if not have_tool("dcmdjpeg"):
            self.append_log("[AVISO] No encuentro 'dcmdjpeg'. Instala: sudo apt install dcmtk")
        if self.use_gdcm_var.get() and not have_tool("gdcmconv"):
            self.append_log("[AVISO] No encuentro 'gdcmconv'. Instala: sudo apt install gdcm-tools")
        if not have_tool("file"):
            self.append_log("[AVISO] No encuentro 'file'. Instala: sudo apt install file")

    def _set_app_icon(self):
        """
        Busca icon.ico o icon.png en la misma carpeta del script.
        - Windows: icon.ico (preferido)
        - Linux/macOS: icon.png via iconphoto
        """
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        except Exception:
            base_dir = os.getcwd()

        ico_path = os.path.join(base_dir, "icon.ico")
        png_path = os.path.join(base_dir, "icon.png")

        # 1) Windows .ico (si está)
        if os.path.exists(ico_path):
            try:
                self.master.iconbitmap(ico_path)
                return
            except Exception:
                pass  # si falla, probamos png

        # 2) PNG (multiplataforma)
        if os.path.exists(png_path):
            try:
                img = PhotoImage(file=png_path)
                self.master.iconphoto(True, img)
                self._icon_img_ref = img  # mantener referencia para que no lo "recoja" el GC
            except Exception:
                pass

    def pick_src(self):
        d = filedialog.askdirectory(title="Selecciona carpeta ORIGEN")
        if d:
            self.src_var.set(d)

    def pick_dst(self):
        d = filedialog.askdirectory(title="Selecciona carpeta DESTINO")
        if d:
            self.dst_var.set(d)

    def append_log(self, msg: str):
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    def open_path(self, path: str):
        """Abrir carpeta/archivo con el visor del sistema (Linux/Windows/macOS)."""
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showwarning("No se pudo abrir", f"No pude abrir:\n{path}\n\nDetalle: {e}")

    def show_done_dialog(self, info: dict):
        dlg = Toplevel(self.master)
        dlg.title("Proceso completado")
        dlg.transient(self.master)
        dlg.resizable(False, False)

        # Mostrar arriba (Linux a veces lo deja "detrás")
        try:
            dlg.attributes("-topmost", True)
        except Exception:
            pass

        # Centrar cerca de la ventana principal
        self.master.update_idletasks()
        x = self.master.winfo_rootx() + 50
        y = self.master.winfo_rooty() + 50
        dlg.geometry(f"+{x}+{y}")

        # Contenido
        wrap = ttk.Frame(dlg, padding=16)
        wrap.grid(row=0, column=0, sticky="nsew")

        ttk.Label(wrap, text="Listo", font=("TkDefaultFont", 14, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(wrap, text="Los archivos fueron procesados correctamente.").grid(row=1, column=0, sticky="w", pady=(4, 10))

        ttk.Separator(wrap).grid(row=2, column=0, sticky="ew", pady=8)

        cnt_ok   = info.get("cnt_ok", 0)
        cnt_conv = info.get("cnt_conv", 0)
        cnt_rej  = info.get("cnt_rej", 0)
        cnt_nd   = info.get("cnt_nd", 0)

        ttk.Label(wrap, text="Resumen:", font=("TkDefaultFont", 10, "bold")).grid(row=3, column=0, sticky="w", pady=(0, 6))
        ttk.Label(wrap, text=f"• Copiados (sin compresión): {cnt_ok}").grid(row=4, column=0, sticky="w")
        ttk.Label(wrap, text=f"• Convertidos / descomprimidos: {cnt_conv}").grid(row=5, column=0, sticky="w")
        ttk.Label(wrap, text=f"• Rechazados (cuarentena): {cnt_rej}").grid(row=6, column=0, sticky="w")
        ttk.Label(wrap, text=f"• No DICOM: {cnt_nd}").grid(row=7, column=0, sticky="w")

        ttk.Separator(wrap).grid(row=8, column=0, sticky="ew", pady=12)

        dst_root = info.get("dst_root", "")

        btns = ttk.Frame(wrap)
        btns.grid(row=9, column=0, sticky="e")

        ttk.Button(btns, text="Abrir carpeta destino", command=lambda: self.open_path(dst_root)).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(btns, text="Cerrar", command=dlg.destroy).grid(row=0, column=1)

        # Asegurar que aparezca
        dlg.update_idletasks()
        dlg.lift()
        try:
            dlg.focus_force()
        except Exception:
            pass

        # Soltar topmost (para no dejar la app siempre arriba)
        try:
            dlg.attributes("-topmost", False)
        except Exception:
            pass

        # Grab al final (cuando ya está visible)
        try:
            dlg.grab_set()
        except Exception:
            pass

        dlg.bind("<Return>", lambda e: dlg.destroy())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _safe_show_done_dialog(self, info: dict):
        try:
            self.show_done_dialog(info)
        except Exception as e:
            # Fallback si por cualquier motivo el Toplevel falla
            self.append_log(f"[ERROR] Ventana final: {type(e).__name__}: {e}")
            messagebox.showinfo(
                "Proceso completado",
                "✅ Listo.\n\nEl proceso terminó, pero no se pudo mostrar la ventana final.\n"
                f"Detalle: {type(e).__name__}: {e}"
            )

    def poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()

                if isinstance(msg, tuple) and msg and msg[0] == "DONE":
                    # Detener/ocultar progreso
                    try:
                        self.pb.stop()
                        self.pb.grid_remove()
                    except Exception:
                        pass

                    info = msg[1] if len(msg) > 1 and isinstance(msg[1], dict) else {}

                    self.btn_run.config(state="normal")
                    self.status.config(text="Terminado.")
                    self.append_log("✅ Proceso completado.")

                    # Abrir diálogo de forma segura (fuera del while)
                    self.master.after(0, lambda i=info: self._safe_show_done_dialog(i))

                elif isinstance(msg, tuple) and msg and msg[0] == "ERROR":
                    try:
                        self.pb.stop()
                        self.pb.grid_remove()
                    except Exception:
                        pass

                    err = msg[1] if len(msg) > 1 else "Error desconocido"

                    self.btn_run.config(state="normal")
                    self.status.config(text="Error.")
                    self.append_log(f"[ERROR] {err}")
                    messagebox.showerror("Error durante el proceso", str(err))

                else:
                    self.append_log(str(msg))

        except queue.Empty:
            pass
        except Exception as e:
            # Si algo falla aquí, no “muere” el poll_queue
            self.append_log(f"[ERROR] poll_queue: {type(e).__name__}: {e}")

        self.master.after(100, self.poll_queue)

    def run(self):
        src = self.src_var.get().strip()
        dst = self.dst_var.get().strip()

        if not src or not os.path.isdir(src):
            self.status.config(text="Selecciona un origen válido.")
            return
        if not dst:
            self.status.config(text="Selecciona un destino válido.")
            return

        # Evitar casos peligrosos (destino = origen)
        if os.path.abspath(src) == os.path.abspath(dst):
            messagebox.showerror("Destino inválido", "La carpeta destino no puede ser la misma que la carpeta origen.")
            return

        # Si el destino existe y NO está vacío: preguntar si se reemplaza (para no mezclar)
        if os.path.isdir(dst):
            try:
                not_empty = any(os.scandir(dst))
            except Exception:
                not_empty = True  # si no puedo leer, asumo que tiene contenido/risco

            if not_empty:
                ok = messagebox.askyesno(
                    "Carpeta destino con contenido",
                    "La carpeta destino ya tiene archivos.\n\n"
                    "Para NO mezclar datos, si continúas se BORRARÁ TODO el contenido de esa carpeta.\n\n"
                    f"Destino:\n{dst}\n\n"
                    "¿Quieres reemplazarla?"
                )
                if not ok:
                    self.status.config(text="Operación cancelada.")
                    return

                # Borrar todo el destino para partir limpio
                try:
                    shutil.rmtree(dst)
                except Exception as e:
                    messagebox.showerror("No se pudo reemplazar", f"No pude borrar la carpeta destino:\n{dst}\n\nDetalle: {e}")
                    self.status.config(text="Error.")
                    return

        # Crear destino limpio
        os.makedirs(dst, exist_ok=True)

        self.btn_run.config(state="disabled")
        self.status.config(text="Procesando...")
        self.pb.grid()     # mostrar
        self.pb.start(12)  # velocidad (ms entre pasos aprox)

        t = threading.Thread(
            target=process_all,
            args=(src, dst, self.use_gdcm_var.get(), self.copy_nondicom_var.get(), self.anonimize_test_var.get(), self.q),
            daemon=True
        )
        t.start()


def main():
    root = Tk()
    apply_medical_theme(root)
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
