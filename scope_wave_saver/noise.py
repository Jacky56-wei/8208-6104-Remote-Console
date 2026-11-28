# -*- coding: utf-8 -*-
"""突波／雜訊量測（PC 端用存下來的波形算；不佔示波器 P 量測欄位）

每條「電源 rail」（類型 = 電源）算：
  · 穩態準位 Vss        = 穩態窗口內的中位數（不被 ripple 或單點雜訊帶偏）
  · 過衝 overshoot      = 到達穩態之後，波形最高點 − Vss（V，與佔 Vss 的 %）
  · 欠衝 undershoot     = 到達穩態之後，Vss − 波形最低點（V 與 %）
  · 漣波／雜訊 Vpp      = 穩態窗口內「去掉線性漂移」後的 max − min（V、佔 Vss 的 %）；另有 RMS 雜訊
  · 可選「頻寬限制」     = 數位低通（移動平均，−3 dB ≈ 0.443 / (N·dt)），例如 20 MHz；對 Vss、過衝／欠衝、漣波一起生效

穩態窗口怎麼找（不靠示波器 P 量測、不用觸發時間）：
  1. 取波形後段中位數 V_tail，容許帶 = max(1 %·V_tail, 3.5σ_MAD)（「穩態容許帶 %」可調）；窗口 = 最後一次「跑出容許帶」之後再多等 settle（預設 0.5 ms）。
  2. 後段不是穩態（例如下電、rail 結尾 = 0 V）→ 改用前段：第一次跑出容許帶之前（再提早 settle）。
  3. 兩段都不是標稱附近（|V − 標稱| > 30 %）或窗口太短 → 「無穩態」，不判。
過衝／欠衝只看「最後一次到達穩態」之後（ARB 的 ON-OFF-ON 先掉到 0 V 的設計動作不算欠衝）；前段窗口則看到窗口尾。
邏輯訊號（RSTn、BUSPG）不量；電流（IBUS）只記錄：過衝欄 = 峰值 − 穩態（A）、漣波欄 = 穩態雜訊 A，不判 PASS／FAIL（沒有限值）。

判定（每條 rail、三個項目各一個限值，任一項超過 → FAIL）：
  限值來源優先順序：使用者在 UI 填的（每條 rail 或改過的全域值）> 設計表 JSON 的選填欄位 > 預設值（預設值 = 待確認）。
  設計表 JSON（u2core dutspec 格式）每列可選填 ovs_pct、uns_pct、ripple_pct、ripple_mv；目前隨附兩份設計表沒有這些欄位。
  預設：過衝 ≤ 5 %、欠衝 ≤ 5 %、漣波 Vpp ≤ 2 %（皆佔 Vss）。這是「待確認」的工程預設，不是正式規格；
  預設值得到的 PASS／FAIL 預設「不計入規格總判定」（UI 勾選才計入）。
"""
from __future__ import annotations

import csv
import json
import os

import numpy as np

from .rails import kind as rail_kind, sort_rails

DEFAULT_LIMITS = {"ovs_pct": 5.0, "uns_pct": 5.0, "ripple_pct": 2.0, "ripple_mv": None}
SRC_DEFAULT, SRC_DESIGN, SRC_USER = "預設（待確認）", "設計表", "使用者"
LIMIT_KEYS = ("ovs_pct", "uns_pct", "ripple_pct", "ripple_mv")
BW_CHOICES = {"關閉": 0.0, "20 MHz": 20e6, "100 MHz": 100e6, "200 MHz": 200e6}
ROLE = {"8208HD": "主", "6104B": "輔"}
NOISE_CSV = ("突波雜訊判定",)
HEAD_ZH = ("示波器", "CH", "rail", "類型", "穩態 Vss", "過衝 V", "過衝 %", "欠衝 V", "欠衝 %", "漣波 Vpp", "漣波 %",
           "RMS 雜訊", "單位", "窗口起 ms", "窗口迄 ms", "窗口位置", "頻寬限制", "過衝限 %", "欠衝限 %", "漣波限 %",
           "漣波限 mV", "限值來源", "判定", "備註")


def default_cfg():
    return {"on": True, "save": True, "settle_ms": 0.5, "bw_hz": 0.0, "band_pct": 1.0,
            "limits": dict(DEFAULT_LIMITS), "limits_user": (), "rail_over": {}, "design": {}, "include_default": False}


