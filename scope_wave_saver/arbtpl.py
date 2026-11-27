# -*- coding: utf-8 -*-
"""N6705 ARB 範本（參數式，不用手動編點）→ Autotest_tool u2core.arbmodule.ArbConfig

使用者（t35u）：「ARB 設定可以直覺設定 ramp；brownout margin；instance on off on」
  RAMP      Ramp：起始 V → 結束 V，延遲、上升時間、停留、下降時間、結束停留
            → N6705 原生 Trapezoid（ARB:FUNC:SHAP TRAP；同 arbmodule.from_powercut 的 TRAP 參數）
  BROWNOUT  Brownout margin：標稱 V、掉到（V 或 % margin，可掃描 "-5%/-10%/-20%"）、掉落時間、下降 / 回升時間、
            間隔、重複次數 → 單一準位 = TRAP ＋ ARB:COUN；「全部放在同一個 ARB」= 各段 TRAP 轉 UDEF 串接（arbmodule.merge）
  ONOFFON   Instant ON-OFF-ON：ON 準位、先 ON 停留、OFF 時間（可掃描 "1ms/2ms/5ms"）、再 ON 停留、重複
            → User Defined 3 點（同 arbmodule ON-OFF-ON 磚、dutpower.cycle_config：ON → 0 V → ON，結束停在最後值）
掃描：每次擷取用下一個值（預設；一次 ARB = 一次擷取，序號對應掃描值）或全部放在同一個 ARB。
數值可帶單位（2ms、100u、12 V），用 arbmodule.parse_num（與 N6705 前面板一樣）。
"""
from __future__ import annotations

TEMPLATES = [("NONE", "不使用"), ("RAMP", "Ramp（上升 / 下降）"), ("BROWNOUT", "Brownout margin（掉電壓）"),
             ("ONOFFON", "Instant ON-OFF-ON")]
TPL_NAME = dict(TEMPLATES)

# (key, 顯示, 預設, 單位, 可掃描)
FIELDS = {
    "RAMP": [("v0", "起始", "0", "V", False), ("v1", "結束", "12", "V", False), ("t0", "延遲", "2ms", "s", False),
             ("tr", "上升時間", "1ms", "s", False), ("th", "停留", "20ms", "s", False), ("tf", "下降時間", "1ms", "s", False),
             ("te", "結束停留", "2ms", "s", False), ("n", "重複", "1", "n", False)],
    "BROWNOUT": [("vn", "標稱", "12", "V", False), ("dip", "掉到（V 或 %）", "-10%/-20%/-50%", "", True),
                 ("td", "掉落時間", "1ms", "s", False), ("te", "下降 / 回升", "100u", "s", False),
                 ("t0", "前段停留", "2ms", "s", False), ("ti", "間隔（回到標稱）", "10ms", "s", False),
                 ("n", "重複", "1", "n", False)],
    "ONOFFON": [("von", "ON 準位", "12", "V", False), ("t0", "先 ON 停留", "2ms", "s", False),
                ("toff", "OFF 時間", "1ms/2ms/5ms", "s", True), ("t1", "再 ON 停留", "20ms", "s", False),
                ("n", "重複", "1", "n", False)],
}


def _AM():
    from u2core import arbmodule as AM
    return AM


def num(text, unit=""):
    return _AM().parse_num(text, unit)


def values(text):
    """'1ms/2ms/5ms' 或 '1ms,2ms' → 字串清單"""
    s = str(text or "").replace("，", "/").replace(",", "/").replace(";", "/")
    return [x.strip() for x in s.split("/") if x.strip()]


def dip_volts(tok, vn):
    """'-10%' / '10%' → 標稱 × (1 − 10 %)；'90%' 以上也當 margin（|%|）；'10.8' → 10.8 V"""
    t = str(tok).strip()
    if t.endswith("%"):
        p = abs(float(t[:-1]))
        return vn * (1 - p / 100.0)
    return num(t, "V")


def n_sweep(tpl, p):
    for k, _lb, _d, _u, sw in FIELDS.get(tpl, []):
        if sw:
            return max(1, len(values(p.get(k, ""))))
    return 1


