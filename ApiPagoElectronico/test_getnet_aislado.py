"""
Test aislado: usa el GetnetModule real del proyecto, pero sin Flask, sin
threads de fondo, sin worker, sin monitor de reconciliacion. Exactamente
igual de simple que el script de referencia que si funciono.

Ademas imprime en bytes crudos exactamente lo que se manda por el puerto
serie, para poder comparar caracter a caracter contra el script que si
funciono.

Correr desde la carpeta ApiPagoElectronico con:
    python test_getnet_aislado.py
"""
import time
from pos import getnet_module
from pos.getnet_module import GetnetModule

print("=== Test aislado GetnetModule ===")

# Monkeypatch temporal solo para este test: imprime los bytes exactos antes
# de escribirlos al puerto, sin tocar el codigo real de getnet_module.py.
_original_send = GetnetModule._send_command

def _send_command_debug(self, command_data):
    import json, hashlib
    json_str = json.dumps(command_data, separators=(',', ':'))
    message = {"JsonSerialized": json_str, "Sign": hashlib.sha256(json_str.encode()).hexdigest().upper()}
    message_bytes = (json.dumps(message, separators=(',', ': ')) + '\r\n').encode('utf-8')
    print(f"\n>>> BYTES A ENVIAR ({len(message_bytes)} bytes):")
    print(repr(message_bytes))
    print()
    return _original_send(self, command_data)

GetnetModule._send_command = _send_command_debug

g = GetnetModule()
ticket = str(int(time.time()))  # segundos desde epoch (10 digitos), igual que el script que si funciono
monto = 5000
print(f"Ticket: {ticket}")
print(f"Monto: ${monto}")

resultado = g.do_sale_with_timeout(monto, timeout=90, ticket=ticket)

print("=== Resultado ===")
print(resultado)
