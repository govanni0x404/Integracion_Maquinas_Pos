import logging
import traceback
import subprocess
from core.logging_config import APP_NAME

logger = logging.getLogger(APP_NAME)

RULE_NAME = "ApiPagoElectronico"


def _run_netsh(cmd):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    logger.debug("Netsh cmd: %s | Return: %s | Stdout: %s | Stderr: %s",
                 cmd, res.returncode, res.stdout.strip()[:200], res.stderr.strip()[:200])
    return res


def close_firewall_port():
    """Elimina las reglas creadas por versiones anteriores (entrada y salida)."""
    for sufijo in ("Inbound", "Outbound"):
        _run_netsh(f'netsh advfirewall firewall delete rule name="{RULE_NAME}_{sufijo}" >nul 2>&1')


def open_firewall_port(port: int):
    """
    Abre el puerto TCP de entrada en el firewall de Windows, solo para redes
    privadas y de dominio (nunca en una red "Pública", ej. un WiFi abierto).
    La regla de salida no hace falta: Windows permite la salida por defecto.
    """
    try:
        close_firewall_port()
        add_in = (
            f'netsh advfirewall firewall add rule '
            f'name="{RULE_NAME}_Inbound" '
            f'dir=in action=allow '
            f'protocol=TCP localport={int(port)} profile=private,domain enable=yes'
        )
        res_in = _run_netsh(add_in)
        if res_in.returncode == 0:
            logger.info(f"Regla inbound creada para puerto {port} (redes privadas/dominio)")
        else:
            logger.warning(f"Error creando regla inbound: {res_in.stderr.strip()}")
    except Exception as e:
        logger.error("Error en open_firewall_port: %s\n%s", e, traceback.format_exc())
