#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Aplicación Unificada - POS Payment Gateway
Servidor y Cliente (Agent) en una sola aplicación
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

# Intentar importar dependencias opcionales
try:
    from flask import Flask, request, jsonify
    from flask_cors import CORS
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("⚠️  Flask no disponible - Modo servidor deshabilitado")

try:
    import serial.tools.list_ports
    from transbank import POSIntegrado
    TRANSBANK_AVAILABLE = True
except ImportError:
    TRANSBANK_AVAILABLE = False
    print("⚠️  Transbank SDK no disponible - Modo agente deshabilitado")

try:
    import pystray
    from PIL import Image, ImageDraw
    SYSTRAY_AVAILABLE = True
except ImportError:
    SYSTRAY_AVAILABLE = False
    print("⚠️  pystray no disponible - Icono de bandeja deshabilitado")

# Cargar variables de entorno
load_dotenv()

# ============================================
# CONFIGURACIÓN GLOBAL
# ============================================
APP_MODE = os.environ.get("APP_MODE", "agent").lower()  # "server" o "agent"
APP_NAME = "POS Payment Gateway"
LOG_FILE = "app.log"
LOCK_FILE = "app.lock"

# Configuración común
HTTP_PORT = int(os.environ.get("HTTP_PORT", "5000"))
ID_SUCURSAL = os.environ.get("ID_SUCURSAL", os.environ.get("CLIENT_ID", "default_sucursal"))
NOMBRE_CAJA = os.environ.get("NOMBRE_CAJA", os.environ.get("BOX_ID", "default_caja"))

# Configuración de agente
USAR_POS_FISICO = os.environ.get("USAR_POS_FISICO", "true").lower() == "true"
SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:5000")
TERMINAL_ID = os.environ.get("TERMINAL_ID", "POS_01")
PREFERRED_PORTS = os.environ.get("PREFERRED_PORTS", "COM7,COM6,COM8")
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "10"))
RECONNECT_DELAY = int(os.environ.get("RECONNECT_DELAY", "5"))
MAX_TRANSACTION_TIME = int(os.environ.get("MAX_TRANSACTION_TIME", "90"))

# Configuración de servidor
TIMEOUT = int(os.environ.get("TIMEOUT", "120"))
MP_API_URL = "https://api.mercadopago.com/v1/orders"
ALLOWED_MP = set([x.strip() for x in os.environ.get("ALLOWED_MP", "").split(",") if x.strip()])

# ============================================
# CONFIGURACIÓN DE LOGGING
# ============================================
handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[handler, logging.StreamHandler()]
)
logger = logging.getLogger(APP_NAME)

# ============================================
# VERIFICACIÓN DE INSTANCIA ÚNICA
# ============================================
def ensure_single_instance():
    """Verifica que solo haya una instancia de la aplicación corriendo"""
    lock_path = Path(LOCK_FILE)
    
    if lock_path.exists():
        try:
            with open(lock_path, 'r') as f:
                pid = int(f.read().strip())
            
            # Verifica si el proceso existe
            try:
                os.kill(pid, 0)  # No mata, solo verifica
                logger.error("❌ Ya hay una instancia corriendo (PID: {})".format(pid))
                return False
            except OSError:
                # El proceso no existe, elimina el lock viejo
                lock_path.unlink()
        except Exception as e:
            logger.warning("Error verificando instancia anterior: {}".format(e))
            lock_path.unlink()
    
    # Crea el lock file
    try:
        with open(lock_path, 'w') as f:
            f.write(str(os.getpid()))
        logger.info("✅ Lock file creado: {}".format(lock_path))
        return True
    except Exception as e:
        logger.error("Error creando lock file: {}".format(e))
        return False

def cleanup_lock():
    """Elimina el lock file al cerrar"""
    try:
        Path(LOCK_FILE).unlink(missing_ok=True)
        logger.info("Lock file eliminado")
    except Exception as e:
        logger.error("Error eliminando lock file: {}".format(e))

