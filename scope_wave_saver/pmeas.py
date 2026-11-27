# -*- coding: utf-8 -*-
"""量測 P1–P24（示波器 ⇄ 本機）。照 All in Test tool u2core/pmtable.py（parse / setup / readback / read / local）：

每列 {on, src1, src2, level, param}；param：MAX MIN MEAN PKPK RISE FALL WID FREQ DELAY DTLEV W_MEAN W_MAX W_MIN
  示波器：VBS app.Measure.Pn.View / ParamEngine / Source1 / Source2；DTLEV 再送 Operator.LevelType/PercentLevel/Slope；
          V×I（W_*）→ 數學通道 Fk = Product(前 CH, 後 CH)，Pn 量 Fk；最後 app.Measure.ClearSweeps；送完讀回比對
  上限（同 u2core/instruments.MEAS_MAX）：8208HD 示波器 P1–P12、HDO6104B P1–P8；超過的列只在 PC 端用同一筆波形計算
  （工具的做法：「P1–P12 由示波器量測並與程式計算比對，P13–P24 由程式計算」）
  每次存波形：讀 Pn.out.Result.Value（示波器值）＋ 本機值 → 差值 / 差異 %（容許 1 %，工具預設）
"""
from __future__ import annotations

import math
import re

import numpy as np

NP = 24
# 示波器上限（已核對）：
#   WaveRunner 8000HD datasheet：「Display up to 12 measurement parameters」「up to 12 math functions traces (F1-F12)」
#   HDO6104B datasheet（Autotest_tool DesignSpec_MD）：「Display up to 8 measurement parameters」「up to 8 math functions traces (F1-F8)」
#   = u2core/instruments.MEAS_MAX / MATH_MAX（6104B = 8；其他 12）
INST_MAX = {"8208HD": 12, "6104B": 8}
MATH_MAX = {"8208HD": 12, "6104B": 8}
# 兩台 datasheet 都列的參數（Maximum / Minimum / Mean / Peak-to-Peak / Rise / Fall / Width / Frequency / Delay / Δ Time@level；
# V×I 用 Math Product）。Autotest_tool 已在 WR8208HD 實機確認：Rise、Fall、Max、Min、Mean、PkPk、Delay；其餘依手冊（未實機確認）
SUPPORTED = {"8208HD": set(PARAMS_ALL := ["MAX", "MIN", "MEAN", "PKPK", "RISE", "FALL", "WID", "FREQ", "DELAY", "DTLEV",
                                          "W_MEAN", "W_MAX", "W_MIN"]),
             "6104B": set(PARAMS_ALL)}
HW_VERIFIED = {"8208HD": {"MAX", "MIN", "MEAN", "PKPK", "RISE", "FALL", "DELAY"}, "6104B": set()}
PARAMS = ["MAX", "MIN", "MEAN", "PKPK", "RISE", "FALL", "WID", "FREQ", "DELAY", "DTLEV", "W_MEAN", "W_MAX", "W_MIN"]
ENGINE = {"MAX": "Maximum", "MIN": "Minimum", "MEAN": "Mean", "PKPK": "PeakToPeak", "RISE": "Rise", "FALL": "Fall",
          "WID": "Width", "FREQ": "Frequency", "DELAY": "Delay", "DTLEV": "DeltaTimeAtLevel",
          "W_MEAN": "Mean", "W_MAX": "Maximum", "W_MIN": "Minimum"}
UNIT = {"MAX": "V", "MIN": "V", "MEAN": "V", "PKPK": "V", "FREQ": "Hz", "W_MEAN": "W", "W_MAX": "W", "W_MIN": "W"}
POWER = ("W_MEAN", "W_MAX", "W_MIN")
DEF_LEVEL = {"RISE": "10,90", "FALL": "90,10", "DTLEV": "90,90"}
TOL_PCT = 1.0
NONE = "—"
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SL = {"POS": "Pos", "RISE": "Pos", "UP": "Pos", "NEG": "Neg", "FALL": "Neg", "DOWN": "Neg", "BOTH": "Both", "EITHER": "Both"}


def inst_max(model):
    return INST_MAX.get(model, 12)


