# -*- coding: utf-8 -*-
"""OCR-резерв: чтение текста, который нельзя выделить или который не видит
UI Automation (игры, canvas, картинки, нестандартные интерфейсы).

Как работает: снимаем область экрана вокруг курсора через GDI (BitBlt),
при необходимости увеличиваем, распознаём встроенным Windows OCR
(Windows.Media.Ocr — офлайн, использует языковые компоненты Windows),
и выбираем слово/строку/блок по позиции курсора.

Поддерживаются пакеты winrt-Windows.* (pywinrt) или старый winsdk.
"""

import asyncio
import ctypes
import importlib
import io
import math
import threading
from ctypes import wintypes

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000

_lock = threading.Lock()
_engine = None
_engine_tag = None


def _ns(name):
    """Импорт WinRT-модуля из пакета winrt-* (pywinrt) или winsdk."""
    for root in ('winrt', 'winsdk'):
        try:
            return importlib.import_module(root + '.windows.' + name)
        except ImportError:
            continue
    raise ImportError(
        'не установлен WinRT-пакет для OCR — выполните: '
        '.venv\\Scripts\\python.exe -m pip install --no-cache-dir '
        'winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging '
        'winrt-Windows.Storage.Streams winrt-Windows.Globalization '
        'winrt-Windows.Foundation')


# --------------------------------------------------------------------------
def available_tags():
    """Список языков, доступных Windows OCR (например ['ru-RU', 'en-US'])."""
    try:
        ocr = _ns('media.ocr')
        return [l.language_tag
                for l in ocr.OcrEngine.available_recognizer_languages]
    except Exception:
        return []


def _get_engine(pref='auto'):
    global _engine, _engine_tag
    with _lock:
        if _engine is not None:
            return _engine, _engine_tag
        ocr = _ns('media.ocr')
        langs = list(ocr.OcrEngine.available_recognizer_languages)
        eng, tag = None, None

        def try_lang(l):
            try:
                return ocr.OcrEngine.try_create_from_language(l)
            except Exception:
                return None

        if pref and pref != 'auto':
            want = str(pref).lower()
            for l in langs:
                if l.language_tag.lower().startswith(want):
                    eng, tag = try_lang(l), l.language_tag
                    if eng:
                        break
                    tag = None
        if eng is None:
            for l in langs:
                if l.language_tag.lower().startswith('ru'):
                    eng, tag = try_lang(l), l.language_tag
                    if eng:
                        break
                    tag = None
        if eng is None:
            try:
                eng = ocr.OcrEngine.try_create_from_user_profile_languages()
                if eng:
                    try:
                        tag = eng.recognizer_language.language_tag
                    except Exception:
                        tag = 'auto'
            except Exception:
                eng = None
        if eng is None:
            raise RuntimeError(
                'Windows OCR недоступен — установите русский языковой пакет: '
                'Параметры → Время и язык → Язык и регион → добавить русский '
                '(языковые компоненты, включая распознавание текста)')
        _engine, _engine_tag = eng, tag
        return _engine, _engine_tag


# --------------------------------------------------------------------------
# GDI-сигнатуры: чтобы handle-и корректно проходили и на x64
_gdi32 = ctypes.windll.gdi32
_user32 = ctypes.windll.user32
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int,
                                          ctypes.c_int)
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
_gdi32.BitBlt.restype = wintypes.BOOL
_gdi32.BitBlt.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, wintypes.HDC,
                          ctypes.c_int, ctypes.c_int, wintypes.DWORD)
_gdi32.GetDIBits.restype = ctypes.c_int
_gdi32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                             wintypes.UINT, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.UINT)
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = (wintypes.HDC,)
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = (wintypes.HWND,)
_user32.ReleaseDC.restype = ctypes.c_int
_user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)


