# -*- coding: utf-8 -*-
"""Зелёная рамка-подсветка: обычное окно + WINDOW REGION + быстрый Win32-апдейт.

Окну назначается регион в форме рамки (4 полосы) — внутренность окна
отсекается, и сквозь неё видно экран. Во время перетаскивания окно
двигается и меняет размер прямыми вызовами SetWindowPos + SetWindowRgn,
минуя Tk-геометрию, — рамка следует за мышью практически мгновенно.
"""

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.SetWindowRgn.restype = ctypes.c_int
user32.SetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN, wintypes.BOOL)
user32.SetWindowPos.restype = ctypes.c_bool
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_uint)
user32.ShowWindowAsync.restype = ctypes.c_bool
user32.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetWindowLongPtrW = getattr(user32, 'GetWindowLongPtrW',
                                   user32.GetWindowLongW)
user32.SetWindowLongPtrW = getattr(user32, 'SetWindowLongPtrW',
                                   user32.SetWindowLongW)
gdi32.CreateRectRgn.restype = wintypes.HRGN
gdi32.CreateRectRgn.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int)
gdi32.CombineRgn.restype = ctypes.c_int
gdi32.CombineRgn.argtypes = (wintypes.HRGN, wintypes.HRGN, wintypes.HRGN,
                             ctypes.c_int)
gdi32.DeleteObject.restype = ctypes.c_bool
gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)

HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_HIDE = 0
GA_ROOT = 2

_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000

_EDGE = '#00e676'   # зелёная рамка
_THICK = 4          # толщина полосы рамки
_RGN_OR = 2


class Overlay:
    def __init__(self, root):
        self.root = root
        self.win = None
        self._after = None
        self._size = (0, 0)
        self._shown_via_win = False

    # ------------------------------------------------------------------
    def _ensure(self):
        if self.win is not None:
            return
        import tkinter as tk
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg=_EDGE)
        self.win.withdraw()
        self._make_aux_styles()

    def _make_aux_styles(self):
        try:
            hwnd = self._hwnd()
            if not hwnd:
                return
            ex = user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
            # ВАЖНО: без WS_EX_TRANSPARENT — на неслоёном окне он меняет
            # порядок отрисовки и рамка «прорисовывается под» другими окнами.
            user32.SetWindowLongPtrW(
                hwnd, _GWL_EXSTYLE,
                ex | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE)
        except Exception as e:
            print('[RuVoise] overlay стили:', e)

    def _hwnd(self):
        try:
            hwnd = self.win.winfo_id()
            return user32.GetAncestor(hwnd, GA_ROOT) or \
                user32.GetParent(hwnd) or hwnd
        except Exception:
            return None

    # ------------------------------------------------------------------
    def _apply_region(self, hwnd, w, h):
        """Регион = 4 полосы рамки (внутренность отсекается)."""
        th = min(_THICK, max(1, min(w, h) // 4))
        top = gdi32.CreateRectRgn(0, 0, w, th)
        bottom = gdi32.CreateRectRgn(0, h - th, w, h)
        left = gdi32.CreateRectRgn(0, 0, th, h)
        right = gdi32.CreateRectRgn(w - th, 0, w, h)
        rgn = gdi32.CreateRectRgn(0, 0, 0, 0)
        gdi32.CombineRgn(rgn, top, bottom, _RGN_OR)
        gdi32.CombineRgn(rgn, rgn, left, _RGN_OR)
        gdi32.CombineRgn(rgn, rgn, right, _RGN_OR)
        gdi32.DeleteObject(top)
        gdi32.DeleteObject(bottom)
        gdi32.DeleteObject(left)
        gdi32.DeleteObject(right)
        # система забирает владение регионом
        user32.SetWindowRgn(hwnd, rgn, True)

    # ------------------------------------------------------------------
    def _win_show(self, l, t, w, h):
        """Показ/движение/ресайз прямыми вызовами Win32 — без Tk-геометрии."""
        hwnd = self._hwnd()
        if not hwnd:
            return
        try:
            if not self._shown_via_win:
                self.win.deiconify()   # первый показ — через Tk
                self._shown_via_win = True
        except Exception:
            pass
        user32.SetWindowPos(hwnd, HWND_TOPMOST, int(l), int(t), int(w), int(h),
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)
        if self._size != (w, h):
            self._apply_region(hwnd, w, h)
            self._size = (w, h)

    # ------------------------------------------------------------------
    def show_rects(self, rects, ms=1000, sticky=False, dashed=False):
        try:
            l = min(r[0] for r in rects)
            t = min(r[1] for r in rects)
            rgt = max(r[0] + r[2] for r in rects)
            btm = max(r[1] + r[3] for r in rects)
        except Exception:
            return
        w, h = rgt - l, btm - t
        if w < 12 or h < 12:
            return
        self._ensure()
        self._win_show(l, t, w, h)
        if self._after is not None:
            try:
                self.root.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if not sticky:
            self._after = self.root.after(ms, self.hide)

    def show_drag(self, rect):
        """Рамка выделения (держится, пока тянут)."""
        self.show_rects([rect], sticky=True)

    def hide(self):
        self._after = None
        try:
            hwnd = self._hwnd()
            if hwnd:
                user32.ShowWindowAsync(hwnd, SW_HIDE)
        except Exception:
            pass

    def prewarm(self):
        """Создать окно заранее и вывести его за экран — первый показ
        реальной рамки тогда обходится без Tk и без deiconify."""
        self._ensure()
        try:
            self.root.update_idletasks()
            hwnd = self._hwnd()
            if hwnd:
                user32.SetWindowPos(hwnd, HWND_TOPMOST, -32000, -32000,
                                    40, 40, SWP_NOACTIVATE | SWP_SHOWWINDOW)
                self._shown_via_win = True
                self._size = (40, 40)
                self._apply_region(hwnd, 40, 40)
        except Exception:
            pass
