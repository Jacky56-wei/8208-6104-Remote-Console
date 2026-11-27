# -*- coding: utf-8 -*-
"""⤓ 讀回儀器設定 / ⤒ 寫入儀器設定（兩台：主 MASTER、輔 SLAVE）— 草稿，尚未實機測試

設定 dict（同 Autotest_tool u2core/instprofile 的 "scope" 區：chan_setup / trig / pm，加 sync）：
  {"model", "role": "master"/"slave",
   "trig": {"src": "C1", "level": 0.42, "slope": "POS"/"NEG"/"EITHER", "mode": "SINGLE"/"NORM"/"AUTO"},
   "timebase": {"tdiv": 1e-3, "delay": -3e-3},
   "channels": [{"ch": 1, "on": True, "name": "CORE", "vdiv": 0.2, "offset": -0.7, "unit": "V"}, …],
   "pm": [P1–P24 列], "sync": {"aux_out": "TriggerOut", "ext_src": "EX10", "ext_level": 0.5}}
   （不碰 10 MHz Ref In / Ref Out，不接 cable）
指令來源（Autotest_tool）：
  觸發   trigpanel.write_trigger：TRIG_SELECT EDGE,SR,Cn,HT,OFF · Cn:TRIG_LEVEL · Cn:TRIG_SLOPE；
         EITHER → Cn:TRIG_SLOPE WINDOW，讀回不是雙向 → VBS app.Acquisition.Trigger.Cn.Slope = "Either"
  時基   TIME_DIV · TRIG_DELAY；通道 Cn:VOLT_DIV / Cn:OFFSET（電流 A → VBS VerScale / VerOffset，scopesync.vdiv_cmd）
  名稱   busname.set_names（scopesync）；P1–P24 pmeas.setup（只寫示波器支援的 Pn，其餘 PC 計算並提醒）
  同步   scope2.sync_plan：MASTER AUXILIARY_OUTPUT TRIGGER_OUT ＋ VBS AuxOutput.AuxMode = "TriggerOut"；
         SLAVE TRIG_SELECT EDGE,SR,EX10,HT,OFF · EX10:TRIG_LEVEL 0.5 · EX10:TRIG_SLOPE POS
SLAVE 的觸發一律是 EXT（跟 MASTER Aux Out），UI 的觸發通道只寫到 MASTER。
"""
from __future__ import annotations

import re

from . import busname as BN
from . import pmeas as PM
from .lecroy import SCOPES

AUX_VBS = "app.Acquisition.AuxOutput.AuxMode"
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _num(s, d=None):
    m = _NUM.findall(str(s or ""))
    return float(m[-1]) if m else d


def _vbs(r):
    r = str(r or "").strip()
    return (r[3:] if r.upper().startswith("VBS") else r).strip().strip('"')


def slope_q(q, ch):
    """Cn:TRIG_SLOPE? → POS / NEG / EITHER（WINDOW 或 VBS Either 都算雙向）"""
    try:
        s = str(q(f"C{ch}:TRIG_SLOPE?")).split()[-1].upper()
    except Exception:  # noqa: BLE001
        s = ""
    if s in ("WINDOW", "EITHER"):
        return "EITHER"
    if s in ("POS", "NEG"):
        return s
    try:
        v = _vbs(q(f"VBS? 'return=app.Acquisition.Trigger.C{ch}.Slope'")).upper()
        return {"POSITIVE": "POS", "NEGATIVE": "NEG", "EITHER": "EITHER"}.get(v, v or "?")
    except Exception:  # noqa: BLE001
        return "?"


def read(q, model, role="master"):
    """示波器 → 設定 dict（只讀）"""
    n = SCOPES[model][1]
    out = {"model": model, "role": role}
    sel = str(q("TRIG_SELECT?"))
    m = re.search(r"\b(C\d|EX10|EX)\b", sel.upper())
    src = m.group(1) if m else "C1"
    out["trig"] = {"src": src, "mode": str(q("TRIG_MODE?")).split()[-1].upper(),
                   "level": _num(q(f"{src}:TRIG_LEVEL?")),
                   "slope": slope_q(q, src[1:]) if src.startswith("C") else str(q(f"{src}:TRIG_SLOPE?")).split()[-1].upper()}
    out["timebase"] = {"tdiv": _num(q("TIME_DIV?")), "delay": _num(q("TRIG_DELAY?"))}
    names = BN.read_names(q, range(1, n + 1))
    chans = []
    for c in range(1, n + 1):
        chans.append({"ch": c, "on": "ON" in str(q(f"C{c}:TRACE?")).upper(), "name": names.get(c, ""),
                      "vdiv": _num(q(f"C{c}:VOLT_DIV?")), "offset": _num(q(f"C{c}:OFFSET?"))})
    out["channels"] = chans
    out["pm"] = PM.read_setup(q, model) + [{"on": False, "src1": "C1", "src2": PM.NONE, "level": "", "param": "MAX"}
                                           for _ in range(PM.NP - PM.inst_max(model))]
    sync = {}
    for k, expr in (("aux_out", AUX_VBS),):
        try:
            sync[k] = _vbs(q(f"VBS? 'return={expr}'"))
        except Exception:  # noqa: BLE001
            sync[k] = "?"
    out["sync"] = sync
    return out


