"""
Script de prueba rápida para verificar detección de Getnet
Este script prueba SOLO el puerto COM8 con el nuevo código
"""
import sys
sys.path.insert(0, 'c:\\Proyects\\Api_Global\\ApiPagoElectronico')

from pos.getnet_module import GetnetPOS
import logging

logging.basicConfig(level=logging.INFO)

print("=" * 60)
print("PRUEBA RÁPIDA: Detección de Getnet en COM8")
print("=" * 60)

pos = GetnetPOS(port="COM8")

print("\n1. Intentando conexión directa...")
if pos.connect():
    print("   ✓ Conexión establecida en COM8")
    
    print("\n2. Enviando comando POLL...")
    result = pos.poll()
    
    if result:
        print(f"   ✓ POLL exitoso!")
        print(f"   ResponseCode: {result.get('ResponseCode')}")
        print(f"   ResponseMessage: {result.get('ResponseMessage')}")
    else:
        print("   ✗ POLL falló (sin respuesta)")
    
    pos.disconnect()
else:
    print("   ✗ No se pudo conectar a COM8")

print("\n" + "=" * 60)
print("Presiona Enter para salir...")
input()