def default_rows(chs, n=NP):
    """沒設定過 → 每個通道 MAX，再每個通道 MIN（pmtable.default_fill，工具預設）；不足 n 列補空白"""
    rows = [{"on": True, "src1": c, "src2": NONE, "level": "", "param": p} for p in ("MAX", "MIN") for c in chs][:n]
    rows += [{"on": False, "src1": "C1", "src2": NONE, "level": "", "param": "MAX"} for _ in range(n - len(rows))]
    return rows


def ch_of(s):
    m = re.match(r"\s*C(\d+)", str(s or ""))
    return int(m.group(1)) if m else None


def level_parts(row):
    txt = str(row.get("level") or "").strip() or DEF_LEVEL.get(str(row.get("param") or "").upper(), "50,50")
    parts = [x for x in re.split(r"[,/ ]+", txt) if x]
    try:
        nums = [float(x.rstrip("%")) for x in parts[:2]]
        if len(nums) == 1:
            nums *= 2
        a, b = nums[:2]
    except (ValueError, IndexError):
        raise ValueError(f"準位 % 格式錯誤：{txt}（例 90,90 或 90,90,pos,neg）")
    sl = []
    for x in parts[2:4]:
        k = _SL.get(x.strip().upper())
        if k is None:
            raise ValueError(f"準位斜率 {x} 無法辨識（pos / neg / both）")
        sl.append(k)
    sl += [None] * (2 - len(sl))
    return a, b, sl[0], sl[1]


def parse(rows, model="8208HD", nch=8):
    """列 → 啟用的 P [{p, ch1, ch2, param, lv1, lv2, sl1, sl2, label, where, why}]
    where = "scope"（寫到示波器 Pn）/ "pc"（PC 用同一筆波形計算）；why = 為什麼改 PC 計算"""
    im = inst_max(model)
    fmax = MATH_MAX.get(model, 12)
    pairs = []
    out = []
    for i, r in enumerate(rows or [], 1):
        if i > NP or not r.get("on"):
            continue
        prm = str(r.get("param") or "").upper()
        if prm not in PARAMS:
            raise ValueError(f"P{i}：參數 {prm} 不支援")
        c1, c2 = ch_of(r.get("src1")), ch_of(r.get("src2"))
        if c1 is None or c1 > nch:
            raise ValueError(f"P{i}：前 CH {r.get('src1')} 不在 C1–C{nch}")
        if prm == "DTLEV" and c2 is None:
            raise ValueError(f"P{i}：DTLEV 需要後 CH")
        if prm in POWER and (c2 is None or c2 == c1):
            raise ValueError(f"P{i}：{prm}（V×I）前 CH 選電壓、後 CH 選電流")
        if c2 is not None and c2 > nch:
            raise ValueError(f"P{i}：後 CH C{c2} 不在 C1–C{nch}")
        lv1, lv2, s1, s2 = level_parts(r) if prm in ("RISE", "FALL", "DTLEV") else (50.0, 50.0, None, None)
        lab = f"P{i} {prm} C{c1}" + (f"→C{c2} {lv1:g}/{lv2:g}%" if prm == "DTLEV" else f"×C{c2}" if prm in POWER else
                                     f" {lv1:g}-{lv2:g}%" if prm in ("RISE", "FALL") else "")
        where, why = "scope", ""
        if i > im:
            where, why = "pc", f"{model} 示波器只有 P1–P{im}"
        elif prm not in SUPPORTED.get(model, set()):
            where, why = "pc", f"{model} 不支援 {prm}"
        elif prm in POWER:
            key = (c1, c2)
            if key not in pairs:
                if len(pairs) >= fmax:
                    where, why = "pc", f"{model} 數學通道只有 F1–F{fmax}"
                else:
                    pairs.append(key)
        out.append({"p": i, "ch1": c1, "ch2": c2 if prm in ("DTLEV",) + POWER else None, "param": prm,
                    "lv1": lv1, "lv2": lv2, "sl1": s1, "sl2": s2, "label": lab, "where": where, "why": why,
                    "hw_verified": prm in HW_VERIFIED.get(model, set())})
    return out


