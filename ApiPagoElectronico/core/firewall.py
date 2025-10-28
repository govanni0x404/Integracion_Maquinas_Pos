import subprocess
import logging
from config.settings import HTTP_PORT, APP_NAME

logger = logging.getLogger(APP_NAME)

def open_firewall_port(port=HTTP_PORT):
    try:
        rule_name = f"{APP_NAME}_Port_{port}"

        def run_netsh(cmd):
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            logger.debug("Netsh cmd: %s | Return: %s | Stdout: %.200s | Stderr: %.200s",
                         cmd, res.returncode, res.stdout, res.stderr)
            return res

        # Borra reglas previas con el mismo nombre
        run_netsh(f'netsh advfirewall firewall delete rule name="{rule_name}_Inbound"')
        run_netsh(f'netsh advfirewall firewall delete rule name="{rule_name}_Outbound"')

        # Añade regla inbound
        res_in = run_netsh(f'netsh advfirewall firewall add rule name="{rule_name}_Inbound" '
                           f'dir=in action=allow protocol=TCP localport={port} profile=any')
        if res_in.returncode == 0:
            logger.info(f"Regla inbound creada para puerto {port}")
        else:
            logger.warning(f"Error creando inbound para {port}: {res_in.stderr.strip()}")

        # Añade regla outbound
        res_out = run_netsh(f'netsh advfirewall firewall add rule name="{rule_name}_Outbound" '
                            f'dir=out action=allow protocol=TCP localport={port} profile=any')
        if res_out.returncode == 0:
            logger.info(f"Regla outbound creada para puerto {port}")
        else:
            logger.warning(f"Error creando outbound para {port}: {res_out.stderr.strip()}")

    except Exception as e:
        logger.error(f"Error en open_firewall_port: {e}")
