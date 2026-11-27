# -*- coding: utf-8 -*-
"""USB 自動重連：一台示波器一個 Link。

· 所有 I/O 走 Link.call(fn)（同一台一次一個指令；RLock）
· VISA 錯誤 / 逾時（pyvisa VisaIOError、OSError、TimeoutError、ConnectionError、模擬 SimDisconnect）
  → 標成「斷線」（UI 紅燈）→ 背景重試，退避 1、2、4、8 … 最長 30 s
· 重連成功 → 先跑 on_setup(dev)（對時、Aux Out / EXT、bus name、上次寫入的設定）→ 才標成「已連線」
· 擷取迴圈用 wait_up() 等待（暫停），中間的空檔寫紀錄
· 所有事件寫 log（紀錄分頁 ＋ log 檔）
"""
from __future__ import annotations

import threading
import time


class Disconnected(RuntimeError):
    """這台示波器目前斷線（呼叫端：暫停 / 等重連）"""


def is_link_error(e):
    """這個例外代表 USB / VISA 連線出問題（不是一般指令錯誤）"""
    if isinstance(e, (Disconnected, TimeoutError, ConnectionError, OSError)):
        return True
    try:
        import pyvisa
        if isinstance(e, pyvisa.errors.VisaIOError):
            return True
        if isinstance(e, getattr(pyvisa.errors, "InvalidSession", ())):
            return True
    except Exception:  # noqa: BLE001
        pass
    s = str(e).upper()
    return any(k in s for k in ("VI_ERROR_TMO", "VI_ERROR_CONN_LOST", "VI_ERROR_RSRC_NFOUND", "VI_ERROR_IO",
                                "VI_ERROR_INV_OBJECT", "VI_ERROR_NLISTENERS", "INVALID SESSION"))


class Link:
    BACKOFF = (1, 2, 4, 8, 15, 30)

    def __init__(self, model, factory, log=print, on_state=None, on_setup=None, name=""):
        self.model, self.factory, self.log = model, factory, log or (lambda *_: None)
        self.on_state = on_state or (lambda *_: None)          # (model, state, detail)
        self.on_setup = on_setup                                # (dev, first: bool) → None；丟例外 = 這次重連不算成功
        self.name = name or model
        self.dev = None
        self.state = "down"                                     # "up" / "down" / "connecting"
        self.detail = ""
        self.lock = threading.RLock()
        self._up = threading.Event()
        self._quit = threading.Event()
        self._th = None
        self.n_lost = 0
        self.t_lost = None
        self.first = True

    # -------------------------------------------------------------- 狀態
    @property
    def up(self):
        return self.state == "up"

    def _set(self, state, detail=""):
        self.state, self.detail = state, detail
        (self._up.set if state == "up" else self._up.clear)()
        try:
            self.on_state(self.model, state, detail)
        except Exception:  # noqa: BLE001
            pass

    def wait_up(self, timeout=None, stop_evt=None):
        """等到已連線（或 stop_evt）。回傳是否已連線"""
        t_end = None if timeout is None else time.time() + timeout
        while not self._up.is_set():
            if stop_evt is not None and stop_evt.is_set():
                return False
            if self._quit.is_set():
                return False
            if t_end is not None and time.time() >= t_end:
                return False
            self._up.wait(0.2)
        return True

    # -------------------------------------------------------------- 連線 / 重連
    def start(self):
        """背景連線（開程式自動連線）；失敗自動重試"""
        self._spawn()

    def _spawn(self):
        if self._th is not None and self._th.is_alive():
            return
        self._th = threading.Thread(target=self._loop, name=f"link-{self.model}", daemon=True)
        self._th.start()

    def _loop(self):
        k = 0
        while not self._quit.is_set():
            self._set("connecting", f"第 {k + 1} 次連線" if self.first else f"重連中（第 {k + 1} 次）")
            try:
                dev = self.factory()
                idn = dev.open()
                with self.lock:
                    self.dev = dev
                    if self.on_setup:
                        self.on_setup(dev, self.first)
                gap = "" if self.t_lost is None else f"，斷線 {time.time() - self.t_lost:.1f} s"
                self.log(f"[{self.model}] {'已連線' if self.first else '已重新連線'}：{idn}{gap}")
                self.first, self.t_lost = False, None
                self._set("up", idn)
                return
            except Exception as e:  # noqa: BLE001
                try:
                    if self.dev is not None:
                        self.dev.close()
                except Exception:  # noqa: BLE001
                    pass
                self.dev = None
                wait = self.BACKOFF[min(k, len(self.BACKOFF) - 1)]
                self.log(f"[{self.model}] 連線失敗（第 {k + 1} 次）：{e} → {wait} s 後重試")
                self._set("down", f"連線失敗，{wait} s 後重試（第 {k + 1} 次）：{e}")
                k += 1
                if self._quit.wait(wait):
                    return

    def lost(self, e):
        """I/O 出錯 → 斷線 → 背景重連"""
        with self.lock:
            if self.state != "up":
                return
            self.n_lost += 1
            self.t_lost = time.time()
            try:
                self.dev.close()
            except Exception:  # noqa: BLE001
                pass
            self.dev = None
        self.log(f"[{self.model}] ⚠ USB / VISA 中斷：{e} → 標成斷線，背景重連（第 {self.n_lost} 次斷線）")
        self._set("down", f"USB 中斷：{e}")
        self._spawn()

    def call(self, fn, *a, **kw):
        """fn(dev, *a) → 結果；連線錯誤 → lost() 並丟 Disconnected"""
        if not self.up:
            raise Disconnected(f"{self.model} 斷線中")
        with self.lock:
            dev = self.dev
            if dev is None:
                raise Disconnected(f"{self.model} 斷線中")
            try:
                return fn(dev, *a, **kw)
            except Exception as e:  # noqa: BLE001
                if is_link_error(e):
                    self.lost(e)
                    raise Disconnected(f"{self.model}：{e}") from e
                raise

    def w(self, cmd):
        return self.call(lambda d: d.w(cmd))

    def q(self, cmd):
        return self.call(lambda d: d.q(cmd))

    def close(self):
        self._quit.set()
        with self.lock:
            if self.dev is not None:
                try:
                    self.dev.close()
                except Exception:  # noqa: BLE001
                    pass
            self.dev = None
        self._set("down", "已關閉")
