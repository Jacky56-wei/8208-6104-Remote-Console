# -*- coding: utf-8 -*-
"""雙示波器波形工具（示範模式 = 模擬示波器跑完整流程；--real 接實機，尚未實機測試）

版型照 All in Test tool《UI設計風格說明.md》：u2core.theme.apply、工具列 ▶/■ + action_row、狀態列、
UnifiedBody（左：① 儀器連線 + ②③… 設定卡片；右：執行監看）。
DUT 規格（U.2／M.2＋機種／電源設計表＋總判定）放在最上方、① 儀器連線之上的固定列。子視窗（①–⑨、時序表／圖、規格判比表、紀錄、NVMe…）可點 ▾/▸ 收合、拖標題排序、拖分隔條改大小、⧉ 獨立視窗；狀態寫入 ui_layout.json。
⑨ 是設計規格判比表（跟隨 Autotest_tool u2core.dutspec／powerprofile／designspec.compare；U2_TOOL_DIR 沒有就用隨附 specs/dut_power）。
右側主畫面 = 兩部示波器「實圖」（SCREEN_DUMP）上下無縫接合：上 = 輔（SLAVE）、下 = 主（MASTER），時間格對齊。
「合一張」可與實圖同時展開：用讀回的波形資料在 PC 重畫。存檔一律存上下實圖。
連續擷取（capture.py）、上電時序（sequencing.py）、USB 自動重連（link.py）。

    U2_TOOL_DIR=<Autotest_tool 路徑> python -m scope_wave_saver.gui --demo
"""
from __future__ import annotations

import datetime as _dt
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk


