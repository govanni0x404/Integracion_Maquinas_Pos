import base64
import hmac
from functools import wraps
from flask import request, Response
import config.settings as settings

def _unauthorized():
    return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="POS Gateway"'})

def check_basic_auth_header(auth_header: str, expected_user: str, expected_pass: str) -> bool:
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        encoded = auth_header[6:]
        decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
        if ":" not in decoded:
            return False
        user, pwd = decoded.split(":", 1)
        return hmac.compare_digest(user, expected_user) and hmac.compare_digest(pwd, expected_pass)
    except Exception:
        return False

def require_basic_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        settings.refresh_settings()
        if not settings.API_AUTH_USER or not settings.API_AUTH_PASS:
            return _unauthorized()
        auth = request.headers.get("Authorization")
        if not check_basic_auth_header(auth, settings.API_AUTH_USER, settings.API_AUTH_PASS):
            return _unauthorized()
        return fn(*args, **kwargs)
    return wrapper
