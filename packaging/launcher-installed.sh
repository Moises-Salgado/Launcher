#!/bin/sh
set -eu

app_install_dir="/opt/centro-comando-clinico"
python_exec="$app_install_dir/venv/bin/python"

if [ ! -x "$python_exec" ]; then
    printf '%s\n' "No se encontró el entorno de ejecución del Centro de Comando Clínico." >&2
    exit 1
fi

exec "$python_exec" "$app_install_dir/app/launcher.py" "$@"
