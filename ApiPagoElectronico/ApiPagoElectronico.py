import os          
import sys        
import time        
import uuid        
import json        
import gc          
import queue       
import socket      
import logging     
import traceback   
import threading   
import requests    
from pathlib import Path                      
from logging.handlers import RotatingFileHandler 
from datetime import datetime                   
from dotenv import load_dotenv                   

# IMPORTACIONES OPCIONALES
try:
    from flask import Flask, request, jsonify  
    from flask_cors import CORS              
    FLASK_AVAILABLE = True             
except Exception:
    FLASK_AVAILABLE = False                   

try:
    import serial.tools.list_ports
    from transbank import POSIntegrado  
    TRANSBANK_AVAILABLE = True    
except Exception:
    TRANSBANK_AVAILABLE = False  

try:
    import pystray                          
    from PIL import Image, ImageDraw, ImageFont  
    SYSTRAY_AVAILABLE = True               
except Exception:
    SYSTRAY_AVAILABLE = False             

# Safety para cuando se empaqueta con --noconsole
if getattr(sys, "stdout", None):  # Si stdout existe
    try:
        sys.stdout.reconfigure(encoding="utf-8")  
    except Exception:
        pass

if getattr(sys, "stderr", None):  # Si stderr existe
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass 


load_dotenv()

# CONFIGURACIÓN GLOBAL
APP_NAME = os.environ.get("APP_NAME", "POS Gateway")

LOG_FILE = os.environ.get("LOG_FILE", "pos_gateway.log")

LOCK_FILE = os.environ.get("LOCK_FILE", "pos_gateway.lock")

HTTP_PORT = int(os.environ.get("HTTP_PORT", os.environ.get("PORT", "5000")))

ID_SUCURSAL = os.environ.get("ID_SUCURSAL", "1")
NOMBRE_CAJA = os.environ.get("NOMBRE_CAJA", socket.gethostname())
ID_TERMINAL = os.environ.get("ID_TERMINAL", os.environ.get("TERMINAL_ID", f"POS_{NOMBRE_CAJA}")) 

USAR_POS_FISICO = os.environ.get("USAR_POS_FISICO", "true").lower() == "true"
PUERTOS_COM = os.environ.get("PUERTOS_COM", "COM7,COM6,COM8")

MAX_TRANSACTION_TIME = int(os.environ.get("MAX_TRANSACTION_TIME", "90"))
TIMEOUT_SERVER = int(os.environ.get("TIMEOUT_SERVER", "120")) 

ALLOWED_MP = set([x.strip() for x in os.environ.get("ALLOWED_MP", "").split(",") if x.strip()])
MP_API_URL = os.environ.get("MP_API_URL", "https://api.mercadopago.com/v1/orders")

# CONFIGURACIÓN DE LOGGING
# Handler que rota el archivo cuando llega a 10MB, mantiene 5 backups
handler = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5)

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s", 
    handlers=[handler, logging.StreamHandler()]
)

logger = logging.getLogger(APP_NAME)

# INSTANCIA ÚNICA
def ensure_single_instance():
    """
    Verifica si ya hay otra instancia corriendo.
    Usa un archivo de lock con el PID del proceso.
    Retorna True si puede correr, False si ya hay otra instancia.
    """
    lock_path = Path(LOCK_FILE)  # Ruta del archivo de lock
    
    if lock_path.exists():
        try:
            with open(lock_path, "r") as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                logger.error("Otra instancia detectada (PID=%s).", pid)
                return False 
            except OSError:
                lock_path.unlink()
        except Exception:
            try:
                lock_path.unlink()
            except Exception:
                pass
    
    # Crea el archivo de lock con el PID actual
    try:
        with open(lock_path, "w") as f:
            f.write(str(os.getpid()))
        logger.info("Lock file creado: %s", lock_path)
        return True
    except Exception as e:
        logger.error("No se pudo crear lock file: %s", e)
        return False

def cleanup_lock():
    """
    Elimina el archivo de lock al cerrar la aplicación.
    Se llama en el finally del main.
    """
    try:
        Path(LOCK_FILE).unlink(missing_ok=True)
    except Exception:
        pass 

