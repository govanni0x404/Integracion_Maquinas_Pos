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
import requests
from flask_cors import CORS
from dotenv import load_dotenv

# Logging (Configuración de registros/logs)
log_file = "server.log"
handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[handler, logging.StreamHandler()]
)

app = Flask(__name__)

CORS(app)

load_dotenv()
TIMEOUT = int(os.environ.get("TIMEOUT", "120"))
MP_API_URL = "https://api.mercadopago.com/v1/orders"

allowed_mp_raw = os.environ.get("ALLOWED_MP", "")
ALLOWED_MP = set([x.strip() for x in allowed_mp_raw.split(",") if x.strip()])

QUEUES = {}
QUEUES_LOCK = threading.Lock()
AGENTS = {}
AGENTS_LOCK = threading.Lock()
TASKS = {}
TASKS_LOCK = threading.Lock()
BUSY_BOXES = set()
BUSY_LOCK = threading.Lock()

def ensure_queue(client_id, box_id):
    with QUEUES_LOCK:
        if client_id not in QUEUES:
            QUEUES[client_id] = {}
        if box_id not in QUEUES[client_id]:
            QUEUES[client_id][box_id] = queue.Queue()
        return QUEUES[client_id][box_id]

def register_agent(client_id, box_id, info=None):
    with AGENTS_LOCK:
        if client_id not in AGENTS:
            AGENTS[client_id] = {}
        AGENTS[client_id][box_id] = {"last_seen": time.time(), "info": info or {}}
    ensure_queue(client_id, box_id)

    with BUSY_LOCK:
        if (client_id, box_id) in BUSY_BOXES:
            BUSY_BOXES.discard((client_id, box_id))
            logging.warning("[REGISTER_FIX] Caja {}/{} estaba marcada como ocupada, liberada por reconexión".format(client_id, box_id))

    with BUSY_LOCK, TASKS_LOCK:
        active = any(
            t.get("client_id") == client_id and t.get("box_id") == box_id
            for t in TASKS.values()
        )
        if not active:
            BUSY_BOXES.discard((client_id, box_id))
            logging.info("[REGISTER] Caja {}/{} liberada al reconectarse".format(client_id, box_id))

    with AGENTS_LOCK:
        if client_id in AGENTS and box_id in AGENTS[client_id]:
           AGENTS[client_id][box_id]["last_seen"] = time.time()

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

def internal_free_box(client_id, box_id, reason=""):
    key = (client_id, box_id)
    with BUSY_LOCK:
        if key in BUSY_BOXES:
            BUSY_BOXES.discard(key)
            logging.info("[FREE_BOX] Caja {}/{} liberada {}".format(client_id, box_id, reason))
    
    with QUEUES_LOCK:
        q = QUEUES.get(client_id, {}).get(box_id)
        if q:
            while not q.empty():
                try:
                    q.get_nowait()
                except:
                    pass

def cleanup_stale_tasks():
    while True:
        now = time.time()
        
        with QUEUES_LOCK, BUSY_LOCK, TASKS_LOCK:
            for client_id, boxes in list(QUEUES.items()):
                for box_id, q in list(boxes.items()):
                    key = (client_id, box_id)
                    if key in BUSY_BOXES and not q.empty():
                        task_found = any(
                            t.get("client_id") == client_id and t.get("box_id") == box_id
                            for t in TASKS.values()
                        )
                        
                        if not task_found:
                            try:
                                task = q.get_nowait()
                                tx_id = task.get("tx_id")
                                age = now - TASKS.get(tx_id, {}).get("timestamp", now)
                                logging.error("[ORPHAN_TASK] Tarea en cola pero no enviada: {}/{} tx={} (age={}s)".format(
                                    client_id, box_id, tx_id, int(age)))
                                
                                if tx_id in TASKS:
                                    if age > 30:
                                        TASKS.pop(tx_id)
                                        BUSY_BOXES.discard(key)
                                        logging.warning("[ORPHAN_TIMEOUT] Tarea descartada por timeout: {}/{} tx={}".format(
                                            client_id, box_id, tx_id))
                                    else:
                                        q.put(task)
                                        logging.info("[ORPHAN_RETRY] Tarea reencolada: {}/{} tx={}".format(
                                            client_id, box_id, tx_id))
                            except queue.Empty:
                                pass
        
        with TASKS_LOCK:
            for tx_id, task in list(TASKS.items()):
                ts = task.get("timestamp", now)
                if now - ts > 2 * TIMEOUT:
                    TASKS.pop(tx_id, None)
                    with BUSY_LOCK:
                        key = (task.get("client_id"), task.get("box_id"))
                        BUSY_BOXES.discard(key)
                        logging.warning("[CLEANUP] Tarea huérfana eliminada y caja liberada: {} tx={}".format(key, tx_id))
                    clean_queue_of_task(key[0], key[1], tx_id)

        with AGENTS_LOCK, BUSY_LOCK:
            for client_id, boxes in list(AGENTS.items()):
                for box_id, info in list(boxes.items()):
                    key = (client_id, box_id)
                    last_time = info.get("last_task_time", 0)
                    last_seen = info.get("last_seen",0)
                    
                    if key in BUSY_BOXES and last_time > 0 and (now - last_time > 60):
                        BUSY_BOXES.discard(key)
                        logging.warning("[CLEANUP_BUSY] Caja liberada por timeout: {}/{} ({}s sin respuesta)".format(
                            client_id, box_id, int(now - last_time)))
                    
                    if now - last_seen > 90:
                        logging.warning("[AGENT_TIMEOUT] Eliminando agente inactivo: {}/{}".format(client_id, box_id))
                        boxes.pop(box_id, None)
                        BUSY_BOXES.discard(key)
        time.sleep(3)

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

