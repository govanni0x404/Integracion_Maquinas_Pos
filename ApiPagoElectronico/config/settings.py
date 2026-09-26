import os
import sys
import socket
from pathlib import Path

# Credenciales API — SIEMPRE definidas en el código, nunca del .env.
# La clave NO se guarda en texto plano: solo su hash PBKDF2-SHA256
# (formato "pbkdf2_sha256$<iteraciones>$<salt_hex>$<hash_hex>").
# Para cambiar la clave, generar un hash nuevo con:
#   python -c "import hashlib,os,getpass; s=os.urandom(16); p=getpass.getpass(); print('pbkdf2_sha256$200000$'+s.hex()+'$'+hashlib.pbkdf2_hmac('sha256',p.encode(),s,200000).hex())"
API_AUTH_USER = "DATAMAULE"
API_AUTH_PASS_HASH = "pbkdf2_sha256$200000$b5fada2c559914f4d7b835f3673e6039$fcbff68cb7b00032f9872f0f981ca3d86b05d0785d31fc5d86f358cbee49064e"

# Claves que NUNCA se leen del .env — siempre hardcodeadas en el código
_ENV_PROTECTED_KEYS = {"API_AUTH_USER", "API_AUTH_PASS", "API_AUTH_PASS_HASH"}

def _parse_env_file(path: str) -> dict:
    """Parser minimo de .env — no requiere el modulo dotenv."""
    result = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                # Ignorar claves protegidas
                if key in _ENV_PROTECTED_KEYS:
                    continue
                value = value.strip()
                # Quitar comillas simples o dobles
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                result[key] = value
    except Exception:
        pass
    return result

def _find_and_load_dotenv():
    """
    Busca el .env en el orden correcto para PyInstaller y desarrollo.
    Usa dotenv si esta disponible, sino un parser propio.
    """
    candidates = []

    # 0. Ruta explícita (tests / diagnóstico): no se busca en ningún otro lado
    forced = os.environ.get("POS_GATEWAY_ENV_FILE", "").strip()
    if forced and Path(forced).exists():
        candidates.append(Path(forced))

    # 1. Junto al ejecutable (PyInstaller onefile/onedir)
    try:
        exe_dir = Path(sys.executable).parent
        for folder in [exe_dir, exe_dir.parent, exe_dir.parent.parent, exe_dir.parent.parent.parent]:
            env_path = folder / ".env"
            if env_path.exists():
                candidates.append(env_path)
                break
    except Exception:
        pass

    # 2. Junto al archivo .py (modo desarrollo)
    try:
        script_dir = Path(__file__).parent.parent
        env_path = script_dir / ".env"
        if env_path.exists():
            candidates.append(env_path)
    except Exception:
        pass

    # 3. CWD
    cwd_env = Path(os.getcwd()) / ".env"
    if cwd_env.exists():
        candidates.append(cwd_env)

    if not candidates:
        try:
            exe_dir = Path(sys.executable).parent
            target_dir = exe_dir
            parts = exe_dir.parts
            if "Contents" in parts and "MacOS" in parts:
                target_dir = exe_dir.parent.parent.parent
            env_path = target_dir / ".env"
            if not env_path.exists():
                default_env = "\n".join([
                    "HTTP_PORT=5005",
                    "ID_SUCURSAL=1",
                    "NOMBRE_CAJA=",
                    "TERMINAL_ID=",
                    "USAR_POS_FISICO=false",
                    "PUERTOS_COM=COM5,COM6,COM7,COM8",
                    "USAR_GETNET=true",
                    "GETNET_MAX_ESPERA=300",
                    "MAX_TRANSACTION_TIME=90",
                    "TIMEOUT_SERVER=120",
                    "ALLOWED_ORIGINS=",
                    "ALLOWED_MP=",
                    "MP_API_URL=https://api.mercadopago.com/v1/orders",
                    "MP_ACCESS_TOKEN=",
                    "MP_TERMINAL_ID=",
                    "ALLOW_MP_TOKEN_IN_REQUEST=false",
                    "",
                ])
                env_path.write_text(default_env, encoding="utf-8")
            candidates.append(env_path)
        except Exception:
            return

    chosen = candidates[0]
    os.chdir(chosen.parent)

    # Intentar con dotenv (desarrollo), sino usar parser propio (compilado)
    _env_loaded_with = None
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=str(chosen), override=True)
        _env_loaded_with = "python-dotenv"
    except ImportError:
        for key, value in _parse_env_file(str(chosen)).items():
            os.environ.setdefault(key, value)
        _env_loaded_with = "parser-propio"

    # Garantía final: eliminar claves protegidas del entorno aunque el .env
    # las haya cargado. Las credenciales siempre vienen del código, nunca del .env.
    _removed = []
    for _k in _ENV_PROTECTED_KEYS:
        if os.environ.pop(_k, None) is not None:
            _removed.append(_k)

    # Guardar info de diagnóstico para el log posterior
    global _dotenv_info
    _dotenv_info = {
        "path": str(chosen),
        "loader": _env_loaded_with,
        "removed_from_env": _removed,
    }

