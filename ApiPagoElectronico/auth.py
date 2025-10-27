import base64
import hmac
from functools import wraps
from flask import Response, request
from config import API_AUTH_USER, API_AUTH_PASS
from logger_config import logger


def _unauthorized():
    return Response(
        "Unauthorized - Credenciales inválidas o ausentes",
        401,
        {"WWW-Authenticate": 'Basic realm="POS Gateway"'}
    )


def check_basic_auth_header(auth_header: str) -> bool:
    if not auth_header:
        return False
    
    # Verifica que sea Basic Auth
    if not auth_header.startswith("Basic "):
        return False
    
    try:
        # Extrae la parte base64 (después de "Basic ")
        encoded = auth_header[6:]
        
        # Decodifica de base64
        decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
        
        # El formato debe ser "user:password"
        if ":" not in decoded:
            return False
        
        # Separa usuario y contraseña
        user, pwd = decoded.split(":", 1)
        
        # Compara usando hmac.compare_digest para evitar timing attacks
        user_match = hmac.compare_digest(user, API_AUTH_USER)
        pwd_match = hmac.compare_digest(pwd, API_AUTH_PASS)
        
        return user_match and pwd_match
        
    except Exception as e:
        logger.debug("Error validando auth header: %s", e)
        return False


def require_basic_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Si no hay credenciales configuradas, rechaza por seguridad
        if not API_AUTH_USER or not API_AUTH_PASS:
            logger.warning("API_AUTH_USER/API_AUTH_PASS no definidos - rechazando petición")
            return _unauthorized()
        
        # Obtiene el header de autorización
        auth = request.headers.get("Authorization")
        
        # Valida las credenciales
        if not check_basic_auth_header(auth):
            logger.warning(
                "[AUTH] Petición rechazada desde %s - Header presente: %s",
                request.remote_addr,
                bool(auth)
            )
            return _unauthorized()
        
        # Credenciales válidas - ejecuta la función original
        return fn(*args, **kwargs)
    
    return wrapper


def generate_auth_header(username: str, password: str) -> str:
    credentials = f"{username}:{password}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"