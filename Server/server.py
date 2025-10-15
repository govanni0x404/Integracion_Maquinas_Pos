import os
import uuid
import time
import logging
import traceback
import threading
import queue
from datetime import datetime
from flask import Flask, json, request, jsonify
from logging.handlers import RotatingFileHandler
from flask_cors import CORS
import requests
from dotenv import load_dotenv

# Logging (Configuración de registros/logs)
log_file = "server.log"

# Crea un manejador de logs que rota el archivo cuando alcanza 10MB, guardando 5 copias anteriores
handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)

# Configura el sistema de logging con nivel INFO, formato personalizado y dos destinos (archivo y consola)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[handler, logging.StreamHandler()]
)

app = Flask(__name__)

# CONFIGURACIÓN DE CORS
CORS(app)

# Configuración granular
#CORS(app, resources={
#     r"/*": {
#         "origins": ["https://localhost:3000", "http://localhost:3000"],
#         "methods": ["GET", "POST", "OPTIONS"],
#         "allow_headers": ["Content-Type", "Authorization"],
#         "expose_headers": ["X-Transaction-ID"],
#         "max_age": 3600
#     }
# })

# Carga las variables de entorno desde un archivo .env
load_dotenv()
TIMEOUT = int(os.environ.get("TIMEOUT", "60"))
MP_API_URL = "https://api.mercadopago.com/v1/orders"

# Obtiene y procesa la lista de clientes/cajas autorizadas para Mercado Pago
allowed_mp_raw = os.environ.get("ALLOWED_MP", "")

# Convierte la cadena en un conjunto (set) para búsquedas rápidas
ALLOWED_MP = set([x.strip() for x in allowed_mp_raw.split(",") if x.strip()])

QUEUES = {}
QUEUES_LOCK = threading.Lock()
AGENTS = {}
AGENTS_LOCK = threading.Lock()
TASKS = {}
TASKS_LOCK = threading.Lock()
BUSY_BOXES = set()
BUSY_LOCK = threading.Lock()

# Asegura que exista una cola para la combinación cliente/caja
def ensure_queue(client_id, box_id):
    with QUEUES_LOCK:
        if client_id not in QUEUES:
            QUEUES[client_id] = {}
        if box_id not in QUEUES[client_id]:
            QUEUES[client_id][box_id] = queue.Queue()
        return QUEUES[client_id][box_id]

# Registra un agente (terminal POS) como activo en el sistema
def register_agent(client_id, box_id, info=None):
    with AGENTS_LOCK:
        if client_id not in AGENTS:
            AGENTS[client_id] = {}
        AGENTS[client_id][box_id] = {"last_seen": time.time(), "info": info or {}}
    ensure_queue(client_id, box_id)

    # Verifica si la caja estaba marcada como ocupada y la libera si es necesario
    with BUSY_LOCK:
        if (client_id, box_id) in BUSY_BOXES:
            BUSY_BOXES.discard((client_id, box_id))
            logging.warning("[REGISTER_FIX] Caja {}/{} estaba marcada como ocupada, liberada por reconexión".format(client_id, box_id))

    # Verifica si hay tareas activas para la caja
    with BUSY_LOCK, TASKS_LOCK:
        active = any(
            t.get("client_id") == client_id and t.get("box_id") == box_id
            for t in TASKS.values()
        )
        # Si no hay tareas activas, libera la caja
        if not active:
            BUSY_BOXES.discard((client_id, box_id))
            logging.info("[REGISTER] Caja {}/{} liberada al reconectarse".format(client_id, box_id))

    # Actualiza el timestamp de última actividad del agente
    with AGENTS_LOCK:
        if client_id in AGENTS and box_id in AGENTS[client_id]:
           AGENTS[client_id][box_id]["last_seen"] = time.time()

