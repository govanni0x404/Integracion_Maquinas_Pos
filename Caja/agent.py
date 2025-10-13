import gc
import os
import time
import logging
import requests
import traceback
import serial.tools.list_ports
import threading
from dotenv import load_dotenv
from transbank import POSIntegrado
from logging.handlers import RotatingFileHandler

load_dotenv()

SERVER = os.environ.get("SERVER_URL", "http://localhost:5000")
CLIENT_ID = os.environ.get("CLIENT_ID", "default_client")
BOX_ID = os.environ.get("BOX_ID", "default_box")
PREFERRED_PORTS = os.environ.get("PREFERRED_PORTS", "COM7,COM6,COM8")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "1"))
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "15"))
RECONNECT_DELAY = int(os.environ.get("RECONNECT_DELAY", "5"))

# Nombre del archivo de log
log_file = "agent.log"

# Crea un manejador de logs que rota el archivo cuando alcanza 5MB, guardando 5 copias anteriores
handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5)

# Configura el sistema de logging con formato personalizado que incluye nombre del logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[handler, logging.StreamHandler()]
)
logger = logging.getLogger("Agent")

# Registra este agente en el servidor central
def register():
    url = "{}/register_agent".format(SERVER.rstrip('/'))
    body = {
        "client_id": CLIENT_ID,
        "box_id": BOX_ID,
        "meta": {
            "host": os.environ.get("COMPUTERNAME", None),
        }
    }
    try:
        r = requests.post(url, json=body, timeout=10)
        logger.info("Registro en servidor: {} {}".format(r.status_code, r.text))
    except Exception as e:
        logger.error("Error registrando agente: {}".format(e))

# Detecta automáticamente en qué puerto COM está conectado el dispositivo POS
def detect_port(preferred=None):
    ports = [p.device for p in serial.tools.list_ports.comports()]
    if not ports:
        logger.warning("No hay puertos COM disponibles en este PC.")
        return None

    # Procesa la lista de puertos preferidos (separa por comas y limpia espacios)
    pref_list = [p.strip() for p in (preferred or "").split(",") if p.strip()]
    ordered = [p for p in pref_list if p in ports] + [p for p in ports if p not in pref_list]

    for p in ordered:
        pos = None
        try:
            pos = POSIntegrado()
            if pos.open_port(p) and pos.poll():
                logger.info("POS detectado en {}".format(p))
                return p
        except Exception as e:
            logger.debug("No usable en {}: {}".format(p, e))
        finally:
            try:
                if pos:
                    pos.close_port()
            except Exception:
                pass
    logger.warning("No se detectó POS en ningún puerto.")
    return None

# Realiza una transacción de venta en el dispositivo POS
def do_sale(pos_port, amount):
    pos = None
    try:
        pos = POSIntegrado()
        if not pos.open_port(pos_port):
            return {"status": "error", "message": "No se pudo abrir {}".format(pos_port)}
        ticket = time.strftime("%H%M%S")
        logger.info("Iniciando venta en {} | Monto={} | Ticket={}".format(pos_port, amount, ticket))
        res = pos.sale(amount, ticket)
        logger.info("[Venta] Respuesta del Pos ({}): {}".format(pos_port, res))
        if res.get("response_code") in ("0","00"):
            return {"status": "success", "response": res}
        else:
            return {"status": "failed", "response": res}
    except Exception as e:
        logger.error("Error en do_sale: {}\n{}".format(e, traceback.format_exc()))
        return {"status": "error", "message": str(e)}
    finally:
        try:
            if pos:
                pos.close_port()
        except Exception:
            pass
        gc.collect()

# Ejecuta una venta con un timeout máximo para evitar bloqueos infinitos
def do_sale_with_timeout(tx_id, port, amount, timeout, result_url):
    result = {}
    
    # Función worker que ejecuta la venta en un hilo separado
    def worker():
        nonlocal result
        result = do_sale(port, amount)

    # Crea un hilo para ejecutar la venta
    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout)

    # Si el hilo sigue activo después del timeout, significa que se excedió el tiempo
    if t.is_alive():
        logger.error("Timeout en la venta con el POS")
        payload = {"tx_id": tx_id, "result": {"status": "error", "message": "Timeout en venta POS"}}
        try:
            rr = requests.post(result_url, json=payload, timeout=15)
            logger.info("[Agent] Resultado enviado por timeout: {} {}".format(rr.status_code, rr.text))
        except Exception as e:
            logger.error("[Agent] Error enviando resultado tras timeout: {}".format(e))
        return {"status": "error", "message": "Pos Timeout"}
    return result

# Bucle principal que continuamente consulta el servidor por nuevas tareas (polling)
def poll_loop():
    register()
    poll_url = "{}/poll".format(SERVER.rstrip('/'))
    result_url = "{}/result".format(SERVER.rstrip('/'))
    cycle_start = time.time()

    # Bucle infinito para continuamente consultar nuevas tareas
    while True:
        try:
            if time.time() - cycle_start > 360:
                logger.info("[Agent] Reiniciando sesion de polling para evitar colgaduras")
                register()
                cycle_start = time.time()

            params = {"client_id": CLIENT_ID, "box_id": BOX_ID}
            r = requests.get(poll_url, params=params, timeout=POLL_TIMEOUT+5)
            if r.status_code != 200:
                logger.warning("Error en poll: {} {}".format(r.status_code, r.text))
                time.sleep(RECONNECT_DELAY)
                continue

            data = r.json()
            task = data.get("task")
            if not task:
                time.sleep(POLL_INTERVAL)
                continue

            tx_id = task.get("tx_id")
            pos_id = task.get("pos_id")
            amount = task.get("amount")
            logger.info("Tarea recibida: tx={} pos_id={} monto={}".format(tx_id, pos_id, amount))

            puerto = detect_port(PREFERRED_PORTS)
            if not puerto:
                result = {"status": "error", "message": "No se detectó POS conectado"}
            else:
                result = do_sale_with_timeout(tx_id, puerto, amount, POLL_TIMEOUT, result_url)

            payload = {"tx_id": tx_id, "result": result}
            try:
                rr = requests.post(result_url, json=payload, timeout=15)
                logger.info("[Agent] Resultado enviado: {} {}".format(rr.status_code, rr.text))
            except Exception as e:
                logger.error("[Agent] Error enviando resultado final: {}".format(e))

        except requests.exceptions.ReadTimeout:
            logger.debug("Timeout en poll (normal). Reintentando...")
            continue
        except Exception as e:
            logger.error("Error en poll_loop: {}\n{}".format(e, traceback.format_exc()))
            time.sleep(RECONNECT_DELAY)

if __name__ == "__main__":
    while True:
        try:
            logger.info("Iniciando agente para CLIENT_ID={}, BOX_ID={}, SERVER={}".format(CLIENT_ID, BOX_ID, SERVER))
            poll_loop()
        except Exception as e:
            logger.error("[MainLoop] Error fatal, reiniciando agente: {}".format(e))
            time.sleep(5)