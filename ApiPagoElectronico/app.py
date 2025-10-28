import threading
from core.logging_config import logger
from core.singleton import ensure_single_instance, cleanup_lock
from core.firewall import open_firewall_port
from pos.pos_module import POSModule
from server.api_server import APIServer
from ui.tray_icon import TrayIcon
from config.settings import PUERTOS_COM, HTTP_PORT, USAR_POS_FISICO

def main():
    logger.info("Iniciando aplicación...")

    # Singleton lock
    if not ensure_single_instance():
        logger.info("Ya hay otra instancia corriendo. Saliendo.")
        return

    # Firewall
    try:
        open_firewall_port(HTTP_PORT)
    except Exception:
        logger.exception("Error abriendo puerto en firewall")

    # Módulo POS
    pos_module = POSModule(PUERTOS_COM)
    pos_module.start_monitor()

    # Servidor API
    server = APIServer(pos_module)

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
