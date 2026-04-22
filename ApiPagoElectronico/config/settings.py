import os
import socket
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

_ENV_LOCK = threading.Lock()
_ENV_MTIME = None


def get_base_dir() -> Path:
    return Path(sys.argv[0]).resolve().parent


def get_env_path() -> Path:
    base_candidate = get_base_dir() / ".env"
    if base_candidate.exists():
        return base_candidate
    cwd_candidate = Path.cwd() / ".env"
    return cwd_candidate


def _load_env_if_needed(force: bool) -> None:
    global _ENV_MTIME
    env_path = get_env_path()
    if not env_path.exists():
        return
    try:
        mtime = env_path.stat().st_mtime
    except Exception:
        return

    with _ENV_LOCK:
        if not force and _ENV_MTIME is not None and _ENV_MTIME == mtime:
            return
        load_dotenv(dotenv_path=env_path, override=True)
        _ENV_MTIME = mtime


def ensure_env_loaded() -> None:
    _load_env_if_needed(force=False)


def reload_env() -> None:
    _load_env_if_needed(force=True)
    refresh_settings()


def _env_str(key: str, default: str = "") -> str:
    ensure_env_loaded()
    val = os.environ.get(key)
    if val is None:
        return default
    return str(val)


def _env_int(key: str, default: int) -> int:
    ensure_env_loaded()
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    ensure_env_loaded()
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _hostname_default() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "caja"


def refresh_settings() -> None:
    global APP_NAME
    global LOG_FILE
    global HTTP_PORT
    global ID_SUCURSAL
    global NOMBRE_CAJA
    global ID_TERMINAL
    global USAR_POS_FISICO
    global PUERTOS_COM
    global USAR_GETNET
    global MAX_TRANSACTION_TIME
    global TIMEOUT_SERVER
    global ALLOWED_MP
    global MP_API_URL
    global API_AUTH_USER
    global API_AUTH_PASS

    ensure_env_loaded()

    APP_NAME = _env_str("APP_NAME", "ApiPagoElectronico")
    LOG_FILE = _env_str("LOG_FILE", str(get_base_dir() / "pos_gateway.log"))

    HTTP_PORT = _env_int("HTTP_PORT", 5005)

    ID_SUCURSAL = _env_int("ID_SUCURSAL", 0)

    NOMBRE_CAJA = _env_str("NOMBRE_CAJA", "").strip() or _hostname_default()

    terminal_env = _env_str("TERMINAL_ID", "").strip() or _env_str("ID_TERMINAL", "").strip()
    ID_TERMINAL = terminal_env or f"POS_{NOMBRE_CAJA}"

    USAR_POS_FISICO = _env_bool("USAR_POS_FISICO", True)

    PUERTOS_COM = _env_str("PUERTOS_COM", "")

    USAR_GETNET = _env_bool("USAR_GETNET", False)

    MAX_TRANSACTION_TIME = _env_int("MAX_TRANSACTION_TIME", 90)
    TIMEOUT_SERVER = _env_int("TIMEOUT_SERVER", 120)

    allowed_raw = _env_str("ALLOWED_MP", "")
    ALLOWED_MP = set([x.strip() for x in allowed_raw.split(",") if x.strip()])
    MP_API_URL = _env_str("MP_API_URL", "https://api.mercadopago.com/v1/orders")

    API_AUTH_USER = _env_str("API_AUTH_USER", "")
    API_AUTH_PASS = _env_str("API_AUTH_PASS", "")


refresh_settings()

def is_flask_available() -> bool:
    try:
        import flask
        return True
    except Exception:
        return False

def is_transbank_available() -> bool:
    try:
        import transbank
        return True
    except Exception:
        return False

def is_systray_available() -> bool:
    try:
        import pystray
        import PIL
        return True
    except Exception:
        return False
