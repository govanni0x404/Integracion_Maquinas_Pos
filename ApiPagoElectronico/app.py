import threading
from core.logging_config import logger
from core.singleton import ensure_single_instance_interactive, cleanup_lock
from core.firewall import open_firewall_port
from pos.pos_module import POSModule
from server.api_server import APIServer
from pos.getnet_module import GetnetModule
from ui.tray_icon import TrayIcon
from config.settings import PUERTOS_COM, HTTP_PORT, USAR_POS_FISICO, USAR_GETNET 

def main():
    logger.info("Iniciando aplicación...")

    if not ensure_single_instance_interactive(stop_existing_default=None, wait_seconds=5):
       logger.info("Saliendo por decision del usuario o error al tomar el lock")        
       return

    # Firewall
    try:
        logger.info(f"Verificando reglas de firewall para el puerto {HTTP_PORT}...")
        open_firewall_port(HTTP_PORT)
        logger.info(f"Reglas de firewall listas para el puerto {HTTP_PORT}")
    except PermissionError:
        logger.warning("No se tienen permisos de administrador para modificar el firewall.")
    except Exception as e:
        logger.exception("Error al abrir puerto en firewall: %s", e)

    # Módulo POS
    pos_module = POSModule(PUERTOS_COM)
    if USAR_POS_FISICO:
        pos_module.start_monitor()

    #Módulo POS Getnet
    getnet_module = None
    if USAR_GETNET:
        try:
            getnet_module = GetnetModule()
            logger.info("Módulo Getnet inicializado")
        except Exception as e:
            logger.error(f"Error inicializando Getnet: {e}")

    # Servidor API
    #server = APIServer(pos_module)
    server = APIServer(pos_module, getnet_module)

    # Icono bandeja
    try:
        tray = TrayIcon(pos_module)
        t = threading.Thread(target=tray.run, daemon=True)
        t.start()
    except Exception:
        logger.exception("No se pudo iniciar tray icon")

    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("Interrupción por teclado")
    except Exception:
        logger.exception("Error fatal en server.run()")
    finally:
        try:
            server.stop_local_worker()
            server.stop_cleanup_task()
        except Exception:
            pass
        pos_module.stop_monitor()
        cleanup_lock()
        logger.info("Aplicación finalizada")

if __name__ == "__main__":
    main()