# /root/Live-Agent/backend/src/logging_config.py
import logging
import os
import sys

def setup_logging():
    log_folder = "/root/Live-Agent/backend/logs"
    os.makedirs(log_folder, exist_ok=True)
    log_file = os.path.join(log_folder, "agent2.log")

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
