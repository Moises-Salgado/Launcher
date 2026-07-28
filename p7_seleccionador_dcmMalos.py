#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
p7_seleccionador_dcmMalos_v2.py

Mejoras:
- No parece "pegado": muestra progreso también durante la reescritura.
- Permite procesar SOLO series problemáticas para no reescribir todo.
- Ignora el warning de VR SH largo, dejándolo anotado en el reporte si hiciera falta.
- No toca originales.

Uso recomendado para tu caso:
1) Ejecuta.
2) Elige la carpeta DICOM origen.
3) Elige carpeta destino.
4) Cuando pregunte modo, usa "2" para SOLO series problemáticas.
"""

import csv
import re
import sys
import shutil
import warnings
from pathlib import Path
from collections import defaultdict
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except Exception:
    tk = None

try:
    import pydicom
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid
    from pydicom.dataset import FileMetaDataset
except ImportError:
    print("Error: falta instalar pydicom.")
    print("Instala con: pip install pydicom")
    sys.exit(1)

# Evita llenar la terminal con este warning conocido
warnings.filterwarnings(
    "ignore",
    message=r".*maximum length of 16 allowed for VR SH.*",
    category=UserWarning
)


def safe_str(value):
    if value is None:
        return ""
    try:
        if isinstance(value, (list, tuple)):
            return "\\".join(str(x) for x in value)
        return str(value)
    except Exception:
        return ""


def get_tag(ds, name, default=""):
    try:
        return safe_str(getattr(ds, name, default))
    except Exception:
        return default


def normalize_text(text):
    text = safe_str(text).strip()
    text = re.sub(r"[^\w\s\-.()]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("._") or "SIN_NOMBRE"


def choose_folder(title):
    if tk is None:
        return input(f"{title}\nRuta: ").strip()

    root = tk.Tk()
    root.withdraw()
    root.update()
    folder = filedialog.askdirectory(title=title)
    root.destroy()
    return folder


def ask_mode():
    msg = (
        "\nElige modo de reescritura:\n"
        "1 = Reescribir TODAS las series\n"
        "2 = Reescribir SOLO series problemáticas (recomendado)\n"
        "3 = Reescribir SOLO CT primarias\n"
        "Opción: "
    )
    while True:
        x = input(msg).strip()
        if x in {"1", "2", "3"}:
            return x
        print("Opción no válida.")


def show_info(title, text):
    if tk is None:
        print(f"\n{title}\n{text}\n")
        return
    root = tk.Tk()
    root.withdraw()
    root.update()
    messagebox.showinfo(title, text)
    root.destroy()


def classify_series(modality, image_type, series_description):
    txt = f"{modality} {image_type} {series_description}".upper()

    if modality == "CT" and all(x in txt for x in ["ORIGINAL", "PRIMARY", "AXIAL"]):
        return "CT_PRIMARY_AXIAL"

    if modality == "CT" and "REFORMATTED" in txt:
        return "REFORMATTED_CT"

    if modality == "CT" and "LOCALIZER" in txt:
        return "CT_SCOUT_LOCALIZER"

    if modality == "PT" and "ORIGINAL" in txt and "PRIMARY" in txt:
        return "PT_PRIMARY"

    if modality == "OT" and ("SCREEN SAVE" in txt or "FUSION" in txt):
        return "OT_SCREEN_SAVE_OR_FUSION"

    if "SCREEN SAVE" in txt:
        return "SCREEN_SAVE"

    if "FUSION" in txt:
        return "FUSION"

    if "DERIVED" in txt or "SECONDARY" in txt:
        return "DERIVED_SECONDARY"

    if modality == "CT":
        return "CT_REVIEW"

    return "UNCLASSIFIED"


def should_rewrite(classification, mode):
    if mode == "1":
        return True
    if mode == "2":
        return classification in {
            "REFORMATTED_CT",
            "OT_SCREEN_SAVE_OR_FUSION",
            "SCREEN_SAVE",
            "FUSION",
            "DERIVED_SECONDARY",
            "CT_REVIEW",
        }
    if mode == "3":
        return classification == "CT_PRIMARY_AXIAL"
    return False


def read_header(path):
    ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
    return {
        "file_path": str(path),
        "file_name": path.name,
        "patient_name": get_tag(ds, "PatientName"),
        "patient_id": get_tag(ds, "PatientID"),
        "study_date": get_tag(ds, "StudyDate"),
        "study_description": get_tag(ds, "StudyDescription"),
        "series_description": get_tag(ds, "SeriesDescription"),
        "modality": get_tag(ds, "Modality"),
        "image_type": get_tag(ds, "ImageType"),
        "study_instance_uid": get_tag(ds, "StudyInstanceUID"),
        "series_instance_uid": get_tag(ds, "SeriesInstanceUID"),
        "frame_of_reference_uid": get_tag(ds, "FrameOfReferenceUID"),
        "instance_number": get_tag(ds, "InstanceNumber"),
        "sop_instance_uid": get_tag(ds, "SOPInstanceUID"),
        "sop_class_uid": get_tag(ds, "SOPClassUID"),
    }


def build_series(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["study_instance_uid"], row["series_instance_uid"])].append(row)

    series = []
    for _, items in groups.items():
        first = items[0]
        cls = classify_series(first["modality"], first["image_type"], first["series_description"])

        def sort_key(x):
            try:
                return (int(float(x["instance_number"])), x["file_name"])
            except Exception:
                return (999999999, x["file_name"])

        items = sorted(items, key=sort_key)

        series.append({
            "patient_name": first["patient_name"],
            "patient_id": first["patient_id"],
            "study_date": first["study_date"],
            "study_description": first["study_description"],
            "series_description": first["series_description"],
            "modality": first["modality"],
            "image_type": first["image_type"],
            "study_instance_uid": first["study_instance_uid"],
            "series_instance_uid": first["series_instance_uid"],
            "classification": cls,
            "num_files": len(items),
            "items": items,
            "sample_file": first["file_path"],
        })

    series.sort(key=lambda s: (
        s["patient_name"], s["study_date"], s["classification"], s["modality"], s["series_description"]
    ))
    return series


def ensure_file_meta(ds):
    sop_class_uid = getattr(ds, "SOPClassUID", None) or "1.2.840.10008.5.1.4.1.1.7"
    sop_instance_uid = getattr(ds, "SOPInstanceUID", None) or generate_uid()

    ds.SOPClassUID = sop_class_uid
    ds.SOPInstanceUID = sop_instance_uid

    file_meta = getattr(ds, "file_meta", None)
    if file_meta is None:
        file_meta = FileMetaDataset()

    file_meta.MediaStorageSOPClassUID = sop_class_uid
    file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.999.2"
    file_meta.ImplementationVersionName = "PYDICOM_ECLIPSE_V2"

    ds.file_meta = file_meta
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def rewrite_one_file(src, dst, log_list):
    try:
        ds = pydicom.dcmread(str(src), force=True)
        ds = ensure_file_meta(ds)
        dst.parent.mkdir(parents=True, exist_ok=True)
        pydicom.dcmwrite(str(dst), ds, write_like_original=False)
        return True
    except Exception as e:
        log_list.append({"file_path": str(src), "error": str(e)})
        return False


def export_rewritten_series(series_list, output_root, mode):
    root = output_root / "DICOM_COMPATIBILIZADO_ECLIPSE"
    root.mkdir(parents=True, exist_ok=True)

    rewrite_errors = []
    exported_series_rows = []

    selected_series = [s for s in series_list if should_rewrite(s["classification"], mode)]
    total_selected_files = sum(s["num_files"] for s in selected_series)

    print(f"\nSeries seleccionadas para reescritura: {len(selected_series)}")
    print(f"Archivos a reescribir: {total_selected_files}\n")

    global_count = 0

    for idx, s in enumerate(selected_series, start=1):
        series_dir = root / (
            f"{idx:02d}_"
            f"{normalize_text(s['classification'])}_"
            f"{normalize_text(s['modality'])}_"
            f"{normalize_text(s['series_description'])}_"
            f"{normalize_text(s['study_date'])}_"
            f"{normalize_text(s['series_instance_uid'][-12:])}"
        )
        series_dir.mkdir(parents=True, exist_ok=True)

        print(f"[{idx}/{len(selected_series)}] Reescribiendo serie: {s['series_description']} | {s['classification']} | {s['num_files']} archivos")

        ok_count = 0
        for n, item in enumerate(s["items"], start=1):
            src = Path(item["file_path"])
            dst = series_dir / f"IM{n:06d}.dcm"
            if rewrite_one_file(src, dst, rewrite_errors):
                ok_count += 1

            global_count += 1
            if n % 20 == 0 or n == s["num_files"]:
                print(f"   Serie: {n}/{s['num_files']} | Global: {global_count}/{total_selected_files}")

        exported_series_rows.append({
            "folder": str(series_dir),
            "classification": s["classification"],
            "modality": s["modality"],
            "series_description": s["series_description"],
            "image_type": s["image_type"],
            "num_files_original": s["num_files"],
            "num_files_rewritten_ok": ok_count,
            "sample_file": s["sample_file"],
        })

    return root, exported_series_rows, rewrite_errors


def make_test_sets(exported_series_rows, output_root):
    tests_root = output_root / "PRUEBAS_ECLIPSE"
    tests_root.mkdir(parents=True, exist_ok=True)

    plans = {
        "A_SOLO_CT_PRIMARIO_REWRITTEN": {"CT_PRIMARY_AXIAL"},
        "B_CT_PRIMARIO_MAS_PT_REWRITTEN": {"CT_PRIMARY_AXIAL", "PT_PRIMARY"},
        "C_CT_PRIMARIO_MAS_REFORMATTED_REWRITTEN": {"CT_PRIMARY_AXIAL", "REFORMATTED_CT"},
        "D_SOLO_OT_SCREEN_SAVE_FUSION_REWRITTEN": {"OT_SCREEN_SAVE_OR_FUSION", "SCREEN_SAVE", "FUSION"},
        "E_TODO_REWRITTEN": None,
    }

    created = []

    for plan_name, allowed in plans.items():
        plan_dir = tests_root / plan_name
        plan_dir.mkdir(parents=True, exist_ok=True)

        linked = 0
        for row in exported_series_rows:
            if allowed is not None and row["classification"] not in allowed:
                continue

            src = Path(row["folder"])
            dst = plan_dir / src.name

            if dst.exists():
                continue

            try:
                shutil.copytree(src, dst)
                linked += 1
            except Exception:
                pass

        created.append({"plan": plan_name, "folder": str(plan_dir), "num_series": linked})

    return tests_root, created


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def write_txt_report(path, input_folder, series_list, mode, compat_root, tests_root, rewrite_errors):
    mode_text = {
        "1": "TODAS las series",
        "2": "SOLO series problemáticas",
        "3": "SOLO CT primarias",
    }.get(mode, mode)

    with open(path, "w", encoding="utf-8") as f:
        f.write("REPORTE DE COMPATIBILIZACION DICOM PARA ECLIPSE\n")
        f.write("=" * 100 + "\n")
        f.write(f"Carpeta origen: {input_folder}\n")
        f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Modo: {mode_text}\n")
        f.write(f"Series detectadas: {len(series_list)}\n")
        f.write(f"Carpeta compatibilizada: {compat_root}\n")
        f.write(f"Carpeta pruebas: {tests_root}\n\n")

        for i, s in enumerate(series_list, start=1):
            f.write(f"[SERIE {i}]\n")
            f.write(f"Paciente: {s['patient_name']}\n")
            f.write(f"Patient ID: {s['patient_id']}\n")
            f.write(f"Study Date: {s['study_date']}\n")
            f.write(f"Study Description: {s['study_description']}\n")
            f.write(f"Modality: {s['modality']}\n")
            f.write(f"Series Description: {s['series_description']}\n")
            f.write(f"Image Type: {s['image_type']}\n")
            f.write(f"Clasificación: {s['classification']}\n")
            f.write(f"N° archivos: {s['num_files']}\n")
            f.write(f"Archivo ejemplo: {s['sample_file']}\n")
            f.write("-" * 100 + "\n")

        if rewrite_errors:
            f.write("\nERRORES DE REESCRITURA\n")
            f.write("=" * 100 + "\n\n")
            for i, err in enumerate(rewrite_errors, start=1):
                f.write(f"[ERROR {i}]\n")
                f.write(f"file_path: {err['file_path']}\n")
                f.write(f"error: {err['error']}\n")
                f.write("-" * 100 + "\n")


def main():
    input_folder = choose_folder("Selecciona la carpeta raíz con los DICOM")
    if not input_folder:
        print("No se seleccionó carpeta origen.")
        return

    output_folder = choose_folder("Selecciona la carpeta destino")
    if not output_folder:
        print("No se seleccionó carpeta destino.")
        return

    mode = ask_mode()

    input_path = Path(input_folder)
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)

    all_files = [p for p in input_path.rglob("*") if p.is_file()]
    print(f"\nAnalizando {len(all_files)} archivos...")

    ok_rows = []
    read_errors = []

    for i, p in enumerate(all_files, start=1):
        try:
            ok_rows.append(read_header(p))
        except Exception as e:
            read_errors.append({"file_path": str(p), "error": str(e)})

        if i % 100 == 0 or i == len(all_files):
            print(f"Procesados: {i}/{len(all_files)}")

    series_list = build_series(ok_rows)

    print("\nSeries detectadas:")
    for i, s in enumerate(series_list, start=1):
        print(f"  {i:02d}. {s['classification']:26} | {s['modality']:2} | {s['num_files']:4} | {s['series_description']}")

    compat_root, exported_rows, rewrite_errors = export_rewritten_series(series_list, output_path, mode)
    tests_root, test_rows = make_test_sets(exported_rows, output_path)

    report_txt = output_path / "reporte_compatibilizacion_eclipse.txt"
    series_csv = output_path / "series_detectadas.csv"
    exported_csv = output_path / "series_reescritas.csv"
    tests_csv = output_path / "planes_prueba_eclipse.csv"
    read_errors_csv = output_path / "errores_lectura.csv"
    rewrite_errors_csv = output_path / "errores_reescritura.csv"

    write_txt_report(report_txt, input_path, series_list, mode, compat_root, tests_root, rewrite_errors)

    write_csv(series_csv, [{
        "patient_name": s["patient_name"],
        "patient_id": s["patient_id"],
        "study_date": s["study_date"],
        "study_description": s["study_description"],
        "modality": s["modality"],
        "series_description": s["series_description"],
        "image_type": s["image_type"],
        "classification": s["classification"],
        "num_files": s["num_files"],
        "study_instance_uid": s["study_instance_uid"],
        "series_instance_uid": s["series_instance_uid"],
        "sample_file": s["sample_file"],
    } for s in series_list], [
        "patient_name", "patient_id", "study_date", "study_description",
        "modality", "series_description", "image_type", "classification",
        "num_files", "study_instance_uid", "series_instance_uid", "sample_file"
    ])

    write_csv(exported_csv, exported_rows, [
        "folder", "classification", "modality", "series_description",
        "image_type", "num_files_original", "num_files_rewritten_ok", "sample_file"
    ])

    write_csv(tests_csv, test_rows, ["plan", "folder", "num_series"])
    write_csv(read_errors_csv, read_errors, ["file_path", "error"])
    write_csv(rewrite_errors_csv, rewrite_errors, ["file_path", "error"])

    summary = (
        f"Proceso terminado.\n\n"
        f"Series detectadas: {len(series_list)}\n"
        f"Series reescritas: {len(exported_rows)}\n"
        f"Errores de lectura: {len(read_errors)}\n"
        f"Errores de reescritura: {len(rewrite_errors)}\n\n"
        f"Carpeta compatibilizada:\n{compat_root}\n\n"
        f"Planes de prueba:\n{tests_root}\n\n"
        f"Reportes:\n"
        f"{report_txt}\n"
        f"{series_csv}\n"
        f"{exported_csv}\n"
        f"{tests_csv}\n"
    )

    print("\n" + summary)
    show_info("Proceso terminado", summary)


if __name__ == "__main__":
    main()