# Elimina una tarea específica de la cola por su transaction ID
def clean_queue_of_task(client_id, box_id, tx_id):
    with QUEUES_LOCK:
        q = QUEUES.get(client_id, {}).get(box_id)
        if q:
            temp_list = []
            found = False
            try:
                while not q.empty():
                    task = q.get_nowait()
                    if task.get("tx_id") == tx_id:
                        found = True
                        logging.warning("[CLEAN_QUEUE] Eliminada tarea tx={} de la cola {}/{}".format(tx_id, client_id, box_id))
                        continue
                    temp_list.append(task)
            except Exception:
                pass
            for task in temp_list:
                q.put(task)
            return found
    return False

# Limpia tareas huérfanas y agentes inactivos (se ejecuta en un hilo separado)
def cleanup_stale_tasks():
    while True:
        now = time.time()
        with TASKS_LOCK:
            for tx_id, task in list(TASKS.items()):
                ts = task.get("timestamp", now)
                if now - ts > 2 * TIMEOUT:
                    TASKS.pop(tx_id, None)
                    with BUSY_LOCK:
                        key = (task.get("client_id"), task.get("box_id"))
                        BUSY_BOXES.discard(key)
                        logging.warning("[CLEANUP] Tarea huérfana eliminada y la caja liberada: {} tx={}".format(key, tx_id))
                    clean_queue_of_task(key[0], key[1], tx_id)

        # Verifica agentes inactivos y cajas que han estado ocupadas demasiado tiempo
        with AGENTS_LOCK, BUSY_LOCK:
            for client_id, boxes in list(AGENTS.items()):
                for box_id, info in list(boxes.items()):
                    key = (client_id, box_id)
                    last_time = info.get("last_task_time", 0)
                    last_seen = info.get("last_seen",0)
                    if key in BUSY_BOXES and (now - last_time > 90):
                        BUSY_BOXES.discard(key)
                        logging.warning("[CLEANUP_BUSY] Caja liberada por timeout: {}/{}".format(client_id, box_id))
                    if now - last_seen > 90:
                        logging.warning("[AGENT_TIMEOUT] Eliminando agente inactivo: {}/{}".format(client_id, box_id))
                        boxes.pop(box_id, None)
                        BUSY_BOXES.discard(key)
        time.sleep(5)

# Endpoint para que un agente (terminal POS) se registre en el sistema
@app.route("/register_agent", methods=["POST"])
def http_register_agent():
    data = request.get_json(force=True, silent=True) or {}
    client_id = data.get("client_id")
    box_id = data.get("box_id")
    meta = data.get("meta", {})
    if not client_id or not box_id:
        return jsonify({"error": "client_id y box_id son requeridos"}), 400
    register_agent(client_id, box_id, info=meta)
    logging.info("Agent registered: {}/{} meta={}".format(client_id, box_id, meta))
    return jsonify({"status": "ok"})

# Endpoint para que un agente espere nuevas tareas (polling)
@app.route("/poll", methods=["GET"])
def http_poll():
    client_id = request.args.get("client_id")
    box_id = request.args.get("box_id")
    if not client_id or not box_id:
        return jsonify({"error": "client_id y box_id requeridos"}), 400
    register_agent(client_id, box_id)
    q = ensure_queue(client_id, box_id)
    try:
        task = q.get(timeout=TIMEOUT)
        logging.info("Despachando tarea -> {}/{} tx={}".format(client_id, box_id, task.get("tx_id")))
        return jsonify({"task": task})
    except queue.Empty:
        return jsonify({"task": None, "heartbeat": True})

# Endpoint para que un agente reporte el resultado de una tarea ejecutada
@app.route("/result", methods=["POST"])
def http_result():
    data = request.get_json(force=True, silent=True) or {}
    tx_id = data.get("tx_id")
    result = data.get("result")
    if not tx_id or result is None:
        return jsonify({"error": "tx_id y result requeridos"}), 400

    # Bloquea el acceso a TASKS
    with TASKS_LOCK:
        task = TASKS.get(tx_id)
        if not task:
            logging.warning("Resultado recibido para tx_id desconocido: {}".format(tx_id))
            return jsonify({"status": "unknown_tx"}), 404
        task["result"] = result
        task["event"].set()

    # Registra que el resultado fue guardado
    logging.info("Resultado guardado tx={}: {}".format(tx_id, result))

    # Realiza limpieza y liberación de recursos
    try:
        client_id = task.get("client_id")
        box_id = task.get("box_id")
        with BUSY_LOCK:
            BUSY_BOXES.discard((client_id, box_id))
            logging.info("[RESULT] Caja liberada: {}/{}".format(client_id, box_id))
        clean_queue_of_task(client_id, box_id, tx_id)
        AGENTS.setdefault(client_id, {}).setdefault(box_id, {})["last_task_time"] = 0
    except Exception:
        pass
    return jsonify({"status": "ok"})

