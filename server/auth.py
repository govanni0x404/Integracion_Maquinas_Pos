import base64
import hashlib
import hmac
import threading
import time
from collections import deque
from functools import lru_cache, wraps
from urllib.parse import urlsplit
from flask import request, Response, jsonify
from config.settings import API_AUTH_USER, API_AUTH_PASS_HASH, PANEL_PASS_HASH
from core.logging_config import logger as _log

LOOPBACK_ADDRS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

def _unauthorized():
    return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="POS Gateway"'})

def _hash_coincide(pwd: str, hash_guardado: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = hash_guardado.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(calc.hex(), hash_hex)
    except Exception:
        return False


@lru_cache(maxsize=16)
def _password_matches(pwd: str) -> bool:
    """Compara la clave recibida contra API_AUTH_PASS_HASH (PBKDF2-SHA256).
    Se cachea porque PBKDF2 es lento a propósito y los clientes repiten la misma clave."""
    return _hash_coincide(pwd, API_AUTH_PASS_HASH)


def _panel_password_matches(pwd: str) -> bool:
    return _hash_coincide(pwd, PANEL_PASS_HASH)


class LimitadorClave:
    """
    Freno a la fuerza bruta. Tras max_fallos claves incorrectas en `ventana`
    segundos, durante `bloqueo` segundos las claves se siguen verificando pero
    de a UNA y con `espacio` segundos entre cada una (cada PBKDF2 cuesta CPU a
    propósito). Así quien prueba claves queda frenado, pero quien manda la
    clave correcta igual entra (espera su turno hasta `espera` segundos): un
    error de tipeo en la caja ya no deja sin servicio al sistema web.
    Todo llega desde loopback, por eso no se puede bloquear por IP.
    Una clave ya validada se acepta sin recalcular.
    """

    def __init__(self, nombre, verificar, max_fallos=10, ventana=60, bloqueo=60, espacio=0.5, espera=3.0):
        self.nombre = nombre
        self.verificar = verificar
        self.max_fallos, self.ventana, self.bloqueo = max_fallos, ventana, bloqueo
        self.espacio, self.espera = espacio, espera
        self._fallos = deque()
        self._bloqueado_hasta = 0.0
        self._validas = set()  # sha256 de claves ya verificadas
        self._lock = threading.Lock()
        self._turno = threading.Lock()
        self._ultimo_calculo = 0.0

    def reiniciar(self):
        with self._lock:
            self._fallos.clear()
            self._validas.clear()
            self._bloqueado_hasta = 0.0
            self._ultimo_calculo = 0.0

    def bloqueado(self):
        with self._lock:
            return time.time() < self._bloqueado_hasta

    def valida(self, pwd: str) -> bool:
        huella = hashlib.sha256(pwd.encode("utf-8")).hexdigest()
        with self._lock:
            if huella in self._validas:
                return True
        if self.bloqueado():
            if not self._turno.acquire(timeout=self.espera):
                return False
            try:
                pausa = self._ultimo_calculo + self.espacio - time.time()
                if pausa > 0:
                    time.sleep(pausa)
                ok = self.verificar(pwd)
                self._ultimo_calculo = time.time()
            finally:
                self._turno.release()
        else:
            ok = self.verificar(pwd)
        ahora = time.time()
        with self._lock:
            if ok:
                self._validas.add(huella)
                return True
            self._fallos.append(ahora)
            while self._fallos and self._fallos[0] < ahora - self.ventana:
                self._fallos.popleft()
            if len(self._fallos) >= self.max_fallos and ahora >= self._bloqueado_hasta:
                self._bloqueado_hasta = ahora + self.bloqueo
                self._fallos.clear()
                _log.warning(
                    f"[AUTH] {self.nombre}: {self.max_fallos} claves incorrectas en {self.ventana}s: "
                    f"por {self.bloqueo}s se verifica de a una clave cada {self.espacio}s (posible fuerza bruta)"
                )
        return False


MAX_FALLOS = 10
_limitador_api = LimitadorClave("API", lambda p: _password_matches(p), max_fallos=MAX_FALLOS)
_limitador_panel = LimitadorClave("Panel", lambda p: _panel_password_matches(p), max_fallos=5)


def _reiniciar_bloqueo():
    _limitador_api.reiniciar()
    _limitador_panel.reiniciar()


def _clave_valida(pwd: str) -> bool:
    return _limitador_api.valida(pwd)


def clave_panel_valida(pwd: str) -> bool:
    if not PANEL_PASS_HASH:
        return False
    return _limitador_panel.valida(pwd or "")


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
