# -*- coding: utf-8 -*-
"""Keysight N6705 DC Power Analyzer：ARB 下載 / 啟動 / 安全中止（走 link.Link 背景自動連線 ＋ USB 自動重連）

指令沿用 Autotest_tool u2core.arbmodule（scpi / download / run_cmds）與 u2core.instruments.N6705C.safe_off：
  下載：ABOR:TRAN → ARB:FUNC:TYPE / SHAP → 形狀參數 → ARB:COUN → ARB:TERM:LAST → TRIG:ARB:SOUR BUS → SYST:ERR? / *OPC?
  啟動：OUTP ON,(@ch…) → INIT:TRAN (@ch…) → *TRG（多通道同一個 *TRG 同時開始）
  中止：ABOR:TRAN (@ch) → OUTP OFF,(@ch)（或 keep = 維持某電壓）
"""
from __future__ import annotations

import time

KNOWN_ADDR = {"N6705C": "USB0::0x2A8D::0x0F02::MY56010271::0::INSTR",
              "N6705B": "USB0::0x0957::0x0F07::MY53003861::0::INSTR"}
DEF_ADDR = KNOWN_ADDR["N6705C"]


class N6705:
    """實機（pyvisa）"""
    sim = False
    model = "N6705"

    def __init__(self, addr=DEF_ADDR, log=print, timeout_ms=10000):
        self.addr, self.log, self.timeout_ms = addr, log or (lambda *_: None), int(timeout_ms)
        self.inst, self.idn = None, ""

    def open(self):
        import pyvisa
        try:
            rm = pyvisa.ResourceManager()
        except Exception:  # noqa: BLE001
            rm = pyvisa.ResourceManager("@py")
        inst = rm.open_resource(self.addr)
        inst.timeout = self.timeout_ms
        inst.read_termination = inst.write_termination = "\n"
        self.inst = inst
        self.idn = inst.query("*IDN?").strip()
        if "N67" not in self.idn.upper():
            self.close()
            raise RuntimeError(f"{self.addr} 回應 *IDN? = {self.idn!r}，不是 N6705")
        return self.idn

    def close(self):
        if self.inst is not None:
            try:
                self.inst.close()
            except Exception:  # noqa: BLE001
                pass
        self.inst = None

    def w(self, cmd):
        self.inst.write(cmd)

    def q(self, cmd):
        return self.inst.query(cmd).strip()

    def write(self, cmd):                  # arbmodule.download / robust.syst_err 用 write() / query()
        return self.w(cmd)

    def query(self, cmd):
        return self.q(cmd)

    def sim_load(self, cfg, label=""):
        pass


