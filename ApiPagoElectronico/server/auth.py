import base64
import hashlib
import hmac
import threading
import time
from collections import deque
from functools import lru_cache, wraps
from urllib.parse import urlsplit
from flask import request, Response, jsonify
from config.settings import API_AUTH_USER, API_AUTH_PASS_HASH
from core.logging_config import logger as _log

LOOPBACK_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

def _unauthorized():
    return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="POS Gateway"'})

@lru_cache(maxsize=16)
def _password_matches(pwd: str) -> bool:
    """Compara la clave recibida contra API_AUTH_PASS_HASH (PBKDF2-SHA256).
    Se cachea porque PBKDF2 es lento a propósito y los clientes repiten la misma clave."""
    try:
        algo, iterations, salt_hex, hash_hex = API_AUTH_PASS_HASH.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(calc.hex(), hash_hex)
    except Exception:
        return False

# Fuerza bruta: tras MAX_FALLOS claves incorrectas en VENTANA_FALLOS segundos se
# deja de calcular PBKDF2 durante BLOQUEO_SEGUNDOS (cada cálculo cuesta CPU a
# propósito). Es global y no por IP porque con BIND_HOST=127.0.0.1 todo llega
# desde loopback; para no bloquear a la caja misma, una clave que ya se validó
# antes se sigue aceptando durante el bloqueo sin recalcular nada.
MAX_FALLOS = 10
VENTANA_FALLOS = 60
BLOQUEO_SEGUNDOS = 60

_fallos = deque()
_bloqueado_hasta = 0.0
_claves_validas = set()  # sha256 de claves ya verificadas
_auth_lock = threading.Lock()


def _reiniciar_bloqueo():
    global _bloqueado_hasta
    with _auth_lock:
        _fallos.clear()
        _claves_validas.clear()
        _bloqueado_hasta = 0.0


def _clave_valida(pwd: str) -> bool:
    global _bloqueado_hasta
    huella = hashlib.sha256(pwd.encode("utf-8")).hexdigest()
    ahora = time.time()
    with _auth_lock:
        if huella in _claves_validas:
            return True
        if ahora < _bloqueado_hasta:
            return False
    ok = _password_matches(pwd)
    with _auth_lock:
        if ok:
            _claves_validas.add(huella)
            return True
        _fallos.append(ahora)
        while _fallos and _fallos[0] < ahora - VENTANA_FALLOS:
            _fallos.popleft()
        if len(_fallos) >= MAX_FALLOS:
            _bloqueado_hasta = ahora + BLOQUEO_SEGUNDOS
            _fallos.clear()
            _log.warning(
                f"[AUTH] {MAX_FALLOS} claves incorrectas en {VENTANA_FALLOS}s: se bloquean nuevos intentos "
                f"por {BLOQUEO_SEGUNDOS}s (posible fuerza bruta)"
            )
    return False


def check_basic_auth_header(auth_header: str) -> bool:
    """Devuelve True si Authorization header es Basic y usuario/clave coinciden."""
    if not auth_header or not auth_header.startswith("Basic "):
        _log.warning("[AUTH] Header Authorization ausente o no es Basic")
        return False
    try:
        encoded = auth_header[6:]
        decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
        if ":" not in decoded:
            _log.warning("[AUTH] Header Basic malformado (sin ':')")
            return False
        user, pwd = decoded.split(":", 1)
        user_ok = hmac.compare_digest(user, API_AUTH_USER)
        pass_ok = user_ok and _clave_valida(pwd)
        if not user_ok or not pass_ok:
            # repr + recorte: el usuario lo manda el cliente y no debe poder inyectar líneas en el log.
            _log.warning(
                f"[AUTH] Credenciales inválidas — "
                f"usuario_recibido={user[:40]!r} user_ok={user_ok} pass_ok={pass_ok}"
            )
        return user_ok and pass_ok
    except Exception as e:
        _log.error(f"[AUTH] Excepción al validar header: {e}")
        return False

def require_basic_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method == "OPTIONS":
            return Response("", 200)
        if not API_AUTH_USER or not API_AUTH_PASS_HASH:
            _log.error("[AUTH] CRITICO: credenciales vacías en runtime")
            return _unauthorized()
        auth = request.headers.get("Authorization")
        if not check_basic_auth_header(auth):
            _log.warning(f"[AUTH] Acceso rechazado a {request.path} desde {request.remote_addr}")
            return _unauthorized()
        return fn(*args, **kwargs)
    return wrapper

def _hostname(netloc: str) -> str:
    try:
        return (urlsplit(f"//{netloc}").hostname or "").lower()
    except ValueError:
        return ""

def check_local_panel_request():
    """
    El panel (/panel/*) permite reescribir el .env, reiniciar el servicio y
    resolver transacciones, así que solo se acepta desde esta misma máquina:
    - la conexión debe venir de loopback (nadie de la LAN),
    - el Host debe ser localhost/127.0.0.1 (evita DNS rebinding),
    - si el navegador manda Origin, debe ser el mismo host (evita que una
      página web cualquiera abierta en la caja llame al panel por detrás).
    Devuelve None si la petición es válida, o una respuesta 403.
    """
    if request.remote_addr not in LOOPBACK_ADDRS:
        _log.warning(f"[PANEL] Acceso rechazado a {request.path} desde {request.remote_addr} (no es local)")
        return jsonify({"ok": False, "error": "El panel solo está disponible desde esta máquina (localhost)"}), 403
    if _hostname(request.host) not in LOOPBACK_HOSTS:
        _log.warning(f"[PANEL] Host no permitido '{request.host}' en {request.path}")
        return jsonify({"ok": False, "error": "Host no permitido"}), 403
    origin = request.headers.get("Origin")
    if origin and origin != "null":
        origin_netloc = urlsplit(origin).netloc
        if origin_netloc.lower() != request.host.lower():
            _log.warning(f"[PANEL] Origin no permitido '{origin}' en {request.path}")
            return jsonify({"ok": False, "error": "Origen no permitido"}), 403
    elif origin == "null":
        return jsonify({"ok": False, "error": "Origen no permitido"}), 403
    return None