@app.route("/poll", methods=["GET"])
def http_poll():
    client_id = request.args.get("client_id")
    box_id = request.args.get("box_id")
    if not client_id or not box_id:
        return jsonify({"error": "client_id y box_id requeridos"}), 400
    register_agent(client_id, box_id)
    q = ensure_queue(client_id, box_id)
    
    try:
        task = q.get(timeout=2)
        logging.info("Despachando tarea -> {}/{} tx={}".format(client_id, box_id, task.get("tx_id")))
        return jsonify({"task": task})
    except queue.Empty:
        if not q.empty():
            logging.error("[POLL] ALERTA: Hay tareas en cola para {}/{} pero queue.get() falló".format(client_id, box_id))
        return jsonify({"task": None, "heartbeat": True})

@app.route("/result", methods=["POST"])
def http_result():
    data = request.get_json(force=True, silent=True) or {}
    tx_id = data.get("tx_id")
    result = data.get("result")
    if not tx_id or result is None:
        return jsonify({"error": "tx_id y result requeridos"}), 400

    with TASKS_LOCK:
        task = TASKS.get(tx_id)
        if not task:
            logging.warning("Resultado recibido para tx_id desconocido: {}".format(tx_id))
            return jsonify({"status": "unknown_tx"}), 404
        
        if task.get("result") is not None:
            logging.warning("Resultado duplicado recibido para tx={} (ya procesado)".format(tx_id))
            return jsonify({"status": "already_processed"}), 200
        
        task["result"] = result
        task["event"].set()

    logging.info("Resultado guardado tx={}: {}".format(tx_id, result))

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

@app.route("/pago", methods=["POST"])
def http_pago():
    data = request.get_json(force=True, silent=True) or {}
    client_id = data.get("client_id")
    box_id = data.get("box_id")
    pos_type = data.get("type")

    if not client_id or not box_id or not pos_type:
        return jsonify({"error": "client_id, box_id y type son requeridos"}), 400

    if pos_type == "transbank":
        pos_id = data.get("pos_id")
        amount = data.get("amount")
        if not pos_id or amount is None:
            return jsonify({"error": "Faltan campos transbank"}), 400

        with AGENTS_LOCK:
            agent_info = AGENTS.get(client_id, {}).get(box_id)
        if not agent_info:
            return jsonify({"status": "no_agent", "message": "Caja sin agente"}), 504

        key = (client_id, box_id)
        with BUSY_LOCK:
            if key in BUSY_BOXES:
                logging.warning("[Pago] Caja ocupada: {}/{} , rechazando nueva venta".format(client_id, box_id))
                return jsonify({"status": "busy", "message": "Caja ocupada realizando otra transaccion"}), 429
            BUSY_BOXES.add(key)
            AGENTS.setdefault(client_id, {}).setdefault(box_id, {})["last_task_time"] = time.time()

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

        task_payload = {
            "tx_id": tx_id, "client_id": client_id, "box_id": box_id,
            "pos_id": pos_id, "amount": amount, "timestamp": datetime.utcnow().isoformat()
        }

        q = ensure_queue(client_id, box_id)
        q.put(task_payload)

        finished = event.wait(timeout=TIMEOUT)
        if not finished:
            logging.warning("[TIMEOUT] No hubo respuesta del agente tx={}, liberando caja {}/{}. Timeout: {}s".format(
                tx_id, client_id, box_id, TIMEOUT))

            with TASKS_LOCK:
                TASKS.pop(tx_id, None)

            internal_free_box(client_id, box_id, "por timeout de respuesta ({}s)".format(TIMEOUT))
            
            return jsonify({
                "status": "timeout", 
                "transaction_id": tx_id,
                "message": "El agente no respondió en {} segundos".format(TIMEOUT)
            }), 504

        with TASKS_LOCK:
            res = TASKS.pop(tx_id)["result"]
        with BUSY_LOCK:
            BUSY_BOXES.discard(key)
        clean_queue_of_task(client_id, box_id, tx_id)
        
        logging.info("[SUCCESS] Transacción completada tx={}: {}".format(tx_id, res))
        return jsonify({"transaction_id": tx_id, "result": res})

    elif pos_type == "mercadopago":
        terminal_id = data.get("terminal_id")
        access_token = data.get("access_token")
        amount = data.get("amount")
        if not terminal_id or not access_token or amount is None:
            return jsonify({"error": "Faltan campos mercadopago"}), 400

        key = "{}:{}".format(client_id, box_id)
        if key not in ALLOWED_MP:
            logging.warning("Caja o Cliente no autorizada para Mercado Pago: {}".format(key))
            return jsonify({"status": "forbidden", "message": "Caja o Cliente no autorizada para Mercado Pago"}), 403
        
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

            start = time.time()
            while time.time() - start < TIMEOUT:
                check = requests.get("{}/{}".format(MP_API_URL, order_id), headers=headers, timeout=10)
                order_info = check.json()
                status = order_info.get("status")

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

