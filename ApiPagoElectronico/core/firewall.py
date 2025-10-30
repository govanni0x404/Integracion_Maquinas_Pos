import logging
import traceback
import subprocess
from core.logging_config import APP_NAME

logger = logging.getLogger(APP_NAME)

def open_firewall_port(port: int):
    """
    Abre el puerto TCP especificado en el firewall de Windows
    (tanto entrada como salida), eliminando reglas previas si existen.
    """
    try:
        rule_name = f"ApiPagoElectronico"

        def run_netsh(cmd):
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            logger.debug("Netsh cmd: %s | Return: %s | Stdout: %s | Stderr: %s",
                         cmd, res.returncode, res.stdout.strip()[:200], res.stderr.strip()[:200])
            return res

        # --- INBOUND ---
        run_netsh(f'netsh advfirewall firewall delete rule name="{rule_name}_Inbound" >nul 2>&1')
        add_in = (
            f'netsh advfirewall firewall add rule '
            f'name="{rule_name}_Inbound" '
            f'dir=in action=allow '
            f'protocol=TCP localport={port} profile=any enable=yes'
        )
        res_in = run_netsh(add_in)
        if res_in.returncode == 0:
            logger.info(f"Regla inbound creada para puerto {port}")
        else:
            logger.warning(f"Error creando regla inbound: {res_in.stderr.strip()}")

        # --- OUTBOUND ---
        run_netsh(f'netsh advfirewall firewall delete rule name="{rule_name}_Outbound" >nul 2>&1')
        add_out = (
            f'netsh advfirewall firewall add rule '
            f'name="{rule_name}_Outbound" '
            f'dir=out action=allow '
            f'protocol=TCP localport={port} profile=any enable=yes'
        )
        res_out = run_netsh(add_out)
        if res_out.returncode == 0:
            logger.info(f"Regla outbound creada para puerto {port}")
        else:
            logger.warning(f"Error creando regla outbound: {res_out.stderr.strip()}")

    except Exception as e:
        logger.error("Error en open_firewall_port: %s\n%s", e, traceback.format_exc())
