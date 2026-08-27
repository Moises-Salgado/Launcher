#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    printf '%s\n' "Uso: $0 paquete.deb" >&2
    exit 2
fi

package_file=$(realpath "$1")
test_root=$(mktemp -d)

cleanup() {
    rm -rf "$test_root"
}
trap cleanup EXIT INT TERM

dpkg-deb --info "$package_file" >/dev/null
dpkg-deb --extract "$package_file" "$test_root"

app_root="$test_root/opt/centro-comando-clinico/app"
venv_root="$test_root/opt/centro-comando-clinico/venv"
desktop_file="$test_root/usr/share/applications/centro-comando-clinico.desktop"

test -x "$test_root/usr/bin/centro-comando-clinico"
test -f "$app_root/launcher.py"
test -f "$app_root/qt_app.py"
test -f "$app_root/P1_ExtractorOTs.py"
test -f "$app_root/P5_Extractor_Halcyon.py"
test -f "$app_root/halcyon_serial_map.json"
test -f "$test_root/usr/share/doc/centro-comando-clinico/README.md"
test -f "$desktop_file"
test -f "$test_root/usr/share/icons/hicolor/256x256/apps/centro-comando-clinico.png"
desktop-file-validate "$desktop_file"

XDG_CONFIG_HOME="$test_root/user-config" \
QT_QPA_PLATFORM=offscreen \
PYTHONPATH="$app_root" \
"$venv_root/bin/python" - <<'PY'
import tempfile
from pathlib import Path

import fitz
import numpy as np
import pydicom
import pylibjpeg
import _libjpeg
import _openjpeg
from PIL import Image
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import BasicTextSRStorage, ExplicitVRLittleEndian, MRImageStorage, generate_uid
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from config_manager import CONFIG_FILE, save_config
from P1_ExtractorOTs import analyze_ot_pdf, classify_ot_text
from qt_app import APP_STYLESHEET, APP_VERSION, ClinicalCommandCenter, scan_dicom_folder
from qt_theme import ASSETS_DIR

application = QApplication([])
application.setStyle("Fusion")
application.setStyleSheet(APP_STYLESHEET)
window = ClinicalCommandCenter()

assert window.pages.count() == 5
assert window.p1.kind_combo.count() == 6
visible_texts = [widget.text() for widget in window.findChildren(QLabel)]
for removed_text in (
    "Procesador masivo",
    "SISTEMA DISPONIBLE",
    "EJECUCIÓN LOCAL",
    "Buenos días, Operador",
    "Buenas tardes, Operador",
    "Buenas noches, Operador",
    "P1–P4  ·  Aplicación nativa para Ubuntu",
    "DATOS PROTEGIDOS",
    "Herramientas disponibles",
):
    assert removed_text not in visible_texts
assert APP_VERSION == "1.4.1"
assert pydicom.__version__ == "3.0.2"
assert pylibjpeg.__version__ == "2.1.0"
assert "Herramientas de radioterapia" in visible_texts
assert "Programas" in visible_texts
assert "PROCESAMIENTO LOCAL" in visible_texts
assert set(window.navigation_buttons) == {"home", "p1", "p2", "p3", "p4"}
assert all(button.text() != "Herramientas clínicas" for button in window.findChildren(QPushButton))
assert not hasattr(window.home, "inspect_button")
for key, tool_card in window.home.cards.items():
    assert tool_card.open_button.objectName() == "ToolLaunchButton"
    assert tool_card.open_button.text().startswith(f"Abrir {key.upper()}")
window.home.cards["p2"].open_button.click()
application.processEvents()
assert window.pages.currentWidget() is window.p2
assert window.navigation_buttons["p2"].isChecked()
window.navigate("home")
application.processEvents()
assert window.pages.currentWidget() is window.home
with Image.open(ASSETS_DIR / "radiotherapy_logo.png") as logo:
    assert "A" in logo.getbands()
    alpha_min, alpha_max = logo.getchannel("A").getextrema()
    assert alpha_min == 0 and alpha_max == 255
assert not window.windowIcon().isNull()
for page in (window.p1, window.p2, window.p3, window.p4):
    back_button = page.findChild(QPushButton, "BackButton")
    assert back_button is not None
    assert back_button.text() == "←  VOLVER AL INICIO"
    assert back_button.width() >= 220
assert window.p3.series_tree.columnCount() == 3
assert window.p3.series_tree.headerItem().text(0) == "Modalidad"
assert window.p3.series_tree.headerItem().text(1) == "Serie / descripción"
assert classify_ot_text("Varian Halcyon 2", "ot.pdf") == "HALCYON_2"
assert classify_ot_text("Control de Calidad PTWECM", "ot.pdf") == "CONTROL_DE_CALIDAD"
with tempfile.TemporaryDirectory() as temporary:
    test_pdf = Path(temporary) / "halcyon2.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 50), "FieldBeat Varian Halcyon 2")
    page.insert_text((40, 70), "Información de la Tarea 12345")
    document.save(test_pdf)
    document.close()
    analysis = analyze_ot_pdf(str(test_pdf), "AUTO")
    assert analysis["kind"] == "HALCYON_2"
    assert analysis["data"]["_N_TAREA"] == "12345"
