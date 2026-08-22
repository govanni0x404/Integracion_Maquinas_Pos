import time
import uuid
import json
import threading
import queue
import logging
import traceback
import urllib.request
import urllib.error

from flask import Flask, jsonify, request
from flask_cors import CORS

from config.settings import (
    APP_NAME,
    ID_SUCURSAL,
    NOMBRE_CAJA,
    ID_TERMINAL,
    MP_API_URL,
    MP_ACCESS_TOKEN,
    MP_TERMINAL_ID,
    ALLOW_MP_TOKEN_IN_REQUEST,
    ALLOWED_ORIGINS,
    TIMEOUT_SERVER,
    MAX_TRANSACTION_TIME,
    HTTP_PORT,
)
from server.auth import require_basic_auth
from server.panel_html import PANEL_HTML
from pos.pos_module import POSModule
from core import transaction_store as tx_store
from core.business_logging import getnet_log, transbank_log, mercadopago_log, fmt_monto


logger = logging.getLogger(APP_NAME)

def _http_json(method: str, url: str, headers: dict | None = None, payload: dict | None = None, timeout: int = 15):
    data = None
    req_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, method=method.upper(), headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            body = resp.read()
    except urllib.error.HTTPError as e:
        status = int(getattr(e, "code", 0) or 0)
        try:
            body = e.read()
        except Exception:
            body = b""
    decoded = body.decode("utf-8", errors="replace") if body else ""
    try:
        parsed = json.loads(decoded) if decoded else {}
    except Exception:
        parsed = {"raw": decoded}
    return status, parsed

