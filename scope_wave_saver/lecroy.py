# -*- coding: utf-8 -*-
"""LeCroy MAUI（WaveRunner 8208HD / HDO6104B）只讀取：*IDN?、通道開關、波形（WAVEDESC）、螢幕 PNG。

連線方式照 All in Test tool（Autotest_tool）：
  · pyvisa ResourceManager()（NI-VISA / Keysight IO）→ 失敗改 "@py"（u2core/visapool._rm）
  · 傳輸設定 COMM_HEADER OFF / COMM_FORMAT DEF9,WORD,BIN / COMM_ORDER LO / WAVEFORM_SETUP SP,0,NP,0,FP,0,SN,0
    （u2tools/timing/scope_hdo.py connect()；只影響傳輸格式，不改擷取設定）
  · 波形 "Cn:WAVEFORM? ALL" → WAVEDESC 解析（scope_hdo.parse_wavedesc，改成 8 通道 / 讀單位）
  · 螢幕 HARDCOPY_SETUP DEV,PNG,BCKG,BLACK,DEST,REMOTE,AREA,DSOWINDOW → SCREEN_DUMP（u2core/scope8208.fetch_png）
不送 *RST、Default Setup、RECALL、*CAL、TRIG_MODE、V/div、時基（不改示波器設定）。
唯一例外：stop_first=True 時送 STOP（讓各通道是同一次擷取），預設不送。
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

    # -- 讀取 -------------------------------------------------------------
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


class SimScope(LeCroyScope):
    """模擬示波器（沒有硬體時測試用）"""

    def open(self, check_model=True):
        self.idn = f"LECROY,{'WR8208HD' if self.model == '8208HD' else 'HDO6104B'},SIM0000,MAUI (模擬)"
        self.inst = True
        return self.idn

    def close(self):
        self.inst = None

    def q(self, cmd):
        return "ON" if cmd.endswith("TRACE?") else "1E-6"

    def stop(self):
        pass

    def read_raw_wave(self, trace):
        k = int(re.sub(r"\D", "", trace) or 1)
        t = np.arange(10000) * 1e-8
        y = (k * 0.5) * np.sin(2 * np.pi * 1e5 * t + k) + 0.01 * np.random.randn(t.size)
        return encode_wavedesc(y, 1e-8, -5e-5, "V", k - 1, self.model)

    def screen_png(self):
        import zlib
        w, h = 4, 4
        rows = b"".join(b"\0" + b"\x20\x20\x20" * w for _ in range(h))

        def ck(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return PNG_SIG + ck(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ck(b"IDAT", zlib.compress(rows)) \
            + ck(b"IEND", b"")
