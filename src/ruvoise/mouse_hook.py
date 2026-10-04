# -*- coding: utf-8 -*-
"""Глобальный перехват кнопок мыши и клавиш (pynput) — настраиваемый триггер.

Триггер чтения/выделения задаётся одной строкой:
  'x1'              — боковая кнопка 4
  'x2'              — боковая кнопка 5
  'middle'          — средняя кнопка (колесо)
  'x1+x2'           — аккорд: кнопки 4 и 5 зажаты вместе (можно и три)
  'ctrl+x1+x2'      — аккорд при зажатом Ctrl (так же Alt/Shift/Win)
  'f9', 'ctrl+r'    — клавиатурное сочетание
  'g+h', 'shift+g+h', '6+7' — аккорд из нескольких клавиш

Режим «выделение области»: зажать кнопку (или аккорд), обвести прямоугольник,
отпустить — прочитается весь текст внутри. Короткое нажатие (сдвиг меньше
DRAG_THRESHOLD) — чтение слова под курсором.
"""

import os
import threading

DRAG_THRESHOLD = 14  # px — сдвиг, начиная с которого нажатие считается выделением

_DBG = os.environ.get('RUVOISE_DBG') == '1'

_BTN_ORDER = {'x1': 0, 'x2': 1, 'middle': 2}
for _n in range(6, 33):
    _BTN_ORDER['x%d' % _n] = _n          # расширенные кнопки мыши, если появятся
_MOD_ORDER = ('ctrl', 'shift', 'alt', 'cmd')
_MOD_NAMES = {'ctrl': 'Ctrl', 'shift': 'Shift', 'alt': 'Alt', 'cmd': 'Win'}
_MOUSE_NAMES = {'x1': 'Кнопка 4', 'x2': 'Кнопка 5', 'middle': 'Средняя кнопка'}
for _n in range(6, 33):
    _MOUSE_NAMES['x%d' % _n] = 'Кнопка %d' % _n

# --- общее состояние триггера (читается колбэками слушателей) ---
_state = {
    'mods': set(),              # зажатые сейчас модификаторы
    'mouse': ['x1'],            # кнопки-триггер (аккорд, может быть пустым)
    'mods_for_mouse': set(),    # требуемые модификаторы для аккорда кнопок
    'keys': [],                 # клавиатурный аккорд (список клавиш)
    'mods_for_keys': set(),     # модификаторы клавиатурного аккорда
    'capture': False,           # True — идёт захват сочетания в настройках
}
_lock = threading.Lock()


def parse_combo(s):
    """'ctrl+x1+x2' -> (моды, [кнопки], [клавиши]); 'g+h' -> (моды, [], ['g','h'])."""
    parts = [p.strip().lower() for p in (s or '').split('+') if p.strip()]
    mods = {p for p in parts if p in _MOD_ORDER}
    buttons = sorted((p for p in parts if p in _BTN_ORDER),
                     key=lambda b: _BTN_ORDER[b])
    keys = sorted(p for p in parts if p not in _MOD_ORDER
                  and p not in _BTN_ORDER)
    return mods, buttons, keys


def canonical(mods, buttons=(), keys=()):
    """Каноническая строка: 'x1+x2', 'ctrl+g+h', '6+7'."""
    ms = [m for m in _MOD_ORDER if m in (mods or set())]
    btns = sorted(buttons or (), key=lambda b: _BTN_ORDER.get(b, 99))
    return '+'.join(ms + btns + sorted(keys or ()))


def combo_label(s):
    """Человекочитаемый вид: 'Кнопка 4 + Кнопка 5', 'Shift + G + H'."""
    mods, buttons, keys = parse_combo(s)
    parts = [_MOD_NAMES[m] for m in _MOD_ORDER if m in mods]
    parts += [_MOUSE_NAMES.get(b, b) for b in buttons]
    for k in keys:
        parts.append(k.upper() if len(k) <= 3
                     else k.replace('_', ' ').title())
    return ' + '.join(parts) if parts else '— не задано —'


def set_trigger(combo=None):
    """Обновить триггер на ходу (без перезапуска слушателей).

    Одна строка заменяет ВЕСЬ триггер: кнопки мыши ИЛИ клавиши.
    '' — ничего не назначено.
    """
    with _lock:
        if combo is None:
            return
        s = (combo or '').strip()
        mods, buttons, keys = parse_combo(s)
        if buttons:
            _state['mouse'] = list(buttons)
            _state['mods_for_mouse'] = set(mods)
            _state['keys'] = []
            _state['mods_for_keys'] = set()
        elif keys:
            _state['mouse'] = []
            _state['mods_for_mouse'] = set()
            _state['keys'] = list(keys)
            _state['mods_for_keys'] = set(mods)
        else:
            _state['mouse'] = []
            _state['mods_for_mouse'] = set()
            _state['keys'] = []
            _state['mods_for_keys'] = set()