# APERTURA DE PUERTO EN FIREWALL (Windows)
# Intenta abrir el puerto automáticamente
def open_firewall_port(port):
    """
    Intenta abrir el puerto en Windows Firewall usando netsh.
    Requiere permisos de administrador para funcionar.
    """
    try:
        import subprocess
        rule_name = f"{APP_NAME}_Port_{port}"
        
        check_cmd = f'netsh advfirewall firewall show rule name="{rule_name}"'
        result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
        
        if "No rules match" in result.stdout:
            add_cmd = f'netsh advfirewall firewall add rule name="{rule_name}" dir=in action=allow protocol=TCP localport={port}'
            res = subprocess.run(add_cmd, shell=True, capture_output=True, text=True)
            
            if res.returncode == 0:
                logger.info("Puerto %s abierto en firewall", port)
            else:
                logger.warning("No se pudo abrir puerto en firewall (admin required): %s", res.stderr)
        else:
            logger.info("Puerto %s ya presente en firewall", port)
    except Exception as e:
        logger.warning("open_firewall_port error: %s", e)

# MÓDULO POS (TRANSBANK)
class POSModule:
    """
    Clase que maneja el POS físico Transbank:
    - Detecta automáticamente el puerto COM
    - Mantiene un caché del puerto detectado
    - Ejecuta ventas con timeout
    - Monitorea constantemente el POS
    """
    
    def __init__(self, prefer_ports):
        """
        Inicializa el módulo POS.
        prefer_ports: String con puertos preferidos separados por coma (ej: "COM7,COM6")
        """
        self.prefer_ports = [p.strip() for p in prefer_ports.split(",") if p.strip()]
        
        self.current_port = None
        
        self.lock = threading.Lock()
        
        self._stop_monitor = threading.Event()
        
        self.monitor_thread = None
        
        self.last_ok = 0

    def list_ports(self):
        """
        Lista todos los puertos COM disponibles en el sistema.
        Retorna una lista de strings con los nombres de los puertos.
        """
        try:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            return ports
        except Exception:
            return []

    def detect_port(self):
        """
        Detecta en qué puerto COM está conectado el POS.
        Intenta primero los puertos preferidos, luego el resto.
        Retorna el puerto detectado o None si no encuentra.
        """
        if not USAR_POS_FISICO or not TRANSBANK_AVAILABLE:
            logger.debug("POS físico deshabilitado o SDK no presente")
            return None

        ports = self.list_ports()
        logger.info("Puertos detectados: %s | Preferidos: %s", ports, ",".join(self.prefer_ports))
        
        ordered = [p for p in self.prefer_ports if p in ports] + [p for p in ports if p not in self.prefer_ports]
        
        for p in ordered:
            pos = None
            try:
                pos = POSIntegrado()
                
                if pos.open_port(p) and pos.poll():
                    try:
                        pos.close_port()
                    except:
                        pass
                    
                    logger.info("POS detectado en %s", p)
                    
                    with self.lock:
                        self.current_port = p
                    
                    self.last_ok = time.time()
                    
                    return p 
                    
            except Exception as e:
                logger.debug("Puerto %s no usable: %s", p, e)
            finally:
                # Siempre intenta cerrar el puerto
                try:
                    if pos:
                        pos.close_port()
                except:
                    pass
        
        logger.warning("No se detectó POS en los puertos listados")
        
        # Limpia el caché
        with self.lock:
            self.current_port = None
        
        return None

    def get_current_port(self):
        """
        Obtiene el puerto actualmente en caché (thread-safe).
        Retorna el puerto o None.
        """
        with self.lock:
            return self.current_port

    def open_port_and_sale(self, port, amount):
        """
        Abre el puerto especificado y ejecuta una venta.
        port: Puerto COM (ej: "COM7")
        amount: Monto de la venta
        Retorna un dict con el resultado de la venta.
        """
        if not TRANSBANK_AVAILABLE:
            return {"status": "error", "message": "Transbank SDK no disponible"}
        
        pos = None
        try:
            pos = POSIntegrado()
            if not pos.open_port(port):
                return {"status": "error", "message": f"No se pudo abrir {port}"}
            
            ticket = time.strftime("%H%M%S")
            logger.info("Venta POS -> puerto=%s monto=%s ticket=%s", port, amount, ticket)
            
            res = pos.sale(amount, ticket)
            logger.info("Respuesta POS: %s", res)
            
            if res.get("response_code") in ("0", "00"):
                self.last_ok = time.time() 
                return {"status": "success", "response": res}
            else:
                return {"status": "failed", "response": res}
                
        except Exception as e:
            logger.error("Error do_sale: %s\n%s", e, traceback.format_exc())
            return {"status": "error", "message": str(e)}
        finally:
            try:
                if pos:
                    pos.close_port()
            except:
                pass
            gc.collect()  

    def do_sale_with_timeout(self, amount, timeout=MAX_TRANSACTION_TIME):
        """
        Ejecuta una venta con timeout para evitar bloqueos infinitos.
        amount: Monto de la venta
        timeout: Tiempo máximo de espera en segundos
        Retorna el resultado de la venta o error de timeout.
        """
        port = self.get_current_port() or self.detect_port()
        
        if not port:
            return {"status": "error", "message": "No se detectó POS conectado"}
        
        result = {}
        
        def worker():
            """Worker que ejecuta la venta en un hilo separado"""
            nonlocal result
            result = self.open_port_and_sale(port, amount)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        
        t.join(timeout=timeout)
        
        if t.is_alive():
            logger.error("Timeout en venta POS (thread sigue vivo).")
            return {"status": "error", "message": "Timeout en venta POS"}
        return result 

    def start_monitor(self, interval=5):
        """
        Inicia un hilo monitor que verifica constantemente el POS.
        interval: Intervalo de verificación en segundos
        """
        if not USAR_POS_FISICO or not TRANSBANK_AVAILABLE:
            logger.info("Monitor POS no iniciado (no use POS físico o SDK falta)")
            return
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            return
        
        self._stop_monitor.clear()
        
        def monitor():
            """Función del hilo monitor"""
            while not self._stop_monitor.is_set():
                try:
                    if not self.get_current_port():
                        self.detect_port()
                    else:
                        p = self.get_current_port()
                        try:
                            pos = POSIntegrado()
                            ok = pos.open_port(p) and pos.poll()
                            pos.close_port()
                            
                            if not ok:
                                logger.warning("POS en %s dejó de responder, limpiando puerto.", p)
                                with self.lock:
                                    self.current_port = None
                        except Exception:
                            with self.lock:
                                self.current_port = None
                    
                    time.sleep(interval)
                except Exception as e:
                    logger.debug("Monitor POS error: %s", e)
                    time.sleep(interval)
        
        self.monitor_thread = threading.Thread(target=monitor, daemon=True)
        self.monitor_thread.start()
        logger.info("Monitor POS iniciado")

    def stop_monitor(self):
        """Detiene el hilo monitor"""
        self._stop_monitor.set() 
        try:
            if self.monitor_thread:
                self.monitor_thread.join(timeout=1)
        except:
            pass
        logger.info("Monitor POS detenido")

    def restart(self):
        """
        Reinicia el módulo POS: limpia el caché y redetecta.
        Útil para cuando el POS se desconecta y reconecta.
        """
        logger.info("Reiniciando POS module (clear port + redetect)...")
        with self.lock:
            self.current_port = None 
        return self.detect_port()

