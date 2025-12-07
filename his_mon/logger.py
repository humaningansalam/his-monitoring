import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from multiprocessing import Queue
from typing import Optional, Dict

try:
    from logging_loki import LokiQueueHandler
except ImportError:
    LokiQueueHandler = None

def setup_logging(
    level: str = "INFO", 
    loki_url: Optional[str] = None, 
    tags: Optional[Dict[str, str]] = None,
    log_file: Optional[str] = None,
    max_bytes: int = 1 * 1024 * 1024, # 10MB
    backup_count: int = 1
) -> None:
    """
    Setup unified logging (Console + File + Loki).
    
    :param level: Logging level ("DEBUG", "INFO", "ERROR")
    :param loki_url: Loki server URL (e.g., "http://loki:3100/loki/api/v1/push"). If None, Loki is disabled.
    :param tags: Tags for Loki logs (e.g., {'app': 'myapp'})
    :param log_file: Path to the log file. If None, file logging is disabled.
    """
    logger = logging.getLogger()
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Consle Handler
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    # File Handler
    if log_file:
        try:
            log_dir = os.path.dirname(log_file)
            if log_dir: os.makedirs(log_dir, exist_ok=True)
                
            file_handler = RotatingFileHandler(
                filename=log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            print(f"✅ [HisMon] File log: {log_file}")
        except Exception as e:
            print(f"⚠️ [HisMon] File log error: {e}")

    # Loki Handler
    if loki_url and LokiQueueHandler:
        try:
            loki_handler = LokiQueueHandler(
                Queue(-1), url=loki_url, tags=tags or {}, version="1"
            )
            logger.addHandler(loki_handler)
            print(f"✅ [HisMon] Loki attached: {loki_url}")
        except Exception as e:
            print(f"⚠️ [HisMon] Loki error: {e}")