def sweep_label(tpl, p, idx):
    for k, lb, _d, _u, sw in FIELDS.get(tpl, []):
        if sw:
            vs = values(p.get(k, ""))
            if vs:
                return f"{lb} {vs[idx % len(vs)]}"
    return ""


def build(tpl, p, ch=1, idx=None, name=""):
    """範本 ＋ 參數 → ArbConfig。idx = 掃描第幾個值（None = 全部串在同一個 ARB）"""
    AM = _AM()
    if tpl == "RAMP":
        c = AM.ArbConfig(channel=ch, shape="TRAP", last_value=True, dc_value=num(p["v0"], "V"),
                         repeat=max(1, int(num(p.get("n", 1)))), name=name or "Ramp")
        c.params["TRAP"] = {"v0": num(p["v0"], "V"), "v1": num(p["v1"], "V"), "t0": num(p["t0"], "s"),
                            "t1": num(p["tr"], "s"), "t2": num(p["th"], "s"), "t3": num(p["tf"], "s"), "t4": num(p["te"], "s")}
        return c
    if tpl == "BROWNOUT":
        vn = num(p["vn"], "V")
        dips = [dip_volts(x, vn) for x in values(p["dip"])] or [vn]
        use = dips if idx is None else [dips[idx % len(dips)]]
        rep = max(1, int(num(p.get("n", 1))))
        segs = []
        for v in use:
            c = AM.from_powercut(vn, v, num(p["te"], "s"), num(p["td"], "s"), t_start=num(p["t0"], "s"),
                                 t_end=num(p["ti"], "s"), ch=ch, name=f"Brownout {v:g} V")
            segs.append((f"{v:g} V（{(v / vn - 1) * 100:+.0f} %）" if vn else f"{v:g} V", c, 1))
        if len(segs) == 1:
            c = segs[0][1]
            c.repeat, c.name = rep, name or c.name
            return c
        m = AM.merge(segs * rep, ch=ch, name=name or "Brownout margin")
        m.last_value, m.dc_value = True, vn
        return m
    if tpl == "ONOFFON":
        von = num(p["von"], "V")
        offs = [num(x, "s") for x in values(p["toff"])] or [1e-3]
        use = offs if idx is None else [offs[idx % len(offs)]]
        steps = []
        for t in use:
            steps += [[von, num(p["t0"], "s"), 0, "ON"], [0.0, t, 0, f"OFF {t * 1e3:g} ms"], [von, num(p["t1"], "s"), 0, "ON"]]
        c = AM.ArbConfig(channel=ch, shape="UDEF", steps=steps, last_value=True, dc_value=von,
                         repeat=max(1, int(num(p.get("n", 1)))), name=name or "Instant ON-OFF-ON")
        return c
    return None


def describe(tpl, p, idx=None):
    """info.json / summary 用的參數說明"""
    d = {"template": tpl, "template_name": TPL_NAME.get(tpl, tpl)}
    d.update({k: str(p.get(k, "")) for k, *_r in FIELDS.get(tpl, [])})
    if idx is not None and n_sweep(tpl, p) > 1:
        d["sweep"] = sweep_label(tpl, p, idx)
    return d


def preview(cfg):
    """→ (t[], v[]) 用 arbmodule.expand（含重複；最多畫 2 個循環）"""
    e = _AM().expand(cfg, tail=True)
    pts = list(e["points"])
    if e.get("tail"):
        t0, t1, v, _txt = e["tail"]
        pts += [(t0, v), (t1, v)]
    return [x for x, _v in pts], [v for _x, v in pts]


def profile(cfg, cycles=None):
    """實際輸出（給模擬）：(t[], v[])，包含 repeat 次數（最多 20 次）"""
    AM = _AM()
    n = 1 if cfg.continuous else min(max(int(cfg.repeat), 1), 20)
    e = AM.expand(cfg, tail=False, cycles=n if cycles is None else cycles)
    if e["shown"] < n:                                # expand 最多畫 2 個 → 自己接
        pts = AM.expand(cfg, tail=False, cycles=1)["points"]
        cyc = pts[-1][0] - pts[0][0]
        out = []
        for k in range(n):
            out += [(x + k * cyc, v) for x, v in pts]
        return [x for x, _v in out], [v for _x, v in out]
    return [x for x, _v in e["points"]], [v for _x, v in e["points"]]
