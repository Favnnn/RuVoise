# -*- coding: utf-8 -*-
"""Системные мелочи: DPI-awareness и автозапуск с Windows."""

import ctypes
import os
import sys


def enable_dpi_awareness():
    """Физические пиксели: чтобы координаты UIA, мыши и окон совпадали."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_APP = 'RuVoise'


def _command():
    from .app_config import ROOT_DIR
    exe = sys.executable
    if exe.lower().endswith('\\python.exe'):
        exe = exe[: -len('python.exe')] + 'pythonw.exe'
    script = os.path.join(ROOT_DIR, 'run.py')
    return '"%s" "%s"' % (exe, script)


def set_autostart(enable):
    """Добавляет/убирает RuVoise в автозагрузку текущего пользователя (HKCU)."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        if enable:
            winreg.SetValueEx(k, _APP, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(k, _APP)
            except FileNotFoundError:
                pass


def get_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, _APP)
            return bool(val)
    except OSError:
        return False


WM_CLOSE = 0x0010


def close_old_ruvoise_windows():
    """Аккуратно закрывает окна старых копий RuVoise (заголовок «RuVoise …»)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    my = k.GetCurrentProcessId()
    EW = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    closed = []

    def cb(hwnd, lp):
        if not u.IsWindowVisible(hwnd):
            return True
        b = ctypes.create_unicode_buffer(64)
        u.GetWindowTextW(hwnd, b, 64)
        title = b.value
        # именно окно программы (не папка «RuVoise — проводник» и т.п.)
        if title.startswith('RuVoise') and 'озвучка' in title.lower():
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != my:
                u.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                closed.append(pid.value)
        return True

    u.EnumWindows(EW(cb), 0)
    return closed


def acquire_single_instance():
    """True — это единственный запущенный экземпля RuVoise."""
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateMutexW(None, False, 'RuVoise_SingleInstance')
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
