import logging
import os
from datetime import datetime

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, f"gateway_{datetime.now().strftime('%Y%m%d')}.log")

os.makedirs(LOG_DIR, exist_ok=True)

def setup_logger():
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Evitar duplicar handlers si se llama varias veces
    if not logger.handlers:
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        logger.addHandler(ch)

    logger.info("Logger inicializado")
    return logger
