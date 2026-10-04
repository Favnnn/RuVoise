# -*- coding: utf-8 -*-
"""Конфигурация RuVoise (config.json в папке data/)."""

import json
import os

APP_NAME = 'RuVoise'
# src/ruvoise/app_config.py → корень проекта на два уровня выше пакета
ROOT_DIR = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
LOGS_DIR = os.path.join(ROOT_DIR, 'logs')
CONFIG_PATH = os.path.join(DATA_DIR, 'config.json')


def _migrate_old_files():
    """Перенос файлов из старой плоской структуры (код рядом с данными)."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(LOGS_DIR, exist_ok=True)
        old_cfg = os.path.join(ROOT_DIR, 'config.json')
        if os.path.exists(old_cfg) and not os.path.exists(CONFIG_PATH):
            os.replace(old_cfg, CONFIG_PATH)
        old_log = os.path.join(ROOT_DIR, 'log.txt')
        new_log = os.path.join(LOGS_DIR, 'ruvoise.log')
        if os.path.exists(old_log):
            if os.path.exists(new_log):
                # старый журнал дописываем к новому и убираем
                with open(old_log, 'rb') as f_old:
                    tail = f_old.read()
                with open(new_log, 'ab') as f_new:
                    f_new.write(b'\n' + tail)
                os.remove(old_log)
            else:
                os.replace(old_log, new_log)
        old_cache = os.path.join(ROOT_DIR, 'cache')
        new_cache = os.path.join(DATA_DIR, 'cache')
        if os.path.isdir(old_cache) and not os.path.isdir(new_cache):
            os.replace(old_cache, new_cache)
    except OSError:
        pass


_migrate_old_files()

DEFAULTS = {
    # включено ли чтение (тумблер в трее)
    'enabled': True,
    # триггер чтения/выделения: кнопка мыши с модификаторами,
    # например 'x1', 'x2', 'middle', 'ctrl+x1', 'ctrl+shift+x2'
    'trigger': 'x1',
    # клавиатурное сочетание чтения слова под курсором ('' — не задано),
    # например 'ctrl+shift+r', 'f9'
    'key_hotkey': '',
    # (устаревшие ключи прежних версий — читаются для совместимости)
    'button': 'x1',
    'second_button_action': 'none',
    # движок речи (оба офлайн): 'sapi' (как в LunaTranslator) или 'winrt'
    'engine': 'sapi',
    'sapi_voice': '',       # пусто = автоматически выбрать русский голос
    'winrt_voice': '',      # id голоса Windows (пусто = по умолчанию)
    # скорость речи в процентах: -50..+100 (по умолчанию 30)
    'rate': 30,
    # громкость речи: 0..100
    'volume': 100,
    # имя устройства вывода или 'default' (следует за переключением в системе)
    'device_name': 'default',
    # показывать зелёную рамку вокруг читаемого текста/области
    'overlay': True,
    # автозапуск вместе с Windows
    'autostart': False,
    # максимальная длина озвучиваемого текста
    'max_read_chars': 1500,
    # размер области захвата вокруг курсора и увеличение перед распознаванием
    'ocr_grab_w': 640,
    'ocr_grab_h': 260,
    'ocr_scale': 2,
    # язык OCR: 'auto' (русский, иначе язык системы) или тег типа 'ru-RU'
    'ocr_lang': 'auto',
}


class Config:
    """Простое хранилище настроек с сохранением в JSON."""

    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self._d = dict(DEFAULTS)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._d.update(data)
            except Exception as e:  # повреждённый файл — используем умолчания
                print('[RuVoise] config.json:', e)

    def get(self, key, fallback=None):
        return self._d.get(key, DEFAULTS.get(key, fallback))

    def set(self, key, value):
        self._d[key] = value

    def file_exists(self):
        """Был ли уже файл настроек (для показа окна при первом запуске)."""
        return os.path.exists(self.path)

    def save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self._d, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print('[RuVoise] Не удалось сохранить настройки:', e)
