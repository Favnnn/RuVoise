# -*- coding: utf-8 -*-
"""Синтез речи: Edge TTS (нейроголоса, онлайн) и SAPI (системные голоса, офлайн).

Оба движка возвращают (numpy-массив float32, частота), воспроизведение
идёт через sounddevice на выбранном пользователем устройстве.
"""

import asyncio
import io
import os
import tempfile
import threading
from collections import OrderedDict

import numpy as np  # noqa: F401 (нужен для soundfile/sounddevice)
import soundfile as sf

EDGE_FALLBACK_VOICES = ['ru-RU-DmitryNeural', 'ru-RU-SvetlanaNeural']

# Папка кэша синтеза (data/cache)
from .app_config import DATA_DIR  # noqa: E402
_CACHE_DIR = os.path.join(DATA_DIR, 'cache')


def _temp_wav():
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix='ruvoise_', suffix='.wav',
                                    dir=_CACHE_DIR)
        os.close(fd)
        return path
    except OSError:
        fd, path = tempfile.mkstemp(prefix='ruvoise_', suffix='.wav')
        os.close(fd)
        return path

_cache = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX = 64


def _cache_get(key):
    with _cache_lock:
        return _cache.get(key)


def _cache_put(key, value):
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def _run_async(coro, timeout):
    """Запускает корутину в отдельном потоке с лимитом времени."""
    result = {}

    def runner():
        try:
            result['value'] = asyncio.run(coro)
        except BaseException as e:
            result['error'] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise RuntimeError('превышено время ожидания сервиса (%s с)' % timeout)
    if 'error' in result:
        raise result['error']
    return result['value']


def _decode(data):
    arr, sr = sf.read(io.BytesIO(data), dtype='float32')
    if arr.ndim == 1:
        arr = arr[:, None]
    return arr, int(sr)


# ----------------------------- Edge TTS ------------------------------------
def edge_list_voices(timeout=25):
    """Список русских голосов Edge TTS (ShortName)."""
    import edge_tts

    async def _list():
        voices = await edge_tts.list_voices()
        out = []
        for v in voices:
            if str(v.get('Locale', '')).lower().startswith('ru'):
                out.append(v.get('ShortName'))
        return out or list(EDGE_FALLBACK_VOICES)

    return _run_async(_list(), timeout)


def edge_synthesize(text, voice, rate_pct=0, volume_pct=100):
    key = ('edge', text, voice, int(rate_pct), int(volume_pct))
    hit = _cache_get(key)
    if hit is not None:
        return hit
    import edge_tts

    async def _synth():
        c = edge_tts.Communicate(
            text, voice,
            rate='%+d%%' % int(rate_pct),
            volume='%+d%%' % int(volume_pct))
        chunks = []
        async for part in c.stream():
            try:
                if part.get('type') == 'audio':
                    chunks.append(part['data'])
            except Exception:
                continue
        return b''.join(chunks)

    data = _run_async(_synth(), 40)
    if not data:
        raise RuntimeError('пустой ответ Edge TTS')
    arr, sr = _decode(data)
    _cache_put(key, (arr, sr))
    return arr, sr


# ------------------------------- SAPI ---------------------------------------
_tls = threading.local()
_auto_voice = None
_last_used_voice = None   # голос последнего синтеза (для журнала смен)

_RU_HINTS = ('russian', 'ru-', 'ru)', 'русск', 'ирина', 'irina', 'pavel',
             'татьяна', 'tatyana', 'светлана', 'катя')


def _sapi_objs():
    """Пар объектов SAPI на поток (создаются один раз — быстрее старт)."""
    v = getattr(_tls, 'voice', None)
    if v is None:
        v = _sapi_create('SAPI.SpVoice')
        if v is None:
            raise RuntimeError('SAPI недоступен (Speech API не установлен)')
        st = _sapi_create('SAPI.SpFileStream')
        try:
            st.Format.Type = 23  # SAFT22kHz16BitMono
        except Exception:
            pass
        _tls.voice = v
        _tls.stream = st
    return v, _tls.stream


def _sapi_create(progid):
    import comtypes.client
    try:
        return comtypes.client.CreateObject(progid)
    except Exception:
        try:
            return comtypes.client.CreateObject(progid, dynamic=True)
        except Exception:
            return None


def _voices_count(col):
    try:
        return int(col.GetCount())
    except Exception:
        return int(col.Count)


def sapi_list_voices():
    v = _sapi_create('SAPI.SpVoice')
    if v is None:
        return []
    try:
        col = v.GetVoices()
        out = []
        for i in range(_voices_count(col)):
            try:
                out.append(col.Item(i).GetDescription(0))
            except Exception:
                pass
        return out
    except Exception:
        return []


def sapi_pick_default_voice():
    """Первый русский системный голос (для пустой настройки)."""
    v = _sapi_create('SAPI.SpVoice')
    if v is None:
        return ''
    try:
        col = v.GetVoices()
        descs = []
        for i in range(_voices_count(col)):
            try:
                descs.append(col.Item(i).GetDescription(0))
            except Exception:
                pass
    except Exception:
        return ''
    for d in descs:
        ld = (d or '').lower()
        if any(h in ld for h in _RU_HINTS):
            return d
    return descs[0] if descs else ''


