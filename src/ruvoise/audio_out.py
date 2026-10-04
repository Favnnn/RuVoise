# -*- coding: utf-8 -*-
"""Вывод звука через sounddevice/PortAudio с выбором устройства.

Важно для Stream Deck: при устройстве 'default' RuVoise каждый раз играет
в текущее системное устройство вывода — то есть следует за переключением
«колонки ↔ наушники», которое делает плагин на Stream Deck. Можно и
закрепить конкретное устройство (например, наушники) — тогда вывод не
зависит от переключений.
"""

DEFAULT_KEY = 'default'
DEFAULT_LABEL = 'По умолчанию (следует за переключением звука в системе)'


def list_outputs():
    """Список устройств вывода: [{'index','name','api'}, ...]."""
    out = []
    try:
        devices = sd_query_devices()
    except Exception as e:
        print('[RuVoise] Не удалось получить список устройств:', e)
        return out
    apis = []
    try:
        import sounddevice as sd
        apis = list(sd.query_hostapis())
    except Exception:
        pass
    for i, d in enumerate(devices):
        try:
            if int(d.get('max_output_channels', 0)) > 0:
                api = ''
                try:
                    api = apis[int(d['hostapi'])]['name']
                except Exception:
                    pass
                out.append({'index': int(i), 'name': d['name'], 'api': api})
        except Exception:
            continue
    return out


def sd_query_devices():
    import sounddevice as sd
    return list(sd.query_devices())


def resolve(name):
    """Имя устройства → индекс PortAudio (None = системное умолчание)."""
    if not name or name == DEFAULT_KEY:
        return None
    for d in list_outputs():
        if d['name'] == name:
            return d['index']
    return None


def play(data, samplerate, device_name=DEFAULT_KEY):
    import sounddevice as sd
    try:
        sd.stop()  # прерываем предыдущую озвучку
    except Exception:
        pass
    dev = resolve(device_name)
    # device=None → берётся системное умолчание в момент запуска воспроизведения
    sd.play(data, int(samplerate), device=dev)


def beep(kind='none'):
    """Короткий сигнал вместо молчания.

    kind='none' — текст не распознан (один короткий низкий тон);
    kind='fail' — синтез не удался (нисходящий тон).
    """
    try:
        import numpy as np
        import sounddevice as sd
        sr = 22050
        if kind == 'fail':
            dur = 0.22
            t = np.linspace(0, dur, int(sr * dur), False)
            tone = np.sin(2 * np.pi * (520 - 900 * t) * t)
        else:
            dur = 0.09
            t = np.linspace(0, dur, int(sr * dur), False)
            tone = np.sin(2 * np.pi * 330 * t)
        env = np.minimum(1.0, (dur - t) / 0.03)
        data = (tone * env * 0.25).astype('float32')
        play(data, sr)
    except Exception:
        pass
