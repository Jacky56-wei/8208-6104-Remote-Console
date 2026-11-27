# -*- coding: utf-8 -*-
"""LeCroy MAUI（WaveRunner 8208HD / HDO6104B）只讀取：*IDN?、通道開關、波形（WAVEDESC）、螢幕 PNG。

連線方式照 All in Test tool（Autotest_tool）：
  · pyvisa ResourceManager()（NI-VISA / Keysight IO）→ 失敗改 "@py"（u2core/visapool._rm）
  · 傳輸設定 COMM_HEADER OFF / COMM_FORMAT DEF9,WORD,BIN / COMM_ORDER LO / WAVEFORM_SETUP SP,0,NP,0,FP,0,SN,0
    （u2tools/timing/scope_hdo.py connect()；只影響傳輸格式，不改擷取設定）
  · 波形 "Cn:WAVEFORM? ALL" → WAVEDESC 解析（scope_hdo.parse_wavedesc，改成 8 通道 / 讀單位）
  · 螢幕 HARDCOPY_SETUP DEV,PNG,BCKG,BLACK,DEST,REMOTE,AREA,DSOWINDOW → SCREEN_DUMP（u2core/scope8208.fetch_png）
這裡的讀取函式不送 *RST、Default Setup、RECALL、*CAL；設定寫入走 settings.py / busname.py / pmeas.py（呼叫 w()）。
SimScope / SimBus：沒有硬體時的模擬（示範模式）：SINGLE arm / 觸發 / INR? / 上電時序波形 / 模擬拔 USB。
"""
from __future__ import annotations

import re
import struct
import time

import numpy as np

LECROY_VID = "0x05FF"
# 型號 → (all-in-one 儀器 key, 通道數, 測試站已知 VISA 位址)；位址同 u2core/instruments.KNOWN_ADDR（2026-10-22）
SCOPES = {
    "8208HD": ("wr8000", 8, "USB0::0x05FF::0x1023::5003N60268::0::INSTR"),
    "6104B": ("hdo6104", 4, "USB0::0x05FF::0x1023::5108N13350::0::INSTR"),
}
SHORT = {"8208": "8208HD", "8208HD": "8208HD", "WR8208HD": "8208HD",
         "6104": "6104B", "6104B": "6104B", "HDO6104B": "6104B", "HDO6104": "6104B"}
LECROY_HCSU = "HARDCOPY_SETUP DEV,PNG,BCKG,BLACK,DEST,REMOTE,AREA,DSOWINDOW"
PNG_SIG = b"\x89PNG\r\n\x1a\n"
COMM_SETUP = ("COMM_HEADER OFF", "COMM_FORMAT DEF9,WORD,BIN", "COMM_ORDER LO", "WAVEFORM_SETUP SP,0,NP,0,FP,0,SN,0")


class ScopeError(RuntimeError):
    pass


def norm_model(m):
    s = re.sub(r"[\s_-]", "", str(m or "")).upper()
    if s in SHORT:
        return SHORT[s]
    if "8208" in s or "8000HD" in s or s.startswith("WR8"):
        return "8208HD"
    if "6104" in s or s.startswith("HDO6"):
        return "6104B"
    raise ValueError(f"不認得的示波器型號：{m}（只支援 8208 / 6104）")


def model_from_idn(idn):
    """'LECROY,WR8208HD,LCRY…' → 8208HD；'LECROY,HDO6104B,…' → 6104B；其他 → None"""
    p = [x.strip() for x in str(idn or "").split(",")]
    if len(p) < 2:
        return None
    m = re.sub(r"[\s_-]", "", p[1]).upper()
    return "8208HD" if "8208" in m else "6104B" if "6104" in m else None