# MERCADO PAGO
def process_mercadopago(terminal_id, access_token, amount):
    """
    Procesa un pago con Mercado Pago Point.
    terminal_id: ID del terminal de Mercado Pago
    access_token: Token de acceso de la cuenta
    amount: Monto del pago
    Retorna un dict con el resultado.
    """
    logger.info("Procesando MercadoPago terminal=%s monto=%s", terminal_id, amount)
    
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
        
        if resp.status_code != 201:
            logger.warning("Error creando orden MP: %s %s", resp.status_code, data_resp)
            return {"status": "failed", "http_status": resp.status_code, "response": data_resp}
        
        order_id = data_resp["id"]
        logger.info("Orden MP creada: %s; esperando resultado...", order_id)
        
        start = time.time()
        while time.time() - start < TIMEOUT_SERVER:
            check = requests.get(f"{MP_API_URL}/{order_id}", headers=headers, timeout=10)
            order_info = check.json()
            status = order_info.get("status")
            
            if status in ("created", "in_process", "at_terminal"):
                time.sleep(3)
                continue
            
            logger.info("Orden %s finalizada con estado: %s", order_id, status)
            return {"status": status, "order_id": order_id, "response": order_info}
        
        logger.warning("Timeout esperando respuesta MP orden %s", order_id)
        return {"status": "timeout", "order_id": order_id}
        
    except Exception as e:
        logger.error("Error MercadoPago: %s\n%s", e, traceback.format_exc())
        return {"status": "error", "message": str(e)}

