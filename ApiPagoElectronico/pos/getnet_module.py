"""
Módulo de integración con POS Getnet A920 Pro
Compatible con la arquitectura ApiPagoElectronico
"""
import serial
import serial.tools.list_ports
import json
import hashlib
import time
import re
import logging
import threading
from datetime import datetime
from config.settings import APP_NAME, GETNET_PORT
from core.business_logging import getnet_log, fmt_monto

logger = logging.getLogger(APP_NAME)

# Códigos del manual (anexo "Códigos de respuestas").
CODIGO_CANCELADO_POR_POS = 1006
# Command 116 (Cancelar Venta): la caja pide cancelar la venta en curso. El POS
# la acepta solo si el cliente todavía no ingresó el PIN ni se envió al autorizador.
COMANDO_CANCELAR_VENTA = 116
# Tras confirmar la cancelación se espera un poco más la respuesta de la venta
# (100), por si el POS igual la entrega (1006 o, si ya estaba autorizada, 0).
ESPERA_TRAS_CANCELAR_SEGUNDOS = 10
# 1003/1004/1005 = error de impresión (tapa abierta, sin papel, atasco). El
# manual no aclara si el cobro alcanzó a autorizarse antes de imprimir, así
# que no se trata como rechazo definitivo: queda INDETERMINADA y se verifica.
CODIGOS_ERROR_IMPRESION = (1003, 1004, 1005)

# Reconciliación: si el último comprobante del POS es ANTERIOR a nuestra venta,
# la venta todavía puede estar en curso en la máquina (cliente operando, o el
# cable se cortó y volvió). Solo después de esta ventana se asume que el POS
# ya la anuló por su cuenta y se descarta como no cobrada.
RECON_VENTANA_SEGUNDOS = 300
# Tolerancia de diferencia de reloj entre el PC y el POS al comparar fechas.
RECON_TOLERANCIA_RELOJ_SEGUNDOS = 120

_FORMATOS_FECHA_POS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
)


def _mismo_monto(a, b):
    try:
        return int(float(a)) == int(float(b))
    except (TypeError, ValueError):
        return str(a) == str(b)


def _es_comprobante_de_la_venta(response, ticket, amount, sent_at, known_operation_ids):
    """
    ¿Este comprobante/respuesta del POS es de la venta `ticket`?
    Devuelve (True|False|None, motivo). None = no hay datos para saberlo.
    Ver reconciliar_ticket para el porqué de cada regla.
    """
    pos_ticket = str(_rget(response, "Ticket", "") or "").strip()
    pos_amount = _rget(response, "Amount")
    operation_id = _rget(response, "OperationId")
    fecha_ts = _parse_fecha_pos(_rget(response, "AccountingDate"))
    known = {str(x) for x in known_operation_ids if x not in (None, "", 0, "0")}

    if pos_ticket:
        es_nuestro = pos_ticket == str(ticket).strip()
        motivo = f"ticket {pos_ticket}"
    elif operation_id not in (None, "", 0, "0") and str(operation_id) in known:
        es_nuestro = False
        motivo = f"el comprobante {operation_id} es de una venta anterior ya registrada"
    elif fecha_ts is not None and sent_at is not None:
        es_nuestro = fecha_ts >= sent_at - RECON_TOLERANCIA_RELOJ_SEGUNDOS
        motivo = f"fecha del comprobante {_rget(response, 'AccountingDate')}"
    elif operation_id not in (None, "", 0, "0") and known:
        es_nuestro = True
        motivo = f"comprobante {operation_id} nuevo (no pertenece a ninguna venta anterior)"
    else:
        es_nuestro = None
        motivo = "el POS no devolvió Ticket, OperationId reconocible ni fecha"

    if es_nuestro and amount is not None and pos_amount is not None and not _mismo_monto(pos_amount, amount):
        es_nuestro = None
        motivo += f", pero el monto no coincide (${pos_amount} vs ${amount})"
    return es_nuestro, motivo


def _parse_fecha_pos(valor):
    """Convierte AccountingDate del POS a timestamp (hora local), o None."""
    if not valor:
        return None
    texto = str(valor).strip().split(".")[0].rstrip("Z")
    for fmt in _FORMATOS_FECHA_POS:
        try:
            return datetime.strptime(texto, fmt).timestamp()
        except ValueError:
            continue
    return None


def _rget(response, key, default=None):
    """Lee un campo de la respuesta del POS sin importar mayúsculas/minúsculas.

    El manual de Getnet documenta los campos en PascalCase (ResponseCode,
    FunctionCode, ResponseMessage...), pero en pruebas con hardware real el
    POS respondió en camelCase (responseCode, functionCode...). Sin esto,
    response.get('ResponseCode') daba siempre None y toda venta se
    clasificaba como RECHAZADA sin importar lo que dijera realmente el POS.
    """
    if key in response:
        return response[key]
    key_lower = key.lower()
    for k, v in response.items():
        if k.lower() == key_lower:
            return v
    return default


