import time
import uuid
import traceback
import requests
from config import MP_API_URL, TIMEOUT_SERVER
from logger_config import logger


def process_mercadopago(terminal_id, access_token, amount, timeout=TIMEOUT_SERVER):
    
    logger.info("MercadoPago: terminal=%s monto=%s timeout=%s", 
                terminal_id, amount, timeout)
    
    # Genera clave de idempotencia única
    idempotency_key = str(uuid.uuid4())
    
    # Payload de la orden
    payload = {
        "type": "point",
        "external_reference": f"ext_ref_{uuid.uuid4().hex[:8]}",
        "expiration_time": "PT16M",
        "transactions": {
            "payments": [{"amount": str(amount)}]
        },
        "config": {
            "point": {
                "terminal_id": terminal_id,
                "print_on_terminal": "no_ticket"
            }
        },
        "description": "Venta POS"
    }
    
    # Headers con autenticación
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key
    }
    
    try:
        # 1. Crea la orden
        logger.info("Creando orden MP...")
        resp = requests.post(
            MP_API_URL,
            json=payload,
            headers=headers,
            timeout=15
        )
        data_resp = resp.json()
        
        # Verifica respuesta
        if resp.status_code != 201:
            logger.warning("Error creando orden MP: %s %s", 
                          resp.status_code, data_resp)
            return {
                "status": "failed",
                "http_status": resp.status_code,
                "response": data_resp
            }
        
        order_id = data_resp["id"]
        logger.info("Orden MP creada: %s", order_id)
        
        # 2. Polling: espera que el usuario complete el pago
        logger.info("Esperando pago del usuario (timeout=%ss)...", timeout)
        start = time.time()
        
        while time.time() - start < timeout:
            # Consulta el estado de la orden
            check = requests.get(
                f"{MP_API_URL}/{order_id}",
                headers=headers,
                timeout=10
            )
            order_info = check.json()
            status = order_info.get("status")
            
            logger.debug("Estado orden %s: %s", order_id, status)
            
            # Si aún está en proceso, espera y reintenta
            if status in ("created", "in_process", "at_terminal"):
                time.sleep(3)
                continue
            
            # Orden finalizada
            logger.info("Orden %s finalizada con estado: %s", order_id, status)
            return {
                "status": status,
                "order_id": order_id,
                "response": order_info
            }
        
        # Timeout alcanzado
        logger.warning("Timeout esperando respuesta MP orden %s (timeout=%ss)", 
                      order_id, timeout)
        return {
            "status": "timeout",
            "order_id": order_id,
            "message": f"Timeout {timeout}s excedido"
        }
    
    except requests.exceptions.Timeout:
        logger.error("Timeout en request a MP")
        return {
            "status": "error",
            "message": "Timeout conectando con Mercado Pago"
        }
    
    except requests.exceptions.RequestException as e:
        logger.error("Error de conexión MP: %s", e)
        return {
            "status": "error",
            "message": f"Error de conexión: {str(e)}"
        }
    
    except Exception as e:
        logger.error("Error MercadoPago: %s\n%s", e, traceback.format_exc())
        return {
            "status": "error",
            "message": str(e)
        }


def validate_mp_credentials(access_token):
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Endpoint para validar token
        resp = requests.get(
            "https://api.mercadopago.com/users/me",
            headers=headers,
            timeout=10
        )
        
        if resp.status_code == 200:
            data = resp.json()
            return {
                "valid": True,
                "user_id": data.get("id"),
                "email": data.get("email")
            }
        else:
            return {
                "valid": False,
                "error": f"Token inválido: {resp.status_code}"
            }
    
    except Exception as e:
        return {
            "valid": False,
            "error": str(e)
        }