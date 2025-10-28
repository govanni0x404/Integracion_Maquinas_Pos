import subprocess
import logging
from config.settings import HTTP_PORT, APP_NAME

logger = logging.getLogger(APP_NAME)

def run_netsh(cmd):
    # Asegura ejecución en cmd
    full_cmd = ["cmd", "/c"] + cmd
    res = subprocess.run(full_cmd, capture_output=True, text=True)
    logger.debug("Netsh cmd: %s | Return: %s | Stdout: %.200s | Stderr: %.200s",
                 " ".join(full_cmd), res.returncode, res.stdout, res.stderr)
    return res

def open_firewall_port(port=HTTP_PORT):
    try:
        rule_name_in = f"{APP_NAME}_Port_{port}_Inbound"
        rule_name_out = f"{APP_NAME}_Port_{port}_Outbound"

        # Borrar reglas previas si existen
        run_netsh(["netsh", "advfirewall", "firewall", "delete", "rule", f'name={rule_name_in}'])
        run_netsh(["netsh", "advfirewall", "firewall", "delete", "rule", f'name={rule_name_out}'])

        # Crear inbound
        cmd_in = [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name_in}",
            "dir=in",
            "action=allow",
            "protocol=TCP",
            f"localport={port}",
            "profile=any"
        ]
        res_in = run_netsh(cmd_in)
        if res_in.returncode == 0:
            logger.info("Regla inbound creada para puerto %s", port)
        else:
            logger.warning("Error creando inbound para %s: %s", port, res_in.stderr.strip()[:200])

        # Crear outbound
        cmd_out = [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name_out}",
            "dir=out",
            "action=allow",
            "protocol=TCP",
            f"localport={port}",
            "profile=any"
        ]
        res_out = run_netsh(cmd_out)
        if res_out.returncode == 0:
            logger.info("Regla outbound creada para puerto %s", port)
        else:
            logger.warning("Error creando outbound para %s: %s", port, res_out.stderr.strip()[:200])

    except Exception as e:
        logger.error("Error en open_firewall_port: %s", e)
