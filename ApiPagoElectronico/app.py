import threading
import sys
from core.logging_config import logger
from core.singleton import ensure_single_instance_interactive, cleanup_lock
from core.firewall import open_firewall_port, close_firewall_port
from pos.pos_module import POSModule
from server.api_server import APIServer
from pos.getnet_module import GetnetModule
from ui.tray_icon import TrayIcon, SYSTRAY_AVAILABLE
from config.settings import (
    APP_NAME, PUERTOS_COM, HTTP_PORT, BIND_HOST, ESCUCHA_SOLO_LOCAL, USAR_POS_FISICO, USAR_GETNET,
    _log_auth_diagnostics,
)


def _notify_windows(title: str, message: str):
    try:
        import platform
        if platform.system() != "Windows":
            return
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name=APP_NAME,
            timeout=5,
        )
    except Exception:
        return


def _wait_http_ready(port: int, timeout_seconds: float = 2.5) -> bool:
    try:
        import time
        import urllib.request
        end = time.time() + timeout_seconds
        while time.time() < end:
            try:
                urllib.request.urlopen(f"http://localhost:{port}/status", timeout=0.75).read()
                return True
            except Exception:
                time.sleep(0.2)
        return False
    except Exception:
        return False


def _detect_getnet_background(getnet_module, detected_ports):
    """Detecta el puerto Getnet en un hilo de fondo para no bloquear el inicio del servidor."""
    try:
        logger.info("[GETNET] Iniciando detección en segundo plano...")
        detected_port = getnet_module._find_getnet_port()
        if detected_port:
            getnet_module.port = detected_port
            detected_ports["getnet"] = detected_port
            logger.info(f"[GETNET] ✓ Detectado en segundo plano: {detected_port}")
        else:
            logger.warning("[GETNET] Habilitado pero no se detectó puerto en segundo plano")
    except Exception as e:
        logger.error(f"[GETNET] Error en detección de fondo: {e}")


def _run_flask_background(server, cleanup_fn):
    """Ejecuta Flask en un hilo de fondo. Llama cleanup_fn al terminar."""
    try:
        server.run()
    except Exception:
        logger.exception("Error fatal en server.run()")
    finally:
        try:
            cleanup_fn()
        except Exception:
            pass


def main():
    logger.info("Iniciando aplicación...")
    _notify_windows(APP_NAME, "Servicio iniciando…")
    _log_auth_diagnostics()  # Diagnóstico de credenciales al arranque


    if not ensure_single_instance_interactive(stop_existing_default=True, wait_seconds=5):
        logger.info("Saliendo por decision del usuario o error al tomar el lock")
        return

    # Firewall: escuchando solo en localhost no hace falta abrir nada (y se
    # borran reglas que hayan quedado de versiones anteriores).
    try:
        if ESCUCHA_SOLO_LOCAL:
            logger.info(f"API solo local ({BIND_HOST}:{HTTP_PORT}): no se abre el firewall")
            close_firewall_port()
        else:
            logger.info(f"Verificando reglas de firewall para el puerto {HTTP_PORT}...")
            open_firewall_port(HTTP_PORT)
            logger.info(f"Reglas de firewall listas para el puerto {HTTP_PORT}")
    except PermissionError:
        logger.warning("No se tienen permisos de administrador para modificar el firewall.")
    except Exception as e:
        logger.exception("Error al configurar el firewall: %s", e)

    detected_ports = {"transbank": None, "getnet": None}

    # 1. Getnet en hilo de fondo
    getnet_module = None
    if USAR_GETNET:
        try:
            getnet_module = GetnetModule()
            getnet_thread = threading.Thread(
                target=_detect_getnet_background,
                args=(getnet_module, detected_ports),
                daemon=True,
                name="GetnetDetector"
            )
            getnet_thread.start()
            logger.info("[GETNET] Detección iniciada en segundo plano")
        except Exception as e:
            logger.error(f"Error iniciando hilo Getnet: {e}")

    # 2. Detectar Transbank en hilo de fondo también
    pos_module = POSModule(PUERTOS_COM)
    if USAR_POS_FISICO:
        transbank_thread = threading.Thread(
            target=_init_transbank,
            args=(pos_module, detected_ports),
            daemon=True,
            name="TransbankDetector"
        )
        transbank_thread.start()
        logger.info("Transbank: detección iniciada en segundo plano")

    logger.info(f"Puertos iniciales asignados: {detected_ports}")

    # 3. Inicializar servidor API
    server = APIServer(pos_module, getnet_module)

    # 4. Función de limpieza compartida
    def cleanup():
        try:
            server.stop_local_worker()
            server.stop_cleanup_task()
            server.stop_reconciliation_monitor()
        except Exception:
            pass
        pos_module.stop_monitor()
        cleanup_lock()
        logger.info("Aplicación finalizada")

    # 5. Flask en hilo de fondo (libera el main thread para pystray)
    flask_thread = threading.Thread(
        target=_run_flask_background,
        args=(server, cleanup),
        daemon=True,
        name="FlaskServer"
    )
    flask_thread.start()
    logger.info(f"Servidor Flask iniciado en hilo de fondo (puerto {HTTP_PORT})")
    if _wait_http_ready(HTTP_PORT, timeout_seconds=2.5):
        _notify_windows(APP_NAME, f"Servicio iniciado (puerto {HTTP_PORT})")
    else:
        _notify_windows(APP_NAME, f"Servicio arrancando (puerto {HTTP_PORT})")

    # 6. Tray icon en el MAIN THREAD
    try:
        tray = TrayIcon(pos_module)
        if SYSTRAY_AVAILABLE:
            logger.info("Iniciando tray icon en hilo principal...")
            tray.run_blocking()
        else:
            logger.warning("pystray no disponible — esperando Flask en foreground")
            flask_thread.join()  # Fallback: esperar Flask si no hay tray
    except KeyboardInterrupt:
        logger.info("Interrupción por teclado")
    except Exception:
        logger.exception("Error en tray icon del hilo principal")
        # Fallback: mantener vivo esperando Flask
        flask_thread.join()
    finally:
        cleanup()


def _init_transbank(pos_module, detected_ports):
    """Detecta e inicia el monitor de Transbank en hilo de fondo."""
    try:
        detected_port = pos_module.detect_port()
        if detected_port:
            detected_ports["transbank"] = detected_port
            logger.info(f"✓ Transbank detectado en {detected_port}")
        pos_module.start_monitor()
    except Exception as e:
        logger.error(f"Error iniciando Transbank: {e}")


if __name__ == "__main__":
    main()
