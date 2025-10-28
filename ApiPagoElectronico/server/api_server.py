import time
import uuid
import json
import threading
import queue
import logging
import traceback
import requests

from flask import Flask, jsonify, request
from flask_cors import CORS

from config.settings import (APP_NAME,ID_SUCURSAL, NOMBRE_CAJA, ID_TERMINAL, ALLOWED_MP,MP_API_URL, TIMEOUT_SERVER, MAX_TRANSACTION_TIME, HTTP_PORT)
from server.auth import require_basic_auth
from pos.pos_module import POSModule

logger = logging.getLogger(APP_NAME)

def process_mercadopago(terminal_id, access_token, amount, timeout=TIMEOUT_SERVER):
    """
    Procesa un pago con Mercado Pago Point.
    Retorna un dict con el resultado.
    """
    logger.info("Procesando MercadoPago terminal=%s monto=%s timeout=%s", terminal_id, amount, timeout)

    idempotency_key = str(uuid.uuid4())

    payload = {
        "type": "point",
        "external_reference": f"ext_ref_{uuid.uuid4().hex[:8]}",
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
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key
    }

    try:
        resp = requests.post(MP_API_URL, json=payload, headers=headers, timeout=15)
        data_resp = resp.json()

        logger.info("Respuesta inicial MercadoPago (%s): %s", resp.status_code, json.dumps(data_resp, ensure_ascii=False))

        if resp.status_code != 201:
            logger.warning("Error creando orden MP: %s %s", resp.status_code, data_resp)
            return {"status": "failed", "http_status": resp.status_code, "response": data_resp}

        order_id = data_resp["id"]
        logger.info("Orden MP creada: %s; esperando resultado...", order_id)

        start = time.time()
        while time.time() - start < timeout:
            check = requests.get(f"{MP_API_URL}/{order_id}", headers=headers, timeout=10)
            order_info = check.json()
            status = order_info.get("status")

            logger.info("Estado actual orden MP %s: %s -> %s", order_id, check.status_code, json.dumps(order_info, ensure_ascii=False))

            if status in ("created", "in_process", "at_terminal"):
                time.sleep(3)
                continue

            logger.info("Orden %s finalizada con estado: %s", order_id, status)
            logger.info("Respuesta final MercadoPago: %s", json.dumps(order_info, ensure_ascii=False))
            return {"status": status, "order_id": order_id, "response": order_info}

        logger.warning("Timeout esperando respuesta MP orden %s", order_id)
        return {"status": "timeout", "order_id": order_id}

    except Exception as e:
        logger.error("Error MercadoPago: %s\n%s", e, traceback.format_exc())
        return {"status": "error", "message": str(e)}


