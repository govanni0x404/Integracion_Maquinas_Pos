import logging
import sys
import os
from pathlib import Path
from config.settings import APP_NAME

# Archivo de log en la misma carpeta del exe o script
LOG_FILE = os.environ.get("LOG_FILE", Path(sys.argv[0]).parent / "pos_gateway.log")

# Logger global
logger = logging.getLogger(APP_NAME)
logger.setLevel(logging.INFO)

if not logger.handlers:
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

logger.info("Logger inicializado")
