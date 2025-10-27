import os
import socket
from dotenv import load_dotenv

# Carga variables de entorno desde .env
load_dotenv()

# AUTENTICACIÓN
API_AUTH_USER = os.environ.get("API_AUTH_USER", "")
API_AUTH_PASS = os.environ.get("API_AUTH_PASS", "")

# APLICACIÓN
APP_NAME = os.environ.get("APP_NAME", "POS Gateway")
LOG_FILE = os.environ.get("LOG_FILE", "pos_gateway.log")
LOCK_FILE = os.environ.get("LOCK_FILE", "pos_gateway.lock")
HTTP_PORT = int(os.environ.get("HTTP_PORT", os.environ.get("PORT", "5005")))

# TERMINAL
ID_SUCURSAL = os.environ.get("ID_SUCURSAL", "1")
NOMBRE_CAJA = os.environ.get("NOMBRE_CAJA", socket.gethostname())
ID_TERMINAL = os.environ.get("ID_TERMINAL", os.environ.get("TERMINAL_ID", f"POS_{NOMBRE_CAJA}"))

# POS FÍSICO (TRANSBANK)
USAR_POS_FISICO = os.environ.get("USAR_POS_FISICO", "true").lower() == "true"
PUERTOS_COM = os.environ.get("PUERTOS_COM", "COM5,COM6,COM7,COM8")

# TIMEOUTS
MAX_TRANSACTION_TIME = int(os.environ.get("MAX_TRANSACTION_TIME", "90"))
TIMEOUT_SERVER = int(os.environ.get("TIMEOUT_SERVER", "120"))

# MERCADO PAGO
ALLOWED_MP = set([x.strip() for x in os.environ.get("ALLOWED_MP", "").split(",") if x.strip()])
MP_API_URL = os.environ.get("MP_API_URL", "https://api.mercadopago.com/v1/orders")


# VALIDACIÓN DE CONFIGURACIÓN
def validate_config():
    """Valida que las configuraciones críticas estén presentes"""
    warnings = []
    
    if not API_AUTH_USER or not API_AUTH_PASS:
        warnings.append("⚠️  API_AUTH_USER/API_AUTH_PASS no configurados (sin autenticación)")
    
    if USAR_POS_FISICO and not PUERTOS_COM:
        warnings.append("⚠️  USAR_POS_FISICO=true pero PUERTOS_COM vacío")
    
    return warnings