import os
import json
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_FILE = CONFIG_HOME / "centro-comando-clinico" / "config.json"
LEGACY_CONFIG_FILE = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "base_ots_dir": str(Path.home() / "Escritorio" / "OTs"),
    "dicom_export_dir": str(Path.home() / "Escritorio" / "DICOM_Export")
}

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        # Migración transparente desde las versiones portables anteriores.
        if LEGACY_CONFIG_FILE.exists():
            try:
                with open(LEGACY_CONFIG_FILE, "r", encoding="utf-8") as source:
                    legacy = json.load(source)
                save_config(legacy)
                return legacy
            except Exception:
                pass
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(config: dict):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Error guardando config: {e}")

def get_ots_dir() -> Path:
    config = load_config()
    return Path(config.get("base_ots_dir", DEFAULT_CONFIG["base_ots_dir"]))

def get_dicom_export_dir() -> Path:
    config = load_config()
    return Path(config.get("dicom_export_dir", DEFAULT_CONFIG["dicom_export_dir"]))