# Campos de la respuesta de una venta (manual, "Response 1: Sale"), en el orden
# del manual. Toda respuesta que se entrega al cliente trae todos, aunque el POS
# no los haya mandado (null), para que una venta confirmada por conciliación
# tenga la misma forma que una normal.
CAMPOS_VENTA = (
    "FunctionCode", "ResponseCode", "ResponseMessage", "CommerceCode", "TerminalId", "Ticket",
    "AuthorizationCode", "Amount", "SharesNumber", "SharesAmount", "Last4Digits", "OperationId",
    "CardType", "AccountingDate", "AccountNumber", "CardBrand", "RealDate", "EmployeeId", "Tip",
    "SaleType", "PosMode", "Cashback",
)


def normalizar_comprobante(response, ticket, origen):
    """
    Deja la respuesta de una venta con la misma forma venga de donde venga:
    - origen "venta": respuesta directa del Command 100.
    - origen "conciliacion": se confirmó después (Command 101 o respuesta que el
      POS entregó al reconectar). En el A920 el 101 no trae Ticket y manda
      OperationId 0, y varios campos opcionales no vienen.
    Reglas: nombres en PascalCase del manual, campos faltantes en null, Ticket =
    el de nuestra venta si el POS no lo manda, Last4Digits siempre texto de 4
    dígitos, y OperationId 0 -> null (0 no es un número de comprobante real y no
    sirve para anular). FunctionCode se deja tal cual (100 o 101) para saber qué
    comando lo informó. Los campos extra que mande el POS se conservan.
    """
    if not isinstance(response, dict):
        return response
    out = {campo: _rget(response, campo) for campo in CAMPOS_VENTA}
    conocidos = {c.lower() for c in CAMPOS_VENTA}
    for k, v in response.items():
        if k.strip().lower() not in conocidos:
            out[k] = v
    if out["Ticket"] in (None, "") and ticket:
        out["Ticket"] = str(ticket)
    if out["Last4Digits"] not in (None, ""):
        out["Last4Digits"] = str(out["Last4Digits"]).zfill(4)
    if out["OperationId"] in (0, "0", ""):
        out["OperationId"] = None
    out["Origen"] = origen
    return out


