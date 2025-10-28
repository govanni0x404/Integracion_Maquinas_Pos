# server/auth.py
import base64
import hmac
from functools import wraps
from flask import request, Response
from config.settings import API_AUTH_USER, API_AUTH_PASS

def _unauthorized():
    return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="POS Gateway"'})

def check_basic_auth_header(auth_header: str) -> bool:
    """Devuelve True si Authorization header es Basic y usuario/clave coinciden."""
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        encoded = auth_header[6:]
        decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
        if ":" not in decoded:
            return False
        user, pwd = decoded.split(":", 1)
        return hmac.compare_digest(user, API_AUTH_USER) and hmac.compare_digest(pwd, API_AUTH_PASS)
    except Exception:
        return False

def require_basic_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not API_AUTH_USER or not API_AUTH_PASS:
            return _unauthorized()
        auth = request.headers.get("Authorization")
        if not check_basic_auth_header(auth):
            return _unauthorized()
        return fn(*args, **kwargs)
    return wrapper