# SERVIDOR API (FLASK)
class APIServer:
    """
    Servidor Flask que:
    - Recibe peticiones HTTP (/pago, /status, etc.)
    - Maneja colas de tareas por cada caja
    - Procesa tareas locales en un worker
    - Coordina agentes remotos
    """
    
    def __init__(self, pos_module):
        """
        Inicializa el servidor.
        pos_module: Instancia de POSModule para procesar pagos Transbank
        """
        if not FLASK_AVAILABLE:
            logger.critical("Flask no instalado - no se puede iniciar servidor")
            raise RuntimeError("Flask requerido")
        
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

        self.setup_routes() 
        self.register_local_agent() 
        self.start_local_worker() 

    def ensure_queue(self, id_sucursal, nombre_caja):
        """
        Asegura que exista una cola para la caja especificada.
        Retorna la cola (Queue).
        """
        with self.queues_lock:
            if id_sucursal not in self.queues:
                self.queues[id_sucursal] = {}
            if nombre_caja not in self.queues[id_sucursal]:
                self.queues[id_sucursal][nombre_caja] = queue.Queue()
            return self.queues[id_sucursal][nombre_caja]

    def register_agent(self, id_sucursal, nombre_caja, info=None):
        """
        Registra un agente (caja) en el sistema.
        id_sucursal: ID de la sucursal
        nombre_caja: Nombre de la caja
        info: Información adicional (metadata)
        """
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
        """
        Registra esta máquina como un agente local.
        Se llama al iniciar el servidor.
        """
        info = {
            "host": socket.gethostname(),
            "terminal_id": ID_TERMINAL,
            "usa_pos_fisico": USAR_POS_FISICO,
            "local": True
        }
        self.register_agent(ID_SUCURSAL, NOMBRE_CAJA, info)
        logger.info("Agente local registrado %s/%s", ID_SUCURSAL, NOMBRE_CAJA)

    def start_local_worker(self):
        """
        Inicia un hilo worker que procesa la cola LOCAL.
        Este worker saca tareas de la cola y las ejecuta (Transbank o MP).
        """
        self._stop_local_worker.clear()
        
        def worker():
            """Función del hilo worker"""
            logger.info("Local worker iniciado (procesa la cola local)...")
            
            q = self.ensure_queue(ID_SUCURSAL, NOMBRE_CAJA)
            
            while not self._stop_local_worker.is_set():
                try:
                    task = q.get(timeout=1)
                except queue.Empty:
                    continue
                
                try:
                    tx_id = task.get("tx_id")
                    tipo = task.get("type", "transbank")
                    logger.info("Local worker procesando tx=%s tipo=%s", tx_id, tipo)
                    
                    if tipo == "transbank":
                        amount = task.get("amount")
                        result = self.pos.do_sale_with_timeout(amount, timeout=MAX_TRANSACTION_TIME)
                    
                    elif tipo == "mercadopago":
                        terminal_id = task.get("id_terminal") or ID_TERMINAL
                        access_token = task.get("access_token")

                        amount = task.get("amount")
                        result = process_mercadopago(terminal_id, access_token, amount)
                    else:
                        result = {"status": "error", "message": "Tipo no soportado"}
                    # save result to tasks and free box
                    with self.tasks_lock:
                        entry = self.tasks.get(tx_id)
                        if entry:
                            entry["result"] = result
                            entry["event"].set()
                    with self.busy_lock:
                        self.busy_boxes.discard((task.get("id_sucursal"), task.get("nombre_caja")))
                    logger.info("Local worker finalizó tx=%s res=%s", tx_id, result)
                except Exception as e:
                    logger.error("Error en local worker: %s\n%s", e, traceback.format_exc())
        self.local_worker_thread = threading.Thread(target=worker, daemon=True)
        self.local_worker_thread.start()

    def stop_local_worker(self):
        self._stop_local_worker.set()
        try:
            if self.local_worker_thread:
                self.local_worker_thread.join(timeout=1)
        except:
            pass

    # --- Routes
    def setup_routes(self):
        @self.app.route("/register_agent", methods=["POST"])
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

        @self.app.route("/poll", methods=["GET"])
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
            except:
                pass
            return jsonify({"status": "ok"})

        @self.app.route("/pago", methods=["POST"])
        def http_pago():
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")
            pos_type = data.get("type")
            logger.info("[HTTP /pago] Petición recibida: %s", json.dumps(data, ensure_ascii=False))
            if id_sucursal is not None:
                id_sucursal = str(id_sucursal)
            if nombre_caja is not None:
                nombre_caja = str(nombre_caja)
            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "id_sucursal, nombre_caja y type requeridos"}), 400

            # check if this machine is target (local)
            is_local = (id_sucursal == str(ID_SUCURSAL) and nombre_caja == str(NOMBRE_CAJA))

            # Transbank flow
            if pos_type == "transbank":
                id_terminal = data.get("id_terminal", ID_TERMINAL)   # changed from pos_id -> id_terminal
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

                # If is local, enqueue to local queue (worker will process)
                if is_local:
                    with self.tasks_lock:
                        event = threading.Event()
                        self.tasks[tx_id] = {"event": event, "result": None, "id_sucursal": id_sucursal, "nombre_caja": nombre_caja, "timestamp": time.time()}
                    task_payload = {"tx_id": tx_id, "id_sucursal": id_sucursal, "nombre_caja": nombre_caja, "type": "transbank", "id_terminal": id_terminal, "amount": amount}
                    q = self.ensure_queue(id_sucursal, nombre_caja)
                    q.put(task_payload)
                    logger.info("Tarea local encolada tx=%s -> %s/%s", tx_id, id_sucursal, nombre_caja)
                    finished = self.tasks[tx_id]["event"].wait(timeout=TIMEOUT_SERVER)
                    if not finished:
                        with self.tasks_lock:
                            self.tasks.pop(tx_id, None)
                        self.internal_free_box(id_sucursal, nombre_caja, "timeout")
                        return jsonify({"status": "timeout", "transaction_id": tx_id, "message": f"Timeout {TIMEOUT_SERVER}s"}), 504
                    with self.tasks_lock:
                        res = self.tasks.pop(tx_id)["result"]
                    with self.busy_lock:
                        self.busy_boxes.discard(key)
                    return jsonify({"transaction_id": tx_id, "result": res})

                else:
                    # remote enqueue -> expect remote agent to poll
                    with self.tasks_lock:
                        event = threading.Event()
                        self.tasks[tx_id] = {"event": event, "result": None, "id_sucursal": id_sucursal, "nombre_caja": nombre_caja, "timestamp": time.time()}
                    task_payload = {"tx_id": tx_id, "id_sucursal": id_sucursal, "nombre_caja": nombre_caja, "type": "transbank", "id_terminal": id_terminal, "amount": amount}
                    q = self.ensure_queue(id_sucursal, nombre_caja)
                    q.put(task_payload)
                    logger.info("Tarea remota encolada tx=%s -> %s/%s", tx_id, id_sucursal, nombre_caja)
                    finished = event.wait(timeout=TIMEOUT_SERVER)
                    if not finished:
                        with self.tasks_lock:
                            self.tasks.pop(tx_id, None)
                        self.internal_free_box(id_sucursal, nombre_caja, "timeout")
                        return jsonify({"status": "timeout", "transaction_id": tx_id, "message": f"Timeout {TIMEOUT_SERVER}s"}), 504
                    with self.tasks_lock:
                        res = self.tasks.pop(tx_id)["result"]
                    with self.busy_lock:
                        self.busy_boxes.discard(key)
                    return jsonify({"transaction_id": tx_id, "result": res})

            # MercadoPago flow
            elif pos_type == "mercadopago":
                terminal_id = data.get("terminal_id", ID_TERMINAL)
                access_token = data.get("access_token")
                amount = data.get("amount")
                if access_token is None or amount is None:
                    return jsonify({"error": "Faltan campos mercadopago"}), 400
                if ALLOWED_MP and f"{id_sucursal}:{nombre_caja}" not in ALLOWED_MP:
                    return jsonify({"status": "forbidden", "message": "Caja no autorizada para MP"}), 403
                res = process_mercadopago(terminal_id, access_token, amount)
                return jsonify(res), 200
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
                "id_terminal": ID_TERMINAL,
                "usa_pos_fisico": USAR_POS_FISICO,
                "agents_count": agents_count,
                "current_port": self.pos.get_current_port()
            })

        @self.app.route("/debug/queues")
        def debug_queues():
            with self.queues_lock:
                info = {}
                for cid, boxes in self.queues.items():
                    for box, q in boxes.items():
                        info[f"{cid}:{box}"] = {"queued": q.qsize()}
            return jsonify(info)

    def internal_free_box(self, id_sucursal, nombre_caja, reason=""):
        key = (id_sucursal, nombre_caja)
        with self.busy_lock:
            if key in self.busy_boxes:
                self.busy_boxes.discard(key)
                logger.info("Caja %s/%s liberada (%s)", id_sucursal, nombre_caja, reason)

    def run(self):
        logger.info("Servidor Flask arrancando en puerto %s", HTTP_PORT)
        # Note: use_reloader=False to avoid double-start when developing
        self.app.run(host="0.0.0.0", port=HTTP_PORT, threaded=True, use_reloader=False)

