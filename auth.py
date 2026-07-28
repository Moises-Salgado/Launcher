# auth.py
"""
Sistema de autenticación para el launcher.
Maneja usuarios, roles y validación de acceso.
"""
import json
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any

HERE = Path(__file__).resolve().parent
USERS_FILE = HERE / "users.json"


def _hash_password(password: str) -> str:
    """Genera un hash SHA-256 de la contraseña."""
    return hashlib.sha256(password.encode()).hexdigest()


def load_users() -> list[Dict[str, Any]]:
    """Carga la lista de usuarios desde el archivo JSON."""
    try:
        if USERS_FILE.exists():
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            return data.get("users", [])
    except Exception:
        pass
    return []


def save_users(users: list[Dict[str, Any]]) -> None:
    """Guarda la lista de usuarios en el archivo JSON."""
    try:
        data = {"users": users}
        USERS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception:
        pass


def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    """
    Autentica un usuario con username y password.
    Devuelve el diccionario del usuario si las credenciales son correctas, None en caso contrario.
    """
    users = load_users()
    password_hash = _hash_password(password)
    
    for user in users:
        stored_hash = user.get("password", "")
        # Si la contraseña está en texto plano (primera vez), la comparamos directamente
        # y luego la hasheamos para guardarla
        if user.get("username", "").lower() == username.lower():
            if stored_hash == password_hash or stored_hash == password:
                # Si estaba en texto plano, ahora la hasheamos
                if stored_hash == password:
                    user["password"] = password_hash
                    save_users(users)
                return user
    return None


def check_admin_role(user: Dict[str, Any]) -> bool:
    """Verifica si un usuario tiene rol de administrador."""
    role = user.get("role", "").lower()
    return role == "administrador" or role == "admin"


def is_admin(username: str, password: str) -> bool:
    """
    Verifica si el usuario es administrador.
    Devuelve True solo si las credenciales son correctas Y el usuario es administrador.
    """
    user = authenticate(username, password)
    if user is None:
        return False
    return check_admin_role(user)
