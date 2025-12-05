import time
import threading
import queue
import gc
import logging
import traceback
import serial
import serial.tools.list_ports
from pathlib import Path
from config.settings import APP_NAME,PUERTOS_COM, USAR_POS_FISICO, MAX_TRANSACTION_TIME

logger = logging.getLogger(APP_NAME)

try:
    import serial.tools.list_ports
    from transbank import POSIntegrado
    TRANSBANK_AVAILABLE = True
except Exception:
    TRANSBANK_AVAILABLE = False

class POSModule:
    """
    Clase que maneja el POS físico Transbank:
    - Detecta automáticamente el puerto COM
    - Mantiene un caché del puerto detectado
    - Ejecuta ventas con timeout
    - Monitorea constantemente el POS
    """

    def __init__(self, prefer_ports: str = PUERTOS_COM):
        self.prefer_ports = [p.strip() for p in prefer_ports.split(",") if p.strip()]
        self.current_port = None
        self.lock = threading.Lock()
        self._stop_monitor = threading.Event()
        self.monitor_thread = None
        self.last_ok = 0
        self._is_online = False

    def is_online(self):
        with self.lock:
            return self._is_online

    def list_ports(self):
        """Lista los puertos COM disponibles."""
        try:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            return ports
        except Exception:
            return []

    def detect_port(self):
        """Detecta en qué puerto COM está conectado el POS."""
        if not USAR_POS_FISICO:
            logger.debug("POS físico deshabilitado por configuración")
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
                    except Exception:
                        pass

                    logger.info("POS detectado en %s", p)
                    with self.lock:
                        self.current_port = p
                        self._is_online = True

                    self.last_ok = time.time()
                    return p
            except Exception as e:
                logger.debug("Puerto %s no usable: %s", p, e)
            finally:
                try:
                    if pos:
                        pos.close_port()
                except Exception:
                    pass

        logger.warning("No se detectó POS en los puertos listados")
        with self.lock:
            self.current_port = None
            self._is_online = False
        return None

    def get_current_port(self):
        with self.lock:
            return self.current_port

    def open_port_and_sale(self, port, amount):
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
            except Exception:
                pass
            gc.collect()

    def do_sale_with_timeout(self, amount, timeout=MAX_TRANSACTION_TIME):
        """Ejecuta una venta con timeout para evitar bloqueos infinitos."""
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
        """Inicia un hilo monitor que verifica constantemente el POS."""
        if not USAR_POS_FISICO:
            logger.info("Monitor POS no iniciado (USAR_POS_FISICO=false)")
            return

        if self.monitor_thread and self.monitor_thread.is_alive():
            return

        self._stop_monitor.clear()

        def monitor():
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
        self._stop_monitor.set()
        try:
            if self.monitor_thread:
                self.monitor_thread.join(timeout=1)
        except Exception:
            pass
        logger.info("Monitor POS detenido")

    def restart(self):
        """Reinicia el módulo del POS."""
        logger.info("Reiniciando POS module (clear port + redetect)...")
        with self.lock:
            self.current_port = None
            self._is_online = False
        return self.detect_port()
    
    def open_port_and_refund(self, port, operation_id):
        if not TRANSBANK_AVAILABLE:
            return {"status": "error", "message": "Transbank SDK no disponible"}

        pos = None
        try:
            pos = POSIntegrado()
            if not pos.open_port(port):
                return {"status": "error", "message": f"No se pudo abrir {port}"}

            logger.info("Anulación POS -> puerto=%s operation_id=%s", port, operation_id)

            res = pos.refund(operation_id)
            logger.info("Respuesta anulación POS: %s", res)

            if res.get("response_code") in ("0", "00"):
                self.last_ok = time.time()
                return {"status": "success", "response": res}
            else:
                return {"status": "failed", "response": res}

        except Exception as e:
            logger.error("Error do_refund: %s\n%s", e, traceback.format_exc())
            return {"status": "error", "message": str(e)}
        finally:
            try:
                if pos:
                    pos.close_port()
            except Exception:
                pass
            gc.collect()


    def do_refund_with_timeout(self, operation_id, timeout=MAX_TRANSACTION_TIME):
        port = self.get_current_port() or self.detect_port()

        if not port:
            return {"status": "error", "message": "No se detectó POS conectado"}

        result_queue = queue.Queue(maxsize=1)

        def worker():
            try:
                res = self.open_port_and_refund(port, operation_id)
                result_queue.put(res)
            except Exception as e:
                result_queue.put({"status": "error", "message": str(e)})

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout=timeout)

        if t.is_alive():
            logger.error("Timeout en anulación POS")
            return {"status": "error", "message": "Timeout en anulación POS"}

        try:
            return result_queue.get_nowait()
        except queue.Empty:
            return {"status": "error", "message": "No se obtuvo respuesta"}
        
    def open_port_and_details(self, port, print_on_pos=False):
        if not TRANSBANK_AVAILABLE:
            return {"status": "error", "message": "Transbank SDK no disponible"}

        pos = None
        try:
            pos = POSIntegrado()
            if not pos.open_port(port):
                return {"status": "error", "message": f"No se pudo abrir {port}"}

            logger.info("Obteniendo detalle POS -> puerto=%s, print_on_pos=%s", port, print_on_pos)

            res = pos.details(print_on_pos)
            logger.info("Respuesta detalle POS: %s", res)

            if res.get("response_code") in ("0", "00"):
                self.last_ok = time.time()
                return {"status": "success", "response": res}
            else:
                return {"status": "failed", "response": res}

        except Exception as e:
            logger.error("Error do_details: %s\n%s", e, traceback.format_exc())
            return {"status": "error", "message": str(e)}
        finally:
            try:
                if pos:
                    pos.close_port()
            except Exception:
                pass
            gc.collect()


    def do_details_with_timeout(self, print_on_pos=False, timeout=MAX_TRANSACTION_TIME):
        """Obtiene el detalle de la última transacción con timeout."""
        port = self.get_current_port() or self.detect_port()

        if not port:
            return {"status": "error", "message": "No se detectó POS conectado"}

        result_queue = queue.Queue(maxsize=1)

        def worker():
            try:
                res = self.open_port_and_details(port, print_on_pos)
                result_queue.put(res)
            except Exception as e:
                result_queue.put({"status": "error", "message": str(e)})

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout=timeout)

        if t.is_alive():
            logger.error("Timeout obteniendo detalle POS")
            return {"status": "error", "message": "Timeout obteniendo detalle POS"}

        try:
            return result_queue.get_nowait()
        except queue.Empty:
            return {"status": "error", "message": "No se obtuvo respuesta"}