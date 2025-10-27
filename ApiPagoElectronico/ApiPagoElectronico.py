import os
import sys
import time
import uuid
import json
import base64
import hmac
import gc
import queue
import socket
import logging
import traceback
import threading
from dotenv import load_dotenv
import requests
import psutil
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime
from functools import lru_cache

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
# load_secure_env()

# CONFIGURACIÓN GLOBAL (En caso de fallar el archivo .env)
API_AUTH_USER = os.environ.get("API_AUTH_USER", "")
API_AUTH_PASS = os.environ.get("API_AUTH_PASS", "")

APP_NAME = os.environ.get("APP_NAME", "POS Gateway")

LOG_FILE = os.environ.get("LOG_FILE", "pos_gateway.log")

LOCK_FILE = os.environ.get("LOCK_FILE", "pos_gateway.lock")

HTTP_PORT = int(os.environ.get("HTTP_PORT", os.environ.get("PORT", "5005")))

ID_SUCURSAL = os.environ.get("ID_SUCURSAL", "1")
NOMBRE_CAJA = os.environ.get("NOMBRE_CAJA", socket.gethostname())
ID_TERMINAL = os.environ.get("ID_TERMINAL", os.environ.get(
    "TERMINAL_ID", f"POS_{NOMBRE_CAJA}"))

USAR_POS_FISICO = os.environ.get("USAR_POS_FISICO", "true").lower() == "true"
PUERTOS_COM = os.environ.get("PUERTOS_COM", "COM5,COM6,COM7,COM8")

MAX_TRANSACTION_TIME = int(os.environ.get("MAX_TRANSACTION_TIME", "90"))
TIMEOUT_SERVER = int(os.environ.get("TIMEOUT_SERVER", "120"))

ALLOWED_MP = set([x.strip() for x in os.environ.get(
    "ALLOWED_MP", "").split(",") if x.strip()])
MP_API_URL = os.environ.get(
    "MP_API_URL", "https://api.mercadopago.com/v1/orders")

# VALIDACION DE BASIC AUTH EN PETICION
def _unauthorized():
    """Respuesta 401 con encabezado WWW-Authenticate para que clientes pidan credenciales."""
    from flask import Response
    return Response(
        "Unauthorized",
        401,
        {"WWW-Authenticate": 'Basic realm="POS Gateway"'}
    )


def check_basic_auth_header(auth_header: str) -> bool:
    """Devuelve True si Authorization header es Basic y usuario/clave coinciden."""
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        encoded = auth_header[6:]
        decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")

        if ":" not in decoded:
            return False

        user, pwd = decoded.split(":", 1)
        return hmac.compare_digest(user, API_AUTH_USER) and hmac.compare_digest(pwd, API_AUTH_PASS)
    except Exception:
        return False


