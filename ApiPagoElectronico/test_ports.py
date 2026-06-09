import serial
import serial.tools.list_ports
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_ports():
    logger.info("=" * 60)
    logger.info("PRUEBA DE DETECCIÓN DE PUERTOS COM")
    logger.info("=" * 60)
    
    # Obtener puertos disponibles
    logger.info("1. Buscando puertos COM disponibles...")
    available_ports = serial.tools.list_ports.comports()
    
    if not available_ports:
        logger.warning("❌ NO SE ENCONTRARON PUERTOS COM DISPONIBLES")
        return
    
    logger.info(f"✓ Encontrados {len(available_ports)} puerto(s):")
    for i, port_info in enumerate(available_ports, 1):
        logger.info(f"\nPuerto {i}:")
        logger.info(f"  Dispositivo: {port_info.device}")
        logger.info(f"  Descripción: {port_info.description}")
        logger.info(f"  Hardware ID: {port_info.hwid}")
        logger.info(f"  Producto: {port_info.product}")
        logger.info(f"  Fabricante: {port_info.manufacturer}")
        logger.info(f"  Serial: {port_info.serial_number}")
        logger.info(f"  Ubicación: {port_info.location}")
        logger.info(f"  VID:PID: {port_info.vid}:{port_info.pid}" if port_info.vid and port_info.pid else "")
    
    # Prueba de conexión simple
    logger.info("\n" + "=" * 60)
    logger.info("2. PRUEBA DE CONEXIÓN A PUERTOS")
    logger.info("=" * 60)
    
    for port_info in available_ports:
        port_name = port_info.device
        logger.info(f"\nProbando conexión a {port_name}...")
        
        test_connection = None
        try:
            test_connection = serial.Serial(
                port=port_name,
                baudrate=115200,
                timeout=1,
                write_timeout=1
            )
            logger.info(f"✓ {port_name} se pudo abrir")
            
            # Cerrar
            test_connection.close()
            logger.info(f"✓ {port_name} cerrado correctamente")
            
        except Exception as e:
            logger.error(f"❌ Error al abrir {port_name}: {e}")
        finally:
            if test_connection and test_connection.is_open:
                try:
                    test_connection.close()
                except:
                    pass
    
    logger.info("\n" + "=" * 60)
    logger.info("PRUEBA COMPLETA")
    logger.info("=" * 60)

if __name__ == "__main__":
    test_ports()
