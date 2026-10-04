# -*- coding: utf-8 -*-
"""Окно настроек RuVoise (голос — системный, выбирается автоматически)."""

import threading

import tkinter as tk
from tkinter import ttk

from . import audio_out
from . import mouse_hook
from . import win_util

_AUTO_VOICE = 'Авто — системный русский'


class ComboCapture:
    """Захват одного назначения: кнопка мыши, аккорд из нескольких кнопок
    (можно с Ctrl/Alt/Shift/Win) или клавиатурное сочетание.

    Аккорд фиксируется, когда отпущена последняя из зажатых кнопок.
    Клавиатурное сочетание — по нажатию обычной клавиши. Esc или 20 с
    бездействия — отмена. on_done(combo|None) — None означает отмену.
    """

    def __init__(self, root, on_done, timeout_ms=20000):
        from pynput import keyboard, mouse
        self.root = root
        self.on_done = on_done
        self._mods = set()
        self._buttons = []      # порядок нажатия кнопок аккорда
        self._held = set()      # зажатые сейчас кнопки мыши
        self._keys_held = set()   # зажатые сейчас обычные клавиши
        self._keys_chord = set()  # клавиши текущего аккорда
        self._done = False
        self._modmap = {
            keyboard.Key.ctrl_l: 'ctrl', keyboard.Key.ctrl_r: 'ctrl',
            keyboard.Key.ctrl: 'ctrl',
            keyboard.Key.alt_l: 'alt', keyboard.Key.alt_r: 'alt',
            keyboard.Key.alt_gr: 'alt', keyboard.Key.alt: 'alt',
            keyboard.Key.shift_l: 'shift', keyboard.Key.shift_r: 'shift',
            keyboard.Key.shift: 'shift',
            keyboard.Key.cmd_l: 'cmd', keyboard.Key.cmd_r: 'cmd',
            keyboard.Key.cmd: 'cmd',
        }
        self._btnmap = {mouse.Button.x1: 'x1', mouse.Button.x2: 'x2',
                        mouse.Button.middle: 'middle'}
        self._kb = keyboard.Listener(on_press=self._kp, on_release=self._kr)
        self._ms = mouse.Listener(on_click=self._mc)
        self._kb.start()
        self._ms.start()
        self._timer = root.after(timeout_ms, self.cancel)

    @staticmethod
    def _token(key):
        """Стабильный токен: VK для букв/цифр/F-клавиш (раскладка не важна)."""
        vk = getattr(key, 'vk', None)
        if vk is None:
            vk = getattr(getattr(key, 'value', None), 'vk', None)
        if vk is not None:
            if 0x30 <= vk <= 0x39:
                return chr(vk).lower()
            if 0x41 <= vk <= 0x5A:
                return chr(vk).lower()
            if 0x70 <= vk <= 0x87:
                return 'f%d' % (vk - 0x6F)
            if vk == 0x1B:
                return 'esc'
            if vk == 0x0D:
                return 'enter'
            if vk == 0x20:
                return 'space'
            if vk == 0x09:
                return 'tab'
        ch = getattr(key, 'char', None)
        if ch:
            return ch.lower()
        return getattr(key, 'name', '') or ''

    def _kr(self, key):
        m = self._modmap.get(key)
        if m:
            self._mods.discard(m)
            return
        tok = self._token(key)
        if not tok:
            return
        self._keys_held.discard(tok)
        if self._keys_held or not self._keys_chord:
            return
        chord = set(self._keys_chord)
        self._keys_chord.clear()
        # аккорд клавиш готов, когда отпущены все клавиши (и кнопки мыши)
        if not self._held:
            self._finish(mouse_hook.canonical(set(self._mods), keys=chord))

    def _kp(self, key):
        m = self._modmap.get(key)
        if m:
            self._mods.add(m)
            return
        tok = self._token(key)
        if not tok:
            return
        if tok == 'esc':
            self.cancel()
            return
        # обычные клавиши копим в аккорд (без зажатых кнопок мыши)
        if not self._held:
            self._keys_held.add(tok)
            self._keys_chord.add(tok)

    def _mc(self, x, y, button, pressed):
        tok = button if isinstance(button, str) else self._btnmap.get(button)
        if tok is None:
            return
        if pressed:
            self._held.add(tok)
            if tok not in self._buttons:
                self._buttons.append(tok)
            return
        # аккорд кнопок готов, когда отпущена последняя из зажатых кнопок
        self._held.discard(tok)
        if not self._held and self._buttons:
            self._keys_chord.clear()
            self._keys_held.clear()
            self._finish(mouse_hook.canonical(set(self._mods),
                                              buttons=list(self._buttons)))

    def _finish(self, combo):
        if self._done:
            return
        self._done = True
        self._cleanup()
        self.on_done(combo)

    def cancel(self):
        if self._done:
            return
        self._done = True
        self._cleanup()
        try:
            self.on_done(None)
        except Exception:
            pass

    def _cleanup(self):
        try:
            self.root.after_cancel(self._timer)
        except Exception:
            pass
        try:
            self._kb.stop()
        except Exception:
            pass
        if self._ms:
            try:
                self._ms.stop()
            except Exception:
                pass


