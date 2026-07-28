#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
varian_color_bridge_gui_v5_1_carpetas.py

Objetivo:
- Trabajar principalmente con carpetas DICOM S1..S9, como las entrega el CD. ZIP queda solo como compatibilidad opcional.
- Intentar maximizar las chances de ver PET/OT con color o equivalente útil en Varian/Eclipse.
- Ofrecer 3 métodos seleccionables:
    1) PET real + CT (copiar S3+S4 y S3+S2)  -> mejor probabilidad de ver color nativo en Varian.
    2) Intento color OT/PT Secondary Capture -> convierte S5/S6/S7 a PALETTE COLOR.
    3) Derivados desde OT -> pseudo-PET axial/coronal y RTSTRUCT experimental de hotspot.

Notas importantes:
- No modifica originales.
- RTSTRUCT y pseudo-PET son EXPERIMENTALES, generados desde screenshots (OT), útiles solo como apoyo visual.
- El método con mayor probabilidad real de mostrar color nativo es el #1, importando PET real (S3) junto a CT (S4 o S2).
"""

import os
import re
import sys
import csv
import math
import copy
import shutil
import zipfile
import queue
import tempfile
import threading
import argparse
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple, Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except Exception:
    tk = None
    ttk = None

try:
    import numpy as np
    import pydicom
    from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
    from pydicom.sequence import Sequence
    from pydicom.uid import (
        ExplicitVRLittleEndian,
        generate_uid,
        SecondaryCaptureImageStorage,
        PositronEmissionTomographyImageStorage,
        CTImageStorage,
        RTStructureSetStorage,
    )
    from PIL import Image
except Exception as e:
    print("Faltan dependencias. Instala con: pip install pydicom numpy pillow")
    print("Detalle:", e)
    sys.exit(1)

APP_NAME = "Varian Color Bridge GUI v6"
SERIES_KEYS = [f"S{i}" for i in range(1, 10)]

METHOD_NATIVE = "native_pet_ct"
METHOD_PALETTE = "ot_palette_color"
METHOD_DERIVED = "ot_pseudopet_rtstruct"
METHOD_ALL = "all_safe"
METHOD_ALL_PLUS = "all_plus_palette"

STRENGTH_SOFT = "soft"
STRENGTH_NORMAL = "normal"
STRENGTH_STRONG = "strong"

# --------------------------
# Modelos / utilidades
# --------------------------

@dataclass
class ReportRow:
    method: str
    action: str
    src: str
    dst: str
    detail: str


def safe_str(v) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, (list, tuple)):
            return "\\".join(str(x) for x in v)
        return str(v)
    except Exception:
        return ""


def ensure_dir(path: str):
    Path(path).mkdir(parents=True, exist_ok=True)


def log_emit(q, msg: str):
    if q is not None:
        q.put(msg)


def add_report(rows: List[ReportRow], method: str, action: str, src: str, dst: str, detail: str):
    rows.append(ReportRow(method=method, action=action, src=src, dst=dst, detail=detail))


def now_date_time():
    dt = datetime.now()
    return dt.strftime("%Y%m%d"), dt.strftime("%H%M%S")


def list_files_recursive(root: str) -> List[str]:
    out = []
    for r, _, fns in os.walk(root):
        for fn in fns:
            out.append(os.path.join(r, fn))
    return sorted(out)


def read_dicom(path: str):
    return pydicom.dcmread(path, force=True)


def read_headers(paths: List[str]):
    out = []
    for p in sorted(paths):
        try:
            ds = pydicom.dcmread(p, force=True, stop_before_pixels=True)
            out.append((p, ds))
        except Exception:
            pass
    return out


def write_csv_txt_report(dst_root: str, rows: List[ReportRow]):
    csv_path = os.path.join(dst_root, "reporte_varian_color_bridge_v6.csv")
    txt_path = os.path.join(dst_root, "reporte_varian_color_bridge_v6.txt")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["method", "action", "src", "dst", "detail"])
        w.writeheader()
        for r in rows:
            w.writerow(r.__dict__)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(APP_NAME + "\n")
        f.write("=" * 90 + "\n")
        f.write(f"Registros: {len(rows)}\n")
        counts = {}
        for r in rows:
            counts[(r.method, r.action)] = counts.get((r.method, r.action), 0) + 1
        for (m, a), c in sorted(counts.items()):
            f.write(f"- {m} | {a}: {c}\n")
        f.write("\nPrimeros 200 registros:\n")
        for r in rows[:200]:
            f.write(f"[{r.method}] [{r.action}] {Path(r.src).name} -> {r.detail}\n")
    return csv_path, txt_path


# --------------------------
# Descubrimiento de S1..S9
# --------------------------

def discover_series_sources(source_root: str) -> Dict[str, Tuple[str, str]]:
    """
    Devuelve { 'S3': ('zip', '/ruta/S3(3).zip') } o ('dir','/ruta/S3')
    Acepta carpetas extraídas o zip.
    """
    found: Dict[str, Tuple[str, str]] = {}
    for entry in sorted(os.listdir(source_root)):
        p = os.path.join(source_root, entry)
        m = re.match(r'^(S[1-9])(?:\b|\(|_|\.)', entry, flags=re.IGNORECASE)
        if not m:
            continue
        key = m.group(1).upper()
        if os.path.isdir(p):
            found[key] = ('dir', p)
        elif os.path.isfile(p) and entry.lower().endswith('.zip'):
            found[key] = ('zip', p)
    return found


def materialize_series(key: str, source_info: Tuple[str, str], workspace: str, log_q=None) -> str:
    kind, path = source_info
    if kind == 'dir':
        return path
    out_dir = os.path.join(workspace, key)
    if os.path.isdir(out_dir) and any(Path(out_dir).iterdir()):
        return out_dir
    ensure_dir(out_dir)
    log_emit(log_q, f"Extrayendo {key} desde {os.path.basename(path)}...")
    with zipfile.ZipFile(path) as z:
        z.extractall(out_dir)
    # muchas veces queda una subcarpeta Sx dentro de out_dir
    sub = os.path.join(out_dir, key)
    if os.path.isdir(sub):
        return sub
    return out_dir


def collect_series_files(series_dir: str) -> List[str]:
    files = []
    for p in list_files_recursive(series_dir):
        if os.path.isfile(p):
            files.append(p)
    return sorted(files)


# --------------------------
# Copia / paquetes nativos
# --------------------------

def copy_dicom_files(paths: List[str], dst_dir: str, rows: List[ReportRow], method: str):
    ensure_dir(dst_dir)
    for src in paths:
        dst = os.path.join(dst_dir, Path(src).name + ("" if Path(src).suffix else ".dcm"))
        shutil.copy2(src, dst)
        add_report(rows, method, "copied", src, dst, "Copied original DICOM")


def method_native_pet_ct(series_dirs: Dict[str, str], dst_root: str, rows: List[ReportRow], log_q=None):
    needed = ["S2", "S3", "S4"]
    for k in needed:
        if k not in series_dirs:
            log_emit(log_q, f"[WARN] Falta {k}; se omite parte de método 1")
    if "S3" in series_dirs and "S4" in series_dirs:
        out = os.path.join(dst_root, "METHOD1_NATIVE_PETCT_S4_CTCE")
        ensure_dir(out)
        copy_dicom_files(collect_series_files(series_dirs["S4"]), os.path.join(out, "S4_CT"), rows, METHOD_NATIVE)
        copy_dicom_files(collect_series_files(series_dirs["S3"]), os.path.join(out, "S3_PET"), rows, METHOD_NATIVE)
        log_emit(log_q, "Generado paquete S3 + S4")
    if "S3" in series_dirs and "S2" in series_dirs:
        out = os.path.join(dst_root, "METHOD1_NATIVE_PETCT_S2_CTNONCE")
        ensure_dir(out)
        copy_dicom_files(collect_series_files(series_dirs["S2"]), os.path.join(out, "S2_CT"), rows, METHOD_NATIVE)
        copy_dicom_files(collect_series_files(series_dirs["S3"]), os.path.join(out, "S3_PET"), rows, METHOD_NATIVE)
        log_emit(log_q, "Generado paquete S3 + S2")


# --------------------------
# Conversión de color / realce
# --------------------------

def to_uint8_rgb(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr)
    if arr.ndim == 4 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    if arr.ndim != 3 or arr.shape[-1] != 3:
        raise ValueError(f"Pixel array inesperado para RGB: {arr.shape}")
    if arr.dtype == np.uint8:
        return arr
    arrf = arr.astype(np.float32)
    mn, mx = float(arrf.min()), float(arrf.max())
    if mx <= mn:
        return np.zeros(arr.shape, dtype=np.uint8)
    arrf = (arrf - mn) * 255.0 / (mx - mn)
    return np.clip(arrf, 0, 255).astype(np.uint8)


def compute_hot_signal(rgb_arr: np.ndarray, strength: str = STRENGTH_NORMAL) -> np.ndarray:
    rgb = to_uint8_rgb(rgb_arr).astype(np.float32) / 255.0
    r = rgb[..., 0]
    g = rgb[..., 1]
    b = rgb[..., 2]
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    maxc = np.maximum.reduce([r, g, b])
    minc = np.minimum.reduce([r, g, b])
    sat = maxc - minc
    val = maxc
    warm = np.clip(r - b, 0, 1)
    yellow = np.clip((r + g) * 0.5 - b, 0, 1)
    red_dom = np.clip(r - (g * 0.6 + b * 0.4), 0, 1)
    hot = np.clip((0.35 * sat + 0.30 * warm + 0.20 * yellow + 0.15 * red_dom) * (0.45 + 0.55 * val), 0, 1)

    if strength == STRENGTH_SOFT:
        signal = 0.60 * luma + 0.40 * hot
        gamma = 1.00
    elif strength == STRENGTH_STRONG:
        signal = 0.20 * luma + 0.80 * hot
        gamma = 0.75
    else:
        signal = 0.35 * luma + 0.65 * hot
        gamma = 0.85

    p1 = float(np.percentile(signal, 1))
    p99 = float(np.percentile(signal, 99.5))
    if p99 > p1 + 1e-6:
        signal = (signal - p1) / (p99 - p1)
    signal = np.clip(signal, 0, 1)
    signal = np.power(signal, gamma)
    return np.clip(signal, 0, 1)


def boost_rgb_for_palette(rgb_arr: np.ndarray, strength: str) -> np.ndarray:
    rgb = to_uint8_rgb(rgb_arr).astype(np.float32) / 255.0
    gray = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])[..., None]
    sat_factor = {STRENGTH_SOFT: 0.90, STRENGTH_NORMAL: 1.10, STRENGTH_STRONG: 1.35}.get(strength, 1.10)
    out = gray + sat_factor * (rgb - gray)
    if strength == STRENGTH_STRONG:
        warm = np.clip(out[..., 0] - out[..., 2], 0, 1)
        out[..., 0] = np.clip(out[..., 0] + 0.12 * warm, 0, 1)
        out[..., 1] = np.clip(out[..., 1] + 0.05 * warm, 0, 1)
    return np.clip(np.round(out * 255), 0, 255).astype(np.uint8)


def rgb_to_palette_indices_and_lut(rgb_arr: np.ndarray, strength: str):
    rgb = boost_rgb_for_palette(rgb_arr, strength)
    img = Image.fromarray(rgb, mode="RGB")
    pal_img = img.quantize(colors=256, method=Image.FASTOCTREE, dither=Image.NONE)
    indices = np.array(pal_img, dtype=np.uint8)
    pal = pal_img.getpalette() or []
    pal = pal[:768] + [0] * max(0, 768 - len(pal[:768]))
    r8 = np.array(pal[0::3][:256], dtype=np.uint16)
    g8 = np.array(pal[1::3][:256], dtype=np.uint16)
    b8 = np.array(pal[2::3][:256], dtype=np.uint16)
    r16 = (r8 * 257).astype(np.uint16)
    g16 = (g8 * 257).astype(np.uint16)
    b16 = (b8 * 257).astype(np.uint16)
    return indices, r16.tobytes(), g16.tobytes(), b16.tobytes()


def clear_color_related_tags(ds):
    for tag_name in [
        "PlanarConfiguration", "ICCProfile",
        "RedPaletteColorLookupTableDescriptor", "GreenPaletteColorLookupTableDescriptor", "BluePaletteColorLookupTableDescriptor",
        "RedPaletteColorLookupTableData", "GreenPaletteColorLookupTableData", "BluePaletteColorLookupTableData",
        "SegmentedRedPaletteColorLookupTableData", "SegmentedGreenPaletteColorLookupTableData", "SegmentedBluePaletteColorLookupTableData",
        "WindowCenter", "WindowWidth", "RescaleIntercept", "RescaleSlope",
    ]:
        if tag_name in ds:
            del ds[tag_name]


def build_file_meta(ds, impl_version: str):
    file_meta = getattr(ds, "file_meta", None) or FileMetaDataset()
    sop_class = getattr(ds, "SOPClassUID", None) or SecondaryCaptureImageStorage
    sop_inst = getattr(ds, "SOPInstanceUID", None) or generate_uid()
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = sop_inst
    file_meta.MediaStorageSOPClassUID = sop_class
    file_meta.MediaStorageSOPInstanceUID = sop_inst
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.777.5"
    file_meta.ImplementationVersionName = impl_version[:16]
    ds.file_meta = file_meta
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def finalize_palette_dataset(ds, idx: np.ndarray, red: bytes, green: bytes, blue: bytes, detail: str):
    clear_color_related_tags(ds)
    ds.PixelData = idx.astype(np.uint8).tobytes()
    ds.Rows, ds.Columns = idx.shape
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "PALETTE COLOR"
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    if "PlanarConfiguration" in ds:
        del ds.PlanarConfiguration
    ds.add_new((0x0028, 0x1101), 'US', [256, 0, 16])
    ds.add_new((0x0028, 0x1102), 'US', [256, 0, 16])
    ds.add_new((0x0028, 0x1103), 'US', [256, 0, 16])
    ds.add_new((0x0028, 0x1201), 'OW', red)
    ds.add_new((0x0028, 0x1202), 'OW', green)
    ds.add_new((0x0028, 0x1203), 'OW', blue)
    ds.ImageComments = f"Varian color attempt - {detail}"[:1024]
    return build_file_meta(ds, "VARCOLV5PAL")


def method_palette_color(series_dirs: Dict[str, str], dst_root: str, rows: List[ReportRow], strength: str, log_q=None):
    out = os.path.join(dst_root, "METHOD2_OT_PALETTE_COLOR_TRY")
    ensure_dir(out)
    # copiar CT/PET base para acompañar la importación
    for key in ["S2", "S3", "S4"]:
        if key in series_dirs:
            copy_dicom_files(collect_series_files(series_dirs[key]), os.path.join(out, key), rows, METHOD_PALETTE)
    for key in ["S5", "S6", "S7"]:
        if key not in series_dirs:
            continue
        dst_series_dir = os.path.join(out, key)
        ensure_dir(dst_series_dir)
        for src in collect_series_files(series_dirs[key]):
            try:
                ds = read_dicom(src)
                arr = ds.pixel_array
                idx, red, green, blue = rgb_to_palette_indices_and_lut(arr, strength)
                work_ds = copy.deepcopy(ds)
                new_ds = finalize_palette_dataset(work_ds, idx, red, green, blue, f"{key} -> PALETTE COLOR ({strength})")
                dst = os.path.join(dst_series_dir, Path(src).name + ".dcm")
                pydicom.dcmwrite(dst, new_ds, write_like_original=False)
                add_report(rows, METHOD_PALETTE, "converted", src, dst, f"Converted {key} to PALETTE COLOR")
            except Exception as e:
                add_report(rows, METHOD_PALETTE, "error", src, os.path.join(dst_series_dir, Path(src).name + ".dcm"), str(e))
        log_emit(log_q, f"Convertido {key} a PALETTE COLOR")


# --------------------------
# Pseudo-PET desde OT
# --------------------------

def sort_by_instance(files: List[str]) -> List[str]:
    headers = []
    for p in files:
        try:
            ds = pydicom.dcmread(p, force=True, stop_before_pixels=True)
            inst = int(getattr(ds, 'InstanceNumber', 0) or 0)
            headers.append((inst, p))
        except Exception:
            headers.append((0, p))
    headers.sort(key=lambda x: (x[0], x[1]))
    return [p for _, p in headers]


def map_index(i: int, n_src: int, n_ref: int) -> int:
    if n_ref <= 1:
        return 0
    if n_src <= 1:
        return n_ref // 2
    return int(round(i * (n_ref - 1) / (n_src - 1)))


def build_pseudo_pet_dataset(ot_ds, signal01: np.ndarray, ref_ds, series_uid: str, study_uid: str, series_desc: str, series_number: int, image_type_label: str):
    ds = copy.deepcopy(ref_ds)
    if hasattr(ds, 'PixelData'):
        del ds.PixelData
    # limpieza básica de tags dependientes de CT
    for tag_name in [
        'RescaleType', 'KVP', 'XRayTubeCurrent', 'ExposureTime', 'Exposure', 'ConvolutionKernel',
        'WindowCenter', 'WindowWidth', 'RescaleIntercept', 'RescaleSlope', 'RescaleType',
        'SmallestImagePixelValue', 'LargestImagePixelValue', 'PixelPaddingValue', 'PixelPaddingRangeLimit',
    ]:
        if tag_name in ds:
            del ds[tag_name]

    # cuantización a 16-bit con rango 0..32767
    raw = np.clip(np.round(signal01 * 32767.0), 0, 32767).astype(np.uint16)
    ds.Modality = 'PT'
    ds.SOPClassUID = PositronEmissionTomographyImageStorage
    ds.SeriesInstanceUID = series_uid
    ds.StudyInstanceUID = study_uid
    ds.SOPInstanceUID = generate_uid()
    ds.SeriesDescription = series_desc[:64]
    ds.SeriesNumber = series_number
    ds.InstanceNumber = int(getattr(ref_ds, 'InstanceNumber', 1) or 1)
    ds.ImageType = ['DERIVED', 'SECONDARY', image_type_label]
    ds.PhotometricInterpretation = 'MONOCHROME2'
    ds.SamplesPerPixel = 1
    ds.Rows, ds.Columns = raw.shape
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelData = raw.tobytes()
    ds.RescaleIntercept = 0
    ds.RescaleSlope = 1
    ds.Units = 'BQML'
    ds.ImageComments = 'Pseudo-PET derived from RGB fusion screenshot. EXPERIMENTAL. Not for diagnosis/planning.'[:1024]
    if 'SliceThickness' not in ds and 'SpacingBetweenSlices' in ds:
        ds.SliceThickness = ds.SpacingBetweenSlices
    # Suplementarios PET amistosos
    if 'AcquisitionDate' in ot_ds:
        ds.AcquisitionDate = ot_ds.AcquisitionDate
    if 'AcquisitionTime' in ot_ds:
        ds.AcquisitionTime = ot_ds.AcquisitionTime
    return build_file_meta(ds, 'VARCOLV5PPET')


def create_pseudo_pet_from_ot(ot_files: List[str], ref_files: List[str], dst_dir: str, rows: List[ReportRow], series_desc: str, method_label: str, strength: str, log_q=None):
    ensure_dir(dst_dir)
    ot_files = sort_by_instance(ot_files)
    ref_files = sort_by_instance(ref_files)
    series_uid = generate_uid()
    first_ref = pydicom.dcmread(ref_files[0], force=True, stop_before_pixels=True)
    study_uid = getattr(first_ref, 'StudyInstanceUID', generate_uid())
    for i, src in enumerate(ot_files):
        try:
            ot_ds = read_dicom(src)
            rgb = ot_ds.pixel_array
            signal = compute_hot_signal(rgb, strength)
            j = map_index(i, len(ot_files), len(ref_files))
            ref_ds = read_dicom(ref_files[j])
            new_ds = build_pseudo_pet_dataset(
                ot_ds=ot_ds,
                signal01=signal,
                ref_ds=ref_ds,
                series_uid=series_uid,
                study_uid=study_uid,
                series_desc=series_desc,
                series_number=int(getattr(ref_ds, 'SeriesNumber', 500) or 500) + 500,
                image_type_label='PSEUDO PET'
            )
            dst = os.path.join(dst_dir, f"PPET_{i+1:04d}.dcm")
            pydicom.dcmwrite(dst, new_ds, write_like_original=False)
            add_report(rows, method_label, 'converted', src, dst, f'Pseudo-PET generated from OT slice {i+1}')
        except Exception as e:
            add_report(rows, method_label, 'error', src, os.path.join(dst_dir, f"PPET_{i+1:04d}.dcm"), str(e))
    log_emit(log_q, f"Generado pseudo-PET: {series_desc}")


# --------------------------
# RTSTRUCT experimental desde OT
# --------------------------

def hot_mask_from_rgb(rgb_arr: np.ndarray, strength: str = STRENGTH_NORMAL) -> np.ndarray:
    sig = compute_hot_signal(rgb_arr, strength)
    if strength == STRENGTH_SOFT:
        thr = 0.74
    elif strength == STRENGTH_STRONG:
        thr = 0.58
    else:
        thr = 0.66
    mask = (sig >= thr).astype(np.uint8)
    # filtro morfológico simple y vectorizado: mantener píxeles con >=4 vecinos activos en 3x3
    p = np.pad(mask, 1, mode='constant')
    neigh = (
        p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] +
        p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:] +
        p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]
    )
    return neigh >= 4


def connected_components_bboxes(mask: np.ndarray, min_area: int = 90) -> List[Tuple[int, int, int, int]]:
    h, w = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    boxes = []
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            pts = []
            while stack:
                cy, cx = stack.pop()
                pts.append((cy, cx))
                for ny, nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            if len(pts) >= min_area:
                ys = [p[0] for p in pts]
                xs = [p[1] for p in pts]
                y0, y1 = max(0, min(ys)-1), min(h-1, max(ys)+1)
                x0, x1 = max(0, min(xs)-1), min(w-1, max(xs)+1)
                boxes.append((x0, y0, x1, y1))
    return boxes


def pixel_to_patient(ds, x: float, y: float) -> List[float]:
    iop = [float(v) for v in ds.ImageOrientationPatient]
    ipp = [float(v) for v in ds.ImagePositionPatient]
    ps = [float(v) for v in ds.PixelSpacing]
    row = np.array(iop[:3], dtype=float)
    col = np.array(iop[3:6], dtype=float)
    origin = np.array(ipp, dtype=float)
    pt = origin + row * (y * ps[0]) + col * (x * ps[1])
    return [float(pt[0]), float(pt[1]), float(pt[2])]


def make_rtstruct_for_hotspots(ct_files: List[str], ot_files: List[str], dst_dir: str, rows: List[ReportRow], roi_name: str, strength: str, log_q=None):
    ensure_dir(dst_dir)
    ct_files = sort_by_instance(ct_files)
    ot_files = sort_by_instance(ot_files)
    # Copiar CT base al paquete
    copy_dicom_files(ct_files, os.path.join(dst_dir, 'CT_REF'), rows, METHOD_DERIVED)

    ct_hdrs = [pydicom.dcmread(p, force=True, stop_before_pixels=True) for p in ct_files]
    first_ct = ct_hdrs[0]
    study_uid = getattr(first_ct, 'StudyInstanceUID', generate_uid())
    series_uid = getattr(first_ct, 'SeriesInstanceUID', generate_uid())
    frame_uid = getattr(first_ct, 'FrameOfReferenceUID', generate_uid())
    patient_id = getattr(first_ct, 'PatientID', '')
    patient_name = getattr(first_ct, 'PatientName', '')

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = RTStructureSetStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.777.6"
    file_meta.ImplementationVersionName = "VARCOLV5RTS"

    date_str, time_str = now_date_time()
    ds = FileDataset("", {}, file_meta=file_meta, preamble=b"\0"*128)
    ds.SOPClassUID = RTStructureSetStorage
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.Modality = 'RTSTRUCT'
    ds.StructureSetLabel = 'HOTSPOT'
    ds.StructureSetName = roi_name[:16]
    ds.StructureSetDate = date_str
    ds.StructureSetTime = time_str
    ds.SeriesDescription = 'Experimental hotspot RTSTRUCT from OT'[:64]
    ds.SeriesInstanceUID = generate_uid()
    ds.StudyInstanceUID = study_uid
    ds.SeriesNumber = 990
    ds.InstanceNumber = 1
    ds.PatientID = patient_id
    ds.PatientName = patient_name
    ds.StudyDate = getattr(first_ct, 'StudyDate', date_str)
    ds.StudyTime = getattr(first_ct, 'StudyTime', time_str)
    ds.FrameOfReferenceUID = frame_uid
    ds.Manufacturer = 'OpenAI'
    ds.OperatorsName = 'ChatGPT'
    ds.SoftwareVersions = 'v5'

    # Referenced Frame of Reference
    ds.ReferencedFrameOfReferenceSequence = Sequence([Dataset()])
    rfor = ds.ReferencedFrameOfReferenceSequence[0]
    rfor.FrameOfReferenceUID = frame_uid
    rfor.RTReferencedStudySequence = Sequence([Dataset()])
    rstudy = rfor.RTReferencedStudySequence[0]
    # Muchos viewers aceptan los siguientes UID de referencia así
    rstudy.ReferencedSOPClassUID = '1.2.840.10008.3.1.2.3.1'  # Detached Study Management SOP Class (legacy style)
    rstudy.ReferencedSOPInstanceUID = study_uid
    rstudy.RTReferencedSeriesSequence = Sequence([Dataset()])
    rseries = rstudy.RTReferencedSeriesSequence[0]
    rseries.SeriesInstanceUID = series_uid
    rseries.ContourImageSequence = Sequence([])
    for ct in ct_hdrs:
        item = Dataset()
        item.ReferencedSOPClassUID = getattr(ct, 'SOPClassUID', CTImageStorage)
        item.ReferencedSOPInstanceUID = ct.SOPInstanceUID
        rseries.ContourImageSequence.append(item)

    # StructureSetROISequence
    ds.StructureSetROISequence = Sequence([Dataset()])
    roi = ds.StructureSetROISequence[0]
    roi.ROINumber = 1
    roi.ReferencedFrameOfReferenceUID = frame_uid
    roi.ROIName = roi_name[:64]
    roi.ROIGenerationAlgorithm = 'AUTOMATIC'

    # ROIContourSequence
    ds.ROIContourSequence = Sequence([Dataset()])
    roi_cont = ds.ROIContourSequence[0]
    roi_cont.ReferencedROINumber = 1
    roi_cont.ROIDisplayColor = [255, 0, 0]
    roi_cont.ContourSequence = Sequence([])

    # RTROIObservationsSequence
    ds.RTROIObservationsSequence = Sequence([Dataset()])
    obs = ds.RTROIObservationsSequence[0]
    obs.ObservationNumber = 1
    obs.ReferencedROINumber = 1
    obs.RTROIInterpretedType = 'CTV'
    obs.ROIInterpreter = 'AUTO'

    min_area = 60 if strength == STRENGTH_STRONG else 90
    total_contours = 0
    for i, ot_path in enumerate(ot_files):
        try:
            ot_ds = read_dicom(ot_path)
            rgb = ot_ds.pixel_array
            mask = hot_mask_from_rgb(rgb, strength)
            boxes = connected_components_bboxes(mask, min_area=min_area)
            if not boxes:
                continue
            j = map_index(i, len(ot_files), len(ct_hdrs))
            ct = ct_hdrs[j]
            for (x0, y0, x1, y1) in boxes:
                # polígono rectangular cerrado
                pts_xy = [(x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0)]
                data = []
                for x, y in pts_xy:
                    xyz = pixel_to_patient(ct, x, y)
                    data.extend([float(xyz[0]), float(xyz[1]), float(xyz[2])])
                c = Dataset()
                c.ContourGeometricType = 'CLOSED_PLANAR'
                c.NumberOfContourPoints = len(pts_xy)
                c.ContourData = data
                c.ContourImageSequence = Sequence([Dataset()])
                c.ContourImageSequence[0].ReferencedSOPClassUID = getattr(ct, 'SOPClassUID', CTImageStorage)
                c.ContourImageSequence[0].ReferencedSOPInstanceUID = ct.SOPInstanceUID
                roi_cont.ContourSequence.append(c)
                total_contours += 1
        except Exception:
            pass

    dst = os.path.join(dst_dir, 'RTSTRUCT_HOTSPOT.dcm')
    pydicom.dcmwrite(dst, ds, write_like_original=False)
    add_report(rows, METHOD_DERIVED, 'generated', 'OT->RTSTRUCT', dst, f'RTSTRUCT created with {total_contours} contours')
    log_emit(log_q, f"RTSTRUCT generado con {total_contours} contornos")


def method_derived(series_dirs: Dict[str, str], dst_root: str, rows: List[ReportRow], strength: str, log_q=None):
    # Pseudo-PET axial importante: S7 usando CT S4
    if 'S7' in series_dirs and 'S4' in series_dirs:
        out = os.path.join(dst_root, 'METHOD3_PSEUDOPET_AXIAL_S7_ON_S4')
        ensure_dir(out)
        ct_files = collect_series_files(series_dirs['S4'])
        copy_dicom_files(ct_files, os.path.join(out, 'S4_CT'), rows, METHOD_DERIVED)
        create_pseudo_pet_from_ot(
            ot_files=collect_series_files(series_dirs['S7']),
            ref_files=ct_files,
            dst_dir=os.path.join(out, 'S7_PSEUDOPET'),
            rows=rows,
            series_desc='PSEUDO PET FROM S7 FUSION AXIAL',
            method_label=METHOD_DERIVED,
            strength=strength,
            log_q=log_q,
        )
    else:
        log_emit(log_q, '[WARN] Falta S7 o S4 para pseudo-PET axial')

    # Pseudo-PET coronal: S5 usando S9 (si existe) o S4 como fallback
    if 'S5' in series_dirs and ('S9' in series_dirs or 'S4' in series_dirs):
        ref_key = 'S9' if 'S9' in series_dirs else 'S4'
        out = os.path.join(dst_root, f'METHOD3_PSEUDOPET_CORONAL_S5_ON_{ref_key}')
        ensure_dir(out)
        ref_files = collect_series_files(series_dirs[ref_key])
        copy_dicom_files(ref_files, os.path.join(out, f'{ref_key}_REF'), rows, METHOD_DERIVED)
        create_pseudo_pet_from_ot(
            ot_files=collect_series_files(series_dirs['S5']),
            ref_files=ref_files,
            dst_dir=os.path.join(out, 'S5_PSEUDOPET'),
            rows=rows,
            series_desc='PSEUDO PET FROM S5 FUSION CORONAL',
            method_label=METHOD_DERIVED,
            strength=strength,
            log_q=log_q,
        )
    else:
        log_emit(log_q, '[WARN] Falta S5 y/o referencia CT para pseudo-PET coronal')

    # RTSTRUCT hotspot axial sobre S4
    if 'S7' in series_dirs and 'S4' in series_dirs:
        out = os.path.join(dst_root, 'METHOD3_RTSTRUCT_HOTSPOT_S7_ON_S4')
        ensure_dir(out)
        make_rtstruct_for_hotspots(
            ct_files=collect_series_files(series_dirs['S4']),
            ot_files=collect_series_files(series_dirs['S7']),
            dst_dir=out,
            rows=rows,
            roi_name='HOTSPOT_S7',
            strength=strength,
            log_q=log_q,
        )
    else:
        log_emit(log_q, '[WARN] Falta S7 o S4 para RTSTRUCT axial')


def write_quickstart(dst_root: str):
    quick = os.path.join(dst_root, '00_IMPORTAR_PRIMERO.txt')
    with open(quick, 'w', encoding='utf-8') as f:
        f.write('ORDEN RECOMENDADO DE PRUEBA EN ECLIPSE\n')
        f.write('==================================\n\n')
        f.write('1) Importa primero la carpeta: METHOD1_NATIVE_PETCT_S4_CTCE\n')
        f.write('   - Esta usa S3 (PET real) + S4 (CT con contraste).\n')
        f.write('   - Es la mejor opción para ver color PET en Varian.\n\n')
        f.write('2) Si no te sirve o no ves color, importa: METHOD3_PSEUDOPET_AXIAL_S7_ON_S4\n')
        f.write('   - Esta está pensada para FUSION AXIAL CEREBRO (S7).\n\n')
        f.write('3) Si quieres ver el hotspot como estructura en color, importa: METHOD3_RTSTRUCT_HOTSPOT_S7_ON_S4\n\n')
        f.write('4) METHOD2_OT_PALETTE_COLOR_TRY es experimental y puede fallar; no lo uses como primera prueba.\n')
        f.write('\nIMPORTANTE: si usaste all_safe, NO incluye METHOD2. Si quieres también METHOD2, usa all_plus_palette.\n')



# --------------------------
# Pipeline general
# --------------------------

def process(source_root: str, dst_root: str, mode: str, strength: str, log_q=None):
    ensure_dir(dst_root)
    rows: List[ReportRow] = []

    found = discover_series_sources(source_root)
    if not found:
        raise RuntimeError('No se encontraron series S1..S9 (zip o carpetas) en la carpeta origen.')

    workspace = tempfile.mkdtemp(prefix='varcolv5_')
    log_emit(log_q, f"Workspace temporal: {workspace}")
    try:
        series_dirs: Dict[str, str] = {}
        need = set()
        if mode in (METHOD_NATIVE, METHOD_ALL, METHOD_ALL_PLUS):
            need.update(['S2', 'S3', 'S4'])
        if mode in (METHOD_PALETTE, METHOD_ALL_PLUS):
            need.update(['S2', 'S3', 'S4', 'S5', 'S6', 'S7'])
        if mode in (METHOD_DERIVED, METHOD_ALL, METHOD_ALL_PLUS):
            need.update(['S4', 'S5', 'S7', 'S9'])
        for key in sorted(need):
            if key in found:
                series_dirs[key] = materialize_series(key, found[key], workspace, log_q)
                log_emit(log_q, f"{key}: listo")
            else:
                log_emit(log_q, f"[WARN] {key} no encontrado")

        if mode in (METHOD_NATIVE, METHOD_ALL, METHOD_ALL_PLUS):
            log_emit(log_q, 'Ejecutando método 1: PET real + CT...')
            method_native_pet_ct(series_dirs, dst_root, rows, log_q)
        if mode in (METHOD_DERIVED, METHOD_ALL, METHOD_ALL_PLUS):
            log_emit(log_q, 'Ejecutando método 3: pseudo-PET + RTSTRUCT...')
            method_derived(series_dirs, dst_root, rows, strength, log_q)
        if mode in (METHOD_PALETTE, METHOD_ALL_PLUS):
            log_emit(log_q, 'Ejecutando método 2: OT palette color...')
            method_palette_color(series_dirs, dst_root, rows, strength, log_q)

        write_quickstart(dst_root)
        csv_path, txt_path = write_csv_txt_report(dst_root, rows)
        log_emit(log_q, f"Reporte CSV: {csv_path}")
        log_emit(log_q, f"Reporte TXT: {txt_path}")
        return rows, csv_path, txt_path
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        log_emit(log_q, 'Workspace temporal eliminado.')


# --------------------------
# GUI
# --------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry('980x740')
        self.log_q = queue.Queue()
        self.worker = None

        self.src_var = tk.StringVar()
        self.dst_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=METHOD_ALL)
        self.strength_var = tk.StringVar(value=STRENGTH_NORMAL)

        self._build()
        self.root.after(150, self._poll)

    def _build(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill='both', expand=True)

        ttk.Label(frm, text='Carpeta origen que contiene directamente S1..S9:').grid(row=0, column=0, sticky='w')
        ttk.Entry(frm, textvariable=self.src_var, width=90).grid(row=1, column=0, sticky='ew', padx=(0,6))
        ttk.Button(frm, text='Examinar', command=self._pick_src).grid(row=1, column=1, sticky='ew')

        ttk.Label(frm, text='Carpeta destino:').grid(row=2, column=0, sticky='w', pady=(10,0))
        ttk.Entry(frm, textvariable=self.dst_var, width=90).grid(row=3, column=0, sticky='ew', padx=(0,6))
        ttk.Button(frm, text='Examinar', command=self._pick_dst).grid(row=3, column=1, sticky='ew')

        ttk.Label(frm, text='Método:').grid(row=4, column=0, sticky='w', pady=(10,0))
        ttk.Combobox(frm, textvariable=self.mode_var, state='readonly', values=[METHOD_NATIVE, METHOD_DERIVED, METHOD_ALL, METHOD_ALL_PLUS, METHOD_PALETTE], width=36).grid(row=5, column=0, sticky='w')

        ttk.Label(frm, text='Fuerza hotspot / color:').grid(row=4, column=1, sticky='w', pady=(10,0))
        ttk.Combobox(frm, textvariable=self.strength_var, state='readonly', values=[STRENGTH_SOFT, STRENGTH_NORMAL, STRENGTH_STRONG], width=18).grid(row=5, column=1, sticky='w')

        help_txt = (
            'Métodos disponibles:\n'
            f'1) {METHOD_NATIVE}: genera paquetes S3+S4 y S3+S2. Es la opción con más probabilidad real de ver color en Varian.\n'
            f'2) {METHOD_PALETTE}: convierte S5/S6/S7 a PALETTE COLOR y copia CT/PET base.\n'
            f'3) {METHOD_DERIVED}: crea pseudo-PET desde OT y un RTSTRUCT experimental del hotspot.\n'
            f'4) {METHOD_ALL}: genera solo lo recomendado y seguro (métodos 1 + 3).\n            5) {METHOD_ALL_PLUS}: genera todo, incluyendo palette color experimental.\n\n'
            'Recomendación práctica: probar primero METHOD1_NATIVE_PETCT_S4_CTCE, luego METHOD3_PSEUDOPET_AXIAL_S7_ON_S4. No empezar por METHOD2.\n'
        )
        ttk.Label(frm, text=help_txt, justify='left').grid(row=6, column=0, columnspan=2, sticky='w', pady=(10,10))

        bar = ttk.Frame(frm)
        bar.grid(row=7, column=0, columnspan=2, sticky='ew')
        ttk.Button(bar, text='Procesar', command=self._start).pack(side='left')
        ttk.Button(bar, text='Cerrar', command=self.root.destroy).pack(side='left', padx=(8,0))

        self.log = ScrolledText(frm, height=28)
        self.log.grid(row=8, column=0, columnspan=2, sticky='nsew')
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(8, weight=1)

    def _pick_src(self):
        p = filedialog.askdirectory(title='Selecciona carpeta origen')
        if p:
            self.src_var.set(p)

    def _pick_dst(self):
        p = filedialog.askdirectory(title='Selecciona carpeta destino')
        if p:
            self.dst_var.set(p)

    def _append(self, msg):
        self.log.insert('end', msg + '\n')
        self.log.see('end')

    def _poll(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                if msg == '__DONE__':
                    messagebox.showinfo(APP_NAME, 'Proceso terminado.')
                else:
                    self._append(msg)
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _start(self):
        src = self.src_var.get().strip()
        dst = self.dst_var.get().strip()
        mode = self.mode_var.get().strip()
        strength = self.strength_var.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror(APP_NAME, 'Selecciona una carpeta origen válida.')
            return
        if not dst:
            messagebox.showerror(APP_NAME, 'Selecciona una carpeta destino válida.')
            return
        ensure_dir(dst)

        def run():
            try:
                process(src, dst, mode, strength, self.log_q)
            except Exception as e:
                self.log_q.put(f'[ERROR FATAL] {e}')
            finally:
                self.log_q.put('__DONE__')

        self._append('Iniciando...')
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()


# --------------------------
# CLI
# --------------------------

def parse_args():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument('--source', help='Carpeta origen con S1..S9')
    ap.add_argument('--dest', help='Carpeta destino')
    ap.add_argument('--mode', choices=[METHOD_NATIVE, METHOD_DERIVED, METHOD_ALL, METHOD_ALL_PLUS, METHOD_PALETTE], default=METHOD_ALL)
    ap.add_argument('--strength', choices=[STRENGTH_SOFT, STRENGTH_NORMAL, STRENGTH_STRONG], default=STRENGTH_NORMAL)
    ap.add_argument('--nogui', action='store_true', help='Ejecutar sin GUI')
    return ap.parse_args()


def main():
    args = parse_args()
    if args.nogui or tk is None:
        if not args.source or not args.dest:
            print('Modo CLI: debes indicar --source y --dest')
            sys.exit(2)
        class Q:
            def put(self, msg):
                print(msg)
        process(args.source, args.dest, args.mode, args.strength, Q())
        print('Terminado.')
    else:
        root = tk.Tk()
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        App(root)
        root.mainloop()


if __name__ == '__main__':
    main()