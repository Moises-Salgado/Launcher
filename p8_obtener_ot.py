#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Detector de archivos DICOM OT / Secondary Capture
- Permite seleccionar un archivo o una carpeta
- Detecta DICOM y clasifica:
    * OT (Modality == OT)
    * SC (Secondary Capture por SOP Class)
    * OT+SC
- Permite guardar el resultado como CSV o TXT
- Opcional: copiar los archivos OT/SC detectados a una carpeta aparte

Requiere:
    pip install pydicom
"""

from __future__ import annotations

import csv
import os
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pydicom
from pydicom.errors import InvalidDicomError

SC_SOP_UIDS = {
    "1.2.840.10008.5.1.4.1.1.7": "Secondary Capture Image Storage",
    "1.2.840.10008.5.1.4.1.1.7.1": "Multi-frame Single Bit Secondary Capture Image Storage",
    "1.2.840.10008.5.1.4.1.1.7.2": "Multi-frame Grayscale Byte Secondary Capture Image Storage",
    "1.2.840.10008.5.1.4.1.1.7.3": "Multi-frame Grayscale Word Secondary Capture Image Storage",
    "1.2.840.10008.5.1.4.1.1.7.4": "Multi-frame True Color Secondary Capture Image Storage",
}


def safe_get(ds, name: str, default: str = "") -> str:
    try:
        value = getattr(ds, name, default)
        if value is None:
            return default
        return str(value)
    except Exception:
        return default


def summarize_imagetype(ds) -> str:
    try:
        value = ds.get("ImageType", "")
        if not value:
            return ""
        if isinstance(value, (list, tuple)):
            return "\\".join(str(x) for x in value)
        return str(value)
    except Exception:
        return ""


def is_dicom_file(path: Path) -> bool:
    try:
        pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
        return True
    except Exception:
        return False


def analyze_dicom(path: Path) -> dict | None:
    try:
        ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
    except (InvalidDicomError, Exception):
        return None

    modality = safe_get(ds, "Modality")
    sop_uid = safe_get(ds, "SOPClassUID")
    sop_name = SC_SOP_UIDS.get(sop_uid, safe_get(ds, "SOPClassUID"))
    image_type = summarize_imagetype(ds)

    is_ot = modality.upper() == "OT"
    is_sc = sop_uid in SC_SOP_UIDS

    if not (is_ot or is_sc):
        return None

    if is_ot and is_sc:
        clasificacion = "OT+SC"
    elif is_ot:
        clasificacion = "OT"
    else:
        clasificacion = "SC"

    return {
        "archivo": str(path),
        "nombre_archivo": path.name,
        "clasificacion": clasificacion,
        "modality": modality,
        "sop_class_uid": sop_uid,
        "sop_class_name": sop_name,
        "patient_name": safe_get(ds, "PatientName"),
        "patient_id": safe_get(ds, "PatientID"),
        "study_date": safe_get(ds, "StudyDate"),
        "study_description": safe_get(ds, "StudyDescription"),
        "series_number": safe_get(ds, "SeriesNumber"),
        "series_description": safe_get(ds, "SeriesDescription"),
        "instance_number": safe_get(ds, "InstanceNumber"),
        "image_type": image_type,
        "photometric_interpretation": safe_get(ds, "PhotometricInterpretation"),
        "burned_in_annotation": safe_get(ds, "BurnedInAnnotation"),
        "transfer_syntax_uid": safe_get(ds.file_meta, "TransferSyntaxUID") if hasattr(ds, "file_meta") else "",
    }


def iter_files(target: Path):
    if target.is_file():
        yield target
        return
    for root, _, files in os.walk(target):
        for name in files:
            yield Path(root) / name


def save_csv(results: list[dict], out_path: Path):
    fieldnames = [
        "archivo", "nombre_archivo", "clasificacion", "modality",
        "sop_class_uid", "sop_class_name", "patient_name", "patient_id",
        "study_date", "study_description", "series_number",
        "series_description", "instance_number", "image_type",
        "photometric_interpretation", "burned_in_annotation",
        "transfer_syntax_uid",
    ]
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for row in results:
            writer.writerow(row)


def save_txt(results: list[dict], out_path: Path):
    with out_path.open("w", encoding="utf-8") as f:
        f.write("REPORTE DE DICOM OT / SC\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total detectados: {len(results)}\n\n")
        for i, row in enumerate(results, start=1):
            f.write(f"[{i}]\n")
            for key, value in row.items():
                f.write(f"{key}: {value}\n")
            f.write("-" * 80 + "\n")


def copy_detected_files(results: list[dict], dest_dir: Path):
    dest_dir.mkdir(parents=True, exist_ok=True)
    for row in results:
        src = Path(row["archivo"])
        dst = dest_dir / src.name
        base = dst.stem
        ext = dst.suffix
        count = 1
        while dst.exists():
            dst = dest_dir / f"{base}_{count}{ext}"
            count += 1
        shutil.copy2(src, dst)


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Detector DICOM OT / SC")
        self.root.geometry("760x420")

        self.target_path = tk.StringVar()
        self.save_path = tk.StringVar()
        self.copy_ot = tk.BooleanVar(value=False)
        self.copy_path = tk.StringVar()

        self._build()

    def _build(self):
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Origen (archivo o carpeta DICOM):").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.target_path, width=78).grid(row=1, column=0, padx=(0, 8), sticky="we")
        btns1 = ttk.Frame(frm)
        btns1.grid(row=1, column=1, sticky="e")
        ttk.Button(btns1, text="Archivo...", command=self.pick_file).pack(side="left", padx=3)
        ttk.Button(btns1, text="Carpeta...", command=self.pick_folder).pack(side="left", padx=3)

        ttk.Label(frm, text="Guardar reporte como:").grid(row=2, column=0, pady=(16, 0), sticky="w")
        ttk.Entry(frm, textvariable=self.save_path, width=78).grid(row=3, column=0, padx=(0, 8), sticky="we")
        ttk.Button(frm, text="Guardar como...", command=self.pick_save).grid(row=3, column=1, sticky="e")

        chk = ttk.Checkbutton(
            frm,
            text="Copiar también los OT/SC detectados a una carpeta",
            variable=self.copy_ot,
            command=self.toggle_copy_controls
        )
        chk.grid(row=4, column=0, pady=(16, 0), sticky="w")

        self.copy_entry = ttk.Entry(frm, textvariable=self.copy_path, width=78, state="disabled")
        self.copy_entry.grid(row=5, column=0, padx=(0, 8), sticky="we")
        self.copy_btn = ttk.Button(frm, text="Destino copia...", command=self.pick_copy_folder, state="disabled")
        self.copy_btn.grid(row=5, column=1, sticky="e")

        ttk.Separator(frm).grid(row=6, column=0, columnspan=2, sticky="we", pady=18)

        ttk.Button(frm, text="Analizar", command=self.run).grid(row=7, column=0, sticky="w")
        ttk.Button(frm, text="Salir", command=self.root.destroy).grid(row=7, column=1, sticky="e")

        info = (
            "Detecta archivos DICOM con modalidad OT y/o SOP Class Secondary Capture.\n"
            "Útil para identificar exactamente qué series o archivos son OT antes de compararlos."
        )
        ttk.Label(frm, text=info, foreground="#444").grid(row=8, column=0, columnspan=2, pady=(18, 0), sticky="w")

        frm.columnconfigure(0, weight=1)

    def toggle_copy_controls(self):
        state = "normal" if self.copy_ot.get() else "disabled"
        self.copy_entry.configure(state=state)
        self.copy_btn.configure(state=state)

    def pick_file(self):
        path = filedialog.askopenfilename(title="Seleccionar archivo DICOM")
        if path:
            self.target_path.set(path)

    def pick_folder(self):
        path = filedialog.askdirectory(title="Seleccionar carpeta")
        if path:
            self.target_path.set(path)

    def pick_save(self):
        path = filedialog.asksaveasfilename(
            title="Guardar reporte como",
            defaultextension=".csv",
            filetypes=[
                ("CSV", "*.csv"),
                ("TXT", "*.txt"),
            ],
            initialfile="reporte_ot_sc.csv",
        )
        if path:
            self.save_path.set(path)

    def pick_copy_folder(self):
        path = filedialog.askdirectory(title="Seleccionar carpeta destino para OT/SC")
        if path:
            self.copy_path.set(path)

    def run(self):
        target = self.target_path.get().strip()
        save = self.save_path.get().strip()

        if not target:
            messagebox.showerror("Error", "Debes seleccionar un archivo o carpeta.")
            return
        if not save:
            messagebox.showerror("Error", "Debes indicar dónde guardar el reporte.")
            return

        target_path = Path(target)
        save_path = Path(save)

        if not target_path.exists():
            messagebox.showerror("Error", "La ruta de origen no existe.")
            return

        results = []
        scanned = 0
        dicom_ok = 0

        try:
            for f in iter_files(target_path):
                scanned += 1
                try:
                    row = analyze_dicom(f)
                    if is_dicom_file(f):
                        dicom_ok += 1
                    if row:
                        results.append(row)
                except Exception:
                    continue

            results.sort(key=lambda x: (
                x["patient_id"],
                x["study_date"],
                x["series_number"],
                x["instance_number"],
                x["archivo"],
            ))

            save_path.parent.mkdir(parents=True, exist_ok=True)
            if save_path.suffix.lower() == ".txt":
                save_txt(results, save_path)
            else:
                if save_path.suffix.lower() != ".csv":
                    save_path = save_path.with_suffix(".csv")
                save_csv(results, save_path)

            copied = 0
            if self.copy_ot.get():
                dest = self.copy_path.get().strip()
                if not dest:
                    messagebox.showerror("Error", "Marcaste copia de OT/SC pero no elegiste carpeta destino.")
                    return
                copy_detected_files(results, Path(dest))
                copied = len(results)

            msg = (
                "Proceso completado.\n\n"
                f"Revisados: {scanned}\n"
                f"DICOM válidos leídos: {dicom_ok}\n"
                f"OT/SC detectados: {len(results)}\n"
                f"Reporte: {save_path}"
            )
            if self.copy_ot.get():
                msg += f"\nCopiados: {copied}\nDestino copia: {self.copy_path.get().strip()}"

            messagebox.showinfo("Éxito", msg)

        except Exception as e:
            messagebox.showerror("Error", f"Ocurrió un problema:\n{e}")


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()