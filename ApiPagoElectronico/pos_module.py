import time
import gc
import queue
import threading
import traceback
from config import USAR_POS_FISICO, MAX_TRANSACTION_TIME
from logger_config import logger

# Importaciones opcionales
try:
    import serial.tools.list_ports
    from transbank import POSIntegrado
    TRANSBANK_AVAILABLE = True
except Exception:
    TRANSBANK_AVAILABLE = False
    logger.warning("SDK Transbank no disponible")


class POSModule:    
    def __init__(self, prefer_ports):
        self.prefer_ports = [p.strip() for p in prefer_ports.split(",") if p.strip()]
        self.current_port = None
        self.lock = threading.Lock()
        self._stop_monitor = threading.Event()
        self.monitor_thread = None
        self.last_ok = 0
        self._is_online = False  # Estado de conexión del POS
        
        logger.info("POSModule inicializado con puertos: %s", ", ".join(self.prefer_ports))
    
    def list_ports(self):
        try:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            return ports
        except Exception as e:
            logger.debug("Error listando puertos: %s", e)
            return []
    
    def detect_port(self):
        if not USAR_POS_FISICO or not TRANSBANK_AVAILABLE:
            logger.debug("POS físico deshabilitado o SDK no presente")
            return None
        
        ports = self.list_ports()
        logger.info("Puertos detectados: %s | Preferidos: %s", 
                   ports, ",".join(self.prefer_ports))
        
        # Ordena: primero preferidos, luego el resto
        ordered = [p for p in self.prefer_ports if p in ports] + \
                 [p for p in ports if p not in self.prefer_ports]
        
        # Prueba cada puerto
        for p in ordered:
            pos = None
            try:
                pos = POSIntegrado()
                
                if pos.open_port(p) and pos.poll():
                    # POS detectado!
                    try:
                        pos.close_port()
                    except:
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
                except:
                    pass
        
        logger.warning("No se detectó POS en los puertos listados")
        
        with self.lock:
            self.current_port = None
            self._is_online = False
        
        return None
    
    def is_online(self):
        with self.lock:
            return self._is_online
    
    def get_current_port(self):
        with self.lock:
            return self.current_port
    
    def open_port_and_sale(self, port, amount):
        if not TRANSBANK_AVAILABLE:
            return {"status": "error", "message": "SDK Transbank no disponible"}
        
        pos = None
        try:
            pos = POSIntegrado()
            if not pos.open_port(port):
                return {"status": "error", "message": f"No se pudo abrir {port}"}
            
            ticket = time.strftime("%H%M%S")
            logger.info("Venta POS: puerto=%s monto=%s ticket=%s", port, amount, ticket)
            
            res = pos.sale(amount, ticket)
            logger.info("Respuesta POS: %s", res)
            
            # Verifica código de respuesta
            if res.get("response_code") in ("0", "00"):
                self.last_ok = time.time()
                with self.lock:
                    self._is_online = True
                return {"status": "success", "response": res}
            else:
                return {"status": "failed", "response": res}
        
        except Exception as e:
            logger.error("Error en venta: %s\n%s", e, traceback.format_exc())
            return {"status": "error", "message": str(e)}
        finally:
            try:
                if pos:
                    pos.close_port()
            except:
                pass
            gc.collect()
    
    def do_sale_with_timeout(self, amount, timeout=MAX_TRANSACTION_TIME):
        logger.info("🔧 do_sale_with_timeout: monto=%s, timeout=%s", amount, timeout)
        
        port = self.get_current_port() or self.detect_port()
        
        if not port:
            logger.error("No se detectó puerto POS")
            return {"status": "error", "message": "POS no conectado"}
        
        logger.info("Puerto POS: %s", port)
        
        result_queue = queue.Queue(maxsize=1)
        
        def worker():
            try:
                logger.info("Ejecutando venta en puerto %s...", port)
                res = self.open_port_and_sale(port, amount)
                logger.info("Resultado venta: %s", res.get("status"))
                result_queue.put(res)
            except Exception as e:
                logger.error("Error en worker venta: %s", e)
                result_queue.put({"status": "error", "message": str(e)})
        
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        logger.info("Esperando venta (timeout=%s)...", timeout)
        
        t.join(timeout=timeout)
        
        if t.is_alive():
            logger.error("TIMEOUT en venta POS después de %s segundos", timeout)
            return {"status": "error", "message": f"Timeout en venta POS ({timeout}s)"}
        
        try:
            result = result_queue.get_nowait()
            logger.info("Venta completada: %s", result.get("status"))
            return result
        except queue.Empty:
            logger.error("No se obtuvo respuesta de la venta")
            return {"status": "error", "message": "Sin respuesta del POS"}
    
    def start_monitor(self, interval=5):
        if not USAR_POS_FISICO or not TRANSBANK_AVAILABLE:
            logger.info("Monitor POS no iniciado (POS físico deshabilitado)")
            return
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            logger.warning("Monitor POS ya está corriendo")
            return
        
        self._stop_monitor.clear()
        
        def monitor():
            logger.info("🔍 Monitor POS iniciado (intervalo=%ss)", interval)
            
            while not self._stop_monitor.is_set():
                try:
                    if not self.get_current_port():
                        # No hay puerto, intenta detectar
                        self.detect_port()
                    else:
                        # Hay puerto, verifica que siga funcionando
                        p = self.get_current_port()
                        try:
                            pos = POSIntegrado()
                            ok = pos.open_port(p) and pos.poll()
                            pos.close_port()
                            
                            with self.lock:
                                self._is_online = ok
                            
                            if not ok:
                                logger.warning("POS en %s dejó de responder", p)
                                with self.lock:
                                    self.current_port = None
                        except Exception as e:
                            logger.debug("Monitor error en %s: %s", p, e)
                            with self.lock:
                                self.current_port = None
                                self._is_online = False
                    
                    time.sleep(interval)
                    
                except Exception as e:
                    logger.debug("Monitor POS error: %s", e)
                    time.sleep(interval)
        
        self.monitor_thread = threading.Thread(target=monitor, daemon=True)
        self.monitor_thread.start()
        logger.info("Monitor POS thread iniciado")
    
    def stop_monitor(self):
        self._stop_monitor.set()
        try:
            if self.monitor_thread:
                self.monitor_thread.join(timeout=1)
        except:
            pass
        logger.info("Monitor POS detenido")
    
    def restart(self):
        logger.info("Reiniciando módulo POS...")
        with self.lock:
            self.current_port = None
            self._is_online = False
        return self.detect_port()