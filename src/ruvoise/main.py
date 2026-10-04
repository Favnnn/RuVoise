# -*- coding: utf-8 -*-
"""RuVoise — озвучивание текста с экрана по боковой кнопке мыши.

Как работает:
- Короткий клик кнопкой 4 — читается слово под курсором (OCR).
- Зажать кнопку 4, обвести область, отпустить — читается весь текст области.
- Рамка (окно с регионом-рамкой, как рамки захвата) видна всё время,
  пока длится чтение, — в том числе поверх игры.
- Экран фиксируется в момент отпускания кнопки: даже если картинка
  поменяется, чтение продолжится по снятому снимку.
- Синтез речи офлайн: системный голос SAPI (выбирается автоматически),
  запасной — голоса Windows WinRT.
"""

import itertools
import os
import queue
import threading
import time
import traceback

SMOKE = os.environ.get('RUVOISE_SMOKE') == '1'


def main():
    from . import win_util
    win_util.enable_dpi_awareness()

    from .app_config import Config
    cfg = Config()

    import tkinter as tk
    from .overlay import Overlay
    from .settings_gui import SettingsWindow

    from . import audio_out
    from . import tts_engines as tts
    from . import ocr_reader
    from . import logutil
    from . import mouse_hook

    # закрываем окна старых копий и защищаемся от повторного запуска:
    # старая версия (без выделения области) могла остаться висеть в трее
    stale = win_util.close_old_ruvoise_windows()
    if stale:
        time.sleep(1.2)
    if not win_util.acquire_single_instance():
        try:
            import tkinter.messagebox as mb
            mb.showwarning(
                'RuVoise уже запущен',
                'Похоже, RuVoise уже работает (иконка в трее).\n'
                'Закройте старую копию: правый клик по иконке → «Выход»,\n'
                'затем запустите RuVoise снова.')
        except Exception:
            pass
        return
    logutil.log('Запуск RuVoise%s' %
                (' (закрыты старые копии: %s)' % stale if stale else ''))
    # поддерживаем путь автозапуска в актуальном состоянии (переезд и т.п.)
    try:
        win_util.set_autostart(bool(cfg.get('autostart', False)))
    except Exception:
        pass

    ui_q = queue.Queue()     # события для GUI-потока
    tasks_q = queue.Queue()  # задачи синтеза для рабочего потока

    root = tk.Tk()
    root.title('RuVoise — озвучка текста с экрана')
    root.minsize(600, 0)
    root.geometry('+80+80')

    overlay = Overlay(root)
    overlay.prewarm()

    _read_seq = itertools.count(1)
    _gen_seq = itertools.count(1)
    ov_state = {'gen': 0}   # поколение рамки: чтобы старое чтение не гасило новую

    # --------------------- синтез речи (рабочий поток) --------------------
    class SynthWorker(threading.Thread):
        def __init__(self):
            super().__init__(daemon=True, name='ruvoise-synth')
            self._latest = 0

        def run(self):
            try:
                import comtypes
                comtypes.CoInitialize()  # COM для SAPI в этом потоке
            except Exception:
                pass
            while True:
                task = tasks_q.get()
                try:
                    kind = task[0]
                    if kind == 'quit':
                        return
                    if kind == 'speak':
                        _, text, seq, ov = task
                        self._latest = max(self._latest, int(seq))
                        self._speak(text, overrides=ov, seq=int(seq))
                    elif kind == 'test':
                        _, ov = task
                        self._latest += 1
                        self._speak('Проверка голоса Рувойс',
                                    overrides=ov, seq=self._latest)
                except SystemExit:
                    return
                except Exception as e:
                    logutil.log('Ошибка синтеза:\n' + traceback.format_exc())
                    ui_q.put(('status', 'Ошибка: %s' % e))

        def _speak(self, text, overrides=None, seq=None):
            ov = overrides or {}
            text = ' '.join((text or '').split())
            if not text:
                return
            limit = int(cfg.get('max_read_chars', 1500))
            if len(text) > limit:
                text = text[:limit].rsplit(' ', 1)[0] + '…'
            engine = cfg.get('engine') or 'sapi'

            def synth(eng):
                rate = int(ov.get('rate', cfg.get('rate', 0)) or 0)
                volume = int(ov.get('volume', cfg.get('volume', 100)) or 100)
                if eng == 'sapi':
                    # голос можно переопределить кнопкой «Проверить голос»
                    voice = ov.get('voice', cfg.get('sapi_voice', '')) or ''
                    return tts.sapi_synthesize(text, voice, rate, volume)
                return tts.winrt_synthesize(
                    text, cfg.get('winrt_voice') or '', rate, volume)

            t0 = time.time()
            try:
                arr, sr = synth(engine)
            except Exception as e:
                # оба движка офлайн: если один недоступен — пробуем второй
                fb = 'winrt' if engine == 'sapi' else 'sapi'
                try:
                    arr, sr = synth(fb)
                    ui_q.put(('status', '%s недоступен (%s) — озвучено через %s'
                              % (engine, e, fb)))
                except Exception as e2:
                    ui_q.put(('status', 'Синтез речи не удался: %s' % e2))
                    ui_q.put(('read_done', None, 0))  # погасить рамку
                    audio_out.beep('fail')
                    return

            if seq is not None and seq != self._latest:
                return  # пришёл более новый запрос — старый пропускаем

            try:
                audio_out.play(arr, sr,
                               ov.get('device') or cfg.get('device_name'))
            except Exception as e:
                ui_q.put(('status', 'Ошибка воспроизведения: %s' % e))
                return

            dur = len(arr) / float(sr)
            if seq is None or seq == self._latest:
                ui_q.put(('read_done', seq, dur))
            ms = int((time.time() - t0) * 1000)
            ui_q.put(('status', 'Читаю (синтез %d мс): «%s%s»' %
                      (ms, text[:50], '…' if len(text) > 50 else '')))

    SynthWorker().start()

    # ---------------- OCR и чтение (главный поток GUI) --------------------
    def try_ocr(x, y, unit, grab_w, grab_h):
        try:
            res = ocr_reader.read_at(
                x, y, unit, grab_w, grab_h,
                int(cfg.get('ocr_scale', 2)),
                cfg.get('ocr_lang', 'auto'))
        except Exception as e:
            logutil.log('OCR ошибка:\n' + traceback.format_exc())
            gui.set_status('OCR: %s' % e)
            return None
        if not res or not res.get('text'):
            gui.set_status('OCR: текст не распознан (в исключительном '
                           'полноэкранном режиме игра может блокировать захват)')
            return None
        return res

    def try_ocr_area(l, t, w, h):
        try:
            res = ocr_reader.read_area(l, t, w, h,
                                       cfg.get('ocr_lang', 'auto'))
        except Exception as e:
            logutil.log('OCR области ошибка:\n' + traceback.format_exc())
            gui.set_status('OCR: %s' % e)
            return None
        if not res or not res.get('text'):
            gui.set_status('OCR: в выделенной области текст не распознан')
            return None
        return res

    def handle_read(x, y, unit):
        """Короткий клик: слово (или блок) под курсором."""
        if not cfg.get('enabled'):
            gui.set_status('Пауза: чтение отключено (кликните по иконке в трее)')
            return
        gui.set_status('Читаю под курсором…')
        seq = next(_read_seq)
        ov_state['gen'] = next(_gen_seq)
        res = try_ocr(x, y, unit,
                      480 if unit == 'word' else int(cfg.get('ocr_grab_w', 640)),
                      220 if unit == 'word' else int(cfg.get('ocr_grab_h', 260)))
        if not res:
            audio_out.beep('none')
            return
        logutil.log('Чтение (%s): %r' % (unit, (res['text'] or '')[:100]))
        if cfg.get('overlay', True) and res.get('rects'):
            overlay.show_rects(res['rects'], sticky=True)
        tasks_q.put(('speak', res['text'], seq, None))

    def handle_area(l, t, w, h):
        """Выделенная область: весь текст внутри неё."""
        if not cfg.get('enabled'):
            gui.set_status('Пауза: чтение отключено (кликните по иконке в трее)')
            return
        logutil.log('Выделена область: (%d,%d) %dx%d' % (l, t, w, h))
        seq = next(_read_seq)
        ov_state['gen'] = next(_gen_seq)
        if cfg.get('overlay', True):
            overlay.show_rects([(l, t, w, h)], sticky=True)
        gui.set_status('Распознаю область %d×%d…' % (w, h))
        res = try_ocr_area(l, t, w, h)
        if not res or not res.get('text'):
            overlay.hide()
            audio_out.beep('none')
            logutil.log('Область (%d,%d) %dx%d: текст не распознан' % (l, t, w, h))
            return
        logutil.log('Область: %r' % (res['text'] or '')[:100])
        tasks_q.put(('speak', res['text'], seq, None))

    def run_diag():
        import ctypes

        class PT(ctypes.Structure):
            _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

        pt = PT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        out = ['=== Диагностика %s ===' %
               time.strftime('%Y-%m-%d %H:%M:%S'),
               'Курсор: %d, %d' % (pt.x, pt.y)]
        try:
            tags = ocr_reader.available_tags()
            out.append('OCR языки: %s' %
                       (', '.join(tags) if tags else 'нет'))
        except Exception as e:
            out.append('OCR языки ОШИБКА: %r' % (e,))
        try:
            t0 = time.time()
            r = ocr_reader.read_at(pt.x, pt.y, 'word', 480, 220)
            out.append('OCR слово: %r (%.0f мс)' %
                       ((r['text'][:60] if r else 'ничего'),
                        (time.time() - t0) * 1000))
        except Exception as e:
            out.append('OCR ОШИБКА: %r' % (e,))
        try:
            engine = cfg.get('engine', 'sapi')
            out.append('Движок речи: %s' % engine)
            t0 = time.time()
            if engine == 'winrt':
                arr, sr = tts.winrt_synthesize('Проверка', '', 0, 100)
            else:
                arr, sr = tts.sapi_synthesize('Проверка', '', 0, 100)
            out.append('Синтез: OK, %.0f мс, %.2f c звука' %
                       ((time.time() - t0) * 1000, len(arr) / float(sr)))
        except Exception as e:
            out.append('Синтез ОШИБКА: %r' % (e,))
        text = '\n'.join(out)
        logutil.log(text)
        print(text)
        gui.set_status('Диагностика дописана в logs\\ruvoise.log')

    # ------------------------------ мышь ----------------------------------
    def on_read(src, x, y):
        # src: токен кнопки ('x1'/'x2'/'middle') или 'kb' (горячая клавиша);
        # нужность нажатия уже проверена хуком по настроенному триггеру
        ui_q.put(('read', int(x), int(y), 'word'))

    def on_area(l, t, w, h):
        ui_q.put(('area', int(l), int(t), int(w), int(h)))

    def on_drag(l, t, w, h):
        ui_q.put(('drag', (int(l), int(t), int(w), int(h))))

    hook = None
    if not SMOKE:
        # один слот назначения: триггер, а если он пуст — старый key_hotkey
        combo = str(cfg.get('trigger') or '').strip() \
            or str(cfg.get('key_hotkey') or '').strip() or 'x1'

        def on_mode(on):
            ui_q.put(('status', 'Режим выделения: ' + (
                'вкл — клик левой = слово, обвод = область; '
                'повторное сочетание выключает' if on else 'выкл')))

        def on_cancel():
            ui_q.put(('drag_cancel',))

        hook = mouse_hook.start(on_read, on_area, on_drag, combo=combo,
                                on_mode=on_mode, on_cancel=on_cancel)

    # --------------------------- окно настроек ----------------------------
    class AppBridge:
        def put_ui(self, msg):
            ui_q.put(msg)

        def set_status(self, text):
            ui_q.put(('status', text))

        def speak_test(self, overrides):
            tasks_q.put(('test', overrides))

        def diagnose(self):
            ui_q.put(('diag',))

        def hide_window(self):
            ui_q.put(('hide',))

        def apply_trigger(self, combo):
            ui_q.put(('apply_trigger', combo))

    gui = SettingsWindow(root, cfg, AppBridge())
    gui.set_status('Готово: зажмите кнопку и обведите область, '
                   'или коротко кликните — слово под курсором.')
    # крестик окна и «Свернуть в трей» прячут окно, приложение продолжает
    # работать из трея; выход — правый клик по иконке в трее → «Выход»
    root.protocol('WM_DELETE_WINDOW', lambda: ui_q.put(('hide',)))
    # GUI не должен выскакивать при запуске: работаем из трея
    # («Настройки» — правый клик по иконке в трее)
    if not SMOKE:
        root.withdraw()

    # ------------------------------ трей ----------------------------------
    tray = None
    if not SMOKE:
        try:
            from .tray import TrayIcon
            tray = TrayIcon(cfg, ui_q)
            tray.start()
        except Exception as e:
            print('[RuVoise] Трей недоступен:', e)

    # --------------------------- цикл событий UI --------------------------
    closing = {'flag': False}

    def shutdown():
        if closing['flag']:
            return
        closing['flag'] = True
        try:
            tasks_q.put_nowait(('quit',))
        except Exception:
            pass
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        if hook is not None:
            try:
                hook.stop()
            except Exception:
                pass
        if tray is not None:
            tray.stop()
        try:
            root.destroy()
        except Exception:
            pass

    def poll():
        drag_latest = None
        try:
            while True:
                msg = ui_q.get_nowait()
                kind = msg[0]
                if kind == 'status':
                    gui.set_status(msg[1])
                elif kind == 'read':
                    handle_read(msg[1], msg[2], msg[3])
                elif kind == 'area':
                    handle_area(msg[1], msg[2], msg[3], msg[4])
                elif kind == 'drag':
                    drag_latest = msg[1]
                elif kind == 'drag_cancel':
                    drag_latest = None
                    overlay.hide()
                elif kind == 'read_done':
                    _, seq, dur = msg
                    gen = ov_state['gen']
                    # рамка горит, пока длится чтение (+0.4 с запас)
                    root.after(int((dur + 0.4) * 1000),
                               lambda g=gen: overlay.hide()
                               if ov_state['gen'] == g else None)
                elif kind == 'diag':
                    run_diag()
                elif kind == 'show':
                    root.deiconify()
                    root.lift()
                    root.attributes('-topmost', True)
                    root.after(200, lambda: root.attributes('-topmost', False))
                elif kind == 'hide':
                    root.withdraw()
                elif kind == 'apply_trigger':
                    mouse_hook.set_trigger(msg[1])
                    logutil.log('Триггер изменён: %r' % (msg[1],))
                elif kind == 'quit':
                    shutdown()
                    return
        except queue.Empty:
            pass
        if drag_latest is not None:
            overlay.show_drag(drag_latest)  # пунктирная рамка выделения
            if os.environ.get('RUVOISE_DBG') == '1':
                print('[dbg] show_drag:', drag_latest, flush=True)
        root.after(15, poll)   # частый опрос: рамка следует за мышью плавно

    if SMOKE:
        root.after(1800, shutdown)
    poll()
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        try:
            import tkinter.messagebox as mb
            mb.showerror('RuVoise', 'Ошибка запуска:\n' + traceback.format_exc())
        except Exception:
            pass