def warnings(slots):
    """寫入前的提醒（不送無效指令）：改 PC 計算的列、未實機確認的參數"""
    groups = {}
    for sl in slots:
        if sl["where"] == "pc" and sl.get("why"):
            groups.setdefault(sl["why"], []).append(sl["p"])
    w = [f"P{f'{ps[0]}–P{ps[-1]}' if len(ps) >= 3 and ps[-1] - ps[0] == len(ps) - 1 else ',P'.join(map(str, ps))}：{why} → PC 計算（不送示波器）"
         for why, ps in groups.items()]
    nv = sorted({sl["param"] for sl in slots if sl["where"] == "scope" and not sl.get("hw_verified")})
    if nv:
        w.append("未實機確認（依手冊，寫入後讀回比對）：" + "、".join(nv))
    return w


# ---------------------------------------------------------------- 本機計算（pmtable.local）
def _bt(y):
    return float(np.percentile(y, 2)), float(np.percentile(y, 98))


def _cross(t, y, lv, rising, start=0):
    if rising:
        idx = np.nonzero((y[start:-1] < lv) & (y[start + 1:] >= lv))[0]
    else:
        idx = np.nonzero((y[start:-1] > lv) & (y[start + 1:] <= lv))[0]
    if not len(idx):
        return None
    i = int(idx[0]) + start
    y0, y1 = y[i], y[i + 1]
    return float(t[i] + (lv - y0) * (t[i + 1] - t[i]) / (y1 - y0)) if y1 != y0 else float(t[i])