# -----------------------
# Tray icon (pystray) helpers
# -----------------------
class TrayIcon:
    def __init__(self, pos_module):
        self.icon = None
        self.pos_module = pos_module

    def create_image(self):
        size = (64, 64)
        img = Image.new("RGBA", size, (255, 255, 255, 255))  # fondo blanco
        dc = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 28)
        except:
            font = None
        dc.text((10, 15), "T", fill=(128, 0, 128, 255), font=font)   # morado
        dc.text((35, 15), "M", fill=(255, 215, 0, 255), font=font)   # amarillo
        return img

    def on_quit(self, icon, item):
        logger.info("Tray -> salir solicitado")
        try:
            cleanup_lock()
        finally:
            os._exit(0)

    def on_open_log(self, icon, item):
        try:
            os.startfile(LOG_FILE)
        except Exception as e:
            logger.error("No se pudo abrir log: %s", e)

    def on_restart_pos(self, icon, item):
        try:
            logger.info("Tray -> Reiniciar POS manualmente")
            new_port = self.pos_module.restart()
            logger.info("Nuevo puerto detectado: %s", new_port)
        except Exception as e:
            logger.error("Error reiniciando POS desde tray: %s", e)

    def run(self):
        if not SYSTRAY_AVAILABLE:
            logger.info("pystray no disponible, no se muestra icono")
            return
        try:
            menu = (
                pystray.MenuItem(f"{APP_NAME}", lambda : None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Ver Log", self.on_open_log),
                pystray.MenuItem("Reiniciar POS", self.on_restart_pos),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", self.on_quit)
            )
            icon = pystray.Icon(APP_NAME, self.create_image(), APP_NAME, menu=pystray.Menu(*menu))
            self.icon = icon
            icon.run()
        except Exception as e:
            logger.error("Tray icon error: %s", e)

# -----------------------
# MAIN
# -----------------------
def main():
    print("="*60)
    print("POS PAYMENT GATEWAY - TODO EN UNO")
    print("="*60)
    print("HTTP_PORT:", HTTP_PORT)
    print("ID_SUCURSAL:", ID_SUCURSAL)
    print("NOMBRE_CAJA:", NOMBRE_CAJA)
    print("ID_TERMINAL:", ID_TERMINAL)
    print("USAR_POS_FISICO:", USAR_POS_FISICO)
    print("="*60)

    logger.info("Iniciando POS Gateway...")

    if not ensure_single_instance():
        print("Ya hay otra instancia corriendo. Saliendo.")
        return

    open_firewall_port(HTTP_PORT)

    pos_module = POSModule(PUERTOS_COM)
    pos_module.start_monitor()

    server = APIServer(pos_module)

    # start tray icon
    if SYSTRAY_AVAILABLE:
        tray = TrayIcon(pos_module)
        t = threading.Thread(target=tray.run, daemon=True)
        t.start()

    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("Interrupción por teclado")
    except Exception as e:
        logger.error("Error fatal: %s\n%s", e, traceback.format_exc())
    finally:
        server.stop_local_worker()
        pos_module.stop_monitor()
        cleanup_lock()
        logger.info("Aplicación finalizada")

if __name__ == "__main__":
    main()
    