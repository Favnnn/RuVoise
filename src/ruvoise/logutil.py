# -*- coding: utf-8 -*-
"""Простой файловый журнал (logs/ruvoise.log)."""

import os
import time

from .app_config import LOGS_DIR

LOG_PATH = os.path.join(LOGS_DIR, 'ruvoise.log')
_MAX_SIZE = 512 * 1024


def log(msg):
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > _MAX_SIZE:
            os.remove(LOG_PATH)
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S  ') +
                    str(msg).rstrip() + '\n')
    except OSError:
        pass
