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
from datetime import datetime
from config.settings import APP_NAME

logger = logging.getLogger(APP_NAME)


class GetnetModule:
    """
    Módulo para manejar POS Getnet con detección automática de puerto
    """
    
    def __init__(self):
        self.port = None
        self.baudrate = 115200
        self.serial_connection = None
        self.last_error = None
    
    def _find_getnet_port(self):
        """Detectar automáticamente el puerto del POS Getnet"""
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
                return port_name
        
        logger.warning("[GETNET] No se encontró puerto Getnet funcional")
        return None
    
    def _test_port_connection(self, port_name):
        """Probar si un puerto es el POS Getnet"""
        test_connection = None
        
        try:
            test_connection = serial.Serial(
                port=port_name,
                baudrate=self.baudrate,
                timeout=2,
                write_timeout=2,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False
            )
            
            time.sleep(0.5)
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
            
            while time.time() - start < 3:
                if test_connection.in_waiting:
                    data = test_connection.read(test_connection.in_waiting).decode('utf-8', errors='ignore')
                    buffer += data
                    
                    if any(k in buffer for k in ['JsonSerialized', 'FunctionCode', 'ResponseCode']):
                        return True
                
                time.sleep(0.1)
            
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
            
            logger.info(f"[GETNET] ✓ Conectado a {self.port}")
            return True
            
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"[GETNET] Error: {e}")
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
    
    def _read_response(self, timeout=60):
        """Leer respuesta del POS"""
        if not self.serial_connection or not self.serial_connection.is_open:
            return None
        
        start = time.time()
        buffer = ""
        
        while time.time() - start < timeout:
            try:
                if self.serial_connection.in_waiting:
                    data = self.serial_connection.read(
                        self.serial_connection.in_waiting
                    ).decode('utf-8', errors='ignore')
                    
                    buffer += data
                    
                    response = self._extract_json_response(buffer)
                    if response:
                        return response
                
                if buffer.strip() in ('D', 'DD'):
                    time.sleep(0.2)
                    continue
                
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"[GETNET] Error leyendo: {e}")
                break
        
        return None
    
    def do_sale_with_timeout(self, amount, timeout=120):
        """
        Realizar venta con timeout (compatible con arquitectura existente)
        
        Returns:
            dict: {"status": "success|failed|error", "response": {...}}
        """
        logger.info(f"[GETNET] Iniciando venta: ${amount}")
        
        if not self.connect():
            return {
                "status": "error",
                "message": self.last_error or "No se pudo conectar al POS"
            }
        
        try:
            ticket = time.strftime("%H%M%S")
            
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
                return {"status": "error", "message": "Error enviando comando"}
            
            logger.info("[GETNET] Esperando usuario en POS...")
            response = self._read_response(timeout=timeout)
            
            if not response:
                return {"status": "error", "message": "Sin respuesta del POS"}
            
            response_code = response.get('ResponseCode')
            
            # Aprobada
            if response_code == 0:
                logger.info(f"[GETNET] ✓ APROBADA - Auth: {response.get('AuthorizationCode')}")
                return {"status": "success", "response": response}
            
            # Cancelada
            elif response_code == 1006:
                logger.warning("[GETNET] Venta CANCELADA por usuario")
                return {"status": "failed", "response": response}
            
            # Rechazada
            else:
                logger.warning(f"[GETNET] RECHAZADA - Código: {response_code}")
                return {"status": "failed", "response": response}
        
        except Exception as e:
            logger.error(f"[GETNET] Error en venta: {e}")
            return {"status": "error", "message": str(e)}
        
        finally:
            self.disconnect()
    
    def get_current_port(self):
        """Obtener puerto actual"""
        return self.port
    
    def is_online(self):
        """Verificar si está online"""
        if not self.port:
            self.port = self._find_getnet_port()
        return self.port is not None