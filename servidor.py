"""
Arranque de ApiPago en un SERVIDOR (Linux, como servicio de systemd).

Es la misma API que el .exe de la caja (mismas rutas, mismas peticiones y
respuestas), pero:
- solo atiende Mercado Pago y TUU (cobros por internet): en un servidor no hay
  máquinas por cable, así que Getnet y Transbank quedan apagados;
- no tiene ícono en la bandeja, ni notificaciones, ni toca el firewall;
- el servidor HTTP corre en el hilo principal y termina limpio con SIGTERM
  (así lo detiene systemd), y systemd lo vuelve a levantar si se cae.

Requiere MODO_SERVIDOR=true en el .env. Ver deploy/servidor/LEEME.md.
"""
import signal
import sys

from config.settings import APP_NAME, APP_VERSION, BIND_HOST, HTTP_PORT, MODO_SERVIDOR, _log_auth_diagnostics
from core.logging_config import logger, current_log_file
from core.singleton import tomar_instancia_unica, cleanup_lock
from pos.pos_module import POSModule
from server.api_server import APIServer


def main():
    if not MODO_SERVIDOR:
        logger.error("servidor.py necesita MODO_SERVIDOR=true en el .env (en la caja se usa app.py / el .exe)")
        print("ERROR: falta MODO_SERVIDOR=true en el .env", file=sys.stderr)
        return 2

    logger.info("Iniciando %s %s en MODO SERVIDOR (solo Mercado Pago y TUU)", APP_NAME.strip(), APP_VERSION)
    logger.info("Log técnico de hoy: %s", current_log_file())
    _log_auth_diagnostics()

    if not tomar_instancia_unica(0):
        logger.error("Ya hay otra instancia corriendo en esta carpeta: se sale")
        return 1

    # Sin detección de puertos ni monitor: el módulo existe solo para que la API
    # tenga la misma forma que en la caja (con USAR_POS_FISICO apagado).
    server = APIServer(POSModule(""), None)

    def detener(signum, _frame):
        logger.info("Señal %s recibida: deteniendo el servicio", signum)
        cleanup()
        sys.exit(0)

    detenido = []

    def cleanup():
        if detenido:  # la señal y el finally la llaman: solo una vez
            return
        detenido.append(True)
        try:
            server.stop_local_worker()
            server.stop_cleanup_task()
            server.stop_reconciliation_monitor()
        except Exception:
            pass
        cleanup_lock()
        logger.info("Servicio detenido")

    signal.signal(signal.SIGTERM, detener)
    signal.signal(signal.SIGINT, detener)

    logger.info("Escuchando en %s:%s", BIND_HOST, HTTP_PORT)
    try:
        server.run()
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