def grab_rect(left, top, w, h):
    """Снимок экрана w×h с левого верхнего угла (left, top)
    → (PIL.Image, left, top)."""
    user32 = _user32
    gdi32 = _gdi32

    vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    w = max(16, min(int(w), vw))
    h = max(16, min(int(h), vh))
    left = max(vx, min(int(left), vx + vw - w))
    top = max(vy, min(int(top), vy + vh - h))

    hdc = user32.GetDC(None)
    if not hdc:
        raise RuntimeError('GetDC(0) не удался')
    mem, bmp, old = None, None, None
    try:
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mem, bmp)
        if not gdi32.BitBlt(mem, 0, 0, w, h, hdc, left, top,
                            SRCCOPY | CAPTUREBLT):
            raise RuntimeError(
                'BitBlt не удался (в исключительном полноэкранном режиме игра '
                'может блокировать захват — попробуйте режим «окно без рамки»)')
        class BMIH(ctypes.Structure):
            _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                        ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                        ('biBitCount', wintypes.WORD),
                        ('biCompression', wintypes.DWORD),
                        ('biSizeImage', wintypes.DWORD),
                        ('biXPelsPerMeter', wintypes.LONG),
                        ('biYPelsPerMeter', wintypes.LONG),
                        ('biClrUsed', wintypes.DWORD),
                        ('biClrImportant', wintypes.DWORD)]

        bih = BMIH(ctypes.sizeof(BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0) == 0:
            raise RuntimeError('GetDIBits не удался')
        from PIL import Image
        img = Image.frombytes('RGB', (w, h), buf.raw, 'raw', 'BGRX', 0, 1)
        return img, left, top
    finally:
        try:
            if mem is not None and old is not None:
                gdi32.SelectObject(mem, old)
            if bmp:
                gdi32.DeleteObject(bmp)
            if mem:
                gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, hdc)
        except Exception:
            pass