class GetnetModule:
    """
    Módulo para manejar POS Getnet con detección automática de puerto
    """

    def __init__(self):
        # Si GETNET_PORT está seteado (.env), se usa ese puerto fijo y se
        # salta la detección automática por completo — útil para descartar
        # que la autodetección esté enganchando el puerto equivocado cuando
        # el dispositivo expone varios puertos COM (USB compuesto).
        self.port = GETNET_PORT
        if self.port:
            logger.info(f"[GETNET] Puerto fijado por configuración (GETNET_PORT): {self.port}")
        self.baudrate = 115200
        self.serial_connection = None
        self.last_error = None
        self._port_cache = GETNET_PORT
        # Todo acceso al puerto serie (venta, consulta de último comprobante) pasa
        # por acá. Antes solo lo tocaba el worker local, uno a la vez; ahora también
        # lo puede tocar el monitor de reconciliación automática en background, así
        # que sin este lock dos operaciones podrían pisarse en el mismo puerto.
        self._serial_lock = threading.Lock()
        # True si el último _send_command falló DESPUÉS de empezar a escribir
        # en el puerto: el POS pudo haber recibido el comando igual.
        self._send_uncertain = False
        # Último puerto donde respondió el POS. Se conserva aunque el cable se
        # corte, para reconocer cuándo vuelve a aparecer.
        self._ultimo_puerto = GETNET_PORT
        # Puertos de otras integraciones (Transbank) que nunca se deben sondear
        # con el POLL de Getnet. Lo configura APIServer.
        self.puertos_ajenos = lambda: []
        # Cancelación (Command 116) pedida por la caja mientras se espera la venta.
        # La envía el hilo de la venta, que es el dueño del puerto.
        self._cancelar_venta = threading.Event()
        self._venta_en_curso = False
        # Respuesta del 116 si el POS aceptó cancelar la venta en curso.
        self._cancelacion_confirmada = None
    
    def _find_getnet_port(self, exclude=None):
        """Detectar automáticamente el puerto del POS Getnet

        exclude: puertos que no se deben sondear (ej. el que ya usa Transbank).
        """
        try:
            ajenos = list(self.puertos_ajenos() or [])
        except Exception:
            ajenos = []
        excluded = {p for p in [*(exclude or []), *ajenos] if p}
        logger.info("[GETNET] Buscando puerto automáticamente...")
        
        if self._port_cache and self._port_cache not in excluded:
            logger.info(f"[GETNET] Usando puerto cacheado: {self._port_cache}")
            if self._test_port_connection(self._port_cache):
                self.port = self._port_cache
                return self._port_cache
            else:
                logger.warning(f"[GETNET] Puerto cacheado {self._port_cache} no responde, re-detectando...")
                self._port_cache = None
        
        logger.info("[GETNET] Buscando puerto automáticamente...")

        available_ports = serial.tools.list_ports.comports()
        
        if not available_ports:
            logger.warning("[GETNET] No se encontraron puertos COM disponibles")
            return None
        
        logger.info(f"[GETNET] Puertos disponibles: {[p.device for p in available_ports]}")
        
        # Candidatos con prioridad
        candidates = []
        
        for port_info in available_ports:
            port_name = port_info.device
            if port_name in excluded:
                logger.info(f"[GETNET] Omitiendo {port_name} (en uso por otra integración)")
                continue
            description = port_info.description.lower()
            
            if 'getnet' in description:
                candidates.insert(0, (port_name, 100))
            elif any(k in description for k in ['usb serial', 'ch340', 'ch341', 'ftdi']):
                candidates.append((port_name, 80))
            elif 'usb' in description:
                candidates.append((port_name, 60))
            else:
                candidates.append((port_name, 40))
        
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        for port_name, priority in candidates:
            logger.info(f"[GETNET] Probando {port_name} (prioridad: {priority})...")
            
            if self._test_port_connection(port_name):
                logger.info(f"[GETNET] ✓ Puerto encontrado: {port_name}")
                getnet_log.info(f"🔌 POS Getnet conectado — puerto {port_name}")
                self._port_cache = port_name
                self.port = port_name
                self._ultimo_puerto = port_name
                return port_name

        logger.warning("[GETNET] No se encontró puerto Getnet funcional")
        getnet_log.info("🔌 No se detectó ningún POS Getnet conectado en los puertos disponibles.")
        return None
    
    def _test_port_connection(self, port_name):
        """Probar si un puerto es el POS Getnet"""
        test_connection = None
        
        try:
            test_connection = serial.Serial(
                port=port_name,
                baudrate=self.baudrate,
                timeout=1,
                write_timeout=1,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False
            )
            
            time.sleep(0.3)
            test_connection.reset_output_buffer()
            test_connection.reset_input_buffer()
            
            # Enviar POLL
            command = {
                "Command": 106,
                "DateTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            
            json_str = json.dumps(command, separators=(',', ':'))
            sign = hashlib.sha256(json_str.encode()).hexdigest().upper()
            
            message = {
                "JsonSerialized": json_str,
                "Sign": sign
            }
            
            message_bytes = (json.dumps(message, separators=(',', ': ')) + '\r\n').encode('utf-8')
            
            test_connection.write(message_bytes)
            test_connection.flush()
            
            # Esperar respuesta
            start = time.time()
            buffer = ""
            
            while time.time() - start < 1.5:
                if test_connection.in_waiting:
                    data = test_connection.read(test_connection.in_waiting).decode('utf-8', errors='ignore')
                    buffer += data

                    # OJO: 'JsonSerialized' NO sirve como marcador acá — es
                    # una palabra que nosotros mismos escribimos en el
                    # comando de prueba, así que un puerto que simplemente
                    # hace eco de lo que se le envía (común en puertos COM
                    # de diagnóstico/AT que aparecen junto al puerto real en
                    # dispositivos USB compuestos) pasaría el test igual sin
                    # ser el POS. 'FunctionCode'/'ResponseCode' sí son
                    # exclusivos de una respuesta real, porque no aparecen
                    # en nada de lo que nosotros enviamos.
                    if any(k in buffer for k in ['FunctionCode', 'ResponseCode']):
                        return True
                
                time.sleep(0.05)
            
            return False
        
        except Exception as e:
            logger.debug(f"[GETNET] Error probando {port_name}: {e}")
            return False
        
        finally:
            if test_connection and test_connection.is_open:
                try:
                    test_connection.close()
                except:
                    pass
    
    def connect(self):
        """Conectar al POS"""
        if self.serial_connection and self.serial_connection.is_open:
            return True

        if not self.port and self._ultimo_puerto and self.puerto_presente():
            # Reconexión del mismo cable: se abre directo, sin el POLL de la
            # detección, para no descartar una respuesta que el POS tenga pendiente.
            self.port = self._ultimo_puerto

        if not self.port:
            self.port = self._find_getnet_port()
            
            if not self.port:
                self.last_error = "No se pudo detectar puerto Getnet"
                logger.error(f"[GETNET] {self.last_error}")
                return False

        try:
            logger.info(f"[GETNET] Conectando a {self.port}...")
            
            self.serial_connection = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1,
                write_timeout=5,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False
            )
            
            time.sleep(1)
            self.serial_connection.reset_output_buffer()
            # NOTA: en algún momento se agregó acá un reset_input_buffer()
            # para descartar basura vieja del buffer de entrada, pero en
            # pruebas con hardware real eso hacía que el POS dejara de
            # responder por completo al comando de Venta (probablemente
            # interrumpe algún handshake inicial que el POS espera al abrir
            # la sesión). Se sacó a propósito — el caso que intentaba cubrir
            # (una respuesta vieja tomada como si fuera la actual) ya queda
            # cubierto por la validación de FunctionCode en _read_response,
            # sin tocar el buffer físico del puerto.

            logger.info(f"[GETNET] ✓ Conectado a {self.port}")
            self._ultimo_puerto = self.port
            return True
            
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[GETNET] Error: {e}")
            # El puerto que teníamos cacheado ya no sirve (ej. el dispositivo USB
            # desapareció): lo soltamos para que la próxima llamada a connect()/
            # is_online() vuelva a probar puertos en vez de repetir para siempre
            # el mismo puerto muerto.
            self.port = None
            self._port_cache = None
            return False

    def disconnect(self):
        """Desconectar. Nunca lanza: si el cable se cortó, cerrar el puerto
        muerto puede fallar, y esa excepción (al ocurrir en un finally) pisaba
        el resultado real de la venta y la dejaba como ERROR."""
        conn, self.serial_connection = self.serial_connection, None
        if not conn:
            return
        try:
            if conn.is_open:
                conn.close()
            logger.info("[GETNET] Desconectado")
        except Exception as e:
            logger.warning(f"[GETNET] Error cerrando el puerto (probablemente desconectado): {e}")
    
    def _sign_message(self, json_string):
        """Firmar mensaje con SHA256"""
        return hashlib.sha256(json_string.encode()).hexdigest().upper()
    
    def _extract_json_response(self, buffer):
        """Extraer JSON de respuesta"""
        try:
            match = re.search(r'\{"JsonSerialized":"(.+?)","Sign":"(.+?)"\}', buffer)
            if match:
                json_serialized = match.group(1)
                json_serialized = json_serialized.replace('\\"', '"')
                json_serialized = json_serialized.replace('\\\\', '\\')
                
                try:
                    return json.loads(json_serialized)
                except:
                    pass
            
            if '}{' in buffer:
                parts = buffer.split('}{')
                for i, part in enumerate(parts):
                    if i == 0:
                        part = part + '}'
                    elif i == len(parts) - 1:
                        part = '{' + part
                    else:
                        part = '{' + part + '}'
                    
                    if 'JsonSerialized' in part:
                        try:
                            outer = json.loads(part)
                            if 'JsonSerialized' in outer:
                                inner = json.loads(outer['JsonSerialized'])
                                return inner
                        except:
                            continue
        
        except Exception as e:
            logger.error(f"[GETNET] Error extrayendo JSON: {e}")
        
        return None
    
    def _send_command(self, command_data):
        """Enviar comando al POS.

        Devuelve False si falló. En ese caso self._send_uncertain indica si la
        falla fue antes de tocar el cable (el POS seguro no recibió nada) o
        durante/después de la escritura (el POS PUDO haberlo recibido, p. ej.
        el cable se desconectó mientras se enviaba).
        """
        self._send_uncertain = False
        if not self.serial_connection or not self.serial_connection.is_open:
            return False

        writing = False
        try:
            json_str = json.dumps(command_data, separators=(',', ':'))
            
            message = {
                "JsonSerialized": json_str,
                "Sign": self._sign_message(json_str)
            }
            
            message_bytes = (json.dumps(message, separators=(',', ': ')) + '\r\n').encode('utf-8')

            writing = True
            self.serial_connection.write(message_bytes)
            self.serial_connection.flush()
            
            time.sleep(0.5)
            return True
            
        except Exception as e:
            self._send_uncertain = writing
            logger.error(f"[GETNET] Error enviando (envío {'incierto' if writing else 'no realizado'}): {e}")
            return False
    
    def _read_response(self, timeout=60, expected_function_code=None, cancelable=False):
        """Leer respuesta del POS.

        Si `expected_function_code` viene informado, se ignora cualquier
        respuesta cuyo FunctionCode no coincida (ej. una respuesta vieja que
        haya quedado pendiente en el buffer de otra operación) y se sigue
        esperando hasta encontrar la respuesta real o agotar el timeout. En
        pruebas con hardware real esto era necesario: apenas se abría el
        puerto llegaba una respuesta con FunctionCode 0 que no correspondía
        en absoluto al comando enviado, y sin este chequeo se tomaba como si
        fuera el resultado real de la venta.

        cancelable: durante una venta, si la caja pidió cancelarla
        (solicitar_cancelacion) se envía el Command 116 por este mismo puerto y
        se procesa su respuesta; si el POS la acepta queda en
        self._cancelacion_confirmada.
        """
        if not self.serial_connection or not self.serial_connection.is_open:
            return None

        deadline = time.time() + timeout
        buffer = ""
        cancel_enviado = False

        while time.time() < deadline:
            try:
                if cancelable and not cancel_enviado and self._cancelar_venta.is_set():
                    cancel_enviado = True
                    logger.info("[GETNET] Enviando Command 116 (Cancelar Venta)")
                    getnet_log.info("✋ La caja pidió cancelar la venta en curso: se envía Cancelar Venta (Command 116) al POS...")
                    if not self._send_command({"Command": COMANDO_CANCELAR_VENTA,
                                               "DateTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}):
                        getnet_log.info("↪️ No se pudo enviar Cancelar Venta al POS; se sigue esperando el resultado de la venta.")

                if self.serial_connection.in_waiting:
                    data = self.serial_connection.read(self.serial_connection.in_waiting).decode('utf-8', errors='ignore')
                    buffer += data

                # Sigue extrayendo mensajes del buffer ya recibido (sin
                # esperar bytes nuevos) hasta encontrar uno que nos sirva o
                # quedarnos sin mensajes completos que parsear.
                while buffer:
                    response = self._extract_json_response(buffer)
                    if not response:
                        break

                    match = re.search(r'\{"JsonSerialized":".+?","Sign":".+?"\}', buffer)
                    buffer = buffer[match.end():] if match else ""

                    response_function_code = _rget(response, "FunctionCode")
                    if cancel_enviado and response_function_code == COMANDO_CANCELAR_VENTA:
                        if _rget(response, "ResponseCode") == 0:
                            self._cancelacion_confirmada = response
                            deadline = time.time() + ESPERA_TRAS_CANCELAR_SEGUNDOS
                            getnet_log.info("🚫 El POS aceptó cancelar la venta (Command 116).")
                        else:
                            getnet_log.info(
                                f"↪️ El POS NO aceptó cancelar la venta ({_rget(response, 'ResponseMessage')}, código "
                                f"{_rget(response, 'ResponseCode')}): el cliente probablemente ya ingresó el PIN. "
                                "Se sigue esperando el resultado."
                            )
                        continue
                    if expected_function_code is not None and response_function_code != expected_function_code:
                        logger.warning(
                            f"[GETNET] Respuesta con FunctionCode inesperado ({response_function_code}, "
                            f"se esperaba {expected_function_code}) — se descarta y se sigue esperando: {response}"
                        )
                        continue

                    return response

                if buffer.strip() in ('D', 'DD'):
                    time.sleep(0.2)
                    continue

                time.sleep(0.1)

            except Exception as e:
                logger.error(f"[GETNET] Error leyendo: {e}")
                break

        return None
    
    def do_sale_with_timeout(self, amount, timeout=120, ticket=None):
        """
        Realizar venta con timeout (compatible con arquitectura existente)

        El ticket se genera fuera de este método (a partir del tx_id de la
        transacción) cuando es posible, para poder correlacionarlo después
        con `reconciliar_ticket` si la confirmación no llega. El ticket
        siempre se devuelve en la respuesta, incluso en error/indeterminada.

        Returns:
            dict: {"status": "success|failed|error|indeterminada", "response": {...}, "ticket": str}
        """
        with self._serial_lock:
            return self._do_sale_with_timeout_locked(amount, timeout=timeout, ticket=ticket)

    def _do_sale_with_timeout_locked(self, amount, timeout=120, ticket=None):
        logger.info(f"[GETNET] Iniciando venta: ${amount}")

        ticket = ticket or time.strftime("%H%M%S")
        getnet_log.info(f"🟢 Venta iniciada — monto {fmt_monto(amount)} — ticket {ticket}")

        if not self.connect():
            getnet_log.info(
                f"❌ No se pudo conectar con el POS Getnet (ticket {ticket}): "
                f"{self.last_error or 'puerto no detectado'}. No se llegó a enviar nada, es seguro reintentar."
            )
            return {
                "status": "error",
                "message": self.last_error or "No se pudo conectar al POS",
                "ticket": ticket,
            }

        command_sent = False  # True en cuanto el POS PUDO haber recibido la venta
        self._cancelar_venta.clear()
        self._cancelacion_confirmada = None
        try:
            command = {
                "Command": 100,
                "Amount": int(amount),
                "TicketNumber": ticket,
                "PrintOnPos": True,
                "SaleType": 1,
                "SendMessage": False,
                "EmployeeId": 1,
                "DateTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            if not self._send_command(command):
                if self._send_uncertain:
                    # Falló a mitad de la escritura (ej. se desconectó el cable
                    # mientras se enviaba): el POS pudo haber recibido la venta.
                    return self._resultado_indeterminado(
                        ticket, amount, "se cortó la comunicación mientras se enviaba la venta al POS"
                    )
                # El comando NO llegó a salir por el puerto serie: es seguro
                # asumir que el POS nunca lo recibió.
                getnet_log.info(
                    f"❌ No se pudo enviar la venta al POS (ticket {ticket}): falla al escribir en el puerto serie. "
                    "El POS nunca recibió el pedido, es seguro reintentar."
                )
                return {"status": "error", "message": "Error enviando comando", "ticket": ticket}

            command_sent = True
            self._venta_en_curso = True
            logger.info("[GETNET] Esperando usuario en POS...")
            getnet_log.info(f"⏳ Venta enviada al POS (ticket {ticket}), esperando que el cliente pague...")
            response = self._read_response(timeout=timeout, expected_function_code=100, cancelable=True)

            if not response and self._cancelacion_confirmada is not None:
                # El POS confirmó (116 con código 0) que canceló la venta antes de
                # enviarla al autorizador: no hubo cobro.
                getnet_log.info(f"🚫 Venta CANCELADA desde la caja (Command 116) — ticket {ticket} — monto {fmt_monto(amount)}")
                return {"status": "failed", "response": self._cancelacion_confirmada, "ticket": ticket,
                        "cancelada_desde_caja": True}

            if not response:
                # El comando SÍ se envió al POS, pero nunca llegó la
                # confirmación de vuelta (ej. corte de cable). El cliente
                # puede haber pagado igual: no es seguro tratar esto como un
                # simple error ni permitir un reintento automático.
                return self._resultado_indeterminado(
                    ticket, amount, "se le mandó la venta al POS pero nunca llegó la respuesta de vuelta (posible corte de cable)"
                )

            response_code = _rget(response, 'ResponseCode')

            # Aprobada
            if response_code == 0:
                logger.info(f"[GETNET] ✓ APROBADA - Auth: {_rget(response, 'AuthorizationCode')}")
                getnet_log.info(
                    f"✅ Venta APROBADA — ticket {ticket} — monto {fmt_monto(amount)} — "
                    f"autorización {_rget(response, 'AuthorizationCode')} — tarjeta terminada en {_rget(response, 'Last4Digits')}"
                )
                return {"status": "success", "response": normalizar_comprobante(response, ticket, "venta"), "ticket": ticket}

            # Cancelada (1006 = "Cancelado por POS": el cliente canceló o la máquina anuló la venta)
            elif response_code == CODIGO_CANCELADO_POR_POS:
                logger.warning("[GETNET] Venta CANCELADA por el POS (código 1006)")
                getnet_log.info(f"🚫 Venta CANCELADA en el POS (código 1006) — ticket {ticket} — monto {fmt_monto(amount)}")
                return {"status": "failed", "response": normalizar_comprobante(response, ticket, "venta"), "ticket": ticket}

            # Error de impresión: no se sabe si el cobro alcanzó a autorizarse
            elif response_code in CODIGOS_ERROR_IMPRESION:
                result = self._resultado_indeterminado(
                    ticket, amount,
                    f"el POS informó un error de impresión ({_rget(response, 'ResponseMessage')}, código {response_code}) "
                    "y no se sabe si el cobro alcanzó a autorizarse"
                )
                result["response"] = normalizar_comprobante(response, ticket, "venta")
                return result

            # Rechazada
            else:
                logger.warning(f"[GETNET] RECHAZADA - Código: {response_code}")
                getnet_log.info(
                    f"⛔ Venta RECHAZADA — ticket {ticket} — monto {fmt_monto(amount)} — "
                    f"motivo: {_rget(response, 'ResponseMessage')} (código {response_code})"
                )
                return {"status": "failed", "response": normalizar_comprobante(response, ticket, "venta"), "ticket": ticket}

        except Exception as e:
            logger.error(f"[GETNET] Error en venta: {e}")
            if command_sent:
                # La venta ya estaba en el POS: un error nuestro (o del puerto) no
                # dice nada sobre si el cliente pagó.
                return self._resultado_indeterminado(ticket, amount, f"error inesperado después de enviar la venta: {e}")
            getnet_log.info(f"❌ Error inesperado durante la venta (ticket {ticket}): {e}")
            return {"status": "error", "message": str(e), "ticket": ticket}

        finally:
            self._venta_en_curso = False
            self.disconnect()

    def solicitar_cancelacion(self):
        """
        Pide cancelar (Command 116) la venta que está esperando respuesta del POS.
        No escribe en el puerto: lo hace el hilo de la venta, que lo tiene tomado.
        Devuelve False si no hay ninguna venta esperando respuesta.
        """
        if not self._venta_en_curso:
            return False
        self._cancelar_venta.set()
        return True

    def _resultado_indeterminado(self, ticket, amount, motivo):
        logger.error(
            "[GETNET] Venta sin confirmar (ticket=%s, monto=%s): %s -> queda INDETERMINADA, requiere reconciliación",
            ticket, amount, motivo,
        )
        getnet_log.info(
            f"⚠️ SIN CONFIRMACIÓN — ticket {ticket}, monto {fmt_monto(amount)}: {motivo}. "
            "El cliente PUDO HABER PAGADO en la máquina sin que quedara registrado acá. "
            "Esta caja queda bloqueada para nuevas ventas hasta confirmar qué pasó "
            "(se reintenta automáticamente cuando el POS vuelva a responder, o desde el panel)."
        )
        return {
            "status": "indeterminada",
            "message": "Se envió la venta al POS pero no se recibió confirmación. "
                        "La venta pudo haberse realizado en el POS.",
            "ticket": ticket,
        }

    def get_last_receipt(self, timeout=15):
        """
        Comando 101 (Último Comprobante): pregunta al POS por el resultado
        de la última transacción que ejecutó (venta, anulación o devolución).
        No recibe ningún identificador — siempre devuelve la última, por eso
        hay que comparar su "Ticket" contra el que nos interesa reconciliar.

        Returns:
            dict: {"status": "found|not_found|error", "response": {...}|None, "message": str}
        """
        with self._serial_lock:
            return self._get_last_receipt_locked(timeout=timeout)

    def _get_last_receipt_locked(self, timeout=15):
        if not self.connect():
            return {"status": "error", "message": self.last_error or "No se pudo conectar al POS"}

        try:
            command = {
                "Command": 101,
                "PrintOnPos": False,
                "DateTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            if not self._send_command(command):
                return {"status": "error", "message": "Error enviando comando de último comprobante"}

            response = self._read_response(timeout=timeout)
            if not response:
                return {"status": "error", "message": "Sin respuesta del POS al consultar último comprobante"}

            # 21 = "Transacción no encontrada" (según el manual). En hardware real
            # observamos 99 = "No hay transacciones" para el mismo caso — el manual
            # no lo documenta, pero el mensaje es inequívoco. Se aceptan ambos.
            if _rget(response, "ResponseCode") in (21, 99):
                return {"status": "not_found", "response": response}

            return {"status": "found", "response": response}

        except Exception as e:
            logger.error(f"[GETNET] Error consultando último comprobante: {e}")
            return {"status": "error", "message": str(e)}

        finally:
            self.disconnect()

    def reconciliar_ticket(self, ticket, amount=None, timeout=15, sent_at=None, known_operation_ids=()):
        """
        Intenta resolver una venta que quedó INDETERMINADA consultando al POS
        por su último comprobante (Command 101). Se llama cuando el POS vuelve
        a estar accesible (cable reconectado), y se reintenta periódicamente.

        El problema es saber si ese "último comprobante" es de NUESTRA venta o
        de una anterior:
        - El A920 Pro no devuelve el campo "Ticket" (en la práctica nunca llega).
        - Una venta cancelada/anulada por el POS no genera comprobante, así que
          el 101 devuelve la venta ANTERIOR, que puede tener el mismo monto.
          Comparar solo por monto daba por APROBADA una venta que el cliente no
          pagó (o por RECHAZADA una que todavía estaba en curso).

        Por eso el comprobante solo se toma como nuestro si hay prueba de que es
        nuevo: Ticket igual, u OperationId que no pertenece a ninguna venta ya
        conocida (known_operation_ids) y/o AccountingDate posterior al envío
        (sent_at). Si es claramente de una venta anterior, la nuestra no generó
        comprobante: se descarta como no cobrada solo cuando pasó
        RECON_VENTANA_SEGUNDOS desde el envío (antes, el cliente podría seguir
        operando la máquina). Sin prueba en ningún sentido queda para el panel.

        En hardware real el FunctionCode de la respuesta es el del comando
        consultado (101), no el de la venta original (100).

        Returns:
            dict: {"status": "aprobado|rechazado|no_resuelto", "response": {...}|None, "message": str}
        """
        getnet_log.info(f"🔍 Reconciliando ticket {ticket}: consultando al POS su último comprobante (Command 101)...")
        last = self.get_last_receipt(timeout=timeout)

        if last["status"] == "error":
            getnet_log.info(f"↪️ No se pudo reconciliar el ticket {ticket}: {last.get('message')}. Sigue INDETERMINADA.")
            return {"status": "no_resuelto", "message": last.get("message")}

        ventana_cumplida = sent_at is not None and time.time() - sent_at >= RECON_VENTANA_SEGUNDOS

        if last["status"] == "not_found":
            # El POS no tiene ningún comprobante: nuestra venta no se completó
            # (todavía). Solo es definitivo cuando ya pasó la ventana.
            if not ventana_cumplida:
                getnet_log.info(
                    f"↪️ Reconciliación del ticket {ticket}: el POS aún no registra ningún comprobante. "
                    "La venta podría seguir en curso en la máquina — se vuelve a consultar más tarde."
                )
                return {"status": "no_resuelto", "response": last.get("response"),
                        "message": "El POS no registra comprobantes todavía; la venta podría seguir en curso."}
            getnet_log.info(
                f"↪️ Reconciliación del ticket {ticket}: el POS no tiene ningún comprobante registrado y ya pasaron "
                f"{RECON_VENTANA_SEGUNDOS // 60} minutos — la venta nunca se completó. Queda descartada, es seguro reintentar."
            )
            return {
                "status": "rechazado",
                "response": last.get("response"),
                "message": "El POS no registra ninguna transacción; la venta nunca se completó.",
            }

        response = last["response"]
        response_json = json.dumps(response, ensure_ascii=False, default=str)
        es_nuestro, motivo = _es_comprobante_de_la_venta(response, ticket, amount, sent_at, known_operation_ids)

        if es_nuestro is None:
            getnet_log.info(
                f"↪️ Reconciliación del ticket {ticket}: no se puede confirmar que el último comprobante del POS "
                f"sea de esta venta ({motivo}). Requiere revisión manual desde el panel. JSON: {response_json}"
            )
            return {"status": "no_resuelto", "response": response,
                    "message": f"No se puede confirmar que el último comprobante sea de esta venta ({motivo})."}

        if es_nuestro is False:
            if not ventana_cumplida:
                getnet_log.info(
                    f"↪️ Reconciliación del ticket {ticket}: el último comprobante del POS es de OTRA venta ({motivo}). "
                    "La nuestra podría seguir en curso en la máquina — se vuelve a consultar más tarde. "
                    f"JSON: {response_json}"
                )
                return {"status": "no_resuelto", "response": response,
                        "message": f"El último comprobante del POS es de otra venta ({motivo}); la venta podría seguir en curso."}
            getnet_log.info(
                f"↪️ Reconciliación del ticket {ticket}: pasaron {RECON_VENTANA_SEGUNDOS // 60} minutos y el POS no generó "
                f"comprobante para esta venta (el último es de otra: {motivo}). No se cobró — es seguro descartarla. "
                f"JSON: {response_json}"
            )
            return {"status": "rechazado", "response": response,
                    "message": "El POS no generó comprobante para esta venta; no se cobró."}

        response_code = _rget(response, "ResponseCode")

        if response_code == 0:
            getnet_log.info(
                f"✅ Reconciliación exitosa (por {motivo}): el POS confirma que el ticket {ticket} fue APROBADO "
                f"(autorización {_rget(response, 'AuthorizationCode')}). JSON: {response_json}"
            )
            return {"status": "aprobado", "response": normalizar_comprobante(response, ticket, "conciliacion"),
                    "message": "Venta confirmada como APROBADA en el POS."}

        getnet_log.info(
            f"↪️ Reconciliación exitosa (por {motivo}): el POS confirma que el ticket {ticket} fue RECHAZADO/ANULADO "
            f"(código {response_code}). No se cobró — es seguro descartarla. JSON: {response_json}"
        )
        return {
            "status": "rechazado",
            "response": normalizar_comprobante(response, ticket, "conciliacion"),
            "message": f"Venta confirmada como RECHAZADA/ANULADA en el POS (código {response_code}).",
        }

    def puerto_presente(self):
        """True/False si el último puerto conocido del POS está (o no) en el
        sistema, sin abrirlo. None si nunca se conoció un puerto."""
        if not self._ultimo_puerto:
            return None
        try:
            return self._ultimo_puerto in {p.device for p in serial.tools.list_ports.comports()}
        except Exception:
            return None

    def resolver_venta_pendiente(self, ticket, amount=None, sent_at=None, known_operation_ids=(), escucha=5):
        """
        Intenta conocer el resultado real de una venta que quedó sin confirmar.
        1) Abre el puerto y escucha unos segundos SIN enviar nada, por si el POS
           entrega la respuesta de la venta que no pudo mandar mientras el cable
           estaba cortado.
        2) Si no llega, pregunta el último comprobante (Command 101).
        Returns lo mismo que reconciliar_ticket.
        """
        with self._serial_lock:
            if not self.connect():
                return {"status": "no_resuelto", "message": self.last_error or "No se pudo conectar al POS"}
            try:
                response = self._read_response(timeout=escucha, expected_function_code=100)
            finally:
                self.disconnect()

        if response:
            es_nuestro, motivo = _es_comprobante_de_la_venta(response, ticket, amount, sent_at, known_operation_ids)
            code = _rget(response, "ResponseCode")
            if es_nuestro is not False and code not in CODIGOS_ERROR_IMPRESION and (
                amount is None or _rget(response, "Amount") is None or _mismo_monto(_rget(response, "Amount"), amount)
            ):
                getnet_log.info(
                    f"📬 Tras reconectar, el POS entregó la respuesta pendiente del ticket {ticket}: "
                    f"código {code}. JSON: {json.dumps(response, ensure_ascii=False, default=str)}"
                )
                response = normalizar_comprobante(response, ticket, "conciliacion")
                if code == 0:
                    return {"status": "aprobado", "response": response, "message": "Respuesta de la venta recibida tras reconectar."}
                return {"status": "rechazado", "response": response,
                        "message": f"El POS informó la venta como rechazada/cancelada (código {code}) tras reconectar."}
            logger.warning("[GETNET] Respuesta pendiente descartada (%s): %s", motivo, response)

        return self.reconciliar_ticket(ticket, amount=amount, timeout=15, sent_at=sent_at,
                                       known_operation_ids=known_operation_ids)

    def get_current_port(self):
        """Obtener puerto actual"""
        return self.port
    
    def is_online(self):
        """Verificar si está online"""
        if self.port:
            return True
        # El sondeo de puertos escribe en ellos: nunca en paralelo con una venta o
        # una reconciliación que ya tiene tomado el puerto.
        if not self._serial_lock.acquire(blocking=False):
            return bool(self.puerto_presente())
        try:
            if not self.port:
                self.port = self._find_getnet_port()
            return self.port is not None
        finally:
            self._serial_lock.release()