# ============================================
# FIREWALL - APERTURA DE PUERTO
# ============================================
def open_firewall_port(port):
    """Abre el puerto en el firewall de Windows"""
    try:
        import subprocess
        
        rule_name = "POS_Payment_Gateway_Port_{}".format(port)
        
        # Verifica si la regla ya existe
        check_cmd = 'netsh advfirewall firewall show rule name="{}"'.format(rule_name)
        result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
        
        if "No rules match" in result.stdout:
            # Crea la regla
            add_cmd = 'netsh advfirewall firewall add rule name="{}" dir=in action=allow protocol=TCP localport={}'.format(
                rule_name, port)
            
            result = subprocess.run(add_cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info("✅ Puerto {} abierto en firewall".format(port))
                return True
            else:
                logger.warning("⚠️  No se pudo abrir puerto en firewall (requiere permisos admin): {}".format(result.stderr))
                return False
        else:
            logger.info("✅ Puerto {} ya está abierto en firewall".format(port))
            return True
            
    except Exception as e:
        logger.warning("⚠️  Error abriendo puerto en firewall: {}".format(e))
        return False

# ============================================
# ICONO DE BANDEJA DEL SISTEMA
# ============================================
class SystemTrayIcon:
    def __init__(self, app_mode):
        self.app_mode = app_mode
        self.icon = None
        
    def create_image(self):
        """Crea un icono simple"""
        width = 64
        height = 64
        color1 = "blue" if self.app_mode == "server" else "green"
        color2 = "white"
        
        image = Image.new('RGB', (width, height), color1)
        dc = ImageDraw.Draw(image)
        dc.rectangle([width // 4, height // 4, width * 3 // 4, height * 3 // 4], fill=color2)
        
        return image
    
    def on_quit(self, icon, item):
        """Maneja el cierre desde la bandeja"""
        logger.info("Cerrando desde bandeja del sistema...")
        icon.stop()
        cleanup_lock()
        os._exit(0)
    
    def on_show_logs(self, icon, item):
        """Abre el archivo de logs"""
        try:
            os.startfile(LOG_FILE)
        except Exception as e:
            logger.error("Error abriendo logs: {}".format(e))
    
    def run(self):
        """Ejecuta el icono en la bandeja"""
        if not SYSTRAY_AVAILABLE:
            logger.warning("pystray no disponible - Icono de bandeja deshabilitado")
            return
        
        try:
            menu_items = [
                pystray.MenuItem("Modo: {}".format(self.app_mode.upper()), lambda: None, enabled=False),
                pystray.MenuItem("Puerto: {}".format(HTTP_PORT), lambda: None, enabled=False),
                pystray.MenuItem("Ver Logs", self.on_show_logs),
                pystray.MenuItem("Salir", self.on_quit)
            ]
            
            self.icon = pystray.Icon(
                APP_NAME,
                self.create_image(),
                "{} - {}".format(APP_NAME, self.app_mode.upper()),
                menu=pystray.Menu(*menu_items)
            )
            
            logger.info("✅ Icono de bandeja iniciado")
            self.icon.run()
            
        except Exception as e:
            logger.error("Error en icono de bandeja: {}".format(e))

# ============================================
# MÓDULO AGENTE (CLIENT)
# ============================================
class POSAgent:
    def __init__(self):
        self.usar_pos_fisico = USAR_POS_FISICO
        self.server_url = SERVER_URL
        self.id_sucursal = ID_SUCURSAL
        self.nombre_caja = NOMBRE_CAJA
        self.terminal_id = TERMINAL_ID
        
    def register(self):
        """Registra el agente en el servidor"""
        url = "{}/register_agent".format(self.server_url.rstrip('/'))
        body = {
            "id_sucursal": self.id_sucursal,
            "nombre_caja": self.nombre_caja,
            "meta": {
                "host": os.environ.get("COMPUTERNAME", socket.gethostname()),
                "terminal_id": self.terminal_id,
                "usa_pos_fisico": self.usar_pos_fisico
            }
        }
        try:
            r = requests.post(url, json=body, timeout=10)
            logger.info("Registro en servidor: {} {}".format(r.status_code, r.text))
        except Exception as e:
            logger.error("Error registrando agente: {}".format(e))
    
    def detect_port(self, preferred=None):
        """Detecta el puerto COM del POS"""
        if not self.usar_pos_fisico:
            logger.info("POS físico deshabilitado en configuración")
            return None
        
        if not TRANSBANK_AVAILABLE:
            logger.error("Transbank SDK no disponible")
            return None
        
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if not ports:
            logger.warning("No hay puertos COM disponibles")
            return None
        
        pref_list = [p.strip() for p in (preferred or "").split(",") if p.strip()]
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
    
    def do_sale(self, pos_port, amount):
        """Realiza una venta en el POS"""
        if not self.usar_pos_fisico:
            return {"status": "error", "message": "POS físico deshabilitado"}
        
        pos = None
        try:
            pos = POSIntegrado()
            if not pos.open_port(pos_port):
                return {"status": "error", "message": "No se pudo abrir {}".format(pos_port)}
            
            ticket = time.strftime("%H%M%S")
            logger.info("Iniciando venta | Puerto={} | Monto={} | Ticket={}".format(pos_port, amount, ticket))
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
    
    def do_sale_with_timeout(self, tx_id, port, amount, timeout):
        """Ejecuta venta con timeout"""
        result = {}
        
        def worker():
            nonlocal result
            try:
                result = self.do_sale(port, amount)
            except Exception as e:
                logger.error("Error en worker: {}".format(e))
                result = {"status": "error", "message": str(e)}
        
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout=timeout)
        
        if t.is_alive():
            logger.error("Timeout en venta ({} segundos)".format(timeout))
            return {"status": "error", "message": "Timeout en venta POS"}
        
        return result
    
    def poll_loop(self):
        """Bucle principal de polling"""
        self.register()
        poll_url = "{}/poll".format(self.server_url.rstrip('/'))
        result_url = "{}/result".format(self.server_url.rstrip('/'))
        cycle_start = time.time()
        last_poll_attempt = time.time()
        failed_polls = 0
        
        while True:
            try:
                # Heartbeat
                if time.time() - cycle_start > 360:
                    logger.info("Heartbeat - Reiniciando sesión")
                    self.register()
                    cycle_start = time.time()
                
                # Detecta inactividad
                if time.time() - last_poll_attempt > 30:
                    logger.warning("30s sin poll exitoso, reconectando...")
                    self.register()
                    last_poll_attempt = time.time()
                
                params = {"id_sucursal": self.id_sucursal, "nombre_caja": self.nombre_caja}
                r = requests.get(poll_url, params=params, timeout=10)
                last_poll_attempt = time.time()
                
                if r.status_code != 200:
                    logger.warning("Error en poll: {} {}".format(r.status_code, r.text))
                    failed_polls += 1
                    if failed_polls >= 3:
                        logger.warning("3 polls fallidos, reconectando...")
                        self.register()
                        failed_polls = 0
                    time.sleep(RECONNECT_DELAY)
                    continue
                
                failed_polls = 0
                data = r.json()
                task = data.get("task")
                
                if not task:
                    time.sleep(0.5)
                    continue
                
                tx_id = task.get("tx_id")
                terminal_id = task.get("terminal_id")
                amount = task.get("amount")
                logger.info("✅ Tarea recibida: tx={} terminal={} monto={}".format(tx_id, terminal_id, amount))
                
                puerto = self.detect_port(PREFERRED_PORTS)
                if not puerto:
                    result = {"status": "error", "message": "No se detectó POS conectado"}
                else:
                    result = self.do_sale_with_timeout(tx_id, puerto, amount, MAX_TRANSACTION_TIME)
                
                payload = {"tx_id": tx_id, "result": result}
                try:
                    rr = requests.post(result_url, json=payload, timeout=15)
                    logger.info("Resultado enviado: {} {}".format(rr.status_code, rr.text))
                except Exception as e:
                    logger.error("Error enviando resultado: {}".format(e))
                
                time.sleep(0.2)
                
            except requests.exceptions.ReadTimeout:
                logger.debug("Timeout en poll (normal)")
                time.sleep(1)
                continue
            except requests.exceptions.ConnectionError as e:
                logger.error("Error de conexión: {}".format(e))
                time.sleep(RECONNECT_DELAY)
                self.register()
                continue
            except Exception as e:
                logger.error("Error inesperado: {}\n{}".format(e, traceback.format_exc()))
                time.sleep(RECONNECT_DELAY)
                try:
                    self.register()
                except:
                    pass

# ============================================
# MÓDULO SERVIDOR
# ============================================
class POSServer:
    def __init__(self):
        if not FLASK_AVAILABLE:
            raise ImportError("Flask no está instalado")
        
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
        
        self.setup_routes()
    
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
            self.agents[id_sucursal][nombre_caja] = {"last_seen": time.time(), "info": info or {}}
        
        self.ensure_queue(id_sucursal, nombre_caja)
        
        with self.busy_lock:
            if (id_sucursal, nombre_caja) in self.busy_boxes:
                self.busy_boxes.discard((id_sucursal, nombre_caja))
                logger.warning("Caja {}/{} estaba ocupada, liberada por reconexión".format(id_sucursal, nombre_caja))
    
    def setup_routes(self):
        @self.app.route("/register_agent", methods=["POST"])
        def http_register_agent():
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")
            meta = data.get("meta", {})
            
            if not id_sucursal or not nombre_caja:
                return jsonify({"error": "id_sucursal y nombre_caja son requeridos"}), 400
            
            self.register_agent(id_sucursal, nombre_caja, info=meta)
            logger.info("Agent registered: {}/{} meta={}".format(id_sucursal, nombre_caja, meta))
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
                    logger.warning("Resultado duplicado para tx={}".format(tx_id))
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
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")
            pos_type = data.get("type")
            
            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "id_sucursal, nombre_caja y type son requeridos"}), 400
            
            if pos_type == "transbank":
                terminal_id = data.get("terminal_id")
                amount = data.get("amount")
                
                if not terminal_id or amount is None:
                    return jsonify({"error": "Faltan campos transbank"}), 400
                
                with self.agents_lock:
                    agent_info = self.agents.get(id_sucursal, {}).get(nombre_caja)
                
                if not agent_info:
                    return jsonify({"status": "no_agent", "message": "Caja sin agente"}), 504
                
                key = (id_sucursal, nombre_caja)
                with self.busy_lock:
                    if key in self.busy_boxes:
                        logger.warning("Caja ocupada: {}/{}".format(id_sucursal, nombre_caja))
                        return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                    self.busy_boxes.add(key)
                
                tx_id = str(uuid.uuid4())
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
                
                finished = event.wait(timeout=TIMEOUT)
                
                if not finished:
                    logger.warning("TIMEOUT tx={}, liberando caja".format(tx_id))
                    with self.tasks_lock:
                        self.tasks.pop(tx_id, None)
                    with self.busy_lock:
                        self.busy_boxes.discard(key)
                    return jsonify({
                        "status": "timeout",
                        "transaction_id": tx_id,
                        "message": "Timeout ({} segundos)".format(TIMEOUT)
                    }), 504
                
                with self.tasks_lock:
                    res = self.tasks.pop(tx_id)["result"]
                with self.busy_lock:
                    self.busy_boxes.discard(key)
                
                logger.info("Transacción completada tx={}: {}".format(tx_id, res))
                return jsonify({"transaction_id": tx_id, "result": res})
            
            return jsonify({"error": "POS type no soportado"}), 400
        
        @self.app.route("/status")
        def http_status():
            return jsonify({
                "status": "ok",
                "mode": "server",
                "port": HTTP_PORT,
                "agents_count": sum(len(boxes) for boxes in self.agents.values())
            })
    
    def run(self):
        """Inicia el servidor Flask"""
        logger.info("Iniciando servidor en puerto {}".format(HTTP_PORT))
        self.app.run(host="0.0.0.0", port=HTTP_PORT, threaded=True)

