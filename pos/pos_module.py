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
from core.puertos_com import SONDEO_LOCK

logger = logging.getLogger(APP_NAME)

try:
    from transbank.POS.POSIntegrado import POSIntegrado
    from transbank.responses.sales_detail_response import SalesDetailResponse
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

# Límites del comando de venta del SDK (validados antes de enviar: si el SDK
# los rechaza después, no se sabría si la venta salió o no).
MONTO_MINIMO_SDK = 50
MONTO_MAXIMO_SDK = 999_999_999
# Reconciliación con "Última venta" (0250): si la última venta del POS es otra,
# la nuestra puede seguir en curso en la máquina; solo pasado este tiempo desde
# el envío se asume que el POS no la completó (no se cobró).
RECON_VENTANA_SEGUNDOS = 300
# Cuánto espera una venta nueva a que el puerto se libere (monitor o consulta en curso).
ESPERA_PUERTO_SEGUNDOS = 15


def _es_nack(exc):
    """True si el POS rechazó el mensaje (NACK): no lo procesó, no hubo venta."""
    while exc is not None:
        if "NACK" in str(exc):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _codigo_ok(codigo):
    return str(codigo or "").strip() in ("0", "00")

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
        self._is_online = False
        # Un solo uso del puerto COM a la vez: venta, consulta de última venta y
        # el monitor. En Windows el puerto es exclusivo; abrirlo en paralelo
        # hacía que el monitor marcara el POS como desconectado en plena venta.
        self._port_lock = threading.Lock()
        self._ultimo_puerto = None
        # Puertos de otras integraciones (Getnet) que nunca se deben sondear
        # con tramas de Transbank. Lo configura APIServer.
        self.puertos_ajenos = lambda: []

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

        exclude: puertos que no se deben sondear, además del que ya usa Getnet
        (puertos_ajenos), para no mandarle tramas de Transbank a otra máquina.
        """
        if not USAR_POS_FISICO:
            logger.debug("POS físico deshabilitado por configuración")
            return None

        # De a una detección a la vez (Transbank o Getnet): los ajenos se leen
        # ya con el lock tomado, así se excluye lo que Getnet acaba de detectar.
        with SONDEO_LOCK:
            return self._detectar(exclude)

    def _ajenos(self):
        try:
            return [p for p in (self.puertos_ajenos() or []) if p]
        except Exception:
            return []

    def _detectar(self, exclude=None):
        ports = self.list_ports()
        logger.info("Puertos detectados: %s | Preferidos: %s", ports, ",".join(self.prefer_ports))

        ordered = [p for p in self.prefer_ports if p in ports] + [p for p in ports if p not in self.prefer_ports]
        excluded = {p for p in [*(exclude or []), *self._ajenos()] if p}
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
                        self._ultimo_puerto = p

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

    def get_current_port(self):
        with self.lock:
            return self.current_port

    def open_port_and_sale(self, port, amount, ticket=None):
        if not TRANSBANK_AVAILABLE:
            transbank_log.info(f"❌ No se pudo vender: el SDK de Transbank no está disponible en esta instalación.")
            return {"status": "error", "message": "Transbank SDK no disponible"}

        ticket = ticket or time.strftime("%H%M%S")
        if not (MONTO_MINIMO_SDK <= int(amount) <= MONTO_MAXIMO_SDK):
            transbank_log.info(
                f"❌ Venta rechazada: el POS Transbank acepta montos entre {fmt_monto(MONTO_MINIMO_SDK)} y "
                f"{fmt_monto(MONTO_MAXIMO_SDK)} (monto {fmt_monto(amount)}). No se envió nada al POS."
            )
            return {"status": "error", "ticket": ticket,
                    "message": f"El POS Transbank acepta montos entre {MONTO_MINIMO_SDK} y {MONTO_MAXIMO_SDK}"}
        if not self._port_lock.acquire(timeout=ESPERA_PUERTO_SEGUNDOS):
            transbank_log.info(f"❌ El puerto {port} sigue ocupado (venta o consulta anterior). No se envió nada al POS.")
            return {"status": "error", "ticket": ticket, "message": "El puerto del POS está ocupado; reintente"}

        pos = None
        enviada = False  # True desde que la venta PUDO haber llegado al POS
        try:
            pos = POSIntegrado()
            transbank_log.info(f"🟢 Venta iniciada — monto {fmt_monto(amount)} — puerto {port}")
            if not pos.open_port(port):
                transbank_log.info(f"❌ No se pudo abrir el puerto {port}. No se llegó a enviar nada al POS, es seguro reintentar.")
                return {"status": "error", "message": f"No se pudo abrir {port}", "ticket": ticket}
            with self.lock:
                self._ultimo_puerto = port

            logger.info("Venta POS -> puerto=%s monto=%s ticket=%s", port, amount, ticket)
            transbank_log.info(f"⏳ Venta enviada al POS (ticket {ticket}), esperando que el cliente pague...")

            enviada = True
            res = pos.sale(amount, ticket)
            logger.info("Respuesta POS: %s", res)

            if res.get("response_code") in ("0", "00"):
                transbank_log.info(
                    f"✅ Venta APROBADA — ticket {ticket} — monto {fmt_monto(amount)} — "
                    f"autorización {res.get('authorization_code')}"
                )
                return {"status": "success", "response": res, "ticket": ticket}
            else:
                transbank_log.info(
                    f"⛔ Venta RECHAZADA — ticket {ticket} — monto {fmt_monto(amount)} — "
                    f"código {res.get('response_code')} — {res.get('response_message')}"
                )
                return {"status": "failed", "response": res, "ticket": ticket}

        except Exception as e:
            logger.error("Error do_sale: %s\n%s", e, traceback.format_exc())
            if enviada and not _es_nack(e):
                # Se cortó la comunicación (ej. cable desconectado) con la venta ya
                # enviada: el cliente pudo haber pagado en el POS. No es un error.
                transbank_log.info(
                    f"⚠️ SIN CONFIRMACIÓN — se cortó la comunicación con el POS durante la venta (ticket {ticket}, "
                    f"monto {fmt_monto(amount)}, puerto {port}): {e}. El cliente pudo haber pagado: queda "
                    "INDETERMINADA y se consulta la última venta del POS cuando vuelva a conectarse."
                )
                return {"status": "indeterminada", "ticket": ticket,
                        "message": f"Se cortó la comunicación con el POS durante la venta: {e}"}
            transbank_log.info(f"❌ Error durante la venta (puerto {port}, monto {fmt_monto(amount)}): {e}. "
                               "El POS no procesó la venta, es seguro reintentar.")
            return {"status": "error", "message": str(e), "ticket": ticket}
        finally:
            try:
                if pos:
                    pos.close_port()
            except Exception:
                pass
            self._port_lock.release()
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
                    
                    if current and not self._port_lock.acquire(blocking=False):
                        # Venta o consulta en curso: el puerto está en uso, no se toca.
                        time.sleep(interval)
                        continue
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
                        finally:
                            self._port_lock.release()
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

    def puerto_presente(self):
        """True/False si el último puerto conocido del POS está (o no) en el
        sistema, sin abrirlo. None si nunca se conoció un puerto."""
        with self.lock:
            puerto = self._ultimo_puerto or self.current_port
        if not puerto:
            return None
        if puerto in self._ajenos():
            # Al reconectar, ese COM quedó para Getnet: Transbank no está ahí.
            return False
        return puerto in self.list_ports()

    def consultar_ultima_venta(self, ticket, amount=None, sent_at=None):
        """
        Resultado real de una venta que quedó sin confirmar, preguntando al POS su
        "Última venta" (comando 0250). A diferencia de Getnet, Transbank devuelve
        el ticket: la venta es nuestra si coinciden ticket y monto.
        - Si la última venta es otra, la nuestra podría seguir en curso en la
          máquina: solo pasado RECON_VENTANA_SEGUNDOS se asume que no se cobró.
        Returns: {"status": "aprobado"|"rechazado"|"no_resuelto", "response", "message"}
        """
        if not TRANSBANK_AVAILABLE:
            return {"status": "no_resuelto", "message": "Transbank SDK no disponible"}
        if not self._port_lock.acquire(blocking=False):
            # La venta original sigue con el puerto tomado: su resultado llega solo.
            return {"status": "no_resuelto", "message": "El POS sigue procesando la venta (puerto en uso)"}
        pos = None
        try:
            ultimo = self._ultimo_puerto if self._ultimo_puerto not in self._ajenos() else None
            port = self.get_current_port() or ultimo or self.detect_port()
            if not port:
                return {"status": "no_resuelto", "message": "No se detectó el POS Transbank"}
            transbank_log.info(f"🔍 Reconciliando ticket {ticket}: consultando al POS su última venta (comando 0250)...")
            pos = POSIntegrado()
            if not pos.open_port(port):
                return {"status": "no_resuelto", "message": f"No se pudo abrir {port}"}
            res = pos.last_sale()
            logger.info("Última venta POS Transbank: %s", res)
        except Exception as e:
            logger.warning("No se pudo consultar la última venta Transbank: %s", e)
            transbank_log.info(f"↪️ No se pudo consultar la última venta del POS ({e}). Sigue INDETERMINADA.")
            return {"status": "no_resuelto", "message": f"No se pudo consultar la última venta del POS: {e}"}
        finally:
            try:
                if pos:
                    pos.close_port()
            except Exception:
                pass
            self._port_lock.release()

        ventana_cumplida = sent_at is not None and time.time() - sent_at >= RECON_VENTANA_SEGUNDOS
        pos_ticket = str(res.get("ticket") or "").strip()
        es_nuestra = pos_ticket == str(ticket).strip()
        if es_nuestra and amount is not None and str(res.get("amount") or "").strip():
            try:
                es_nuestra = int(res.get("amount")) == int(amount)
            except (TypeError, ValueError):
                es_nuestra = False
        if es_nuestra and sent_at is not None and str(res.get("real_date") or "").strip():
            # El ticket es HHMMSS: se descarta una venta de otro día con el mismo ticket.
            es_nuestra = str(res.get("real_date")).strip() in (
                time.strftime("%d%m%Y", time.localtime(sent_at)), time.strftime("%d%m%y", time.localtime(sent_at)))

        if not es_nuestra:
            if not ventana_cumplida:
                transbank_log.info(
                    f"↪️ Reconciliación del ticket {ticket}: la última venta del POS es otra (ticket {pos_ticket or '-'}). "
                    "La nuestra podría seguir en curso en la máquina — se vuelve a consultar más tarde."
                )
                return {"status": "no_resuelto", "response": res,
                        "message": f"La última venta del POS es otra (ticket {pos_ticket or '-'}); la venta podría seguir en curso"}
            transbank_log.info(
                f"↪️ Reconciliación del ticket {ticket}: pasaron {RECON_VENTANA_SEGUNDOS // 60} minutos y el POS no "
                f"registra esta venta (su última venta es el ticket {pos_ticket or '-'}). No se cobró — es seguro descartarla."
            )
            return {"status": "rechazado", "response": res, "message": "El POS no registró esta venta; no se cobró."}

        if _codigo_ok(res.get("response_code")):
            transbank_log.info(
                f"✅ Reconciliación exitosa: el POS confirma que el ticket {ticket} fue APROBADO "
                f"(autorización {res.get('authorization_code')}). Respuesta: {res}"
            )
            return {"status": "aprobado", "response": res, "message": "Venta confirmada como APROBADA en el POS."}
        transbank_log.info(
            f"↪️ Reconciliación exitosa: el POS informa el ticket {ticket} como RECHAZADO (código "
            f"{res.get('response_code')}). No se cobró — es seguro descartarla."
        )
        return {"status": "rechazado", "response": res,
                "message": f"Venta confirmada como RECHAZADA en el POS (código {res.get('response_code')})."}

    def stop_monitor(self):
        self._stop_monitor.set()
        try:
            if self.monitor_thread:
                self.monitor_thread.join(timeout=1)
        except Exception:
            pass
        logger.info("Monitor POS detenido")

    def open_port_and_refund(self, port, operation_id):
        if not TRANSBANK_AVAILABLE:
            return {"status": "error", "message": "Transbank SDK no disponible"}

        if not self._port_lock.acquire(timeout=ESPERA_PUERTO_SEGUNDOS):
            return {"status": "error", "message": "El puerto del POS está ocupado (venta en curso); reintente"}
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
            self._port_lock.release()
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

        if not self._port_lock.acquire(timeout=ESPERA_PUERTO_SEGUNDOS):
            return {"status": "error", "message": "El puerto del POS está ocupado (venta en curso); reintente"}
        pos = None
        try:
            pos = POSIntegrado()
            if not pos.open_port(port):
                return {"status": "error", "message": f"No se pudo abrir {port}"}

            logger.info("Obteniendo detalle POS -> puerto=%s, print_on_pos=%s", port, print_on_pos)
            transbank_log.info(f"🔍 Consultando el detalle de ventas al POS — puerto {port} — "
                               f"{'se imprime en el POS' if print_on_pos else 'sin imprimir'}")

            # No se usa pos.details(): en transbank-pos-sdk 1.0.1 la condición está
            # invertida (sin imprimir lee un solo mensaje y lo recorre byte a byte,
            # lo que siempre falla). El POS manda una venta por mensaje y cierra con
            # uno sin código de autorización: se leen todos con el mismo lector del SDK.
            comando = "0260|{}|".format("1" if print_on_pos else "0")
            mensajes = pos._send_command(comando, sales_detail=True, print_on_pos=True)
            ventas = []
            for mensaje in mensajes:
                venta = SalesDetailResponse(mensaje).get_response()
                if str(venta.get("authorization_code") or "").strip():
                    ventas.append(venta)
            logger.info("Detalle POS: %d venta(s): %s", len(ventas), ventas)
            transbank_log.info(
                f"↪️ Detalle obtenido — puerto {port} — {len(ventas)} venta(s)"
                + (" (el POS lo imprimió)" if print_on_pos else "")
            )
            return {"status": "success", "cantidad": len(ventas), "response": ventas}

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
            self._port_lock.release()
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