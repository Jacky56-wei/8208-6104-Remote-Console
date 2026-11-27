# -*- coding: utf-8 -*-
"""上 / 下電時序 ＋ slew rate（PC 端計算，用存下來的波形資料）

兩台的波形時間軸都是「觸發相對」（t = 0 = 觸發點；WAVEDESC HORIZ_OFFSET + i·HORIZ_INTERVAL）。
輔 6104B 由主 8208HD 的 AUX OUT（Trigger Out）觸發 → 兩台 t = 0 是同一個事件（BNC + Trigger Out 延遲約數十 ns，
可用 slave_skew_s 修正），所以可以直接放在同一條時間軸比較。

方向：direction = "up"（上電）/ "down"（下電）/ "auto"（各 rail 比較波形頭尾 5 % 的平均，多數決）。
每條電源（rail）：
  · 低門檻 lo%（預設 10 %）、高門檻 hi%（預設 90 %）× 標稱電壓 → 第一次穿越時間 t_lo、t_hi
      上電 = 往上穿越；下電 = 往下穿越（先過 90 % 再過 10 %）
  · 上升 / 下降時間 = |t_hi − t_lo|；slew rate = (V_hi − V_lo) / (t_hi − t_lo)，V/ms（下電為負值）
  · 邏輯訊號（RSTn、BUSPG）：上 / 下電時間用單一高準位門檻 V（預設 0.7 × 標稱）；slew 一樣用 10 / 90 %
  · 電流（IBUS）：上電 → 峰值、峰值時間、slew = 10 → 90 % 峰值（A/ms）；下電 → 起始電流 90 → 10 %（A/ms）。不排序
  · 「上 / 下電時間」= 高門檻穿越（basis="hi"，預設；上電 = power good，下電 = 掉出 power good）或低門檻（basis="lo"）
  · 順序 = 依上 / 下電時間排序；延遲 = 該時間 − 參考 rail（預設 VIN）的時間
  · 可選：每條 rail 的延遲下限 / 上限（ms），上電一組（min_ms / max_ms）、下電一組（dn_min_ms / dn_max_ms）→ PASS / FAIL
"""
from __future__ import annotations

import csv
import math

import numpy as np

from .rails import LOGIC, MASTER_DEF, NAMES, SLAVE_DEF, UNIT_A, VNOM, kind

ROLE = {MASTER_DEF: "主", SLAVE_DEF: "輔"}


def default_rails():
    """→ [{model, ch, name, kind, on, nom, lo_pct, hi_pct, logic_v, min_ms, max_ms}]（輔在前：VIN 是預設參考）"""
    out = []
    for m in (SLAVE_DEF, MASTER_DEF):
        for i, nm in enumerate(NAMES[m]):
            nom = VNOM.get(nm, 0.0)
            out.append({"model": m, "ch": i + 1, "name": nm, "kind": kind(nm), "on": True, "nom": nom,
                        "lo_pct": 10.0, "hi_pct": 90.0, "logic_v": round(0.7 * nom, 3) if nm in LOGIC else None,
                        "min_ms": None, "max_ms": None, "dn_min_ms": None, "dn_max_ms": None})
    return out


def _smooth(y, n=5):
    if len(y) < n * 2:
        return y
    k = np.ones(n) / n
    return np.convolve(y, k, mode="same")


