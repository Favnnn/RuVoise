# -*- coding: utf-8 -*-
"""Точка входа RuVoise: запускает пакет ruvoise из папки src.

Структура проекта:
    run.py            — этот файл (запуск)
    src/ruvoise/      — код программы
    data/             — config.json и кэш синтеза
    logs/             — logs/ruvoise.log
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'src'))

from ruvoise.main import main  # noqa: E402

if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            import tkinter.messagebox as mb
            mb.showerror('RuVoise',
                         'Ошибка запуска:\n' + traceback.format_exc())
        except Exception:
            pass
