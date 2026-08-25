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
    
    def _find_getnet_port(self):
        """Detectar automáticamente el puerto del POS Getnet"""
        logger.info("[GETNET] Buscando puerto automáticamente...")
        
        if self._port_cache:
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
        """Desconectar"""
        if self.serial_connection and self.serial_connection.is_open:
            self.serial_connection.close()
            logger.info("[GETNET] Desconectado")
    
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
        """Enviar comando al POS"""
        if not self.serial_connection or not self.serial_connection.is_open:
            return False
        
        try:
            json_str = json.dumps(command_data, separators=(',', ':'))
            
            message = {
                "JsonSerialized": json_str,
                "Sign": self._sign_message(json_str)
            }
            
            message_bytes = (json.dumps(message, separators=(',', ': ')) + '\r\n').encode('utf-8')
            
            self.serial_connection.write(message_bytes)
            self.serial_connection.flush()
            
            time.sleep(0.5)
            return True
            
        except Exception as e:
            logger.error(f"[GETNET] Error enviando: {e}")
            return False
    
    def _read_response(self, timeout=60, expected_function_code=None):
        """Leer respuesta del POS.

        Si `expected_function_code` viene informado, se ignora cualquier
        respuesta cuyo FunctionCode no coincida (ej. una respuesta vieja que
        haya quedado pendiente en el buffer de otra operación) y se sigue
        esperando hasta encontrar la respuesta real o agotar el timeout. En
        pruebas con hardware real esto era necesario: apenas se abría el
        puerto llegaba una respuesta con FunctionCode 0 que no correspondía
        en absoluto al comando enviado, y sin este chequeo se tomaba como si
        fuera el resultado real de la venta.
        """
        if not self.serial_connection or not self.serial_connection.is_open:
            return None

        start = time.time()
        buffer = ""

        while time.time() - start < timeout:
            try:
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
                # El comando NO llegó a salir por el puerto serie: es seguro
                # asumir que el POS nunca lo recibió.
                getnet_log.info(
                    f"❌ No se pudo enviar la venta al POS (ticket {ticket}): falla al escribir en el puerto serie. "
                    "El POS nunca recibió el pedido, es seguro reintentar."
                )
                return {"status": "error", "message": "Error enviando comando", "ticket": ticket}

            logger.info("[GETNET] Esperando usuario en POS...")
            getnet_log.info(f"⏳ Venta enviada al POS (ticket {ticket}), esperando que el cliente pague...")
            response = self._read_response(timeout=timeout, expected_function_code=100)

            if not response:
                # El comando SÍ se envió al POS, pero nunca llegó la
                # confirmación de vuelta (ej. corte de cable). El cliente
                # puede haber pagado igual: no es seguro tratar esto como un
                # simple error ni permitir un reintento automático.
                logger.error(
                    "[GETNET] Sin respuesta tras enviar venta (ticket=%s, monto=%s) -> "
                    "queda INDETERMINADA, requiere reconciliación", ticket, amount
                )
                getnet_log.info(
                    f"⚠️ SIN CONFIRMACIÓN — ticket {ticket}, monto {fmt_monto(amount)}. "
                    "Se le mandó la venta al POS pero nunca llegó la respuesta de vuelta (posible corte de cable). "
                    "El cliente PUDO HABER PAGADO en la máquina sin que quedara registrado acá. "
                    "Esta caja queda bloqueada para nuevas ventas hasta confirmar qué pasó (ver panel)."
                )
                return {
                    "status": "indeterminada",
                    "message": "Se envió la venta al POS pero no se recibió confirmación. "
                                "La venta pudo haberse realizado en el POS.",
                    "ticket": ticket,
                }

            response_code = _rget(response, 'ResponseCode')

            # Aprobada
            if response_code == 0:
                logger.info(f"[GETNET] ✓ APROBADA - Auth: {_rget(response, 'AuthorizationCode')}")
                getnet_log.info(
                    f"✅ Venta APROBADA — ticket {ticket} — monto {fmt_monto(amount)} — "
                    f"autorización {_rget(response, 'AuthorizationCode')} — tarjeta terminada en {_rget(response, 'Last4Digits')}"
                )
                return {"status": "success", "response": response, "ticket": ticket}

            # Cancelada
            elif response_code == 1006:
                logger.warning("[GETNET] Venta CANCELADA por usuario")
                getnet_log.info(f"🚫 Venta CANCELADA por el cliente en el POS — ticket {ticket} — monto {fmt_monto(amount)}")
                return {"status": "failed", "response": response, "ticket": ticket}

            # Rechazada
            else:
                logger.warning(f"[GETNET] RECHAZADA - Código: {response_code}")
                getnet_log.info(
                    f"⛔ Venta RECHAZADA — ticket {ticket} — monto {fmt_monto(amount)} — "
                    f"motivo: {_rget(response, 'ResponseMessage')} (código {response_code})"
                )
                return {"status": "failed", "response": response, "ticket": ticket}

        except Exception as e:
            logger.error(f"[GETNET] Error en venta: {e}")
            getnet_log.info(f"❌ Error inesperado durante la venta (ticket {ticket}): {e}")
            return {"status": "error", "message": str(e), "ticket": ticket}

        finally:
            self.disconnect()

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

    def reconciliar_ticket(self, ticket, amount=None, timeout=15):
        """
        Intenta resolver una venta que quedó INDETERMINADA consultando al
        POS por su último comprobante (Command 101) y comparando el Ticket
        recibido contra el que buscamos. Debe llamarse una vez que el POS
        vuelve a estar accesible (cable reconectado).

        En pruebas con hardware real, el POS A920 Pro NO devuelve el campo
        "Ticket" en la respuesta del Command 101 (el manual lo marca como
        "campo opcional" y en la práctica nunca llega) — así que no se puede
        confiar en él para correlacionar. Si `amount` viene informado y el
        POS no devolvió Ticket, se usa el monto como respaldo: como la caja
        queda bloqueada para nuevas ventas mientras haya una transacción
        indeterminada (ver `hay_pendiente_sin_resolver`), el último
        comprobante del POS en ese estado sólo puede pertenecer a la venta
        que estamos reconciliando.

        Tampoco se puede confiar en que el FunctionCode de la respuesta sea
        el del comando original (100 = Venta), como asume el manual: en
        hardware real el POS devuelve el FunctionCode del propio comando
        consultado (101), sin importar si el último comprobante es de una
        Venta, Anulación o Devolución.

        Returns:
            dict: {"status": "aprobado|rechazado|no_resuelto", "response": {...}|None, "message": str}
        """
        getnet_log.info(f"🔍 Reconciliando ticket {ticket}: consultando al POS su último comprobante (Command 101)...")
        last = self.get_last_receipt(timeout=timeout)

        if last["status"] == "error":
            getnet_log.info(f"↪️ No se pudo reconciliar el ticket {ticket}: {last.get('message')}. Sigue INDETERMINADA.")
            return {"status": "no_resuelto", "message": last.get("message")}

        if last["status"] == "not_found":
            # El POS no tiene ningún comprobante registrado: nuestra venta
            # nunca llegó a ejecutarse (el corte pasó antes de que el POS
            # alcanzara a procesarla).
            getnet_log.info(
                f"↪️ Reconciliación del ticket {ticket}: el POS no tiene ningún comprobante registrado — "
                "la venta nunca llegó a ejecutarse. Queda descartada, es seguro reintentar."
            )
            return {
                "status": "rechazado",
                "message": "El POS no registra ninguna transacción; la venta nunca se ejecutó.",
            }

        response = last["response"]
        pos_ticket = str(_rget(response, "Ticket", "")).strip()
        pos_amount = _rget(response, "Amount")

        if pos_ticket:
            match = pos_ticket == str(ticket).strip()
            match_reason = f"ticket {pos_ticket}"
        elif amount is not None and pos_amount == amount:
            # El POS no devolvió Ticket (comportamiento normal en este
            # hardware) -> correlacionamos por monto, respaldados por el
            # bloqueo de caja que garantiza que no hay otra venta en curso.
            match = True
            match_reason = f"monto ${pos_amount} (el POS no devolvió Ticket)"
        else:
            match = False
            match_reason = f"ticket vacío, monto ${pos_amount}"

        response_json = json.dumps(response, ensure_ascii=False, default=str)

        if not match:
            # El último comprobante del POS pertenece a OTRA transacción
            # (más reciente que la nuestra) -> no podemos confirmar por esta
            # vía qué pasó con la nuestra.
            getnet_log.info(
                f"↪️ Reconciliación del ticket {ticket}: el último comprobante del POS es de OTRA venta "
                f"({match_reason}). No se puede confirmar automáticamente — requiere revisión manual desde el panel. "
                f"JSON: {response_json}"
            )
            return {
                "status": "no_resuelto",
                "response": response,
                "message": f"El último comprobante del POS ({match_reason}) "
                            f"no coincide con el ticket buscado ({ticket}).",
            }

        response_code = _rget(response, "ResponseCode")

        if response_code == 0:
            getnet_log.info(
                f"✅ Reconciliación exitosa (por {match_reason}): el POS confirma que el ticket {ticket} fue APROBADO "
                f"(autorización {_rget(response, 'AuthorizationCode')}). JSON: {response_json}"
            )
            return {"status": "aprobado", "response": response, "message": "Venta confirmada como APROBADA en el POS."}

        getnet_log.info(
            f"↪️ Reconciliación exitosa (por {match_reason}): el POS confirma que el ticket {ticket} fue RECHAZADO/ANULADO "
            f"(código {response_code}). No se cobró — es seguro descartarla. JSON: {response_json}"
        )
        return {
            "status": "rechazado",
            "response": response,
            "message": f"Venta confirmada como RECHAZADA/ANULADA en el POS (código {response_code}).",
        }

    def get_current_port(self):
        """Obtener puerto actual"""
        return self.port
    
    def is_online(self):
        """Verificar si está online"""
        if not self.port:
            self.port = self._find_getnet_port()
        return self.port is not None