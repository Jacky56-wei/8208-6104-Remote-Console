# -*- coding: utf-8 -*-
"""通道 bus name（訊號名稱）→ 示波器。照 All in Test tool u2core/scopesync.py（set_names / set_alias / label_cmds）：

  通道名稱 CHANNEL NAME（前面板 Add/Edit Name）：VBS app.Acquisition.Cn.Alias = "名稱"（空白 → RemoveAlias）
      支不支援：VBS? 'return=Len(app.Acquisition.C1.Alias)' 回數字 且 CMR? = 0（同一台只試一次）
  波形 Label：VBS app.Acquisition.Cn.LabelsText = "名稱" ＋ ViewLabels = True（清掉：LabelsText = "" ＋ ViewLabels = False）
  mode：name = 只設 Alias（不支援 → 自動改寫 Label；工具預設）/ label = 只設 Label / both = 兩個都設
  示波器只吃 ASCII：中文會被換成空白；全中文 → "Cn"。Label 最長 24 字、Alias 最長 250 字。
"""
from __future__ import annotations

import re

MODES = ("name", "label", "both")
ALIAS_MAX = 250


def _vbs_val(r):
    r = str(r or "").strip()
    return r[3:].strip() if r.upper().startswith("VBS") else r


def label_text(name, ch):
    s = re.sub(r"[^\x20-\x7E]", " ", str(name or ""))
    s = re.sub(r"\s+", " ", s).strip(" /-").replace('"', "'")
    return (s or f"C{ch}")[:24]


def alias_text(name, ch):
    if not str(name or "").strip():
        return ""
    s = re.sub(r"[^\x20-\x7E]", " ", str(name)).replace('"', "").replace("'", "")
    s = re.sub(r"\s+", " ", s).strip(" /-")
    return (s or f"C{ch}")[:ALIAS_MAX]


def label_cmds(ch, name):
    return [f"VBS 'app.Acquisition.C{ch}.LabelsText = \"{label_text(name, ch)}\"'",
            f"VBS 'app.Acquisition.C{ch}.ViewLabels = True'"]


def label_off_cmds(ch):
    return [f"VBS 'app.Acquisition.C{ch}.LabelsText = \"\"'", f"VBS 'app.Acquisition.C{ch}.ViewLabels = False'"]


def probe_alias(query):
    try:
        ok = bool(re.fullmatch(r"\d+", _vbs_val(query("VBS? 'return=Len(app.Acquisition.C1.Alias)'"))))
        if ok:
            nums = re.findall(r"-?\d+", str(query("CMR?")))
            ok = (int(nums[-1]) == 0) if nums else True
    except Exception:  # noqa: BLE001
        ok = False
    return ok


def set_names(write, query, pairs, mode="name", log=print, clear_empty=False):
    """[(ch, 名稱)] → 通道 Alias / Label。名稱空白：clear_empty=True → 清掉（RemoveAlias + Label 關），否則略過
    → {"alias": True/False/None, "set": [(ch, 顯示名稱)]}"""
    mode = mode if mode in MODES else "name"
    alias_ok = probe_alias(query) if mode in ("name", "both") else None
    done = []
    for ch, name in pairs:
        ch = int(ch)
        blank = not str(name or "").strip()
        if blank and not clear_empty:
            continue
        if alias_ok:
            a = alias_text(name, ch)
            write(f"VBS 'app.Acquisition.C{ch}.Alias = \"{a}\"'" if a else f"VBS 'app.Acquisition.C{ch}.RemoveAlias'")
        if mode in ("label", "both") or (mode == "name" and alias_ok is False):
            for c in (label_off_cmds(ch) if blank else label_cmds(ch, name)):
                write(c)
        done.append((ch, "" if blank else label_text(name, ch)))
    if log and done:
        how = {"name": "通道名稱 Alias", "label": "波形 Label", "both": "Alias + Label"}[mode]
        log(f"bus name（{how}）：" + "、".join(f"C{c}={n or '(清除)'}" for c, n in done)
            + ("" if alias_ok is not False else "；這台 MAUI 不支援 Alias → 改寫 Label"))
    return {"alias": alias_ok, "set": done}


def read_names(query, chs):
    """讀回示波器上的 bus name：Alias 優先，沒有 → Label 第一段 → {ch: 名稱}"""
    out = {}
    sup = probe_alias(query)
    for ch in chs:
        nm = ""
        if sup:
            try:
                n = _vbs_val(query(f"VBS? 'return=Len(app.Acquisition.C{ch}.Alias)'"))
                if re.fullmatch(r"\d+", n) and int(n):
                    nm = _vbs_val(query(f"VBS? 'return=app.Acquisition.C{ch}.Alias'")).strip('"')
            except Exception:  # noqa: BLE001
                nm = ""
        if not nm:
            try:
                nm = _vbs_val(query(f"VBS? 'return=app.Acquisition.C{ch}.LabelsText'")).strip().strip('"').split(",")[0].strip()
            except Exception:  # noqa: BLE001
                nm = ""
        if nm:
            out[int(ch)] = nm
    return out