def sapi_synthesize(text, voice_desc='', rate_pct=0, volume_pct=100):
    key = ('sapi', text, voice_desc, int(rate_pct), int(volume_pct))
    hit = _cache_get(key)
    if hit is not None:
        return hit

    v, stream = _sapi_objs()

    global _auto_voice, _last_used_voice
    desc = voice_desc or _auto_voice or ''
    if not desc:
        desc = sapi_pick_default_voice()
        _auto_voice = desc
    if desc:
        found = False
        try:
            col = v.GetVoices()
            for i in range(_voices_count(col)):
                tok = col.Item(i)
                if (tok.GetDescription(0) or '') == desc:
                    v.Voice = tok
                    found = True
                    break
        except Exception:
            pass
        if not found:
            from . import logutil
            logutil.log('Голос не найден: %r — используется прежний' % desc)
    # журнал: какой голос реально применяется (пишем при смене)
    try:
        cur_voice = v.Voice.GetDescription(0)
        if cur_voice != _last_used_voice:
            _last_used_voice = cur_voice
            from . import logutil
            logutil.log('Синтез: голос %s' % cur_voice)
    except Exception:
        pass
    r10 = max(-10, min(10, int(round(rate_pct / 10))))
    try:
        v.Rate = r10
    except Exception:
        pass
    try:
        v.Volume = max(0, min(100, int(volume_pct)))
    except Exception:
        pass

    path = _temp_wav()
    try:
        try:
            opened = False
            for mode in (3, 2, 1):  # SSFCreateForWrite и родственные
                try:
                    stream.Open(path, mode, False)
                    opened = True
                    break
                except Exception:
                    continue
            if not opened:
                raise RuntimeError('SAPI: не удалось открыть WAV-файл для записи')
            v.AudioOutputStream = stream
            # Скорость задаём и свойством v.Rate, и SAPI-тегом <rate> —
            # часть голосов надёжнее читает тег.
            if r10:
                from xml.sax.saxutils import escape
                payload = '<rate speed="%d">%s</rate>' % (r10, escape(text))
            else:
                payload = text
            try:
                v.Speak(payload, 8 if r10 else 16)  # SPFIsXML / SPFIsNotXML
            except TypeError:
                v.Speak(payload)
        finally:
            try:
                stream.Close()   # обязательно: иначе поток «залипает»
            except Exception:
                pass
        with open(path, 'rb') as f:
            data = f.read()
    except Exception:
        # COM-объекты могли застрять в плохом состоянии — пересоздаём
        _tls.voice = None
        _tls.stream = None
        raise
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    if not data:
        raise RuntimeError('SAPI: пустой результат синтеза')
    arr, sr = _decode(data)
    _cache_put(key, (arr, sr))
    return arr, sr


# ------------------------ Голоса Windows (WinRT) -----------------------------
def _ns(name):
    """Импорт WinRT-модуля из пакета winrt-* (pywinrt) или winsdk."""
    import importlib
    for root in ('winrt', 'winsdk'):
        try:
            return importlib.import_module(root + '.windows.' + name)
        except ImportError:
            continue
    raise ImportError(
        'не установлен WinRT-пакет — выполните: '
        '.venv\\Scripts\\python.exe -m pip install --no-cache-dir '
        'winrt-Windows.Media.SpeechSynthesis winrt-Windows.Storage.Streams')


def winrt_list_voices():
    """Русские голоса Windows (WinRT SpeechSynthesis) — офлайн."""
    ss = _ns('media.speechsynthesis')
    out = []
    for v in ss.SpeechSynthesizer.all_voices:
        try:
            out.append({'id': str(v.id), 'name': str(v.display_name),
                        'lang': str(v.language)})
        except Exception:
            continue
    ru = [v for v in out if v['lang'].lower().startswith('ru')]
    return ru if ru else out[:10]


async def _winrt_synth_async(text, voice_id, rate_factor, volume_factor):
    ss = _ns('media.speechsynthesis')
    streams = _ns('storage.streams')
    synth = ss.SpeechSynthesizer()
    try:
        if voice_id:
            for v in ss.SpeechSynthesizer.all_voices:
                if str(v.id) == voice_id:
                    synth.voice = v
                    break
        try:
            synth.options.rate = max(0.0, min(6.0, rate_factor))
        except Exception:
            pass
        try:
            synth.options.volume = max(0.0, min(1.0, volume_factor))
        except Exception:
            pass
        stream = await synth.synthesize_text_async(text)
        size = int(stream.size)
        rdr = streams.DataReader(stream.get_input_stream_at(0))
        await rdr.load_async(size)
        data = bytes(rdr.read_bytes(size))
    finally:
        try:
            synth.close()
        except Exception:
            pass
    return data


def winrt_synthesize(text, voice_id='', rate_pct=0, volume_pct=100):
    key = ('winrt', text, voice_id, int(rate_pct), int(volume_pct))
    hit = _cache_get(key)
    if hit is not None:
        return hit
    data = _run_async(
        _winrt_synth_async(text, voice_id,
                           1.0 + float(rate_pct) / 100.0,
                           float(volume_pct) / 100.0), 15)
    if not data:
        raise RuntimeError('пустой ответ синтезатора Windows')
    arr, sr = _decode(data)
    _cache_put(key, (arr, sr))
    return arr, sr