def process_mercadopago(terminal_id, access_token, amount, timeout=TIMEOUT_SERVER, idempotency_key=None, external_reference=None):
    """
    Procesa un pago con Mercado Pago Point.
    Retorna un dict con el resultado.
    """
    logger.info("Procesando MercadoPago terminal=%s monto=%s timeout=%s", terminal_id, amount, timeout)
    mercadopago_log.info(f"🟢 Cobro iniciado — terminal {terminal_id} — monto {fmt_monto(amount)}")

    idempotency_key = idempotency_key or str(uuid.uuid4())

    payload = {
        "type": "point",
        "external_reference": external_reference or f"ext_ref_{uuid.uuid4().hex[:8]}",
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
        status_code, data_resp = _http_json("POST", MP_API_URL, headers=headers, payload=payload, timeout=15)

        logger.info("Respuesta inicial MercadoPago (%s): %s", status_code, json.dumps(data_resp, ensure_ascii=False))

        if status_code != 201:
            logger.warning("Error creando orden MP: %s %s", status_code, data_resp)
            mercadopago_log.info(
                f"❌ No se pudo crear la orden de cobro (terminal {terminal_id}, monto {fmt_monto(amount)}): "
                f"Mercado Pago respondió HTTP {status_code} — {data_resp}"
            )
            return {"status": "failed", "http_status": status_code, "response": data_resp}

        order_id = data_resp["id"]
        logger.info("Orden MP creada: %s; esperando resultado...", order_id)
        mercadopago_log.info(f"⏳ Orden creada (id {order_id}) — esperando que el cliente pague en el terminal {terminal_id}...")

        start = time.time()
        last_logged_status = None
        while time.time() - start < timeout:
            check_code, order_info = _http_json("GET", f"{MP_API_URL}/{order_id}", headers=headers, payload=None, timeout=10)
            status = order_info.get("status")

            logger.info("Estado actual orden MP %s: %s -> %s", order_id, check_code, json.dumps(order_info, ensure_ascii=False))

            if status in ("created", "in_process", "at_terminal"):
                if status != last_logged_status:
                    mercadopago_log.info(f"↪️ Orden {order_id}: estado {status} (cliente aún no completa el pago)")
                    last_logged_status = status
                time.sleep(3)
                continue

            logger.info("Orden %s finalizada con estado: %s", order_id, status)
            logger.info("Respuesta final MercadoPago: %s", json.dumps(order_info, ensure_ascii=False))
            aprobado = status in ("approved", "success")
            mercadopago_log.info(
                f"{'✅' if aprobado else '⛔'} Cobro FINALIZADO — orden {order_id} — terminal {terminal_id} — "
                f"monto {fmt_monto(amount)} — estado {status}"
            )
            return {"status": status, "order_id": order_id, "response": order_info}

        logger.warning("Timeout esperando respuesta MP orden %s", order_id)
        mercadopago_log.info(
            f"⚠️ Se agotó el tiempo de espera del cobro — orden {order_id} — terminal {terminal_id}. "
            "Estado desconocido: revisar manualmente en el portal de Mercado Pago antes de reintentar."
        )
        return {"status": "timeout", "order_id": order_id}

    except Exception as e:
        logger.error("Error MercadoPago: %s\n%s", e, traceback.format_exc())
        mercadopago_log.info(f"❌ Error inesperado procesando el cobro (terminal {terminal_id}, monto {fmt_monto(amount)}): {e}")
        return {"status": "error", "message": str(e)}


class APIServer:
    """
    Servidor Flask que:
    - Recibe peticiones HTTP (/pago, /status, etc.)
    - Maneja colas de tareas por cada caja
    - Procesa tareas locales en un worker
    - Coordina agentes remotos
    """

    def __init__(self, pos_module: POSModule, getnet_module=None):
        self.pos = pos_module
        self.getnet = getnet_module
        self.app = Flask(__name__)
        cors_origins = ALLOWED_ORIGINS or "*"
        CORS(
            self.app,
            resources={r"/*": {"origins": cors_origins}},
            allow_headers=["Content-Type", "Authorization", "X-Pos-Token"],
            methods=["GET", "POST", "OPTIONS"],
            supports_credentials=False,
        )

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

        self.getnet_monitor_thread = None
        self._stop_getnet_monitor = threading.Event()

        self.setup_routes()
        self.register_local_agent()
        self.start_local_worker()
        self.start_cleanup_task()
        self.start_getnet_reconciliation_monitor()

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
                    elif tipo == "getnet":
                        if not self.getnet:
                            result = {"status": "error", "message": "Getnet no disponible"}
                        else:
                            amount = task.get("amount")
                            ticket = task.get("ticket")
                            result = self.getnet.do_sale_with_timeout(amount, timeout=custom_timeout, ticket=ticket)
                            logger.info("Resultado Getnet: %s", result.get("status"))

                            if result.get("status") == "indeterminada":
                                # Puede que el cable ya se haya reconectado: intentamos
                                # resolverlo de inmediato antes de dejarlo pendiente.
                                recon = self.getnet.reconciliar_ticket(ticket, timeout=15)
                                logger.info("Reconciliación Getnet tx=%s ticket=%s -> %s", tx_id, ticket, recon.get("status"))
                                if recon.get("status") == "aprobado":
                                    result = {"status": "success", "response": recon.get("response"), "ticket": ticket, "reconciliado": True}
                                elif recon.get("status") == "rechazado":
                                    result = {"status": "failed", "response": recon.get("response"), "ticket": ticket,
                                              "reconciliado": True, "reconcile_message": recon.get("message")}
                                else:
                                    result["reconcile_message"] = recon.get("message")

                            if tx_store.obtener(tx_id):
                                estado_db = {
                                    "success": "APROBADO", "approved": "APROBADO",
                                    "error": "ERROR", "indeterminada": "INDETERMINADA",
                                }.get(result.get("status"), "RECHAZADO")
                                tx_store.actualizar_estado(tx_id, estado_db, raw_response=result)
                    elif tipo == "mercadopago":
                        terminal_id = task.get("terminal_id") or task.get("id_terminal") or MP_TERMINAL_ID or ID_TERMINAL
                        access_token = None
                        if ALLOW_MP_TOKEN_IN_REQUEST:
                            access_token = task.get("access_token")
                        if not access_token:
                            access_token = MP_ACCESS_TOKEN
                        amount = task.get("amount")
                        idempotency_key = f"mp_{tx_id}"
                        result = process_mercadopago(
                            terminal_id,
                            access_token,
                            amount,
                            timeout=custom_timeout,
                            idempotency_key=idempotency_key,
                            external_reference=tx_id,
                        )
                    else:
                        result = {"status": "error", "message": "Tipo no soportado"}

                    with self.tasks_lock:
                        entry = self.tasks.get(tx_id)
                        if entry:
                            entry["result"] = result
                            if result.get("status") in ("success", "approved"):
                                entry["estado"] = "APROBADO"
                            elif result.get("status") == "indeterminada":
                                entry["estado"] = "INDETERMINADA"
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
                        if tx_store.obtener(tx_id):
                            tx_store.actualizar_estado(tx_id, "ERROR", raw_response={"status": "error", "message": str(e)})
                        with self.busy_lock:
                            self.busy_boxes.discard((task.get("id_sucursal"), task.get("nombre_caja")))
                        _tipo_log = {"getnet": getnet_log, "transbank": transbank_log, "mercadopago": mercadopago_log}.get(task.get("type"))
                        if _tipo_log:
                            _tipo_log.info(f"❌ Error interno inesperado procesando la venta (tx {tx_id}): {e}")
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

    # ---------- Monitor de reconciliación automática Getnet ----------
    def start_getnet_reconciliation_monitor(self, interval=30):
        """
        Reintenta periódicamente resolver transacciones Getnet que quedaron
        INDETERMINADA (comando enviado al POS, sin confirmación) usando Command 101
        (Último Comprobante). No depende de is_online() para decidir cuándo intentar
        —esa señal queda cacheada y no siempre refleja si el cable realmente volvió—
        así que simplemente reintenta cada `interval` segundos hasta que el POS
        conteste algo útil; el intento en sí (conectar + leer) es la prueba real de
        si ya hay comunicación de nuevo.
        """
        if not self.getnet:
            return

        self._stop_getnet_monitor.clear()

        def monitor():
            logger.info("Monitor de reconciliación Getnet iniciado (cada %ss)", interval)
            while not self._stop_getnet_monitor.wait(interval):
                try:
                    pendientes = [
                        tx for tx in tx_store.listar_no_resueltas()
                        if tx.get("tipo") == "getnet" and tx.get("estado") == "INDETERMINADA"
                    ]
                    for tx in pendientes:
                        tx_id = tx["tx_id"]
                        ticket = tx.get("ticket")
                        getnet_log.info(
                            f"🔁 Monitor automático: reintentando reconciliar el ticket {ticket} (tx {tx_id})..."
                        )
                        recon = self.getnet.reconciliar_ticket(ticket, timeout=15)
                        estado_map = {"aprobado": "APROBADO", "rechazado": "RECHAZADO", "no_resuelto": "INDETERMINADA"}
                        nuevo_estado = estado_map.get(recon.get("status"), "INDETERMINADA")

                        if nuevo_estado == "INDETERMINADA":
                            continue  # sigue sin poder confirmarse, se reintenta en el próximo ciclo

                        tx_store.actualizar_estado(
                            tx_id, nuevo_estado, raw_response=recon, nota=recon.get("message"),
                            resuelto_por="monitor automático (Command 101)",
                        )
                        with self.busy_lock:
                            self.busy_boxes.discard((tx.get("id_sucursal"), tx.get("nombre_caja")))
                        logger.info("Monitor Getnet: tx=%s reconciliada automáticamente -> %s", tx_id, nuevo_estado)
                        getnet_log.info(f"✅ Monitor automático: ticket {ticket} (tx {tx_id}) reconciliado como {nuevo_estado}.")
                except Exception as e:
                    logger.error("Error en monitor de reconciliación Getnet: %s", e)

        self.getnet_monitor_thread = threading.Thread(target=monitor, daemon=True, name="GetnetReconMonitor")
        self.getnet_monitor_thread.start()

    def stop_getnet_reconciliation_monitor(self):
        self._stop_getnet_monitor.set()
        try:
            if self.getnet_monitor_thread:
                self.getnet_monitor_thread.join(timeout=1)
        except Exception:
            pass
        logger.info("Monitor de reconciliación Getnet detenido")

    # ---------- HTTP routes ----------
    def setup_routes(self):
        app = self.app

        # ── Panel de control (/panel) ─────────────────────────────
        @app.route("/")
        def root_redirect():
            from flask import redirect
            return redirect("/panel")

        @app.route("/auth/test")
        def auth_test():
            """Endpoint de diagn\u00f3stico: verifica credenciales sin bloquear. Accesible desde cualquier browser."""
            import base64
            from config.settings import API_AUTH_USER, API_AUTH_PASS

            # Credenciales configuradas en el servidor
            server_user = API_AUTH_USER
            server_pass = API_AUTH_PASS

            # Leer credenciales enviadas (si las hay)
            auth_header = request.headers.get("Authorization", "")
            client_user = None
            client_pass = None
            decode_error = None
            match = False

            if auth_header.startswith("Basic "):
                try:
                    encoded = auth_header[6:].strip()
                    decoded = base64.b64decode(encoded).decode("utf-8", errors="replace")
                    client_user, _, client_pass = decoded.partition(":")
                except Exception as e:
                    decode_error = str(e)

            if client_user is not None and client_pass is not None:
                match = (client_user == server_user and client_pass == server_pass)

            result = {
                "server": {
                    "user": server_user,
                    "user_len": len(server_user),
                    "pass_len": len(server_pass),
                    "user_repr": repr(server_user),       # muestra chars invisibles
                    "pass_sha256_8": __import__("hashlib").sha256(server_pass.encode()).hexdigest()[:8],
                },
                "client": {
                    "header_present": bool(auth_header),
                    "user": client_user,
                    "user_len": len(client_user) if client_user else 0,
                    "pass_len": len(client_pass) if client_pass else 0,
                    "user_repr": repr(client_user) if client_user else None,
                    "pass_sha256_8": __import__("hashlib").sha256(client_pass.encode()).hexdigest()[:8] if client_pass else None,
                    "decode_error": decode_error,
                },
                "match": match,
                "verdict": "CREDENCIALES CORRECTAS" if match else "CREDENCIALES INCORRECTAS o ausentes",
            }
            return jsonify(result)

        @app.route("/panel")
        def panel_ui():
            from flask import render_template_string
            import os, sys
            import platform
            from pathlib import Path
            from config.settings import (
                HTTP_PORT, ID_SUCURSAL, NOMBRE_CAJA, ID_TERMINAL,
                USAR_POS_FISICO, USAR_GETNET, MAX_TRANSACTION_TIME, TIMEOUT_SERVER
            )

            # Buscar .env — en --onefile el .env está junto al .exe
            env_path = None
            exe_dir = Path(sys.executable).parent
            for p in [
                exe_dir / ".env",                           # junto al .exe (--onefile portable)
                exe_dir.parent / ".env",                   # un nivel arriba
                exe_dir.parent.parent / ".env",            # dos niveles arriba
                Path(sys.argv[0]).resolve().parent / ".env",
                Path(os.getcwd()) / ".env",
            ]:
                if p.exists():
                    env_path = str(p)
                    break

            env_vars = {}
            if env_path:
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, _, v = line.partition("=")
                            env_vars[k.strip()] = v.strip()

            # Estado del servidor
            pos_port = self.pos.get_current_port() if self.pos else "N/A"
            mp_token_configured = bool((env_vars.get("MP_ACCESS_TOKEN", "") or "").strip())
            env_vars_ui = dict(env_vars)
            env_vars_ui.pop("MP_ACCESS_TOKEN", None)
            is_windows = platform.system() == "Windows"

            autostart_enabled = False
            if is_windows:
                try:
                    import winreg
                    run_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_READ) as k:
                        winreg.QueryValueEx(k, APP_NAME)
                        autostart_enabled = True
                except Exception:
                    autostart_enabled = False

            from core.logging_config import current_log_file

            return render_template_string(PANEL_HTML, **{
                "app_name":      APP_NAME,
                "http_port":     HTTP_PORT,
                "id_sucursal":   ID_SUCURSAL,
                "nombre_caja":   NOMBRE_CAJA,
                "id_terminal":   ID_TERMINAL,
                "usar_pos":      str(USAR_POS_FISICO).lower(),
                "usar_getnet":   str(USAR_GETNET).lower(),
                "max_tx_time":   MAX_TRANSACTION_TIME,
                "timeout":       TIMEOUT_SERVER,
                "pos_port":      pos_port,
                "env_path":      env_path or "no encontrado",
                "log_path":      current_log_file(),
                "env_vars":      env_vars_ui,
                "agents_count":  len(self.agents),
                # Mercado Pago: vacío = todas las cajas habilitadas
                "mp_allowed":    env_vars.get("ALLOWED_MP", ""),
                "mp_enabled":    env_vars.get("ALLOWED_MP", None) is not None
                                 or "ALLOWED_MP" not in env_vars,
                "mp_token_configured": mp_token_configured,
                "is_windows": is_windows,
                "autostart_enabled": autostart_enabled,
            })

        @app.route("/panel/config", methods=["POST"])
        def panel_save_config():
            """Guarda cambios al .env desde el panel web."""
            import sys, os
            from pathlib import Path
            data = request.get_json(force=True, silent=True) or {}
            data.pop("HTTP_PORT", None)

            env_path = None
            for p in [
                Path(sys.executable).parent / ".env",
                Path(sys.executable).parent.parent.parent / ".env",
                Path(sys.argv[0]).resolve().parent / ".env",
                Path(os.getcwd()) / ".env",
            ]:
                if p.exists():
                    env_path = p
                    break

            if not env_path:
                return jsonify({"ok": False, "error": ".env no encontrado"}), 404

            # Leer .env actual
            with open(env_path, encoding="utf-8") as f:
                lines = f.readlines()

            # Actualizar valores
            updated = set()
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k in data:
                        new_lines.append(f"{k}={data[k]}\n")
                        updated.add(k)
                        continue
                new_lines.append(line)

            # Añadir claves nuevas que no estaban
            for k, v in data.items():
                if k not in updated:
                    new_lines.append(f"{k}={v}\n")

            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            logger.info("Configuración actualizada vía panel: %s", list(data.keys()))
            return jsonify({"ok": True, "message": "Configuración guardada. Reinicia el servicio para aplicar."})

        @app.route("/panel/log")
        def panel_log():
            """
            Devuelve las últimas 100 líneas del log de HOY, como texto plano.
            ?tipo=general (default, técnico) | getnet | transbank | mercadopago (narrados por venta).
            Cada uno vive en un archivo por día (<tipo>-YYYY-MM-DD.log); los días
            anteriores quedan en el disco con su propio archivo, no se muestran acá.
            """
            from core.logging_config import daily_log_path, LOG_BASENAME

            tipo = request.args.get("tipo", "general")
            if tipo == "general":
                log_path = str(daily_log_path(LOG_BASENAME))
            elif tipo in ("getnet", "transbank", "mercadopago"):
                log_path = str(daily_log_path(tipo))
            else:
                return "tipo inválido", 400

            try:
                with open(log_path, encoding="utf-8") as f:
                    lines = f.readlines()[-100:]
                return "".join(lines) or "(sin entradas todavía)", 200, {"Content-Type": "text/plain; charset=utf-8"}
            except FileNotFoundError:
                return "(sin entradas todavía)", 200, {"Content-Type": "text/plain; charset=utf-8"}
            except Exception as e:
                return f"Error leyendo log: {e}", 500

        @app.route("/panel/pendientes", methods=["GET"])
        def panel_pendientes():
            """Transacciones que quedaron sin confirmar y requieren reconciliación."""
            try:
                pendientes = tx_store.listar_no_resueltas()
                return jsonify({"ok": True, "pendientes": pendientes})
            except Exception as e:
                return jsonify({"ok": False, "error": str(e), "pendientes": []}), 500

        @app.route("/panel/pendientes/<tx_id>/reconciliar", methods=["POST"])
        def panel_pendientes_reconciliar(tx_id):
            """Reconsulta al POS (Command 101) por el ticket de esta transacción."""
            tx = tx_store.obtener(tx_id)
            if not tx:
                return jsonify({"ok": False, "error": "Transacción no encontrada"}), 404
            if tx.get("tipo") != "getnet" or not self.getnet:
                return jsonify({"ok": False, "error": "Reconciliación automática solo disponible para Getnet"}), 400

            getnet_log.info(f"👤 Un operador pidió desde el panel reconsultar el ticket {tx.get('ticket')} (tx {tx_id}).")
            recon = self.getnet.reconciliar_ticket(tx.get("ticket"), timeout=15)
            estado_map = {"aprobado": "APROBADO", "rechazado": "RECHAZADO", "no_resuelto": "INDETERMINADA"}
            nuevo_estado = estado_map.get(recon.get("status"), "INDETERMINADA")
            tx_store.actualizar_estado(
                tx_id, nuevo_estado, raw_response=recon, nota=recon.get("message"),
                resuelto_por="sistema (Command 101, gatillado desde panel)" if nuevo_estado != "INDETERMINADA" else None,
            )

            if nuevo_estado != "INDETERMINADA":
                with self.busy_lock:
                    self.busy_boxes.discard((tx.get("id_sucursal"), tx.get("nombre_caja")))

            logger.info("Reconciliación manual (panel) tx=%s -> %s", tx_id, nuevo_estado)
            return jsonify({"ok": True, "estado": nuevo_estado, "detalle": recon})

        @app.route("/panel/pendientes/<tx_id>/resolver", methods=["POST"])
        def panel_pendientes_resolver(tx_id):
            """Resolución manual: el operador confirma el resultado mirando el comprobante impreso en el POS."""
            body = request.get_json(silent=True) or {}
            ok, err, code = self.resolver_transaccion(
                tx_id, body.get("estado"), resuelto_por="panel", nota=body.get("nota")
            )
            if not ok:
                return jsonify({"ok": False, "error": err}), code
            return jsonify({"ok": True, "estado": body.get("estado")})

        @app.route('/pago/resolver/<tx_id>', methods=['POST'])
        @require_basic_auth
        def pago_resolver(tx_id):
            """
            Resolución manual autenticada de una transacción PENDIENTE/INDETERMINADA
            (ej. el cajero confirma en tu sistema web mirando el comprobante del POS).
            Body: {"estado": "APROBADO"|"RECHAZADO", "resuelto_por": "<nombre/id del cajero>", "nota": "..."}
            """
            body = request.get_json(silent=True) or {}
            estado = body.get("estado")
            resuelto_por = body.get("resuelto_por") or "api"
            ok, err, code = self.resolver_transaccion(tx_id, estado, resuelto_por=resuelto_por, nota=body.get("nota"))
            if not ok:
                return jsonify({"status": "error", "message": err}), code
            return jsonify({
                "status": "ok",
                "transaction_id": tx_id,
                "estado": estado,
                "resuelto_por": resuelto_por,
            }), 200

        @app.route("/panel/com_ports", methods=["GET"])
        def panel_com_ports():
            try:
                from serial.tools import list_ports
                ports = [p.device for p in list_ports.comports()]
                ports = [p for p in ports if p]
                ports = sorted(set(ports), key=lambda x: x.lower())
                return jsonify({"ok": True, "ports": ports, "csv": ",".join(ports)})
            except Exception as e:
                return jsonify({"ok": False, "error": str(e), "ports": [], "csv": ""}), 500

        @app.route("/panel/autostart", methods=["GET", "POST"])
        def panel_autostart():
            import platform
            if platform.system() != "Windows":
                return jsonify({"ok": True, "supported": False, "enabled": False})

            def _get_command():
                import sys
                from pathlib import Path
                exe = Path(sys.executable).resolve()
                name = exe.name.lower()
                if exe.suffix.lower() == ".exe" and name not in ("python.exe", "pythonw.exe"):
                    return f"\"{str(exe)}\""
                main = Path(sys.argv[0]).resolve()
                if main.suffix.lower() == ".py":
                    pyw = exe
                    if name == "python.exe":
                        candidate = exe.parent / "pythonw.exe"
                        if candidate.exists():
                            pyw = candidate
                    if pyw.name.lower() == "python.exe":
                        return None
                    return f"\"{str(pyw)}\" \"{str(main)}\""
                return None

            run_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            value_name = APP_NAME

            try:
                import winreg
                if request.method == "GET":
                    try:
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_READ) as k:
                            winreg.QueryValueEx(k, value_name)
                            return jsonify({"ok": True, "supported": True, "enabled": True})
                    except Exception:
                        return jsonify({"ok": True, "supported": True, "enabled": False})

                data = request.get_json(force=True, silent=True) or {}
                enabled = bool(data.get("enabled"))

                if enabled:
                    cmd = _get_command()
                    if not cmd:
                        return jsonify({"ok": False, "error": "No se pudo determinar el comando de inicio automático"}), 400
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.SetValueEx(k, value_name, 0, winreg.REG_SZ, cmd)
                    return jsonify({"ok": True, "supported": True, "enabled": True})

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_SET_VALUE) as k:
                    try:
                        winreg.DeleteValue(k, value_name)
                    except FileNotFoundError:
                        pass
                return jsonify({"ok": True, "supported": True, "enabled": False})
            except Exception as e:
                logger.error("Error configurando autostart: %s", e)
                return jsonify({"ok": False, "error": str(e)}), 500

        @app.route("/panel/restart", methods=["POST"])
        def panel_restart():
            """Reinicia el servicio — multiplataforma (macOS, Linux, Windows)."""
            import sys
            import platform
            import subprocess
            import threading
            import time
            import os
            import signal
            from pathlib import Path

            is_windows = platform.system() == "Windows"
            is_mac     = platform.system() == "Darwin"

            # Buscar script de reinicio según plataforma
            bases = [
                Path(sys.executable).parent.parent.parent,  # dist/ compiled
                Path(sys.executable).parent,                 # dist/AppName/ (onedir)
            ]
            try:
                argv0 = Path(sys.argv[0]).resolve()
                if not is_mac or argv0.suffix != ".py":
                    bases.append(argv0.parent)  # desarrollo (solo si no es mac + .py)
            except Exception:
                pass

            script = None
            for base in bases:
                if is_windows:
                    candidates = ["reiniciar_windows.bat", "iniciar_windows.bat"]
                elif is_mac:
                    candidates = ["reiniciar_servicio.sh", "iniciar_servicio.sh",
                                  "reiniciar_mac.sh", "iniciar_mac.sh"]
                else:  # Linux
                    candidates = ["reiniciar_linux.sh", "iniciar_linux.sh",
                                  "reiniciar_servicio.sh"]
                for name in candidates:
                    c = base / name
                    if c.exists():
                        script = c
                        break
                if script:
                    break

            def _kill_me(delay=2.5):
                time.sleep(delay)
                os.kill(os.getpid(), signal.SIGTERM)

            if script:
                logger.info("Reinicio vía panel: ejecutando %s", script)
                env = os.environ.copy()
                env.pop("_MEIPASS2", None)
                env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                if is_windows:
                    no_window_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    subprocess.Popen(
                        ["cmd", "/c", str(script), "--silent"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                        creationflags=no_window_flag,
                    )
                else:
                    subprocess.Popen(
                        ["bash", str(script)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                        start_new_session=True,
                    )
                threading.Thread(target=_kill_me, args=(2.5,), daemon=True).start()
            else:
                # Fallback: relanzar el propio proceso
                logger.info("Reinicio vía panel: relanzando proceso directamente")
                exe  = sys.executable
                argv = sys.argv[:]

                def _relaunch():
                    time.sleep(2.0)
                    try:
                        env = os.environ.copy()
                        env.pop("_MEIPASS2", None)
                        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                        if is_windows:
                            no_window_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                            subprocess.Popen([exe] + argv, creationflags=no_window_flag, env=env)
                        elif is_mac:
                            exe_path = Path(exe).resolve()
                            exe_dir = exe_path.parent
                            parts = exe_dir.parts
                            if "Contents" in parts and "MacOS" in parts:
                                app_bundle = exe_dir.parent.parent
                                subprocess.Popen(
                                    ["open", "-n", str(app_bundle)],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    env=env,
                                    start_new_session=True,
                                )
                            else:
                                cmd = [exe] + argv
                                try:
                                    if argv and str(argv[0]).lower().endswith(".py"):
                                        cmd = [exe, str(Path(argv[0]).resolve())] + argv[1:]
                                except Exception:
                                    pass
                                subprocess.Popen(
                                    cmd,
                                    start_new_session=True,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    env=env,
                                )
                        else:
                            cmd = [exe] + argv
                            try:
                                if argv and str(argv[0]).lower().endswith(".py"):
                                    cmd = [exe, str(Path(argv[0]).resolve())] + argv[1:]
                            except Exception:
                                pass
                            subprocess.Popen(
                                cmd,
                                start_new_session=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                env=env,
                            )
                    except Exception as e:
                        logger.error("Error al relanzar: %s", e)
                    finally:
                        os.kill(os.getpid(), signal.SIGTERM)

                threading.Thread(target=_relaunch, daemon=True).start()

            return jsonify({"ok": True, "message": "Reiniciando..."}), 200

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
        
        @app.route('/pago/refund', methods=['POST'])
        @require_basic_auth
        def refund():
            try:
                data = request.get_json()
                
                if not data or 'operation_id' not in data:
                    return jsonify({
                        "status": "error",
                        "message": "Falta el campo 'operation_id' en el body"
                    }), 400
                
                operation_id = data['operation_id']
                
                # Validar que operation_id sea un número
                try:
                    operation_id = int(operation_id)
                except (ValueError, TypeError):
                    return jsonify({
                        "status": "error",
                        "message": "operation_id debe ser un número válido"
                    }), 400
                
                logger.info(f"Solicitud de anulación recibida: operation_id={operation_id}")
                
                # Ejecutar anulación con timeout
                result = self.pos.do_refund_with_timeout(operation_id)
                
                if result["status"] == "success":
                    return jsonify(result), 200
                elif result["status"] == "failed":
                    return jsonify(result), 400
                else:
                    return jsonify(result), 500
                    
            except Exception as e:
                logger.exception("Error en endpoint /api/refund")
                return jsonify({
                    "status": "error",
                    "message": str(e)
                }), 500
            
        @app.route('/pago/detalle', methods=['POST', 'GET'])
        @require_basic_auth
        def detalle():
            try:
                # Obtener parámetro print_on_pos / type del body o query string
                if request.method == 'POST':
                    data = request.get_json(silent=True) or {}
                    print_on_pos = data.get('print_on_pos', False)
                    tipo = data.get('type', 'transbank')
                else:  # GET
                    print_on_pos = request.args.get('print_on_pos', 'false').lower() == 'true'
                    tipo = request.args.get('type', 'transbank')

                logger.info(f"Solicitud de detalle recibida: type={tipo} print_on_pos={print_on_pos}")

                if tipo == 'getnet':
                    if not self.getnet:
                        return jsonify({"status": "error", "message": "POS Getnet no habilitado en esta máquina"}), 503

                    # Command 101 (Último Comprobante): consulta directa a la máquina.
                    result = self.getnet.get_last_receipt(timeout=15)
                    if result["status"] == "found":
                        return jsonify(result), 200
                    elif result["status"] == "not_found":
                        return jsonify(result), 404
                    else:
                        return jsonify(result), 500

                # Transbank (comportamiento original)
                result = self.pos.do_details_with_timeout(print_on_pos)

                if result["status"] == "success":
                    return jsonify(result), 200
                elif result["status"] == "failed":
                    return jsonify(result), 400
                else:
                    return jsonify(result), 500

            except Exception as e:
                logger.exception("Error en endpoint /pago/detalle")
                return jsonify({
                    "status": "error",
                    "message": str(e)
                }), 500

        @app.route("/pago", methods=["POST", "GET"])
        @require_basic_auth
        def http_pago():
            if request.method == "GET":
                data = request.args.to_dict()
            else:
                data = request.get_json(force=True, silent=True) or {}
            id_sucursal = str(data.get("id_sucursal"))
            nombre_caja = str(data.get("nombre_caja"))
            pos_type = data.get("type")
            logger.info("[HTTP /pago] Petición (%s): id_sucursal=%s, nombre_caja=%s, type=%s", request.method, id_sucursal, nombre_caja, pos_type)

            # Validar solo id_sucursal (nombre_caja puede ser cualquier caja del cliente)
            if id_sucursal != str(ID_SUCURSAL):
                return jsonify({
                    "status": "forbidden",
                    "message": f"El id_sucursal '{id_sucursal}' no corresponde a este servicio (esperado: {ID_SUCURSAL})."
                }), 403

            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "Es necesario el id_sucursal, nombre_caja y el tipo(type) de pos"}), 400

            # is_local: la petición viene de la caja configurada en .env
            is_local = (nombre_caja == str(NOMBRE_CAJA))

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

            if pos_type == "getnet":
                if not self.getnet:
                    return jsonify({
                        "error": "POS Getnet no habilitado en esta máquina"
                    }), 503
                
                # Validar campos obligatorios para Getnet
                terminal_id = data.get("terminal_id")
                amount = data.get("amount")
                custom_timeout = data.get("timeout")
                
                # Validaciones obligatorias
                if terminal_id is None:
                    return jsonify({"error": "Es necesario el terminal_id de Getnet"}), 400
                if amount is None:
                    return jsonify({"error": "Es necesario el motno de venta de Getnet"}), 400
                if custom_timeout is None:
                    return jsonify({"error": "Es necesario el timeout de Getnet"}), 400
                
                # Validar rango de timeout
                try:
                    timeout = int(custom_timeout)
                    timeout = max(30, min(timeout, 300))
                    logger.info("Timeout Getnet: %s segundos", timeout)
                except (ValueError, TypeError):
                    return jsonify({"error": "timeout debe ser un número válido"}), 400
                
                # Validar que terminal_id coincida con el del .env
                if terminal_id != ID_TERMINAL:
                    return jsonify({
                        "status": "forbidden",
                        "message": f"El terminal_id no coincide con las credenciales de configuración"
                    }), 403

                runner_id_sucursal = str(ID_SUCURSAL)
                runner_nombre_caja = str(NOMBRE_CAJA)

                # Si una venta anterior en esta caja quedó sin confirmar (PENDIENTE o
                # INDETERMINADA), no se permite intentar una nueva hasta resolverla:
                # el cliente pudo haber pagado ya en el POS y no queremos cobrarle
                # dos veces. Esta verificación es persistente (sobrevive un reinicio
                # del servicio), a diferencia de busy_boxes que es solo en memoria.
                pendiente = tx_store.hay_pendiente_sin_resolver(runner_id_sucursal, runner_nombre_caja)
                if pendiente:
                    logger.warning("Venta Getnet bloqueada: transacción previa sin resolver tx=%s", pendiente)
                    getnet_log.info(
                        f"🔒 Venta rechazada por el sistema: la caja {runner_nombre_caja} tiene una transacción "
                        f"anterior ({pendiente}) sin confirmar. No se le pidió nada al POS. "
                        "Debe resolverse desde el panel antes de poder vender de nuevo."
                    )
                    return jsonify({
                        "status": "unresolved_previous_transaction",
                        "message": "Hay una venta Getnet previa en esta caja sin confirmar. "
                                    "Debe resolverse desde el panel antes de intentar una nueva venta.",
                        "pending_transaction_id": pendiente,
                    }), 409

                key = (runner_id_sucursal, runner_nombre_caja)
                with self.busy_lock:
                    if key in self.busy_boxes:
                        logger.warning("Caja ocupada (runner): %s/%s", runner_id_sucursal, runner_nombre_caja)
                        getnet_log.info(
                            f"🔁 Venta rechazada: la caja {runner_nombre_caja} ya tiene otra venta Getnet en curso "
                            f"(monto {fmt_monto(amount)} intentado)."
                        )
                        return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                    self.busy_boxes.add(key)

                tx_id = str(uuid.uuid4())
                # TicketNumber ("Número de Boleta") del protocolo Getnet: tiene que ser
                # puramente numérico. En pruebas con hardware real, tickets de 22 y 13
                # dígitos (milisegundos desde epoch) se ignoraban en silencio, mientras que
                # uno de 10 dígitos (segundos desde epoch) sí funcionó — el firmware del
                # POS parece guardar este campo como entero de 32 bits internamente (máx.
                # 2.147.483.647) aunque el protocolo lo declare como string: milisegundos
                # desde epoch desborda ese rango, segundos desde epoch no. Se usa segundos.
                ticket = str(int(time.time()))

                tx_store.registrar_intento(
                    tx_id=tx_id,
                    tipo="getnet",
                    ticket=ticket,
                    terminal_id=terminal_id,
                    id_sucursal=runner_id_sucursal,
                    nombre_caja=runner_nombre_caja,
                    monto=amount,
                    client_id_sucursal=id_sucursal,
                    client_nombre_caja=nombre_caja,
                )

                with self.tasks_lock:
                    event = threading.Event()
                    self.tasks[tx_id] = {
                        "event": event,
                        "result": None,
                        "estado": "PENDIENTE",
                        "id_sucursal": runner_id_sucursal,
                        "nombre_caja": runner_nombre_caja,
                        "client_id_sucursal": id_sucursal,
                        "client_nombre_caja": nombre_caja,
                        "timestamp": time.time(),
                        "timeout": timeout
                    }

                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": runner_id_sucursal,
                    "nombre_caja": runner_nombre_caja,
                    "client_id_sucursal": id_sucursal,
                    "client_nombre_caja": nombre_caja,
                    "type": "getnet",
                    "terminal_id": terminal_id,
                    "amount": amount,
                    "timeout": timeout,
                    "ticket": ticket,
                }

                q = self.ensure_queue(runner_id_sucursal, runner_nombre_caja)
                q.put(task_payload)
                logger.info(
                    "Tarea Getnet encolada: tx=%s ticket=%s client=%s/%s runner=%s/%s",
                    tx_id,
                    ticket,
                    id_sucursal,
                    nombre_caja,
                    runner_id_sucursal,
                    runner_nombre_caja,
                )

                start_wait = time.time()
                finished = event.wait(timeout=timeout)
                wait_time = time.time() - start_wait

                if not finished:
                    # OJO: a propósito NO se borra la tarea de self.tasks ni se libera
                    # la caja acá. El worker sigue procesándola en background (el POS
                    # puede responder tarde, o el módulo Getnet puede quedar intentando
                    # reconciliar) y su resultado real no debe perderse. Use
                    # /pago/estado/<tx_id> para conocer el resultado final.
                    #
                    # 409 (no 504): no es "reintenta más tarde", es "no sabemos qué pasó,
                    # no reintentes a ciegas" — el cliente puede haber pagado ya en el POS.
                    logger.error("DESCONOCIDO Getnet tx=%s después de %.2fs (sigue en proceso)", tx_id, wait_time)
                    return jsonify({
                        "status": "desconocido",
                        "transaction_id": tx_id,
                        "message": "Estado desconocido: verificar comprobante en la máquina antes de reintentar. "
                                    f"Consulte /pago/estado/{tx_id} para conocer el resultado final una vez disponible.",
                    }), 409

                with self.tasks_lock:
                    entry = self.tasks.pop(tx_id, {})
                    res = entry.get("result")
                    final_estado = entry.get("estado", "DESCONOCIDO")

                with self.busy_lock:
                    self.busy_boxes.discard(key)

                logger.info("Respuesta Getnet: tx=%s, estado=%s, tiempo=%.2fs",
                            tx_id, final_estado, wait_time)

                return jsonify({
                    "transaction_id": tx_id,
                    "result": res,
                    "estado": final_estado,
                    "tiempo_total": round(wait_time, 2)
                })

            # TRANSBANK FLOW
            elif pos_type == "transbank":
                
                #validar campos obligatorios
                terminal_id_transbank = data.get("terminal_id")
                amount_transbank = data.get("amount")
                timeout_transbank = data.get("timeout")
                
                #validaciones obligatorias
                if terminal_id_transbank is None:
                    return jsonify({"error": "Es necesario el terminal_id de Transbank"}), 400
                if amount_transbank is None:
                    return jsonify({"error": "Es necesario el monto de venta de Transbank"}), 400
                if timeout_transbank is None:
                    return jsonify({"error": "Es necesario el timeout de Transbank"}), 400

                #validar rango de timeout
                try:
                    timeout_transbank = int(timeout_transbank)
                    timeout_transbank = max(30, min(timeout_transbank, 300))
                    logger.info("Timeout Transbank: %s segundos", timeout_transbank)
                except (ValueError, TypeError):
                    return jsonify({"error": "timeout debe ser un número válido"}), 400

                #validar que terminal_id coincida con el del .env
                if terminal_id_transbank != ID_TERMINAL:
                    return jsonify({
                        "status": "forbidden",
                        "message": f"El terminal_id no coincide con las credenciales de configuración"
                    }), 403

                runner_id_sucursal = str(ID_SUCURSAL)
                runner_nombre_caja = str(NOMBRE_CAJA)
                key = (runner_id_sucursal, runner_nombre_caja)
                with self.busy_lock:
                    if key in self.busy_boxes:
                        logger.warning("Caja ocupada (runner): %s/%s", runner_id_sucursal, runner_nombre_caja)
                        return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                    self.busy_boxes.add(key)

                tx_id = str(uuid.uuid4())

                with self.tasks_lock:
                    event = threading.Event()
                    self.tasks[tx_id] = {
                        "event": event,
                        "result": None,
                        "estado": "PENDIENTE",
                        "id_sucursal": runner_id_sucursal,
                        "nombre_caja": runner_nombre_caja,
                        "client_id_sucursal": id_sucursal,
                        "client_nombre_caja": nombre_caja,
                        "timestamp": time.time(),
                        "timeout": timeout_transbank
                    }

                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": runner_id_sucursal,
                    "nombre_caja": runner_nombre_caja,
                    "client_id_sucursal": id_sucursal,
                    "client_nombre_caja": nombre_caja,
                    "type": "transbank",
                    "id_terminal": terminal_id_transbank,
                    "amount": amount_transbank,
                    "timeout": timeout_transbank
                }

                q = self.ensure_queue(runner_id_sucursal, runner_nombre_caja)
                q.put(task_payload)
                logger.info(
                    "Tarea encolada: tx=%s, timeout=%s, client=%s/%s runner=%s/%s",
                    tx_id,
                    timeout_transbank,
                    id_sucursal,
                    nombre_caja,
                    runner_id_sucursal,
                    runner_nombre_caja,
                )

                start_wait = time.time()
                finished = event.wait(timeout=timeout_transbank)
                wait_time = time.time() - start_wait

                if not finished:
                    with self.tasks_lock:
                        self.tasks.pop(tx_id, None)
                    self.internal_free_box(runner_id_sucursal, runner_nombre_caja, "timeout")
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
                terminal_id = data.get("terminal_id") or MP_TERMINAL_ID or ID_TERMINAL
                access_token = None
                if ALLOW_MP_TOKEN_IN_REQUEST:
                    access_token = data.get("access_token")
                if not access_token:
                    access_token = MP_ACCESS_TOKEN
                amount = data.get("amount")
                if not access_token:
                    return jsonify({"error": "Falta configuración MP_ACCESS_TOKEN"}), 500
                if amount is None or amount == 0:
                    return jsonify({"error": "Es necesario el monto de venta"}), 400
                if terminal_id is None or terminal_id == "":
                    return jsonify({"error": "Es necesario el terminal_id de mercado pago"}), 400

                tx_id_input = data.get("tx_id") or data.get("external_reference")
                tx_id = str(tx_id_input).strip() if tx_id_input else str(uuid.uuid4())
                idempotency_key = f"mp_{tx_id}"
                res = process_mercadopago(
                    terminal_id,
                    access_token,
                    amount,
                    timeout=timeout,
                    idempotency_key=idempotency_key,
                    external_reference=tx_id,
                )

                status = res.get("status")
                response_obj = res.get("response") or {}
                status_detail = None
                if isinstance(response_obj, dict):
                    status_detail = response_obj.get("status_detail")

                data_out = {
                    "status": status,
                    "status_detail": status_detail,
                    "order_id": res.get("order_id") or response_obj.get("id") if isinstance(response_obj, dict) else res.get("order_id"),
                    "response": response_obj,
                }
                success = status in ("approved", "success")

                return jsonify({
                    "success": success,
                    "data": data_out,
                    **res,
                }), 200

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
            try:
                if request.method == "GET":
                    data = request.args.to_dict()
                else:
                    data = request.get_json(force=True, silent=True) or {}

                tipo = data.get("type")
                logger.info("[HTTP /online] método=%s tipo=%s", request.method, tipo)

                if tipo == "transbank":
                    if not self.pos:
                        return jsonify({
                            "success": True,
                            "online": False,
                            "puerto": None,
                            "message": "POS no inicializado"
                        }), 503

                    pos_online = False
                    try:
                        logger.info("[HTTP /online] verificando POS transbank...")
                        pos_online = self.pos.is_online()
                        logger.info("[HTTP /online] POS online=%s", pos_online)
                    except Exception as e:
                        logger.warning("Error al verificar estado del POS: %s", e)
                        pos_online = False

                    return jsonify({
                        "success": True,
                        "online": pos_online,
                        "puerto": self.pos.get_current_port(),
                        "message": "POS conectado" if pos_online else "POS no detectado"
                    }), 200 if pos_online else 503
                elif tipo == "getnet":
                    if not self.getnet:
                        return jsonify({
                            "success": False,
                            "online": False,
                            "message": "Getnet no habilitado"
                        }), 503
                    
                    online = self.getnet.is_online()
                    
                    return jsonify({
                        "success": True,
                        "online": online,
                        "puerto": self.getnet.get_current_port(),
                        "message": "Getnet conectado" if online else "Getnet no detectado"
                    }), 200 if online else 503
                elif tipo == "mercadopago":
                    token_ok = bool(MP_ACCESS_TOKEN)
                    return jsonify({
                        "success": True,
                        "online": token_ok,
                        "message": "Mercado Pago configurado" if token_ok else "Falta MP_ACCESS_TOKEN en configuración",
                    }), 200 if token_ok else 503
                else:
                    # Sin tipo especificado → ping simple
                    logger.info("[HTTP /online] ping simple (sin tipo)")
                    return jsonify({
                        "success": True,
                        "message": "Servicio activo"
                    }), 200
                    
            except Exception as e:
                logger.error("Error en /online: %s", e)
                return jsonify({
                    "success": False,
                    "online": False,
                    "message": f"Error verificando POS: {e}"
            }), 500

        @app.route("/debug/queues")
        def debug_queues():
            with self.queues_lock:
                info = {}
                for cid, boxes in self.queues.items():
                    for box, q in boxes.items():
                        info[f"{cid}:{box}"] = {"queued": q.qsize()}
            return jsonify(info)

        @app.route("/pago/iniciar", methods=["POST", "GET"])
        @require_basic_auth
        def http_pago_iniciar():
            if request.method == "GET":
                data = request.args.to_dict()
            else:
                data = request.get_json(force=True, silent=True) or {}
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")

            # Validación contra el .env
            if id_sucursal != str(ID_SUCURSAL) or nombre_caja != str(NOMBRE_CAJA):
                return jsonify({
                    "status": "forbidden",
                    "message": f"El id_sucursal y el nombre_caja no coinciden con la configuración de esta máquina"
                }), 403

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
                    # La tarea en memoria pudo haberse limpiado (más de 10 min sin
                    # resolverse) sin que la transacción esté realmente cerrada.
                    # La base persistente sigue teniendo el estado real.
                    tx = tx_store.obtener(tx_id)
                    if tx:
                        return jsonify({
                            "transaction_id": tx_id,
                            "estado": tx.get("estado"),
                            "result": json.loads(tx["raw_response"]) if tx.get("raw_response") else None,
                            "fuente": "transaction_store",
                        }), 200
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
                    status = result.get("status")
                    if status in ("success", "approved"):
                        estado = "APROBADO"
                    elif status == "error":
                        estado = "ERROR"
                    elif status == "indeterminada":
                        # No confundir con RECHAZADO: el cliente pudo haber pagado
                        # igual en el POS y sigue pendiente de reconciliación.
                        estado = "INDETERMINADA"
                    elif status == "timeout":
                        estado = "TIMEOUT"
                    else:
                        estado = "RECHAZADO"
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

    def resolver_transaccion(self, tx_id, estado, resuelto_por, nota=None):
        """
        Resuelve (manual o automáticamente) una transacción PENDIENTE/INDETERMINADA:
        actualiza el registro persistente con quién y cuándo, y recién ahí libera la
        caja. Usado tanto por el panel (humano mirando el comprobante del POS) como
        por /pago/resolver/<tx_id> (API autenticada) y el monitor de reconciliación.

        Returns: (ok: bool, error_message: str|None, http_code: int)
        """
        tx = tx_store.obtener(tx_id)
        if not tx:
            return False, "Transacción no encontrada", 404
        if estado not in ("APROBADO", "RECHAZADO"):
            return False, "estado debe ser APROBADO o RECHAZADO", 400

        nota_final = nota or f"Resuelto manualmente por {resuelto_por or 'operador desconocido'}"
        tx_store.actualizar_estado(
            tx_id, estado,
            raw_response={"manual": True, "nota": nota_final, "resuelto_por": resuelto_por},
            nota=nota_final,
            resuelto_por=resuelto_por,
        )
        with self.busy_lock:
            self.busy_boxes.discard((tx.get("id_sucursal"), tx.get("nombre_caja")))

        logger.info("Resolución tx=%s -> %s por %s", tx_id, estado, resuelto_por)
        if tx.get("tipo") == "getnet":
            getnet_log.info(
                f"👤 {resuelto_por or 'Un operador'} resolvió MANUALMENTE (mirando el comprobante del POS) el "
                f"ticket {tx.get('ticket')} (tx {tx_id}) como {estado}. Nota: {nota_final}"
            )
        return True, None, 200

    def run(self):
        logger.info("Servidor Flask arrancando en puerto %s", HTTP_PORT)
        self.app.run(host="0.0.0.0", port=HTTP_PORT, threaded=True, use_reloader=False)
