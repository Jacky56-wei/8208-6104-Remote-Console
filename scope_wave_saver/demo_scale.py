# -*- coding: utf-8 -*-
"""UI 原型用：標稱電壓 → Auto scale 的 V/div 顯示文字（實作改用 u2core.scopescale.best_vdiv）"""


def vdiv_for(vnom):
    if not vnom:
        return "—"
    need = vnom * 1.3 / 6.0
    for e in (-3, -2, -1, 0, 1):
        for m in (1, 2, 5):
            v = m * 10 ** e
            if v >= need:
                return f"{v * 1000:g} mV" if v < 1 else f"{v:g} V"
    return "10 V"