with tempfile.TemporaryDirectory() as temporary:
    dicom_root = Path(temporary)
    study_uid = generate_uid()

    def make_dataset(path, sop_class, series_uid, modality):
        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = sop_class
        file_meta.MediaStorageSOPInstanceUID = generate_uid()
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        dataset = FileDataset(path, {}, file_meta=file_meta, preamble=b"\0" * 128)
        dataset.SOPClassUID = sop_class
        dataset.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
        dataset.StudyInstanceUID = study_uid
        dataset.SeriesInstanceUID = series_uid
        dataset.Modality = modality
        dataset.PatientName = "PRUEBA^DICOM"
        dataset.PatientID = "11111111-1"
        dataset.StudyDate = "20260826"
        dataset.SeriesNumber = 1 if modality == "MR" else 2
        dataset.InstanceNumber = 1
        return dataset

    image_dataset = make_dataset(dicom_root / "imagen.dcm", MRImageStorage, generate_uid(), "MR")
    image_dataset.Rows = 2
    image_dataset.Columns = 2
    image_dataset.SamplesPerPixel = 1
    image_dataset.PhotometricInterpretation = "MONOCHROME2"
    image_dataset.BitsAllocated = 16
    image_dataset.BitsStored = 12
    image_dataset.HighBit = 11
    image_dataset.PixelRepresentation = 0
    image_dataset.PixelData = np.array([[0, 100], [200, 300]], dtype=np.uint16).tobytes()
    image_dataset.save_as(dicom_root / "imagen.dcm", enforce_file_format=True)

    report_dataset = make_dataset(dicom_root / "informe.dcm", BasicTextSRStorage, generate_uid(), "SR")
    report_dataset.save_as(dicom_root / "informe.dcm", enforce_file_format=True)

    orphan_meta = FileMetaDataset()
    orphan_meta.MediaStorageSOPClassUID = MRImageStorage
    orphan_meta.MediaStorageSOPInstanceUID = generate_uid()
    orphan_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    orphan_dataset = FileDataset(
        dicom_root / "cabecera_incompleta.dcm",
        {},
        file_meta=orphan_meta,
        preamble=b"\0" * 128,
    )
    orphan_dataset.SOPClassUID = MRImageStorage
    orphan_dataset.SOPInstanceUID = orphan_meta.MediaStorageSOPInstanceUID
    orphan_dataset.save_as(dicom_root / "cabecera_incompleta.dcm", enforce_file_format=True)

    dicom_scan = scan_dicom_folder(str(dicom_root))
    assert dicom_scan["pixel_files"] == 1
    assert dicom_scan["non_pixel_files"] == 1
    assert dicom_scan["ignored_technical_files"] == 1
    assert sum(len(files) for files in dicom_scan["series_image_map"].values()) == 1
    window.p3.load_scan(dicom_scan)
    application.processEvents()
    assert not window.p3.graphics.pixmap_item.pixmap().isNull()
    assert window.p3.image_counter.text() == "1 / 1"
    sr_item = next(
        window.p3.series_tree.topLevelItem(index)
        for index in range(window.p3.series_tree.topLevelItemCount())
        if window.p3.series_tree.topLevelItem(index).text(0) == "SR"
    )
    assert sr_item.text(2) == "—"
    window.p3.series_tree.setCurrentItem(sr_item)
    application.processEvents()
    assert window.p3.image_counter.text() == "Sin imagen"
    assert "informe DICOM" in window.p3.viewer_status.text()
assert not hasattr(window.p4, "anonymize")
assert not hasattr(window.p4, "copy_non_dicom")
assert not hasattr(window.p4, "gdcm")
window.p3.original_name.setText("MARTINEZ MORALES^MARIELA CRISTINA")
window.p3.new_name.setText("MARTINEZ MORALES MARIELA CRISTINA")
application.processEvents()
assert window.p3.name_warning_card.property("invalid") is True
assert "SE ELIMINÓ" in window.p3.name_warning_title.text()
window.p3.new_name.setText("MARTINEZ MORALES^MARIELA CRISTINA")
application.processEvents()
assert window.p3.name_warning_card.property("invalid") is False
save_config({"base_ots_dir": "/tmp/ots", "dicom_export_dir": "/tmp/dicom"})
assert CONFIG_FILE.exists()
assert "centro-comando-clinico" in str(CONFIG_FILE)

print("Aplicación instalada simulada: OK")
window.close()
PY

"$venv_root/bin/python" -m pip check
printf '%s\n' "Paquete Debian validado: $package_file"
