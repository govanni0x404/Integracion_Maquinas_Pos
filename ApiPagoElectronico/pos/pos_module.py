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
from core.business_logging import transbank_log, fmt_monto

logger = logging.getLogger(APP_NAME)

try:
    #from transbank import POSIntegrado
    from transbank.POS.POSIntegrado import POSIntegrado
    TRANSBANK_AVAILABLE = True
    logger.info("SDK Transbank importado correctamente")
except ImportError as e:
    TRANSBANK_AVAILABLE = False
    logger.error("ImportError al cargar Transbank: %s", str(e))
except Exception as e:
    TRANSBANK_AVAILABLE = False
    logger.error("Error desconocido al cargar Transbank: %s", str(e))
    logger.error("Traceback completo:\n%s", traceback.format_exc())

logger.info("TRANSBANK_AVAILABLE = %s", TRANSBANK_AVAILABLE)

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

    def detect_port(self, exclude=None):
        """Detecta en qué puerto COM está conectado el POS.

        exclude: puertos que no se deben sondear (ej. el que ya usa Getnet),
        para no mandarle tramas de Transbank a otra máquina.
        """
        if not USAR_POS_FISICO:
            logger.debug("POS físico deshabilitado por configuración")
            return None

        ports = self.list_ports()
        logger.info("Puertos detectados: %s | Preferidos: %s", ports, ",".join(self.prefer_ports))

        ordered = [p for p in self.prefer_ports if p in ports] + [p for p in ports if p not in self.prefer_ports]
        excluded = {p for p in (exclude or []) if p}
        if excluded:
            ordered = [p for p in ordered if p not in excluded]
            logger.info("Puertos excluidos de la detección Transbank: %s", ",".join(sorted(excluded)))

        for p in ordered:
            pos = None
            try:
                logger.info("Probando puerto %s...", p)  # ← AGREGAR ESTE LOG
                pos = POSIntegrado()

                port_opened = [False]
                error_msg = [None]

                def try_open():
                    try:
                        port_opened[0] = pos.open_port(p)
                    except Exception as e:
                        error_msg[0] = str(e)
                        logger.warning("Error abriendo %s: %s", p, e)
                
                t = threading.Thread(target=try_open, daemon=True)
                t.start()
                t.join(timeout=3)  # ← TIMEOUT DE 3 SEGUNDOS
                
                if t.is_alive():
                    logger.warning("Timeout (3s) abriendo puerto %s", p)
                    continue
                
                if error_msg[0]:
                    logger.warning("Error en puerto %s: %s", p, error_msg[0])
                    continue
                
                if not port_opened[0]:
                    logger.warning("No se pudo abrir puerto %s", p)
                    continue

                logger.info("Puerto %s abierto, ejecutando poll...", p)
                
                # TIMEOUT PARA POLL
                poll_result = [False]
                
                def try_poll():
                    try:
                        poll_result[0] = pos.poll()
                    except Exception as e:
                        logger.warning("Error en poll %s: %s", p, e)
                
                t2 = threading.Thread(target=try_poll, daemon=True)
                t2.start()
                t2.join(timeout=2)  # ← TIMEOUT DE 2 SEGUNDOS
                
                if t2.is_alive():
                    logger.warning("Timeout en poll de puerto %s", p)
                    continue
                
                if poll_result[0]:
                    try:
                        pos.close_port()
                    except Exception:
                        pass

                    logger.info("✓ POS detectado en %s", p)
                    transbank_log.info(f"🔌 POS Transbank conectado — puerto {p}")
                    with self.lock:
                        self.current_port = p
                        self._is_online = True

                    self.last_ok = time.time()
                    return p
                else:
                    logger.warning("Puerto %s no respondió al poll", p)
                        
            except Exception as e:
                logger.warning("Puerto %s error: %s", p, e)
            finally:
                try:
                    if pos:
                        pos.close_port()
                except Exception as ex:
                    logger.debug("Error cerrando puerto %s: %s", p, ex)

        logger.warning("No se detectó POS en los puertos listados")
        transbank_log.info("🔌 No se detectó ningún POS Transbank conectado en los puertos disponibles.")
        with self.lock:
            self.current_port = None
            self._is_online = False
        return None

        #         if not pos.open_port(p):
        #             logger.warning("No se pudo abrir puerto %s", p)  # ← CAMBIAR A WARNING
        #             continue

        #         logger.info("Puerto %s abierto, ejecutando poll...", p)  # ← AGREGAR ESTE LOG
                
        #         if pos.poll():
        #             try:
        #                 pos.close_port()
        #             except Exception:
        #                 pass

        #             logger.info("✓ POS detectado en %s", p)
        #             with self.lock:
        #                 self.current_port = p
        #                 self._is_online = True

        #             self.last_ok = time.time()
        #             return p
        #         else:
        #             logger.warning("Puerto %s no respondió al poll", p)  # ← CAMBIAR A WARNING
                    
        #     except Exception as e:
        #         logger.warning("Puerto %s error: %s\n%s", p, e, traceback.format_exc())  # ← CAMBIAR A WARNING y AGREGAR TRACEBACK
        #     finally:
        #         try:
        #             if pos:
        #                 pos.close_port()
        #         except Exception as ex:
        #             logger.debug("Error cerrando puerto %s: %s", p, ex)  # ← AGREGAR LOG

        # logger.warning("No se detectó POS en los puertos listados")
        # with self.lock:
        #     self.current_port = None
        #     self._is_online = False
        # return None

    def get_current_port(self):
        with self.lock:
            return self.current_port

    def open_port_and_sale(self, port, amount, ticket=None):
        if not TRANSBANK_AVAILABLE:
            transbank_log.info(f"❌ No se pudo vender: el SDK de Transbank no está disponible en esta instalación.")
            return {"status": "error", "message": "Transbank SDK no disponible"}

        pos = None
        try:
            pos = POSIntegrado()
            transbank_log.info(f"🟢 Venta iniciada — monto {fmt_monto(amount)} — puerto {port}")
            if not pos.open_port(port):
                transbank_log.info(f"❌ No se pudo abrir el puerto {port}. No se llegó a enviar nada al POS, es seguro reintentar.")
                return {"status": "error", "message": f"No se pudo abrir {port}"}

            ticket = ticket or time.strftime("%H%M%S")
            logger.info("Venta POS -> puerto=%s monto=%s ticket=%s", port, amount, ticket)
            transbank_log.info(f"⏳ Venta enviada al POS (ticket {ticket}), esperando que el cliente pague...")

            res = pos.sale(amount, ticket)
            logger.info("Respuesta POS: %s", res)

            if res.get("response_code") in ("0", "00"):
                self.last_ok = time.time()
                transbank_log.info(
                    f"✅ Venta APROBADA — ticket {ticket} — monto {fmt_monto(amount)} — "
                    f"autorización {res.get('authorization_code')}"
                )
                return {"status": "success", "response": res}
            else:
                transbank_log.info(
                    f"⛔ Venta RECHAZADA — ticket {ticket} — monto {fmt_monto(amount)} — "
                    f"código {res.get('response_code')} — {res.get('response_message')}"
                )
                return {"status": "failed", "response": res}

        except Exception as e:
            logger.error("Error do_sale: %s\n%s", e, traceback.format_exc())
            transbank_log.info(f"❌ Error inesperado durante la venta (puerto {port}, monto {fmt_monto(amount)}): {e}")
            return {"status": "error", "message": str(e)}
        finally:
            try:
                if pos:
                    pos.close_port()
            except Exception:
                pass
            gc.collect()

    def do_sale_with_timeout(self, amount, timeout=MAX_TRANSACTION_TIME, ticket=None, on_late_result=None):
        """
        Ejecuta una venta con timeout para evitar bloqueos infinitos.

        Si se agota el timeout devuelve status "indeterminada": la venta ya se
        envió al POS y el cliente pudo haber pagado. El hilo de la venta no se
        puede matar, así que si el POS responde después, ese resultado tardío
        se entrega a on_late_result(res) para reconciliar la transacción.
        """
        port = self.get_current_port() or self.detect_port()

        if not port:
            transbank_log.info(f"❌ Venta rechazada: no hay ningún POS Transbank conectado (monto {fmt_monto(amount)}).")
            return {"status": "error", "message": "No se detectó POS conectado"}

        result_queue = queue.Queue(maxsize=1)
        state_lock = threading.Lock()
        state = {"timed_out": False}

        def worker():
            try:
                res = self.open_port_and_sale(port, amount, ticket=ticket)
            except Exception as e:
                res = {"status": "error", "message": str(e)}
            with state_lock:
                late = state["timed_out"]
                if not late:
                    result_queue.put(res)
            if late:
                logger.info("Resultado tardío Transbank (ticket %s): %s", ticket, res.get("status"))
                transbank_log.info(
                    f"📬 El POS respondió DESPUÉS del timeout — ticket {ticket} — monto {fmt_monto(amount)} — "
                    f"resultado {res.get('status')}"
                )
                if on_late_result:
                    try:
                        on_late_result(res)
                    except Exception as e:
                        logger.error("Error procesando resultado tardío Transbank: %s\n%s", e, traceback.format_exc())

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout=timeout)

        with state_lock:
            if result_queue.empty():
                state["timed_out"] = True

        if state["timed_out"]:
            logger.error("Timeout en venta POS (ticket %s)", ticket)
            transbank_log.info(
                f"⚠️ SIN CONFIRMACIÓN (timeout) — ticket {ticket} — monto {fmt_monto(amount)} — puerto {port}. "
                "La venta puede seguir procesándose en el POS: queda INDETERMINADA y la caja bloqueada "
                "hasta que el POS responda o se resuelva desde el panel."
            )
            return {
                "status": "indeterminada",
                "ticket": ticket,
                "message": "Timeout en venta POS: verificar el comprobante en la máquina antes de reintentar",
            }

        return result_queue.get_nowait()

    def start_monitor(self, interval=15):
        """Monitor inteligente: verifica puerto actual, re-detecta solo si se pierde"""
        if not USAR_POS_FISICO:
            logger.info("Monitor POS no iniciado (USAR_POS_FISICO=false)")
            return

        if self.monitor_thread and self.monitor_thread.is_alive():
            return

        self._stop_monitor.clear()

        def monitor():
            last_detection_attempt = 0
            detection_cooldown = 30  # Solo re-detectar cada 30 segundos si se pierde
            
            while not self._stop_monitor.is_set():
                try:
                    current = self.get_current_port()
                    
                    if current:
                        # Tenemos puerto: solo verificar que siga conectado
                        try:
                            pos = POSIntegrado()
                            if pos.open_port(current):
                                pos.poll()
                                pos.close_port()
                                with self.lock:
                                    self._is_online = True
                            else:
                                logger.warning("Puerto %s no responde, marcando offline", current)
                                transbank_log.info(f"🔌 POS Transbank desconectado — el puerto {current} dejó de responder.")
                                with self.lock:
                                    self.current_port = None
                                    self._is_online = False
                        except Exception as e:
                            logger.debug("Error verificando %s: %s", current, e)
                            transbank_log.info(f"🔌 POS Transbank desconectado — puerto {current} dejó de responder ({e}).")
                            with self.lock:
                                self.current_port = None
                                self._is_online = False
                    else:
                        # No hay puerto: intentar re-detectar (con cooldown)
                        now = time.time()
                        if now - last_detection_attempt > detection_cooldown:
                            logger.info("Re-detectando POS...")
                            self.detect_port()
                            last_detection_attempt = now

                    time.sleep(interval)
                except Exception as e:
                    logger.debug("Monitor error: %s", e)
                    time.sleep(interval)

        self.monitor_thread = threading.Thread(target=monitor, daemon=True)
        self.monitor_thread.start()
        logger.info("Monitor POS iniciado (modo inteligente)")

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
            transbank_log.info(f"🟡 Anulación solicitada — operación {operation_id} — puerto {port}")
            if not pos.open_port(port):
                transbank_log.info(f"❌ No se pudo abrir el puerto {port} para anular la operación {operation_id}.")
                return {"status": "error", "message": f"No se pudo abrir {port}"}

            logger.info("Anulación POS -> puerto=%s operation_id=%s", port, operation_id)

            res = pos.refund(operation_id)
            logger.info("Respuesta anulación POS: %s", res)

            if res.get("response_code") in ("0", "00"):
                self.last_ok = time.time()
                transbank_log.info(f"✅ Anulación APROBADA — operación {operation_id}")
                return {"status": "success", "response": res}
            else:
                transbank_log.info(
                    f"⛔ Anulación RECHAZADA — operación {operation_id} — código {res.get('response_code')}"
                )
                return {"status": "failed", "response": res}

        except Exception as e:
            logger.error("Error do_refund: %s\n%s", e, traceback.format_exc())
            transbank_log.info(f"❌ Error inesperado anulando la operación {operation_id}: {e}")
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
            transbank_log.info(f"❌ Anulación rechazada: no hay ningún POS Transbank conectado (operación {operation_id}).")
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
            transbank_log.info(f"⚠️ SIN CONFIRMACIÓN (timeout) anulando la operación {operation_id} — puerto {port}.")
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
            transbank_log.info(f"🔍 Consultando el detalle de la última transacción — puerto {port}")

            res = pos.details(print_on_pos)
            logger.info("Respuesta detalle POS: %s", res)

            if res.get("response_code") in ("0", "00"):
                self.last_ok = time.time()
                transbank_log.info(f"↪️ Detalle obtenido correctamente — puerto {port}")
                return {"status": "success", "response": res}
            else:
                transbank_log.info(f"↪️ No se pudo obtener el detalle — puerto {port} — código {res.get('response_code')}")
                return {"status": "failed", "response": res}

        except Exception as e:
            logger.error("Error do_details: %s\n%s", e, traceback.format_exc())
            transbank_log.info(f"❌ Error inesperado consultando el detalle (puerto {port}): {e}")
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
            transbank_log.info("❌ Consulta de detalle rechazada: no hay ningún POS Transbank conectado.")
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
            transbank_log.info("⚠️ Timeout consultando el detalle de la última transacción.")
            return {"status": "error", "message": "Timeout obteniendo detalle POS"}

        try:
            return result_queue.get_nowait()
        except queue.Empty:
            return {"status": "error", "message": "No se obtuvo respuesta"}