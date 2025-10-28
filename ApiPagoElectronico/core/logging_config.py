import logging
from logging.handlers import RotatingFileHandler
import sys
from config.settings import LOG_FILE, APP_NAME

def setup_logging():
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.INFO)

    # Formatter
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    # Console handler (stdout)
    if getattr(sys, "stdout", None):
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

    # Rotating file handler
    fh = RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    logger.info("Logger inicializado")
    return logger