def require_basic_auth(fn):
    """Decorador Flask: rechaza con 401 si no viene Authorization Basic correcta."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        from flask import request
        if not API_AUTH_USER or not API_AUTH_PASS:
            logger.warning(
                "API_AUTH_USER/API_AUTH_PASS no definidos; rechazando petición por seguridad.")
            return _unauthorized()

        auth = request.headers.get("Authorization")
        if not check_basic_auth_header(auth):
            logger.warning(
                "[AUTH] Petición rechazada por credenciales inválidas. Header presente: %s", bool(auth))
            return _unauthorized()
        return fn(*args, **kwargs)
    return wrapper


# CONFIGURACIÓN DE LOGGING
logger = logging.getLogger(APP_NAME)
logger.setLevel(logging.INFO)

# Handler de archivo
file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=10*1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"))

# Handler de consola (solo si no es --noconsole)
if getattr(sys, "stdout", None):
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(console_handler)

logger.addHandler(file_handler)

# INSTANCIA ÚNICA
def ensure_single_instance():
    """
    Verifica si ya hay otra instancia corriendo.
    Usa un archivo de lock con el PID del proceso.
    Retorna True si puede correr, False si ya hay otra instancia.
    """
    lock_path = Path(LOCK_FILE)
    current_pid = os.getpid()

    if lock_path.exists():
        try:
            with open(lock_path, "r") as f:
                old_pid = int(f.read().strip())

            # Verifica si el proceso existe
            if psutil.pid_exists(old_pid):
                try:
                    proc = psutil.Process(old_pid)
                    # Verifica que sea el mismo programa
                    if "ApiPagoElectronico" in proc.name() or "python" in proc.name():
                        logger.error(
                            "Otra instancia detectada (PID=%s)", old_pid)
                        return False
                except psutil.NoSuchProcess:
                    pass

            # Si el proceso no existe, limpia el lock
            lock_path.unlink()

        except Exception as e:
            logger.warning("Error verificando lock file: %s", e)
            lock_path.unlink(missing_ok=True)

    # Crea el nuevo lock
    try:
        with open(lock_path, "w") as f:
            f.write(str(current_pid))
        logger.info("Lock file creado: %s (PID=%s)", lock_path, current_pid)
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
    Crea reglas inbound y outbound específicas para el puerto.
    Limpia duplicados si existen.
    """
    try:
        import subprocess
        rule_name = f"{APP_NAME}_Port_{port}"
        
        # Función helper para ejecutar netsh y loggear
        def run_netsh(cmd):
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            logger.debug("Netsh cmd: %s | Return: %s | Stdout: %s | Stderr: %s", cmd, res.returncode, res.stdout.strip()[:100], res.stderr.strip()[:100])
            return res
        
        # Chequea si regla inbound existe
        check_in_cmd = f'netsh advfirewall firewall show rule name="{rule_name}_Inbound"'
        result_in = run_netsh(check_in_cmd)
        
        # Si existe, borra para evitar duplicados
        if "No rules match" not in result_in.stdout:
            delete_in_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}_Inbound"'
            run_netsh(delete_in_cmd)
            logger.info("Regla inbound duplicada eliminada para puerto %s", port)
        
        # Añade regla inbound (entrada) específica
        add_in_cmd = f'netsh advfirewall firewall add rule name="{rule_name}_Inbound" dir=in action=allow protocol=TCP localport={port} remoteport=any profile=any'
        res_in = run_netsh(add_in_cmd)
        
        if res_in.returncode == 0:
            logger.info("Regla inbound creada para puerto %s (TCP, local={port})", port)
        else:
            logger.warning("Error creando inbound para %s: %s", port, res_in.stderr)
        
        # Chequea si regla outbound existe
        check_out_cmd = f'netsh advfirewall firewall show rule name="{rule_name}_Outbound"'
        result_out = run_netsh(check_out_cmd)
        
        # Si existe, borra para evitar duplicados
        if "No rules match" not in result_out.stdout:
            delete_out_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}_Outbound"'
            run_netsh(delete_out_cmd)
            logger.info("Regla outbound duplicada eliminada para puerto %s", port)
        
        # Añade regla outbound (salida) específica
        add_out_cmd = f'netsh advfirewall firewall add rule name="{rule_name}_Outbound" dir=out action=allow protocol=TCP localport={port} remoteport=any profile=any'
        res_out = run_netsh(add_out_cmd)
        
        if res_out.returncode == 0:
            logger.info("Regla outbound creada para puerto %s (TCP, local={port})", port)
        else:
            logger.warning("Error creando outbound para %s: %s", port, res_out.stderr)
            
    except Exception as e:
        logger.error("Error en open_firewall_port: %s\n%s", e, traceback.format_exc())


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
        self.prefer_ports = [p.strip()
                             for p in prefer_ports.split(",") if p.strip()]

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
        logger.info("Puertos detectados: %s | Preferidos: %s",
                    ports, ",".join(self.prefer_ports))

        ordered = [p for p in self.prefer_ports if p in ports] + \
            [p for p in ports if p not in self.prefer_ports]

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
                try:
                    if pos:
                        pos.close_port()
                except:
                    pass

        logger.warning("No se detectó POS en los puertos listados")

        with self.lock:
            self.current_port = None

        return None

    def get_current_port(self):
        """Obtiene el puerto de forma thread-safe"""
        with self.lock:
            return self.current_port

    def open_port_and_sale(self, port, amount):
        """
        Abre el puerto especificado y ejecuta una venta.
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
            logger.info("Venta POS -> puerto=%s monto=%s ticket=%s",
                        port, amount, ticket)

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
        Retorna el resultado de la venta o error de timeout.
        """
        port = self.get_current_port() or self.detect_port()

        if not port:
            return {"status": "error", "message": "No se detectó POS conectado"}

        result_queue = queue.Queue(maxsize=1)

        def worker():
            try:
                res = self.open_port_and_sale(port, amount)
                result_queue.put(res)
            except Exception as e:
                result_queue.put({"status": "error", "message": str(e)})

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        t.join(timeout=timeout)

        if t.is_alive():
            logger.error("Timeout en venta POS")
            return {"status": "error", "message": "Timeout en venta POS"}
        try:
            return result_queue.get_nowait()
        except queue.Empty:
            return {"status": "error", "message": "No se obtuvo respuesta"}

    def start_monitor(self, interval=5):
        """
        Inicia un hilo monitor que verifica constantemente el POS.
        """
        if not USAR_POS_FISICO or not TRANSBANK_AVAILABLE:
            logger.info(
                "Monitor POS no iniciado (no use POS físico o SDK falta)")
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
                                logger.warning(
                                    "POS en %s dejó de responder, limpiando puerto.", p)
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
        Útil para cuando el POS se desconecta y reconecta.
        """
        logger.info("Reiniciando POS module (clear port + redetect)...")
        with self.lock:
            self.current_port = None
        return self.detect_port()

# MERCADO PAGO
def process_mercadopago(terminal_id, access_token, amount, timeout=TIMEOUT_SERVER):
    """
    Procesa un pago con Mercado Pago Point.
    Retorna un dict con el resultado.
    """
    logger.info("Procesando MercadoPago terminal=%s monto=%s",terminal_id, amount, timeout)

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
        resp = requests.post(MP_API_URL, json=payload,
                             headers=headers, timeout=15)
        data_resp = resp.json()

        if resp.status_code != 201:
            logger.warning("Error creando orden MP: %s %s", resp.status_code, data_resp)
            return {"status": "failed", "http_status": resp.status_code, "response": data_resp}

        order_id = data_resp["id"]
        logger.info("Orden MP creada: %s; esperando resultado...", order_id, timeout)

        start = time.time()
        while time.time() - start < timeout:
            check = requests.get(
                f"{MP_API_URL}/{order_id}", headers=headers, timeout=10)
            order_info = check.json()
            status = order_info.get("status")

            if status in ("created", "in_process", "at_terminal"):
                time.sleep(3)
                continue

            logger.info("Orden %s finalizada con estado: %s", order_id, status)
            return {"status": status, "order_id": order_id, "response": order_info}

        logger.warning("Timeout esperando respuesta MP orden %s", order_id, timeout)
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
        """
        if not FLASK_AVAILABLE:
            logger.critical(
                "Flask no instalado - no se puede iniciar servidor")
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

        self.cleanup_thread = None
        self._stop_cleanup = threading.Event()

        self.setup_routes()
        self.register_local_agent()
        self.start_local_worker()
        self.start_cleanup_task()

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
                        t.get("id_sucursal") == id_sucursal and t.get(
                            "nombre_caja") == nombre_caja
                        for t in self.tasks.values()
                    )

                if not active:
                    self.busy_boxes.discard(key)
                    logger.info("Caja %s/%s liberada por reconexión",
                                id_sucursal, nombre_caja)

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
        """Worker que procesa la cola LOCAL"""
        self._stop_local_worker.clear()

        def worker():
            logger.info("Local worker iniciado")
            q = self.ensure_queue(ID_SUCURSAL, NOMBRE_CAJA)

            while not self._stop_local_worker.is_set():
                try:
                    task = q.get(timeout=1)
                except queue.Empty:
                    continue

                try:
                    tx_id = task.get("tx_id")
                    tipo = task.get("type", "transbank")
                    custom_timeout = task.get("timeout", MAX_TRANSACTION_TIME)

                    logger.info("Procesando: tx=%s, tipo=%s, timeout=%s",
                                tx_id, tipo, custom_timeout)

                    # ACTUALIZA ESTADO A "PROCESANDO"
                    with self.tasks_lock:
                        if tx_id in self.tasks:
                            self.tasks[tx_id]["estado"] = "PROCESANDO"

                    logger.info(
                        "Local worker procesando tx=%s tipo=%s", tx_id, tipo)

                    if tipo == "transbank":
                        amount = task.get("amount")
                        logger.info(
                            "Venta Transbank: monto=%s, timeout=%s", amount, custom_timeout)

                        result = self.pos.do_sale_with_timeout(
                            amount, timeout=custom_timeout)

                        logger.info(" Resultado: %s", result.get("status"))

                    elif tipo == "mercadopago":
                        terminal_id = task.get("id_terminal") or ID_TERMINAL
                        access_token = task.get("access_token")
                        amount = task.get("amount")
                        result = process_mercadopago(
                            terminal_id, access_token, amount)

                    else:
                        result = {"status": "error",
                                  "message": "Tipo no soportado"}

                    # GUARDA RESULTADO Y ACTUALIZA ESTADO
                    with self.tasks_lock:
                        entry = self.tasks.get(tx_id)
                        if entry:
                            entry["result"] = result

                            # Determina el estado final
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

                    # Libera la caja
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
                    except:
                        pass
        self.local_worker_thread = threading.Thread(target=worker, daemon=True)
        self.local_worker_thread.start()
        logger.info("Local worker thread iniciado")

    def start_cleanup_task(self):
        """Limpia automáticamente transacciones antiguas cada 5 minutos"""
        self._stop_cleanup.clear()  # ← AGREGAR

        def cleanup():
            while not self._stop_cleanup.is_set():  # ← MODIFICAR
                time.sleep(300)  # 5 minutos
                try:
                    now = time.time()
                    max_age = 600  # 10 minutos

                    with self.tasks_lock:
                        to_delete = [
                            tx_id for tx_id, task in self.tasks.items()
                            if now - task.get("timestamp", now) > max_age
                        ]
                        for tx_id in to_delete:
                            self.tasks.pop(tx_id, None)

                    if to_delete:
                        logger.info(
                            "Limpieza automática: %d transacciones eliminadas", len(to_delete))
                except Exception as e:
                    logger.error("Error en cleanup task: %s", e)

        self.cleanup_thread = threading.Thread(
            target=cleanup, daemon=True)  # ← MODIFICAR
        self.cleanup_thread.start()  # ← MODIFICAR
        logger.info("Tarea de limpieza automática iniciada")

    def stop_cleanup_task(self):
        """Detiene la tarea de limpieza"""
        self._stop_cleanup.set()
        try:
            if self.cleanup_thread:
                self.cleanup_thread.join(timeout=1)
        except:
            pass
        logger.info("Tarea de limpieza detenida")

    # RUTAS HTTP (ENDPOINTS)
    def setup_routes(self):
        """Define todas las rutas/endpoints del servidor Flask"""

        # POST /register_agent
        # Endpoint para que agentes remotos se registren
        @self.app.route("/register_agent", methods=["POST"])
        def http_register_agent():
            """Registra un agente en el sistema"""
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

        # GET /poll
        # Endpoint para que agentes remotos pidan tareas
        @self.app.route("/poll", methods=["GET"])
        def http_poll():
            """Agentes remotos hacen polling para obtener tareas"""
            id_sucursal = request.args.get("id_sucursal")
            nombre_caja = request.args.get("nombre_caja")

            if not id_sucursal or not nombre_caja:
                return jsonify({"error": "id_sucursal y nombre_caja requeridos"}), 400

            self.register_agent(str(id_sucursal), str(nombre_caja))

            q = self.ensure_queue(str(id_sucursal), str(nombre_caja))

            try:
                task = q.get(timeout=2)
                logger.info("Despachando tarea -> %s/%s tx=%s",
                            id_sucursal, nombre_caja, task.get("tx_id"))
                return jsonify({"task": task})
            except queue.Empty:
                return jsonify({"task": None, "heartbeat": True})

        # POST /result
        # Endpoint para que agentes remotos reporten resultados
        @self.app.route("/result", methods=["POST"])
        def http_result():
            """Recibe el resultado de una tarea ejecutada por un agente remoto"""
            data = request.get_json(force=True, silent=True) or {}
            tx_id = data.get("tx_id")
            result = data.get("result")

            if not tx_id or result is None:
                return jsonify({"error": "tx_id y result requeridos"}), 400

            with self.tasks_lock:
                task = self.tasks.get(tx_id)
                if not task:
                    logger.warning(
                        "Resultado para tx_id desconocido: %s", tx_id)
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

        # POST /pago
        # Endpoint principal para procesar pagos
        @self.app.route("/pago", methods=["POST"])
        @require_basic_auth
        def http_pago():
            """Procesa un pago con timeout configurable"""
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = str(data.get("id_sucursal", ""))
            nombre_caja = str(data.get("nombre_caja", ""))
            pos_type = data.get("type")

            logger.info("[HTTP /pago] Petición: id_sucursal=%s, nombre_caja=%s, type=%s",
                        id_sucursal, nombre_caja, pos_type)

            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "id_sucursal, nombre_caja y type requeridos"}), 400

            is_local = (id_sucursal == str(ID_SUCURSAL)
                        and nombre_caja == str(NOMBRE_CAJA))

            custom_timeout = data.get("timeout")
            if custom_timeout:
                try:
                    timeout = int(custom_timeout)
                    #timeout = max(5, min(timeout, 300))
                    timeout = max(30, min(timeout, 300))
                    logger.info("Timeout personalizado: %s segundos", timeout)
                except (ValueError, TypeError):
                    logger.warning("Timeout inválido, usando default: %s", timeout)
                    timeout = TIMEOUT_SERVER
            else:
                timeout = TIMEOUT_SERVER

            logger.info("Configuración: timeout=%s, is_local=%s",
                        timeout, is_local)

            # FLUJO TRANSBANK
            if pos_type == "transbank":
                id_terminal = data.get("id_terminal", ID_TERMINAL)
                amount = data.get("amount")

                if amount is None:
                    return jsonify({"error": "amount requerido"}), 400

                key = (id_sucursal, nombre_caja)

                # Verifica si está ocupada
                with self.busy_lock:
                    if key in self.busy_boxes:
                        logger.warning("Caja ocupada: %s/%s",
                                       id_sucursal, nombre_caja)
                        return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                    self.busy_boxes.add(key)

                tx_id = str(uuid.uuid4())

                # Crea la tarea
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

                # Crea el payload
                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": id_sucursal,
                    "nombre_caja": nombre_caja,
                    "type": "transbank",
                    "id_terminal": id_terminal,
                    "amount": amount,
                    "timeout": timeout
                }

                # Encola
                q = self.ensure_queue(id_sucursal, nombre_caja)
                q.put(task_payload)
                logger.info("Tarea encolada: tx=%s, timeout=%s, local=%s",
                            tx_id, timeout, is_local)

                # ESPERA CON EL TIMEOUT CORRECTO
                start_wait = time.time()
                logger.info("Esperando resultado (timeout=%s)...", timeout)
                finished = event.wait(timeout=timeout)
                wait_time = time.time() - start_wait

                if finished:
                    logger.info("Espera OK: tx=%s, duración=%.2fs", tx_id, wait_time)
                else:
                    logger.warning("Espera TIMEOUT: tx=%s, duración=%.2fs", tx_id, wait_time)

                logger.info("Espera finalizada: tx=%s, duración=%.2fs, finished=%s",
                            tx_id, wait_time, finished)

                # Si timeout
                if not finished:
                    with self.tasks_lock:
                        self.tasks.pop(tx_id, None)
                    self.internal_free_box(id_sucursal, nombre_caja, "timeout")
                    logger.error(
                        " TIMEOUT tx=%s después de %.2fs", tx_id, wait_time)
                    return jsonify({
                        "status": "timeout",
                        "transaction_id": tx_id,
                        "message": f"Timeout {timeout}s excedido (esperó {wait_time:.1f}s)"
                    }), 504

                # Obtiene resultado
                with self.tasks_lock:
                    entry = self.tasks.pop(tx_id, {})
                    res = entry.get("result")
                    final_estado = entry.get("estado", "DESCONOCIDO")

                # Libera caja
                with self.busy_lock:
                    self.busy_boxes.discard(key)

                logger.info("Respuesta: tx=%s, estado=%s, tiempo=%.2fs",
                            tx_id, final_estado, wait_time)

                return jsonify({
                    "transaction_id": tx_id,
                    "result": res,
                    "estado": final_estado,
                    "tiempo_total": round(wait_time, 2)
                })

            # FLUJO MERCADO PAGO
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

        # GET /status
        # Endpoint para ver el estado del servidor
        @self.app.route("/status")
        def http_status():
            """Retorna información del estado del servidor"""
            with self.agents_lock:
                # Cuenta cuántos agentes hay registrados
                agents_count = sum(len(boxes)
                                   for boxes in self.agents.values())

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

        # GET /debug/queues
        # Endpoint de debug para ver estado de las colas
        @self.app.route("/debug/queues")
        def debug_queues():
            """Retorna información de las colas (para debug)"""
            with self.queues_lock:
                info = {}
                for cid, boxes in self.queues.items():
                    for box, q in boxes.items():
                        info[f"{cid}:{box}"] = {"queued": q.qsize()}
            return jsonify(info)

        # NUEVA RUTA: Iniciar pago sin esperar
        @self.app.route("/pago/iniciar", methods=["POST"])
        @require_basic_auth
        def http_pago_iniciar():
            """
            Inicia un pago y retorna inmediatamente con un transaction_id.
            El cliente debe usar /pago/estado para consultar el resultado.
            """
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")
            pos_type = data.get("type")

            logger.info("[HTTP /pago/iniciar] Petición recibida: %s",
                        json.dumps(data, ensure_ascii=False))

            if id_sucursal is not None:
                id_sucursal = str(id_sucursal)
            if nombre_caja is not None:
                nombre_caja = str(nombre_caja)

            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "id_sucursal, nombre_caja y type requeridos"}), 400

            # Verifica si la caja está ocupada
            key = (id_sucursal, nombre_caja)
            with self.busy_lock:
                if key in self.busy_boxes:
                    logger.warning("Caja ocupada: %s/%s",
                                   id_sucursal, nombre_caja)
                    return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                self.busy_boxes.add(key)

            tx_id = str(uuid.uuid4())

            custom_timeout = data.get("timeout", MAX_TRANSACTION_TIME)
            try:
                custom_timeout = max(30, min(int(custom_timeout), 300))
            except:
                custom_timeout = MAX_TRANSACTION_TIME

            # SOLO TRANSBANK POR AHORA
            if pos_type == "transbank":
                amount = data.get("amount")
                id_terminal = data.get("id_terminal", ID_TERMINAL)

                if amount is None:
                    with self.busy_lock:
                        self.busy_boxes.discard(key)
                    return jsonify({"error": "amount requerido"}), 400

                # Crea la tarea en estado PENDIENTE
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

                # Encola la tarea
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

                logger.info("Pago iniciado tx=%s -> %s/%s (respuesta inmediata)",
                            tx_id, id_sucursal, nombre_caja)

                # RESPONDE INMEDIATAMENTE
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

        # NUEVA RUTA: Consultar estado de un pago
        @self.app.route("/pago/estado/<tx_id>", methods=["GET"])
        @require_basic_auth
        def http_pago_estado(tx_id):
            """
            Consulta el estado de una transacción.
            """
            with self.tasks_lock:
                task = self.tasks.get(tx_id)

                if not task:
                    return jsonify({
                        "error": "Transacción no encontrada",
                        "transaction_id": tx_id
                    }), 404

                # Si aún está pendiente
                if task.get("estado") == "PENDIENTE" and task["result"] is None:
                    return jsonify({
                        "transaction_id": tx_id,
                        "estado": "PENDIENTE",
                        "tiempo_transcurrido": int(time.time() - task["timestamp"])
                    }), 200

                # Si ya terminó
                result = task.get("result")
                if result:
                    estado = "APROBADO" if result.get(
                        "status") == "success" else "RECHAZADO"
                    if result.get("status") == "error":
                        estado = "ERROR"

                # Actualiza el estado en la tarea
                task["estado"] = estado

                return jsonify({
                    "transaction_id": tx_id,
                    "estado": estado,
                    "result": result,
                    "tiempo_total": int(time.time() - task["timestamp"])
                }), 200

            # Estado desconocido
            return jsonify({
                "transaction_id": tx_id,
                "estado": "DESCONOCIDO"
            }), 200

        # NUEVA RUTA: Cancelar un pago pendiente
        @self.app.route("/pago/cancelar/<tx_id>", methods=["POST"])
        @require_basic_auth
        def http_pago_cancelar(tx_id):
            """
            Intenta cancelar un pago pendiente.
            """
            with self.tasks_lock:
                task = self.tasks.get(tx_id)

                if not task:
                    return jsonify({"error": "Transacción no encontrada"}), 404

                if task.get("result") is not None:
                    return jsonify({
                        "error": "La transacción ya finalizó",
                        "estado": task.get("estado")
                    }), 400

                # Marca como cancelada
                task["result"] = {"status": "cancelled","message": "Cancelado por usuario"}
                task["estado"] = "CANCELADO"
                task["event"].set()

                # Libera la caja
                id_sucursal = task.get("id_sucursal")
                nombre_caja = task.get("nombre_caja")
                self.internal_free_box(id_sucursal, nombre_caja, "cancelacion")

                logger.info("Transacción cancelada: %s", tx_id)

                return jsonify({
                    "status": "ok",
                    "transaction_id": tx_id,
                    "estado": "CANCELADO"
                }), 200

        # NUEVA RUTA: Limpiar transacciones antiguas
        @self.app.route("/pago/limpiar", methods=["POST"])
        @require_basic_auth
        def http_pago_limpiar():
            now = time.time()
            max_age = 600  # 10 minutos

            with self.tasks_lock:
                old_txs = [
                    tx_id for tx_id, task in self.tasks.items()
                    if now - task["timestamp"] > max_age
                ]

                for tx_id in old_txs:
                    self.tasks.pop(tx_id, None)

            logger.info("Limpiadas %d transacciones antiguas", len(old_txs))

            return jsonify({
                "status": "ok",
                "eliminadas": len(old_txs)
            }), 200

    def internal_free_box(self, id_sucursal, nombre_caja, reason=""):
        key = (id_sucursal, nombre_caja)
        with self.busy_lock:
            if key in self.busy_boxes:
                self.busy_boxes.discard(key)
                logger.info("Caja %s/%s liberada (%s)",
                            id_sucursal, nombre_caja, reason)

    def run(self):
        """Inicia el servidor Flask (bloqueante)"""
        logger.info("Servidor Flask arrancando en puerto %s", HTTP_PORT)
        self.app.run(host="0.0.0.0", port=HTTP_PORT,
                     threaded=True, use_reloader=False)