_TOOL = os.environ.get("U2_TOOL_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(_TOOL, "u2core")) and _TOOL not in sys.path:
    sys.path.insert(0, _TOOL)

from .pmeas import MATH_MAX, NONE, PARAMS, default_rows, inst_max, parse as pm_parse, warnings as pm_warn  # noqa: E402

PASS_C, FAIL_C, ERR_C = ("#1a7f37", "#dafbe1"), ("#cf222e", "#ffebe9"), ("#9a6700", "#fff8c5")
RED = "#cf222e"
from .rails import MASTER_DEF, NAMES, NCH, SLAVE_DEF, UNIT_A, VNOM  # noqa: E402
from . import speccmp as SC  # noqa: E402
TITLE_M = {"8208HD": "WaveRunner 8208HD（8 CH）", "6104B": "HDO6104B（4 CH）"}
KEY = {"8208HD": "wr8000", "6104B": "hdo6104"}
ADDR = {"8208HD": "USB0::0x05FF::0x1023::5003N60268::0::INSTR", "6104B": "USB0::0x05FF::0x1023::5108N13350::0::INSTR"}
COLORS = ["#F2C744", "#E0457B", "#3FA9F5", "#3BB273", "#F28C28", "#9B59B6", "#1ABC9C", "#7F8C8D"]   # LeCroy C1–C8 色系


def _demo_waves(model, n=2000, vnom=None):
    """示範：上電時序（觸發在 t = 0；同 lecroy.sim_waves）。vnom 可覆寫標稱（⑨ 選了 M.2／設計表時）。"""
    from .lecroy import sim_waves
    return sim_waves(model, n=n, vnom=vnom)


class WaveSaverUI(tk.Tk):
    def __init__(self, demo=True, layout=""):
        super().__init__()
        self.title("雙示波器波形工具 v1.1（8208HD ＋ 6104B · MASTER / SLAVE）" + ("— 示範（模擬示波器）" if demo else ""))
        self.demo = demo
        self._place_window()
        self._style()
        self.v = {}
        self.q_ui = queue.Queue()                 # 背景執行緒 → UI（Tk 只在主執行緒更新）
        self.cap = None
        self.last_cfg = {}                        # 每台上次「寫入儀器設定」的內容（重連後重新套用）
        self.snap = {}                            # 背景重連用的 UI 快照（bus name 等；主執行緒更新）
        self.master_model = tk.StringVar(self, value=MASTER_DEF)
        self.bus = None
        self.links = {}
        self.psu = None                           # N6705 Link（ARB）
        from .foldui import LayoutStore
        self.ui_store = LayoutStore(layout)
        self.folds = {}
        self.nvme_tab = None
        self._build()
        self._make_links()
        self.after(100, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._quit)

    # ------------------------------------------------------------------ 樣式
    def _style(self):
        try:
            from u2core.theme import FAM, apply as _theme
            st = _theme(self)
        except Exception:  # noqa: BLE001
            FAM, st = "Noto Sans CJK TC", ttk.Style(self)
        import tkinter.font as tkfont
        for n in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(n).configure(family=FAM, size=10)
            except tk.TclError:
                pass
        self.fam = FAM
        st.configure("Head.TLabel", font=(FAM, 14, "bold"), foreground="#0E9BD8")
        st.configure("Muted.TLabel", foreground="#57606a")
        st.configure("Trig.TLabel", font=(FAM, 11, "bold"), foreground="#0E9BD8")

    def _place_window(self):
        """1920×1080 螢幕：預設約 1920×1040（留工作列）；Windows 再開最大化。"""
        self.update_idletasks()
        try:
            sw = int(self.winfo_screenwidth() or 1920)
            sh = int(self.winfo_screenheight() or 1080)
        except tk.TclError:
            sw, sh = 1920, 1080
        w = 1920 if sw >= 1900 else max(min(sw, 1920), 1280)
        h = 1040 if sh >= 1080 else max(sh - 40, 720)
        self.geometry(f"{w}x{h}+0+0")
        self.minsize(1280, 720)
        try:
            if sys.platform == "win32":
                self.state("zoomed")
        except tk.TclError:
            pass

    def _sv(self, k, d):
        self.v[k] = tk.StringVar(self, value=str(d))
        return self.v[k]

    def _bv(self, k, d):
        self.v[k] = tk.BooleanVar(self, value=bool(d))
        return self.v[k]

    # ------------------------------------------------------------------ 版面
    def _build(self):
        try:
            from u2core.ui import Tip, UnifiedBody, action_row
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"需要 All in Test tool 的 u2core（設 U2_TOOL_DIR）：{e}")
        self.Tip = Tip
        # ---- 工具列
        bar = ttk.Frame(self, padding=(10, 8, 10, 4))
        bar.pack(fill="x")
        self.btn_go = ttk.Button(bar, text="▶ 擷取＋存檔", style="Start.TButton", width=14, command=self.start_capture)
        self.btn_go.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="■ 停止", style="Stop.TButton", state="disabled", width=10, command=self.stop_capture)
        self.btn_stop.pack(side="left", padx=6)
        self.btn_arb = ttk.Button(bar, text="⚡ ARB＋擷取", width=12, command=self.start_arb_capture)
        self.btn_arb.pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="⟳ 實圖即時更新", width=13).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="📷 存上下實圖", width=13, command=lambda: self.do_save(data=False)).pack(side="left", padx=(0, 6))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=4)
        ttk.Combobox(bar, textvariable=self._sv("io_target", "兩台"), values=["兩台", "主 8208HD", "輔 6104B"],
                     state="readonly", width=9).pack(side="left")
        ttk.Button(bar, text="⤓ 讀回儀器設定", width=14, command=self.read_settings).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="⤒ 寫入儀器設定", width=14, command=self.write_settings).pack(side="left", padx=(4, 6))
        action_row(bar, self, omit=("save", "detail", "wiring", "report"), folder=lambda: None, export=lambda: None)
        self.lb_v = tk.Label(bar, text="已同步", font=(self.fam, 15, "bold"), fg=PASS_C[0], bg=PASS_C[1], width=8)
        self.lb_v.pack(side="right")
        ttk.Label(bar, text="主 / 輔觸發", style="Muted.TLabel").pack(side="right", padx=4)
        bar2 = ttk.Frame(self, padding=(10, 0, 10, 2))
        bar2.pack(fill="x")
        self.v_status = tk.StringVar(self, value="連線中…")
        self.lb_status = ttk.Label(bar2, textvariable=self.v_status, anchor="e", foreground="#0969da")
        self.lb_status.pack(side="right", fill="x", expand=True)
        if self.demo:
            ttk.Label(bar2, text="【示範模式 · 模擬示波器】", foreground="#9a6700").pack(side="left")
        ttk.Label(bar2, text="　子視窗：點 ▾/▸ 收合，拖標題改順序，拖分隔條改大小，⧉ 獨立視窗",
                  style="Muted.TLabel").pack(side="left")

        # 版型例外：不收進 v9 切換列（一次只看一個區塊）→ 維持左設定 / 右波形並排，觸發設定和兩台波形同時看得到
        from u2core import pagetpl
        if not getattr(pagetpl, "_ws_patched", False):
            _orig = pagetpl.install
            pagetpl.install = lambda b, _o=_orig: None if getattr(b, "_no_tpl", False) else _o(b)
            pagetpl._ws_patched = True
        body = UnifiedBody(self, left_ratio=0.42, monitor=True, monitor_title="雙示波器 執行監看")
        body._no_tpl = True
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.body = body
        try:
            from u2core import pagetpl
            self.after(500, lambda: pagetpl.set_header(body, "雙示波器波形",
                                                       "MASTER 8208HD ＋ SLAVE 6104B · Aux Out → EXT 觸發同步 · 波形存中控電腦"))
        except Exception:  # noqa: BLE001
            pass
        self._conn(body)
        self._fold_conn(body)
        self._dut_bar(body)
        page = body.page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        from .foldui import FoldStack
        stack = FoldStack(page, store=self.ui_store, group="left")
        stack.grid(row=0, column=0, sticky="nsew")
        self.left_stack = stack
        self._trigger(self._left_card("trig", "觸發設定（MASTER 觸發通道 / 準位 / 斜率）", 2))
        self._channels(self._left_card("ch", "通道 ＋ bus name", 3))
        self._pm(self._left_card("pm", "量測 P1–P24（每台示波器）", 4))
        self._save(self._left_card("save", "存檔（中控電腦）", 5))
        self._capture_card(self._left_card("cap", "連續擷取（觸發次數）", 6))
        self._seq_card(self._left_card("seq", "上電時序（PC 計算）", 7))
        self._arb_card(self._left_card("arb", "N6705 ARB 啟動＋擷取（Ramp / Brownout margin / Instant ON-OFF-ON）", 8))
        self._spec_card(self._left_card("spec", "設計規格判比（實測 vs 電源設計表）", 9))
        self._monitor(body)
        self.after(200, self._restore_sashes)
        self.after(400, lambda: self.body.pw.bind("<ButtonRelease-1>", lambda _e: self._persist_sash(), add="+"))


    def _left_card(self, key, title, num, default_open=True):
        from .foldui import FoldCard, numbered
        c = FoldCard(self.left_stack, key, numbered(num, title), store=self.ui_store,
                     default_open=default_open, tip=getattr(self, "Tip", None))
        self.left_stack.add(c)
        self.folds[key] = c
        return c.body

    def _fold_conn(self, body):
        from .foldui import adapt_section
        box = getattr(body.conn, "master", None)
        if box is None:
            return
        st = adapt_section(box, "conn", "① 儀器連線", store=self.ui_store, tip=getattr(self, "Tip", None))
        if st:
            self.folds["conn"] = st

    def _restore_sashes(self):
        sp = self.ui_store.sash("left_right")
        if not sp or not hasattr(self, "body"):
            return
        self.body._sash_done = True
        try:
            self.body.pw.sashpos(0, int(sp))
        except tk.TclError:
            pass

    def _persist_sash(self):
        try:
            self.ui_store.set_sash("left_right", self.body.pw.sashpos(0))
            if getattr(self, "seq_pw", None) is not None:
                self.ui_store.set_sash("seq_table_plot", self.seq_pw.sashpos(0))
        except tk.TclError:
            pass

    def _apply_fold(self, fold="", unfold=""):
        """測試 / 截圖：強制收合或展開指定 key（逗號分隔：conn,trig,ch,pm,save,cap,seq,arb,spec,shots,waves,seqv,specv,pmt,saved,log,nvme）。DUT 規格選擇列在最上方，不收進 fold。"""
        def keys(s):
            return [k.strip() for k in (s or "").split(",") if k.strip()]
        for k in keys(fold):
            self._set_fold(k, False)
        for k in keys(unfold):
            self._set_fold(k, True)

    def _set_fold(self, key, opened):
        # 時序監看卡 key 用 seqv，避免和左欄 ⑦ seq 撞名
        alias = {"timing": "seqv", "時序": "seqv", "合成": "waves", "合一": "waves",
                 "實圖": "shots", "紀錄": "log", "畫面": "shots"}
        key = alias.get(key, key)
        f = self.folds.get(key)
        if f is None:
            return
        if hasattr(f, "set_open"):
            f.set_open(opened)
        elif isinstance(f, dict) and "set_open" in f:
            f["set_open"](opened)

    # ---- ① 儀器連線（自動連線；對時 / Auto scale 背景做，只寫紀錄，失敗才顯示）
    def _conn(self, body):
        """① 單列狀態條：色點＋型號；位址收進 ⚙ 進階。背景自動連線不變。"""
        from u2core.ui import Dot
        from .n6705 import DEF_ADDR
        self.rows, self.st_var, self.idn_var, self.bg_warn = {}, {}, {}, {}
        for m in (MASTER_DEF, SLAVE_DEF):
            self._sv(f"addr_{m}", ADDR[m])
        self._sv("addr_N6705", DEF_ADDR)
        strip = ttk.Frame(body.conn)
        strip.pack(fill="x", pady=(0, 2))
        items = ((MASTER_DEF, "8208HD", "主"), (SLAVE_DEF, "6104B", "輔"), ("N6705", "N6705", "ARB"))
        for m, short, role in items:
            chip = ttk.Frame(strip)
            chip.pack(side="left", padx=(0, 14))
            st = self.st_var[m] = tk.StringVar(self, value="連線中…")
            self.idn_var[m] = tk.StringVar(self, value="")
            dot = Dot(chip, size=12)
            dot.pack(side="left", padx=(0, 4))
            ttk.Label(chip, text=short, font=(self.fam, 10, "bold")).pack(side="left")
            ttk.Label(chip, text=f"（{role}）", style="U.Muted.TLabel").pack(side="left")
            ttk.Label(chip, textvariable=st, style="U.Muted.TLabel").pack(side="left", padx=(4, 0))
            self.rows[m] = type("Chip", (), {"dot": dot, "model": m})()
            self.Tip(chip, lambda m=m: (self.idn_var[m].get() or self.st_var[m].get() or short))
        ttk.Button(strip, text="⚙ 位址 / 進階", style="U.Small.TButton",
                   command=self._conn_dialog).pack(side="right")
        warn = ttk.Frame(body.conn)
        warn.pack(fill="x")
        self.conn_warn = warn
        for m, _s, _r in items:
            self.bg_warn[m] = tk.Label(warn, text="", fg=FAIL_C[0], bg=FAIL_C[1], font=(self.fam, 9), padx=5)

    def _conn_dialog(self):
        if getattr(self, "_conn_win", None) is not None:
            try:
                if self._conn_win.winfo_exists():
                    self._conn_win.deiconify()
                    self._conn_win.lift()
                    return
            except tk.TclError:
                pass
        w = tk.Toplevel(self)
        w.title("儀器位址 / 進階")
        w.transient(self)
        self._conn_win = w
        ttk.Label(w, text="背景自動連線與 USB 重連會一直跑；這裡只改 VISA 位址，或示範模擬拔線。",
                  style="U.Muted.TLabel", wraplength=680, justify="left").pack(anchor="w", padx=12, pady=(10, 6))
        for m, title in ((MASTER_DEF, TITLE_M[MASTER_DEF]), (SLAVE_DEF, TITLE_M[SLAVE_DEF]),
                         ("N6705", "Keysight N6705（ARB 電源）")):
            lf = ttk.LabelFrame(w, text=f" {title} ", padding=8)
            lf.pack(fill="x", padx=12, pady=4)
            r = ttk.Frame(lf)
            r.pack(fill="x")
            ttk.Label(r, text="VISA").pack(side="left")
            ttk.Entry(r, textvariable=self.v[f"addr_{m}"], width=56,
                      font=("DejaVu Sans Mono", 9)).pack(side="left", padx=6, fill="x", expand=True)
            ttk.Label(lf, textvariable=self.idn_var[m], style="U.Muted.TLabel").pack(anchor="w", pady=(4, 0))
            if self.demo:
                ttk.Button(lf, text="模擬拔 USB 5 s（示範）", style="U.Small.TButton",
                           command=lambda m=m: self.sim_unplug(m, 5.0)).pack(anchor="w", pady=(4, 0))
        ttk.Button(w, text="關閉", command=w.destroy).pack(pady=10)

    def bg_result(self, model, task, ok, msg=""):
        """背景工作（對時 / Auto scale）結果：成功 → 只寫紀錄；失敗 → 紀錄 ＋ ① 該台下方紅字"""
        self.log(f"[{model}] {task}：{'OK' if ok else '失敗'}{('　' + msg) if msg else ''}")
        lb = self.bg_warn.get(model)
        if lb is None:
            return
        if ok:
            lb.pack_forget()
        else:
            lb.configure(text=f"⚠ {task}失敗：{msg}")
            lb.pack(side="left", padx=(8, 0))

    def log(self, text):
        line = f"{_dt.datetime.now():%H:%M:%S}  {text}"
        if threading.current_thread() is not threading.main_thread():
            self.q_ui.put(("log_line", line))
            return
        self._log_line(line)

    def _log_line(self, line):
        t = getattr(self, "log_txt", None)
        if t is not None:
            t.insert("end", line + "\n")
            t.see("end")
        try:
            d = os.path.join(os.path.expanduser("~"), "Desktop", "scope waveform")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "scope_wave_saver.log"), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ---- ② 觸發（主要）
    def _trigger(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="MASTER（主）").pack(side="left")
        cb = ttk.Combobox(top, textvariable=self.master_model, values=["8208HD", "6104B"], state="readonly", width=8)
        cb.pack(side="left", padx=(4, 10))
        ttk.Label(top, text="→ SLAVE（輔）6104B　可配對：8208HD＋6104B、8208HD＋8208HD、6104B＋6104B",
                  style="U.Muted.TLabel").pack(side="left")
        g = ttk.Frame(f, padding=(0, 6, 0, 2))
        g.pack(fill="x")
        ttk.Label(g, text="觸發通道", style="Trig.TLabel").grid(row=0, column=0, sticky="w")
        srcs = [f"C{i + 1}  {n}" for i, n in enumerate(NAMES[MASTER_DEF])]
        rstn = next((s for s in srcs if s.endswith("RSTn")), srcs[-1])
        lv0 = f"{VNOM['RSTn'] * 0.5:g}"
        cb_src = ttk.Combobox(g, textvariable=self._sv("trig_src", rstn), values=srcs, state="readonly", width=14,
                              font=(self.fam, 11))
        cb_src.grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(g, text="準位", style="Trig.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(g, textvariable=self._sv("trig_level", lv0), from_=-50, to=50, increment=0.01, width=8,
                    font=(self.fam, 11)).grid(row=0, column=3, sticky="w", padx=(6, 2))
        self.v_trig_hint = tk.StringVar(self, value=f"V（50 % × RSTn {VNOM['RSTn']:g} V）")
        ttk.Label(g, textvariable=self.v_trig_hint, style="U.Muted.TLabel").grid(row=0, column=4, sticky="w", padx=(0, 18))
        ttk.Label(g, text="斜率", style="Trig.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
        sf = ttk.Frame(g)
        sf.grid(row=1, column=1, columnspan=4, sticky="w", pady=(6, 0), padx=(6, 0))
        sv = self._sv("trig_slope", "POS")          # 預設 CH3 Ramp → Rising；切 Brownout / ON-OFF-ON 會改 Falling（可改）
        self._trig_slope_user = False
        for txt, val in (("↑ Rising 上升", "POS"), ("↓ Falling 下降", "NEG"), ("↕ Either 雙向", "EITHER")):
            ttk.Radiobutton(sf, text=txt, value=val, variable=sv,
                            command=lambda: setattr(self, "_trig_slope_user", True)).pack(side="left", padx=(0, 14))
        cb_src.bind("<<ComboboxSelected>>", self._on_trig_src)
        ttk.Label(g, text="模式").grid(row=2, column=0, sticky="w", pady=(6, 0))
        hf = ttk.Frame(g)
        hf.grid(row=2, column=1, columnspan=4, sticky="w", pady=(6, 0), padx=(6, 0))
        ttk.Combobox(hf, textvariable=self._sv("trig_mode", "SINGLE"), values=["SINGLE", "NORM", "AUTO"],
                     state="readonly", width=8).pack(side="left")
        ttk.Label(hf, text="  T/div").pack(side="left")
        ttk.Combobox(hf, textvariable=self._sv("tdiv", "0.001"), values=["0.0001", "0.0005", "0.001", "0.002", "0.005"],
                     width=8).pack(side="left", padx=(2, 0))
        ttk.Label(hf, text="s  Delay").pack(side="left")
        ttk.Entry(hf, textvariable=self._sv("delay", "-0.003"), width=8).pack(side="left", padx=(2, 0))
        ttk.Label(hf, text="s").pack(side="left")
        bf = ttk.Frame(f, padding=(0, 6, 0, 0))
        bf.pack(fill="x")
        ttk.Button(bf, text="⤒ 套用觸發到兩台", style="Primary.TButton", command=self.write_settings).pack(side="left")
        ttk.Button(bf, text="▶ SINGLE（輔先 arm，主再 arm）", command=lambda: self.start_capture(count=1)).pack(side="left", padx=6)
        ttk.Button(bf, text="⤓ 讀回示波器目前觸發", style="U.Small.TButton").pack(side="left")
        ttk.Label(bf, text="預設主 C8 RSTn；斜率跟 ⑧ 範本走（可改）　輔仍走 Aux Out → EXT",
                  style="U.Muted.TLabel").pack(side="left", padx=8)
        # 主 / 輔同步固定在背景做（主 8208HD AUX OUT ──BNC── 輔 6104B EXT；EX10、0.5 V、↑；輔時基跟主），UI 不顯示
        self._bv("sync_on", True)
        self._bv("align", True)

    def _on_trig_src(self, _e=None):
        """換觸發通道 → 準位改成該通道標稱的 50 %（RSTn 1.8 V → 0.9 V）"""
        src = self.v["trig_src"].get()
        parts = src.split()
        nm = parts[-1] if len(parts) >= 2 else ""
        vnom = self._rail_vnom(nm)
        if vnom:
            self.v["trig_level"].set(f"{vnom * 0.5:g}")
            self.v_trig_hint.set(f"V（50 % × {nm} {vnom:g} V）")
        else:
            self.v_trig_hint.set("V")
        self._note()

    def _slope_for_tpl(self, tpl):
        return "POS" if tpl == "RAMP" else "NEG" if tpl in ("BROWNOUT", "ONOFFON") else None

    def _sync_trig_from_tpl(self, force=False):
        """斜率跟目前啟用的 ARB 範本走：Ramp → Rising，Brownout / ON-OFF-ON → Falling。使用者改過斜率則不覆蓋。"""
        if not force and getattr(self, "_trig_slope_user", False):
            return
        tpl = (self.v["arb_tpl"].get() if self.v.get("arb_tpl") else "") or ""
        sl = self._slope_for_tpl(tpl)
        if sl:
            self.v["trig_slope"].set(sl)
            self._trig_slope_user = False
        self._note()

    # ---- ③ 通道＋bus name
    def _channels(self, f):
        self._vnom_lbl = {}
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="bus name 寫到").pack(side="left")
        ttk.Combobox(top, textvariable=self._sv("name_mode", "通道名稱 Alias（不支援 → Label）"),
                     values=["通道名稱 Alias（不支援 → Label）", "波形 Label", "Alias + Label"], state="readonly",
                     width=26).pack(side="left", padx=4)
        ttk.Button(top, text="⤒ 寫入 bus name", style="U.Small.TButton").pack(side="left", padx=4)
        ttk.Button(top, text="⤓ 讀回", style="U.Small.TButton").pack(side="left")
        nb = ttk.Notebook(f)
        nb.pack(fill="x", pady=(4, 0))
        for m, role in ((MASTER_DEF, "主"), (SLAVE_DEF, "輔")):
            t = ttk.Frame(nb, padding=4)
            nb.add(t, text=f"  {role} {m}  ")
            heads = ("存", "CH", "bus name", "標稱 V", "V/div", "Offset", "示波器上名稱")
            for j, h in enumerate(heads):
                tk.Label(t, text=h, bg="#0E9BD8", fg="white", font=(self.fam, 9, "bold"), padx=4).grid(
                    row=0, column=j, sticky="we", padx=1)
            for i, nm in enumerate(NAMES[m]):
                rr = i + 1
                ttk.Checkbutton(t, variable=self._bv(f"en_{m}_{i}", True)).grid(row=rr, column=0)
                tk.Label(t, text=f"C{i + 1}", fg=COLORS[i], bg="#FFFFFF", font=(self.fam, 10, "bold")).grid(row=rr, column=1)
                ttk.Entry(t, textvariable=self._sv(f"nm_{m}_{i}", nm), width=12).grid(row=rr, column=2, padx=2, pady=1)
                vn = VNOM.get(nm, 0)
                lb = ttk.Label(t, text=f"{vn:g}" if vn else ("電流 A" if nm in UNIT_A else "—"))
                lb.grid(row=rr, column=3)
                self._vnom_lbl[(m, i)] = lb
                from .demo_scale import vdiv_for
                ttk.Label(t, text=vdiv_for(vn)).grid(row=rr, column=4)
                ttk.Label(t, text=f"{-3.5 * float(vdiv_for(vn).split()[0]) * (1e-3 if 'mV' in vdiv_for(vn) else 1):.3g} V"
                          if vn else "0 V").grid(row=rr, column=5)
                ttk.Label(t, text=nm + " ✓", foreground=PASS_C[0]).grid(row=rr, column=6, sticky="w", padx=4)

    # ---- ④ P1–P24
    def _pm(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Button(top, text="預設（每 CH MAX / MIN）", style="U.Small.TButton").pack(side="left")
        ttk.Button(top, text="⤒ 寫入示波器", style="U.Small.TButton").pack(side="left", padx=4)
        ttk.Button(top, text="⤓ 讀回示波器", style="U.Small.TButton").pack(side="left")
        ttk.Label(top, text="  上限（datasheet）8208HD：P1–P12、F1–F12｜6104B：P1–P8、F1–F8；超過 / 不支援 → PC 計算，不送示波器",
                  style="U.Muted.TLabel").pack(side="left")
        nb = ttk.Notebook(f)
        nb.pack(fill="x", pady=(4, 0))
        for m, role in ((MASTER_DEF, "主"), (SLAVE_DEF, "輔")):
            t = ttk.Frame(nb, padding=4)
            nb.add(t, text=f"  {role} {m}  ")
            rows = default_rows([f"C{i + 1}" for i in range(NCH[m])])
            if m == MASTER_DEF:
                rows[16:20] = [{"on": True, "src1": "C1", "src2": NONE, "level": "10,90", "param": "RISE"},
                               {"on": True, "src1": "C1", "src2": "C2", "level": "90,90", "param": "DTLEV"},
                               {"on": True, "src1": "C2", "src2": "C3", "level": "90,90", "param": "DTLEV"},
                               {"on": True, "src1": "C3", "src2": "C4", "level": "90,90", "param": "DTLEV"}]
            if m == SLAVE_DEF:
                rows[8] = {"on": True, "src1": "C2", "src2": "C4", "level": "", "param": "W_MEAN"}
                rows[9] = {"on": True, "src1": "C2", "src2": "C4", "level": "", "param": "W_MAX"}
            chs = [f"C{i + 1}" for i in range(NCH[m])]
            im = inst_max(m)
            where = {sl["p"]: sl for sl in pm_parse(rows, m, NCH[m])}
            for half in (0, 1):
                gx = half * 8
                for j, h in enumerate(("P", "", "前 CH", "後 CH", "準位 %", "參數", "")):
                    tk.Label(t, text=h, bg="#0E9BD8", fg="white", font=(self.fam, 9, "bold")).grid(
                        row=0, column=gx + j, sticky="we", padx=(1 if j else (0 if half == 0 else 14), 1))
                for k in range(12):
                    i = half * 12 + k
                    r = rows[i]
                    rr = k + 1
                    ttk.Label(t, text=f"P{i + 1}").grid(row=rr, column=gx + 0, padx=(0 if half == 0 else 14, 0))
                    ttk.Checkbutton(t, variable=self._bv(f"pm_on_{m}_{i}", r["on"])).grid(row=rr, column=gx + 1)
                    ttk.Combobox(t, textvariable=self._sv(f"pm_s1_{m}_{i}", r["src1"]), values=chs, width=4,
                                 state="readonly").grid(row=rr, column=gx + 2, padx=1)
                    ttk.Combobox(t, textvariable=self._sv(f"pm_s2_{m}_{i}", r["src2"]), values=[NONE] + chs, width=4,
                                 state="readonly").grid(row=rr, column=gx + 3, padx=1)
                    ttk.Entry(t, textvariable=self._sv(f"pm_lv_{m}_{i}", r["level"]), width=7).grid(row=rr, column=gx + 4, padx=1)
                    ttk.Combobox(t, textvariable=self._sv(f"pm_p_{m}_{i}", r["param"]), values=PARAMS, width=7,
                                 state="readonly").grid(row=rr, column=gx + 5, padx=1)
                    sl = where.get(i + 1)
                    on_sc = (sl["where"] == "scope") if sl else i < im
                    lb = ttk.Label(t, text="示波器" if on_sc else "PC 計算", foreground="#1a7f37" if on_sc else "#9a6700")
                    lb.grid(row=rr, column=gx + 6, padx=(2, 0), sticky="w")
                    if sl and sl.get("why"):
                        self.Tip(lb, sl["why"])
            ws = pm_warn(list(where.values()))
            ttk.Label(t, text=("⚠ 寫入時：" + "；".join(ws)) if ws else "✓ 全部可寫入示波器", wraplength=760, justify="left",
                      foreground="#9a6700" if ws else "#1a7f37").grid(row=13, column=0, columnspan=16, sticky="w", pady=(4, 0))

    # ---- ⑤ 存檔
    def _save(self, f):
        r = ttk.Frame(f)
        r.pack(fill="x")
        ttk.Label(r, text="存到").pack(side="left")
        ttk.Entry(r, textvariable=self._sv("folder", r"C:\Users\Jacky\Desktop\scope waveform"), width=44).pack(side="left", padx=4)
        ttk.Button(r, text="…", width=3).pack(side="left")
        ttk.Label(r, text="  檔名前綴").pack(side="left")
        ttk.Entry(r, textvariable=self._sv("prefix", "PWRON_DUT1"), width=14).pack(side="left", padx=4)
        r2 = ttk.Frame(f)
        r2.pack(fill="x", pady=(4, 0))
        ttk.Label(r2, text="格式").pack(side="left")
        for k, txt in (("png", "畫面 PNG"), ("trc", "原始 .trc"), ("csv", "CSV（時間, 電壓）"), ("npz", "NPZ"),
                       ("pm", "P 量測 CSV")):
            ttk.Checkbutton(r2, text=txt, variable=self._bv(f"fmt_{k}", k != "npz")).pack(side="left", padx=(6, 0))
        r3 = ttk.Frame(f)
        r3.pack(fill="x", pady=(4, 0))
        ttk.Label(r3, text="畫面存檔").pack(side="left")
        self.v_save_note = tk.StringVar(self, value="")
        ttk.Label(r3, textvariable=self.v_save_note, foreground="#0958d9").pack(side="left", padx=6)
        ttk.Label(f, text="每次一個資料夾：<前綴>_<日期時間>\\  主_8208HD_C1_CORE.trc / .csv、輔_6104B_C1_VIN.csv、主_8208HD.png、"
                          "pmeas.csv、info.json（IDN、觸發、時基、bus name）",
                  style="U.Muted.TLabel").pack(anchor="w", pady=(4, 0))

    # ---- 右：執行監看（兩台示波器實圖同時看）
    def _right_card(self, key, title, default_open=True, weight=1.0):
        from .foldui import FoldCard
        c = FoldCard(self.right_stack, key, title, store=self.ui_store,
                     default_open=default_open, tip=getattr(self, "Tip", None))
        self.right_stack.add(c, weight=weight)
        self.folds[key] = c
        return c.body

    def _monitor(self, body):
        """右欄改為可收合、可拉分隔條、可拖排序的子視窗（不再只用分頁一次只看一個）。"""
        from .foldui import SashStack
        try:
            body.right_nb.pack_forget()
        except tk.TclError:
            pass
        stack = SashStack(body.right, store=self.ui_store, group="right",
                          defaults={"shots": 3.2, "waves": 1.6, "seqv": 2.0, "specv": 1.6, "pmt": 1.0,
                                    "saved": 0.8, "log": 1.1, "nvme": 1.0})
        stack.pack(fill="both", expand=True, padx=(6, 0))
        self.right_stack = stack
        self.mon_nb = None
        self.nvme_tab = None
        # -- 兩台實機畫面
        rf = self._right_card("shots", "兩台實機畫面（實圖，輔上／主下）", default_open=True, weight=3.2)
        self.v_lay = tk.StringVar(self, value="stack")
        tb = ttk.Frame(rf)
        tb.pack(fill="x")
        ttk.Label(tb, text="更新").pack(side="left")
        ttk.Combobox(tb, textvariable=self._sv("shot_every", "1 s"), values=["手動", "0.5 s", "1 s", "2 s", "5 s"],
                     state="readonly", width=6).pack(side="left", padx=2)
        ttk.Checkbutton(tb, text="即時更新", variable=self._bv("live", True)).pack(side="left", padx=6)
        ttk.Checkbutton(tb, text="擷取中暫停（不搶 USB）", variable=self._bv("pause_busy", True)).pack(side="left")
        hd = tk.Frame(rf, bg="#0E9BD8")
        hd.pack(fill="x", pady=(4, 0))
        self.lb_head = tk.Label(hd, text="", bg="#0E9BD8", fg="white", font=(self.fam, 10, "bold"))
        self.lb_head.pack(side="left")
        for txt, cmd in (("⤢ 放大", None), ("📷 存這張（上下實圖）", lambda: self.do_save(data=False))):
            tk.Button(hd, text=txt, command=cmd, bg="#FFFFFF", fg="#0E9BD8", relief="flat", font=(self.fam, 9),
                      padx=6).pack(side="right", padx=3, pady=2)
        tk.Label(hd, text="SCREEN_DUMP 21:00:12" + ("　示意圖（未連實機）" if self.demo else ""), bg="#0E9BD8", fg="white",
                 font=(self.fam, 9)).pack(side="right", padx=6)
        self.shot_area = tk.Frame(rf, bg="#000000")
        self.shot_area.pack(fill="both", expand=True)
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        self.shots = {}
        for m in (SLAVE_DEF, MASTER_DEF):
            self.shots[m] = {"src": os.path.join(here, f"placeholder_{m}.png"), "tk": None}
        self.stack_lab = tk.Label(self.shot_area, bg="#000000", bd=0, highlightthickness=0)
        self.stack_tk = None
        self.shot_area.bind("<Configure>", lambda _e: self.after_idle(self._fit_shots))
        # -- 合一張波形（預設收合）
        wf = self._right_card("waves", "合一張（PC 合成波形）", default_open=False, weight=1.6)
        tb2 = ttk.Frame(wf)
        tb2.pack(fill="x")
        self.v_view = tk.StringVar(self, value="merge")
        ttk.Checkbutton(tb2, text="每通道分開排（stack）", variable=self._bv("stack", True), command=self._draw).pack(side="left")
        ttk.Label(tb2, text="只看：兩台讀回的波形資料（WAVEFORM? ALL）畫在同一時間軸，觸發 t = 0 對齊；存檔一律存上下實圖",
                  style="U.Muted.TLabel").pack(side="left", padx=8)
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        self.fig = Figure(figsize=(11, 8.5), dpi=96, facecolor="#FFFFFF")
        self.canvas = FigureCanvasTkAgg(self.fig, wf)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.waves = {m: _demo_waves(m) for m in (MASTER_DEF, SLAVE_DEF)}
        self._draw()
        # -- 時序（預設收合；擷取後會展開）
        seqf = self._right_card("seqv", "上 / 下電時序（表＋圖，可拉分隔條）", default_open=False, weight=2.0)
        self._seq_tab(seqf)
        # -- ⑨ 規格判比結果（預設收合；選了機種或擷取後展開看）
        spf = self._right_card("specv", "設計規格判比（實測 vs 電源設計表）", default_open=False, weight=1.6)
        self._spec_tab(spf)
        # -- P 量測（預設收合）
        pf = self._right_card("pmt", "P 量測結果", default_open=False, weight=1.0)
        tv = ttk.Treeview(pf, columns=("sc", "p", "lab", "inst", "pc", "pct", "ok"), show="headings", height=8)
        for c, h, w in (("sc", "示波器", 80), ("p", "P", 40), ("lab", "量測", 220), ("inst", "示波器值", 110),
                        ("pc", "PC 值", 110), ("pct", "差異 %", 70), ("ok", "比對", 70)):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="center")
        tv.pack(fill="both", expand=True)
        self.tv_pm = tv
        # -- 存檔結果（預設收合）
        sf = self._right_card("saved", "存檔結果", default_open=False, weight=0.8)
        self.saved_txt = tk.Text(sf, font=("DejaVu Sans Mono", 9), height=8)
        self.saved_txt.pack(fill="both", expand=True)
        # -- 紀錄（預設展開，可跟畫面一起看、拉分隔條改高度）
        lg = self._right_card("log", "紀錄", default_open=True, weight=1.1)
        self.log_txt = tk.Text(lg, bg="#0d1117", fg="#c9d1d9", font=("DejaVu Sans Mono", 9), height=8)
        self.log_txt.pack(fill="both", expand=True)
        self._layout_shots()

    def _tab_changed(self, _e=None):
        self._note()

    def _note(self):
        self.v_save_note.set("上下實圖（輔 上 / 主 下，裁到格線區、無縫接合）→ 畫面_上下.png；另存兩台原始實圖")
        if hasattr(self, "lb_head"):
            sl = {"POS": "↑", "NEG": "↓", "EITHER": "↕"}.get(self.v.get("trig_slope") and self.v["trig_slope"].get(), "↓")
            src = self.v["trig_src"].get() if self.v.get("trig_src") else "C8  RSTn"
            lv = self.v["trig_level"].get() if self.v.get("trig_level") else "0.9"
            self.lb_head.configure(text=f"  上：輔 SLAVE  HDO6104B（EXT 觸發 ✓）　／　下：主 MASTER  WaveRunner 8208HD（{src} {sl} {lv} V ✓）")

    def _layout_shots(self):
        self.stack_lab.grid(row=0, column=0, sticky="nsew")
        self.shot_area.columnconfigure(0, weight=1)
        self.shot_area.rowconfigure(0, weight=1)
        self._note()
        self.after(50, self._fit_shots)

    def _fit_shots(self):
        from PIL import Image, ImageTk
        from . import composite as CP
        W, H = max(self.shot_area.winfo_width(), 100), max(self.shot_area.winfo_height(), 100)
        s_ = Image.open(self.shots[SLAVE_DEF]["src"]).convert("RGB")
        m_ = Image.open(self.shots[MASTER_DEF]["src"]).convert("RGB")
        im = CP.stack(s_, m_, SLAVE_DEF, MASTER_DEF, legend="", tags=("輔 SLAVE", "主 MASTER"))
        im = im.crop((0, 34, im.width, im.height))          # 畫面上不重複標題列（共用標題列已在上方）
        k = min(W / im.width, H / im.height)
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
        self.stack_tk = ImageTk.PhotoImage(im)
        self.stack_lab.configure(image=self.stack_tk)

    # ------------------------------------------------------------------ 存檔（畫面 = 上下無縫實圖）
    def save_dir(self):
        base = self.v["folder"].get().strip()
        if not base or not os.path.isdir(os.path.dirname(base.rstrip("\\/")) or "."):
            base = os.path.join(os.path.expanduser("~"), "Desktop", "scope waveform")
        name = "_".join(x for x in (self.v["prefix"].get().strip(), _dt.datetime.now().strftime("%Y%m%d_%H%M%S")) if x)
        return os.path.join(base, name)

    def do_save(self, data=True, folder=None):
        """畫面：上下無縫實圖一張 ＋ 兩台原始實圖；data=True 另存波形資料（照 ⑤ 格式；原型未接實機）"""
        from . import composite as CP
        lay = "stack"
        d = folder or self.save_dir()
        os.makedirs(d, exist_ok=True)
        sp, mp = self.shots[SLAVE_DEF]["src"], self.shots[MASTER_DEF]["src"]
        legend = ("輔：" + "  ".join(f"C{i + 1} {n}" for i, n in enumerate(NAMES[SLAVE_DEF]))
                  + "　｜　主：" + "  ".join(f"C{i + 1} {n}" for i, n in enumerate(NAMES[MASTER_DEF]))
                  + f"　｜　{self.v['tdiv'].get()} s/div  Delay {self.v['delay'].get()} s")
        trig = f"觸發：主 {self.v['trig_src'].get().split()[0]} {self.v['trig_slope'].get()} {self.v['trig_level'].get()} V → Aux Out → 輔 EXT"
        out = os.path.join(d, f"畫面_{CP.LAYOUTS[lay]}.png")
        CP.save(out, lay, sp, mp, right=trig, legend=legend,
                slave_model=SLAVE_DEF, master_model=MASTER_DEF)
        saved = [out]
        if self.v.get("fmt_png") is None or self.v["fmt_png"].get():
            import shutil
            for m, role in ((SLAVE_DEF, "輔"), (MASTER_DEF, "主")):
                dst = os.path.join(d, f"{role}_{m}_實圖.png")
                shutil.copyfile(self.shots[m]["src"], dst)
                saved.append(dst)
        self.last_saved = saved
        return saved

    # ---- ⑥ 連續擷取
    def _capture_card(self, f):
        r = ttk.Frame(f)
        r.pack(fill="x")
        ttk.Label(r, text="觸發次數 N").pack(side="left")
        ttk.Spinbox(r, textvariable=self._sv("cap_n", "10"), from_=0, to=100000, width=7).pack(side="left", padx=4)
        ttk.Label(r, text="（0 = 一直到 ■ 停止）　等觸發逾時").pack(side="left")
        ttk.Entry(r, textvariable=self._sv("cap_to", "0"), width=6).pack(side="left", padx=4)
        ttk.Label(r, text="s（0 = 一直等；逾時 → 重新 arm 同一個序號）").pack(side="left")
        r2 = ttk.Frame(f)
        r2.pack(fill="x", pady=(6, 0))
        self.pb = ttk.Progressbar(r2, mode="determinate", length=260, maximum=10)
        self.pb.pack(side="left")
        self.v_prog = tk.StringVar(self, value="0 / 10　·　最後觸發 —　·　待命")
        ttk.Label(r2, textvariable=self.v_prog, font=(self.fam, 10, "bold"), foreground="#0958d9").pack(side="left", padx=8)
        ttk.Label(f, text="每次：輔先 arm → 主再 arm（SINGLE）→ 等主觸發、輔跟著觸發 → 兩台 idle 才讀 → 存一組（序號資料夾 0001_時間\\、"
                          "檔名 0001_…）＋ 同一個 summary.csv。斷線 → 暫停，重連後重做這一次（空檔寫紀錄 / summary 備註）",
                  style="U.Muted.TLabel", wraplength=780, justify="left").pack(anchor="w", pady=(4, 0))

    # ---- ⑦ 上 / 下電時序
    def _seq_card(self, f):
        from . import sequencing as SQ
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="方向").pack(side="left")
        ttk.Combobox(top, textvariable=self._sv("seq_dir", "自動判斷"), values=["自動判斷", "上電", "下電"], state="readonly",
                     width=8).pack(side="left", padx=(4, 10))
        names = [x["name"] for x in SQ.default_rails() if x["kind"] != "current"]
        ttk.Label(top, text="參考 rail").pack(side="left")
        ttk.Combobox(top, textvariable=self._sv("seq_ref", "VIN"), values=names, state="readonly", width=8).pack(side="left", padx=(4, 10))
        ttk.Label(top, text="上 / 下電時間 =").pack(side="left")
        ttk.Combobox(top, textvariable=self._sv("seq_basis", "高門檻（power good）"),
                     values=["高門檻（power good）", "低門檻（開始動）"], state="readonly", width=17).pack(side="left", padx=(4, 10))
        ttk.Label(top, text="輔時間修正").pack(side="left")
        ttk.Entry(top, textvariable=self._sv("seq_skew", "0"), width=5).pack(side="left", padx=2)
        ttk.Label(top, text="ns").pack(side="left")
        ttk.Checkbutton(top, text="每次存 CSV ＋ PNG", variable=self._bv("seq_save", True)).pack(side="left", padx=(10, 0))
        t = ttk.Frame(f)
        t.pack(fill="x", pady=(4, 0))
        heads = ("用", "示波器", "CH", "rail", "類型", "低 %", "高 %", "邏輯門檻 V", "上電延遲 下限", "上限", "下電延遲 下限", "上限")
        for j, h in enumerate(heads):
            tk.Label(t, text=h, bg="#0E9BD8", fg="white", font=(self.fam, 9, "bold"), padx=4).grid(row=0, column=j, sticky="we", padx=1)
        ex = {"CORE": ("0.2", "1.0", "-4", "-2"), "RSTn": ("3", "6", "-6", "-3")}      # 範例限制 ms（對參考 rail；可清掉）
        self.seq_rails = SQ.default_rails()
        for k, x in enumerate(self.seq_rails):
            rr = k + 1
            ttk.Checkbutton(t, variable=self._bv(f"sq_on_{k}", True)).grid(row=rr, column=0)
            ttk.Label(t, text=f"{'主' if x['model'] == MASTER_DEF else '輔'} {x['model']}").grid(row=rr, column=1, padx=2)
            ttk.Label(t, text=f"C{x['ch']}").grid(row=rr, column=2)
            ttk.Label(t, text=x["name"], font=(self.fam, 9, "bold")).grid(row=rr, column=3, sticky="w", padx=2)
            ttk.Label(t, text={"rail": "電源", "logic": "邏輯", "current": "電流 A"}[x["kind"]]).grid(row=rr, column=4)
            ttk.Entry(t, textvariable=self._sv(f"sq_lo_{k}", f"{x['lo_pct']:g}"), width=5).grid(row=rr, column=5, padx=1)
            ttk.Entry(t, textvariable=self._sv(f"sq_hi_{k}", f"{x['hi_pct']:g}"), width=5).grid(row=rr, column=6, padx=1)
            e = ttk.Entry(t, textvariable=self._sv(f"sq_lg_{k}", "" if x["logic_v"] is None else f"{x['logic_v']:g}"), width=7)
            e.grid(row=rr, column=7, padx=1)
            if x["kind"] != "logic":
                e.configure(state="disabled")
            lo, hi, dlo, dhi = ex.get(x["name"], ("", "", "", ""))
            for j, (key, val) in enumerate((("sq_min", lo), ("sq_max", hi), ("sq_dmin", dlo), ("sq_dmax", dhi))):
                e = ttk.Entry(t, textvariable=self._sv(f"{key}_{k}", val), width=7)
                e.grid(row=rr, column=8 + j, padx=1)
                if x["kind"] == "current":
                    e.configure(state="disabled")
        ttk.Label(f, text="PC 用存下來的波形算（兩台同一條時間軸，t = 0 = 主觸發）：每條 rail 10 % / 90 % 穿越時間、上升 / 下降時間、"
                          "slew rate（V/ms；電流 A/ms；下電為負）、順序、對參考 rail 的延遲；邏輯訊號用邏輯門檻；上電 / 下電各一組下限 / 上限（ms，對參考 rail），有填才判 PASS / FAIL。"
                          "方向自動判斷 = 看波形頭尾是升還是降",
                  style="U.Muted.TLabel", wraplength=780, justify="left").pack(anchor="w", pady=(4, 0))

    def _rails_from_ui(self):
        out = []

        def fnum(k):
            try:
                v = self.v[k].get().strip()
                return float(v) if v else None
            except (ValueError, KeyError):
                return None
        for k, x in enumerate(self.seq_rails):
            r = dict(x)
            i = int(x["ch"]) - 1
            nm = self.v.get(f"nm_{x['model']}_{i}")
            r["name"] = (nm.get().strip() if nm else "") or x["name"]
            vn = self._rail_vnom(r["name"], r.get("nom") or 0)
            if vn:
                r["nom"] = vn
                if r["kind"] == "logic" and not r.get("logic_v"):
                    r["logic_v"] = round(0.7 * vn, 3)
            r["on"] = self.v[f"sq_on_{k}"].get() and self.v[f"en_{x['model']}_{i}"].get()
            r["lo_pct"] = fnum(f"sq_lo_{k}") or 10.0
            r["hi_pct"] = fnum(f"sq_hi_{k}") or 90.0
            r["logic_v"] = fnum(f"sq_lg_{k}") if x["kind"] == "logic" else None
            r["min_ms"], r["max_ms"] = fnum(f"sq_min_{k}"), fnum(f"sq_max_{k}")
            r["dn_min_ms"], r["dn_max_ms"] = fnum(f"sq_dmin_{k}"), fnum(f"sq_dmax_{k}")
            out.append(r)
        return out

    def _seq_opts(self):
        d = {"自動判斷": "auto", "上電": "up", "下電": "down"}.get(self.v["seq_dir"].get(), "auto")
        basis = "lo" if self.v["seq_basis"].get().startswith("低") else "hi"
        try:
            skew = float(self.v["seq_skew"].get() or 0) * 1e-9
        except ValueError:
            skew = 0.0
        return {"direction": d, "basis": basis, "skew_s": skew, "ref": self.v["seq_ref"].get()}

    # ---- 右：上 / 下電時序分頁
    def _seq_tab(self, f):
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        from . import sequencing as SQ
        pw = ttk.PanedWindow(f, orient="vertical")
        pw.pack(fill="both", expand=True)
        top, bot = ttk.Frame(pw), ttk.Frame(pw)
        pw.add(top, weight=1)
        pw.add(bot, weight=2)
        self.seq_pw = pw
        hd = ttk.Frame(top)
        hd.pack(fill="x")
        self.v_seq_sum = tk.StringVar(self, value="")
        ttk.Label(hd, textvariable=self.v_seq_sum, font=(self.fam, 10, "bold"), foreground="#0E9BD8").pack(side="left")
        ttk.Button(hd, text="⟳ 用目前設定重算", style="U.Small.TButton", command=self._seq_recalc).pack(side="right")
        cols = ("dir", "ord", "sc", "ch", "rail", "ton", "rise", "slew", "dly", "lim", "ok", "note")
        heads = ("方向", "順序", "示波器", "CH", "rail", "上/下電 ms", "10↔90% ms", "slew rate", "延遲 ms", "限制 ms", "判定", "備註")
        tv = ttk.Treeview(top, columns=cols, show="headings", height=8)
        for c, h, w in zip(cols, heads, (40, 36, 80, 32, 64, 70, 72, 100, 64, 76, 48, 120)):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="center", stretch=c == "note")
        tv.tag_configure("FAIL", foreground="#cf222e")
        tv.tag_configure("PASS", foreground="#1a7f37")
        tv.pack(fill="both", expand=True)
        self.tv_seq = tv
        self.fig_seq = Figure(figsize=(11, 5.6), dpi=96, facecolor="#FFFFFF")
        self.cv_seq = FigureCanvasTkAgg(self.fig_seq, bot)
        self.cv_seq.get_tk_widget().pack(fill="both", expand=True)
        self._SQ = SQ
        sp = self.ui_store.sash("seq_table_plot")
        if sp:
            self.after(250, lambda v=int(sp): self._safe_sash(self.seq_pw, 0, v))
        pw.bind("<ButtonRelease-1>", lambda _e: self._persist_sash() or self.ui_store.save(), add="+")
        self.after(300, self._seq_recalc)

    def _safe_sash(self, pw, i, val):
        try:
            pw.sashpos(i, int(val))
        except tk.TclError:
            pass

    def _waves_flat(self):
        return {(m, ch): w for m, ws in self.waves.items() for ch, w in ws.items()}

    def _seq_recalc(self):
        o = self._seq_opts()
        wv = self._waves_flat()
        rows = self._SQ.analyze(wv, self._rails_from_ui(), ref=o["ref"], basis=o["basis"], slave_skew_s=o["skew_s"],
                                direction=o["direction"])
        self._seq_show(rows, wv, getattr(self, "_seq_title", ""))
        self._spec_refresh(waves=wv, seq_rows=rows)

    def _seq_show(self, rows, waves, title=""):
        SQ = self._SQ
        tv = self.tv_seq
        tv.delete(*tv.get_children())
        for x, r in zip(rows, SQ.table(rows)):
            lim = "" if x.get("min_ms") is None and x.get("max_ms") is None else f"{r[15] or '…'} ~ {r[16] or '…'}"
            tv.insert("", "end", values=(r[0], r[1], r[2], r[3], r[4], r[11], r[12], r[13], r[14], lim, r[17],
                                         r[18] or r[19]), tags=(x.get("verdict") or "",))
        ov = SQ.overall(rows)
        d = SQ.DIR_TXT.get(SQ.direction_of(rows), "")
        order = " → ".join(x["name"] for x in rows if x.get("order"))
        self.v_seq_sum.set(f"{title}{d}順序：{order}" + (f"　判定 {ov}" if ov else ""))
        SQ.draw(self.fig_seq, rows, waves, ref=self.v["seq_ref"].get(), fam=[self.fam, "Noto Sans CJK JP", "DejaVu Sans"])
        self.cv_seq.draw_idle()


    def _rail_vnom(self, name, default=0):
        """標稱電壓：先查目前電源設計表（同 Autotest iomap.vnom），沒有再退回 rails.VNOM。"""
        v = SC.vnom(name)
        if v is not None:
            return v
        return VNOM.get(name, default) or default

    def _dut_bar(self, body):
        """DUT 規格選擇列：放在 ① 儀器連線上方的固定列（不收進左欄 fold，避免被捲走或收合）。"""
        top = getattr(body, "_top", None)
        if top is None:
            wrap = ttk.Frame(body.left)
            wrap.pack(fill="x", padx=8, pady=(4, 2))
        else:
            for w in top.grid_slaves():
                info = w.grid_info()
                if str(info.get("row", "0")) == "0":
                    w.grid_configure(row=1)
            wrap = ttk.Frame(top, padding=(10, 6, 10, 4))
            wrap.grid(row=0, column=0, sticky="nsew")
        self.dut_bar = wrap
        hd = ttk.Frame(wrap)
        hd.pack(fill="x")
        ttk.Label(hd, text="DUT 規格", style="U.Sec.TLabel").pack(side="left", padx=(0, 8))
        self.v_spec_form = tk.StringVar(self, value="U2")
        self._spec_form_btns = {}
        short = {"U2": "U.2／U.3（VIN 12 V）", "M2": "M.2（VIN 3.3 V）"}
        for key in SC.DUT.ORDER:
            b = tk.Button(hd, text=short.get(key, SC.DUT.SPECS[key][1]), font=(self.fam, 10, "bold"),
                          width=20, cursor="hand2", bd=3, relief="raised",
                          command=lambda k=key: self._apply_spec(form=k))
            b.pack(side="left", padx=3)
            self._spec_form_btns[key] = b
        self.v_spec_overall = tk.StringVar(self, value="尚未判比")
        self.lb_spec_overall = tk.Label(hd, textvariable=self.v_spec_overall, font=(self.fam, 11, "bold"),
                                        fg="#0E9BD8", bg="#FFFFFF", anchor="e")
        self.lb_spec_overall.pack(side="right", padx=(8, 0))
        row2 = ttk.Frame(wrap)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Label(row2, text="機種／電源設計表", style="Trig.TLabel").pack(side="left", padx=(0, 8))
        names = SC.PP.names() or [SC.DEFAULT]
        self.v_spec_profile = tk.StringVar(self, value=SC.PP.get() or names[0])
        self.cb_spec_profile = ttk.Combobox(row2, textvariable=self.v_spec_profile, values=names,
                                            state="readonly", width=32)
        self.cb_spec_profile.pack(side="left", padx=4)
        self.cb_spec_profile.bind("<<ComboboxSelected>>", lambda _e: self._apply_spec(profile=self.v_spec_profile.get()))
        ttk.Button(row2, text="編輯…", style="U.Small.TButton", command=self._spec_edit).pack(side="left", padx=2)
        ttk.Button(row2, text="另存…", style="U.Small.TButton", command=lambda: self._spec_edit(save_as=True)).pack(side="left")
        ttk.Button(row2, text="⟳ 重判", style="U.Small.TButton", command=lambda: self._spec_refresh()).pack(side="left", padx=6)
        self.v_spec_warn = tk.StringVar(self, value="")
        ttk.Label(wrap, textvariable=self.v_spec_warn, foreground="#9a6700", wraplength=780, justify="left").pack(anchor="w")
        self.after(350, lambda: self._apply_spec(form=getattr(self, "_want_form", None) or "U2",
                                                 profile=getattr(self, "_want_profile", None)))

    def _spec_card(self, f):
        """⑨ 可收合的判比表（選擇列已在最上方固定列）。"""
        tip = ttk.Label(f, text="跟隨 Autotest_tool：機種＝電源設計表（u2core.powerprofile），"
                                "數值是「設計值，非正式規格」。公開輸入（U.2 12V ±8%／M.2 3.3V ±5%）另列。"
                                "缺 min／max 標「待填」，不臆造。選規格在最上方固定列。",
                        style="U.Muted.TLabel", wraplength=780, justify="left")
        tip.pack(anchor="w")
        self.v_spec_src = tk.StringVar(self, value="")
        ttk.Label(f, textvariable=self.v_spec_src, style="U.Muted.TLabel", wraplength=780, justify="left").pack(anchor="w")
        cols = ("ch", "nom", "rng", "meas", "ok")
        heads = ("通道", "標稱", "設計範圍", "實測", "判定")
        tv = ttk.Treeview(f, columns=cols, show="headings", height=8)
        for c, h, w in zip(cols, heads, (90, 56, 200, 70, 70)):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="center", stretch=c == "rng")
        for tag, fg, bg in (("PASS", "#1a7f37", "#dafbe1"), ("FAIL", "#cf222e", "#ffebe9"),
                            ("N/A", "#656d76", "#f6f8fa"), ("REF", "#0550ae", "#ddf4ff")):
            tv.tag_configure(tag, foreground=fg, background=bg)
        tv.pack(fill="x", pady=(2, 0))
        self.tv_spec_left = tv

    def _spec_tab(self, f):
        hd = ttk.Frame(f)
        hd.pack(fill="x")
        self.v_spec_sum = tk.StringVar(self, value="")
        ttk.Label(hd, textvariable=self.v_spec_sum, font=(self.fam, 10, "bold"), foreground="#0E9BD8").pack(side="left")
        ttk.Button(hd, text="⟳ 重判", style="U.Small.TButton", command=lambda: self._spec_refresh()).pack(side="right")
        self.v_spec_notes = tk.StringVar(self, value="")
        ttk.Label(f, textvariable=self.v_spec_notes, style="U.Muted.TLabel", wraplength=900, justify="left").pack(anchor="w")
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True)
        cols = ("ch", "nom", "min", "max", "tol", "unit", "src", "meas", "spec", "ok")
        heads = ("通道／項目", "標稱", "min", "max", "±%", "單位", "來源", "實測", "規格", "判定")
        tv = ttk.Treeview(box, columns=cols, show="headings", height=12)
        for c, h, w in zip(cols, heads, (110, 50, 60, 60, 40, 40, 200, 70, 220, 60)):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="center", stretch=c in ("src", "spec"))
        for tag, fg, bg in (("PASS", "#1a7f37", "#dafbe1"), ("FAIL", "#cf222e", "#ffebe9"),
                            ("N/A", "#656d76", "#f6f8fa"), ("REF", "#0550ae", "#ddf4ff")):
            tv.tag_configure(tag, foreground=fg, background=bg)
        vsb = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vsb.set)
        tv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tv_spec = tv

    def _style_form_btns(self):
        cur = self.v_spec_form.get() if hasattr(self, "v_spec_form") else "U2"
        for k, b in (getattr(self, "_spec_form_btns", {}) or {}).items():
            on = k == cur
            b.configure(relief="sunken" if on else "raised",
                        bg="#0E9BD8" if on else "#FFFFFF",
                        fg="#FFFFFF" if on else "#24292f",
                        activebackground="#0E9BD8" if on else "#f6f8fa")

    def _apply_spec(self, form=None, profile=None):
        """選規格／機種：match_vin、套 ARB VIN、更新標稱與示範波形、重判。"""
        if form:
            SC.set_form(form)
            self.v_spec_form.set(form)
            if not profile:
                profile = SC.PP.get()
        if profile:
            warn = SC.set_profile(profile)
            self.v_spec_profile.set(profile)
        else:
            warn = SC.PP.vin_mismatch(SC.DUT.vin()) if SC.DUT.get() else ""
        names = SC.PP.names()
        if hasattr(self, "cb_spec_profile"):
            self.cb_spec_profile.configure(values=names)
        self._style_form_btns()
        vin = SC.form_vin() or SC.vnom("VIN") or 12.0
        lab = f"VIN {vin:g}V"
        self.ARB_CH_LABEL[3] = lab
        if hasattr(self, "_arb_lb_by_ch"):
            self._arb_lb_by_ch[3] = lab
            if self._arb_sel_ch() == 3 and "arb_lb" in self.v:
                self.v["arb_lb"].set(lab)
        if hasattr(self, "arb_ch_btns") and 3 in self.arb_ch_btns:
            self.arb_ch_btns[3].configure(text=f"CH3　{lab}")
        for key, var, fallback in (("arb_RAMP_v1", "arb_RAMP_v1", f"{vin:g}"),
                                   ("arb_BROWNOUT_vn", "arb_BROWNOUT_vn", f"{vin:g}"),
                                   ("arb_ONOFFON_von", "arb_ONOFFON_von", f"{vin:g}")):
            if key in self.v:
                self.v[key].set(f"{vin:g}")
        # 通道表標稱
        for (m, i), lb in (getattr(self, "_vnom_lbl", {}) or {}).items():
            nm = self.v[f"nm_{m}_{i}"].get().strip() or NAMES[m][i]
            vn = self._rail_vnom(nm, VNOM.get(nm, 0))
            if nm in UNIT_A:
                lb.configure(text="電流 A")
            else:
                lb.configure(text=f"{vn:g}" if vn else "—")
        # ⑦ seq_rails 標稱
        for x in getattr(self, "seq_rails", []) or []:
            vn = self._rail_vnom(x["name"], x.get("nom") or 0)
            if vn:
                x["nom"] = vn
                if x.get("kind") == "logic":
                    x["logic_v"] = round(0.7 * vn, 3)
        # 示範波形跟設計表標稱走（讓 M.2 畫面有對應電壓）
        if self.demo and hasattr(self, "waves"):
            vm = SC.demo_vnom_map()
            self.waves = {m: _demo_waves(m, vnom=vm) for m in (MASTER_DEF, SLAVE_DEF)}
            try:
                self._draw()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._arb_changed()
        except Exception:  # noqa: BLE001
            pass
        src = SC.source_report()
        pp = src.get("powerprofile", "")
        short = "u2core.powerprofile" if pp.startswith("u2core") else "隨附 specs/dut_power（鏡射 Autotest origin/main）"
        if hasattr(self, "v_spec_src"):
            self.v_spec_src.set(f"來源：{short}　目前 {SC.form_label()}／{SC.PP.get()}　VIN {vin:g} V。"
                                f"選規格會自動 match_vin，並把 N6705 CH3 標稱改成 {vin:g} V。")
        if hasattr(self, "v_spec_warn"):
            self.v_spec_warn.set(warn or "")
        self.log(f"⑨ 規格：{SC.form_label()}　設計表 {SC.PP.get()}　VIN {vin:g} V"
                 + (f"　{warn}" if warn else ""))
        # 重算時序＋判比（_seq_recalc 會叫 _spec_refresh）
        if hasattr(self, "tv_seq"):
            self._seq_recalc()
        else:
            self._spec_refresh()

    def _spec_meas(self, waves=None, seq_rows=None, pm_rows=None):
        waves = waves if waves is not None else (self._waves_flat() if hasattr(self, "waves") else {})
        if seq_rows is None and hasattr(self, "_SQ") and waves:
            try:
                o = self._seq_opts()
                seq_rows = self._SQ.analyze(waves, self._rails_from_ui(), ref=o["ref"], basis=o["basis"],
                                            slave_skew_s=o["skew_s"], direction=o["direction"])
            except Exception:  # noqa: BLE001
                seq_rows = []
        seq_rows = seq_rows or []
        by = {x.get("name"): x for x in seq_rows if x.get("name")}
        meas = {}
        rails = self._rails_from_ui() if hasattr(self, "seq_rails") else []
        for r in rails:
            w = waves.get((r["model"], int(r["ch"])))
            if not w:
                continue
            x = by.get(r["name"]) or {}
            kind = "current" if r.get("kind") == "current" else "rail"
            if kind == "current" and x.get("peak") is not None:
                meas[r["name"]] = x["peak"]
            else:
                meas[r["name"]] = SC.steady_mean(w[0], w[1], t_on=x.get("t_on") or x.get("t_hi"), kind=kind)
        for r in (pm_rows or []):
            lab = str(r.get("label") or "")
            v = r.get("scope") if r.get("scope") is not None else r.get("pc")
            if v is None:
                continue
            for nm in list(meas.keys()) + list(VNOM.keys()):
                if nm and nm in lab and ("MEAN" in lab.upper() or "mean" in lab.lower() or "均" in lab):
                    meas[nm] = float(v)
                    break
        return meas, seq_rows

    def _spec_refresh(self, waves=None, seq_rows=None, pm_rows=None):
        if not hasattr(self, "tv_spec_left") and not hasattr(self, "tv_spec"):
            return
        meas, seq_rows = self._spec_meas(waves, seq_rows, pm_rows)
        result = SC.compare_measurements(meas, seq_rows=seq_rows)
        self._spec_last = result
        ov = result["overall_txt"]
        if hasattr(self, "v_spec_overall"):
            self.v_spec_overall.set(ov)
            col = {"PASS": "#1a7f37", "FAIL": "#cf222e"}.get(result["overall"], "#656d76")
            self.lb_spec_overall.configure(fg=col)
        if hasattr(self, "v_spec_sum"):
            self.v_spec_sum.set(ov)
        if hasattr(self, "v_spec_notes"):
            notes = "  ".join(result.get("public_notes") or [])
            self.v_spec_notes.set(notes)
        warn = result.get("warn") or ""
        if hasattr(self, "v_spec_warn") and warn:
            self.v_spec_warn.set(warn)

        def _rng(r):
            if r.get("min") is not None and r.get("max") is not None:
                return f"{r['min']:g}–{r['max']:g}"
            if r.get("nom") is not None:
                return f"標稱 {r['nom']:g}"
            return "待填"

        if hasattr(self, "tv_spec_left"):
            tv = self.tv_spec_left
            tv.delete(*tv.get_children())
            for r in result["rows"]:
                tv.insert("", "end", values=(r["ch"], SC._fmt(r.get("nom"), 4), _rng(r),
                                             SC._fmt(r.get("meas")), r["verdict"]),
                          tags=(r["verdict"] or "N/A",))
            if result["timing"]:
                tv.insert("", "end", values=("── 時序參考 ──", "", "只記錄／⑦ 限制", "", ""), tags=("REF",))
                for r in result["timing"]:
                    tv.insert("", "end", values=(r["ch"], "", r.get("spec") or "",
                                                 r.get("meas") or "", r["verdict"]),
                              tags=(r["verdict"] or "N/A",))
        if hasattr(self, "tv_spec"):
            tv = self.tv_spec
            tv.delete(*tv.get_children())
            for r in result["rows"]:
                tv.insert("", "end", values=(r["ch"], SC._fmt(r.get("nom"), 4), SC._fmt(r.get("min"), 5),
                                             SC._fmt(r.get("max"), 5), SC._fmt(r.get("tol_pct"), 3),
                                             r.get("unit") or "", r.get("source") or "",
                                             SC._fmt(r.get("meas")), (r.get("spec") or "")[:48],
                                             r["verdict"]),
                          tags=(r["verdict"] or "N/A",))
            if result["timing"]:
                tv.insert("", "end", values=("── 時序參考（設計值只記錄；⑦ 限制才計總判定）──",
                                             "", "", "", "", "", "", "", "", ""), tags=("REF",))
                for r in result["timing"]:
                    tv.insert("", "end", values=(f"{r['ch']} {r['item']}", "", "", "", "", "",
                                                 r.get("why") or "", r.get("meas") or "",
                                                 r.get("spec") or "", r["verdict"]),
                              tags=(r["verdict"] or "N/A",))

    def _spec_csv(self, waves, sq_rows, pm_rows):
        """給 Capture 執行緒寫 summary.csv：只用波形／時序列，不讀 Tk 變數。"""
        meas = {}
        for x in sq_rows or []:
            name = x.get("name")
            if not name:
                continue
            if x.get("kind") == "current" and x.get("peak") is not None:
                meas[name] = x["peak"]
                continue
            if not waves:
                continue
            w = waves.get((x.get("model"), int(x.get("ch") or 0)))
            if w:
                meas[name] = SC.steady_mean(w[0], w[1], t_on=x.get("t_on") or x.get("t_hi"),
                                            kind="current" if x.get("kind") == "current" else "rail")
        result = SC.compare_measurements(meas, seq_rows=sq_rows)
        return SC.csv_cols(result)

    def _spec_edit(self, save_as=False):
        """編輯電源設計表（同 Autotest powerprofile_ui）：存到使用者目錄，不覆寫隨附檔。"""
        from tkinter import simpledialog, messagebox
        name = self.v_spec_profile.get() or SC.PP.get()
        data = SC.PP.load(name) or {"name": name, "note": SC.NOTE, "rails": []}
        win = tk.Toplevel(self)
        win.title("編輯電源設計表（另存到使用者目錄，不改隨附檔）")
        win.geometry("760x480")
        ttk.Label(win, text="欄位同 Autotest u2core.powerprofile.FIELDS：通道名、標稱、min、max、±%、單位。"
                             "設計值，非正式規格。空的 min／max 維持待填。",
                  wraplength=740, style="U.Muted.TLabel").pack(anchor="w", padx=8, pady=6)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=8)
        heads = ("通道名", "標稱", "min", "max", "±%", "單位")
        keys = SC.PP.FIELDS
        for j, h in enumerate(heads):
            tk.Label(frm, text=h, bg="#0E9BD8", fg="white", font=(self.fam, 9, "bold")).grid(row=0, column=j, sticky="we", padx=1)
        vars_rows = []
        rails = list(data.get("rails") or [])
        # 預留空列讓使用者加 IBUS 等
        while len(rails) < 14:
            rails.append({"ch": "", "nom": None, "min": None, "max": None, "tol_pct": None, "unit": "V"})
        for i, r in enumerate(rails):
            rowv = {}
            for j, k in enumerate(keys):
                val = r.get(k)
                sv = tk.StringVar(win, value="" if val is None else (val if k in ("ch", "unit") else f"{val:g}"))
                ttk.Entry(frm, textvariable=sv, width=14 if k == "ch" else 10).grid(row=i + 1, column=j, padx=1, pady=1)
                rowv[k] = sv
            vars_rows.append(rowv)
        bt = ttk.Frame(win)
        bt.pack(fill="x", padx=8, pady=8)

        def _collect():
            out = []
            for rowv in vars_rows:
                ch = rowv["ch"].get().strip()
                if not ch:
                    continue
                d = {"ch": ch}
                for k in ("nom", "min", "max", "tol_pct"):
                    s = rowv[k].get().strip()
                    d[k] = None if not s else s
                d["unit"] = rowv["unit"].get().strip() or "V"
                out.append(SC.PP.clean_row(d))
            return out

        def _do_save(as_new):
            try:
                rows = _collect()
            except ValueError as e:
                messagebox.showerror("設計表", str(e), parent=win)
                return
            nm = name
            if as_new or (hasattr(SC.PP, "is_builtin") and SC.PP.is_builtin(name)):
                nm = simpledialog.askstring("另存電源設計表", "新名稱（存到使用者目錄，不覆寫隨附檔）：",
                                            initialvalue=name + "_edit", parent=win)
                if not nm:
                    return
            try:
                path = SC.PP.save(nm, rows, note=data.get("note") or SC.NOTE)
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("設計表", str(e), parent=win)
                return
            self.log(f"⑨ 已另存設計表「{nm}」→ {path}")
            self.cb_spec_profile.configure(values=SC.PP.names())
            self._apply_spec(profile=nm)
            win.destroy()

        ttk.Button(bt, text="另存新檔…", command=lambda: _do_save(True)).pack(side="left")
        ttk.Button(bt, text="儲存（使用者目錄）", style="Primary.TButton",
                   command=lambda: _do_save(save_as)).pack(side="left", padx=8)
        ttk.Button(bt, text="取消", command=win.destroy).pack(side="right")

    # ------------------------------------------------------------------ 連線（背景自動 ＋ USB 自動重連）
    def _make_links(self):

        from .lecroy import LeCroyScope, SimBus, SimScope
        from .link import Link
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        if self.demo:
            self.bus = SimBus()
        self._snap()
        for m in (MASTER_DEF, SLAVE_DEF):
            addr = self.v[f"addr_{m}"].get()
            if self.demo:
                fac = (lambda m=m, a=addr: SimScope(a, m, bus=self.bus, png=os.path.join(here, f"placeholder_{m}.png")))
            else:
                fac = (lambda m=m, a=addr: LeCroyScope(a, m, log=self.log, timeout_ms=20000))
            self.links[m] = Link(m, fac, log=self.log, on_state=lambda *a: self.q_ui.put(("link",) + a),
                                 on_setup=self._bg_setup)
            self.links[m].start()
        from .n6705 import N6705, SimN6705
        addr = self.v["addr_N6705"].get()
        fac = (lambda: SimN6705(addr, bus=self.bus)) if self.demo else (lambda: N6705(addr, log=self.log))
        self.psu = Link("N6705", fac, log=self.log, on_state=lambda *a: self.q_ui.put(("link",) + a),
                        on_setup=self._psu_setup, name="N6705")
        self.psu.start()
        self.after(1500, self._live_tick)

    def _psu_setup(self, dev, first):
        """N6705 連線 / 重連後：*CLS → 對時（u2core.timesync）。不動輸出 / ARB（安全）"""
        from .link import is_link_error
        tag = "" if first else "（重連後）"
        try:
            dev.w("*CLS")
            from u2core import timesync
            msg = timesync.sync(dev.q, dev.w, dev.idn)
            self._bg("N6705", "對時（中控電腦時間）" + tag, "失敗" not in msg and "未當成成功" not in msg, msg)
        except Exception as e:  # noqa: BLE001
            if is_link_error(e):
                raise
            self._bg("N6705", "對時（中控電腦時間）" + tag, False, f"{type(e).__name__}: {e}")

    def _snap(self):
        """背景執行緒會用到的 UI 值（主執行緒更新）"""
        mode = self.v["name_mode"].get()
        self.snap = {"master": self.master_model.get(),
                     "name_mode": "label" if mode.startswith("波形") else "both" if mode.startswith("Alias +") else "name",
                     "names": {m: [(i + 1, self.v[f"nm_{m}_{i}"].get().strip()) for i in range(NCH[m])
                                   if self.v[f"en_{m}_{i}"].get()] for m in NCH}}

    def _bg(self, m, task, ok, msg=""):
        self.q_ui.put(("bg", m, task, ok, msg))

    def _bg_setup(self, dev, first):
        """連線 / 重連後背景自動：對時 → 主 / 輔同步 → bus name → （重連）上次寫入的設定 / （第一次）Auto scale。
        連線錯誤往上丟（Link 會再重試）；其他錯誤只記失敗、不擋連線"""
        from . import busname as BN
        from . import settings as ST
        from .link import is_link_error
        m = dev.model
        role = "master" if m == self.snap.get("master", MASTER_DEF) else "slave"
        tag = "" if first else "（重連後）"

        def step(task, fn):
            try:
                ok, msg = fn()
            except Exception as e:  # noqa: BLE001
                if is_link_error(e):
                    raise
                ok, msg = False, f"{type(e).__name__}: {e}"
            self._bg(m, task + tag, ok, msg)

        def clock():
            if dev.sim:
                return True, "模擬示波器：DATE 略過"
            from u2core.scopeext import set_clock
            msg = set_clock(dev.w, dev.q)
            return ("✓" in msg), msg

        def sync():
            bad = ST.apply_sync(dev.w, dev.q, role)
            return not bad, "；".join(bad) or ("AUX OUT = TriggerOut" if role == "master" else "EX10 0.5 V ↑")

        def names():
            r = BN.set_names(dev.w, dev.q, self.snap["names"].get(m, []), mode=self.snap["name_mode"], log=None)
            return True, f"{len(r['set'])} CH"

        step("對時（中控電腦時間）", clock)
        step("主 / 輔觸發同步", sync)
        step("bus name", names)
        cfg = self.last_cfg.get(m)
        if cfg is not None:
            def again():
                bad = ST.write(dev.w, dev.q, cfg, log=lambda *_: None)
                return (not bad or dev.sim), "；".join(bad) if bad and not dev.sim else "觸發 / 時基 / 通道 / P1–P24"
            step("套用上次寫入的設定", again)
        elif first:
            def scale():
                chans = [{"ch": i + 1, "vmax": VNOM[nm]} for i, nm in enumerate(NAMES[m]) if VNOM.get(nm) and nm not in UNIT_A]
                if not dev.sim:
                    from u2core.scopescale import auto_scale_channels
                    auto_scale_channels(dev.w, dev.q, chans, verify=False)
                return True, f"{len(chans)}/{NCH[m]} CH" + ("（電流不動）" if any(n in UNIT_A for n in NAMES[m]) else "")
            step("Auto scale", scale)

    def _on_link(self, m, state, detail):
        if m == "N6705":
            return self._on_psu_link(state, detail)
        role = "MASTER 主" if m == self.master_model.get() else "SLAVE 輔"
        lb = self.bg_warn.get(m)
        if state == "up":
            self.st_var[m].set(f"已連線 · {role}")
            self.idn_var[m].set(detail)
            if lb is not None and lb.cget("text").startswith("⚠ USB"):
                lb.pack_forget()
        elif state == "connecting":
            self.st_var[m].set(f"{detail}… · {role}")
        else:
            self.st_var[m].set(f"⚠ 斷線 · {role}")
            if lb is not None:
                lb.configure(text=f"⚠ USB 中斷 — 背景重連中（{detail[:60]}）")
                lb.pack(side="left", padx=(8, 0))
        try:
            self.rows[m].dot.set("#999999" if state != "up" else PASS_C[0])
        except Exception:  # noqa: BLE001
            pass
        if not (self.cap and self.cap.running):
            ups = [k for k, ln in self.links.items() if ln.up]
            self.v_status.set("兩台已連線，待命" if len(ups) == 2 else
                              "、".join(f"{k} {'已連線' if self.links[k].up else '斷線 / 連線中'}" for k in self.links))

    def _on_psu_link(self, state, detail):
        m, lb = "N6705", self.bg_warn.get("N6705")
        if state == "up":
            self.st_var[m].set("已連線 · ARB 電源")
            self.idn_var[m].set(detail)
            if lb is not None and lb.cget("text").startswith("⚠ USB"):
                lb.pack_forget()
        elif state == "connecting":
            self.st_var[m].set(f"{detail}… · ARB 電源")
        else:
            self.st_var[m].set("⚠ 斷線 · ARB 電源")
            if lb is not None:
                lb.configure(text=f"⚠ USB 中斷 — 背景重連中（{detail[:60]}）")
                lb.pack(side="left", padx=(8, 0))
        try:
            self.rows[m].dot.set("#999999" if state != "up" else PASS_C[0])
        except Exception:  # noqa: BLE001
            pass

    def sim_unplug(self, m, sec):
        if self.bus is not None:
            self.bus.unplug(m, sec)
            self.log(f"示範：模擬拔掉 {m} 的 USB {sec:g} s（下一個指令會逾時 → 斷線 → 背景重連）")

    # ------------------------------------------------------------------ UI 佇列
    def _pump(self):
        try:
            while True:
                it = self.q_ui.get_nowait()
                k = it[0]
                if k == "log_line":
                    self._log_line(it[1])
                elif k == "link":
                    self._on_link(*it[1:])
                elif k == "bg":
                    self.bg_result(*it[1:])
                elif k == "cap":
                    self._on_cap(it[1], it[2])
                elif k == "shots":
                    for m, p in it[1].items():
                        self.shots[m]["src"] = p
                    self._fit_shots()
                elif k == "readback":
                    self._apply_readback(it[1], it[2])
                elif k == "nvme":
                    rows, note = it[1], it[2]
                    if isinstance(rows, dict) and rows.get("error"):
                        self.log(f"⚠ Linux NVMe rescan 失敗：{rows['error']}")
                    else:
                        self.log(f"Linux NVMe rescan：{note}")
                    self._nvme_fill(rows)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    # ---- ⑧ N6705 ARB（參數式範本，不用編點）
    ARB_CH_LABEL = {1: "CH1", 2: "CH2", 3: "VIN 12V", 4: "CH4"}   # 預設標籤；CH3 = DUT VIN（同 Autotest_tool）
    ARB_MAIN_CH, ARB_ADV_CH = (1, 3), (2, 4)
    ARB_TPL_BTNS = (
        ("RAMP", "Ramp", "上升 / 下降", "▲", "#1a7f37"),
        ("BROWNOUT", "Brownout margin", "掉電壓", "▼", "#bc4c00"),
        ("ONOFFON", "Instant ON-OFF-ON", "瞬間開關", "⚡", "#0550ae"),
    )

    def _arb_card(self, f):
        """一套共用範本＋參數，套到「一個」選中的 N6705 通道（預設 CH3）。"""
        from . import arbtpl as T
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        self._T = T
        self._sv("arb_ch", "3")
        self._sv("arb_tpl", "RAMP")
        self._arb_lb_by_ch = dict(self.ARB_CH_LABEL)
        self._sv("arb_lb", self._arb_lb_by_ch[3])
        for key, *_r in T.TEMPLATES:
            for k, _lbl, d, _u, _sw in T.FIELDS.get(key, []):
                v = self._sv(f"arb_{key}_{k}", d)
                v.trace_add("write", lambda *_: self._arb_changed())
        # -- 通道選擇（通常只會用一個）
        chrow = ttk.Frame(f)
        chrow.pack(fill="x", pady=(2, 0))
        ttk.Label(chrow, text="輸出通道", style="Trig.TLabel").pack(side="left", padx=(0, 8))
        self.arb_ch_btns = {}
        self.arb_ch_main = ttk.Frame(chrow)
        self.arb_ch_main.pack(side="left")
        for ch, sub in ((1, "CH1"), (3, "CH3　VIN 12V")):
            self._arb_mk_ch_btn(self.arb_ch_main, ch, sub)
        ttk.Label(chrow, text="　標籤").pack(side="left", padx=(12, 0))
        ttk.Entry(chrow, textvariable=self.v["arb_lb"], width=12).pack(side="left", padx=4)
        ttk.Label(chrow, text="（範本通用，一次只跑這一個通道；其他通道不動）",
                  style="U.Muted.TLabel").pack(side="left", padx=6)
        # -- 共用範本按鈕
        bf = ttk.Frame(f)
        bf.pack(fill="x", pady=(8, 2))
        self.arb_tpl_btns = {}
        for key, title, sub, icon, col in self.ARB_TPL_BTNS:
            b = tk.Button(bf, text=f"{icon}  {title}\n{sub}", justify="center",
                          font=(self.fam, 11, "bold"), width=18, height=2, cursor="hand2",
                          wraplength=160, bd=3, relief="raised",
                          command=lambda key=key: self._pick_tpl(key))
            b.pack(side="left", padx=4, pady=2, fill="x", expand=True)
            b._sel_color = col
            self.arb_tpl_btns[key] = b
        self.arb_par = ttk.Frame(f)
        self.arb_par.pack(fill="x", pady=(4, 0))
        self._arb_fields()
        self._style_tpl_btns()
        self._style_ch_btns()
        # -- 進階：CH2 / CH4 也可選（同一套範本）
        self.arb_adv = ttk.Frame(f)
        adv_row = ttk.Frame(self.arb_adv)
        adv_row.pack(fill="x")
        ttk.Label(adv_row, text="也可選", style="U.Muted.TLabel").pack(side="left", padx=(0, 6))
        for ch, sub in ((2, "CH2"), (4, "CH4")):
            self._arb_mk_ch_btn(adv_row, ch, sub)
        ttk.Label(adv_row, text="（仍是同一套範本，一次只跑一個通道）", style="U.Muted.TLabel").pack(side="left", padx=8)
        adv_btn = ttk.Button(f, text="▸ 進階：也可選 CH2 / CH4", style="U.Small.TButton")

        def _tog_adv():
            if self.arb_adv.winfo_ismapped():
                self.arb_adv.pack_forget()
                adv_btn.configure(text="▸ 進階：也可選 CH2 / CH4")
            else:
                self.arb_adv.pack(fill="x", after=adv_btn, pady=(4, 0))
                adv_btn.configure(text="▾ 收合 CH2 / CH4")
        adv_btn.configure(command=_tog_adv)
        adv_btn.pack(anchor="w", pady=(4, 0))
        r = ttk.Frame(f)
        r.pack(fill="x", pady=(6, 0))
        ttk.Label(r, text="掃描").pack(side="left")
        ttk.Combobox(r, textvariable=self._sv("arb_sweep", "每次擷取換下一個值"), values=["每次擷取換下一個值", "全部放在同一個 ARB"],
                     state="readonly", width=17).pack(side="left", padx=4)
        ttk.Label(r, text="循環").pack(side="left", padx=(8, 0))
        ttk.Spinbox(r, textvariable=self._sv("arb_loops", "1"), from_=0, to=10000, width=5,
                    command=self._arb_changed).pack(side="left", padx=4)
        ttk.Label(r, text="次（0 = 直到 ■ 停止）").pack(side="left")
        ttk.Label(r, text="　結束 / 停止 →").pack(side="left")
        ttk.Combobox(r, textvariable=self._sv("arb_end", "輸出 OFF"), values=["輸出 OFF", "維持最後電壓"], state="readonly",
                     width=11).pack(side="left", padx=4)
        self.v["arb_sweep"].trace_add("write", lambda *_: self._arb_changed())
        self.v["arb_loops"].trace_add("write", lambda *_: self._arb_changed())
        r2 = ttk.Frame(f)
        r2.pack(fill="x", pady=(6, 0))
        ttk.Button(r2, text="⚡ ARB 啟動＋擷取", style="Start.TButton", width=16,
                   command=self.start_arb_capture).pack(side="left")
        ttk.Button(r2, text="⛔ N6705 安全中止", style="Stop.TButton", width=16,
                   command=self.psu_abort).pack(side="left", padx=6)
        self.v_arb_sum = tk.StringVar(self, value="")
        ttk.Label(r2, textvariable=self.v_arb_sum, font=(self.fam, 10, "bold"), foreground="#0958d9").pack(side="left", padx=6)
        self.v_arb_msg = tk.StringVar(self, value="")
        ttk.Label(f, textvariable=self.v_arb_msg, foreground="#cf222e", wraplength=780, justify="left").pack(anchor="w")
        self.fig_arb = Figure(figsize=(8.2, 1.7), dpi=96, facecolor="#FFFFFF")
        self.cv_arb = FigureCanvasTkAgg(self.fig_arb, f)
        self.cv_arb.get_tk_widget().pack(fill="x", pady=(4, 0))
        ttk.Label(f, text="流程：輔 arm → 主 arm（預設 C8 RSTn）→ 下載 ARB 到選中的那一個 N6705 通道 → OUTP ON → *TRG → 等主觸發 → 讀 / 存；"
                          "同時用 ⑦ 的上 / 下電時序算每條 rail 的 10↔90 % 時間、slew rate、對參考的 Δt，寫入時序 CSV ＋ summary。"
                          "ARB 跑完主沒觸發（RSTn 沒跳，例淺 Brownout）→ summary 記「未觸發」。"
                          "掃描用 / 分隔（-10%/-20%/-50%、1ms/2ms/5ms）。",
                  style="U.Muted.TLabel", wraplength=780, justify="left").pack(anchor="w", pady=(4, 0))
        self._nvme_block(f)
        self._arb_job = None
        self.after(400, self._arb_preview)
        self.after(450, lambda: (self._style_tpl_btns(), self._style_ch_btns(), self._sync_trig_from_tpl()))

    def _arb_mk_ch_btn(self, parent, ch, title):
        b = tk.Button(parent, text=title, justify="center", font=(self.fam, 12, "bold"),
                      width=14, height=1, cursor="hand2", bd=3, relief="raised",
                      command=lambda ch=ch: self._pick_ch(ch))
        b.pack(side="left", padx=3, pady=2)
        self.arb_ch_btns[ch] = b
        return b

    def _arb_sel_ch(self):
        try:
            return int(self.v["arb_ch"].get() or 3)
        except ValueError:
            return 3

    def _pick_ch(self, ch):
        """切到這一個輸出通道；範本與參數不變，只換套用的通道。"""
        prev = self._arb_sel_ch()
        self._arb_lb_by_ch[prev] = self.v["arb_lb"].get().strip() or self.ARB_CH_LABEL.get(prev, f"CH{prev}")
        self.v["arb_ch"].set(str(int(ch)))
        self.v["arb_lb"].set(self._arb_lb_by_ch.get(int(ch), self.ARB_CH_LABEL.get(int(ch), f"CH{ch}")))
        self._style_ch_btns()
        self._arb_changed()

    def _pick_tpl(self, key, ch=None):
        """選共用範本。ch 若有給，一併切到那個輸出通道（相容舊的 --arb-tpl 呼叫）。"""
        if ch is not None:
            self._pick_ch(int(ch))
        self.v["arb_tpl"].set(key)
        self._trig_slope_user = False
        self._sync_trig_from_tpl(force=True)
        self._arb_fields()
        self._style_tpl_btns()
        self._arb_changed()

    def _style_ch_btns(self):
        cur = self._arb_sel_ch()
        for ch, b in getattr(self, "arb_ch_btns", {}).items():
            sel = ch == cur
            col = "#0E9BD8"
            b.configure(bg=col if sel else "#f6f8fa",
                        fg="#ffffff" if sel else "#1f2328",
                        relief="sunken" if sel else "raised",
                        bd=4 if sel else 2,
                        font=(self.fam, 12, "bold") if sel else (self.fam, 12),
                        highlightbackground=col if sel else "#d0d7de",
                        highlightthickness=2 if sel else 1,
                        activebackground=col if sel else "#eaeef2",
                        activeforeground="#ffffff" if sel else "#1f2328")

    def _style_tpl_btns(self, ch=None):
        tpl = self.v["arb_tpl"].get() if self.v.get("arb_tpl") else "RAMP"
        for key, b in getattr(self, "arb_tpl_btns", {}).items():
            sel = key == tpl
            col = getattr(b, "_sel_color", "#0E9BD8")
            b.configure(bg=col if sel else "#f6f8fa",
                        fg="#ffffff" if sel else "#1f2328",
                        relief="sunken" if sel else "raised",
                        bd=4 if sel else 2,
                        font=(self.fam, 11, "bold") if sel else (self.fam, 11),
                        highlightbackground=col if sel else "#d0d7de",
                        highlightthickness=2 if sel else 1,
                        activebackground=col if sel else "#eaeef2",
                        activeforeground="#ffffff" if sel else "#1f2328")

    def _nvme_block(self, f):
        """Linux NVMe rescan：預設關閉；沒勾就不佔空間、不連線、不 import paramiko。"""
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(row, text="啟用 Linux NVMe rescan（選用，預設關閉）",
                        variable=self._bv("nvme_on", False), command=self._nvme_toggle).pack(side="left")
        self.nvme_box = ttk.LabelFrame(f, text=" Linux NVMe rescan ＋ 認碟 ", padding=6)
        r = ttk.Frame(self.nvme_box)
        r.pack(fill="x")
        ttk.Label(r, text="主機").pack(side="left")
        ttk.Entry(r, textvariable=self._sv("nvme_host", ""), width=16).pack(side="left", padx=4)
        ttk.Label(r, text="埠").pack(side="left")
        ttk.Entry(r, textvariable=self._sv("nvme_port", "22"), width=5).pack(side="left", padx=4)
        ttk.Label(r, text="帳號").pack(side="left")
        ttk.Entry(r, textvariable=self._sv("nvme_user", "root"), width=10).pack(side="left", padx=4)
        ttk.Label(r, text="密碼").pack(side="left")
        ttk.Entry(r, textvariable=self._sv("nvme_pw", ""), width=12, show="*").pack(side="left", padx=4)
        ttk.Label(r, text="等").pack(side="left")
        ttk.Entry(r, textvariable=self._sv("nvme_wait", "2"), width=4).pack(side="left", padx=2)
        ttk.Label(r, text="s  後列舉").pack(side="left")
        ttk.Button(r, text="↻ 立刻 rescan", style="U.Small.TButton", command=self._nvme_now).pack(side="left", padx=8)
        from . import nvmehost as NH
        cols = NH.COLS
        tv = ttk.Treeview(self.nvme_box, columns=cols, show="headings", height=3)
        for c in cols:
            tv.heading(c, text=NH.COL_ZH[c])
            tv.column(c, width=110 if c != "capacity" else 180, anchor="w", stretch=True)
        tv.pack(fill="x", pady=(6, 0))
        self.tv_nvme = tv
        self.nvme_tab = None
        # 預設關閉：不 pack nvme_box

    def _nvme_toggle(self):
        on = bool(self.v["nvme_on"].get())
        if on:
            self.nvme_box.pack(fill="x", pady=(4, 0))
            self._nvme_ensure_tab()
        else:
            self.nvme_box.pack_forget()
            self._nvme_drop_tab()

    def _nvme_ensure_tab(self):
        if self.nvme_tab is not None:
            return
        from . import nvmehost as NH
        pf = self._right_card("nvme", "NVMe 認碟", default_open=True, weight=1.0)
        ttk.Label(pf, text="ARB 擷取後（或按 ↻ 立刻 rescan）列出每顆 NVMe。示範模式為模擬資料。",
                  style="U.Muted.TLabel").pack(anchor="w")
        cols = NH.COLS
        tv = ttk.Treeview(pf, columns=cols, show="headings", height=8)
        for c in cols:
            tv.heading(c, text=NH.COL_ZH[c])
            tv.column(c, width=120 if c != "capacity" else 200, anchor="w", stretch=True)
        tv.pack(fill="both", expand=True, pady=(4, 0))
        self.tv_nvme_tab = tv
        self.nvme_tab = pf

    def _nvme_drop_tab(self):
        if self.nvme_tab is None:
            return
        if getattr(self, "right_stack", None) is not None:
            c = self.right_stack.by_key("nvme")
            if c is not None:
                if c.win is not None:
                    c.dock()
                self.right_stack.remove("nvme")
                self.folds.pop("nvme", None)
                try:
                    c.destroy()
                except tk.TclError:
                    pass
        self.nvme_tab = None
        self.tv_nvme_tab = None

    def _nvme_fill(self, drives):
        rows = drives if isinstance(drives, list) else []
        from . import nvmehost as NH
        for tv in (getattr(self, "tv_nvme", None), getattr(self, "tv_nvme_tab", None)):
            if tv is None:
                continue
            tv.delete(*tv.get_children())
            for d in rows:
                tv.insert("", "end", values=tuple(d.get(k, "") or "—" for k in NH.COLS))
        if isinstance(drives, dict) and drives.get("error"):
            self.log(f"⚠ NVMe：{drives['error']}")

    def _nvme_now(self):
        if not self.v["nvme_on"].get():
            return

        def job():
            from . import nvmehost as NH
            try:
                h = NH.connect(self.v["nvme_host"].get(), port=self.v["nvme_port"].get() or 22,
                               user=self.v["nvme_user"].get(), password=self.v["nvme_pw"].get(),
                               sim=self.demo)
                rows, note = NH.rescan_and_list(h, wait_s=float(self.v["nvme_wait"].get() or 2))
                try:
                    h.close()
                except Exception:  # noqa: BLE001
                    pass
                self.q_ui.put(("nvme", rows, note))
            except Exception as e:  # noqa: BLE001
                self.q_ui.put(("nvme", {"error": str(e)}, str(e)))
        threading.Thread(target=job, daemon=True).start()

    def _select_mon_tab(self, hint):
        """展開對應的右欄子視窗（相容舊的分頁名稱）。"""
        mp = {"時序": "seqv", "NVMe": "nvme", "認碟": "nvme", "合成": "waves", "合一": "waves",
              "實圖": "shots", "畫面": "shots", "紀錄": "log", "存檔": "saved", "P 量": "pmt", "量測": "pmt"}
        key = None
        for k, v in mp.items():
            if k in (hint or ""):
                key = v
                break
        if key:
            self._set_fold(key, True)
            return
        # 分頁殘留（理論上沒有）
        nb = getattr(self, "mon_nb", None)
        if nb is None:
            return
        try:
            for i in range(nb.index("end")):
                if hint in str(nb.tab(i, "text")):
                    nb.select(i)
                    return
        except tk.TclError:
            pass

    def _arb_fields(self, ch=None):
        T = self._T
        par = self.arb_par
        for w in par.winfo_children():
            w.destroy()
        tpl = self.v["arb_tpl"].get()
        flds = T.FIELDS.get(tpl, [])
        if not flds:
            ttk.Label(par, text="請選一個範本", style="U.Muted.TLabel").grid(row=0, column=0, sticky="w")
            return
        for i, (k, lbl, _d, u, sw) in enumerate(flds):
            r, c = divmod(i, 4)
            ttk.Label(par, text=lbl + ("　⇄掃描" if sw else "")).grid(row=r * 2, column=c, sticky="w", padx=(0, 10))
            e = ttk.Entry(par, textvariable=self.v[f"arb_{tpl}_{k}"], width=16 if sw else 9)
            e.grid(row=r * 2 + 1, column=c, sticky="w", padx=(0, 10), pady=(0, 4))
        for c in range(4):
            par.columnconfigure(c, weight=1)

    def _arb_params(self, ch=None):
        tpl = self.v["arb_tpl"].get()
        return tpl, {k: self.v[f"arb_{tpl}_{k}"].get() for k, *_r in self._T.FIELDS.get(tpl, [])}

    def _arb_cfg(self):
        """UI → Capture cfg["arb"]：只套用選中的那一個通道（驗證每個掃描值；有錯 → ValueError）"""
        from u2core import arbmodule as AM
        T = self._T
        ch = self._arb_sel_ch()
        tpl, p = self._arb_params()
        if not tpl or tpl == "NONE":
            return {"chans": [], "sweep_mode": "each", "nsweep": 1,
                    "end": "keep" if self.v["arb_end"].get().startswith("維持") else "off"}
        ns = T.n_sweep(tpl, p)
        keep = None
        for idx in range(ns):
            try:
                c = T.build(tpl, p, ch=ch, idx=idx)
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"CH{ch} 參數：{e}") from e
            err, _w = AM.validate(c)
            if err:
                raise ValueError(f"CH{ch} {T.TPL_NAME[tpl]}：" + "；".join(err[:3]))
            keep = float(c.dc_value)
        lb = self.v["arb_lb"].get().strip() or self.ARB_CH_LABEL.get(ch, f"CH{ch}")
        chans = [{"ch": ch, "label": lb, "tpl": tpl, "p": p, "keep_v": keep}]
        mode = "each" if self.v["arb_sweep"].get().startswith("每次") else "all"
        return {"chans": chans, "sweep_mode": mode, "nsweep": ns,
                "end": "keep" if self.v["arb_end"].get().startswith("維持") else "off"}

    def _arb_changed(self):
        if getattr(self, "_arb_job", None):
            try:
                self.after_cancel(self._arb_job)
            except Exception:  # noqa: BLE001
                pass
        self._arb_job = self.after(250, self._arb_preview)

    def _arb_preview(self):
        """即時預覽：每個啟用通道的 ARB 輸出（掃描值疊在一起；目前這組 = 實線）"""
        self._arb_job = None
        from u2core import arbmodule as AM
        T = self._T
        fig = self.fig_arb
        fig.clear()
        ax = fig.add_subplot(111)
        fam = [self.fam, "Noto Sans CJK JP", "DejaVu Sans"]
        cols = {1: "#F2C744", 2: "#3BB273", 3: "#0E9BD8", 4: "#E0457B"}
        msgs, sums = [], []
        try:
            a = self._arb_cfg()
        except ValueError as e:
            a = None
            msgs.append(str(e))
        each = self.v["arb_sweep"].get().startswith("每次")
        for item in ((a or {}).get("chans") or ([{"ch": self._arb_sel_ch()}] if a is None else [])):
            ch = item["ch"]
            tpl, p = self._arb_params()
            if not tpl or tpl == "NONE":
                continue
            lb = self.v["arb_lb"].get().strip() or self.ARB_CH_LABEL.get(ch, f"CH{ch}")
            try:
                ns = T.n_sweep(tpl, p)
                idxs = list(range(ns)) if each else [None]
                for j, idx in enumerate(idxs):
                    c = T.build(tpl, p, ch=ch, idx=idx)
                    tt, vv = T.preview(c)
                    sw = T.sweep_label(tpl, p, idx) if idx is not None and ns > 1 else ""
                    ax.plot([x * 1e3 for x in tt], vv, lw=1.6 if j == 0 else 1.0, alpha=1.0 if j == 0 else 0.55,
                            ls="-" if j == 0 else "--", color=cols[ch], label=f"CH{ch} {lb}" + (f"  {sw.split(' ')[-1]}" if sw else ""))
                c0 = T.build(tpl, p, ch=ch, idx=0 if each else None)
                pts = len(AM.to_udef(c0)) if c0.shape == "UDEF" else None
                sums.append(f"CH{ch} {T.TPL_NAME[tpl].split('（')[0]} · {AM.SHAPE_NAME.get(c0.shape, c0.shape)}"
                            + (f" {pts} 點" if pts else "") + f" · {AM.duration(c0) * 1e3:.1f} ms"
                            + (f" × {c0.repeat}" if c0.repeat > 1 else "") + (f" · 掃描 {ns} 值" if ns > 1 else ""))
            except Exception as e:  # noqa: BLE001
                msgs.append(f"CH{ch}：{e}")
        ax.set_xlabel("時間 (ms)　t = 0 → *TRG", family=fam, fontsize=8)
        ax.set_ylabel("V", fontsize=8)
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=7)
        if ax.lines:
            ax.legend(fontsize=7, loc="best", prop={"family": fam, "size": 7})
        fig.subplots_adjust(left=0.07, right=0.99, top=0.95, bottom=0.2)
        self.cv_arb.draw_idle()
        if a is not None:
            try:
                n = int(float(self.v["arb_loops"].get() or 0))
            except ValueError:
                n = 1
            tot = "直到 ■ 停止" if n <= 0 else f"{n * (a['nsweep'] if a['sweep_mode'] == 'each' else 1)} 次擷取"
            self.v_arb_sum.set(f"→ {tot}　{self._trig_text()}")
        self.v_arb_msg.set("　".join(sums) + (("\n✗ " + "；".join(msgs)) if msgs else ""))

    def psu_abort(self):
        """⛔ N6705 安全中止：停止擷取 → ABOR:TRAN → OUTP OFF（所有啟用的 ARB 通道）"""
        from . import n6705 as N
        if self.cap is not None and self.cap.running:
            self.cap.stop()
        chans = [self._arb_sel_ch()]
        if not (self.psu and self.psu.up):
            self.log("⚠ N6705 斷線，無法送安全中止 — 請手動按 N6705 Output Off")
            return

        def job():
            try:
                self.psu.call(lambda d: N.abort(d, chans, output_off=True))
                self.log(f"⛔ N6705 安全中止：ABOR:TRAN → OUTP OFF（CH {chans}）")
            except Exception as e:  # noqa: BLE001
                self.log(f"✗ N6705 安全中止失敗：{e} — 請手動按 N6705 Output Off")
        threading.Thread(target=job, daemon=True).start()

    # ------------------------------------------------------------------ 連續擷取
    def _pm_rows(self, m):
        return [{"on": self.v[f"pm_on_{m}_{i}"].get(), "src1": self.v[f"pm_s1_{m}_{i}"].get(),
                 "src2": self.v[f"pm_s2_{m}_{i}"].get(), "level": self.v[f"pm_lv_{m}_{i}"].get(),
                 "param": self.v[f"pm_p_{m}_{i}"].get()} for i in range(24)]

    def _legend(self):
        return ("輔：" + "  ".join(f"C{i + 1} {n}" for i, n in enumerate(NAMES[SLAVE_DEF]))
                + "　｜　主：" + "  ".join(f"C{i + 1} {n}" for i, n in enumerate(NAMES[MASTER_DEF]))
                + f"　｜　{self.v['tdiv'].get()} s/div  Delay {self.v['delay'].get()} s")

    def _trig_text(self):
        return f"觸發：主 {self.v['trig_src'].get().split()[0]} {self.v['trig_slope'].get()} {self.v['trig_level'].get()} V → Aux Out → 輔 EXT"

    def _capture_cfg(self, count=None):
        self._snap()
        pm = {}
        for m in NCH:
            try:
                pm[m] = pm_parse(self._pm_rows(m), m, NCH[m])
            except ValueError as e:
                self.log(f"⚠ {m} P1–P24 設定有誤，這次不算 P：{e}")
                pm[m] = []
        try:
            n = int(float(self.v["cap_n"].get())) if count is None else int(count)
        except ValueError:
            n = 1
        try:
            to = float(self.v["cap_to"].get() or 0)
        except ValueError:
            to = 0.0
        base = self.v["folder"].get().strip()
        if not base or not os.path.isdir(os.path.dirname(base.rstrip("\\/")) or "."):
            base = os.path.join(os.path.expanduser("~"), "Desktop", "scope waveform")
        chans = {m: [(i + 1, self.v[f"nm_{m}_{i}"].get().strip() or NAMES[m][i]) for i in range(NCH[m])
                     if self.v[f"en_{m}_{i}"].get()] for m in NCH}
        o = self._seq_opts()
        try:
            tlv = float(self.v["trig_level"].get())
        except ValueError:
            tlv = VNOM["RSTn"] * 0.5
        trig = {"src": self.v["trig_src"].get().split()[0], "level": tlv, "slope": self.v["trig_slope"].get()}
        nvme = {}
        if self.v.get("nvme_on") and self.v["nvme_on"].get():
            try:
                wait = float(self.v["nvme_wait"].get() or 2)
            except ValueError:
                wait = 2.0
            try:
                port = int(float(self.v["nvme_port"].get() or 22))
            except ValueError:
                port = 22
            nvme = {"on": True, "sim": bool(self.demo), "host": self.v["nvme_host"].get().strip(),
                    "port": port, "user": self.v["nvme_user"].get().strip() or "root",
                    "password": self.v["nvme_pw"].get(), "wait_s": wait}
        return dict(count=n, trig_timeout_s=to, base=base, prefix=self.v["prefix"].get().strip(), chans=chans,
                    fmt={k: self.v[f"fmt_{k}"].get() for k in ("png", "trc", "csv", "npz", "pm")}, pm=pm,
                    rails=self._rails_from_ui(), seq_on=self.v["seq_save"].get(), legend=self._legend(),
                    trig=trig, trig_text=self._trig_text(), nvme=nvme,
                    spec_compare=self._spec_csv, **o)

    def start_arb_capture(self, loops=None):
        """⚡ ARB＋擷取：兩台 arm → N6705 下載 / 啟動 ARB → 等觸發 → 讀 / 存（循環 N 次 × 掃描值）"""
        self._sync_trig_from_tpl()
        try:
            arb = self._arb_cfg()
        except ValueError as e:
            self.v_arb_msg.set(f"✗ {e}")
            self.log(f"✗ ARB 設定有誤：{e}")
            return
        if not arb["chans"]:
            self.v_arb_msg.set("✗ 沒有啟用的 N6705 通道（勾「使用」並選範本）")
            return
        if not (self.psu and self.psu.up):
            self.v_arb_msg.set("✗ N6705 還沒連線（背景重連中）")
            self.log("✗ ⚡ ARB＋擷取：N6705 還沒連線")
            return
        try:
            n = int(float(self.v["arb_loops"].get())) if loops is None else int(loops)
        except ValueError:
            n = 1
        tot = 0 if n <= 0 else n * (arb["nsweep"] if arb["sweep_mode"] == "each" else 1)
        self.start_capture(count=tot, arb=arb)

    def start_capture(self, count=None, arb=None):
        from .capture import Capture
        if self.cap is not None and self.cap.running:
            return
        cfg = self._capture_cfg(count)
        if arb:
            cfg["arb"] = arb
        m = self.master_model.get()
        s = SLAVE_DEF if m == MASTER_DEF else MASTER_DEF
        self.cap = Capture({"master": self.links[m], "slave": self.links[s]}, cfg, log=self.log,
                           emit=lambda k, d: self.q_ui.put(("cap", k, d)), psu=self.psu if arb else None)
        n = cfg["count"]
        self.pb.configure(mode="determinate" if n > 0 else "indeterminate", maximum=max(n, 1), value=0)
        if n <= 0:
            self.pb.start(80)
        self.btn_go.configure(state="disabled")
        self.btn_arb.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._cap_last = "—"
        self.saved_txt.delete("1.0", "end")
        self.cap.start()

    def stop_capture(self):
        if self.cap is not None:
            self.cap.stop()
            self.log("■ 停止（等這一次結束）")

    STATE_TXT = {"arb": "N6705 ARB 啟動", "arming": "arm 中（輔 → 主）", "waiting": "已 arm，等觸發", "reading": "讀取 / 存檔中", "paused": "⏸ 暫停"}

    def _prog_text(self, n, N, state="", msg=""):
        cnt = f"{n} / {N}" if N > 0 else f"{n} 次（直到停止）"
        st = self.STATE_TXT.get(state, state)
        return f"{cnt}　·　最後觸發 {self._cap_last}　·　{st}" + (f"：{msg}" if msg and state == "paused" else "")

    def _on_cap(self, kind, d):
        N = self.cap.cfg["count"] if self.cap else 0
        if kind == "progress":
            self.v_prog.set(self._prog_text(d.get("n", 0), N, d.get("state", ""), d.get("msg", "")))
            self.v_status.set(f"擷取 {self._prog_text(d.get('n', 0), N, d.get('state', ''), d.get('msg', ''))}")
            self.lb_v.configure(text="暫停" if d.get("state") == "paused" else "擷取中",
                                fg=(ERR_C if d.get("state") == "paused" else PASS_C)[0],
                                bg=(ERR_C if d.get("state") == "paused" else PASS_C)[1])
        elif kind == "captured" and d.get("missed"):
            if N > 0:
                self.pb.configure(value=d["seq"])
            self.v_prog.set(self._prog_text(d["seq"], N, "未觸發"))
            self.saved_txt.insert("end", f"#{d['seq']}  未觸發（{d.get('arb', '')}）：{d.get('msg', '')}\n")
            self.saved_txt.see("end")
            if d.get("nvme") is not None:
                self._nvme_fill(d["nvme"])
        elif kind == "captured":
            self._cap_last = d["time"]
            if N > 0:
                self.pb.configure(value=d["seq"])
            self.v_prog.set(self._prog_text(d["seq"], N, "已存"))
            for m, p in d["pngs"].items():
                self.shots[m]["src"] = p
            self._fit_shots()
            ws = {}
            for (m, ch), w in d["waves"].items():
                ws.setdefault(m, {})[ch] = w
            for m in (MASTER_DEF, SLAVE_DEF):
                if m in ws:
                    self.waves[m] = ws[m]
            self._draw()
            self._seq_title = f"#{d['seq']} {d['time']}　"
            self._seq_show(d["seq_rows"], d["waves"], self._seq_title)
            self._spec_refresh(waves=d["waves"], seq_rows=d["seq_rows"], pm_rows=d.get("pm_rows"))
            if self.cap and self.cap.cfg.get("arb"):
                self._select_mon_tab("時序")
            if d.get("nvme") is not None:
                self._nvme_fill(d["nvme"])
                if self.v.get("nvme_on") and self.v["nvme_on"].get():
                    self._select_mon_tab("NVMe")  # 認碟表；時序已寫入分頁，使用者可再切回去
                    self._select_mon_tab("時序")
            tv = self.tv_pm
            tv.delete(*tv.get_children())
            for r in d["pm_rows"]:
                f6 = (lambda v: "" if v is None else f"{v:.5g}")
                tv.insert("", "end", values=(r["model"], f"P{r['p']}", r["label"], f6(r["scope"]), f6(r["pc"]),
                                             "" if r["pct"] is None else f"{r['pct']:.2f}", r["ok"]))
            self.saved_txt.insert("end", f"#{d['seq']}  {d['time']}  {d['folder']}\n" +
                                  "".join(f"    {os.path.basename(p)}\n" for p in d["files"]))
            self.saved_txt.see("end")
            self.last_saved = d["files"]
        elif kind in ("finished", "error"):
            self.pb.stop()
            self.btn_go.configure(state="normal")
            self.btn_arb.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.lb_v.configure(text="已同步", fg=PASS_C[0], bg=PASS_C[1])
            if kind == "finished":
                gaps = d.get("gaps") or []
                self.v_prog.set(self._prog_text(d["n"], N, "完成") + (f"（斷線空檔 {len(gaps)} 次）" if gaps else ""))
                self.v_status.set(f"擷取完成 {d['n']}{'' if N <= 0 else f' / {N}'} 次 → {d['dir']}")
            else:
                self.v_status.set(f"✗ 擷取停止：{d.get('msg', '')}")

    # ------------------------------------------------------------------ 讀回 / 寫入儀器設定
    def _targets(self):
        t = self.v["io_target"].get()
        return [MASTER_DEF] if t.startswith("主") else [SLAVE_DEF] if t.startswith("輔") else [MASTER_DEF, SLAVE_DEF]

    def _cfg_for(self, m):
        role = "master" if m == self.master_model.get() else "slave"
        mode = self.v["name_mode"].get()
        try:
            lv = float(self.v["trig_level"].get())
        except ValueError:
            lv = None
        return {"model": m, "role": role,
                "trig": {"src": self.v["trig_src"].get().split()[0], "level": lv, "slope": self.v["trig_slope"].get(),
                         "mode": self.v["trig_mode"].get()},
                "timebase": {"tdiv": float(self.v["tdiv"].get() or 0) or None, "delay": float(self.v["delay"].get() or 0)},
                "channels": [{"ch": i + 1, "on": self.v[f"en_{m}_{i}"].get(), "name": self.v[f"nm_{m}_{i}"].get().strip(),
                              "unit": "A" if NAMES[m][i] in UNIT_A else "V"} for i in range(NCH[m])],
                "pm": self._pm_rows(m), "sync": {"on": True},
                "name_mode": "label" if mode.startswith("波形") else "both" if mode.startswith("Alias +") else "name"}

    def write_settings(self):
        from . import settings as ST
        self._snap()
        for m in self._targets():
            try:
                cfg = self._cfg_for(m)
            except ValueError as e:
                self.bg_result(m, "寫入儀器設定", False, str(e))
                continue
            err, warn = ST.validate(cfg)
            if err:
                self.bg_result(m, "寫入儀器設定", False, "；".join(err))
                continue
            for x in warn:
                self.log(f"[{m}] ⚠ {x}")

            def job(m=m, cfg=cfg):
                try:
                    bad = self.links[m].call(lambda d: [] if d.sim else ST.write(d.w, d.q, cfg, log=self.log))
                except Exception as e:  # noqa: BLE001
                    self._bg(m, "寫入儀器設定", False, str(e))
                    return
                self.last_cfg[m] = cfg                      # 重連後重新套用
                self._bg(m, "寫入儀器設定", not bad, "；".join(bad) if bad else "觸發 / 時基 / 通道 / bus name / P1–P24 / 主輔同步")
            threading.Thread(target=job, daemon=True).start()

    def read_settings(self):
        from . import settings as ST
        for m in self._targets():
            role = "master" if m == self.master_model.get() else "slave"

            def job(m=m, role=role):
                try:
                    cfg = self.links[m].call(lambda d: None if d.sim else ST.read(d.q, m, role))
                except Exception as e:  # noqa: BLE001
                    self._bg(m, "讀回儀器設定", False, str(e))
                    return
                self.q_ui.put(("readback", m, cfg))
            threading.Thread(target=job, daemon=True).start()

    def _apply_readback(self, m, cfg):
        if cfg is None:
            self.log(f"[{m}] 讀回儀器設定：模擬示波器，沒有可讀回的設定")
            return
        if m == self.master_model.get() and cfg.get("trig"):
            t = cfg["trig"]
            for k, srcs in (("trig_src", [f"C{i + 1}  {n}" for i, n in enumerate(NAMES[m])]),):
                hit = [x for x in srcs if x.split()[0] == t.get("src")]
                if hit:
                    self.v[k].set(hit[0])
            if t.get("level") is not None:
                self.v["trig_level"].set(f"{t['level']:g}")
            if t.get("slope") in ("POS", "NEG", "EITHER"):
                self.v["trig_slope"].set(t["slope"])
            tb = cfg.get("timebase") or {}
            if tb.get("tdiv"):
                self.v["tdiv"].set(f"{tb['tdiv']:g}")
            if tb.get("delay") is not None:
                self.v["delay"].set(f"{tb['delay']:g}")
        for c in cfg.get("channels") or []:
            i = int(c["ch"]) - 1
            if i < NCH[m]:
                self.v[f"en_{m}_{i}"].set(bool(c.get("on")))
                if c.get("name"):
                    self.v[f"nm_{m}_{i}"].set(c["name"])
        self.bg_result(m, "讀回儀器設定", True, "觸發 / 時基 / 通道 / bus name 已帶回畫面")

    # ------------------------------------------------------------------ 實圖即時更新 ＋ 連線心跳
    def _live_tick(self):
        busy = self.cap is not None and self.cap.running
        every = {"0.5 s": 0.5, "1 s": 1.0, "2 s": 2.0, "5 s": 5.0}.get(self.v["shot_every"].get())
        if not busy and not getattr(self, "_live_busy", False):
            ups = [m for m, ln in self.links.items() if ln.up]
            live = bool(self.v["live"].get() and every and len(ups) == 2)
            self._live_busy = True

            def job():
                import tempfile
                out = {}
                try:
                    for m in ups:
                        ln = self.links[m]
                        if live:
                            png = ln.call(lambda d: d.screen_png())
                            p = os.path.join(tempfile.gettempdir(), f"scope_wave_saver_live_{m}.png")
                            with open(p, "wb") as f:
                                f.write(png)
                            out[m] = p
                        else:
                            ln.q("*OPC?")                        # 心跳：閒置時也能發現 USB 被拔
                except Exception:  # noqa: BLE001
                    pass                                          # 斷線已由 Link 處理（紀錄 ＋ 重連）
                finally:
                    self._live_busy = False
                if out and len(out) == 2:
                    self.q_ui.put(("shots", out))
            threading.Thread(target=job, daemon=True).start()
        self.after(int(1000 * (every or 3.0)), self._live_tick)

    def _quit(self):
        if self.cap is not None:
            self.cap.stop()
            if self.cap.running and self.cap.cfg.get("arb"):
                self.cap.th.join(3.0)                 # 等 N6705 安全中止送完
        for ln in list(self.links.values()) + ([self.psu] if self.psu else []):
            ln.close()
        try:
            self._persist_sash()
            self.ui_store.save()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    def _draw(self):
        self.fig.clear()
        merge = self.v_view.get() == "merge"
        stack = self.v["stack"].get()
        roles = ((SLAVE_DEF, "輔 SLAVE"), (MASTER_DEF, "主 MASTER"))      # 左 = 輔、右 = 主
        fam = [self.fam, "Noto Sans CJK JP", "DejaVu Sans"]
        if merge:
            allw = [(m, role, i, w) for m, role in roles for i, w in self.waves[m].items()]
            n = len(allw)
            for k, (m, role, i, (t, y)) in enumerate(allw):
                ax = self.fig.add_subplot(n, 1, k + 1) if stack else (self.fig.axes[0] if self.fig.axes else self.fig.add_subplot(111))
                col = COLORS[i - 1]
                nm = NAMES[m][i - 1]
                ax.plot(t * 1e3, y, lw=1.0, color=col, ls="-" if m == MASTER_DEF else "--")
                if stack:
                    ax.set_ylabel(f"{'主' if m == MASTER_DEF else '輔'} C{i}\n{nm}", rotation=0, ha="right", va="center",
                                  fontsize=8, family=fam, color=col)
                    ax.tick_params(labelsize=7, labelbottom=(k == n - 1))
                    ax.set_yticks([])
                ax.axvline(0, color="#cf222e", lw=0.8)
                ax.grid(alpha=0.3)
            self.fig.axes[0].set_title("合一張：主 8208HD（實線）＋ 輔 6104B（虛線）· 共同時間軸（觸發 t = 0 對齊）",
                                       fontsize=10, family=fam, color="#0E9BD8")
            self.fig.axes[-1].set_xlabel("時間 (ms)", family=fam)
            self.fig.subplots_adjust(left=0.1, right=0.98, top=0.96, bottom=0.05, hspace=0.08)
        else:
            gs = self.fig.add_gridspec(8, 2, wspace=0.18, hspace=0.12)
            for col_i, (m, role) in enumerate(roles):
                w = self.waves[m]
                n = len(w)
                for k, (i, (t, y)) in enumerate(w.items()):
                    rows = slice(k * 8 // n, (k + 1) * 8 // n)
                    ax = self.fig.add_subplot(gs[rows, col_i])
                    col = COLORS[i - 1]
                    ax.plot(t * 1e3, y, lw=1.0, color=col)
                    ax.axvline(0, color="#cf222e", lw=0.8)
                    ax.grid(alpha=0.3)
                    ax.tick_params(labelsize=7, labelbottom=(k == n - 1))
                    ax.text(0.01, 0.82, f"C{i} {NAMES[m][i - 1]}" + (" (A)" if NAMES[m][i - 1] in UNIT_A else " (V)"), transform=ax.transAxes, fontsize=8, color=col,
                            weight="bold", family=fam)
                    if k == 0:
                        sl = {"POS": "↑", "NEG": "↓", "EITHER": "↕"}.get(self.v["trig_slope"].get(), "")
                        tsrc = self.v["trig_src"].get() + f" {sl} {self.v['trig_level'].get()} V ✓"
                        ax.set_title(f"{role}　{TITLE_M[m]}" + ("　EXT 觸發 ✓" if m == SLAVE_DEF else f"　{tsrc}"),
                                     fontsize=10, family=fam, color="#0E9BD8")
                    if k == n - 1:
                        ax.set_xlabel("時間 (ms)", family=fam, fontsize=8)
            self.fig.subplots_adjust(left=0.04, right=0.99, top=0.95, bottom=0.06)
        self.canvas.draw_idle()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="接實機（USB VISA）；預設 = 示範模式（模擬示波器）")
    ap.add_argument("--view", default="stack", choices=("stack", "merge", "seq"), help="開在哪個右欄子視窗")
    ap.add_argument("--save-to", default="", help="（測試）照目前顯示存檔到此資料夾")
    ap.add_argument("--scroll", type=float, default=0.0, help="左欄捲到（0–1）")
    ap.add_argument("--shot", default="", help="截圖存檔路徑（給 review 用）")
    ap.add_argument("--shot-after", type=float, default=2.5, help="幾秒後截圖")
    ap.add_argument("--layout", default="", help="版面 json（收合／分隔條）；「-」= 不讀不寫")
    ap.add_argument("--fold", default="", help="（測試／截圖）強制收合的子視窗 key，逗號分隔")
    ap.add_argument("--unfold", default="", help="（測試／截圖）強制展開的子視窗 key，逗號分隔")
    ap.add_argument("--capture", type=int, default=None, help="（測試）啟動後自動開始擷取 N 次（0 = 直到停止）")
    ap.add_argument("--arb", type=int, default=None, help="（測試）啟動後自動 ⚡ ARB＋擷取，循環 N 次")
    ap.add_argument("--arb-tpl", default="", help="（測試）共用範本 RAMP / BROWNOUT / ONOFFON")
    ap.add_argument("--arb-ch", type=int, default=None, help="（測試）ARB 輸出通道 1/2/3/4（預設 3）")
    ap.add_argument("--unplug", default="", help="（示範測試）MODEL:秒數@幾秒後，例如 6104B:4@3")
    ap.add_argument("--folder", default="", help="存檔資料夾")
    ap.add_argument("--form", default="", help="（測試／截圖）DUT 規格 U2 / M2")
    ap.add_argument("--profile", default="", help="（測試／截圖）電源設計表名稱")
    a = ap.parse_args(argv)
    # 截圖時用暫時版面檔，避免寫進使用者的 ui_layout.json
    layout = a.layout or ("-" if a.shot else "")
    app = WaveSaverUI(demo=not a.real, layout=layout)
    if a.folder:
        app.v["folder"].set(a.folder)
    if a.arb_ch is not None:
        app._pick_ch(int(a.arb_ch))
    if a.arb_tpl:
        app._pick_tpl(a.arb_tpl.strip().upper())
    if a.form:
        key = a.form.strip().upper().replace("U.2", "U2").replace("M.2", "M2").replace("U2/", "U2")
        if key.startswith("U2"):
            key = "U2"
        elif key.startswith("M2"):
            key = "M2"
        app._want_form = key
    if a.profile:
        app._want_profile = a.profile.strip()
    if a.fold or a.unfold:
        app.after(200, lambda: app._apply_fold(a.fold, a.unfold))
    if a.view == "merge":
        app.after(300, lambda: app._select_mon_tab("合成"))
    if a.view == "seq":
        app.after(300, lambda: app._select_mon_tab("時序"))
    if a.arb is not None:
        app.after(1800, lambda: app.start_arb_capture(loops=a.arb))
    if a.capture is not None:
        app.after(1500, lambda: app.start_capture(count=a.capture))
    if a.unplug:
        mdl, rest = a.unplug.split(":")
        sec, at = rest.split("@")
        app.after(int(float(at) * 1000), lambda: app.sim_unplug(mdl, float(sec)))
    if a.save_to:
        app.after(2000, lambda: print("\n".join(app.do_save(data=False, folder=a.save_to))))
    if a.scroll:
        def _sc():
            for w in app.body.left.winfo_children():
                for c in [w] + list(w.winfo_children()):
                    if isinstance(c, tk.Canvas):
                        c.yview_moveto(a.scroll)
        app.after(1800, _sc)
    if a.shot:
        def shot():
            app.update_idletasks()
            from PIL import ImageGrab
            ImageGrab.grab(bbox=(0, 0, 1920, 1080), xdisplay=os.environ.get("DISPLAY")).save(a.shot)
            app._quit()
        app.after(int(a.shot_after * 1000), shot)
    app.mainloop()


if __name__ == "__main__":
    main()
