# -*- coding: utf-8 -*-
"""Иконка в системном трее (pystray)."""

import threading


def _icon_image():
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (64, 64), (24, 26, 32))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 61, 61], radius=14, fill=(32, 38, 50))
    for x, h in ((18, 14), (26, 26), (35, 26), (43, 14)):
        d.rounded_rectangle([x, 32 - h // 2, x + 5, 32 + h // 2],
                            radius=2, fill=(0, 230, 118))
    return img


class TrayIcon:
    def __init__(self, cfg, ui_q):
        self.cfg = cfg
        self.ui_q = ui_q
        self.icon = None

    def start(self):
        import pystray
        from pystray import Menu, MenuItem as Item

        def toggle(icon, item):
            self.cfg.set('enabled', not self.cfg.get('enabled'))
            self.update_title()

        def pause_text(item):
            return ('Поставить на паузу' if self.cfg.get('enabled')
                    else 'Продолжить')

        def open_settings(icon, item):
            self.ui_q.put(('show',))

        def quit_app(icon, item):
            self.ui_q.put(('quit',))

        menu = Menu(
            Item(pause_text, toggle, default=True),
            Item('Настройки', open_settings),
            Item('Выход', quit_app),
        )
        self.icon = pystray.Icon('RuVoise', _icon_image(),
                                 'RuVoise — озвучка текста под курсором', menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def update_title(self):
        if self.icon is not None:
            self.icon.title = ('RuVoise — работает' if self.cfg.get('enabled')
                               else 'RuVoise — пауза')

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