class SettingsWindow:
    def __init__(self, root, cfg, app):
        self.root = root
        self.cfg = cfg
        self.app = app
        self._dev_map = {}
        self._vars = {}
        self._capture = None
        self.trigger_combo = 'x1'
        self._voices = []
        self._pick_result = None
        self._build()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._poll_pick()
        self.refresh_devices()
        self._load()
        self._load_voices()

    # ----------------------------- построение -----------------------------
    def _build(self):
        pad = dict(padx=8, pady=4)
        main = ttk.Frame(self.root)
        main.pack(fill='both', expand=True)

        ttk.Label(
            main,
            text='Как пользоваться:\n'
                 '• Зажмите кнопку чтения, обведите область от левого '
                 'верхнего угла к правому нижнему и отпустите — прочитается '
                 'весь текст внутри области.\n'
                 '• Короткий клик (без движения) — слово под курсором.\n'
                 '• Чтение начинается только после отпускания кнопки. Зелёная '
                 'рамка горит, пока длится чтение.\n'
                 '• Программа живёт в трее: правый клик по иконке — пауза, '
                 'настройки, выход.',
            wraplength=520, justify='left').grid(
            row=0, column=0, columnspan=2, sticky='w', **pad)

        b1 = ttk.LabelFrame(main, text=' Чтение ')
        b1.grid(row=1, column=0, columnspan=2, sticky='we', **pad)
        b1.columnconfigure(1, weight=1)

        ttk.Label(b1, text='Кнопка чтения').grid(row=0, column=0, sticky='w',
                                                 padx=10, pady=3)
        self.trigger_lbl = ttk.Label(b1, text='—', font=('', 10, 'bold'))
        self.trigger_lbl.grid(row=0, column=1, sticky='w', padx=10)
        ttk.Button(b1, text='Задать…', width=10,
                   command=self._pick).grid(row=0, column=2, padx=4)
        ttk.Button(b1, text='Убрать', width=8,
                   command=self._remove_binding).grid(row=0, column=3, padx=4)

        ttk.Label(b1,
                  text='«Задать…»: нажмите кнопку мыши — одну или несколько '
                       'сразу (например Кнопка 4 + Кнопка 5), можно с '
                       'зажатыми Ctrl, Alt, Shift или Win; либо клавиши, '
                       'в том числе несколько сразу (например 6+7 или '
                       'Shift+G+H). «Убрать» снимает назначение. '
                       'Клавиатурное назначение: нажмите сочетание — '
                       'включится режим выделения, дальше клик левой '
                       'кнопкой читает слово, обвод левой — область; '
                       'повторное сочетание выключает режим. Esc — отмена '
                       'захвата. Всё применяется сразу.',
                  wraplength=500, justify='left', foreground='#555').grid(
            row=1, column=0, columnspan=4, sticky='w', padx=10, pady=4)

        self._vars['overlay'] = tk.BooleanVar(value=True)
        ttk.Checkbutton(b1, text='Показывать зелёную рамку вокруг читаемого',
                        variable=self._vars['overlay'],
                        command=self._on_overlay_toggle).grid(
            row=2, column=0, columnspan=4, sticky='w', padx=10, pady=4)

        b2 = ttk.LabelFrame(main, text=' Голос ')
        b2.grid(row=2, column=0, columnspan=2, sticky='we', **pad)
        b2.columnconfigure(1, weight=1)
        ttk.Label(b2, text='Скорость речи, %').grid(row=0, column=0,
                                                    sticky='w', padx=10, pady=2)
        self.rate_scale = tk.Scale(b2, from_=-50, to=100, orient='horizontal',
                                   resolution=5, length=320)
        self.rate_scale.grid(row=0, column=1, sticky='we', padx=10, pady=2)
        self.rate_scale.bind('<ButtonRelease-1>', self._voice_param_changed)

        ttk.Label(b2, text='Громкость речи, %').grid(row=1, column=0,
                                                     sticky='w', padx=10, pady=2)
        self.vol_scale = tk.Scale(b2, from_=0, to=100, orient='horizontal',
                                  resolution=5, length=320)
        self.vol_scale.grid(row=1, column=1, sticky='we', padx=10, pady=2)
        self.vol_scale.bind('<ButtonRelease-1>', self._voice_param_changed)

        ttk.Label(b2, text='Голос').grid(row=2, column=0, sticky='w',
                                         padx=10, pady=3)
        self.voice_box = ttk.Combobox(
            b2, values=['Авто — системный русский'], state='readonly')
        self.voice_box.grid(row=2, column=1, sticky='we', padx=10, pady=3)
        self.voice_box.current(0)
        self.voice_box.bind('<<ComboboxSelected>>', self._on_voice_selected)
        ttk.Button(b2, text='Обновить', width=10,
                   command=self._load_voices).grid(row=2, column=2, padx=4)

        ttk.Label(
            b2,
            text='«Авто» — системный русский голос. В списке — установленные '
                 'голоса Windows (SAPI): выберите любой — применится сразу. '
                 'Скорость и громкость тоже применяются сразу.',
            wraplength=500, justify='left', foreground='#555').grid(
            row=3, column=0, columnspan=3, sticky='w', padx=10, pady=4)

        b3 = ttk.LabelFrame(main, text=' Вывод звука ')
        b3.grid(row=3, column=0, columnspan=2, sticky='we', **pad)
        b3.columnconfigure(1, weight=1)
        self.dev_box = self._combo(b3, 'Устройство вывода', 0,
                                   [audio_out.DEFAULT_LABEL])
        self.dev_box.bind('<<ComboboxSelected>>', self._on_device_selected)
        ttk.Button(b3, text='Обновить устройства', command=self.refresh_devices)\
            .grid(row=1, column=1, sticky='e', padx=10, pady=2)
        ttk.Label(
            b3,
            text='«По умолчанию» — звук идёт в активное устройство системы: '
                 'если плагин на Stream Deck переключает колонки на '
                 'наушники, озвучка тоже перейдёт в наушники. Можно '
                 'закрепить конкретное устройство.',
            wraplength=500, justify='left', foreground='#555').grid(
            row=2, column=0, columnspan=2, sticky='w', padx=10, pady=4)

        b4 = ttk.LabelFrame(main, text=' Прочее ')
        b4.grid(row=4, column=0, columnspan=2, sticky='we', **pad)
        self._vars['autostart'] = tk.BooleanVar(value=False)
        ttk.Checkbutton(b4, text='Запускать RuVoise вместе с Windows',
                        variable=self._vars['autostart'],
                        command=self._on_autostart_toggle).grid(
            row=0, column=0, sticky='w', padx=10, pady=4)

        b5 = ttk.Frame(main)
        b5.grid(row=5, column=0, columnspan=2, sticky='we', padx=8, pady=8)
        ttk.Button(b5, text='▶  Проверить голос', command=self._test)\
            .pack(side='left', padx=4)
        ttk.Button(b5, text='🔧 Диагностика', command=self._diag)\
            .pack(side='left', padx=4)
        ttk.Button(b5, text='⚡ От администратора', command=self._runas)\
            .pack(side='left', padx=4)
        ttk.Button(b5, text='Свернуть в трей', command=self._hide_to_tray)\
            .pack(side='left', padx=4)

        self.status = ttk.Label(self.root, text='', relief='sunken',
                                anchor='w', wraplength=560)
        self.status.pack(side='bottom', fill='x')

    def _combo(self, parent, label, row, values):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w',
                                           padx=10, pady=3)
        box = ttk.Combobox(parent, values=list(values), state='readonly')
        box.grid(row=row, column=1, sticky='we', padx=10, pady=3)
        if values:
            box.current(0)
        return box

    # ----------------------------- загрузка -------------------------------
    def _load(self):
        cfg = self.cfg
        trig = str(cfg.get('trigger') or '').strip()
        if not trig:   # старые конфиги: назначение могло жить в key_hotkey
            trig = str(cfg.get('key_hotkey') or '').strip()
        self.trigger_combo = trig
        self._update_trigger_labels()
        self._vars['overlay'].set(bool(cfg.get('overlay', True)))
        self._vars['autostart'].set(bool(cfg.get('autostart', False)))
        self.rate_scale.set(int(cfg.get('rate', 30)))
        self.vol_scale.set(int(cfg.get('volume', 100)))
        cur_voice = cfg.get('sapi_voice', '') or ''
        if cur_voice:
            self.voice_box.set(cur_voice)
        else:
            self.voice_box.set(_AUTO_VOICE)
        self._select_device(cfg.get('device_name', 'default'))

    def _select_device(self, name):
        for label, dev in self._dev_map.items():
            if dev == name:
                self.dev_box.set(label)
                return
        self.dev_box.set(audio_out.DEFAULT_LABEL)

    # ----------------------------- события --------------------------------
    def _voice_param_changed(self, _event=None):
        """Слайдеры применяются и сохраняются сразу при отпускании."""
        self.cfg.set('rate', int(self.rate_scale.get()))
        self.cfg.set('volume', int(self.vol_scale.get()))
        self.cfg.save()
        self.app.set_status('Скорость %d%%, громкость %d%% — применено.'
                            % (self.rate_scale.get(), self.vol_scale.get()))

    def refresh_devices(self):
        outs = audio_out.list_outputs()
        self._dev_map = {audio_out.DEFAULT_LABEL: audio_out.DEFAULT_KEY}
        labels = [audio_out.DEFAULT_LABEL]
        for d in outs:
            label = '%s  (%s)' % (d['name'], d['api'])
            while label in self._dev_map:
                label += ' *'
            self._dev_map[label] = d['name']
            labels.append(label)
        self.dev_box['values'] = labels
        self._select_device(self.cfg.get('device_name', 'default'))

    def _collect_device(self):
        return self._dev_map.get(self.dev_box.get(), audio_out.DEFAULT_KEY)

    # ----------------------------- захват назначения ------------------------
    def _pick(self):
        if self._capture is not None:
            return
        mouse_hook.set_capture(True)
        self.app.set_status('Нажмите кнопку мыши (одну или несколько сразу), '
                            'можно с Ctrl/Alt/Shift/Win, либо сочетание '
                            'клавиш. Esc — отмена.')
        try:
            self._capture = ComboCapture(self.root, self._picked)
        except Exception as e:
            # захват не стартовал — не оставляем хук подавленным
            self._capture = None
            mouse_hook.set_capture(False)
            print('[RuVoise] захват сочетания:', e)
            self.app.set_status('Не удалось запустить захват: %s' % e)

    def _update_trigger_labels(self):
        self.trigger_lbl.config(
            text=mouse_hook.combo_label(self.trigger_combo))

    # ----------------------------- голос -----------------------------------
    def _load_voices(self):
        """Список установленных голосов SAPI (в фоне — COM не любит GUI-поток)."""
        self._voices_req = getattr(self, '_voices_req', 0) + 1
        req = self._voices_req

        def work():
            voices = []
            try:
                import comtypes
                import comtypes.client
                comtypes.CoInitialize()
                v = comtypes.client.CreateObject('SAPI.SpVoice')
                col = v.GetVoices()
                for i in range(col.Count):
                    voices.append(col.Item(i).GetDescription(0))
            except Exception as e:
                print('[RuVoise] список голосов:', e)
            self._voices_result = (req, voices)   # заберёт главный поток

        threading.Thread(target=work, daemon=True).start()
        self._poll_voices(req)

    def _poll_voices(self, req):
        res = getattr(self, '_voices_result', None)
        if res is not None and res[0] == req:
            self._set_voices(res[1])
            return
        self.root.after(120, lambda: self._poll_voices(req))

    def _set_voices(self, voices):
        self._voices = voices or []
        self.voice_box['values'] = [_AUTO_VOICE] + self._voices
        cur = self.cfg.get('sapi_voice', '') or ''
        if cur in self._voices:
            self.voice_box.set(cur)
        else:
            self.voice_box.set(_AUTO_VOICE)

    def _collect_voice(self):
        sel = self.voice_box.get()
        return '' if (not sel or sel == _AUTO_VOICE) else sel

    def _on_voice_selected(self, _event=None):
        """Голос применяется сразу при выборе в списке (как слайдеры)."""
        self.cfg.set('sapi_voice', self._collect_voice())
        self.cfg.save()
        self.app.set_status('Голос: %s — применено. «Проверить голос» '
                            'озвучит им тестовую фразу.' % self.voice_box.get())

    # ------------------------- мгновенное применение ------------------------
    def _cancel_capture(self):
        """Отменить активный захват сочетания (иначе хук подавлен и кнопка
        чтения «не работает» до таймаута)."""
        cap = self._capture
        self._capture = None
        if cap is not None:
            try:
                cap.cancel()
            except Exception:
                pass
        mouse_hook.set_capture(False)

    def _apply_triggers(self):
        """Применить триггер немедленно и сохранить (один слот)."""
        self.cfg.set('trigger', self.trigger_combo)
        self.cfg.set('key_hotkey', '')   # слот теперь один
        self.app.apply_trigger(self.trigger_combo)
        self.cfg.save()

    def _picked(self, combo):
        """Вызывается из потока pynput — только потокобезопасные операции."""
        self._capture = None
        mouse_hook.set_capture(False)
        self._pick_result = ('done', combo)   # обработает главный поток

    def _poll_pick(self):
        res = self._pick_result
        if res is not None:
            self._pick_result = None
            try:
                self._finish_pick(res[1])
            except Exception as e:
                print('[RuVoise] применение сочетания:', e)
        self.root.after(80, self._poll_pick)

    def _finish_pick(self, combo):
        if combo:
            self.trigger_combo = combo
            self._apply_triggers()
            self.app.set_status('Назначено: %s — работает сразу.' %
                                mouse_hook.combo_label(combo))
        else:
            self.app.set_status('Захват отменён.')
        self._update_trigger_labels()

    def _remove_binding(self):
        """Убрать назначение целиком (станет «не задано»)."""
        self._cancel_capture()
        self.trigger_combo = ''
        self._apply_triggers()
        self._update_trigger_labels()
        self.app.set_status('Назначение снято. «Задать…» — назначить заново.')

    def _on_overlay_toggle(self):
        self.cfg.set('overlay', bool(self._vars['overlay'].get()))
        self.cfg.save()
        self.app.set_status('Зелёная рамка: %s.' %
                            ('вкл' if self._vars['overlay'].get() else 'выкл'))

    def _on_autostart_toggle(self):
        autostart = bool(self._vars['autostart'].get())
        self.cfg.set('autostart', autostart)
        self.cfg.save()
        try:
            win_util.set_autostart(autostart)
            self.app.set_status('Автозапуск с Windows: %s.' %
                                ('вкл' if autostart else 'выкл'))
        except Exception as e:
            self.app.set_status('Автозапуск не настроен: %s' % e)

    def _on_device_selected(self, _event=None):
        self.cfg.set('device_name', self._collect_device())
        self.cfg.save()
        self.app.set_status('Устройство вывода: %s — применено.' %
                            self.dev_box.get())

    def _hide_to_tray(self):
        self._cancel_capture()
        self.app.hide_window()

    def _on_close(self):
        """Крестик: отменить захват (если шёл) и свернуть в трей."""
        self._hide_to_tray()

    def _test(self):
        ov = {
            'rate': int(self.rate_scale.get()),
            'volume': int(self.vol_scale.get()),
            'voice': self._collect_voice(),
            'device': self._collect_device(),
        }
        self.app.set_status('Синтез речи…')
        self.app.speak_test(ov)

    def _diag(self):
        self.app.set_status('Диагностика выполняется — результат в logs\\ruvoise.log…')
        self.app.diagnose()

    def _runas(self):
        """Перезапуск RuVoise с правами администратора."""
        import ctypes
        import os
        import sys
        from .app_config import ROOT_DIR
        exe = sys.executable
        if exe.lower().endswith('\\python.exe'):
            exe = exe[:-len('python.exe')] + 'pythonw.exe'
        script = os.path.join(ROOT_DIR, 'run.py')
        res = ctypes.windll.shell32.ShellExecuteW(
            None, 'runas', exe, '"%s"' % script, ROOT_DIR, 1)
        if res > 32:
            self.app.set_status('Запущено от администратора — закрываю эту копию')
            self.app.put_ui(('quit',))
        else:
            self.app.set_status('Запуск от администратора отменён')

    # ----------------------------- статус ----------------------------------
    def set_status(self, text):
        self.status.config(text=text)