def trigger_cmds(ch, level, slope):
    """邊緣觸發（trigpanel.write_trigger 的指令；EITHER 先送 WINDOW，讀回不對再送 VBS — 由 write() 處理）"""
    cmds = [f"TRIG_SELECT EDGE,SR,C{int(ch)},HT,OFF", f"C{int(ch)}:TRIG_LEVEL {float(level):.6g}"]
    sl = str(slope).upper()
    cmds.append(f"C{int(ch)}:TRIG_SLOPE {'WINDOW' if sl == 'EITHER' else sl}")
    return cmds


def validate(cfg):
    """寫入前檢查 → (錯誤 list, 提醒 list)；有錯誤就不寫"""
    err, warn = [], []
    model = cfg["model"]
    n = SCOPES[model][1]
    t = cfg.get("trig") or {}
    if cfg.get("role") == "master":
        c = PM.ch_of(t.get("src"))
        if c is None or c > n:
            err.append(f"觸發通道 {t.get('src')} 不在 C1–C{n}")
        if str(t.get("slope", "")).upper() not in ("POS", "NEG", "EITHER"):
            err.append(f"斜率 {t.get('slope')} 只能 POS / NEG / EITHER")
        if t.get("level") is None:
            err.append("觸發準位沒填")
    try:
        slots = PM.parse(cfg.get("pm") or [], model, n)
        warn += PM.warnings(slots)
    except ValueError as e:
        err.append(str(e))
    return err, warn


def write(w, q, cfg, log=print):
    """UI 設定 → 示波器（先 validate；不支援的 Pn 不送，改 PC 計算並提醒）→ 讀回不符清單"""
    err, warn = validate(cfg)
    if err:
        raise ValueError("；".join(err))
    for x in warn:
        log("⚠ " + x)
    model, n = cfg["model"], SCOPES[cfg["model"]][1]
    bad = []
    tb = cfg.get("timebase") or {}
    if tb.get("tdiv"):
        w(f"TIME_DIV {float(tb['tdiv']):.6g}")
    if tb.get("delay") is not None:
        w(f"TRIG_DELAY {float(tb['delay']):.6g}")
    for c in cfg.get("channels") or []:
        ch = int(c["ch"])
        w(f"C{ch}:TRACE {'ON' if c.get('on') else 'OFF'}")
        if c.get("vdiv"):
            w(f"VBS 'app.Acquisition.C{ch}.VerScale = {float(c['vdiv']):.6g}'" if c.get("unit") == "A"
              else f"C{ch}:VOLT_DIV {float(c['vdiv']):.6g}V")
        if c.get("offset") is not None:
            w(f"VBS 'app.Acquisition.C{ch}.VerOffset = {float(c['offset']):.6g}'" if c.get("unit") == "A"
              else f"C{ch}:OFFSET {float(c['offset']):.6g}V")
    BN.set_names(w, q, [(c["ch"], c.get("name", "")) for c in cfg.get("channels") or [] if c.get("on")],
                 mode=cfg.get("name_mode", "name"), log=log)
    sy = cfg.get("sync") or {}
    if cfg.get("role") == "master":
        t = cfg["trig"]
        ch = PM.ch_of(t["src"])
        for c in trigger_cmds(ch, t["level"], t["slope"]):
            w(c)
        if str(t["slope"]).upper() == "EITHER" and slope_q(q, ch) != "EITHER":
            w(f'VBS \'app.Acquisition.Trigger.C{ch}.Slope = "Either"\'')
        if slope_q(q, ch) != str(t["slope"]).upper():
            bad.append(f"觸發斜率讀回 {slope_q(q, ch)}（要 {t['slope']}）")
        if t.get("mode") in ("NORM", "AUTO"):
            w(f"TRIG_MODE {t['mode']}")
        if sy.get("on", True):
            for c in ("AUXILIARY_OUTPUT TRIGGER_OUT", f"VBS '{AUX_VBS} = \"TriggerOut\"'"):
                try:
                    w(c)
                except Exception:  # noqa: BLE001
                    pass
            if "TRIG" not in _vbs(q(f"VBS? 'return={AUX_VBS}'")).upper():
                bad.append("AUX OUT 不是 Trigger Out（Utilities → Aux Output 選 Trigger Out）")
    else:
        src, lv = sy.get("ext_src", "EX10"), float(sy.get("ext_level", 0.5))
        w(f"TRIG_SELECT EDGE,SR,{src},HT,OFF")
        w(f"{src}:TRIG_LEVEL {lv:.6g}")
        w(f"{src}:TRIG_SLOPE POS")
    slots = PM.parse(cfg.get("pm") or [], model, n)
    bad += PM.setup(w, q, slots, PM.inst_max(model), log=log)
    return bad