class SimN6705(N6705):
    """模擬 N6705：*TRG → 依「VIN」通道的 ARB 輸出算出兩台示波器的波形（lecroy.SimBus.arb_start）"""
    sim = True

    def __init__(self, addr=DEF_ADDR, log=print, bus=None):
        super().__init__(addr, log)
        self.bus = bus
        self.loaded, self.labels, self.armed, self.out = {}, {}, set(), {}
        self.sent = []

    def _chk(self):
        if self.bus is not None and self.bus.is_down(self.model):
            self.inst = None
            from .lecroy import SimDisconnect
            raise SimDisconnect("VI_ERROR_TMO（模擬 USB 中斷：N6705）")
        if self.inst is None:
            from .lecroy import SimDisconnect
            raise SimDisconnect("VI_ERROR_CONN_LOST（模擬：N6705 尚未重新開啟）")

    def open(self):
        if self.bus is not None and self.bus.is_down(self.model):
            from .lecroy import SimDisconnect
            raise SimDisconnect(f"VI_ERROR_RSRC_NFOUND（模擬：{self.addr} 不在 USB 上）")
        self.inst = True
        self.idn = "Keysight Technologies,N6705C,SIM00000,(模擬)"
        return self.idn

    def close(self):
        self.inst = None

    def sim_load(self, cfg, label=""):
        self.loaded[int(cfg.channel)] = cfg
        self.labels[int(cfg.channel)] = label

    def _drive(self):
        """哪個通道接 DUT VIN：標籤有 VIN 的優先，否則最小通道"""
        chans = [c for c in sorted(self.armed) if c in self.loaded]
        if not chans:
            return None
        vin = [c for c in chans if "VIN" in str(self.labels.get(c, "")).upper()]
        return (vin or chans)[0]

    def w(self, cmd):
        self._chk()
        self.sent.append(cmd)
        c = cmd.strip().upper()
        chans = _chans(c)
        if c.startswith("INIT:TRAN"):
            self.armed |= set(chans)
        elif c.startswith("ABOR:TRAN"):
            self.armed -= set(chans)
            if self.bus is not None:
                self.bus.arb_abort()
        elif c.startswith("OUTP"):
            for ch in chans:
                self.out[ch] = c.split(",")[0].split()[-1] in ("ON", "1")
        elif c == "*TRG":
            ch = self._drive()
            if ch is not None and self.bus is not None:
                from . import arbtpl as T
                tp, vp = T.profile(self.loaded[ch])
                self.bus.arb_start(tp, vp)
            self.armed.clear()

    def q(self, cmd):
        self._chk()
        c = cmd.strip().upper()
        if c == "*IDN?":
            return self.idn
        if c.startswith("SYST:ERR"):
            return '+0,"No error"'
        if c == "*OPC?":
            return "1"
        if c.startswith("OUTP?"):
            ch = (_chans(c) or [1])[0]
            return "1" if self.out.get(ch) else "0"
        return "0"


def _chans(c):
    import re
    m = re.search(r"\(@([\d,:]+)\)", c)
    if not m:
        return []
    out = []
    for part in m.group(1).split(","):
        if ":" in part:
            a, b = part.split(":")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _AM():
    from u2core import arbmodule as AM
    return AM


def download(dev, cfg, label=""):
    """驗證 → 下載（u2core.arbmodule.download：SYST:ERR? / *OPC? 檢查）"""
    cmds = _AM().download(dev, cfg, trig="BUS", check=True)
    dev.sim_load(cfg, label)
    return cmds


def start(dev, chans, output_on=True):
    """所有通道同一個 *TRG 同時開始；回傳送出的指令"""
    chans = sorted({int(c) for c in chans})
    if not chans:
        return []
    lst = ",".join(str(c) for c in chans)
    cmds = ([f"OUTP ON,(@{lst})"] if output_on else []) + [f"INIT:TRAN (@{lst})", "*TRG"]
    for c in cmds:
        dev.w(c)
    from u2core.robust import syst_err
    errs = syst_err(dev)
    if errs:
        raise RuntimeError("N6705 ARB 啟動回報錯誤：" + "；".join(errs))
    return cmds


def abort(dev, chans, output_off=True, keep=None):
    """安全中止：ABOR:TRAN → OUTP OFF（keep = {ch: V} 改成維持電壓）。每個指令都試，錯誤收集後一起丟"""
    keep = keep or {}
    errs = []
    for ch in sorted({int(c) for c in chans} | set(keep)):
        cmds = [f"ABOR:TRAN (@{ch})"]
        if ch in keep:
            cmds.append(f"VOLT {float(keep[ch]):g},(@{ch})")
        elif output_off:
            cmds.append(f"OUTP OFF,(@{ch})")
        for c in cmds:
            try:
                dev.w(c)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{c}：{e}")
    if errs:
        raise RuntimeError("；".join(errs))


def wait_done(dev, seconds, stop_evt=None):
    """等 ARB 跑完（時間 = ARB 長度；可被 stop_evt 中斷）"""
    t_end = time.time() + max(float(seconds), 0.0)
    while time.time() < t_end:
        if stop_evt is not None and stop_evt.is_set():
            return False
        time.sleep(min(0.05, max(t_end - time.time(), 0)))
    return True