def _rising(y):
    n = max(3, len(y) // 20)
    return float(np.mean(y[-n:])) >= float(np.mean(y[:n]))


def local(sl, waves):
    """waves: {ch: (t, y)} → 數值 / None"""
    w1 = waves.get(sl["ch1"])
    if w1 is None:
        return None
    t, y = np.asarray(w1[0], float), np.asarray(w1[1], float)
    if len(y) < 3:
        return None
    p = sl["param"]
    if p in POWER:
        w2 = waves.get(sl.get("ch2"))
        if w2 is None:
            return None
        t2, y2 = np.asarray(w2[0], float), np.asarray(w2[1], float)
        if len(t2) != len(t) or not np.allclose(t2, t):
            y2 = np.interp(t, t2, y2)
        pw = y * y2
        return float({"W_MEAN": pw.mean, "W_MAX": pw.max, "W_MIN": pw.min}[p]())
    if p == "MAX":
        return float(y.max())
    if p == "MIN":
        return float(y.min())
    if p == "MEAN":
        return float(y.mean())
    if p == "PKPK":
        return float(y.max() - y.min())
    lo, hi = _bt(y)
    if hi - lo <= 0:
        return None

    def lvl(pct, b=None):
        bb = b or (lo, hi)
        return bb[0] + (bb[1] - bb[0]) * pct / 100.0
    if p in ("RISE", "FALL"):
        a, b = sorted((sl["lv1"], sl["lv2"]))
        up = p == "RISE"
        ta = _cross(t, y, lvl(a if up else b), up)
        if ta is None:
            return None
        tb = _cross(t, y, lvl(b if up else a), up, max(0, int(np.searchsorted(t, ta)) - 1))
        return None if tb is None else tb - ta
    if p == "WID":
        ta = _cross(t, y, lvl(50), True)
        if ta is None:
            return None
        tb = _cross(t, y, lvl(50), False, max(0, int(np.searchsorted(t, ta)) - 1))
        return None if tb is None else tb - ta
    if p == "FREQ":
        mid = lvl(50)
        idx = np.nonzero((y[:-1] < mid) & (y[1:] >= mid))[0]
        if len(idx) < 2:
            return None
        per = (t[idx[-1]] - t[idx[0]]) / (len(idx) - 1)
        return 1.0 / per if per > 0 else None
    if p == "DELAY":
        return _cross(t, y, lvl(50), _rising(y))
    if p == "DTLEV":
        w2 = waves.get(sl["ch2"])
        if w2 is None:
            return None
        t2, y2 = np.asarray(w2[0], float), np.asarray(w2[1], float)
        d1 = _rising(y) if sl.get("sl1") in (None, "Both") else sl["sl1"] == "Pos"
        d2 = _rising(y2) if sl.get("sl2") in (None, "Both") else sl["sl2"] == "Pos"
        ta = _cross(t, y, lvl(sl["lv1"]), d1)
        tb = _cross(t2, y2, lvl(sl["lv2"], b=_bt(y2)), d2)
        return None if ta is None or tb is None else tb - ta
    return None


# ---------------------------------------------------------------- 示波器（pmtable.setup / readback / read）
def dtlev_ops(sl):
    op = {}
    for n, (lv, s_) in enumerate(((sl["lv1"], sl.get("sl1")), (sl["lv2"], sl.get("sl2"))), 1):
        op[f"LevelType{n}"] = "Percent"
        op[f"PercentLevel{n}"] = f"{float(lv):g}"
        op[f"Slope{n}"] = s_ or "Both"
    return op


def math_map(slots, im):
    out = {}
    for sl in slots or []:
        if sl["param"] in POWER and sl["p"] <= im:
            key = (sl["ch1"], sl["ch2"])
            if key not in out:
                out[key] = f"F{len(out) + 1}"
    return out


def setup_cmds(slots, im):
    """→ [VBS 指令]（示波器 P1–P{im}；超過上限的列不送）"""
    cmds = []
    slots = [sl for sl in slots if sl.get("where", "scope") == "scope"]
    on = {sl["p"]: sl for sl in slots if sl["p"] <= im}
    fm = math_map(slots, im)
    for (c1, c2), fk in fm.items():
        cmds += [f"VBS 'app.Math.{fk}.MathMode = \"OneOperator\"'", f"VBS 'app.Math.{fk}.Operator1 = \"Product\"'",
                 f"VBS 'app.Math.{fk}.Source1 = \"C{c1}\"'", f"VBS 'app.Math.{fk}.Source2 = \"C{c2}\"'",
                 f"VBS 'app.Math.{fk}.View = True'"]
    cmds.append("VBS 'app.Measure.ShowMeasure = True'")
    for i in range(1, im + 1):
        sl = on.get(i)
        cmds.append(f"VBS 'app.Measure.P{i}.View = {'True' if sl else 'False'}'")
        if not sl:
            continue
        cmds.append(f'VBS \'app.Measure.P{i}.ParamEngine = "{ENGINE[sl["param"]]}"\'')
        if sl["param"] in POWER:
            cmds.append(f'VBS \'app.Measure.P{i}.Source1 = "{fm[(sl["ch1"], sl["ch2"])]}"\'')
            continue
        cmds.append(f'VBS \'app.Measure.P{i}.Source1 = "C{sl["ch1"]}"\'')
        if sl.get("ch2"):
            cmds.append(f'VBS \'app.Measure.P{i}.Source2 = "C{sl["ch2"]}"\'')
        if sl["param"] == "DTLEV":
            for k, v in dtlev_ops(sl).items():
                vv = f'"{v}"' if k.startswith(("LevelType", "Slope")) else v
                cmds.append(f"VBS 'app.Measure.P{i}.Operator.{k} = {vv}'")
    cmds.append("VBS 'app.Measure.ClearSweeps'")
    return cmds


def setup(write, query, slots, im, log=print):
    for w_ in warnings(slots):
        if log:
            log("⚠ " + w_)
    slots_sc = [sl for sl in slots if sl.get("where", "scope") == "scope"]
    for c in setup_cmds(slots_sc, im):
        write(c)
    bad = readback(query, slots_sc, im) if query else []
    if log:
        n_sc = len(slots_sc)
        log(f"量測 P：示波器 {n_sc} 個（上限 P1–P{im}）、PC 計算 {len(slots) - n_sc} 個"
            + (f"；⚠ 讀回不符：{'；'.join(bad)}" if bad else "；讀回一致"))
    return bad


def _vq(query, expr):
    r = str(query(f"VBS? 'return={expr}'")).strip()
    return (r[3:].strip() if r.upper().startswith("VBS") else r).strip('"')


def readback(query, slots, im):
    bad = []
    fm = math_map(slots, im)
    for (c1, c2), fk in fm.items():
        try:
            for k, v in (("Operator1", "Product"), ("Source1", f"C{c1}"), ("Source2", f"C{c2}")):
                got = _vq(query, f"app.Math.{fk}.{k}")
                if got.upper() != v.upper():
                    bad.append(f"{fk} {k}：要 {v} / 示波器 {got or '?'}")
        except Exception as e:  # noqa: BLE001
            bad.append(f"{fk} 讀回失敗：{e}")
    for sl in slots:
        i = sl["p"]
        if i > im:
            continue
        try:
            want = {"ParamEngine": ENGINE[sl["param"]], "Source1": f"C{sl['ch1']}"}
            if sl["param"] in POWER:
                want["Source1"] = fm[(sl["ch1"], sl["ch2"])]
            elif sl.get("ch2"):
                want["Source2"] = f"C{sl['ch2']}"
            if sl["param"] == "DTLEV":
                want.update({f"Operator.{k}": v for k, v in dtlev_ops(sl).items()})
            for k, v in want.items():
                got = _vq(query, f"app.Measure.P{i}.{k}")
                a = _NUM.search(got) if k.startswith("Operator.Percent") else None
                ok = (abs(float(a.group()) - float(v)) < 1e-6) if a else got.upper() == str(v).upper()
                if not ok:
                    bad.append(f"P{i} {k}：要 {v} / 示波器 {got or '?'}")
        except Exception as e:  # noqa: BLE001
            bad.append(f"P{i} 讀回失敗：{e}")
    return bad


def read(query, slots, im):
    """→ {p: 示波器值 / None}"""
    out = {}
    for sl in slots:
        if sl["p"] > im or sl.get("where") == "pc":
            out[sl["p"]] = None
            continue
        try:
            r = str(query(f"VBS? 'return=app.Measure.P{sl['p']}.out.Result.Value'"))
            r = r[4:] if r.upper().startswith("VBS ") else r
            m = _NUM.search(r)
            v = float(m.group()) if m else None
            if v is not None and (not math.isfinite(v) or abs(v) > 1e30):
                v = None
            out[sl["p"]] = v
        except Exception:  # noqa: BLE001
            out[sl["p"]] = None
    return out


_REV = {v.upper(): k for k, v in ENGINE.items() if k not in POWER}


def read_setup(query, model):
    """讀回示波器 P1–P{上限} → 列 [{on, src1, src2, level, param, engine}]（pmtable.read_setup；P 上限之後補空白列）"""
    im = inst_max(model)
    rows = []
    for i in range(1, im + 1):
        try:
            view = _vq(query, f"app.Measure.P{i}.View").lower() in ("-1", "1", "true")
            eng = _vq(query, f"app.Measure.P{i}.ParamEngine")
            prm = _REV.get(eng.upper())
            s1 = _vq(query, f"app.Measure.P{i}.Source1")
            row = {"on": bool(view and prm), "src1": s1 if re.match(r"C\d", s1) else "C1", "src2": NONE,
                   "level": "", "param": prm or "MAX", "engine": eng}
            if prm == "DTLEV":
                s2 = _vq(query, f"app.Measure.P{i}.Source2")
                row["src2"] = s2 if re.match(r"C\d", s2) else NONE
                lv = [_NUM.search(_vq(query, f"app.Measure.P{i}.Operator.PercentLevel{n}")) for n in (1, 2)]
                if all(lv):
                    row["level"] = ",".join(f"{float(m.group()):g}" for m in lv)
            if re.match(r"F\d", s1) and prm in ("MEAN", "MAX", "MIN"):
                op = _vq(query, f"app.Math.{s1}.Operator1")
                a, b = _vq(query, f"app.Math.{s1}.Source1"), _vq(query, f"app.Math.{s1}.Source2")
                if op.upper() == "PRODUCT" and re.match(r"C\d", a) and re.match(r"C\d", b):
                    row.update(on=bool(view), src1=a, src2=b, param="W_" + prm)
            rows.append(row)
        except Exception:  # noqa: BLE001
            rows.append({"on": False, "src1": "C1", "src2": NONE, "level": "", "param": "MAX", "engine": "?"})
    return rows


def judge(local_v, inst_v, tol=TOL_PCT):
    if local_v is None or inst_v is None:
        return None, None, None
    d = local_v - inst_v
    pct = abs(d) / abs(inst_v) * 100.0 if inst_v else (0.0 if d == 0 else float("inf"))
    return d, pct, pct <= tol


def measure(slots, waves, inst=None, tol=TOL_PCT):
    rows = []
    for sl in slots or []:
        lv, iv = local(sl, waves), (inst or {}).get(sl["p"])
        d, pct, ok = judge(lv, iv, tol)
        rows.append({"p": sl["p"], "label": sl["label"], "param": sl["param"], "unit": UNIT.get(sl["param"], "s"),
                     "where": sl["where"], "scope": iv, "pc": lv, "diff": d, "pct": pct,
                     "ok": "" if ok is None else ("一致" if ok else "不一致")})
    return rows
