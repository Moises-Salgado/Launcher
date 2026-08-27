#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
package_version=${1:-1.4.1}
package_architecture=$(dpkg --print-architecture)
build_dir=$(mktemp -d)
package_root="$build_dir/package"
install_root="$package_root/opt/centro-comando-clinico"
app_root="$install_root/app"
venv_root="$install_root/venv"
dist_dir="$project_dir/dist"
output_file="$dist_dir/centro-comando-clinico_${package_version}_${package_architecture}.deb"

cleanup() {
    rm -rf "$build_dir"
}
trap cleanup EXIT INT TERM

install -d "$package_root/DEBIAN" "$app_root/assets" "$package_root/usr/bin"
install -d "$package_root/usr/share/applications"
install -d "$package_root/usr/share/icons/hicolor/256x256/apps"
install -d "$package_root/usr/share/doc/centro-comando-clinico"
install -d "$dist_dir"

python3 -m venv --copies "$venv_root"
"$venv_root/bin/python" -m pip install --upgrade pip
"$venv_root/bin/python" -m pip install -r "$project_dir/requirements-lock.txt"

for source_file in \
    launcher.py qt_app.py qt_theme.py config_manager.py ui_theme.py \
    P1_ExtraerDatosPDF.py P1_ExtractorOTs.py P5_Extractor_Halcyon.py \
    P2_visor_estructuras.py \
    P3_editor_dmc_carpeta.py P4_1_dicom_eclipse_bulletproof.py
do
    install -m644 "$project_dir/$source_file" "$app_root/$source_file"
done

install -m644 "$project_dir/halcyon_serial_map.json" "$app_root/halcyon_serial_map.json"

for asset_file in "$project_dir"/assets/*
do
    install -m644 "$asset_file" "$app_root/assets/$(basename "$asset_file")"
done

install -m755 "$project_dir/packaging/launcher-installed.sh" "$package_root/usr/bin/centro-comando-clinico"
install -m644 "$project_dir/packaging/centro-comando-clinico.desktop" \
    "$package_root/usr/share/applications/centro-comando-clinico.desktop"
install -m644 "$project_dir/assets/radiotherapy_logo.png" \
    "$package_root/usr/share/icons/hicolor/256x256/apps/centro-comando-clinico.png"
install -m644 "$project_dir/README.md" \
    "$package_root/usr/share/doc/centro-comando-clinico/README.md"
install -m755 "$project_dir/packaging/postinst" "$package_root/DEBIAN/postinst"
install -m755 "$project_dir/packaging/postrm" "$package_root/DEBIAN/postrm"

sed \
    -e "s/@VERSION@/$package_version/g" \
    -e "s/@ARCHITECTURE@/$package_architecture/g" \
    "$project_dir/packaging/control.in" > "$package_root/DEBIAN/control"

find "$package_root" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$package_root" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

rm -f "$output_file"
fakeroot dpkg-deb --root-owner-group -Zzstd -z10 --build "$package_root" "$output_file"

checksum=$(sha256sum "$output_file" | awk '{print $1}')
printf '%s  %s\n' "$checksum" "$(basename "$output_file")" > "$dist_dir/SHA256SUMS"

printf '%s\n' "Paquete creado: $output_file"
