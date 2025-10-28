import subprocess
import logging
from config.settings import HTTP_PORT, APP_NAME

logger = logging.getLogger(APP_NAME)

def open_firewall_port(port=HTTP_PORT):
    """
    Abre el puerto TCP especificado en el firewall de Windows.
    Limpia reglas previas para evitar duplicados.
    """
    try:
        rule_name = f"{APP_NAME}_Port_{port}"

        def run_netsh(cmd):
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            logger.debug("Netsh cmd: %s | Return: %s | Stdout: %.200s | Stderr: %.200s",
                         cmd, res.returncode, res.stdout, res.stderr)
            return res

        # BORRA reglas previas (Inbound/Outbound)
        delete_in_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}_Inbound"'
        run_netsh(delete_in_cmd)
        delete_out_cmd = f'netsh advfirewall firewall delete rule name="{rule_name}_Outbound"'
        run_netsh(delete_out_cmd)

        # CREA regla Inbound solo para el puerto especificado
        add_in_cmd = (
            f'netsh advfirewall firewall add rule '
            f'name="{rule_name}_Inbound" dir=in action=allow '
            f'protocol=TCP localport={port} profile=any'
        )
        res_in = run_netsh(add_in_cmd)
        if res_in.returncode == 0:
            logger.info("Regla inbound creada para puerto %s", port)
        else:
            logger.warning("Error creando inbound para %s: %s", port, res_in.stderr.strip()[:200])

        # CREA regla Outbound solo para el puerto especificado
        add_out_cmd = (
            f'netsh advfirewall firewall add rule '
            f'name="{rule_name}_Outbound" dir=out action=allow '
            f'protocol=TCP localport={port} profile=any'
        )
        res_out = run_netsh(add_out_cmd)
        if res_out.returncode == 0:
            logger.info("Regla outbound creada para puerto %s", port)
        else:
            logger.warning("Error creando outbound para %s: %s", port, res_out.stderr.strip()[:200])

    except Exception as e:
        logger.exception("Error en open_firewall_port: %s", e)