_dotenv_info: dict = {}
_find_and_load_dotenv()

APP_NAME = os.environ.get("APP_NAME", " ApiPagoElectronico")



# ── Log diagnóstico de credenciales (se escribe apenas el logger esté listo) ──
def _log_auth_diagnostics():
    """Llamar desde app.py DESPUES de que el logger esté inicializado."""
    import logging
    # Usar root logger para no depender del nombre exacto del logger configurado
    log = logging.getLogger()  # root logger — siempre existe

    lines = [
        "[AUTH-DIAG] === Diagnóstico de credenciales ===",
        f"[AUTH-DIAG] .env cargado desde: {_dotenv_info.get('path', 'ninguno')}",
        f"[AUTH-DIAG] Método de carga .env: {_dotenv_info.get('loader', 'no se cargó .env')}",
        f"[AUTH-DIAG] Claves en os.environ eliminadas: {_dotenv_info.get('removed_from_env', [])}",
        f"[AUTH-DIAG] API_AUTH_USER final: '{API_AUTH_USER}'",
        f"[AUTH-DIAG] API_AUTH_PASS_HASH configurado: {bool(API_AUTH_PASS_HASH)}",
        f"[AUTH-DIAG] API_AUTH_USER en os.environ ahora: '{os.environ.get('API_AUTH_USER', '(vacío=correcto)')}'" ,
        "[AUTH-DIAG] ========================================",
    ]

    for line in lines:
        if _dotenv_info.get('removed_from_env'):
            log.warning(line)
        else:
            log.info(line)

    # También escribir a un archivo de diagnóstico directo (no depende del logger)
    try:
        diag_path = os.path.join(os.getcwd(), "auth_diag.log")
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(diag_path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(f"{ts} {line}\n")
    except Exception:
        pass


# Archivos
LOG_FILE = os.environ.get("LOG_FILE", "pos_gateway.log")
LOCK_FILE = os.environ.get("LOCK_FILE", "pos_gateway.lock")
DB_FILE = os.environ.get("DB_FILE", "pos_gateway.db")

# Puerto HTTP
HTTP_PORT = int(os.environ.get("HTTP_PORT", os.environ.get("PORT", "5005")))

# Identificación
ID_SUCURSAL = os.environ.get("ID_SUCURSAL", "1")
NOMBRE_CAJA = os.environ.get("NOMBRE_CAJA", socket.gethostname())
ID_TERMINAL = os.environ.get("ID_TERMINAL", os.environ.get("TERMINAL_ID", f"POS_{NOMBRE_CAJA}"))

# POS físico
USAR_POS_FISICO = os.environ.get("USAR_POS_FISICO", "false").lower() == "true"
PUERTOS_COM = os.environ.get("PUERTOS_COM", "COM5,COM6,COM7,COM8")

# Timeouts
MAX_TRANSACTION_TIME = int(os.environ.get("MAX_TRANSACTION_TIME", "90"))
TIMEOUT_SERVER = int(os.environ.get("TIMEOUT_SERVER", "120"))

# Mercado Pago
ALLOWED_MP = set([x.strip() for x in os.environ.get("ALLOWED_MP", "").split(",") if x.strip()])
MP_API_URL = os.environ.get("MP_API_URL", "https://api.mercadopago.com/v1/orders")
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
MP_TERMINAL_ID = os.environ.get("MP_TERMINAL_ID", "")
ALLOW_MP_TOKEN_IN_REQUEST = os.environ.get("ALLOW_MP_TOKEN_IN_REQUEST", "false").lower() == "true"

_allowed_origins_raw = os.environ.get("ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGINS = [x.strip() for x in _allowed_origins_raw.split(",") if x.strip()] or None

# GETNET POS
# ¿Esta máquina tiene POS Getnet conectado?
USAR_GETNET = os.environ.get("USAR_GETNET", "true").lower() == "true"
# Puerto COM fijo para el POS Getnet (ej. "COM3"). Si se deja vacío, se
# detecta automáticamente probando todos los puertos disponibles. Útil para
# diagnosticar cuando el dispositivo expone varios puertos COM (USB
# compuesto) y hay que confirmar cuál es el que realmente procesa las ventas.
GETNET_PORT = os.environ.get("GETNET_PORT", "").strip() or None
# Segundos máximos que una petición /pago Getnet queda abierta esperando que el
# POS vuelva (cable desconectado) cuando la venta quedó sin confirmar. Al
# cumplirse responde INDETERMINADA y la validación sigue en segundo plano.
# 0 o vacío = sin límite.
try:
    GETNET_MAX_ESPERA = max(0, int(os.environ.get("GETNET_MAX_ESPERA", "300").strip() or 0))
except ValueError:
    GETNET_MAX_ESPERA = 300

# Feature detection helpers (runtime)
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
