from core.logging_config import setup_logging
from core.singleton import ensure_single_instance, cleanup_lock
from core.firewall import open_firewall_port
from pos.pos_module import POSModule
from server.api_server import APIServer
from ui.tray_icon import TrayIcon
from config.settings import APP_NAME, USAR_POS_FISICO, PUERTOS_COM, HTTP_PORT

import threading
import logging
import time

logger = None

def main():
    global logger
    logger = setup_logging()

    # Singleton lock
    ok = ensure_single_instance()
    if not ok:
        print("Ya hay otra instancia corriendo. Saliendo.")
        return

    # Intenta abrir puerto en firewall
    try:
        open_firewall_port(HTTP_PORT)
    except Exception:
        pass

    # Crea el módulo POS
    pos_module = POSModule(PUERTOS_COM)

    # Inicia monitor POS
    pos_module.start_monitor()

    # Crea servidor API
    server = APIServer(pos_module)

    # Inicia icono de bandeja en hilo aparte
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
