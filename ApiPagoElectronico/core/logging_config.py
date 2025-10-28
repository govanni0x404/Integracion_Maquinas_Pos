import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import os

APP_NAME = "POS Gateway"

def setup_logger():
    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.DEBUG)

    # Ruta para logs
    if getattr(sys, "frozen", False):
        # Ejecutable .exe
        base_path = Path(sys._MEIPASS)  # carpeta temporal de PyInstaller
        log_dir = Path(os.environ.get("APPDATA", ".")) / APP_NAME
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "pos_gateway.log"
    else:
        # Código fuente
        log_file = Path(__file__).parent.parent / "pos_gateway.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

    # Formato de logs
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Rotating file handler
    fh = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    logger.info("Logger inicializado")
    return logger
