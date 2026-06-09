import base64
import hmac
from functools import wraps
from flask import request, Response
from config.settings import API_AUTH_USER, API_AUTH_PASS
from core.logging_config import logger as _log

def _unauthorized():
    return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="POS Gateway"'})

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
        pass_ok = hmac.compare_digest(pwd, API_AUTH_PASS)
        if not user_ok or not pass_ok:
            _log.warning(
                f"[AUTH] Credenciales inválidas — "
                f"usuario_recibido='{user}' user_ok={user_ok} pass_ok={pass_ok} "
                f"API_AUTH_USER='{API_AUTH_USER}'"
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
        if not API_AUTH_USER or not API_AUTH_PASS:
            _log.error(
                f"[AUTH] CRITICO: credenciales vacías en runtime — "
                f"API_AUTH_USER='{API_AUTH_USER}' API_AUTH_PASS_len={len(API_AUTH_PASS)}"
            )
            return _unauthorized()
        auth = request.headers.get("Authorization")
        if not check_basic_auth_header(auth):
            _log.warning(f"[AUTH] Acceso rechazado a {request.path} desde {request.remote_addr}")
            return _unauthorized()
        return fn(*args, **kwargs)
    return wrapper