class APIServer:
    """
    Servidor Flask que:
    - Recibe peticiones HTTP (/pago, /status, etc.)
    - Maneja colas de tareas por cada caja
    - Procesa tareas locales en un worker
    - Coordina agentes remotos
    """

    def __init__(self, pos_module: POSModule):
        self.pos = pos_module
        self.app = Flask(__name__)
        CORS(self.app)

        self.queues = {}
        self.queues_lock = threading.Lock()

        self.agents = {}
        self.agents_lock = threading.Lock()

        self.tasks = {}
        self.tasks_lock = threading.Lock()

        self.busy_boxes = set()
        self.busy_lock = threading.Lock()

        self.local_worker_thread = None
        self._stop_local_worker = threading.Event()

        self.cleanup_thread = None
        self._stop_cleanup = threading.Event()

        self.setup_routes()
        self.register_local_agent()
        self.start_local_worker()
        self.start_cleanup_task()

        logger.info("APIServer inicializado")

    # ---------- Queue / Agent helpers ----------
    def ensure_queue(self, id_sucursal, nombre_caja):
        with self.queues_lock:
            if id_sucursal not in self.queues:
                self.queues[id_sucursal] = {}
            if nombre_caja not in self.queues[id_sucursal]:
                self.queues[id_sucursal][nombre_caja] = queue.Queue()
            return self.queues[id_sucursal][nombre_caja]

    def register_agent(self, id_sucursal, nombre_caja, info=None):
        with self.agents_lock:
            self.agents.setdefault(id_sucursal, {})[nombre_caja] = {
                "last_seen": time.time(),
                "info": info or {}
            }
        self.ensure_queue(id_sucursal, nombre_caja)
        with self.busy_lock:
            key = (id_sucursal, nombre_caja)
            if key in self.busy_boxes:
                with self.tasks_lock:
                    active = any(
                        t.get("id_sucursal") == id_sucursal and t.get("nombre_caja") == nombre_caja
                        for t in self.tasks.values()
                    )
                if not active:
                    self.busy_boxes.discard(key)
                    logger.info("Caja %s/%s liberada por reconexión", id_sucursal, nombre_caja)

    def register_local_agent(self):
        info = {
            "host": "local",
            "terminal_id": ID_TERMINAL,
            "usa_pos_fisico": self.pos and self.pos.is_online(),
            "local": True
        }
        self.register_agent(str(ID_SUCURSAL), str(NOMBRE_CAJA), info)
        logger.info("Agente local registrado %s/%s", ID_SUCURSAL, NOMBRE_CAJA)

    # ---------- Local worker ----------
    def start_local_worker(self):
        self._stop_local_worker.clear()

        def worker():
            logger.info("Local worker iniciado")
            q = self.ensure_queue(str(ID_SUCURSAL), str(NOMBRE_CAJA))

            while not self._stop_local_worker.is_set():
                try:
                    task = q.get(timeout=1)
                except queue.Empty:
                    continue

                try:
                    tx_id = task.get("tx_id")
                    tipo = task.get("type", "transbank")
                    custom_timeout = task.get("timeout", MAX_TRANSACTION_TIME)

                    logger.info("Procesando: tx=%s, tipo=%s, timeout=%s", tx_id, tipo, custom_timeout)

                    with self.tasks_lock:
                        if tx_id in self.tasks:
                            self.tasks[tx_id]["estado"] = "PROCESANDO"

                    if tipo == "transbank":
                        amount = task.get("amount")
                        result = self.pos.do_sale_with_timeout(amount, timeout=custom_timeout)
                        logger.info("Resultado Transbank: %s", result.get("status") if isinstance(result, dict) else result)
                    elif tipo == "mercadopago":
                        terminal_id = task.get("id_terminal") or ID_TERMINAL
                        access_token = task.get("access_token")
                        amount = task.get("amount")
                        result = process_mercadopago(terminal_id, access_token, amount, timeout=custom_timeout)
                    else:
                        result = {"status": "error", "message": "Tipo no soportado"}

                    with self.tasks_lock:
                        entry = self.tasks.get(tx_id)
                        if entry:
                            entry["result"] = result
                            if result.get("status") == "success":
                                entry["estado"] = "APROBADO"
                            elif result.get("status") == "error":
                                entry["estado"] = "ERROR"
                            elif result.get("status") == "timeout":
                                entry["estado"] = "TIMEOUT"
                            else:
                                entry["estado"] = "RECHAZADO"
                            entry["event"].set()
                            logger.info("Worker completó: tx=%s, estado=%s", tx_id, entry["estado"])

                    with self.busy_lock:
                        self.busy_boxes.discard((task.get("id_sucursal"), task.get("nombre_caja")))

                except Exception as e:
                    logger.error("Error en local worker: %s\n%s", e, traceback.format_exc())
                    try:
                        with self.tasks_lock:
                            if tx_id in self.tasks:
                                self.tasks[tx_id]["result"] = {"status": "error", "message": str(e)}
                                self.tasks[tx_id]["estado"] = "ERROR"
                                self.tasks[tx_id]["event"].set()
                    except Exception:
                        pass

        self.local_worker_thread = threading.Thread(target=worker, daemon=True)
        self.local_worker_thread.start()
        logger.info("Local worker thread iniciado")

    def stop_local_worker(self):
        self._stop_local_worker.set()
        try:
            if self.local_worker_thread:
                self.local_worker_thread.join(timeout=1)
        except Exception:
            pass
        logger.info("Local worker detenido")

    # ---------- Cleanup task ----------
    def start_cleanup_task(self):
        self._stop_cleanup.clear()

        def cleanup():
            while not self._stop_cleanup.is_set():
                time.sleep(300)  # 5 minutos
                try:
                    now = time.time()
                    max_age = 600  # 10 minutos
                    with self.tasks_lock:
                        to_delete = [tx_id for tx_id, task in self.tasks.items() if now - task.get("timestamp", now) > max_age]
                        for tx_id in to_delete:
                            self.tasks.pop(tx_id, None)
                    if to_delete:
                        logger.info("Limpieza automática: %d transacciones eliminadas", len(to_delete))
                except Exception as e:
                    logger.error("Error en cleanup task: %s", e)

        self.cleanup_thread = threading.Thread(target=cleanup, daemon=True)
        self.cleanup_thread.start()
        logger.info("Tarea de limpieza automática iniciada")

    def stop_cleanup_task(self):
        self._stop_cleanup.set()
        try:
            if self.cleanup_thread:
                self.cleanup_thread.join(timeout=1)
        except Exception:
            pass
        logger.info("Tarea de limpieza detenida")

    # ---------- HTTP routes ----------
    def setup_routes(self):
        app = self.app

        @app.route("/register_agent", methods=["POST"])
        def http_register_agent():
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")
            meta = data.get("meta", {})

            if not id_sucursal or not nombre_caja:
                return jsonify({"error": "id_sucursal y nombre_caja requeridos"}), 400

            id_s = str(id_sucursal)
            nc = str(nombre_caja)

            self.register_agent(id_s, nc, meta)
            logger.info("Agent registered: %s/%s meta=%s", id_s, nc, meta)
            return jsonify({"status": "ok"})

        @app.route("/poll", methods=["GET"])
        def http_poll():
            id_sucursal = request.args.get("id_sucursal")
            nombre_caja = request.args.get("nombre_caja")
            if not id_sucursal or not nombre_caja:
                return jsonify({"error": "id_sucursal y nombre_caja requeridos"}), 400

            self.register_agent(str(id_sucursal), str(nombre_caja))
            q = self.ensure_queue(str(id_sucursal), str(nombre_caja))
            try:
                task = q.get(timeout=2)
                logger.info("Despachando tarea -> %s/%s tx=%s", id_sucursal, nombre_caja, task.get("tx_id"))
                return jsonify({"task": task})
            except queue.Empty:
                return jsonify({"task": None, "heartbeat": True})

        @app.route("/result", methods=["POST"])
        def http_result():
            data = request.get_json(force=True, silent=True) or {}
            tx_id = data.get("tx_id")
            result = data.get("result")
            if not tx_id or result is None:
                return jsonify({"error": "tx_id y result requeridos"}), 400

            with self.tasks_lock:
                task = self.tasks.get(tx_id)
                if not task:
                    logger.warning("Resultado para tx_id desconocido: %s", tx_id)
                    return jsonify({"status": "unknown_tx"}), 404
                if task.get("result") is not None:
                    logger.warning("Resultado duplicado tx=%s", tx_id)
                    return jsonify({"status": "already_processed"}), 200
                task["result"] = result
                task["event"].set()

            logger.info("Resultado guardado tx=%s: %s", tx_id, result)
            try:
                id_s = task.get("id_sucursal")
                nc = task.get("nombre_caja")
                with self.busy_lock:
                    self.busy_boxes.discard((id_s, nc))
            except Exception:
                pass

            return jsonify({"status": "ok"})

        @app.route("/pago", methods=["POST"])
        @require_basic_auth
        def http_pago():
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = str(data.get("id_sucursal", ""))
            nombre_caja = str(data.get("nombre_caja", ""))
            pos_type = data.get("type")
            logger.info("[HTTP /pago] Petición: id_sucursal=%s, nombre_caja=%s, type=%s", id_sucursal, nombre_caja, pos_type)

            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "id_sucursal, nombre_caja y type requeridos"}), 400

            is_local = (id_sucursal == str(ID_SUCURSAL) and nombre_caja == str(NOMBRE_CAJA))

            custom_timeout = data.get("timeout")
            if custom_timeout:
                try:
                    timeout = int(custom_timeout)
                    timeout = max(30, min(timeout, 300))
                    logger.info("Timeout personalizado: %s segundos", timeout)
                except (ValueError, TypeError):
                    logger.warning("Timeout inválido, usando default")
                    timeout = TIMEOUT_SERVER
            else:
                timeout = TIMEOUT_SERVER

            # TRANSBANK FLOW
            if pos_type == "transbank":
                id_terminal = data.get("id_terminal", ID_TERMINAL)
                amount = data.get("amount")
                if amount is None:
                    return jsonify({"error": "amount requerido"}), 400

                key = (id_sucursal, nombre_caja)
                with self.busy_lock:
                    if key in self.busy_boxes:
                        logger.warning("Caja ocupada: %s/%s", id_sucursal, nombre_caja)
                        return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                    self.busy_boxes.add(key)

                tx_id = str(uuid.uuid4())

                with self.tasks_lock:
                    event = threading.Event()
                    self.tasks[tx_id] = {
                        "event": event,
                        "result": None,
                        "estado": "PENDIENTE",
                        "id_sucursal": id_sucursal,
                        "nombre_caja": nombre_caja,
                        "timestamp": time.time(),
                        "timeout": timeout
                    }

                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": id_sucursal,
                    "nombre_caja": nombre_caja,
                    "type": "transbank",
                    "id_terminal": id_terminal,
                    "amount": amount,
                    "timeout": timeout
                }

                q = self.ensure_queue(id_sucursal, nombre_caja)
                q.put(task_payload)
                logger.info("Tarea encolada: tx=%s, timeout=%s, local=%s", tx_id, timeout, is_local)

                start_wait = time.time()
                finished = event.wait(timeout=timeout)
                wait_time = time.time() - start_wait

                if not finished:
                    with self.tasks_lock:
                        self.tasks.pop(tx_id, None)
                    self.internal_free_box(id_sucursal, nombre_caja, "timeout")
                    logger.error("TIMEOUT tx=%s después de %.2fs", tx_id, wait_time)
                    return jsonify({
                        "status": "timeout",
                        "transaction_id": tx_id,
                        "message": f"Timeout {timeout}s excedido (esperó {wait_time:.1f}s)"
                    }), 504

                with self.tasks_lock:
                    entry = self.tasks.pop(tx_id, {})
                    res = entry.get("result")
                    final_estado = entry.get("estado", "DESCONOCIDO")

                with self.busy_lock:
                    self.busy_boxes.discard(key)

                logger.info("Respuesta: tx=%s, estado=%s, tiempo=%.2fs", tx_id, final_estado, wait_time)

                return jsonify({
                    "transaction_id": tx_id,
                    "result": res,
                    "estado": final_estado,
                    "tiempo_total": round(wait_time, 2)
                })

            # MERCADO PAGO FLOW
            elif pos_type == "mercadopago":
                terminal_id = data.get("terminal_id", ID_TERMINAL)
                access_token = data.get("access_token")
                amount = data.get("amount")
                if access_token is None or amount is None:
                    return jsonify({"error": "Faltan campos mercadopago"}), 400

                if ALLOWED_MP and f"{id_sucursal}:{nombre_caja}" not in ALLOWED_MP:
                    return jsonify({"status": "forbidden", "message": "Caja no autorizada"}), 403

                res = process_mercadopago(terminal_id, access_token, amount, timeout=timeout)
                return jsonify(res), 200

            else:
                return jsonify({"error": "Tipo POS no soportado"}), 400

        @app.route("/status")
        def http_status():
            with self.agents_lock:
                agents_count = sum(len(boxes) for boxes in self.agents.values())
            return jsonify({
                "status": "ok",
                "port": HTTP_PORT,
                "id_sucursal": ID_SUCURSAL,
                "nombre_caja": NOMBRE_CAJA,
                "id_terminal": ID_TERMINAL,
                "usa_pos_fisico": self.pos.is_online() if self.pos else False,
                "agents_count": agents_count,
                "current_port": self.pos.get_current_port() if self.pos else None
            })

        @app.route("/online", methods=["POST", "GET"])
        @require_basic_auth
        def http_online():
            pos_online = self.pos.is_online() if self.pos else False
            return jsonify({
                "success": True,
                "online": pos_online,
                "puerto": self.pos.get_current_port() if self.pos else None,
                "message": "POS conectado" if pos_online else "POS no detectado"
            }), 200 if pos_online else 503

        @app.route("/debug/queues")
        def debug_queues():
            with self.queues_lock:
                info = {}
                for cid, boxes in self.queues.items():
                    for box, q in boxes.items():
                        info[f"{cid}:{box}"] = {"queued": q.qsize()}
            return jsonify(info)

        @app.route("/pago/iniciar", methods=["POST"])
        @require_basic_auth
        def http_pago_iniciar():
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")
            pos_type = data.get("type")

            logger.info("[HTTP /pago/iniciar] Petición recibida: %s", json.dumps(data, ensure_ascii=False))

            if id_sucursal is not None:
                id_sucursal = str(id_sucursal)
            if nombre_caja is not None:
                nombre_caja = str(nombre_caja)

            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "id_sucursal, nombre_caja y type requeridos"}), 400

            key = (id_sucursal, nombre_caja)
            with self.busy_lock:
                if key in self.busy_boxes:
                    logger.warning("Caja ocupada: %s/%s", id_sucursal, nombre_caja)
                    return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                self.busy_boxes.add(key)

            tx_id = str(uuid.uuid4())

            custom_timeout = data.get("timeout", MAX_TRANSACTION_TIME)
            try:
                custom_timeout = max(30, min(int(custom_timeout), 300))
            except:
                custom_timeout = MAX_TRANSACTION_TIME

            if pos_type == "transbank":
                amount = data.get("amount")
                id_terminal = data.get("id_terminal", ID_TERMINAL)

                if amount is None:
                    with self.busy_lock:
                        self.busy_boxes.discard(key)
                    return jsonify({"error": "amount requerido"}), 400

                with self.tasks_lock:
                    self.tasks[tx_id] = {
                        "event": threading.Event(),
                        "result": None,
                        "estado": "PENDIENTE",
                        "id_sucursal": id_sucursal,
                        "nombre_caja": nombre_caja,
                        "timestamp": time.time(),
                        "timeout": custom_timeout,
                        "type": pos_type,
                        "amount": amount
                    }

                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": id_sucursal,
                    "nombre_caja": nombre_caja,
                    "type": "transbank",
                    "id_terminal": id_terminal,
                    "amount": amount,
                    "timeout": custom_timeout
                }

                q = self.ensure_queue(id_sucursal, nombre_caja)
                q.put(task_payload)

                logger.info("Pago iniciado tx=%s -> %s/%s (respuesta inmediata)", tx_id, id_sucursal, nombre_caja)

                return jsonify({
                    "status": "ok",
                    "transaction_id": tx_id,
                    "estado": "PENDIENTE",
                    "timeout_configurado": custom_timeout,
                    "message": "Pago iniciado. Use /pago/estado/{tx_id} para consultar resultado"
                }), 202

            else:
                with self.busy_lock:
                    self.busy_boxes.discard(key)
                return jsonify({"error": "Tipo POS no soportado"}), 400

        @app.route("/pago/estado/<tx_id>", methods=["GET"])
        @require_basic_auth
        def http_pago_estado(tx_id):
            with self.tasks_lock:
                task = self.tasks.get(tx_id)
                if not task:
                    return jsonify({"error": "Transacción no encontrada", "transaction_id": tx_id}), 404

                if task.get("estado") == "PENDIENTE" and task["result"] is None:
                    return jsonify({
                        "transaction_id": tx_id,
                        "estado": "PENDIENTE",
                        "tiempo_transcurrido": int(time.time() - task["timestamp"])
                    }), 200

                result = task.get("result")
                estado = "DESCONOCIDO"
                if result:
                    estado = "APROBADO" if result.get("status") == "success" else "RECHAZADO"
                    if result.get("status") == "error":
                        estado = "ERROR"
                task["estado"] = estado

                return jsonify({
                    "transaction_id": tx_id,
                    "estado": estado,
                    "result": result,
                    "tiempo_total": int(time.time() - task["timestamp"])
                }), 200

        @app.route("/pago/cancelar/<tx_id>", methods=["POST"])
        @require_basic_auth
        def http_pago_cancelar(tx_id):
            with self.tasks_lock:
                task = self.tasks.get(tx_id)
                if not task:
                    return jsonify({"error": "Transacción no encontrada"}), 404
                if task.get("result") is not None:
                    return jsonify({"error": "La transacción ya finalizó", "estado": task.get("estado")}), 400

                task["result"] = {"status": "cancelled", "message": "Cancelado por usuario"}
                task["estado"] = "CANCELADO"
                task["event"].set()

                id_sucursal = task.get("id_sucursal")
                nombre_caja = task.get("nombre_caja")
                self.internal_free_box(id_sucursal, nombre_caja, "cancelacion")

                logger.info("Transacción cancelada: %s", tx_id)
                return jsonify({"status": "ok", "transaction_id": tx_id, "estado": "CANCELADO"}), 200

        @app.route("/pago/limpiar", methods=["POST"])
        @require_basic_auth
        def http_pago_limpiar():
            now = time.time()
            max_age = 600  # 10 minutos
            with self.tasks_lock:
                old_txs = [tx_id for tx_id, task in self.tasks.items() if now - task["timestamp"] > max_age]
                for tx_id in old_txs:
                    self.tasks.pop(tx_id, None)
            logger.info("Limpiadas %d transacciones antiguas", len(old_txs))
            return jsonify({"status": "ok", "eliminadas": len(old_txs)}), 200

    def internal_free_box(self, id_sucursal, nombre_caja, reason=""):
        key = (id_sucursal, nombre_caja)
        with self.busy_lock:
            if key in self.busy_boxes:
                self.busy_boxes.discard(key)
                logger.info("Caja %s/%s liberada (%s)", id_sucursal, nombre_caja, reason)

    def run(self):
        logger.info("Servidor Flask arrancando en puerto %s", HTTP_PORT)
        self.app.run(host="0.0.0.0", port=HTTP_PORT, threaded=True, use_reloader=False)