def grab_region(cx, cy, w, h):
    """Снимок области вокруг точки (cx, cy)."""
    return grab_rect(int(cx) - int(w) // 2, int(cy) - int(h) // 2, w, h)


# --------------------------------------------------------------------------
async def _pil_to_bitmap(pil_img):
    streams = _ns('storage.streams')
    imaging = _ns('graphics.imaging')
    png = io.BytesIO()
    pil_img.save(png, format='PNG')
    stream = streams.InMemoryRandomAccessStream()
    writer = streams.DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(png.getvalue())
    await writer.store_async()
    await writer.flush_async()
    decoder = await imaging.BitmapDecoder.create_async(stream)
    return await decoder.get_software_bitmap_async()


async def _recognize_async(engine, pil_img):
    bitmap = await _pil_to_bitmap(pil_img)
    return await engine.recognize_async(bitmap)


def recognize(pil_img, pref='auto'):
    """Распознаёт PIL-изображение → {'text','lines','lang'}.

    lines: [{'text': str, 'words': [{'text','x','y','w','h'}, ...]}]
    координаты — в пикселях переданного изображения.
    """
    engine, tag = _get_engine(pref)
    result = asyncio.run(_recognize_async(engine, pil_img))
    lines = []
    for line in getattr(result, 'lines', []) or []:
        words = []
        for wd in line.words or []:
            r = wd.bounding_rect
            words.append({'text': wd.text, 'x': int(r.x), 'y': int(r.y),
                          'w': int(r.width), 'h': int(r.height)})
        if words:
            lines.append({'text': line.text, 'words': words})
    return {'text': (result.text or '').strip(), 'lines': lines, 'lang': tag}


# --------------------------------------------------------------------------
def _variants(img):
    """Варианты предобработки: обычный, инвертированный (светлый текст на
    тёмном фоне), контрастный ч/б."""
    from PIL import ImageOps
    yield img
    try:
        yield ImageOps.invert(img.convert('RGB'))
    except Exception:
        pass
    try:
        yield ImageOps.autocontrast(img.convert('L')).convert('RGB')
    except Exception:
        pass


def _line_rect(line):
    xs = [w['x'] for w in line['words']]
    ys = [w['y'] for w in line['words']]
    x2 = max(w['x'] + w['w'] for w in line['words'])
    y2 = max(w['y'] + w['h'] for w in line['words'])
    return (min(xs), min(ys), x2 - min(xs), y2 - min(ys))


def _edge_dist(px, py, r):
    """Расстояние от точки до прямоугольника (0 — точка внутри)."""
    dx = max(r[0] - px, 0, px - (r[0] + r[2]))
    dy = max(r[1] - py, 0, py - (r[1] + r[3]))
    return math.hypot(dx, dy)


def _pick(lines, px, py, unit):
    """Выбор текста по точке (в координатах изображения).

    Приоритет — тому, что под курсором: содержащие точку слово/строка,
    затем ближайшие по расстоянию. Слишком далёкие строки отбрасываются,
    чтобы не читать случайные надписи в стороне от курсора.
    """
    if unit == 'block':
        text = ' '.join(l['text'] for l in lines)
        rects = [r for r in (_line_rect(l) for l in lines) if r]
        return (text, rects) if text else None

    if unit == 'line':
        best, best_score = None, None
        for l in lines:
            r = _line_rect(l)
            if not r:
                continue
            d = _edge_dist(px, py, r)
            if d > 250:  # далеко от курсора — не наше
                continue
            v = abs((r[1] + r[3] / 2) - py)
            score = d * 2 + v
            if best_score is None or score < best_score:
                best, best_score = (l, r), score
        return (best[0]['text'], [best[1]]) if best else None

    # unit == 'word'
    words = [(w, (w['x'], w['y'], w['w'], w['h']))
             for l in lines for w in l['words']]
    if not words:
        return None
    for w, r in words:
        if w['x'] <= px <= w['x'] + w['w'] and w['y'] <= py <= w['y'] + w['h']:
            return w['text'], [r]
    best, bestd = None, None
    for w, r in words:
        d = math.hypot(w['x'] + w['w'] / 2 - px, w['y'] + w['h'] / 2 - py)
        limit = max(1.5 * w['h'], 60)
        if d <= limit and (bestd is None or d < bestd):
            best, bestd = (w['text'], [r]), d
    return best


def read_at(cx, cy, unit='word', grab_w=640, grab_h=260, scale=2, lang='auto'):
    """OCR области вокруг курсора. Возвращает {'text','rects',...} или None.

    rects — в экранных координатах (для зелёной рамки).
    """
    img, ox, oy = grab_region(cx, cy, grab_w, grab_h)
    rel_x, rel_y = cx - ox, cy - oy
    scale = max(1, min(int(scale or 1), 4))
    for attempt, vimg in enumerate(_variants(img)):
        simg = vimg.resize((vimg.width * scale, vimg.height * scale)) \
            if scale > 1 else vimg
        res = recognize(simg, lang)
        if not res['lines']:
            continue
        picked = _pick(res['lines'], rel_x * scale, rel_y * scale, unit)
        if not picked:
            continue
        text, rects_img = picked
        rects = [(ox + round(x / scale), oy + round(y / scale),
                  round(w / scale), round(h / scale))
                 for (x, y, w, h) in rects_img]
        return {'text': ' '.join(text.split()), 'rects': rects,
                'source': 'ocr-%s' % unit, 'attempt': attempt,
                'lang': res['lang']}
    return None


def read_area(l, t, w, h, lang='auto', scale=None):
    """OCR произвольной прямоугольной области экрана.

    Возвращает {'text','rects','source',...} или None. Текст — все строки
    области в порядке чтения (сверху вниз); rects — прямоугольники строк
    в экранных координатах.
    """
    img, ox, oy = grab_rect(l, t, w, h)
    if scale is None:
        # большие области не увеличиваем — иначе слишком долго
        scale = 2 if img.width * img.height <= 1280 * 800 else 1
    scale = max(1, min(int(scale or 1), 4))
    for attempt, vimg in enumerate(_variants(img)):
        simg = vimg.resize((vimg.width * scale, vimg.height * scale)) \
            if scale > 1 else vimg
        res = recognize(simg, lang)
        if not res['lines']:
            continue
        text = ' '.join(x['text'] for x in res['lines']).strip()
        if not text:
            continue
        rects = [(ox + round(r[0] / scale), oy + round(r[1] / scale),
                  round(r[2] / scale), round(r[3] / scale))
                 for r in (_line_rect(x) for x in res['lines']) if r]
        return {'text': ' '.join(text.split()), 'rects': rects,
                'source': 'ocr-area', 'attempt': attempt,
                'lang': res['lang']}
    return None
