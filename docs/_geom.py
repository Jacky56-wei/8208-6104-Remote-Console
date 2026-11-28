# -*- coding: utf-8 -*-
"""（文件用）啟動 GUI，在截圖前把各元件的文字與螢幕座標倒到 JSON，供 make_images.py 對位標註。
用法：python3 docs/_geom.py out.json <gui 參數…>"""
import json
import sys
import tkinter as tk
from tkinter import ttk

out = sys.argv[1]
argv = sys.argv[2:]
from scope_wave_saver import gui  # noqa: E402

_orig = gui.WaveSaverUI.mainloop


def _walk(w, acc):
    try:
        if w.winfo_viewable():
            t = ""
            try:
                t = str(w.cget("text"))
            except Exception:
                pass
            acc.append({"cls": w.winfo_class(), "text": t, "x": w.winfo_rootx(), "y": w.winfo_rooty(),
                        "w": w.winfo_width(), "h": w.winfo_height()})
    except Exception:
        return
    for c in w.winfo_children():
        _walk(c, acc)


def _mainloop(self, *a, **k):
    def dump():
        acc = []
        _walk(self, acc)
        json.dump(acc, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    at = 3000
    if "--shot-after" in argv:
        at = int(float(argv[argv.index("--shot-after") + 1]) * 1000) - 400
    self.after(at, dump)
    return _orig(self, *a, **k)


gui.WaveSaverUI.mainloop = _mainloop
gui.main(argv)
