import os
import socket
from dotenv import load_dotenv

load_dotenv()

APP_NAME = os.environ.get("APP_NAME", "POS Gateway")

# Credenciales API
API_AUTH_USER = os.environ.get("API_AUTH_USER", "")
API_AUTH_PASS = os.environ.get("API_AUTH_PASS", "")

# Archivos
LOG_FILE = os.environ.get("LOG_FILE", "pos_gateway.log")
LOCK_FILE = os.environ.get("LOCK_FILE", "pos_gateway.lock")

# Puerto HTTP
HTTP_PORT = int(os.environ.get("HTTP_PORT", os.environ.get("PORT", "5005")))

# Identificación
ID_SUCURSAL = os.environ.get("ID_SUCURSAL", "1")
NOMBRE_CAJA = os.environ.get("NOMBRE_CAJA", socket.gethostname())
ID_TERMINAL = os.environ.get("ID_TERMINAL", os.environ.get("TERMINAL_ID", f"POS_{NOMBRE_CAJA}"))

# POS físico
USAR_POS_FISICO = os.environ.get("USAR_POS_FISICO", "true").lower() == "true"
PUERTOS_COM = os.environ.get("PUERTOS_COM", "COM5,COM6,COM7,COM8")

# Timeouts
MAX_TRANSACTION_TIME = int(os.environ.get("MAX_TRANSACTION_TIME", "90"))
TIMEOUT_SERVER = int(os.environ.get("TIMEOUT_SERVER", "120"))

# Mercado Pago
ALLOWED_MP = set([x.strip() for x in os.environ.get("ALLOWED_MP", "").split(",") if x.strip()])
MP_API_URL = os.environ.get("MP_API_URL", "https://api.mercadopago.com/v1/orders")

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