# ---------------------------------------------------------------- WAVEDESC
def parse_wavedesc(raw: bytes):
    """LeCroy WAVEDESC（示波器回傳或 .trc）→ (t, v, meta)。8 通道版（WAVE_SOURCE 0..7 = C1..C8）"""
    p = raw.find(b"WAVEDESC")
    if p < 0:
        raise ScopeError("找不到 WAVEDESC 區塊，資料格式不符")
    e = "<" if struct.unpack_from("<h", raw, p + 34)[0] == 1 else ">"

    def h(o): return struct.unpack_from(e + "h", raw, p + o)[0]
    def l(o): return struct.unpack_from(e + "i", raw, p + o)[0]
    def f(o): return struct.unpack_from(e + "f", raw, p + o)[0]
    def d(o): return struct.unpack_from(e + "d", raw, p + o)[0]
    def s(o, n): return raw[p + o:p + o + n].split(b"\0")[0].decode(errors="ignore").strip()

    comm_type = h(32)                                   # 0 = byte, 1 = word
    start = p + l(36) + l(40) + l(44) + l(48) + l(52) + l(56)
    dtype = np.dtype(e + "i2") if comm_type == 1 else np.dtype("i1")
    n = min(l(60) // dtype.itemsize, (len(raw) - start) // dtype.itemsize)
    if n <= 0:
        raise ScopeError("波形資料長度為 0（示波器還沒擷取 / 通道沒有資料）")
    data = np.frombuffer(raw, dtype=dtype, count=n, offset=start).astype(np.float64)
    gain, offset, dt, t0 = f(156), f(160), f(176), d(180)
    v = data * gain - offset
    t = t0 + np.arange(n) * float(dt)
    src = h(344)
    meta = {"instrument": s(76, 16), "source": src, "trace": f"C{src + 1}" if 0 <= src <= 7 else "",
            "dt": float(dt), "t0": float(t0), "n": int(n), "gain": float(gain), "offset": float(offset),
            "vunit": s(196, 48) or "V", "hunit": s(244, 48) or "S"}
    return t, v, meta


def encode_wavedesc(y, h_int=1e-6, h_off=0.0, unit="V", source=0, instrument="SIM"):
    """t, v → WAVEDESC 區塊（模擬 / 測試用；同 pwrdis/instruments.encode_wavedesc）"""
    y = np.asarray(y, float)
    lo, hi = float(np.min(y)), float(np.max(y))
    gain = max(hi - lo, 1e-6) / 60000.0
    offset = -(lo + hi) / 2.0
    raw = np.clip(np.round((y + offset) / gain), -32768, 32767).astype("<i2")
    desc = bytearray(346)
    desc[0:8] = b"WAVEDESC"
    desc[16:32] = b"LECROY_2_3".ljust(16, b"\0")
    struct.pack_into("<h", desc, 32, 1)
    struct.pack_into("<h", desc, 34, 1)
    struct.pack_into("<i", desc, 36, 346)
    struct.pack_into("<i", desc, 60, raw.nbytes)
    desc[76:76 + len(instrument[:16])] = instrument[:16].encode()
    struct.pack_into("<i", desc, 116, len(raw))
    struct.pack_into("<f", desc, 156, gain)
    struct.pack_into("<f", desc, 160, offset)
    struct.pack_into("<f", desc, 176, h_int)
    struct.pack_into("<d", desc, 180, h_off)
    desc[196:196 + len(unit)] = unit.encode()
    desc[244:245] = b"S"
    struct.pack_into("<h", desc, 344, source)
    body = bytes(desc) + raw.tobytes()
    return b"#9" + f"{len(body):09d}".encode() + body


def block_len(buf):
    """IEEE 488.2 '#<n><len>' → (標頭長, 資料長)；不是區塊 → None"""
    i = buf.find(b"#")
    if 0 <= i < 16 and len(buf) > i + 2 and buf[i + 1:i + 2].isdigit():
        n = int(buf[i + 1:i + 2])
        if n and len(buf) >= i + 2 + n:
            return i + 2 + n, int(buf[i + 2:i + 2 + n])
    return None


# ---------------------------------------------------------------- 示波器
class LeCroyScope:
    """一台 LeCroy MAUI 示波器（VISA）。只讀取。"""

    def __init__(self, addr, model, log=print, timeout_ms=60000):
        self.addr, self.model, self.log = addr, norm_model(model), log or (lambda *_: None)
        self.timeout_ms = int(timeout_ms)
        self.inst = None
        self.idn = ""

    @property
    def nch(self):
        return SCOPES[self.model][1]

    def open(self, check_model=True):
        import pyvisa
        try:
            rm = pyvisa.ResourceManager()                   # NI-VISA / Keysight IO
        except Exception:  # noqa: BLE001
            rm = pyvisa.ResourceManager("@py")              # pyvisa-py
        inst = rm.open_resource(self.addr)
        inst.timeout = self.timeout_ms
        try:
            inst.chunk_size = 4 * 1024 * 1024
        except Exception:  # noqa: BLE001
            pass
        self.inst = inst
        self.idn = inst.query("*IDN?").strip()
        got = model_from_idn(self.idn)
        if check_model and got != self.model:
            self.close()
            raise ScopeError(f"{self.addr} 回應 *IDN? = {self.idn!r}，不是 {self.model}"
                             "（兩台 LeCroy 同 VID/PID，請確認位址；或加 --no-check 略過）")
        for c in COMM_SETUP:
            inst.write(c)
        return self.idn

    def close(self):
        if self.inst is not None:
            try:
                self.inst.close()
            except Exception:  # noqa: BLE001
                pass
        self.inst = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    sim = False

    # -- 讀寫 -------------------------------------------------------------
    def w(self, cmd):
        self.inst.write(cmd)

    def q(self, cmd):
        return self.inst.query(cmd).strip()

    def trace_on(self, trace):
        try:
            return "ON" in self.q(f"{trace}:TRACE?").upper()
        except Exception:  # noqa: BLE001
            return False

    def on_channels(self):
        return [f"C{n}" for n in range(1, self.nch + 1) if self.trace_on(f"C{n}")]

    def stop(self):
        self.inst.write("STOP")
        self.q("*OPC?")

    def _read_block(self):
        buf = bytes(self.inst.read_raw())
        hl = block_len(buf)
        if hl:                                              # USBTMC 大資料分段：讀到區塊長度為止
            need = hl[0] + hl[1]
            t_end = time.time() + self.timeout_ms / 1000.0
            while len(buf) < need and time.time() < t_end:
                buf += bytes(self.inst.read_raw())
        return buf

    def read_raw_wave(self, trace):
        self.inst.write(f"{trace}:WAVEFORM? ALL")
        return self._read_block()

    def screen_png(self):
        self.inst.write(LECROY_HCSU)
        self.inst.write("SCREEN_DUMP")
        buf, t_end = b"", time.time() + 20.0
        while time.time() < t_end:
            buf += bytes(self.inst.read_raw())
            e = buf.rfind(b"IEND")
            if e >= 0 and b"IEND" in buf[-64:] and len(buf) >= e + 8:
                break
        i = buf.find(b"\x89PNG")
        if i < 0:
            raise ScopeError(f"SCREEN_DUMP 回應不是 PNG（{buf[:16]!r}）")
        return buf[i:buf.rfind(b"IEND") + 8]

    def settings(self):
        """只讀：時基 / 取樣率 / 各通道 V/div、offset（寫進 info.json）"""
        out = {}
        for k, c in (("tdiv", "TIME_DIV?"), ("trig_delay", "TRIG_DELAY?"), ("trig_mode", "TRIG_MODE?"),
                     ("trig_select", "TRIG_SELECT?"), ("sample_rate", "VBS? 'Return=app.Acquisition.Horizontal.SamplingRate'")):
            try:
                out[k] = self.q(c)
            except Exception:  # noqa: BLE001
                pass
        return out

    def ch_settings(self, trace):
        out = {}
        for k, c in (("vdiv", "VOLT_DIV?"), ("offset", "OFFSET?")):
            try:
                out[k] = self.q(f"{trace}:{c}")
            except Exception:  # noqa: BLE001
                pass
        return out


class SimDisconnect(OSError):
    """模擬 USB 中斷（VI_ERROR_TMO / VI_ERROR_CONN_LOST 的替身）"""


class SimBus:
    """兩台模擬示波器共用：主觸發 → AUX OUT → 輔 EXT（輔有 arm 才會觸發）＋ 模擬拔 USB"""

    def __init__(self, trig_delay=(0.4, 1.2), seed=None, direction="alt"):
        self.trig_delay = trig_delay
        self.direction = direction                  # "up" / "down" / "alt"（上電、下電輪流）
        self.dir = "up"
        self.rng = np.random.default_rng(seed)
        self.scopes = {}
        self.down_until = {}
        self.n_trig = 0
        self.arb = None                             # N6705 模擬 ARB：{"fire_at": 牆鐘, "waves": {model: {ch: (t, y)}}}
        self.cur = None                             # 這次觸發的 ARB 波形（read_raw_wave 用）
        self.trig = {"src": "C8", "level": 0.9, "slope": "NEG"}  # 預設主 C8 RSTn、50 % × 1.8 V

    def set_trig(self, src="C8", level=0.9, slope="NEG"):
        """主觸發（預設 C8 RSTn、0.9 V）；slope = POS / NEG / EITHER"""
        self.trig = {"src": str(src or "C8").split()[0].upper(),
                     "level": float(level if level is not None else 0.9),
                     "slope": str(slope or "NEG").upper()}

    def arb_start(self, tp, vp):
        """模擬 N6705 *TRG：依 ARB 輸出（VIN）算出兩台示波器的波形；主觸發通道過門檻的那一刻 = 觸發（預設 C8 RSTn）"""
        waves, t_trig = sim_arb_waves(tp, vp, rng=self.rng, trig=self.trig)
        self.arb = {"fire_at": None if t_trig is None else time.time() + max(t_trig, 0.0), "waves": waves}
        return t_trig

    def arb_abort(self):
        self.arb = None

    def unplug(self, model, seconds):
        self.down_until[model] = time.time() + float(seconds)

    def is_down(self, model):
        return time.time() < self.down_until.get(model, 0.0)

    def fire(self):
        """主觸發的那一刻：產生這次的上電波形（兩台共用時間軸）＋ 有 arm 的輔一起觸發"""
        self.n_trig += 1
        if self.arb is not None:                    # ARB 帶動（N6705）：用 ARB 算出的波形
            self.dir, self.cur, self.arb = "arb", self.arb["waves"], None
            for sc in self.scopes.values():
                if sc.mode == "SINGLE":
                    sc.mode, sc.inr, sc.jit, sc.t_trig = "STOP", 1, {}, time.time()
            return
        self.dir = self.direction if self.direction in ("up", "down") else ("up" if self.n_trig % 2 else "down")
        jit = {nm: float(self.rng.normal(0, 0.03e-3)) for m in ("8208HD", "6104B") for nm in _sim_names(m)}
        for sc in self.scopes.values():
            if sc.mode == "SINGLE":
                sc.mode, sc.inr, sc.jit, sc.t_trig = "STOP", 1, jit, time.time()


def _sim_names(model):
    from .rails import NAMES
    return NAMES[model]


def sim_waves(model, jit=None, t0=-2e-3, t1=8e-3, n=10000, rng=None, direction="up", vnom=None):
    """上電時序（觸發 t = 0 = 主 C8 RSTn 上升；輔 VIN 比 RSTn 早）→ {ch: (t, y)}
    direction="down"：下電（t = 0 = 主 C8 RSTn 下降；反順序：RSTn / BUSPG 先掉，VIN 最後）"""
    if direction == "down":
        return _sim_down(model, jit, t0, t1, n, rng, vnom=vnom)
    from .rails import NAMES, VNOM as _VNOM
    VNOM = dict(_VNOM)
    if vnom:
        VNOM.update(vnom)
    rng = rng or np.random.default_rng()
    jit = jit or {}
    t = np.linspace(t0, t1, n)
    out = {}
    for i, nm in enumerate(NAMES[model]):
        v = VNOM.get(nm, 1.0) or 1.5
        # t = 0 = 主 C8 RSTn 上升（數位步階在 d + 1.2 ms）；兩台同一時間軸
        rst_t = 7 * 0.45e-3 + 1.2e-3
        d = ((i * 0.45e-3) if model == "8208HD" else (-0.6e-3 + i * 0.25e-3)) + jit.get(nm, 0.0) - rst_t
        tau = 0.25e-3
        y = v * np.clip((t - d) / tau, 0, 1) if not nm.endswith("n") and nm != "BUSPG" else v * (t > d + 1.2e-3)
        if nm == "IBUS":
            y = 0.8 * np.exp(-np.clip(t - d, 0, None) / 1e-3) * (t > d) + 0.15 * (t > d)
        out[i + 1] = (t, y + 0.004 * v * rng.standard_normal(n))
    return out


def _sim_down(model, jit, t0, t1, n, rng, vnom=None):
    from .rails import NAMES, VNOM as _VNOM
    VNOM = dict(_VNOM)
    if vnom:
        VNOM.update(vnom)
    rng = rng or np.random.default_rng()
    jit = jit or {}
    t = np.linspace(t0, t1, n)
    out = {}
    for i, nm in enumerate(NAMES[model]):
        v = VNOM.get(nm, 1.0) or 1.5
        rst_t = -0.2e-3 - 7 * 0.22e-3                       # 主 C8 RSTn 下降落在 t = 0
        if model == "8208HD":
            d = -0.2e-3 - i * 0.22e-3 - rst_t
        else:
            d = {0: 2.6e-3, 1: 2.2e-3, 2: -1.85e-3, 3: 1.8e-3}.get(i, 0.0) - rst_t  # BUSPG 最早掉、VIN 最後
        d += jit.get(nm, 0.0)
        tau = 0.4e-3 if model == "8208HD" else 0.9e-3
        if nm.endswith("n") or nm == "BUSPG":
            y = v * (t < d)
        elif nm == "IBUS":
            y = 0.15 * (1 - np.clip((t - d) / 0.3e-3, 0, 1))
        else:
            y = v * (1 - np.clip((t - d) / tau, 0, 1))
        out[i + 1] = (t, y + 0.004 * v * rng.standard_normal(n))
    return out


VIN_ON, VIN_OFF = 9.6, 9.0          # 模擬 DUT：VIN UVLO（上升 9.6 V 開、掉到 9.0 V 關）


def _movavg(y, n):
    n = max(int(n), 1)
    if n <= 1:
        return y
    k = np.ones(n) / n
    pad = np.concatenate([np.full(n - 1, y[0]), y])
    return np.convolve(pad, k, mode="valid")


def first_edge(t, y, level, slope="NEG"):
    """波形第一次過門檻的時間。POS = 上升、NEG = 下降、EITHER = 先到的那個；沒過 → None"""
    t, y = np.asarray(t, float), np.asarray(y, float)
    if len(t) < 2:
        return None
    lv = float(level)
    sl = str(slope or "NEG").upper()
    above = y >= lv
    rise = np.flatnonzero(~above[:-1] & above[1:])
    fall = np.flatnonzero(above[:-1] & ~above[1:])
    idx = []
    if sl in ("POS", "EITHER"):
        idx += list(rise)
    if sl in ("NEG", "EITHER"):
        idx += list(fall)
    if not idx:
        return None
    i = int(min(idx))
    y0, y1 = float(y[i]), float(y[i + 1])
    frac = 0.0 if y1 == y0 else (lv - y0) / (y1 - y0)
    return float(t[i] + np.clip(frac, 0.0, 1.0) * (t[i + 1] - t[i]))


def sim_arb_waves(tp, vp, rng=None, t0=-2e-3, t1=8e-3, n=10000, trig=None):
    """模擬 DUT 被 N6705 ARB（接 VIN）帶動：
    VIN ≥ 9.6 V → 各 rail 依上電順序延遲 a_i 開；VIN < 9.0 V → 依下電順序延遲 b_i 關（RSTn / BUSPG 先掉、CORE 最後）。
    Brownout 沒掉到 UVLO → rail 不動、只有 VIN / VBUS 掉；ON-OFF-ON → 先下電再上電。
    觸發 t = 0 = 主觸發通道（預設 C8 RSTn）第一次過準位（斜率 POS / NEG / EITHER）；
    通道沒跳（例淺 Brownout、RSTn 沒 assert）→ t_trig = None（不觸發）。回傳 ({model: {ch: (t, y)}}, t_trig)"""
    from .rails import NAMES, VNOM
    rng = rng or np.random.default_rng()
    trig = trig or {}
    tp, vp = np.asarray(tp, float), np.asarray(vp, float)
    T = float(tp[-1]) if len(tp) else 0.0
    g_t = np.arange(0.0, T + 2e-6, 2e-6) if T > 0 else np.array([0.0, 2e-6])
    g_v = np.interp(g_t, tp, vp) if len(tp) else np.zeros_like(g_t)
    st = bool(g_v[0] >= VIN_ON)
    edges, cur = [], st
    hi, lo = g_v >= VIN_ON, g_v < VIN_OFF
    for i in np.flatnonzero(hi | lo):
        if not cur and hi[i]:
            cur = True
            edges.append((float(g_t[i]), True))
        elif cur and lo[i]:
            cur = False
            edges.append((float(g_t[i]), False))
    a = {nm: 0.3e-3 + i * 0.45e-3 for i, nm in enumerate(NAMES["8208HD"])}
    b = {nm: 0.05e-3 + (7 - i) * 0.22e-3 for i, nm in enumerate(NAMES["8208HD"])}
    a["BUSPG"], b["BUSPG"] = 0.9e-3, 0.02e-3
    tau = 0.3e-3
    src = str(trig.get("src") or "C8").upper()
    msrc = re.search(r"C\s*(\d+)", src)
    trig_ch = int(msrc.group(1)) if msrc else 8
    nm_t = NAMES["8208HD"][trig_ch - 1] if 1 <= trig_ch <= 8 else "RSTn"
    level = float(trig["level"]) if trig.get("level") is not None else (VNOM.get(nm_t, 1.8) * 0.5)
    slope = str(trig.get("slope") or "NEG").upper()
    dt_f = 2e-6
    tf = np.arange(0.0, max(T, 0.0) + 5e-3 + dt_f, dt_f)

    def state_at(nm, tarr):
        e = np.full(len(tarr), float(st))
        last = -1.0
        for te, up in edges:
            tt = max(te + (a[nm] if up else b[nm]), last + 0.05e-3)
            e[tarr >= tt] = 1.0 if up else 0.0
            last = tt
        return e

    v_t = VNOM.get(nm_t, 1.8)
    e_t = state_at(nm_t, tf)
    y_t = v_t * (e_t if nm_t == "RSTn" else _movavg(e_t, tau / dt_f))
    t_trig = first_edge(tf, y_t, level, slope)
    vin_chg = np.flatnonzero(np.abs(g_v - g_v[0]) > 0.02 * max(float(np.max(np.abs(g_v))), 1e-3))
    tz = t_trig if t_trig is not None else (float(g_t[vin_chg[0]]) if len(vin_chg) else 0.0)
    t = np.linspace(t0, t1, n)
    ta = t + tz                                          # ARB 時間軸
    dt = float(t[1] - t[0]) if n > 1 else 1e-6

    def state(nm):
        e = np.full(n, st, dtype=float)
        tog, last = [], -1.0
        for te, up in edges:                             # 照 VIN 事件順序；還沒關完就回來 → 關完才再開
            tt = max(te + (a[nm] if up else b[nm]), last + 0.05e-3)
            tog.append((tt, up))
            last = tt
        for tt, on in tog:
            e[ta >= tt] = 1.0 if on else 0.0
        return e

    out = {"8208HD": {}, "6104B": {}}
    for i, nm in enumerate(NAMES["8208HD"]):
        v = VNOM.get(nm, 1.0)
        e = state(nm)
        y = v * (e if nm == "RSTn" else _movavg(e, tau / dt))
        out["8208HD"][i + 1] = (t, y)
    vin = np.interp(ta, tp, vp)
    vbus = 0.99 * np.interp(ta - 0.25e-3, tp, vp)
    out["6104B"][1] = (t, vin)
    out["6104B"][2] = (t, vbus)
    out["6104B"][3] = (t, VNOM["BUSPG"] * state("BUSPG"))
    dv = np.gradient(vbus, dt)
    ibus = np.clip(5e-5 * dv, 0, 3.0) + 0.15 * _movavg(state("CORE"), tau / dt)
    out["6104B"][4] = (t, ibus)
    return out, t_trig


class SimScope(LeCroyScope):
    """模擬示波器（沒有硬體時測試用）：SINGLE / INR? / TRIG_MODE? / WAVEFORM? / SCREEN_DUMP（示意圖）"""
    sim = True

    def __init__(self, addr, model, log=print, timeout_ms=60000, bus=None, png=None):
        super().__init__(addr, model, log, timeout_ms)
        self.bus = bus or SimBus()
        self.bus.scopes[self.model] = self
        self.png = png
        self.mode, self.inr, self.jit, self.t_trig, self.t_arm = "STOP", 0, {}, None, None
        self.sent = []

    def _chk(self):
        if self.bus.is_down(self.model):
            self.inst = None
            raise SimDisconnect(f"VI_ERROR_TMO（模擬 USB 中斷：{self.model}）")
        if self.inst is None:
            raise SimDisconnect(f"VI_ERROR_CONN_LOST（模擬：{self.model} 尚未重新開啟）")

    def open(self, check_model=True):
        if self.bus.is_down(self.model):
            raise SimDisconnect(f"VI_ERROR_RSRC_NFOUND（模擬：{self.addr} 不在 USB 上）")
        self.idn = f"LECROY,{'WR8208HD' if self.model == '8208HD' else 'HDO6104B'},SIM0000,MAUI (模擬)"
        self.inst = True
        return self.idn

    def close(self):
        self.inst = None

    def _tick(self):
        if self.model != "8208HD" or self.mode != "SINGLE":
            return
        a = self.bus.arb
        if a is not None:                                 # N6705 ARB 已啟動：只在主觸發通道過門檻那一刻觸發（沒過 → 不觸發）
            if a["fire_at"] is not None and time.time() >= a["fire_at"]:
                self.bus.fire()
        elif self.t_arm and time.time() >= self.t_arm:
            self.bus.fire()

    def w(self, cmd):
        self._chk()
        self.sent.append(cmd)
        c = cmd.strip().upper()
        if c == "STOP":
            self.mode = "STOP"
        elif c.startswith("TRIG_MODE") or c.startswith("TRMD"):
            self.mode = c.split()[-1]
            if self.mode == "SINGLE" and self.model == "8208HD":
                self.t_arm = time.time() + float(self.bus.rng.uniform(*self.bus.trig_delay))
        elif self.model == "8208HD":
            m = re.search(r"(?:TRIG_SELECT|TRSE)\s+EDGE,SR,([A-Z0-9]+)", c)
            if m:
                self.bus.trig["src"] = m.group(1)
            m = re.search(r"C(\d+):(?:TRIG_LEVEL|TRLV)\s+(\S+)", c)
            if m:
                try:
                    self.bus.trig["level"] = float(m.group(2))
                    self.bus.trig["src"] = f"C{int(m.group(1))}"
                except ValueError:
                    pass
            m = re.search(r"C(\d+):(?:TRIG_SLOPE|TRSL)\s+(\S+)", c)
            if m:
                sl = m.group(2)
                self.bus.trig["slope"] = "EITHER" if sl == "WINDOW" else sl

    def q(self, cmd):
        self._chk()
        self._tick()
        c = cmd.strip().upper()
        if c == "*IDN?":
            return self.idn
        if c == "INR?":
            v, self.inr = self.inr, 0
            return str(v)
        if c in ("TRIG_MODE?", "TRMD?"):
            return self.mode
        if c == "*OPC?":
            return "1"
        if c == "TIME_DIV?":
            return "1E-3"
        if c == "TRIG_DELAY?":
            return "-3E-3"
        if c == "TRIG_SELECT?":
            src = self.bus.trig.get("src", "C8") if self.model == "8208HD" else "EX10"
            return f"EDGE,SR,{src},HT,OFF"
        if "AUXOUTPUT" in c or "AUXMODE" in c:
            return "TriggerOut"
        if "WAITUNTILIDLE" in c:
            return "1"
        if "APP.MEASURE.P" in c:                          # 模擬：示波器 P 值讀不到 → 只有 PC 計算值
            return "VBS "
        return "ON" if c.endswith("TRACE?") else "1E-6"

    def stop(self):
        self.w("STOP")

    def read_raw_wave(self, trace):
        self._chk()
        k = int(re.sub(r"\D", "", trace) or 1)
        rng = np.random.default_rng(self.bus.n_trig * 100 + k)
        if self.bus.dir == "arb" and self.bus.cur:
            t, y = self.bus.cur[self.model][k]
            v = max(float(np.max(np.abs(y))), 0.05)
            y = y + 0.004 * v * rng.standard_normal(len(y))
            return encode_wavedesc(y, float(t[1] - t[0]), float(t[0]), "A" if (self.model, k) == ("6104B", 4) else "V",
                                   k - 1, self.model)
        t, y = sim_waves(self.model, self.jit, rng=rng, direction=self.bus.dir)[k]
        return encode_wavedesc(y, float(t[1] - t[0]), float(t[0]), "A" if (self.model, k) == ("6104B", 4) else "V",
                               k - 1, self.model)

    def screen_png(self):
        self._chk()
        if self.png:
            with open(self.png, "rb") as f:
                return f.read()
        import zlib
        w, h = 4, 4
        rows = b"".join(b"\0" + b"\x20\x20\x20" * w for _ in range(h))

        def ck(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return PNG_SIG + ck(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ck(b"IDAT", zlib.compress(rows)) \
            + ck(b"IEND", b"")
