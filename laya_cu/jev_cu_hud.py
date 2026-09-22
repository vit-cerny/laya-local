"""Transparent click-through overlay for Laya computer use.

Shows, over the desktop itself, where the agent is acting: a box around the element it chose, a
ring at the point it clicks, and a live panel with the step, speed, and the Laya decision log.
It disappears by itself a few seconds after each action, so nothing is left on screen.

stdlib only: a tkinter toplevel with a transparent colour key, marked WS_EX_LAYERED |
WS_EX_TRANSPARENT so clicks pass straight through to the app underneath. Tk must own its thread,
so the UI runs in a daemon thread and receives work through a queue.

    hud = DesktopHud()
    hud.show(rect={"x": 10, "y": 20, "w": 100, "h": 30}, point=(60, 35), lines=["step 1 ..."])

Set JEV_CU_HUD=0 to disable.
"""

import os
import queue
import threading

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - tkinter ships with CPython on Windows
    tk = None

KEY = "#010203"  # transparent colour key; must not appear in the drawn art
PANEL_BG = "#0b0e14"
ACCENT = "#58a6ff"
HIT = "#ff7b72"
TEXT = "#e6edf3"
MUTED = "#7d8590"

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


def enabled():
    return os.environ.get("JEV_CU_HUD", "1") != "0"


class DesktopHud:
    """A single overlay window driven from any thread. All methods are safe to call from anywhere."""

    def __init__(self, timeout_ms=4000):
        self.timeout_ms = timeout_ms
        self._queue = queue.Queue()
        self._thread = None
        self._ready = threading.Event()

    def start(self):
        if tk is None or self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        return self

    def show(self, rect=None, point=None, lines=()):
        self._queue.put((rect, point, list(lines)))

    def stop(self):
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=3)
        self._thread = None

    def _run(self):
        import ctypes

        user32 = ctypes.windll.user32
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", KEY)
        root.config(bg=KEY)
        x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        root.geometry(f"{w}x{h}+{x}+{y}")
        canvas = tk.Canvas(root, bg=KEY, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        root.update_idletasks()

        def click_through():
            # The style must land on the real toplevel HWND; Tk wraps its own, so try both.
            for hwnd in {root.winfo_id(), user32.GetParent(root.winfo_id())}:
                if not hwnd:
                    continue
                style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)

        click_through()
        root.withdraw()
        self._ready.set()

        def clear():
            canvas.delete("all")
            root.withdraw()

        def draw(rect, point, lines):
            canvas.delete("all")
            if rect and rect.get("w", 0) > 0 and rect.get("h", 0) > 0:
                canvas.create_rectangle(
                    rect["x"] - x, rect["y"] - y, rect["x"] + rect["w"] - x, rect["y"] + rect["h"] - y,
                    outline=ACCENT, width=2,
                )
            if point:
                px, py = point[0] - x, point[1] - y
                canvas.create_oval(px - 14, py - 14, px + 14, py + 14, outline=HIT, width=3)
                canvas.create_line(px - 22, py, px + 22, py, fill=HIT)
                canvas.create_line(px, py - 22, px, py + 22, fill=HIT)
            if lines:
                canvas.create_rectangle(10, 10, 470, 34 + 17 * len(lines), fill=PANEL_BG, outline=ACCENT)
                canvas.create_text(20, 22, anchor="w", text="LAYA CU", fill=ACCENT,
                                   font=("Consolas", 10, "bold"))
                for index, line in enumerate(lines):
                    canvas.create_text(20, 42 + 17 * index, anchor="w", text=line, fill=TEXT,
                                       font=("Consolas", 9))
            root.deiconify()
            click_through()
            root.after(self.timeout_ms, clear)

        def pump():
            try:
                while True:
                    item = self._queue.get_nowait()
                    if item is None:
                        root.quit()
                        return
                    draw(*item)
            except queue.Empty:
                pass
            root.after(60, pump)

        root.after(60, pump)
        # Hold the interpreter on the instance so it is never garbage-collected from the main
        # thread, which raises Tcl_AsyncDelete("async handler deleted by the wrong thread").
        self._root = root
        root.mainloop()


_HUD = None


def hud():
    """The shared overlay, started on first use. Returns None when disabled or tkinter is absent."""
    global _HUD
    if not enabled() or tk is None:
        return None
    if _HUD is None:
        _HUD = DesktopHud().start()
    return _HUD
