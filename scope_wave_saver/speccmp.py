# -*- coding: utf-8 -*-
"""U.2／M.2 設計規格判比：跟隨 Autotest_tool（u2core.dutspec / powerprofile / designspec）。

優先從 U2_TOOL_DIR 的 u2core 匯入「機種」（電源設計表）清單、JSON 格式、rail 範圍與
designspec.compare 判定句。若該份 Autotest 還沒有這些模組（或 dut_power/ 不在工作樹），
改用本套隨附檔（specs/dut_power，與 Autotest origin/main 同內容）＋相同的 rng / compare 邏輯。

Autotest 對設計值原文是「不判正式 PASS/FAIL、只記錄」（設計範圍內／低於設計 min／高於設計 max）。
本工具把同一句對應到畫面與 summary.csv 的 PASS／FAIL／N/A，並標明「設計值，非正式規格」。
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED_DIR = os.path.join(HERE, "specs", "dut_power")
PUBLIC_PATH = os.path.join(HERE, "specs", "public_input.json")
TIMING_PATH = os.path.join(HERE, "specs", "timing_ref.json")

# iomap.canon 用得到的舊名（精簡自 Autotest_tool origin/main u2core/iomap.py ALIAS）
_ALIAS = {
    "SOC_CORE": "CORE", "SOC CORE": "CORE", "P_SOCVDD_0V8": "CORE",
    "DDR_VPP": "DDRVPP", "DDRVPP2V5": "DDRVPP", "DDR VPP": "DDRVPP",
    "P_1V8": "GPIO", "SOC_GPIO": "GPIO", "SOC GPIO": "GPIO", "P1V8": "GPIO",
    "DDR_IO": "DDRIO", "NAND_IO": "NFIO", "NFVCCQ1V2": "NFIO",
    "NFVCC": "NFCORE", "NAND_VPP": "NFCORE", "NFVCC2V5": "NFCORE",
    "SOC_PHYIO": "PHYIO", "SOC PHYIO": "PHYIO",
    "SOC_RSTn": "RSTn", "RESET": "RSTn", "RESET#": "RSTn",
    "U.2_VIN": "VIN", "U.2_VIN12": "VIN", "P_VIN_12V": "VIN", "VIN_12V": "VIN",
    "U2VIN": "VIN", "12VIN": "VIN", "PLP_12V": "VBUS", "BUS": "VBUS",
    "BUS_PG": "BUSPG", "I_12V": "IIN",
}
_TABLE = ["CORE", "DDRVPP", "GPIO", "DDRIO", "PHYIO", "NFIO", "NFCORE", "RSTn",
          "VIN", "VBUS", "BUSPG", "IBUS", "IIN"]
_NORM_ALIAS = None

# 畫面／CSV 欄位（與 Autotest powerprofile.HEAD 相同，另外加來源／實測／判定）
SPEC_CSV = ("規格", "電源設計表", "規格總判定", "規格明細")
NOTE = "設計值，非正式規格"
DEFAULT = "U2_12V_PowerIC_DesignSpec"

# 被綁定的實作（import 成功或 fallback）與來源說明
PP = None          # powerprofile 模組
DUT = None         # dutspec 模組
DS = None          # designspec（只有 lookup 已接 powerprofile 才用 compare）
CANON = None
_SRC = {}


def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def canon(name):
    """舊名／新名 → 新名。優先 u2core.iomap.canon，否則用本套 ALIAS（同 Autotest）。"""
    if CANON is not None:
        try:
            return CANON(name)
        except Exception:  # noqa: BLE001
            pass
    global _NORM_ALIAS
    s = str(name or "").strip()
    if not s:
        return s
    if s in _ALIAS:
        return _ALIAS[s]
    if _NORM_ALIAS is None:
        _NORM_ALIAS = {_norm(k): v for k, v in _ALIAS.items()}
        _NORM_ALIAS.update({_norm(k): k for k in _TABLE})
    return _NORM_ALIAS.get(_norm(s), s)


# ---------------------------------------------------------------------------
# Fallback：鏡射 Autotest origin/main u2core/powerprofile.py 與 dutspec.py 的 API
# ---------------------------------------------------------------------------
class _FallbackDutspec:
    """鏡射 u2core.dutspec：SPECS / ORDER / set_spec / get / vin / label / listen。不存檔。"""

    SPECS = {"U2": (12.0, "U.2／U.3（VIN 12 V）"), "M2": (3.3, "M.2（VIN 3.3 V）")}
    ORDER = ("U2", "M2")

    def __init__(self):
        self._cur = {"key": None}
        self._listeners = []

    def set_spec(self, key):
        if key not in self.SPECS and key is not None:
            raise ValueError(f"DUT 規格只能是 {'／'.join(self.ORDER)}：{key}")
        self._cur["key"] = key
        for fn in list(self._listeners):
            try:
                fn(key)
            except Exception:  # noqa: BLE001
                pass

    def get(self):
        return self._cur["key"]

    def vin(self):
        k = self._cur["key"]
        return self.SPECS[k][0] if k else None

    def label(self, key=None):
        k = key if key is not None else self._cur["key"]
        return self.SPECS[k][1] if k else "未選"

    def listen(self, fn):
        self._listeners.append(fn)


class _FallbackPowerProfile:
    """鏡射 u2core.powerprofile：同一份 JSON 格式與 names/load/save/rng/row/match_vin/text。"""

    FIELDS = ("ch", "nom", "min", "max", "tol_pct", "unit")
    HEAD = {"ch": "通道名", "nom": "標稱", "min": "min", "max": "max", "tol_pct": "±%", "unit": "單位"}
    NOTE = NOTE
    DEFAULT = DEFAULT

    def __init__(self, builtin_dir, extra_dirs=None):
        self.BUILTIN_DIR = builtin_dir
        self._extra = list(extra_dirs or [])
        self._cur = {"name": DEFAULT}
        self._cache = {}
        self._listeners = []

    def user_dir(self):
        try:
            from u2core.appdirs import user_base
            base = user_base()
        except Exception:  # noqa: BLE001
            base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
        return os.path.join(base, "U2TestSuite", "dut_power")

    def _safe(self, name):
        n = re.sub(r'[\\/:*?"<>|]+', "_", str(name or "").strip())
        if not n:
            raise ValueError("設計表名稱不能空白")
        return n

    def _dirs(self):
        out = [self.user_dir(), self.BUILTIN_DIR] + self._extra
        seen, dirs = set(), []
        for d in out:
            if d and os.path.isdir(d) and d not in seen:
                seen.add(d)
                dirs.append(d)
        return dirs

    def _path(self, name):
        n = self._safe(name) + ".json"
        # 使用者目錄優先（同 Autotest：同名 → 使用者版覆蓋隨附）
        up = os.path.join(self.user_dir(), n)
        if os.path.isfile(up):
            return up
        for d in [self.BUILTIN_DIR] + self._extra:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
        return os.path.join(self.BUILTIN_DIR, n)

    def names(self):
        out = set()
        for d in self._dirs():
            try:
                out.update(f[:-5] for f in os.listdir(d) if f.lower().endswith(".json"))
            except OSError:
                pass
        return sorted(out, key=lambda n: (n != self.DEFAULT, n.lower()))

    def _num(self, x):
        if x in (None, ""):
            return None
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return v if v == v else None

    def clean_row(self, r):
        ch = str(r.get("ch") or "").strip()
        if not ch:
            raise ValueError("通道名不能空白")
        out = {"ch": ch, "unit": str(r.get("unit") or "V").strip() or "V"}
        for k in ("nom", "min", "max", "tol_pct"):
            v = r.get(k)
            n = self._num(v)
            if v not in (None, "") and n is None:
                raise ValueError(f"{ch}：{self.HEAD[k]}「{v}」不是數字")
            out[k] = n
        if out["tol_pct"] is not None and out["tol_pct"] < 0:
            raise ValueError(f"{ch}：±% 不能是負的")
        if out["min"] is not None and out["max"] is not None and out["min"] > out["max"]:
            raise ValueError(f"{ch}：min {out['min']:g} > max {out['max']:g}")
        return out

    def load(self, name=None):
        n = name or self._cur["name"]
        if not n:
            return None
        p = self._path(n)
        try:
            mt = os.path.getmtime(p)
        except OSError:
            return None
        c = self._cache.get(p)
        if c and c[0] == mt:
            return c[1]
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d = {"name": n, "note": d.get("note", self.NOTE), "rails": [self.clean_row(r) for r in d.get("rails", [])]}
        self._cache[p] = (mt, d)
        return d

    def save(self, name, rails, note=NOTE):
        rows = [self.clean_row(r) for r in rails if str(r.get("ch") or "").strip()]
        seen = {}
        for r in rows:
            k = canon(r["ch"])
            if k in seen:
                raise ValueError(f"通道名重複：{seen[k]} 與 {r['ch']}")
            seen[k] = r["ch"]
        os.makedirs(self.user_dir(), exist_ok=True)
        p = os.path.join(self.user_dir(), self._safe(name) + ".json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"note": note, "rails": rows}, f, ensure_ascii=False, indent=1)
        self._cache.pop(p, None)
        return p

    def get(self):
        return self._cur["name"]

    def set_current(self, name):
        self._cur["name"] = name or None
        for fn in list(self._listeners):
            try:
                fn(self._cur["name"])
            except Exception:  # noqa: BLE001
                pass

    def listen(self, fn):
        self._listeners.append(fn)

    def rng(self, r):
        """一列 → (min, max)：有 min／max 用它；否則 標稱 ±%；都沒有 → None。同 powerprofile.rng。"""
        if r is None:
            return None
        lo, hi = r.get("min"), r.get("max")
        if lo is not None and hi is not None:
            return lo, hi
        nom, tol = r.get("nom"), r.get("tol_pct")
        if nom is not None and tol is not None:
            d = abs(nom) * tol / 100.0
            return (lo if lo is not None else nom - d), (hi if hi is not None else nom + d)
        return None

    def row(self, name, profile=None):
        d = profile or self.load()
        if not d:
            return None
        c = canon(str(name or "").strip())
        for r in d["rails"]:
            if canon(r["ch"]) == c:
                return r
        return None

    def active(self):
        try:
            return self.load() is not None
        except Exception:  # noqa: BLE001
            return False

    def match_vin(self, dut_vin):
        if dut_vin is None:
            return None
        for n in self.names():
            try:
                d = self.load(n)
            except Exception:  # noqa: BLE001
                continue
            r = next((x for x in d.get("rails", []) if str(x.get("ch")).upper() == "VIN"), None)
            if r and r.get("nom") is not None and abs(float(r["nom"]) - float(dut_vin)) <= 0.05 * float(dut_vin):
                return n
        return None

    def vin_mismatch(self, dut_vin):
        r = self.row("VIN")
        if r is None or r.get("nom") is None or dut_vin is None:
            return ""
        if abs(float(r["nom"]) - float(dut_vin)) > 0.05 * float(dut_vin):
            return (f"⚠ 設計表「{self.get()}」的 VIN 標稱 {r['nom']:g} V "
                    f"與 DUT 規格 {dut_vin:g} V 不符：請改選或新增對應的設計表")
        return ""

    def text(self, r, name=None):
        if r is None:
            return None
        nm = name or self.get()
        src = f"設計表「{nm}」"
        g = self.rng(r)
        u = r.get("unit") or "V"
        if g:
            how = (f"（標稱 {r['nom']:g} ±{r['tol_pct']:g} %）"
                   if (r.get("min") is None or r.get("max") is None) and r.get("tol_pct") is not None
                   else (f"（標稱 {r['nom']:g}）" if r.get("nom") is not None else ""))
            return f"{g[0]:g}–{g[1]:g} {u}{how}{src}；{self.NOTE}"
        if r.get("nom") is not None:
            return f"標稱 {r['nom']:g} {u}：{src}；{self.NOTE}"
        return None

    def is_builtin(self, name):
        n = self._safe(name) + ".json"
        up = os.path.join(self.user_dir(), n)
        if os.path.isfile(up):
            return False
        return os.path.isfile(self._path(name))


def _try_import_u2():
    """U2_TOOL_DIR / 已在 sys.path 的 Autotest_tool → u2core。"""
    tool = os.environ.get("U2_TOOL_DIR") or ""
    if tool and os.path.isdir(os.path.join(tool, "u2core")) and tool not in sys.path:
        sys.path.insert(0, tool)
    out = {"iomap": None, "powerprofile": None, "dutspec": None, "designspec": None}
    try:
        from u2core import iomap as IM
        out["iomap"] = IM
    except Exception:  # noqa: BLE001
        IM = None
    try:
        from u2core import powerprofile as _PP
        if hasattr(_PP, "names") and hasattr(_PP, "rng") and hasattr(_PP, "row"):
            out["powerprofile"] = _PP
    except Exception:  # noqa: BLE001
        pass
    try:
        from u2core import dutspec as _DUT
        if hasattr(_DUT, "SPECS") and hasattr(_DUT, "set_spec"):
            out["dutspec"] = _DUT
    except Exception:  # noqa: BLE001
        pass
    try:
        from u2core import designspec as _DS
        # 只有 lookup 已接 powerprofile（origin/main 有 is_m2）才敢用 compare，
        # 否則舊版 designspec 會把 U.2 EQ1 值套到 M.2。
        if hasattr(_DS, "compare") and hasattr(_DS, "is_m2"):
            out["designspec"] = _DS
    except Exception:  # noqa: BLE001
        pass
    return out


class _PPWrapper:
    """包一層 live u2core.powerprofile：若 Autotest 工作樹沒有 dut_power，改搜隨附檔。"""

    def __init__(self, live, bundled):
        self._live = live
        self.BUILTIN_DIR = getattr(live, "BUILTIN_DIR", bundled)
        self._bundled = bundled
        # Autotest 工作樹若沒有 JSON，暫時把 BUILTIN_DIR 指到隨附（不改檔）
        try:
            has = os.path.isdir(self.BUILTIN_DIR) and any(
                f.endswith(".json") for f in os.listdir(self.BUILTIN_DIR))
        except OSError:
            has = False
        if not has:
            try:
                live.BUILTIN_DIR = bundled
                self.BUILTIN_DIR = bundled
                self._patched = True
            except Exception:  # noqa: BLE001
                self._patched = False
        else:
            self._patched = False
        for k in ("FIELDS", "HEAD", "NOTE", "DEFAULT"):
            setattr(self, k, getattr(live, k, getattr(_FallbackPowerProfile, k, None)))

    def __getattr__(self, name):
        return getattr(self._live, name)

    def names(self):
        out = set(self._live.names() or [])
        try:
            out.update(f[:-5] for f in os.listdir(self._bundled) if f.lower().endswith(".json"))
        except OSError:
            pass
        default = getattr(self, "DEFAULT", DEFAULT)
        return sorted(out, key=lambda n: (n != default, n.lower()))

    def load(self, name=None):
        d = self._live.load(name)
        if d:
            return d
        n = name or self._live.get()
        p = os.path.join(self._bundled, f"{n}.json")
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        return {"name": n, "note": raw.get("note", NOTE),
                "rails": [self._live.clean_row(r) for r in raw.get("rails", [])]}

    def is_builtin(self, name):
        try:
            ud = self._live.user_dir()
            if os.path.isfile(os.path.join(ud, f"{name}.json")):
                return False
        except Exception:  # noqa: BLE001
            pass
        for d in (self.BUILTIN_DIR, self._bundled):
            if os.path.isfile(os.path.join(d, f"{name}.json")):
                return True
        return False


_BOUND = False


def bind(force=False):
    """綁定 u2core 或 fallback。預設只做一次，避免 source_report() 把已選的規格／設計表重設掉。"""
    global PP, DUT, DS, CANON, _SRC, _BOUND
    if _BOUND and not force and PP is not None and DUT is not None:
        return _SRC
    found = _try_import_u2()
    if found["iomap"] is not None and hasattr(found["iomap"], "canon"):
        CANON = found["iomap"].canon
        _SRC["iomap.canon"] = "u2core.iomap.canon"
    else:
        CANON = None
        _SRC["iomap.canon"] = "speccmp.canon（鏡射 Autotest_tool origin/main u2core/iomap.py ALIAS／canon）"

    if found["powerprofile"] is not None:
        PP = _PPWrapper(found["powerprofile"], BUNDLED_DIR)
        _SRC["powerprofile"] = "u2core.powerprofile（U2_TOOL_DIR 匯入；names/load/save/rng/row/match_vin/text/set_current）"
        if getattr(PP, "_patched", False):
            _SRC["powerprofile"] += "；工作樹無 dut_power，BUILTIN_DIR 改指向隨附 specs/dut_power"
    else:
        extra = []
        tool = os.environ.get("U2_TOOL_DIR") or ""
        if tool:
            extra.append(os.path.join(tool, "dut_power"))
        PP = _FallbackPowerProfile(BUNDLED_DIR, extra_dirs=extra)
        _SRC["powerprofile"] = (
            "speccmp._FallbackPowerProfile（鏡射 Autotest_tool origin/main u2core/powerprofile.py；"
            "隨附 specs/dut_power 與 origin/main 同檔）"
        )

    if found["dutspec"] is not None:
        DUT = found["dutspec"]
        _SRC["dutspec"] = "u2core.dutspec（SPECS/ORDER/set_spec/get/vin/label）"
    else:
        DUT = _FallbackDutspec()
        _SRC["dutspec"] = "speccmp._FallbackDutspec（鏡射 Autotest_tool origin/main u2core/dutspec.py）"

    if found["designspec"] is not None:
        DS = found["designspec"]
        _SRC["designspec"] = "u2core.designspec.compare／lookup／spec_text／softstart_compare／sequence_compare（已接 powerprofile）"
    else:
        DS = None
        _SRC["designspec"] = (
            "speccmp.voltage_compare（鏡射 designspec.compare／lookup：設計範圍內／低於設計 min／高於設計 max；"
            "範圍來自 powerprofile.rng）"
        )
    _SRC["files"] = [
        "Autotest_tool/dut_power/U2_12V_PowerIC_DesignSpec.json",
        "Autotest_tool/dut_power/M2_3V3_PowerIC_DesignSpec.json",
        "scope_wave_saver/specs/dut_power/（同上，隨附副本）",
        "scope_wave_saver/specs/public_input.json",
        "scope_wave_saver/specs/timing_ref.json",
        "Autotest_tool/u2core/powerprofile.py",
        "Autotest_tool/u2core/dutspec.py",
        "Autotest_tool/u2core/designspec.py（compare/lookup/rng 語意、SOFTSTART_*、M2_RSTN_DELAY_MS）",
        "Autotest_tool/u2core/iomap.py（canon／vnom）",
        "Autotest_tool/u2core/powerprofile_ui.py（機種下拉＋編輯／另存慣例）",
    ]
    _BOUND = True
    return _SRC


bind()


def source_report():
    """給報告／畫面用：實際重用了哪些 Autotest 檔案與函式。"""
    bind(force=False)
    return dict(_SRC)


def public_input(form=None):
    with open(PUBLIC_PATH, encoding="utf-8") as f:
        d = json.load(f)
    k = form or (DUT.get() if DUT else None) or "U2"
    return d.get(k) or d.get("U2"), d


def timing_ref(profile=None):
    with open(TIMING_PATH, encoding="utf-8") as f:
        d = json.load(f)
    name = profile or (PP.get() if PP else None) or DEFAULT
    return d.get(name) or {}, d.get("note", "")


def form_key():
    return DUT.get() if DUT else None


def form_label(key=None):
    return DUT.label(key) if DUT else "未選"


def form_vin(key=None):
    if key:
        return DUT.SPECS[key][0]
    return DUT.vin() if DUT else None


def set_form(key):
    """選 DUT 規格，並自動 match_vin 換設計表（同 Autotest powerprofile_ui）。"""
    DUT.set_spec(key)
    n = PP.match_vin(DUT.vin())
    if n:
        PP.set_current(n)
    return n


def set_profile(name):
    PP.set_current(name)
    return PP.vin_mismatch(DUT.vin()) if DUT.get() else ""


def vnom(name, default=None):
    """標稱：先查目前設計表（同 iomap.vnom），沒有 → default。"""
    try:
        if PP.active():
            r = PP.row(name)
            if r is not None and r.get("nom") is not None:
                return float(r["nom"])
    except Exception:  # noqa: BLE001
        pass
    return default


def demo_vnom_map():
    """示範波形用的標稱：有 min–max 且 nom 落在範圍外時改用中點（例如 U.2 CORE nom 0.84 < min 0.8443）。"""
    from .rails import VNOM
    out = dict(VNOM)
    d = PP.load() if PP else None
    if not d:
        return out
    for r in d["rails"]:
        ch = r["ch"]
        nom, g = r.get("nom"), PP.rng(r)
        if g and (nom is None or nom < g[0] or nom > g[1]):
            out[ch] = (g[0] + g[1]) / 2.0
        elif nom is not None:
            out[ch] = nom
    return out


def voltage_compare(name, mean):
    """鏡射 designspec.compare：'設計範圍內' / '低於設計 min …' / '高於設計 max …' / None。

    若 u2core.designspec 已接 powerprofile，直接呼叫它（須先 set_current）。
    """
    if DS is not None:
        try:
            return DS.compare(name, mean)
        except Exception:  # noqa: BLE001
            pass
    r = PP.row(name) if PP else None
    g = PP.rng(r) if PP else None
    if not g or mean is None:
        return None
    try:
        v = float(mean)
    except (TypeError, ValueError):
        return None
    lo, hi = g
    unit = (r or {}).get("unit") or "V"
    if v < lo:
        return f"低於設計 min {lo:g} {unit}"
    if v > hi:
        return f"高於設計 max {hi:g} {unit}"
    return "設計範圍內"


def _map_verdict(txt, mean=None, has_range=False):
    """Autotest 記錄句 → 本工具 PASS／FAIL／N/A。"""
    if txt == "設計範圍內":
        return "PASS", txt
    if txt and (txt.startswith("低於設計") or txt.startswith("高於設計")):
        return "FAIL", txt
    if mean is None:
        return "N/A", "無實測"
    if not has_range:
        return "N/A", "待填（無 min–max，無法比）"
    return "N/A", "無法比"


def _fmt(v, nd=5):
    if v is None:
        return ""
    try:
        return f"{float(v):.{nd}g}"
    except (TypeError, ValueError):
        return str(v)


def steady_mean(t, y, t_on=None, kind="rail"):
    """穩態 mean：電流用峰值；電壓取 t_on 之後（或最後 25%）的中位數。"""
    import numpy as np
    t, y = (None if t is None else __import__("numpy").asarray(t, float),
            None if y is None else __import__("numpy").asarray(y, float))
    if y is None or len(y) == 0:
        return None
    if kind == "current":
        return float(y.max()) if len(y) else None
    if t is not None and t_on is not None and len(t) == len(y):
        m = t >= float(t_on)
        if int(m.sum()) >= 8:
            return float(__import__("numpy").median(y[m]))
    n = max(8, len(y) // 4)
    return float(__import__("numpy").median(y[-n:]))


def _public_row(form, meas):
    pub, _ = public_input(form)
    if not pub:
        return None
    rail = next((r for r in pub.get("rails") or [] if canon(r.get("ch")) == "VIN"), None)
    if not rail:
        return None
    lo, hi = rail.get("min"), rail.get("max")
    v = meas.get("VIN")
    if v is None or lo is None or hi is None:
        vd, why = "N/A", "無實測" if v is None else "待填"
    elif v < lo:
        vd, why = "FAIL", f"低於公開預設 min {lo:g} V"
    elif v > hi:
        vd, why = "FAIL", f"高於公開預設 max {hi:g} V"
    else:
        vd, why = "PASS", "公開輸入範圍內"
    return {
        "ch": "VIN（公開輸入）",
        "nom": rail.get("nom"), "min": lo, "max": hi, "tol_pct": rail.get("tol_pct"),
        "unit": rail.get("unit") or "V",
        "source": rail.get("source") or "公開預設",
        "meas": v, "spec": f"{lo:g}–{hi:g} V" if lo is not None and hi is not None else "待填",
        "verdict": vd, "why": why, "kind": "public",
    }


def compare_measurements(meas, seq_rows=None, timing_limits=None):
    """meas: {通道名: 實測穩態 V 或 IBUS 峰值 A}；seq_rows 可帶 rise／delay 做時序參考。

    → dict(rows, timing, overall, overall_txt, form, profile, note, warn, public_notes)
    總判定只計設計表有 min–max 的電壓（及公開輸入 VIN、⑦ 已填的時序限制）；
    soft-start／RSTn typ 為「只記錄」，不計入總判定。
    """
    form = DUT.get() or "U2"
    profile = PP.get() or DEFAULT
    data = PP.load(profile)
    rows = []
    counts = {"PASS": 0, "FAIL": 0, "N/A": 0}
    seen = set()
    rails = list((data or {}).get("rails") or [])
    # IBUS 不在設計表 → 仍列出待填
    if not any(canon(r["ch"]) == "IBUS" for r in rails):
        rails.append({"ch": "IBUS", "nom": None, "min": None, "max": None, "tol_pct": None, "unit": "A"})
    for r in rails:
        ch = r["ch"]
        seen.add(canon(ch))
        g = PP.rng(r)
        v = meas.get(ch)
        if v is None:
            v = meas.get(canon(ch))
        txt = voltage_compare(ch, v) if g and v is not None else None
        vd, why = _map_verdict(txt, mean=v, has_range=bool(g))
        if not g and r.get("nom") is not None and v is not None:
            why = f"待填（僅標稱 {r['nom']:g} {r.get('unit') or 'V'}，無 min–max）"
        if ch == "IBUS" and not g:
            why = "待填（設計表無 IBUS 上限；Autotest 僅有 MP5519 預充 150 mA 註記）"
        src = "設計表（設計值，非正式規格）"
        if not g:
            src = "待填" if r.get("nom") is None else "設計表（僅標稱，min/max 待填）"
        spec = PP.text(r, profile) or "待填"
        rows.append({
            "ch": ch, "nom": r.get("nom"), "min": r.get("min"), "max": r.get("max"),
            "tol_pct": r.get("tol_pct"), "unit": r.get("unit") or "V",
            "source": src, "meas": v, "spec": spec, "verdict": vd, "why": why, "kind": "design",
        })
        # 只有真正做了範圍比對的才計入總判定
        if g and v is not None:
            counts[vd] = counts.get(vd, 0) + 1
        else:
            counts["N/A"] += 1

    prow = _public_row(form, meas)
    if prow:
        rows.append(prow)
        if prow["verdict"] in ("PASS", "FAIL"):
            counts[prow["verdict"]] += 1
        else:
            counts["N/A"] += 1

    # 時序參考（只記錄）＋ ⑦ 使用者限制（計入總判定）
    tref, tnote = timing_ref(profile)
    timing = []
    by_name = {canon(x.get("name")): x for x in (seq_rows or []) if x.get("name")}
    ss = tref.get("softstart_ms") or {}
    for ch, rng in ss.items():
        x = by_name.get(ch) or {}
        rise = x.get("rise")
        rise_ms = None if rise is None else float(rise) * 1e3
        # Autotest softstart_compare：10–90 % 換算 0–100 %
        full = None if rise_ms is None else rise_ms / 0.8
        lo, hi = (rng + [None, None])[:2] if isinstance(rng, (list, tuple)) else (None, None)
        why = tref.get("softstart_src") or tnote
        if full is None:
            vd, detail = "N/A", "無實測；" + why + "（只記錄，不計總判定）"
        else:
            inr = (lo is None or full >= lo) and (hi is None or full <= hi)
            mark = "在設計範圍內" if inr else "超出設計範圍"
            vd, detail = "REF", f"換算 0–100 % ≈ {full:.2f} ms vs 設計 {lo:g}–{hi:g} ms（{mark}；只記錄，不計總判定）"
        timing.append({
            "ch": ch, "item": "soft-start 0–100 %（10–90 % 換算）",
            "meas": None if full is None else f"{full:.3g} ms",
            "spec": None if lo is None else f"{lo:g}–{hi:g} ms",
            "verdict": vd, "why": detail, "kind": "ref",
        })

    rst = tref.get("rstn_delay_ms") or {}
    x = by_name.get("RSTn") or {}
    dly = x.get("delay")
    dly_ms = None if dly is None else float(dly) * 1e3
    timing.append({
        "ch": "RSTn", "item": "對參考 Δt（設計 typ）",
        "meas": None if dly_ms is None else f"{dly_ms:.3g} ms",
        "spec": (f"typ {rst['typ']:g} ms" if rst.get("typ") is not None else "待填"),
        "verdict": "REF",
        "why": (rst.get("src") or "設計 typ，只記錄，不計總判定"),
        "kind": "ref",
    })
    ibus_max = tref.get("inrush_ibus_max_a")
    timing.append({
        "ch": "IBUS", "item": "inrush 峰值上限",
        "meas": _fmt(meas.get("IBUS")),
        "spec": "待填" if ibus_max is None else f"max {ibus_max:g} A",
        "verdict": "N/A", "why": "待填（設計表／designspec 無 IBUS max）", "kind": "ref",
    })

    # ⑦ 已填的延遲限制 → 沿用 sequencing 的 PASS/FAIL，計入總判定
    for x in (seq_rows or []):
        if x.get("verdict") in ("PASS", "FAIL"):
            timing.append({
                "ch": x.get("name"), "item": "⑦ 時序限制（延遲）",
                "meas": None if x.get("delay") is None else f"{x['delay'] * 1e3:.3g} ms",
                "spec": f"{x.get('min_ms') or '…'} ~ {x.get('max_ms') or '…'} ms",
                "verdict": x["verdict"], "why": "⑦ 使用者填的 min／max（非設計表）",
                "kind": "limit",
            })
            # ⑦ 限制已有自己的「時序判定」，只在此列出、不計入規格總判定

    if counts["FAIL"]:
        overall = "FAIL"
    elif counts["PASS"]:
        overall = "PASS"
    else:
        overall = "N/A"
    overall_txt = (f"{overall}　PASS {counts['PASS']}　FAIL {counts['FAIL']}　N/A {counts['N/A']}"
                   f"　（設計值非正式規格；公開輸入另列）")
    pub, _meta = public_input(form)
    warn = PP.vin_mismatch(DUT.vin()) if DUT.get() else ""
    return {
        "rows": rows, "timing": timing, "overall": overall, "overall_txt": overall_txt,
        "counts": counts, "form": form, "form_label": DUT.label(),
        "profile": profile, "note": (data or {}).get("note") or NOTE,
        "warn": warn, "public_notes": (pub or {}).get("notes") or [],
        "source": dict(_SRC),
    }


def csv_cols(result=None):
    """summary.csv 四欄。"""
    if not result:
        return {k: "" for k in SPEC_CSV}
    bits = []
    for r in result.get("rows") or []:
        bits.append(f"{r['ch']} {r['verdict']}" + (f"（{r['why']}）" if r.get("why") and r["verdict"] != "PASS" else ""))
    return {
        "規格": result.get("form_label") or result.get("form") or "",
        "電源設計表": result.get("profile") or "",
        "規格總判定": result.get("overall") or "",
        "規格明細": "；".join(bits),
    }


def snapshot():
    return {"form": DUT.get(), "form_label": DUT.label(), "profile": PP.get(),
            "vin": DUT.vin(), "warn": PP.vin_mismatch(DUT.vin()) if DUT.get() else "",
            "source": dict(_SRC)}