# ICONO EN BANDEJA DEL SISTEMA
# Muestra un icono en el system tray con menú
class TrayIcon:

    def __init__(self, pos_module):
        self.icon = None
        self.pos_module = pos_module

    def create_image(self):
        size = (64, 64)
        img = Image.new("RGBA", size, (255, 255, 255, 255))
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
        """Inicia el icono en la bandeja (bloqueante)"""
        if not SYSTRAY_AVAILABLE:
            logger.info("pystray no disponible, no se muestra icono")
            return

        try:
            menu = (
                pystray.MenuItem(f"{APP_NAME}", lambda: None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Ver Log", self.on_open_log),
                # pystray.MenuItem("Reiniciar POS", self.on_restart_pos),  # Opción: Reiniciar POS
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", self.on_quit)
            )

            # Crea el icono
            icon = pystray.Icon(APP_NAME, self.create_image(),
                                APP_NAME, menu=pystray.Menu(*menu))
            self.icon = icon

            # Inicia el icono (bloqueante - corre en el hilo actual)
            icon.run()

        except Exception as e:
            logger.error("Tray icon error: %s", e)

# FUNCIÓN PRINCIPAL
def main():
    # Banner de inicio
    print("=" * 60)
    print("POS PAYMENT GATEWAY - TODO EN UNO")
    print("=" * 60)
    print("HTTP_PORT:", HTTP_PORT)
    print("ID_SUCURSAL:", ID_SUCURSAL)
    print("NOMBRE_CAJA:", NOMBRE_CAJA)
    print("ID_TERMINAL:", ID_TERMINAL)
    print("USAR_POS_FISICO:", USAR_POS_FISICO)
    print("=" * 60)

    logger.info("Iniciando POS Gateway...")

    # Verifica instancia única
    if not ensure_single_instance():
        print("Ya hay otra instancia corriendo. Saliendo.")
        return

    # Intenta abrir puerto en firewall
    open_firewall_port(HTTP_PORT)

    # Crea el módulo POS
    pos_module = POSModule(PUERTOS_COM)

    # Inicia el monitor del POS (verifica constantemente si está conectado)
    pos_module.start_monitor()

    # Crea el servidor API
    server = APIServer(pos_module)

    # Inicia el icono de bandeja en un hilo separado
    if SYSTRAY_AVAILABLE:
        tray = TrayIcon(pos_module)
        t = threading.Thread(target=tray.run, daemon=True)
        t.start()

    try:
        # Inicia el servidor Flask (bloqueante - corre en el hilo principal)
        server.run()
    except KeyboardInterrupt:
        # Si el usuario presiona Ctrl+C
        logger.info("Interrupción por teclado")
    except Exception as e:
        # Error fatal
        logger.error("Error fatal: %s\n%s", e, traceback.format_exc())
    finally:
        # Limpieza al salir
        server.stop_local_worker()
        server.stop_cleanup_task()
        pos_module.stop_monitor()
        cleanup_lock()
        logger.info("Aplicación finalizada")

# PUNTO DE ENTRADA
if __name__ == "__main__":
    main()