# Endpoint para procesar pagos (soporta Transbank y Mercado Pago)
@app.route("/pago", methods=["POST"])
def http_pago():
    data = request.get_json(force=True, silent=True) or {}
    client_id = data.get("client_id")
    box_id = data.get("box_id")
    pos_type = data.get("type")

    # Valida que los campos requeridos estén presentes
    if not client_id or not box_id or not pos_type:
        return jsonify({"error": "client_id, box_id y type son requeridos"}), 400

    # Procesamiento de pagos Transbank
    if pos_type == "transbank":
        pos_id = data.get("pos_id")
        amount = data.get("amount")
        if not pos_id or amount is None:
            return jsonify({"error": "Faltan campos transbank"}), 400

        # Verifica que el agente esté registrado
        with AGENTS_LOCK:
            agent_info = AGENTS.get(client_id, {}).get(box_id)
        if not agent_info:
            return jsonify({"status": "no_agent", "message": "Caja sin agente"}), 504

        # Clave para identificar la caja
        key = (client_id, box_id)
        with BUSY_LOCK:
            if key in BUSY_BOXES:
                logging.warning("[Pago] Caja ocupada: {}/{} , rechazando nueva venta".format(client_id, box_id))
                return jsonify({"status": "busy", "message": "Caja ocupada realizando otra transaccion"}), 429
            BUSY_BOXES.add(key)
            AGENTS.setdefault(client_id, {}).setdefault(box_id, {})["last_task_time"] = time.time()

        # Genera un ID único para la transacción
        tx_id = str(uuid.uuid4())
        event = threading.Event()
        with TASKS_LOCK:
            TASKS[tx_id] = {
                "event": event,
                "result": None,
                "client_id": client_id,
                "box_id": box_id,
                "timestamp": time.time(),
            }

        # Prepara los datos de la tarea para enviar al agente
        task_payload = {
            "tx_id": tx_id, "client_id": client_id, "box_id": box_id,
            "pos_id": pos_id, "amount": amount, "timestamp": datetime.utcnow().isoformat()
        }

        # Obtiene la cola y agrega la tarea
        q = ensure_queue(client_id, box_id)
        q.put(task_payload)

        # Espera a que el agente complete la transacción o se agote el timeout
        finished = event.wait(timeout=TIMEOUT)
        if not finished:
            logging.warning("[TIMEOUT] No hubo respuesta del agente tx={}, liberando caja {}/{}".format(tx_id, client_id, box_id))

            with TASKS_LOCK:
                TASKS.pop(tx_id, None)

            with BUSY_LOCK:
                BUSY_BOXES.discard(key)

            try:
                clean_queue_of_task(client_id, box_id, tx_id)
                q = ensure_queue(client_id, box_id)
                while not q.empty():
                    t = q.get_nowait()
                    logging.warning("[TIMEOUT_CLEAN] Tarea {} descartada de la cola {}/{}".format(t.get("tx_id"), client_id, box_id))
            except Exception as e:
                logging.error("[TIMEOUT_CLEAN] Error limpiando cola: {}".format(e))
            return jsonify({"status": "timeout", "transaction_id": tx_id}), 504

        # Extrae el resultado de la tarea completada
        with TASKS_LOCK:
            res = TASKS.pop(tx_id)["result"]
        with BUSY_LOCK:
            BUSY_BOXES.discard(key)
        clean_queue_of_task(client_id, box_id, tx_id)
        return jsonify({"transaction_id": tx_id, "result": res})

    # Procesamiento de pagos Mercado Pago
    elif pos_type == "mercadopago":
        terminal_id = data.get("terminal_id")
        access_token = data.get("access_token")
        amount = data.get("amount")
        if not terminal_id or not access_token or amount is None:
            return jsonify({"error": "Faltan campos mercadopago"}), 400

        # Crea una clave para verificar autorización
        key = "{}:{}".format(client_id, box_id)
        if key not in ALLOWED_MP:
            logging.warning("Caja o Cliente no autorizada para Mercado Pago: {}".format(key))
            return jsonify({"status": "forbidden", "message": "Caja o Cliente no autorizada para Mercado Pago"}), 403
        
        # Registra la solicitud
        logging.info("Pago MP solicitado desde {}/{} usando terminal={}".format(client_id, box_id, terminal_id))

        idempotency_key = str(uuid.uuid4())
        payload = {
            "type": "point",
            "external_reference": "ext_ref_{}".format(uuid.uuid4().hex[:8]),
            "expiration_time": "PT16M",
            "transactions": {"payments": [{"amount": str(amount)}]},
            "config": {
                "point": {
                    "terminal_id": terminal_id,
                    "print_on_terminal": "no_ticket"
                }
            },
            "description": "Venta POS"
        }
        headers = {
            "Authorization": "Bearer {}".format(access_token),
            "Content-Type": "application/json",
            "X-Idempotency-Key": idempotency_key
        }

        try:
            resp = requests.post(MP_API_URL, json=payload, headers=headers, timeout=15)
            data_resp = resp.json()
            if resp.status_code != 201:
                logging.warning("Error creando orden MP: {} {}".format(resp.status_code, data_resp))
                return jsonify({"status": "failed", "http_status": resp.status_code, "response": data_resp}), 400

            order_id = data_resp["id"]
            logging.info("Orden MP creada: {}, esperando resultado...".format(order_id))

            # Inicia un bucle para consultar el estado de la orden
            start = time.time()
            while time.time() - start < TIMEOUT:
                check = requests.get("{}/{}".format(MP_API_URL, order_id), headers=headers, timeout=10)
                order_info = check.json()
                status = order_info.get("status")

                # Si la orden aún está en proceso, espera 3 segundos y vuelve a consultar
                if status in ["created", "in_process", "at_terminal"]:
                    time.sleep(3)
                    continue

                logging.info("Orden {} finalizó con estado: {}".format(order_id, status))
                response = {
                    "status": status,
                    "order_id": order_id,
                    "response": order_info
                }
                logging.info("Respuesta enviada al cliente: {}".format(json.dumps(response, ensure_ascii=False)))
                return jsonify(response), 200

        except Exception as e:
            logging.error("Error Mercado Pago: {}\n{}".format(e, traceback.format_exc()))
            return jsonify({"status": "error", "message": str(e)}), 500

    else:
        return jsonify({"error": "POS no soportado"}), 400

