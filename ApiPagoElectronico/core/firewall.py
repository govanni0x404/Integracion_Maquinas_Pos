import subprocess
import logging
from config.settings import HTTP_PORT, APP_NAME

logger = logging.getLogger(APP_NAME)

def run_netsh(args):
    res = subprocess.run(["netsh"] + args, capture_output=True, text=True)
    logger.debug("Netsh cmd: netsh %s | Return: %s | Stdout: %.200s | Stderr: %.200s",
                 " ".join(args), res.returncode, res.stdout, res.stderr)
    return res

def open_firewall_port(port=HTTP_PORT):
    rule_in = f"{APP_NAME}_Port_{port}_Inbound"
    rule_out = f"{APP_NAME}_Port_{port}_Outbound"

    # Borrar reglas viejas
    run_netsh(["advfirewall", "firewall", "delete", "rule", f"name={rule_in}"])
    run_netsh(["advfirewall", "firewall", "delete", "rule", f"name={rule_out}"])

    # Crear inbound
    res_in = run_netsh([
        "advfirewall", "firewall", "add", "rule",
        f"name={rule_in}",
        "dir=in",
        "action=allow",
        "protocol=TCP",
        f"localport={port}",
        "profile=any"
    ])
    if res_in.returncode == 0:
        logger.info("Regla inbound creada para puerto %s", port)
    else:
        logger.error("Error creando inbound: %s", res_in.stderr)

    # Crear outbound
    res_out = run_netsh([
        "advfirewall", "firewall", "add", "rule",
        f"name={rule_out}",
        "dir=out",
        "action=allow",
        "protocol=TCP",
        f"localport={port}",
        "profile=any"
    ])
    if res_out.returncode == 0:
        logger.info("Regla outbound creada para puerto %s", port)
    else:
        logger.error("Error creando outbound: %s", res_out.stderr)
