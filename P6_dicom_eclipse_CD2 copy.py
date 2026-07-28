#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
brain_fusion_trial_gui.py

Objetivo
--------
Generar series "juntas" a partir de:
- VOL BRAIN 1.25 CTE + MAC BRAIN QC 350  (S4 + S3)
- VOL BRAIN SIN CTE 1.25 + MAC BRAIN QC 350 (S2 + S3)

Pensado para cuando Eclipse abre CT y PET por separado y no entrega una opción clara de fusión.

Salidas generadas por cada par:
1) FUSED_MONO_SAFE_CT
   - Serie DICOM tipo CT derivada, MONOCHROME2, con PET "quemado" como realce brillante.
   - Es la opción con mayor probabilidad de abrir en Eclipse, pero NO conserva color.

2) FUSED_RGB_TRY_SC
   - Serie DICOM Secondary Capture RGB con la fusión ya renderizada en color.
   - Es experimental: puede fallar en Eclipse.

3) PET_RESAMPLED_ON_CTGRID_PT
   - Serie PT resampleada a la geometría de la CT.
   - Puede ayudar si Eclipse sí colorea PET pero necesita mejor alineación / grid.

Notas
-----
- No modifica originales.
- Flujo principal con carpetas S1..S9 (no ZIP como requisito).
- Selecciona como carpeta origen la que contiene directamente S1, S2, S3... S9.
"""

import os
import sys
import copy
import math
import shutil
import argparse
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple, Dict, Optional

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter.scrolledtext import ScrolledText
except Exception:
    tk = None
    ttk = None

try:
    import numpy as np
    import pydicom
    from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid, CTImageStorage, SecondaryCaptureImageStorage, PositronEmissionTomographyImageStorage
except Exception as e:
    print("Faltan dependencias. Instala con: python3 -m pip install pydicom numpy")
    print("Detalle:", e)
    sys.exit(1)

APP_NAME = "Brain Fusion Trial GUI"
PAIR_S4 = "S4 + S3 (VOL BRAIN 1.25 CTE + MAC BRAIN QC 350)"
PAIR_S2 = "S2 + S3 (VOL BRAIN SIN CTE 1.25 + MAC BRAIN QC 350)"
PAIR_BOTH = "Ambos pares"

STRENGTHS = {
    "soft": {"pet_pct": 97.5, "alpha": 0.35, "mono_gain": 0.35, "thr": 0.20},
    "normal": {"pet_pct": 96.5, "alpha": 0.50, "mono_gain": 0.55, "thr": 0.15},
    "strong": {"pet_pct": 95.0, "alpha": 0.70, "mono_gain": 0.85, "thr": 0.10},
}


@dataclass
class SeriesInfo:
    key: str
    description: str
    modality: str
    files: List[str]


def ensure_dir(path: str):
    Path(path).mkdir(parents=True, exist_ok=True)


def list_files_recursive(root: str) -> List[str]:
    out = []
    for r, _, fns in os.walk(root):
        for fn in fns:
            out.append(os.path.join(r, fn))
    return sorted(out)


def try_dcm_header(path: str):
    try:
        return pydicom.dcmread(path, force=True, stop_before_pixels=True)
    except Exception:
        return None


def find_series_dirs(source_root: str) -> Dict[str, str]:
    out = {}
    for name in sorted(os.listdir(source_root)):
        p = os.path.join(source_root, name)
        if not os.path.isdir(p):
            continue
        key = name.upper()
        if key in {"S2", "S3", "S4"}:
            out[key] = p
    return out


def get_series_info(series_key: str, series_dir: str) -> SeriesInfo:
    files = []
    desc = ""
    modality = ""
    for p in list_files_recursive(series_dir):
        ds = try_dcm_header(p)
        if ds is None:
            continue
        files.append(p)
        if not desc:
            desc = str(getattr(ds, "SeriesDescription", ""))
        if not modality:
            modality = str(getattr(ds, "Modality", ""))
    def sort_key(path):
        ds = try_dcm_header(path)
        inst = int(getattr(ds, "InstanceNumber", 0) or 0)
        ipp = getattr(ds, "ImagePositionPatient", None)
        z = float(ipp[2]) if ipp is not None and len(ipp) >= 3 else 0.0
        return (inst, z, path)
    files = sorted(files, key=sort_key)
    return SeriesInfo(series_key, desc, modality, files)


def load_slices(files: List[str]):
    lst = []
    for p in files:
        ds = pydicom.dcmread(p, force=True)
        ipp = getattr(ds, "ImagePositionPatient", None)
        z = float(ipp[2]) if ipp is not None and len(ipp) >= 3 else float(getattr(ds, "InstanceNumber", 0) or 0)
        lst.append((z, ds))
    lst.sort(key=lambda x: x[0], reverse=False)
    return [ds for _, ds in lst]


def ct_window_to_u16(ct_ds, center=40.0, width=400.0) -> np.ndarray:
    arr = ct_ds.pixel_array.astype(np.float32)
    slope = float(getattr(ct_ds, "RescaleSlope", 1) or 1)
    intercept = float(getattr(ct_ds, "RescaleIntercept", 0) or 0)
    hu = arr * slope + intercept
    lo = center - width / 2.0
    hi = center + width / 2.0
    x = (hu - lo) / max(1.0, (hi - lo))
    x = np.clip(x, 0, 1)
    return np.round(x * 4095.0).astype(np.uint16)


def normalize_pet(pet_ds, pet_percentile=96.5) -> np.ndarray:
    arr = pet_ds.pixel_array.astype(np.float32)
    slope = float(getattr(pet_ds, "RescaleSlope", 1) or 1)
    intercept = float(getattr(pet_ds, "RescaleIntercept", 0) or 0)
    val = arr * slope + intercept
    val = np.maximum(val, 0)
    nz = val[val > 0]
    if nz.size == 0:
        return np.zeros_like(val, dtype=np.float32)
    hi = float(np.percentile(nz, pet_percentile))
    if hi <= 0:
        hi = float(nz.max()) if nz.size else 1.0
    x = val / max(hi, 1e-6)
    x = np.clip(x, 0, 1)
    # realce un poco no lineal para hacer más visible el hotspot
    return np.power(x, 0.85).astype(np.float32)


def match_pet_slice(ct_ds, pet_slices: List[pydicom.dataset.FileDataset]):
    ct_z = float(ct_ds.ImagePositionPatient[2])
    best = None
    best_d = None
    for pet_ds in pet_slices:
        pet_z = float(pet_ds.ImagePositionPatient[2])
        d = abs(ct_z - pet_z)
        if best is None or d < best_d:
            best = pet_ds
            best_d = d
    return best


def resample_pet_to_ct_grid(ct_ds, pet_ds, pet_norm: np.ndarray) -> np.ndarray:
    rows = int(ct_ds.Rows)
    cols = int(ct_ds.Columns)

    ct_ipp = [float(x) for x in ct_ds.ImagePositionPatient]
    ct_ps = [float(x) for x in ct_ds.PixelSpacing]
    pet_ipp = [float(x) for x in pet_ds.ImagePositionPatient]
    pet_ps = [float(x) for x in pet_ds.PixelSpacing]

    pet_rows, pet_cols = pet_norm.shape

    r_idx = (ct_ipp[1] + np.arange(rows, dtype=np.float32) * ct_ps[0] - pet_ipp[1]) / pet_ps[0]
    c_idx = (ct_ipp[0] + np.arange(cols, dtype=np.float32) * ct_ps[1] - pet_ipp[0]) / pet_ps[1]
    rr, cc = np.meshgrid(r_idx, c_idx, indexing='ij')

    valid = (rr >= 0) & (rr <= pet_rows - 1) & (cc >= 0) & (cc <= pet_cols - 1)
    rr = np.clip(rr, 0, pet_rows - 1)
    cc = np.clip(cc, 0, pet_cols - 1)

    r0 = np.floor(rr).astype(np.int32)
    c0 = np.floor(cc).astype(np.int32)
    r1 = np.minimum(r0 + 1, pet_rows - 1)
    c1 = np.minimum(c0 + 1, pet_cols - 1)
    fr = rr - r0
    fc = cc - c0

    out = (
        pet_norm[r0, c0] * (1 - fr) * (1 - fc)
        + pet_norm[r0, c1] * (1 - fr) * fc
        + pet_norm[r1, c0] * fr * (1 - fc)
        + pet_norm[r1, c1] * fr * fc
    ).astype(np.float32)
    out[~valid] = 0.0
    return np.clip(out, 0, 1)


def pet_to_rgb_overlay(pet_on_ct: np.ndarray, thr: float, alpha: float) -> np.ndarray:
    x = np.clip((pet_on_ct - thr) / max(1e-6, (1.0 - thr)), 0, 1)
    a = np.clip(x * alpha, 0, 1)

    # mapa azul->cian->verde->amarillo->rojo
    r = np.clip(1.5 * x - 0.5, 0, 1)
    g = np.clip(1.5 - 2.0 * np.abs(x - 0.5), 0, 1)
    b = np.clip(1.2 - 1.7 * x, 0, 1)
    rgb = np.stack([r, g, b], axis=-1)
    return rgb.astype(np.float32), a.astype(np.float32)


def blend_ct_pet_rgb(ct_u16: np.ndarray, pet_on_ct: np.ndarray, strength: str) -> np.ndarray:
    params = STRENGTHS[strength]
    ct = np.clip(ct_u16.astype(np.float32) / 4095.0, 0, 1)
    ct_rgb = np.stack([ct, ct, ct], axis=-1)
    pet_rgb, alpha = pet_to_rgb_overlay(pet_on_ct, params["thr"], params["alpha"])
    rgb = ct_rgb * (1 - alpha[..., None]) + pet_rgb * alpha[..., None]
    rgb = np.clip(rgb, 0, 1)
    return np.round(rgb * 255.0).astype(np.uint8)


def fuse_mono_safe(ct_u16: np.ndarray, pet_on_ct: np.ndarray, strength: str) -> np.ndarray:
    params = STRENGTHS[strength]
    ct = np.clip(ct_u16.astype(np.float32) / 4095.0, 0, 1)
    # hotspot brillante sobre la CT
    mono = ct + params["mono_gain"] * np.power(np.clip(pet_on_ct, 0, 1), 0.75)
    mono = np.clip(mono, 0, 1)
    return np.round(mono * 4095.0).astype(np.uint16)


def build_file_meta(sop_class_uid, sop_instance_uid, impl_version: str):
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = sop_class_uid
    fm.MediaStorageSOPInstanceUID = sop_instance_uid
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.777.99"
    fm.ImplementationVersionName = impl_version[:16]
    return fm


def make_derived_ct_from_ct(ct_ds, pixel_u16: np.ndarray, series_desc: str, series_uid: str, instance_number: int):
    ds = copy.deepcopy(ct_ds)
    ds.file_meta = build_file_meta(CTImageStorage, generate_uid(), "BRAINFUSCTV1")
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.SeriesInstanceUID = series_uid
    ds.SeriesDescription = series_desc[:64]
    ds.ImageType = ['DERIVED', 'SECONDARY', 'AXIAL']
    ds.SeriesNumber = int(getattr(ct_ds, 'SeriesNumber', 100) or 100) + 800
    ds.InstanceNumber = instance_number
    ds.Modality = 'CT'
    ds.PhotometricInterpretation = 'MONOCHROME2'
    ds.SamplesPerPixel = 1
    ds.Rows, ds.Columns = pixel_u16.shape
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.RescaleSlope = 1
    ds.RescaleIntercept = -1024
    ds.PixelData = pixel_u16.tobytes()
    ds['PixelData'].VR = 'OW'
    ds.ImageComments = 'Derived fusion CT with PET highlight burned in (safe monochrome).'
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def make_rgb_sc_from_ct(ct_ds, pixel_rgb: np.ndarray, series_desc: str, series_uid: str, instance_number: int):
    fm_uid = generate_uid()
    ds = FileDataset(None, {}, file_meta=build_file_meta(SecondaryCaptureImageStorage, fm_uid, "BRAINFUSRGB1"), preamble=b"\0" * 128)
    # copiar datos clave del estudio/paciente
    for tag in [
        'PatientName', 'PatientID', 'PatientBirthDate', 'PatientSex', 'StudyInstanceUID', 'StudyID', 'AccessionNumber',
        'StudyDate', 'StudyTime', 'ReferringPhysicianName', 'FrameOfReferenceUID'
    ]:
        if tag in ct_ds:
            ds[tag] = copy.deepcopy(ct_ds[tag])
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = fm_uid
    ds.SeriesInstanceUID = series_uid
    ds.SeriesDescription = series_desc[:64]
    ds.SeriesNumber = int(getattr(ct_ds, 'SeriesNumber', 100) or 100) + 900
    ds.InstanceNumber = instance_number
    ds.Modality = 'OT'
    ds.ImageType = ['DERIVED', 'SECONDARY', 'FUSED']
    ds.Rows, ds.Columns, _ = pixel_rgb.shape
    ds.SamplesPerPixel = 3
    ds.PhotometricInterpretation = 'RGB'
    ds.PlanarConfiguration = 0
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PatientOrientation = ''
    if 'ImagePositionPatient' in ct_ds:
        ds.ImagePositionPatient = copy.deepcopy(ct_ds.ImagePositionPatient)
    if 'ImageOrientationPatient' in ct_ds:
        ds.ImageOrientationPatient = copy.deepcopy(ct_ds.ImageOrientationPatient)
    if 'PixelSpacing' in ct_ds:
        ds.PixelSpacing = copy.deepcopy(ct_ds.PixelSpacing)
    if 'SliceThickness' in ct_ds:
        ds.SliceThickness = copy.deepcopy(ct_ds.SliceThickness)
    ds.PixelData = pixel_rgb.tobytes()
    ds['PixelData'].VR = 'OB'
    ds.ImageComments = 'Experimental fused RGB secondary capture (CT+PET).'
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def make_resampled_pet_on_ctgrid(ct_ds, pet_on_ct: np.ndarray, src_pet_ds, series_desc: str, series_uid: str, instance_number: int):
    ds = copy.deepcopy(ct_ds)
    if 'PixelData' in ds:
        del ds.PixelData
    for tag in ['KVP', 'XRayTubeCurrent', 'ExposureTime', 'Exposure', 'ConvolutionKernel']:
        if tag in ds:
            del ds[tag]
    ds.file_meta = build_file_meta(PositronEmissionTomographyImageStorage, generate_uid(), "BRAINFUSPT1")
    ds.SOPClassUID = PositronEmissionTomographyImageStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.SeriesInstanceUID = series_uid
    ds.SeriesDescription = series_desc[:64]
    ds.ImageType = ['DERIVED', 'SECONDARY']
    ds.SeriesNumber = int(getattr(ct_ds, 'SeriesNumber', 100) or 100) + 700
    ds.InstanceNumber = instance_number
    ds.Modality = 'PT'
    ds.PhotometricInterpretation = 'MONOCHROME2'
    ds.SamplesPerPixel = 1
    ds.Rows, ds.Columns = pet_on_ct.shape
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.RescaleIntercept = 0
    ds.RescaleSlope = 1
    ds.Units = getattr(src_pet_ds, 'Units', 'BQML')
    ds.PixelData = np.round(np.clip(pet_on_ct, 0, 1) * 32767.0).astype(np.uint16).tobytes()
    ds['PixelData'].VR = 'OW'
    ds.ImageComments = 'PET resampled to CT grid.'
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def save_series(ds_list: List[pydicom.dataset.FileDataset], dst_dir: str, prefix: str):
    ensure_dir(dst_dir)
    for i, ds in enumerate(ds_list, start=1):
        out = os.path.join(dst_dir, f"{prefix}_{i:04d}.dcm")
        pydicom.dcmwrite(out, ds, write_like_original=False)


def generate_for_pair(ct_info: SeriesInfo, pet_info: SeriesInfo, out_root: str, strength: str, log=None):
    pair_name = f"{ct_info.key}_{pet_info.key}__{ct_info.description}__{pet_info.description}"
    pair_name = pair_name.replace('/', '-').replace('\\', '-').replace(':', '-')
    pair_dir = os.path.join(out_root, pair_name)
    ensure_dir(pair_dir)

    ct_slices = load_slices(ct_info.files)
    pet_slices = load_slices(pet_info.files)

    if log: log(f"Procesando {pair_name}...")

    # pre-normalizar PET por slice
    pet_norm_cache = {}
    params = STRENGTHS[strength]
    for pet_ds in pet_slices:
        pet_norm_cache[id(pet_ds)] = normalize_pet(pet_ds, params['pet_pct'])

    mono_list = []
    rgb_list = []
    pt_list = []

    mono_uid = generate_uid()
    rgb_uid = generate_uid()
    pt_uid = generate_uid()

    for idx, ct_ds in enumerate(ct_slices, start=1):
        pet_ds = match_pet_slice(ct_ds, pet_slices)
        pet_norm = pet_norm_cache[id(pet_ds)]
        pet_on_ct = resample_pet_to_ct_grid(ct_ds, pet_ds, pet_norm)
        ct_u16 = ct_window_to_u16(ct_ds)

        mono = fuse_mono_safe(ct_u16, pet_on_ct, strength)
        rgb = blend_ct_pet_rgb(ct_u16, pet_on_ct, strength)

        mono_ds = make_derived_ct_from_ct(ct_ds, mono, f"FUSED MONO SAFE {ct_info.description} + {pet_info.description}", mono_uid, idx)
        rgb_ds = make_rgb_sc_from_ct(ct_ds, rgb, f"FUSED RGB TRY {ct_info.description} + {pet_info.description}", rgb_uid, idx)
        pt_ds = make_resampled_pet_on_ctgrid(ct_ds, pet_on_ct, pet_ds, f"PET ON CT GRID {ct_info.description} + {pet_info.description}", pt_uid, idx)

        mono_list.append(mono_ds)
        rgb_list.append(rgb_ds)
        pt_list.append(pt_ds)

    save_series(mono_list, os.path.join(pair_dir, "FUSED_MONO_SAFE_CT"), "FMONO")
    save_series(rgb_list, os.path.join(pair_dir, "FUSED_RGB_TRY_SC"), "FRGB")
    save_series(pt_list, os.path.join(pair_dir, "PET_RESAMPLED_ON_CTGRID_PT"), "FPT")

    # También copiamos originales del par para referencia
    orig_dir = os.path.join(pair_dir, "ORIGINALES_REFERENCIA")
    ensure_dir(orig_dir)
    ct_copy = os.path.join(orig_dir, ct_info.key)
    pet_copy = os.path.join(orig_dir, pet_info.key)
    ensure_dir(ct_copy)
    ensure_dir(pet_copy)
    for src in ct_info.files:
        shutil.copy2(src, os.path.join(ct_copy, Path(src).name + ('' if Path(src).suffix else '.dcm')))
    for src in pet_info.files:
        shutil.copy2(src, os.path.join(pet_copy, Path(src).name + ('' if Path(src).suffix else '.dcm')))

    with open(os.path.join(pair_dir, "00_LEER_PRIMERO.txt"), "w", encoding="utf-8") as f:
        f.write("Resultados generados para probar las imágenes 'juntas'.\n\n")
        f.write("Orden sugerido en Eclipse:\n")
        f.write("1) Probar FUSED_MONO_SAFE_CT  -> mayor probabilidad de abrir.\n")
        f.write("2) Probar PET_RESAMPLED_ON_CTGRID_PT -> si Eclipse colorea PT.\n")
        f.write("3) Probar FUSED_RGB_TRY_SC -> experimental, puede fallar.\n\n")
        f.write("ORIGINALES_REFERENCIA contiene los originales del par por si quieres comparar.\n")

    if log: log(f"Listo: {pair_dir}")
    return pair_dir


def process(source_root: str, out_root: str, pair_mode: str, strength: str, log=None):
    ensure_dir(out_root)
    found = find_series_dirs(source_root)
    missing = [k for k in ['S2', 'S3', 'S4'] if k not in found]
    if missing:
        raise RuntimeError(f"Faltan carpetas requeridas: {', '.join(missing)}")

    s2 = get_series_info('S2', found['S2'])
    s3 = get_series_info('S3', found['S3'])
    s4 = get_series_info('S4', found['S4'])

    created = []
    if pair_mode in (PAIR_S4, PAIR_BOTH):
        created.append(generate_for_pair(s4, s3, out_root, strength, log))
    if pair_mode in (PAIR_S2, PAIR_BOTH):
        created.append(generate_for_pair(s2, s3, out_root, strength, log))

    with open(os.path.join(out_root, "00_RESUMEN.txt"), "w", encoding="utf-8") as f:
        f.write("Resumen de salidas creadas\n")
        f.write("=========================\n\n")
        for p in created:
            f.write(p + "\n")
        f.write("\nRecomendación general:\n")
        f.write("- Si quieres la mayor probabilidad de importación en Eclipse, empieza por FUSED_MONO_SAFE_CT.\n")
        f.write("- Si quieres intentar color, luego prueba PET_RESAMPLED_ON_CTGRID_PT.\n")
        f.write("- FUSED_RGB_TRY_SC es el intento más agresivo y puede no abrir.\n")


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("980x720")
        self.src_var = tk.StringVar()
        self.dst_var = tk.StringVar()
        self.pair_var = tk.StringVar(value=PAIR_BOTH)
        self.strength_var = tk.StringVar(value="normal")
        self._build()

    def _build(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill='both', expand=True)

        ttk.Label(frm, text="Carpeta origen (contiene S1..S9):").grid(row=0, column=0, sticky='w')
        ttk.Entry(frm, textvariable=self.src_var, width=90).grid(row=1, column=0, sticky='ew', padx=(0, 6))
        ttk.Button(frm, text="Examinar", command=self.pick_src).grid(row=1, column=1, sticky='ew')

        ttk.Label(frm, text="Carpeta destino:").grid(row=2, column=0, sticky='w', pady=(8,0))
        ttk.Entry(frm, textvariable=self.dst_var, width=90).grid(row=3, column=0, sticky='ew', padx=(0, 6))
        ttk.Button(frm, text="Examinar", command=self.pick_dst).grid(row=3, column=1, sticky='ew')

        ttk.Label(frm, text="Par a generar:").grid(row=4, column=0, sticky='w', pady=(8,0))
        ttk.Combobox(frm, textvariable=self.pair_var, state='readonly', values=[PAIR_S4, PAIR_S2, PAIR_BOTH], width=60).grid(row=5, column=0, sticky='w')

        ttk.Label(frm, text="Fuerza de realce:").grid(row=4, column=1, sticky='w', pady=(8,0))
        ttk.Combobox(frm, textvariable=self.strength_var, state='readonly', values=list(STRENGTHS.keys()), width=12).grid(row=5, column=1, sticky='w')

        help_text = (
            "Este script arma series 'juntas' para probar en Eclipse.\n"
            "No fusiona dentro de Eclipse: genera nuevas series DICOM derivadas.\n\n"
            "Orden sugerido luego en Eclipse:\n"
            "1) FUSED_MONO_SAFE_CT\n"
            "2) PET_RESAMPLED_ON_CTGRID_PT\n"
            "3) FUSED_RGB_TRY_SC\n"
        )
        ttk.Label(frm, text=help_text, justify='left').grid(row=6, column=0, columnspan=2, sticky='w', pady=(10,10))

        btnbar = ttk.Frame(frm)
        btnbar.grid(row=7, column=0, columnspan=2, sticky='w')
        ttk.Button(btnbar, text="Procesar", command=self.run).pack(side='left')
        ttk.Button(btnbar, text="Cerrar", command=self.root.destroy).pack(side='left', padx=(8,0))

        self.log = ScrolledText(frm, height=26)
        self.log.grid(row=8, column=0, columnspan=2, sticky='nsew')
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(8, weight=1)

    def pick_src(self):
        p = filedialog.askdirectory(title="Selecciona carpeta origen")
        if p:
            self.src_var.set(p)

    def pick_dst(self):
        p = filedialog.askdirectory(title="Selecciona carpeta destino")
        if p:
            self.dst_var.set(p)

    def append(self, msg: str):
        self.log.insert('end', msg + "\n")
        self.log.see('end')
        self.root.update_idletasks()

    def run(self):
        src = self.src_var.get().strip()
        dst = self.dst_var.get().strip()
        pair = self.pair_var.get().strip()
        strength = self.strength_var.get().strip()
        if not src or not os.path.isdir(src):
            messagebox.showerror(APP_NAME, "Selecciona una carpeta origen válida.")
            return
        if not dst:
            messagebox.showerror(APP_NAME, "Selecciona una carpeta destino válida.")
            return
        ensure_dir(dst)
        self.append("Iniciando...")
        try:
            process(src, dst, pair, strength, log=self.append)
            messagebox.showinfo(APP_NAME, "Proceso terminado.")
        except Exception as e:
            self.append(f"[ERROR] {e}")
            messagebox.showerror(APP_NAME, str(e))


def parse_args():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument('--source', help='Carpeta origen con S2,S3,S4')
    ap.add_argument('--dest', help='Carpeta destino')
    ap.add_argument('--pair', choices=[PAIR_S4, PAIR_S2, PAIR_BOTH], default=PAIR_BOTH)
    ap.add_argument('--strength', choices=list(STRENGTHS.keys()), default='normal')
    ap.add_argument('--nogui', action='store_true', help='Ejecutar sin GUI')
    return ap.parse_args()


def main():
    args = parse_args()
    if args.nogui or tk is None:
        if not args.source or not args.dest:
            print("Modo CLI: debes indicar --source y --dest")
            sys.exit(2)
        process(args.source, args.dest, args.pair, args.strength, log=print)
        print("Terminado.")
    else:
        root = tk.Tk()
        try:
            ttk.Style().theme_use('clam')
        except Exception:
            pass
        App(root)
        root.mainloop()


if __name__ == '__main__':
    main()