# Endpoint de debug para ver el estado actual del sistema
@app.route("/debug/status")
def debug_status():
    with BUSY_LOCK, QUEUES_LOCK:
        status = {}
        for client_id, boxes in QUEUES.items():
            for box_id, q in boxes.items():
                key = (client_id, box_id)
                status["{}:{}".format(client_id, box_id)] = {
                    "busy": key in BUSY_BOXES,
                    "cola_tareas": [t.get("tx_id") for t in list(q.queue)]
                }
        return jsonify(status)

# Endpoint de debug para liberar forzadamente una caja
@app.route("/debug/force_free")
def debug_force_free():
    client_id = request.args.get("client_id")
    box_id = request.args.get("box_id")
    if not client_id or not box_id:
        return jsonify({"error": "client_id y box_id requeridos"}), 400
    key = (client_id, box_id)
    with BUSY_LOCK:
        BUSY_BOXES.discard(key)
    with QUEUES_LOCK:
        q = QUEUES.get(client_id, {}).get(box_id)
        if q:
            while not q.empty():
                q.get_nowait()
    return jsonify({"status": "force_freed", "client_id": client_id, "box_id": box_id})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    threading.Thread(target=cleanup_stale_tasks, daemon=True).start()
    app.run(host="0.0.0.0", port=port, threaded=True)