# ---------------------------------------------------------------------------
# 量測數學
# ---------------------------------------------------------------------------
def bw_filter(y, dt, bw_hz):
    """數位頻寬限制：零相位移動平均（−3 dB ≈ 0.443/(N·dt)）。bw_hz 空／0 → 不濾。"""
    y = np.asarray(y, float)
    if not bw_hz or bw_hz <= 0 or not dt or dt <= 0 or len(y) < 8:
        return y
    n = int(round(0.443 / (float(bw_hz) * float(dt))))
    n = min(n, max(len(y) // 4, 1))
    if n <= 1:
        return y
    yp = np.pad(y, (n // 2, n - 1 - n // 2), mode="edge")
    return np.convolve(yp, np.ones(n) / n, mode="valid")


def _boxcar(y, n=5):
    if len(y) < n * 2:
        return y
    yp = np.pad(y, (n // 2, n - 1 - n // 2), mode="edge")
    return np.convolve(yp, np.ones(n) / n, mode="valid")


def _mad_sigma(x):
    x = np.asarray(x, float)
    if not len(x):
        return 0.0
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def _win_ok(a, b, n):
    return (b - a) >= max(16, int(0.05 * n))


def _find_window(t, yf, nom, kind, settle_s, band_pct):
    """→ (a, b, "後段"/"前段")；找不到 → 原因字串。窗口 = yf[a:b]。"""
    n = len(yf)
    yd = _boxcar(yf, 5)
    ref = float(nom) if nom and nom > 0 else (float(np.percentile(yf, 97)) if kind != "current" else None)

    def cand(level, band, tail):
        if tail:
            out = np.flatnonzero(np.abs(yd - level) > band)
            a = 0 if not len(out) else int(np.searchsorted(t, t[out[-1]] + settle_s))
            return a, n
        out = np.flatnonzero(np.abs(yd - level) > band)
        b = n if not len(out) else int(np.searchsorted(t, t[out[0]] - settle_s))
        return 0, b

    why = "窗口太短（穩態時間不夠；加長擷取時間或縮短穩態等待）"
    for tail, sl in ((True, slice(int(0.8 * n), n)), (False, slice(0, max(int(0.15 * n), 8)))):
        seg = yf[sl]
        if not len(seg):
            continue
        lv = float(np.median(seg))
        if kind != "current" and ref and abs(lv - ref) > 0.3 * abs(ref):
            why = f"波形準位 {lv:.4g} V 與標稱 {ref:.4g} V 差超過 30 %（未上電？標稱或通道名設錯？）"
            continue
        if kind != "current" and lv <= 0:
            why = "波形沒有電壓（未上電）"
            continue
        band = max(band_pct / 100.0 * abs(lv), 3.5 * _mad_sigma(seg), 1e-9)
        a, b = cand(lv, band, tail)
        if _win_ok(a, b, n):
            return a, b, "後段" if tail else "前段"
        why = "窗口太短（穩態時間不夠；加長擷取時間或縮短穩態等待）"
    return why


def measure(t, y, nom=None, kind="rail", settle_s=0.5e-3, bw_hz=0.0, band_pct=1.0):
    """單一波形 → dict（vss, ovs_v/pct, uns_v/pct, rip_pp/pct, rms, t_a, t_b, where, t_ovs, t_uns, status）。
    status = "ok" 或 無法量的原因（此時數值都是 None）。"""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    out = {"vss": None, "ovs_v": None, "ovs_pct": None, "uns_v": None, "uns_pct": None, "rip_pp": None,
           "rip_pct": None, "rms": None, "t_a": None, "t_b": None, "where": "", "t_ovs": None, "t_uns": None,
           "status": "ok"}
    if len(t) < 32 or len(t) != len(y):
        out["status"] = "波形太短"
        return out
    dt = float(np.median(np.diff(t)))
    yf = bw_filter(y, dt, bw_hz)
    w = _find_window(t, yf, nom, kind, float(settle_s), float(band_pct))
    if isinstance(w, str):
        out["status"] = "無穩態：" + w
        return out
    a, b, where = w
    seg = yf[a:b]
    vss = float(np.median(seg))
    x = t[a:b] - t[a]
    try:
        slope, icpt = np.polyfit(x, seg, 1)
        dseg = seg - (slope * x + icpt)
    except Exception:  # noqa: BLE001
        dseg = seg - np.mean(seg)
    rip = float(np.ptp(dseg))
    rms = float(np.std(dseg))
    n = len(yf)
    if where == "後段":
        below = np.flatnonzero(_boxcar(yf, 5)[:a] < 0.9 * vss)
        i0 = int(below[-1]) + 1 if len(below) else 0
        reach = np.flatnonzero(yf[i0:] >= vss - 0.5 * rip)
        i1 = i0 + int(reach[0]) if len(reach) else a
        reg = slice(min(i1, n - 1), n)
    else:
        reg = slice(0, b)
    r = yf[reg]
    tr = t[reg]
    imax, imin = int(np.argmax(r)), int(np.argmin(r))
    ovs = max(0.0, float(r[imax]) - vss)
    uns = max(0.0, vss - float(r[imin]))
    pct = (lambda v: (100.0 * v / abs(vss)) if abs(vss) >= 1e-3 else None)  # noqa: E731
    out.update(vss=vss, ovs_v=ovs, ovs_pct=pct(ovs), uns_v=uns, uns_pct=pct(uns), rip_pp=rip, rip_pct=pct(rip), rms=rms,
               t_a=float(t[a]), t_b=float(t[b - 1]), where=where,
               t_ovs=float(tr[imax]) if ovs > 0 else None, t_uns=float(tr[imin]) if uns > 0 else None)
    return out


# ---------------------------------------------------------------------------
# 限值與判定
# ---------------------------------------------------------------------------
def design_limits(profile=None):
    """從設計表 JSON 讀選填欄位 {rail: {ovs_pct, uns_pct, ripple_pct, ripple_mv}}。沒有欄位 → 空 dict。
    u2core 的 powerprofile.clean_row 會把未知欄位丟掉，所以這裡直接讀 JSON 檔。"""
    from . import speccmp as SC
    out = {}
    try:
        name = profile or SC.PP.get()
        path = None
        for fn in ("_path", "path"):
            f = getattr(SC.PP, fn, None)
            if callable(f):
                try:
                    p = f(name)
                    if p and os.path.isfile(p):
                        path = p
                        break
                except Exception:  # noqa: BLE001
                    pass
        if path is None:
            p = os.path.join(SC.BUNDLED_DIR, f"{name}.json")
            path = p if os.path.isfile(p) else None
        if not path:
            return out
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("rails") or []:
            lim = {}
            for k in LIMIT_KEYS:
                v = r.get(k)
                try:
                    if v not in (None, ""):
                        lim[k] = float(v)
                except (TypeError, ValueError):
                    pass
            if lim:
                out[SC.canon(r.get("ch"))] = lim
    except Exception:  # noqa: BLE001
        return {}
    return out


def limits_for(name, cfg):
    """→ (limits{ovs_pct,uns_pct,ripple_pct,ripple_mv}, 來源文字, 來源 key 集合)。每個欄位依 使用者 > 設計表 > 預設。"""
    from . import speccmp as SC
    base = dict(DEFAULT_LIMITS)
    base.update({k: v for k, v in (cfg.get("limits") or {}).items() if k in LIMIT_KEYS})
    user = (cfg.get("rail_over") or {}).get(name) or {}
    des = (cfg.get("design") or {}).get(SC.canon(name)) or {}
    lim, srcs = {}, set()
    for k in LIMIT_KEYS:
        if user.get(k) is not None:
            lim[k], s = user[k], SRC_USER
        elif des.get(k) is not None:
            lim[k], s = des[k], SRC_DESIGN
        else:
            lim[k], s = base.get(k), (SRC_USER if k in (cfg.get("limits_user") or ()) else SRC_DEFAULT)
        if lim[k] is not None:
            srcs.add(s)
    if not srcs:
        text = "待填"
    elif srcs == {SRC_DEFAULT}:
        text = SRC_DEFAULT
    else:
        text = "＋".join(s for s in (SRC_USER, SRC_DESIGN, SRC_DEFAULT) if s in srcs)
    return lim, text, srcs


def limit_text(lim):
    bits = []
    if lim.get("ovs_pct") is not None:
        bits.append(f"過衝≤{lim['ovs_pct']:g}%")
    if lim.get("uns_pct") is not None:
        bits.append(f"欠衝≤{lim['uns_pct']:g}%")
    if lim.get("ripple_pct") is not None:
        bits.append(f"漣波≤{lim['ripple_pct']:g}%")
    if lim.get("ripple_mv") is not None:
        bits.append(f"漣波≤{lim['ripple_mv']:g}mV")
    return " ".join(bits) or "待填"


def judge(res, lim):
    """→ (verdict, why)。PASS／FAIL／N/A；N/A 的原因一律寫出（待填、無穩態、電流只記錄）。"""
    if res.get("status") != "ok":
        return "N/A", res.get("status") or "無實測"
    fails, done = [], 0
    chk = (("ovs_pct", res["ovs_pct"], "過衝", "%"), ("uns_pct", res["uns_pct"], "欠衝", "%"),
           ("ripple_pct", res["rip_pct"], "漣波", "%"))
    for k, v, lab, u in chk:
        lv = lim.get(k)
        if lv is None or v is None:
            continue
        done += 1
        if v > lv:
            fails.append(f"{lab} {v:.2f}{u} > {lv:g}{u}")
    lm = lim.get("ripple_mv")
    if lm is not None and res.get("rip_pp") is not None:
        done += 1
        if res["rip_pp"] * 1e3 > lm:
            fails.append(f"漣波 {res['rip_pp'] * 1e3:.1f} mV > {lm:g} mV")
    if fails:
        return "FAIL", "；".join(fails)
    if not done:
        return "N/A", "待填（無限值）"
    return "PASS", ""


def _ratio(x):
    """離限值多近（>1 = 超過）；沒有限值 → 用漣波 %/2 當排序。給「最差」標記用。"""
    if x.get("status") != "ok":
        return -1.0
    lim, rs = x["lim"], []
    for k, v in (("ovs_pct", x["ovs_pct"]), ("uns_pct", x["uns_pct"]), ("ripple_pct", x["rip_pct"])):
        if lim.get(k) and v is not None:
            rs.append(v / lim[k])
    if lim.get("ripple_mv") and x.get("rip_pp") is not None:
        rs.append(x["rip_pp"] * 1e3 / lim["ripple_mv"])
    return max(rs) if rs else (x["rip_pct"] or 0.0) / 2.0 / 100.0


def analyze(waves, rails, cfg=None):
    """waves: {(model, ch): (t, y)}；rails: sequencing 的 rail 列（model, ch, name, kind, on, nom）。
    → 列（VIN, VBUS, IBUS, …；依 rails.sort_rails），邏輯訊號不列。"""
    cfg = dict(default_cfg(), **(cfg or {}))
    rows = []
    for r in rails or []:
        if not r.get("on", True):
            continue
        k = r.get("kind") or rail_kind(r.get("name"))
        if k == "logic":
            continue
        w = (waves or {}).get((r["model"], int(r["ch"])))
        if w is None:
            continue
        nom = r.get("nom")
        res = measure(w[0], w[1], nom=nom, kind=k, settle_s=float(cfg["settle_ms"]) * 1e-3,
                      bw_hz=float(cfg.get("bw_hz") or 0.0), band_pct=float(cfg.get("band_pct") or 1.0))
        if k == "current":
            lim, src, srcs = {kk: None for kk in LIMIT_KEYS}, "待填（電流只記錄）", set()
            vd, why = ("N/A", "電流（IBUS）只記錄，不判") if res["status"] == "ok" else judge(res, lim)
            counts = False
        else:
            lim, src, srcs = limits_for(r["name"], cfg)
            vd, why = judge(res, lim)
            counts = vd in ("PASS", "FAIL") and (srcs != {SRC_DEFAULT} or bool(cfg.get("include_default")))
        row = dict(res, model=r["model"], ch=int(r["ch"]), name=r["name"], role=ROLE.get(r["model"], ""), kind=k,
                   unit="A" if k == "current" else "V", nom=nom, lim=lim, src=src, verdict=vd, why=why,
                   counts=counts, bw_hz=float(cfg.get("bw_hz") or 0.0), settle_ms=float(cfg["settle_ms"]))
        rows.append(row)
    rows = sort_rails(rows)
    ok = [x for x in rows if x["status"] == "ok" and x["kind"] != "current"]
    if ok:
        w = max(ok, key=_ratio)
        w["worst"] = True
    return rows


def worst(rows):
    for x in rows or []:
        if x.get("worst"):
            return x
    return None


def overall(rows):
    """只看計入總判定的列 → "PASS"／"FAIL"／""（沒有任何計入）。"""
    c = [x for x in rows or [] if x.get("counts")]
    if any(x["verdict"] == "FAIL" for x in c):
        return "FAIL"
    return "PASS" if c else ""


def summary_text(rows):
    """一行摘要：判定統計 ＋ 最差 rail。"""
    ev = [x for x in rows or [] if x["verdict"] in ("PASS", "FAIL")]
    if not rows:
        return "尚無突波／雜訊資料"
    nf = sum(1 for x in ev if x["verdict"] == "FAIL")
    s = f"PASS {len(ev) - nf}　FAIL {nf}　N/A {len(rows) - len(ev)}"
    w = worst(rows)
    if w:
        s += f"　最差：{w['name']}（過衝 {_f(w['ovs_pct'], 2)}%　欠衝 {_f(w['uns_pct'], 2)}%　漣波 {_f(w['rip_pct'], 2)}%）"
    return s


def _f(v, nd=3):
    return "" if v is None else f"{v:.{nd}f}"


def _g(v, nd=5):
    return "" if v is None else f"{v:.{nd}g}"


def value_text(x):
    """⑨ 實測欄的一行文字。"""
    if x["status"] != "ok":
        return x["status"]
    return f"過衝 {_f(x['ovs_pct'], 2)}%／欠衝 {_f(x['uns_pct'], 2)}%／漣波 {x['rip_pp'] * 1e3:.3g} m{x['unit']}pp"


def table_values(x):
    """右欄表格一列。"""
    mv = lambda v: "" if v is None else f"{v * 1e3:.3g}"  # noqa: E731
    if x["status"] != "ok":
        return (x["name"], "", "", "", "", "", "", "", "", "無穩態", "N/A", x["src"])
    return (("★ " if x.get("worst") else "") + x["name"], _g(x["vss"], 5), mv(x["ovs_v"]), _f(x["ovs_pct"], 2),
            mv(x["uns_v"]), _f(x["uns_pct"], 2), mv(x["rip_pp"]), _f(x["rip_pct"], 2), mv(x["rms"]),
            limit_text(x["lim"]) if x["kind"] != "current" else "電流只記錄", x["verdict"], x["src"])


# ---------------------------------------------------------------------------
# 輸出：CSV（每次擷取）、summary.csv 欄位、圖
# ---------------------------------------------------------------------------
def save_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEAD_ZH)
        for x in rows:
            ok = x["status"] == "ok"
            w.writerow([x["model"], x["ch"], x["name"], {"rail": "電源", "current": "電流 A"}.get(x["kind"], x["kind"]),
                        _g(x["vss"]), _g(x["ovs_v"]), _f(x["ovs_pct"], 3), _g(x["uns_v"]), _f(x["uns_pct"], 3),
                        _g(x["rip_pp"]), _f(x["rip_pct"], 3), _g(x["rms"]), x["unit"],
                        "" if x["t_a"] is None else f"{x['t_a'] * 1e3:.4f}", "" if x["t_b"] is None else f"{x['t_b'] * 1e3:.4f}",
                        x["where"], "關閉" if not x["bw_hz"] else f"{x['bw_hz'] / 1e6:g} MHz",
                        _g(x["lim"].get("ovs_pct")), _g(x["lim"].get("uns_pct")), _g(x["lim"].get("ripple_pct")),
                        _g(x["lim"].get("ripple_mv")), x["src"], x["verdict"],
                        ("" if ok else x["status"]) or x["why"]])
    return path


def summary_cols(rows):
    """summary.csv：每條 rail 三欄（過衝 %、欠衝 %、漣波 Vpp）＋ 一欄判定。{欄名: 值}"""
    d = {"突波雜訊判定": ""}
    for x in sort_rails(rows or []):
        p = f"{x['role']}{x['name']}"
        d[f"{p} 過衝 %"] = _f(x["ovs_pct"], 3)
        d[f"{p} 欠衝 %"] = _f(x["uns_pct"], 3)
        d[f"{p} 漣波 Vpp {x['unit']}"] = _g(x["rip_pp"])
    ev = [x for x in rows or [] if x["verdict"] in ("PASS", "FAIL")]
    if rows:
        d["突波雜訊判定"] = ("FAIL" if any(x["verdict"] == "FAIL" for x in ev) else "PASS" if ev else "N/A")
    return d


def summary_col_names(rows):
    names = []
    for x in sort_rails(rows or []):
        p = f"{x['role']}{x['name']}"
        names += [f"{p} 過衝 %", f"{p} 欠衝 %", f"{p} 漣波 Vpp {x['unit']}"]
    return names


VCOL = {"PASS": "#1a7f37", "FAIL": "#cf222e", "N/A": "#656d76"}


def draw_rail(ax, x, waves, fam=None):
    """單一 rail：原始波形（淺灰）＋ 穩態窗口（淡藍）＋ Vss 線＋ 過衝▲／欠衝▼ 標記。"""
    w = (waves or {}).get((x["model"], x["ch"]))
    if w is None:
        ax.text(0.5, 0.5, "無波形", ha="center", va="center", transform=ax.transAxes, family=fam)
        return
    t, y = np.asarray(w[0], float), np.asarray(w[1], float)
    tm = t * 1e3
    ax.plot(tm, y, color="#b8c0cc", lw=0.6)
    if x["bw_hz"]:
        ax.plot(tm, bw_filter(y, float(np.median(np.diff(t))), x["bw_hz"]), color="#0E9BD8", lw=0.9)
    if x["status"] == "ok":
        ax.axvspan(x["t_a"] * 1e3, x["t_b"] * 1e3, color="#0E9BD8", alpha=0.10, lw=0)
        ax.axhline(x["vss"], color="#0E9BD8", lw=0.8, ls="--")
        yf = bw_filter(y, float(np.median(np.diff(t))), x["bw_hz"])
        if x["t_ovs"] is not None:
            i = int(np.argmin(np.abs(t - x["t_ovs"])))
            ax.plot([tm[i]], [yf[i]], "^", color="#cf222e", ms=6)
        if x["t_uns"] is not None:
            i = int(np.argmin(np.abs(t - x["t_uns"])))
            ax.plot([tm[i]], [yf[i]], "v", color="#0550ae", ms=6)
        m = max(x["ovs_v"], x["uns_v"], x["rip_pp"]) * 1.5
        m = max(m, 0.004 * abs(x["vss"]), 1e-6)
        ax.set_ylim(x["vss"] - m, x["vss"] + m)           # Y 軸放大到穩態附近（上電爬升段超出畫面）
    col = VCOL.get(x["verdict"], "#656d76")
    tag = ("★最差 " if x.get("worst") else "") + f"{x['role']} {x['name']}　{x['verdict']}"
    sub = value_text(x)
    ax.set_title(f"{tag}\n{sub}", fontsize=8, color=col, family=fam, loc="left")
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.25)


def _cjk():
    try:
        from matplotlib import font_manager as fm
        have = {f.name for f in fm.fontManager.ttflist}
        for n in ("Microsoft JhengHei", "Noto Sans CJK TC", "Noto Sans CJK JP", "WenQuanYi Zen Hei", "Noto Sans TC",
                  "PingFang TC", "Microsoft YaHei", "SimHei"):
            if n in have:
                return n
    except Exception:  # noqa: BLE001
        pass
    return None


def draw_grid(fig, rows, waves, title=""):
    fam = _cjk()
    rs = [x for x in rows if x["kind"] != "current"] + [x for x in rows if x["kind"] == "current"]
    rs = [x for x in rs if (x["model"], x["ch"]) in (waves or {})]
    n = len(rs)
    if not n:
        return
    cols = 3 if n > 4 else 2
    nr = (n + cols - 1) // cols
    axs = fig.subplots(nr, cols, squeeze=False)
    for i, x in enumerate(rs):
        draw_rail(axs[i // cols][i % cols], x, waves, fam)
        if i // cols == nr - 1:
            axs[i // cols][i % cols].set_xlabel("時間 (ms)，t = 0 = 主觸發", fontsize=7, family=fam)
    for j in range(n, nr * cols):
        axs[j // cols][j % cols].axis("off")
    fig.suptitle(title or "突波／雜訊（淡藍 = 穩態窗口、虛線 = Vss、▲ 過衝、▼ 欠衝、★ 最差）", fontsize=10, family=fam,
                 color="#0E9BD8")
    fig.subplots_adjust(left=0.05, right=0.99, top=0.88, bottom=0.07, hspace=0.75, wspace=0.14)


def save_png(rows, waves, path, title=""):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    n = len([x for x in rows if (x["model"], x["ch"]) in (waves or {})])
    fig = Figure(figsize=(14, max(3.0, 2.3 * ((n + 2) // 3) + 0.8)), dpi=100, facecolor="#FFFFFF")
    FigureCanvasAgg(fig)
    draw_grid(fig, rows, waves, title)
    fig.savefig(path)
    return path