def set_capture(on):
    """Включить/выключить режим захвата (пока окно настроек слушает ввод)."""
    with _lock:
        _state['capture'] = bool(on)


def start(on_read, on_area, on_drag, combo='x1', on_mode=None, on_cancel=None):
    """on_read(src, x, y) — короткое нажатие/горячая клавиша.

    on_area(l, t, w, h) — выделена область; on_drag(l, t, w, h) — рамка
    обновляется во время перетаскивания; on_mode(True/False) — включён
    режим выделения (для клавиатурного триггера); on_cancel() — жест
    оборван, нарисованную рамку надо убрать.
    """
    from pynput import keyboard, mouse

    _mode_cb = on_mode   # уведомление о режиме выделения (для статуса)
    _cancel_cb = on_cancel   # уборка рамки при оборванном жесте
    set_trigger(combo)
    st = {'active': False,
          'held': set(),    # зажатые сейчас кнопки-кандидаты
          'chord': set(),   # кнопки текущего аккорда (с первого нажатия)
          'armed': False,   # режим выделения для клавиатурного триггера
          'ldown': False,   # зажата левая кнопка в режиме выделения
          'frame': False,   # рамка перетаскивания сейчас нарисована
          'sx': 0, 'sy': 0, 'lx': None, 'ly': None}
    kst = {'held': set(),   # зажатые сейчас обычные клавиши аккорда
           'chord': set()}

    _MODMAP = {
        keyboard.Key.ctrl_l: 'ctrl', keyboard.Key.ctrl_r: 'ctrl',
        keyboard.Key.ctrl: 'ctrl',
        keyboard.Key.alt_l: 'alt', keyboard.Key.alt_r: 'alt',
        keyboard.Key.alt_gr: 'alt', keyboard.Key.alt: 'alt',
        keyboard.Key.shift_l: 'shift', keyboard.Key.shift_r: 'shift',
        keyboard.Key.shift: 'shift',
        keyboard.Key.cmd_l: 'cmd', keyboard.Key.cmd_r: 'cmd',
        keyboard.Key.cmd: 'cmd',
    }
    _BTNMAP = {mouse.Button.x1: 'x1', mouse.Button.x2: 'x2',
               mouse.Button.middle: 'middle'}

    def _key_token(key):
        """Стабильный токен клавиши: по VK для букв/цифр/F-клавиш (не зависит
        от раскладки и модификаторов), иначе по символу или имени."""
        vk = getattr(key, 'vk', None)
        if vk is None:
            vk = getattr(getattr(key, 'value', None), 'vk', None)
        if vk is not None:
            if 0x30 <= vk <= 0x39:                       # 0-9
                return chr(vk).lower()
            if 0x41 <= vk <= 0x5A:                       # A-Z (любая раскладка)
                return chr(vk).lower()
            if 0x70 <= vk <= 0x87:                       # F1-F24
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

    def _mods_snapshot():
        with _lock:
            return set(_state['mods'])

    def kb_press(key):
        try:
            m = _MODMAP.get(key)
            if m:
                with _lock:
                    _state['mods'].add(m)
                return
            with _lock:
                capt = _state['capture']
                want_btns = list(_state['mouse'])
                want_keys = list(_state['keys'])
            if capt or want_btns or not want_keys:
                return
            tok = _key_token(key)
            if not tok or tok == 'esc':
                return
            kst['held'].add(tok)
            kst['chord'].add(tok)
            if _DBG:
                print('[hook] kb chord press %s: %s' %
                      (tok, sorted(kst['chord'])), flush=True)
        except Exception as e:
            print('[RuVoise] key hook:', e)

    def kb_release(key):
        try:
            m = _MODMAP.get(key)
            if m:
                with _lock:
                    _state['mods'].discard(m)
                return
            tok = _key_token(key)
            if not tok:
                return
            kst['held'].discard(tok)
            if kst['held'] or not kst['chord']:
                return
            chord = set(kst['chord'])
            kst['chord'].clear()
            with _lock:
                capt = _state['capture']
                want_keys = list(_state['keys'])
                want_mods = set(_state['mods_for_keys'])
                want_btns = list(_state['mouse'])
            if capt or want_btns or not want_keys:
                return
            if _DBG:
                print('[hook] kb chord release: %s (want %s)' %
                      (sorted(chord), want_keys), flush=True)
            if chord != set(want_keys):
                return
            if want_mods and not want_mods.issubset(_mods_snapshot()):
                return
            # клавиатурный триггер: переключаем режим выделения.
            # В режиме: клик левой кнопкой — слово, обвод — область.
            with _lock:
                st['armed'] = not st['armed']
                armed = st['armed']
            if _mode_cb:
                try:
                    _mode_cb(armed)
                except Exception:
                    pass
        except Exception as e:
            print('[RuVoise] key release hook:', e)

    def _disarm():
        was_down = st['ldown']
        st['armed'] = False
        st['ldown'] = False
        if was_down and st['frame']:
            st['frame'] = False
            if _cancel_cb:
                try:
                    _cancel_cb()
                except Exception:
                    pass
        if _mode_cb:
            try:
                _mode_cb(False)
            except Exception:
                pass

    def _side_button(button, x, y, pressed):
        """Левая/правая кнопки в режиме выделения (клавиатурный триггер)."""
        if not st['armed']:
            return
        if button == mouse.Button.left:
            if pressed:
                st['ldown'] = True
                st['sx'], st['sy'] = x, y
                st['lx'], st['ly'] = None, None
            elif st['ldown']:
                st['ldown'] = False
                dx, dy = abs(x - st['sx']), abs(y - st['sy'])
                st['frame'] = False   # рамку заменит чтение слова/области
                if max(dx, dy) >= DRAG_THRESHOLD:
                    on_area(min(st['sx'], x), min(st['sy'], y), dx, dy)
                else:
                    on_read('left', x, y)
                _disarm()
        elif pressed:
            # любая другая кнопка снимает режим
            _disarm()

    def cb(x, y, button, pressed):
        tok = _BTNMAP.get(button)
        if tok is None:
            try:
                _side_button(button, int(x), int(y), pressed)
            except Exception as e:
                print('[RuVoise] mouse hook:', e)
            return
        try:
            x, y = int(x), int(y)
            with _lock:
                capt = _state['capture']
                want_btns = list(_state['mouse'])
                want_mods = set(_state['mods_for_mouse'])
                want_keys = list(_state['keys'])
            # клавиатурный триггер — мышь не участвует
            if capt or want_keys or not want_btns:
                if pressed and st['armed']:
                    _disarm()   # кнопки мыши снимают режим выделения
                return
            if pressed:
                st['held'].add(tok)
                if not st['chord']:
                    st['sx'], st['sy'] = x, y
                    st['lx'], st['ly'] = None, None
                st['chord'].add(tok)
                # рамка тянется только пока нажаты кнопки ТРИГГЕРА:
                # зажатое колёсико или чужая кнопка её не двигают
                st['active'] = set(st['chord']).issubset(set(want_btns))
                if _DBG:
                    print('[hook] chord press %s: %s active=%s' %
                          (tok, sorted(st['chord']), st['active']), flush=True)
                return
            # отпускание
            st['held'].discard(tok)
            if st['held'] or not st['chord']:
                return
            st['active'] = False
            chord = set(st['chord'])
            st['chord'].clear()
            shown = st['frame']
            st['frame'] = False
            if _DBG:
                print('[hook] chord release: %s (want %s)' %
                      (sorted(chord), want_btns), flush=True)
            if chord != set(want_btns):
                # жест оборван — убрать нарисованную рамку, если была
                if shown and _cancel_cb:
                    try:
                        _cancel_cb()
                    except Exception:
                        pass
                return
            if want_mods and not want_mods.issubset(_mods_snapshot()):
                if shown and _cancel_cb:
                    try:
                        _cancel_cb()
                    except Exception:
                        pass
                return
            dx, dy = abs(x - st['sx']), abs(y - st['sy'])
            if max(dx, dy) >= DRAG_THRESHOLD:
                on_area(min(st['sx'], x), min(st['sy'], y), dx, dy)
            else:
                on_read('+'.join(want_btns), x, y)
        except Exception as e:
            print('[RuVoise] mouse hook:', e)

    def mv(x, y):
        dragging = st['active'] or (st['ldown'] and st['armed'])
        if not dragging:
            return
        try:
            x, y = int(x), int(y)
            if st['lx'] is None or max(abs(x - st['lx']),
                                       abs(y - st['ly'])) >= 4:
                st['lx'], st['ly'] = x, y
                if _DBG:
                    print('[hook] mv:', x, y, flush=True)
                st['frame'] = True
                on_drag(min(st['sx'], x), min(st['sy'], y),
                        max(abs(x - st['sx']), 1), max(abs(y - st['sy']), 1))
        except Exception as e:
            print('[RuVoise] mouse move:', e)

    kl = keyboard.Listener(on_press=kb_press, on_release=kb_release)
    kl.daemon = True
    kl.start()
    listener = mouse.Listener(on_click=cb, on_move=mv)
    listener.daemon = True
    listener.start()
    return listener
