#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
POS Payment Gateway - TODO EN UNO
Server + Agent completos en un solo archivo
"""
import gc
import os
import sys
import time
import uuid
import json
import logging
import requests
import traceback
import threading
import queue
import socket
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# Dependencias opcionales
try:
    from flask import Flask, request, jsonify
    from flask_cors import CORS
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("⚠️ Flask no disponible")
    sys.exit(1)

try:
    import serial.tools.list_ports
    from transbank import POSIntegrado
    TRANSBANK_AVAILABLE = True
except ImportError:
    TRANSBANK_AVAILABLE = False
    print("⚠️ Transbank SDK no disponible - POS físico deshabilitado")

try:
    import pystray
    from PIL import Image, ImageDraw
    SYSTRAY_AVAILABLE = True
except ImportError:
    SYSTRAY_AVAILABLE = False
    print("⚠️ pystray no disponible - Sin icono de bandeja")

load_dotenv()

# ============================================
# CONFIGURACIÓN
# ============================================
APP_NAME = "POS Gateway"
LOG_FILE = "pos_gateway.log"
LOCK_FILE = "pos_gateway.lock"

# Red
HTTP_PORT = int(os.environ.get("HTTP_PORT", "5000"))

# Identificación
ID_SUCURSAL = os.environ.get("ID_SUCURSAL", "1")
NOMBRE_CAJA = os.environ.get("NOMBRE_CAJA", socket.gethostname())
TERMINAL_ID = os.environ.get("TERMINAL_ID", "POS_{}".format(NOMBRE_CAJA))

# POS físico
USAR_POS_FISICO = os.environ.get("USAR_POS_FISICO", "true").lower() == "true"
PUERTOS_COM = os.environ.get("PUERTOS_COM", "COM7,COM6,COM8")

# Timeouts
MAX_TRANSACTION_TIME = int(os.environ.get("MAX_TRANSACTION_TIME", "90"))
TIMEOUT_SERVER = int(os.environ.get("TIMEOUT_SERVER", "120"))

# Mercado Pago
ALLOWED_MP = set([x.strip() for x in os.environ.get("ALLOWED_MP", "").split(",") if x.strip()])
MP_API_URL = "https://api.mercadopago.com/v1/orders"

# ============================================
# LOGGING
# ============================================
handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[handler, logging.StreamHandler()]
)
logger = logging.getLogger(APP_NAME)

# ============================================
# INSTANCIA ÚNICA
# ============================================
def ensure_single_instance():
    lock_path = Path(LOCK_FILE)
    if lock_path.exists():
        try:
            with open(lock_path, 'r') as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                logger.error("❌ Ya hay una instancia corriendo (PID: {})".format(pid))
                return False
            except OSError:
                lock_path.unlink()
        except:
            lock_path.unlink()
    
    try:
        with open(lock_path, 'w') as f:
            f.write(str(os.getpid()))
        logger.info("✅ Instancia única verificada")
        return True
    except Exception as e:
        logger.error("Error creando lock: {}".format(e))
        return False

def cleanup_lock():
    try:
        Path(LOCK_FILE).unlink(missing_ok=True)
    except:
        pass

# ============================================
# FIREWALL
# ============================================
def open_firewall_port(port):
    try:
        import subprocess
        rule_name = "POS_Gateway_Port_{}".format(port)
        check_cmd = 'netsh advfirewall firewall show rule name="{}"'.format(rule_name)
        result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
        
        if "No rules match" in result.stdout:
            add_cmd = 'netsh advfirewall firewall add rule name="{}" dir=in action=allow protocol=TCP localport={}'.format(rule_name, port)
            result = subprocess.run(add_cmd, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                logger.info("✅ Puerto {} abierto en firewall".format(port))
            else:
                logger.warning("⚠️ No se pudo abrir puerto (requiere admin)")
        else:
            logger.info("✅ Puerto {} ya está abierto".format(port))
    except Exception as e:
        logger.warning("⚠️ Error en firewall: {}".format(e))

# ============================================
# ICONO DE BANDEJA
# ============================================
class SystemTrayIcon:
    def __init__(self):
        self.icon = None
        
    def create_image(self):
        width, height = 64, 64
        image = Image.new('RGB', (width, height), '#2196F3')
        dc = ImageDraw.Draw(image)
        dc.rectangle([12, 12, 52, 52], fill='white')
        dc.text((20, 22), "POS", fill='#2196F3')
        return image
    
    def on_quit(self, icon, item):
        logger.info("Cerrando...")
        icon.stop()
        cleanup_lock()
        os._exit(0)
    
    def on_show_logs(self, icon, item):
        try:
            os.startfile(LOG_FILE)
        except:
            pass
    
    def on_show_config(self, icon, item):
        try:
            os.startfile(".env")
        except:
            pass
    
    def run(self):
        if not SYSTRAY_AVAILABLE:
            return
        
        try:
            menu_items = [
                pystray.MenuItem("POS Gateway", lambda: None, enabled=False),
                pystray.MenuItem("Puerto: {}".format(HTTP_PORT), lambda: None, enabled=False),
                pystray.MenuItem("Caja: {}/{}".format(ID_SUCURSAL, NOMBRE_CAJA), lambda: None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Ver Logs", self.on_show_logs),
                pystray.MenuItem("Ver Configuración", self.on_show_config),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", self.on_quit)
            ]
            
            self.icon = pystray.Icon(
                APP_NAME,
                self.create_image(),
                "POS Gateway - {}/{}".format(ID_SUCURSAL, NOMBRE_CAJA),
                menu=pystray.Menu(*menu_items)
            )
            
            logger.info("✅ Icono de bandeja iniciado")
            self.icon.run()
        except Exception as e:
            logger.error("Error en bandeja: {}".format(e))

# ============================================
# MÓDULO POS (TRANSBANK)
# ============================================
class POSModule:
    def __init__(self):
        self.usar_pos_fisico = USAR_POS_FISICO
        
    def detect_port(self):
        if not self.usar_pos_fisico or not TRANSBANK_AVAILABLE:
            logger.warning("POS físico deshabilitado")
            return None
        
        ports = [p.device for p in serial.tools.list_ports.comports()]
        logger.info("Puertos detectados: {} | Preferidos: {}".format(ports, PUERTOS_COM))
        if not ports:
            logger.warning("No hay puertos COM disponibles")
            return None
        
        pref_list = [p.strip() for p in PUERTOS_COM.split(",") if p.strip()]
        ordered = [p for p in pref_list if p in ports] + [p for p in ports if p not in pref_list]
        
        for p in ordered:
            pos = None
            try:
                pos = POSIntegrado()
                if pos.open_port(p) and pos.poll():
                    logger.info("✅ POS detectado en {}".format(p))
                    return p
            except Exception as e:
                logger.debug("Puerto {} no usable: {}".format(p, e))
            finally:
                try:
                    if pos:
                        pos.close_port()
                except:
                    pass
        
        logger.warning("No se detectó POS en ningún puerto")
        return None
    
    def do_sale(self, port, amount):
        if not self.usar_pos_fisico:
            return {"status": "error", "message": "POS físico deshabilitado"}
        
        pos = None
        try:
            pos = POSIntegrado()
            if not pos.open_port(port):
                return {"status": "error", "message": "No se pudo abrir {}".format(port)}
            
            ticket = time.strftime("%H%M%S")
            logger.info("🔷 Venta Transbank: Puerto={} Monto={} Ticket={}".format(port, amount, ticket))
            res = pos.sale(amount, ticket)
            logger.info("Respuesta POS: {}".format(res))
            
            if res.get("response_code") in ("0", "00"):
                return {"status": "success", "response": res}
            else:
                return {"status": "failed", "response": res}
        except Exception as e:
            logger.error("Error en venta: {}\n{}".format(e, traceback.format_exc()))
            return {"status": "error", "message": str(e)}
        finally:
            try:
                if pos:
                    pos.close_port()
            except:
                pass
            gc.collect()
    
    def do_sale_with_timeout(self, port, amount, timeout):
        result = {}
        
        def worker():
            nonlocal result
            try:
                result = self.do_sale(port, amount)
            except Exception as e:
                result = {"status": "error", "message": str(e)}
        
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout=timeout)
        
        if t.is_alive():
            logger.error("⏱️ Timeout en venta ({} segundos)".format(timeout))
            return {"status": "error", "message": "Timeout en venta POS"}
        
        return result

# ============================================
# SERVIDOR COMPLETO
# ============================================
class POSServer:
    def __init__(self, pos_module):
        self.pos_module = pos_module
        self.app = Flask(__name__)
        CORS(self.app)
        
        # Estado del servidor
        self.queues = {}
        self.queues_lock = threading.Lock()
        self.agents = {}
        self.agents_lock = threading.Lock()
        self.tasks = {}
        self.tasks_lock = threading.Lock()
        self.busy_boxes = set()
        self.busy_lock = threading.Lock()
        
        self.setup_routes()
        self.register_self()
        self.start_cleanup_thread()
    
    def ensure_queue(self, id_sucursal, nombre_caja):
        with self.queues_lock:
            if id_sucursal not in self.queues:
                self.queues[id_sucursal] = {}
            if nombre_caja not in self.queues[id_sucursal]:
                self.queues[id_sucursal][nombre_caja] = queue.Queue()
            return self.queues[id_sucursal][nombre_caja]
    
    def register_agent(self, id_sucursal, nombre_caja, info=None):
        with self.agents_lock:
            if id_sucursal not in self.agents:
                self.agents[id_sucursal] = {}
            self.agents[id_sucursal][nombre_caja] = {
                "last_seen": time.time(),
                "info": info or {}
            }
        
        self.ensure_queue(id_sucursal, nombre_caja)
        
        # Libera si estaba ocupada
        with self.busy_lock:
            key = (id_sucursal, nombre_caja)
            if key in self.busy_boxes:
                # Verifica si hay tareas activas
                with self.tasks_lock:
                    active = any(
                        t.get("id_sucursal") == id_sucursal and t.get("nombre_caja") == nombre_caja
                        for t in self.tasks.values()
                    )
                    if not active:
                        self.busy_boxes.discard(key)
                        logger.info("Caja {}/{} liberada por reconexión".format(id_sucursal, nombre_caja))
    
    def register_self(self):
        """Registra esta máquina como agente local"""
        self.register_agent(ID_SUCURSAL, NOMBRE_CAJA, {
            "host": socket.gethostname(),
            "terminal_id": TERMINAL_ID,
            "usa_pos_fisico": USAR_POS_FISICO,
            "local": True
        })
        logger.info("✅ Agente local registrado: {}/{}".format(ID_SUCURSAL, NOMBRE_CAJA))
    
    def internal_free_box(self, id_sucursal, nombre_caja, reason=""):
        key = (id_sucursal, nombre_caja)
        with self.busy_lock:
            if key in self.busy_boxes:
                self.busy_boxes.discard(key)
                logger.info("Caja {}/{} liberada {}".format(id_sucursal, nombre_caja, reason))
        
        with self.queues_lock:
            q = self.queues.get(id_sucursal, {}).get(nombre_caja)
            if q:
                while not q.empty():
                    try:
                        q.get_nowait()
                    except:
                        pass
    
    def cleanup_stale_tasks(self):
        """Hilo de limpieza"""
        while True:
            now = time.time()
            
            with self.tasks_lock:
                for tx_id, task in list(self.tasks.items()):
                    ts = task.get("timestamp", now)
                    if now - ts > 2 * TIMEOUT_SERVER:
                        self.tasks.pop(tx_id, None)
                        id_sucursal = task.get("id_sucursal")
                        nombre_caja = task.get("nombre_caja")
                        self.internal_free_box(id_sucursal, nombre_caja, "por timeout de limpieza")
                        logger.warning("Tarea huérfana eliminada: {}".format(tx_id))
            
            with self.agents_lock, self.busy_lock:
                for id_sucursal, boxes in list(self.agents.items()):
                    for nombre_caja, info in list(boxes.items()):
                        key = (id_sucursal, nombre_caja)
                        last_seen = info.get("last_seen", 0)
                        
                        if now - last_seen > 90:
                            logger.warning("Agente inactivo eliminado: {}/{}".format(id_sucursal, nombre_caja))
                            boxes.pop(nombre_caja, None)
                            self.busy_boxes.discard(key)
            
            time.sleep(3)
    
    def start_cleanup_thread(self):
        t = threading.Thread(target=self.cleanup_stale_tasks, daemon=True)
        t.start()
    
    def process_local_transbank(self, amount):
        """Procesa una venta Transbank localmente"""
        logger.info("🏠 Procesamiento LOCAL Transbank: monto={}".format(amount))
        
        puerto = self.pos_module.detect_port()
        if not puerto:
            return {"status": "error", "message": "No se detectó POS conectado"}
        
        return self.pos_module.do_sale_with_timeout(puerto, amount, MAX_TRANSACTION_TIME)
    
    def process_mercadopago(self, terminal_id, access_token, amount):
        """Procesa pago con Mercado Pago"""
        logger.info("💳 Pago Mercado Pago: terminal={} monto={}".format(terminal_id, amount))
        
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
                logger.warning("Error creando orden MP: {} {}".format(resp.status_code, data_resp))
                return {"status": "failed", "http_status": resp.status_code, "response": data_resp}
            
            order_id = data_resp["id"]
            logger.info("Orden MP creada: {}, esperando resultado...".format(order_id))
            
            # Polling del estado de la orden
            start = time.time()
            while time.time() - start < TIMEOUT_SERVER:
                check = requests.get("{}/{}".format(MP_API_URL, order_id), headers=headers, timeout=10)
                order_info = check.json()
                status = order_info.get("status")
                
                if status in ["created", "in_process", "at_terminal"]:
                    time.sleep(3)
                    continue
                
                logger.info("Orden {} finalizada con estado: {}".format(order_id, status))
                return {
                    "status": status,
                    "order_id": order_id,
                    "response": order_info
                }
            
            logger.warning("Timeout esperando respuesta de Mercado Pago")
            return {"status": "timeout", "order_id": order_id}
            
        except Exception as e:
            logger.error("Error Mercado Pago: {}\n{}".format(e, traceback.format_exc()))
            return {"status": "error", "message": str(e)}
    
    def setup_routes(self):
        @self.app.route("/register_agent", methods=["POST"])
        def http_register_agent():
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")
            meta = data.get("meta", {})
            
            if not id_sucursal or not nombre_caja:
                return jsonify({"error": "id_sucursal y nombre_caja requeridos"}), 400
            
            self.register_agent(id_sucursal, nombre_caja, info=meta)
            logger.info("Agente registrado: {}/{} meta={}".format(id_sucursal, nombre_caja, meta))
            return jsonify({"status": "ok"})
        
        @self.app.route("/poll", methods=["GET"])
        def http_poll():
            id_sucursal = request.args.get("id_sucursal")
            nombre_caja = request.args.get("nombre_caja")
            
            if not id_sucursal or not nombre_caja:
                return jsonify({"error": "id_sucursal y nombre_caja requeridos"}), 400
            
            self.register_agent(id_sucursal, nombre_caja)
            q = self.ensure_queue(id_sucursal, nombre_caja)
            
            try:
                task = q.get(timeout=2)
                logger.info("Despachando tarea -> {}/{} tx={}".format(id_sucursal, nombre_caja, task.get("tx_id")))
                return jsonify({"task": task})
            except queue.Empty:
                return jsonify({"task": None, "heartbeat": True})
        
        @self.app.route("/result", methods=["POST"])
        def http_result():
            data = request.get_json(force=True, silent=True) or {}
            tx_id = data.get("tx_id")
            result = data.get("result")
            
            if not tx_id or result is None:
                return jsonify({"error": "tx_id y result requeridos"}), 400
            
            with self.tasks_lock:
                task = self.tasks.get(tx_id)
                if not task:
                    logger.warning("Resultado para tx_id desconocido: {}".format(tx_id))
                    return jsonify({"status": "unknown_tx"}), 404
                
                if task.get("result") is not None:
                    logger.warning("Resultado duplicado tx={}".format(tx_id))
                    return jsonify({"status": "already_processed"}), 200
                
                task["result"] = result
                task["event"].set()
            
            logger.info("Resultado guardado tx={}: {}".format(tx_id, result))
            
            try:
                id_sucursal = task.get("id_sucursal")
                nombre_caja = task.get("nombre_caja")
                with self.busy_lock:
                    self.busy_boxes.discard((id_sucursal, nombre_caja))
                    logger.info("Caja liberada: {}/{}".format(id_sucursal, nombre_caja))
            except:
                pass
            
            return jsonify({"status": "ok"})
        
        @self.app.route("/pago", methods=["POST"])
        def http_pago():
            data = request.get_json(force=True, silent=True) or {}
            # Normalizar a strings para evitar mismatches int/str
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")
            pos_type = data.get("type")

            logger.info("[HTTP /pago] Petición recibida: {}".format(json.dumps(data, ensure_ascii=False)))

            # Cast seguro a str si viene algo no nulo (mantener None si no viene)
            if id_sucursal is not None:
                id_sucursal = str(id_sucursal)
            if nombre_caja is not None:
                nombre_caja = str(nombre_caja)

            if not id_sucursal or not nombre_caja or not pos_type:
                logger.warning("[/pago] Campos requeridos faltantes")
                return jsonify({"error": "id_sucursal, nombre_caja y type requeridos"}), 400

            # Ver estado POS físico
            if pos_type == "transbank" and (not USAR_POS_FISICO or not TRANSBANK_AVAILABLE):
                logger.warning("[/pago] Transbank solicitado pero POS físico deshabilitado o SDK no disponible. USAR_POS_FISICO=%s TRANSBANK_AVAILABLE=%s",
                               USAR_POS_FISICO, TRANSBANK_AVAILABLE)
                return jsonify({"status": "error", "message": "POS físico no disponible en esta máquina"}), 503

            # Verifica si es esta máquina (procesamiento local)
            is_local = (id_sucursal == str(ID_SUCURSAL) and nombre_caja == str(NOMBRE_CAJA))

            
            # ==========================================
            # TRANSBANK
            # ==========================================
            if pos_type == "transbank":
                terminal_id = data.get("terminal_id", TERMINAL_ID)
                amount = data.get("amount")
                
                if amount is None:
                    return jsonify({"error": "amount requerido"}), 400
                
                key = (id_sucursal, nombre_caja)
                
                # Verifica si está ocupada
                with self.busy_lock:
                    if key in self.busy_boxes:
                        logger.warning("Caja ocupada: {}/{}".format(id_sucursal, nombre_caja))
                        return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                    self.busy_boxes.add(key)
                
                tx_id = str(uuid.uuid4())
                
                try:
                    if is_local:
                        # PROCESAMIENTO LOCAL
                        result = self.process_local_transbank(amount)
                        with self.busy_lock:
                            self.busy_boxes.discard(key)
                        return jsonify({"transaction_id": tx_id, "result": result})
                    
                    else:
                        # PROCESAMIENTO REMOTO
                        logger.info("🌐 Procesamiento REMOTO tx={} -> {}/{}".format(tx_id, id_sucursal, nombre_caja))
                        
                        with self.agents_lock:
                            agent_info = self.agents.get(id_sucursal, {}).get(nombre_caja)
                        
                        if not agent_info:
                            with self.busy_lock:
                                self.busy_boxes.discard(key)
                            return jsonify({"status": "no_agent", "message": "Caja sin agente"}), 504
                        
                        event = threading.Event()
                        with self.tasks_lock:
                            self.tasks[tx_id] = {
                                "event": event,
                                "result": None,
                                "id_sucursal": id_sucursal,
                                "nombre_caja": nombre_caja,
                                "timestamp": time.time(),
                            }
                        
                        task_payload = {
                            "tx_id": tx_id,
                            "id_sucursal": id_sucursal,
                            "nombre_caja": nombre_caja,
                            "terminal_id": terminal_id,
                            "amount": amount,
                            "timestamp": datetime.utcnow().isoformat()
                        }
                        
                        q = self.ensure_queue(id_sucursal, nombre_caja)
                        q.put(task_payload)
                        
                        finished = event.wait(timeout=TIMEOUT_SERVER)
                        
                        if not finished:
                            logger.warning("TIMEOUT tx={}, liberando {}/{}".format(tx_id, id_sucursal, nombre_caja))
                            with self.tasks_lock:
                                self.tasks.pop(tx_id, None)
                            self.internal_free_box(id_sucursal, nombre_caja, "por timeout")
                            return jsonify({
                                "status": "timeout",
                                "transaction_id": tx_id,
                                "message": "Timeout ({} segundos)".format(TIMEOUT_SERVER)
                            }), 504
                        
                        with self.tasks_lock:
                            res = self.tasks.pop(tx_id)["result"]
                        with self.busy_lock:
                            self.busy_boxes.discard(key)
                        
                        logger.info("Transacción completada tx={}".format(tx_id))
                        return jsonify({"transaction_id": tx_id, "result": res})
                
                except Exception as e:
                    logger.error("Error en pago: {}".format(e))
                    with self.busy_lock:
                        self.busy_boxes.discard(key)
                    return jsonify({"status": "error", "message": str(e)}), 500
            
            # ==========================================
            # MERCADO PAGO
            # ==========================================
            elif pos_type == "mercadopago":
                terminal_id = data.get("terminal_id")
                access_token = data.get("access_token")
                amount = data.get("amount")
                
                if not terminal_id or not access_token or amount is None:
                    return jsonify({"error": "Faltan campos mercadopago"}), 400
                
                # Verifica autorización
                mp_key = "{}:{}".format(id_sucursal, nombre_caja)
                if ALLOWED_MP and mp_key not in ALLOWED_MP:
                    logger.warning("Caja no autorizada para MP: {}".format(mp_key))
                    return jsonify({"status": "forbidden", "message": "Caja no autorizada para MP"}), 403
                
                try:
                    result = self.process_mercadopago(terminal_id, access_token, amount)
                    return jsonify(result), 200
                except Exception as e:
                    logger.error("Error Mercado Pago: {}".format(e))
                    return jsonify({"status": "error", "message": str(e)}), 500
            
            else:
                return jsonify({"error": "Tipo POS no soportado"}), 400
        
        @self.app.route("/status")
        def http_status():
            with self.agents_lock:
                agents_count = sum(len(boxes) for boxes in self.agents.values())
            
            return jsonify({
                "status": "ok",
                "port": HTTP_PORT,
                "id_sucursal": ID_SUCURSAL,
                "nombre_caja": NOMBRE_CAJA,
                "terminal_id": TERMINAL_ID,
                "usa_pos_fisico": USAR_POS_FISICO,
                "agents_count": agents_count
            })
        
        @self.app.route("/debug/agents")
        def debug_agents():
            with self.agents_lock:
                agents_info = {}
                for id_sucursal, boxes in self.agents.items():
                    for nombre_caja, info in boxes.items():
                        key = "{}:{}".format(id_sucursal, nombre_caja)
                        agents_info[key] = {
                            "last_seen": info.get("last_seen"),
                            "info": info.get("info", {})
                        }
                return jsonify(agents_info)
    
    def run(self):
        logger.info("🚀 Servidor iniciado en puerto {}".format(HTTP_PORT))
        self.app.run(host="0.0.0.0", port=HTTP_PORT, threaded=True, use_reloader=False)

# ============================================
# MAIN
# ============================================
def main():
    print("="*60)
    print("POS PAYMENT GATEWAY - TODO EN UNO")
    print("="*60)
    print("Puerto HTTP: {}".format(HTTP_PORT))
    print("Sucursal: {}".format(ID_SUCURSAL))
    print("Caja: {}".format(NOMBRE_CAJA))
    print("Terminal: {}".format(TERMINAL_ID))
    print("POS Físico: {}".format("SI" if USAR_POS_FISICO else "NO"))
    print("="*60)
    
    logger.info("🚀 Iniciando POS Gateway...")
    
    if not ensure_single_instance():
        print("\n❌ Ya hay una instancia corriendo")
        print("Cierra la otra instancia o elimina el archivo:", LOCK_FILE)
        input("\nPresiona Enter para salir...")
        sys.exit(1)
    
    open_firewall_port(HTTP_PORT)
    
    # Inicia icono de bandeja
    if SYSTRAY_AVAILABLE:
        tray = SystemTrayIcon()
        tray_thread = threading.Thread(target=tray.run, daemon=True)
        tray_thread.start()
        logger.info("✅ Icono de bandeja iniciado")
    
    try:
        pos_module = POSModule()
        server = POSServer(pos_module)
        
        # Inicia servidor (bloqueante)
        server.run()
        
    except KeyboardInterrupt:
        logger.info("Cerrando por Ctrl+C...")
    except Exception as e:
        logger.error("Error fatal: {}\n{}".format(e, traceback.format_exc()))
    finally:
        cleanup_lock()
        logger.info("Aplicación cerrada")

if __name__ == "__main__":
    main()