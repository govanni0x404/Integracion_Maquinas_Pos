# pos/pos_module.py
import time
import threading
import queue
import gc
import logging
from pathlib import Path
from config.settings import PUERTOS_COM, USAR_POS_FISICO, MAX_TRANSACTION_TIME

logger = logging.getLogger()

# Si tienes el SDK de transbank disponible, el código real irá en los TODOs.
# IMPORTANTE: pega aquí tus funciones originales donde marqué TODO.

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
        """
        Lista todos los puertos COM disponibles en el sistema.
        Si tienes `serial.tools.list_ports` en tu código original, reemplaza aquí.
        """
        try:
            # TODO: si usas pyserial, reemplaza esta implementación con:
            # ports = [p.device for p in serial.tools.list_ports.comports()]
            # return ports
            import serial.tools.list_ports as _lps  # type: ignore
            ports = [p.device for p in _lps.comports()]
            return ports
        except Exception:
            # fallback vacío si no está instalado
            return []

    def detect_port(self):
        """
        Detecta en qué puerto COM está conectado el POS.
        Intenta primero los puertos preferidos, luego el resto.
        Retorna el puerto detectado o None si no encuentra.
        """
        if not USAR_POS_FISICO:
            logger.debug("POS físico deshabilitado por configuración")
            return None

        ports = self.list_ports()
        logger.info("Puertos detectados: %s | Preferidos: %s", ports, ",".join(self.prefer_ports))

        ordered = [p for p in self.prefer_ports if p in ports] + [p for p in ports if p not in self.prefer_ports]

        for p in ordered:
            pos = None
            try:
                # TODO: Aquí va la lógica real del SDK Transbank (crear POSIntegrado, open_port, poll)
                # EJEMPLO (pegá tu código real):
                # pos = POSIntegrado()
                # if pos.open_port(p) and pos.poll():
                #     pos.close_port()
                #     with self.lock:
                #         self.current_port = p
                #         self._is_online = True
                #
                #     self.last_ok = time.time()
                #     return p

                # Placeholder (intenta abrir/chequear)
                # Si quieres simular, considera que el primer puerto preferido funciona:
                logger.debug("Probando puerto (simulado): %s", p)
                # Simulación: no marcar online aquí; espera que pegues tu lógica real
            except Exception as e:
                logger.debug("Puerto %s no usable: %s", p, e)
            finally:
                try:
                    if pos:
                        # si pegaste pos, intenta cerrar
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
        """
        Abre el puerto especificado y ejecuta una venta.
        Retorna un dict con el resultado de la venta.
        TODO: Pega aquí tu implementación original de `open_port_and_sale` / `do_sale`.
        """
        # Si tienes el SDK Transbank disponible, copia aquí tu lógica original:
        # try:
        #     pos = POSIntegrado()
        #     if not pos.open_port(port):
        #         return {"status": "error", "message": f"No se pudo abrir {port}"}
        #     ticket = time.strftime("%H%M%S")
        #     res = pos.sale(amount, ticket)
        #     ...
        # finally:
        #     pos.close_port()
        #
        return {"status": "error", "message": "Transbank SDK no integrado - pega tu lógica en pos_module.open_port_and_sale"}

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
                            # TODO: si pegaste la lógica de POSIntegrado, aquí debería verificarse
                            # pos = POSIntegrado()
                            # ok = pos.open_port(p) and pos.poll()
                            # pos.close_port()
                            # if not ok: limpiar puerto...
                            pass
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
        """
        Útil para cuando el POS se desconecta y reconecta.
        """
        logger.info("Reiniciando POS module (clear port + redetect)...")
        with self.lock:
            self.current_port = None
            self._is_online = False
        return self.detect_port()
