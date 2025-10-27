import sys
import logging
from logging.handlers import RotatingFileHandler
from config import APP_NAME, LOG_FILE


def setup_logger():
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.INFO)
    
    # Evita duplicar handlers si se llama múltiples veces
    if logger.handlers:
        return logger
    
    # Handler de archivo con rotación
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(file_handler)
    
    # Handler de consola
    if getattr(sys, "stdout", None):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger.addHandler(console_handler)
    
    return logger


# Inicializa el logger al importar este módulo
logger = setup_logger()