def first_cross(t, y, thr, rising=True):
    """第一次穿越 thr 的時間（線性內插；rising = 由下往上，否則由上往下）
    一開始就在終點那側 → (None, "一開始就高 / 低於門檻")；沒穿越 → (None, "未到門檻")"""
    t = np.asarray(t, float)
    ys = _smooth(np.asarray(y, float))
    if len(ys) < 2:
        return None, "波形太短"
    side = ys >= thr if rising else ys <= thr
    if side[: max(3, len(ys) // 200)].all():
        return None, "一開始就" + ("高" if rising else "低") + "於門檻"
    idx = np.where(~side[:-1] & side[1:])[0]
    if not len(idx):
        return None, "未到門檻"
    i = int(idx[0])
    y0, y1 = ys[i], ys[i + 1]
    return (float(t[i] + (thr - y0) * (t[i + 1] - t[i]) / (y1 - y0)) if y1 != y0 else float(t[i])), ""


def first_rise(t, y, thr):
    return first_cross(t, y, thr, True)


def _ends(y):
    n = max(3, len(y) // 20)
    return float(np.mean(y[:n])), float(np.mean(y[-n:]))


def detect_direction(waves, rails=None):
    """各電壓 rail 頭尾平均比較 → "up" / "down"（多數決；沒得比 → "up"）"""
    up = down = 0
    for r in rails or default_rails():
        if r["kind"] == "current" or not r.get("on", True):
            continue
        w = waves.get((r["model"], int(r["ch"])))
        nom = float(r.get("nom") or 0.0)
        if w is None or nom <= 0:
            continue
        a, b = _ends(np.asarray(w[1], float))
        if b - a > 0.3 * nom:
            up += 1
        elif a - b > 0.3 * nom:
            down += 1
    return "down" if down > up else "up"


def _slew(v_lo, v_hi, t_lo, t_hi):
    if t_lo is None or t_hi is None or t_hi == t_lo:
        return None
    return (v_hi - v_lo) / ((t_hi - t_lo) * 1e3)          # V/ms（下電：t_lo > t_hi → 負值）


def analyze(waves, rails=None, ref="VIN", basis="hi", slave_skew_s=0.0, direction="auto"):
    """waves: {(model, ch): (t, y)} → rows（照上 / 下電時間排序；沒穿越的排最後）。每列帶 direction"""
    rails = rails or default_rails()
    d = detect_direction(waves, rails) if direction in (None, "", "auto") else direction
    up = d == "up"
    rows = []
    for r in rails:
        if not r.get("on", True):
            continue
        if not up:                                            # 下電用下電那組限制
            r = dict(r, min_ms=r.get("dn_min_ms"), max_ms=r.get("dn_max_ms"))
        w = waves.get((r["model"], int(r["ch"])))
        row = dict(r, role=ROLE.get(r["model"], ""), direction=d, t_lo=None, t_hi=None, rise=None, t_on=None,
                   delay=None, order=None, peak=None, t_peak=None, thr_lo=None, thr_hi=None, slew=None, note="",
                   verdict="")
        if w is None:
            row["note"] = "沒有波形"
            rows.append(row)
            continue
        t = np.asarray(w[0], float) + (slave_skew_s if r["model"] == SLAVE_DEF else 0.0)
        y = np.asarray(w[1], float)
        if r["kind"] == "current" or r["name"] in UNIT_A:
            if up:
                k = int(np.argmax(y))
                row.update(peak=float(y[k]), t_peak=float(t[k]), note="電流：峰值")
                ref_i = float(y[k])
            else:
                ref_i = _ends(y)[0]
                row["note"] = f"電流：起始 {ref_i:.3g} A"
            if ref_i > 0:
                lo_i, hi_i = ref_i * float(r["lo_pct"]) / 100.0, ref_i * float(r["hi_pct"]) / 100.0
                row["thr_lo"], row["thr_hi"] = lo_i, hi_i
                row["t_lo"], _n1 = first_cross(t, y, lo_i, up)
                row["t_hi"], _n2 = first_cross(t, y, hi_i, up)
                row["slew"] = _slew(lo_i, hi_i, row["t_lo"], row["t_hi"])       # A/ms
                if row["t_lo"] is not None and row["t_hi"] is not None:
                    row["rise"] = abs(row["t_hi"] - row["t_lo"])
            rows.append(row)
            continue
        nom = float(r.get("nom") or 0.0)
        if nom <= 0:
            row["note"] = "沒有標稱電壓"
            rows.append(row)
            continue
        row["thr_lo"], row["thr_hi"] = nom * float(r["lo_pct"]) / 100.0, nom * float(r["hi_pct"]) / 100.0
        row["t_lo"], n1 = first_cross(t, y, row["thr_lo"], up)
        row["t_hi"], n2 = first_cross(t, y, row["thr_hi"], up)
        if row["t_lo"] is not None and row["t_hi"] is not None:
            row["rise"] = abs(row["t_hi"] - row["t_lo"])
            row["slew"] = _slew(row["thr_lo"], row["thr_hi"], row["t_lo"], row["t_hi"])
        if r["kind"] == "logic":
            thr = float(r.get("logic_v") or 0.7 * nom)
            row["t_logic"], row["note"] = first_cross(t, y, thr, up)
            row["t_on"] = row["t_logic"]
        else:
            row["note"] = n1 or n2
            row["t_on"] = row["t_lo"] if basis == "lo" else row["t_hi"]
        rows.append(row)
    ref_row = next((x for x in rows if x["name"] == ref and x["t_on"] is not None), None)
    timed = sorted([x for x in rows if x["t_on"] is not None], key=lambda x: x["t_on"])
    for k, x in enumerate(timed, 1):
        x["order"] = k
        if ref_row is not None:
            x["delay"] = x["t_on"] - ref_row["t_on"]
            lo, hi = x.get("min_ms"), x.get("max_ms")
            if lo is not None or hi is not None:
                ms = x["delay"] * 1e3
                ok = (lo is None or ms >= lo) and (hi is None or ms <= hi)
                x["verdict"] = "PASS" if ok else "FAIL"
    for x in rows:
        if x["t_on"] is None and x["kind"] != "current" and (x.get("min_ms") is not None or x.get("max_ms") is not None):
            x["verdict"] = "FAIL"
    rest = [x for x in rows if x["t_on"] is None]
    return timed + rest


DIR_TXT = {"up": "上電", "down": "下電"}


def direction_of(rows):
    return rows[0]["direction"] if rows else "up"


def overall(rows):
    v = [x["verdict"] for x in rows if x.get("verdict")]
    return "" if not v else ("FAIL" if "FAIL" in v else "PASS")


def _ms(v, nd=3):
    return "" if v is None or (isinstance(v, float) and not math.isfinite(v)) else f"{v * 1e3:.{nd}f}"


COLS = ("方向", "順序", "示波器", "CH", "rail", "類型", "標稱", "門檻低", "門檻高", "t 低 ms", "t 高 ms", "上 / 下電時間 ms",
        "上升 / 下降 ms", "slew rate", "延遲 ms（對參考）", "下限 ms", "上限 ms", "判定", "峰值", "備註")


def slew_txt(x):
    if x.get("slew") is None:
        return ""
    return f"{x['slew']:+.4g} {'A' if x['kind'] == 'current' else 'V'}/ms"


def table(rows):
    """→ 顯示 / CSV 用的字串列"""
    out = []
    for x in rows:
        u = "A" if x["kind"] == "current" else "V"
        out.append([DIR_TXT.get(x.get("direction"), ""), x["order"] or "", f"{x['role']} {x['model']}", f"C{x['ch']}",
                    x["name"], {"rail": "電源", "logic": "邏輯", "current": "電流"}[x["kind"]],
                    f"{x['nom']:g}" if x.get("nom") else "",
                    "" if x["thr_lo"] is None else f"{x['thr_lo']:.4g} {u}", "" if x["thr_hi"] is None else f"{x['thr_hi']:.4g} {u}",
                    _ms(x["t_lo"]), _ms(x["t_hi"]), _ms(x["t_on"]), _ms(x["rise"]), slew_txt(x),
                    _ms(x["delay"]), "" if x.get("min_ms") is None else f"{x['min_ms']:g}",
                    "" if x.get("max_ms") is None else f"{x['max_ms']:g}", x.get("verdict", ""),
                    "" if x["peak"] is None else f"{x['peak']:.4g} A @ {_ms(x['t_peak'])} ms", x["note"]])
    return out


def save_csv(rows, path, ref="VIN", basis="hi"):
    d = DIR_TXT.get(direction_of(rows), "")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([f"# {d}時序；參考 rail = {ref}；{d}時間 = {'高門檻' if basis == 'hi' else '低門檻'}穿越（邏輯訊號 = 邏輯門檻）；"
                    "slew rate = 10 % ↔ 90 %（V/ms，電流 A/ms；下電為負）；時間 = 觸發相對（t = 0 = 主觸發，輔由 AUX OUT → EXT 觸發）"])
        w.writerow(COLS)
        w.writerows(table(rows))
    return path


def draw(fig, rows, waves, ref="VIN", fam=None, title=""):
    """時序圖：每條 rail 一列（波形正規化到 0–1 = 0–標稱），標 t 低 / t 高，紅線 = 觸發 t = 0，藍虛線 = 參考上電時間"""
    fam = fam or ["Noto Sans CJK TC", "Noto Sans CJK JP", "DejaVu Sans"]
    fig.clear()
    ax = fig.add_subplot(111)
    show = [x for x in rows]
    n = len(show)
    ref_t = next((x["t_on"] for x in rows if x["name"] == ref and x["t_on"] is not None), None)
    for k, x in enumerate(show):
        base = n - 1 - k
        w = waves.get((x["model"], int(x["ch"])))
        col = "#cf222e" if x.get("verdict") == "FAIL" else "#0E9BD8" if x["model"] == SLAVE_DEF else "#1a7f37"
        if w is not None:
            t, y = np.asarray(w[0], float), np.asarray(w[1], float)
            sc = (float(x["nom"]) if x.get("nom") else (float(np.max(np.abs(y))) or 1.0))
            yy = np.clip(y / sc, -0.1, 1.15) * 0.8 + base
            step = max(1, len(t) // 3000)
            ax.plot(t[::step] * 1e3, yy[::step], lw=1.1, color=col)
        if x["kind"] != "current":
            for key, mk in (("t_lo", "|"), ("t_hi", "o")):
                if x.get(key) is not None:
                    lv = (x["lo_pct"] if key == "t_lo" else x["hi_pct"]) / 100.0
                    ax.plot([x[key] * 1e3], [base + 0.8 * lv], mk, color=col, ms=5)
        lab = f"{x['role']} C{x['ch']} {x['name']}"
        ax.text(-0.01, base + 0.4, lab, transform=ax.get_yaxis_transform(), ha="right", va="center", fontsize=8,
                family=fam, color=col)
        sl = slew_txt(x)
        if x.get("delay") is not None:
            ax.text(1.005, base + 0.4, f"#{x['order']}  {x['delay'] * 1e3:+.3f} ms" + (f"  {x['verdict']}" if x.get("verdict") else "")
                    + (f"\n{sl}" if sl else ""),
                    transform=ax.get_yaxis_transform(), ha="left", va="center", fontsize=8, family=fam, color=col)
        elif x.get("peak") is not None or sl:
            ax.text(1.005, base + 0.4, (f"峰值 {x['peak']:.3g} A" if x.get("peak") is not None else "") + (f"\n{sl}" if sl else ""),
                    transform=ax.get_yaxis_transform(), ha="left", va="center", fontsize=8, family=fam, color=col)
    ax.axvline(0, color="#cf222e", lw=0.8)
    if ref_t is not None:
        ax.axvline(ref_t * 1e3, color="#0969da", lw=0.8, ls="--")
    ax.set_yticks([])
    ax.set_ylim(-0.3, n + 0.1)
    ax.grid(axis="x", alpha=0.3)
    ax.set_xlabel("時間 (ms)，t = 0 = 主觸發", family=fam, fontsize=9)
    dtx = DIR_TXT.get(direction_of(rows), "上電")
    ax.set_title(title or f"{dtx}時序（參考 {ref}，藍虛線 = 參考{dtx}時間；● = 高門檻、| = 低門檻；右側 = 延遲 / slew rate）",
                 family=fam, fontsize=10, color="#0E9BD8")
    fig.subplots_adjust(left=0.13, right=0.84, top=0.94, bottom=0.08)
    return fig


def save_png(rows, waves, path, ref="VIN", title=""):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(12, 7.5), dpi=110, facecolor="#FFFFFF")
    FigureCanvasAgg(fig)
    draw(fig, rows, waves, ref=ref, title=title)
    fig.savefig(path)
    return path