# ============================================
# MAIN
# ============================================
def main():
    logger.info("="*60)
    logger.info("{} - Modo: {}".format(APP_NAME, APP_MODE.upper()))
    logger.info("="*60)
    logger.info("Puerto HTTP: {}".format(HTTP_PORT))
    logger.info("ID Sucursal: {}".format(ID_SUCURSAL))
    logger.info("Nombre Caja: {}".format(NOMBRE_CAJA))
    
    # Verifica instancia única
    if not ensure_single_instance():
        sys.exit(1)
    
    # Abre puerto en firewall
    if APP_MODE == "server":
        open_firewall_port(HTTP_PORT)
    
    # Inicia icono de bandeja en hilo separado
    if SYSTRAY_AVAILABLE:
        tray_icon = SystemTrayIcon(APP_MODE)
        tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
        tray_thread.start()
    
    try:
        if APP_MODE == "server":
            server = POSServer()
            server.run()
        elif APP_MODE == "agent":
            agent = POSAgent()
            agent.poll_loop()
        else:
            logger.error("❌ APP_MODE inválido: {}".format(APP_MODE))
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Cerrando aplicación...")
    except Exception as e:
        logger.error("Error fatal: {}\n{}".format(e, traceback.format_exc()))
    finally:
        cleanup_lock()

if __name__ == "__main__":
    main()