@app.route("/debug/status")
def debug_status():
    with BUSY_LOCK, QUEUES_LOCK, TASKS_LOCK:
        status = {
            "busy_boxes": list(BUSY_BOXES),
            "active_tasks": list(TASKS.keys()),
            "queues": {}
        }
        for client_id, boxes in QUEUES.items():
            for box_id, q in boxes.items():
                key = "{}:{}".format(client_id, box_id)
                status["queues"][key] = {
                    "busy": (client_id, box_id) in BUSY_BOXES,
                    "cola_tareas": [t.get("tx_id") for t in list(q.queue)]
                }
        return jsonify(status)

@app.route("/debug/force_free")
def debug_force_free():
    client_id = request.args.get("client_id")
    box_id = request.args.get("box_id")
    if not client_id or not box_id:
        return jsonify({"error": "client_id y box_id requeridos"}), 400
    
    internal_free_box(client_id, box_id, "por solicitud de debug")
    return jsonify({"status": "force_freed", "client_id": client_id, "box_id": box_id})

@app.route("/debug/agents")
def debug_agents():
    with AGENTS_LOCK:
        agents_info = {}
        for client_id, boxes in AGENTS.items():
            for box_id, info in boxes.items():
                key = "{}:{}".format(client_id, box_id)
                agents_info[key] = {
                    "last_seen": info.get("last_seen"),
                    "last_task_time": info.get("last_task_time", 0),
                    "info": info.get("info", {})
                }
        return jsonify(agents_info)

@app.route("/debug/stuck_boxes")
def debug_stuck_boxes():
    stuck = []
    with BUSY_LOCK, TASKS_LOCK, QUEUES_LOCK:
        for client_id, box_id in list(BUSY_BOXES):
            task_found = any(
                t.get("client_id") == client_id and t.get("box_id") == box_id
                for t in TASKS.values()
            )
            
            queue_obj = QUEUES.get(client_id, {}).get(box_id)
            queue_has_tasks = queue_obj and not queue_obj.empty()
            
            if not task_found and not queue_has_tasks:
                stuck.append({
                    "client_id": client_id,
                    "box_id": box_id,
                    "status": "STUCK - Sin tarea ni en cola"
                })
            elif not task_found and queue_has_tasks:
                stuck.append({
                    "client_id": client_id,
                    "box_id": box_id,
                    "status": "STUCK - Tarea en cola pero no despachada"
                })
    
    if stuck:
        logging.warning("[STUCK_BOXES] Encontradas cajas pegadas: {}".format(stuck))
        for item in stuck:
            internal_free_box(item["client_id"], item["box_id"], "auto-liberada por detección de stuck box")
    
    return jsonify({"stuck_boxes": stuck, "cleaned": len(stuck)})

@app.route("/debug/tasks")
def debug_tasks():
    with TASKS_LOCK:
        tasks_info = {}
        for tx_id, task in TASKS.items():
            age = time.time() - task.get("timestamp", time.time())
            tasks_info[tx_id] = {
                "client_id": task.get("client_id"),
                "box_id": task.get("box_id"),
                "age_seconds": int(age),
                "completed": task.get("event").is_set(),
                "timestamp": task.get("timestamp")
            }
        return jsonify(tasks_info)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    threading.Thread(target=cleanup_stale_tasks, daemon=True).start()
    app.run(host="0.0.0.0", port=port, threaded=True)