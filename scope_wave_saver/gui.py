# -*- coding: utf-8 -*-
"""雙示波器波形工具（示範模式 = 模擬示波器跑完整流程；--real 接實機，尚未實機測試）

版型照 All in Test tool《UI設計風格說明.md》：u2core.theme.apply、工具列 ▶/■ + action_row、狀態列、
UnifiedBody（左：① 儀器連線 + ②③… 設定卡片；右：執行監看分頁）。
右側主畫面 = 兩部示波器「實圖」（SCREEN_DUMP）上下無縫接合：上 = 輔（SLAVE）、下 = 主（MASTER），時間格對齊。
「合一張」另一分頁（只看）：用讀回的波形資料在 PC 重畫。存檔一律存上下實圖。
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
TITLE_M = {"8208HD": "WaveRunner 8208HD（8 CH）", "6104B": "HDO6104B（4 CH）"}
KEY = {"8208HD": "wr8000", "6104B": "hdo6104"}
ADDR = {"8208HD": "USB0::0x05FF::0x1023::5003N60268::0::INSTR", "6104B": "USB0::0x05FF::0x1023::5108N13350::0::INSTR"}
COLORS = ["#F2C744", "#E0457B", "#3FA9F5", "#3BB273", "#F28C28", "#9B59B6", "#1ABC9C", "#7F8C8D"]   # LeCroy C1–C8 色系


def _demo_waves(model, n=2000):
    """示範：上電時序（觸發在 t = 0；同 lecroy.sim_waves）"""
    from .lecroy import sim_waves
    return sim_waves(model, n=n)


class WaveSaverUI(tk.Tk):
    def __init__(self, demo=True):
        super().__init__()
        self.title("雙示波器波形工具 v1.1（8208HD ＋ 6104B · MASTER / SLAVE）" + ("— 示範（模擬示波器）" if demo else ""))
        self.geometry("1920x1080+0+0")
        self.demo = demo
        self._style()
        self.v = {}
        self.q_ui = queue.Queue()                 # 背景執行緒 → UI（Tk 只在主執行緒更新）
        self.cap = None
        self.last_cfg = {}                        # 每台上次「寫入儀器設定」的內容（重連後重新套用）
        self.snap = {}                            # 背景重連用的 UI 快照（bus name 等；主執行緒更新）
        self.master_model = tk.StringVar(self, value=MASTER_DEF)
        self.bus = None
        self.links = {}
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

    def _sv(self, k, d):
        self.v[k] = tk.StringVar(self, value=str(d))
        return self.v[k]

    def _bv(self, k, d):
        self.v[k] = tk.BooleanVar(self, value=bool(d))
        return self.v[k]

    # ------------------------------------------------------------------ 版面
    def _build(self):
        try:
            from u2core.ui import InstRow, Tip, UnifiedBody, action_row
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
        self._conn(body, InstRow)
        page = body.page
        page.columnconfigure(0, weight=1)
        self._trigger(body.card("觸發設定（MASTER 觸發通道 / 準位 / 斜率）", 0, 0, num=2))
        self._channels(body.card("通道 ＋ bus name", 1, 0, num=3))
        self._pm(body.card("量測 P1–P24（每台示波器）", 2, 0, num=4))
        self._save(body.card("存檔（中控電腦）", 3, 0, num=5))
        self._capture_card(body.card("連續擷取（觸發次數）", 4, 0, num=6))
        self._seq_card(body.card("上電時序（PC 計算）", 5, 0, num=7))
        self._monitor(body.right_nb)

    # ---- ① 儀器連線（自動連線；對時 / Auto scale 背景做，只寫紀錄，失敗才顯示）
    def _conn(self, body, InstRow):
        self.rows, self.st_var, self.idn_var = {}, {}, {}
        for role, m in (("MASTER 主", MASTER_DEF), ("SLAVE 輔", SLAVE_DEF)):
            st = self.st_var[m] = tk.StringVar(self, value=f"連線中… · {role}")
            r = InstRow(body.conn, "wavesaver", KEY[m], f"{TITLE_M[m]}", addr_var=self._sv(f"addr_{m}", ADDR[m]),
                        connect=lambda: None, connected=lambda m=m: bool(self.links.get(m) and self.links[m].up),
                        status_var=st, app_btn_text=True, btn_text="中斷")
            self.rows[m] = r
            try:                                           # 斷線 = 紅燈（InstRow 的灰燈改紅）
                _orig = r.dot.set
                r.dot.set = lambda c, m=m, _o=_orig: _o(RED if (self.links.get(m) and self.links[m].state == "down") else c)
            except Exception:  # noqa: BLE001
                pass
            info = ttk.Frame(body.conn)
            info.pack(fill="x", padx=(22, 0))
            self.idn_var[m] = tk.StringVar(self, value="")
            ttk.Label(info, textvariable=self.idn_var[m], style="U.Muted.TLabel").pack(side="left")
            # 對時 / Auto scale / 重連 在背景做、只寫紀錄；失敗 / 斷線才在這裡顯示（平常隱藏）
            self.bg_warn = getattr(self, "bg_warn", {})
            self.bg_warn[m] = tk.Label(info, text="", fg=FAIL_C[0], bg=FAIL_C[1], font=(self.fam, 9), padx=5)
            if self.demo:
                ttk.Button(info, text="模擬拔 USB 5 s（示範）", style="U.Small.TButton",
                           command=lambda m=m: self.sim_unplug(m, 5.0)).pack(side="right")

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
        ttk.Combobox(g, textvariable=self._sv("trig_src", srcs[0]), values=srcs, state="readonly", width=14,
                     font=(self.fam, 11)).grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(g, text="準位", style="Trig.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(g, textvariable=self._sv("trig_level", "0.42"), from_=-50, to=50, increment=0.01, width=8,
                    font=(self.fam, 11)).grid(row=0, column=3, sticky="w", padx=(6, 2))
        ttk.Label(g, text="V（50 % × 標稱 0.84 V）", style="U.Muted.TLabel").grid(row=0, column=4, sticky="w", padx=(0, 18))
        ttk.Label(g, text="斜率", style="Trig.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
        sf = ttk.Frame(g)
        sf.grid(row=1, column=1, columnspan=4, sticky="w", pady=(6, 0), padx=(6, 0))
        sv = self._sv("trig_slope", "POS")
        for txt, val in (("↑ Rising 上升", "POS"), ("↓ Falling 下降", "NEG"), ("↕ Either 雙向", "EITHER")):
            ttk.Radiobutton(sf, text=txt, value=val, variable=sv).pack(side="left", padx=(0, 14))
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
        ttk.Label(bf, text="讀回 ✓ C1 0.42 V POS", foreground=PASS_C[0]).pack(side="left", padx=8)
        # 主 / 輔同步固定在背景做（主 8208HD AUX OUT ──BNC── 輔 6104B EXT；EX10、0.5 V、↑；輔時基跟主），UI 不顯示
        self._bv("sync_on", True)
        self._bv("align", True)

    # ---- ③ 通道＋bus name
    def _channels(self, f):
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
                ttk.Label(t, text=f"{vn:g}" if vn else ("電流 A" if nm in UNIT_A else "—")).grid(row=rr, column=3)
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
    def _monitor(self, nb):
        rf = ttk.Frame(nb, padding=2)
        nb.add(rf, text="  兩台實機畫面（實圖）  ")
        self.v_lay = tk.StringVar(self, value="stack")       # 排列只留上下（輔 上 / 主 下，無縫）
        tb = ttk.Frame(rf)
        tb.pack(fill="x")
        ttk.Label(tb, text="更新").pack(side="left")
        ttk.Combobox(tb, textvariable=self._sv("shot_every", "1 s"), values=["手動", "0.5 s", "1 s", "2 s", "5 s"],
                     state="readonly", width=6).pack(side="left", padx=2)
        ttk.Checkbutton(tb, text="即時更新", variable=self._bv("live", True)).pack(side="left", padx=6)
        ttk.Checkbutton(tb, text="擷取中暫停（不搶 USB）", variable=self._bv("pause_busy", True)).pack(side="left")
        # 共用標題列（兩台的狀態、存檔 / 放大都在最上面；上下排時兩張圖中間不放任何東西）
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
        nb.bind("<<NotebookTabChanged>>", self._tab_changed, add="+")
        # PC 合成波形（合一張只能用讀回的波形資料重畫：兩張實圖的畫面配置 / 格數不同，無法疊成一張實圖）
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        wf = ttk.Frame(nb, padding=2)
        nb.add(wf, text="  合一張（PC 合成波形）  ")
        tb2 = ttk.Frame(wf)
        tb2.pack(fill="x")
        self.v_view = tk.StringVar(self, value="merge")
        ttk.Checkbutton(tb2, text="每通道分開排（stack）", variable=self._bv("stack", True), command=self._draw).pack(side="left")
        ttk.Label(tb2, text="只看：兩台讀回的波形資料（WAVEFORM? ALL）畫在同一時間軸，觸發 t = 0 對齊；存檔一律存上下實圖",
                  style="U.Muted.TLabel").pack(side="left", padx=8)
        self.fig = Figure(figsize=(11, 8.5), dpi=96, facecolor="#FFFFFF")
        self.canvas = FigureCanvasTkAgg(self.fig, wf)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.waves = {m: _demo_waves(m) for m in (MASTER_DEF, SLAVE_DEF)}
        self._draw()
        self._seq_tab(nb)
        pf = ttk.Frame(nb, padding=4)
        nb.add(pf, text="  P 量測結果  ")
        tv = ttk.Treeview(pf, columns=("sc", "p", "lab", "inst", "pc", "pct", "ok"), show="headings", height=20)
        for c, h, w in (("sc", "示波器", 80), ("p", "P", 40), ("lab", "量測", 220), ("inst", "示波器值", 110),
                        ("pc", "PC 值", 110), ("pct", "差異 %", 70), ("ok", "比對", 70)):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="center")
        tv.pack(fill="both", expand=True)
        self.tv_pm = tv
        sf = ttk.Frame(nb, padding=4)
        nb.add(sf, text="  存檔結果  ")
        self.saved_txt = tk.Text(sf, font=("DejaVu Sans Mono", 9), height=20)
        self.saved_txt.pack(fill="both", expand=True)
        lg = ttk.Frame(nb, padding=4)
        nb.add(lg, text="  紀錄  ")
        self.log_txt = tk.Text(lg, bg="#0d1117", fg="#c9d1d9", font=("DejaVu Sans Mono", 9), height=20)
        self.log_txt.pack(fill="both", expand=True)
        self.mon_nb = nb
        self._layout_shots()

    def _tab_changed(self, _e=None):
        self._note()

    def _note(self):
        self.v_save_note.set("上下實圖（輔 上 / 主 下，裁到格線區、無縫接合）→ 畫面_上下.png；另存兩台原始實圖")
        if hasattr(self, "lb_head"):
            self.lb_head.configure(text="  上：輔 SLAVE  HDO6104B（EXT 觸發 ✓）　／　下：主 MASTER  WaveRunner 8208HD（C1 CORE ↑ 0.42 V ✓）")

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
    def _seq_tab(self, nb):
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        from . import sequencing as SQ
        f = ttk.Frame(nb, padding=2)
        nb.add(f, text="  上 / 下電時序  ")
        hd = ttk.Frame(f)
        hd.pack(fill="x")
        self.v_seq_sum = tk.StringVar(self, value="")
        ttk.Label(hd, textvariable=self.v_seq_sum, font=(self.fam, 10, "bold"), foreground="#0E9BD8").pack(side="left")
        ttk.Button(hd, text="⟳ 用目前設定重算", style="U.Small.TButton", command=self._seq_recalc).pack(side="right")
        cols = ("dir", "ord", "sc", "ch", "rail", "ton", "rise", "slew", "dly", "lim", "ok", "note")
        heads = ("方向", "順序", "示波器", "CH", "rail", "上/下電 ms", "10↔90% ms", "slew rate", "延遲 ms", "限制 ms", "判定", "備註")
        tv = ttk.Treeview(f, columns=cols, show="headings", height=12)
        for c, h, w in zip(cols, heads, (40, 36, 80, 32, 64, 70, 72, 100, 64, 76, 48, 120)):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="center", stretch=c == "note")
        tv.tag_configure("FAIL", foreground="#cf222e")
        tv.tag_configure("PASS", foreground="#1a7f37")
        tv.pack(fill="x")
        self.tv_seq = tv
        self.fig_seq = Figure(figsize=(11, 5.6), dpi=96, facecolor="#FFFFFF")
        self.cv_seq = FigureCanvasTkAgg(self.fig_seq, f)
        self.cv_seq.get_tk_widget().pack(fill="both", expand=True)
        self._SQ = SQ
        self.after(300, self._seq_recalc)

    def _waves_flat(self):
        return {(m, ch): w for m, ws in self.waves.items() for ch, w in ws.items()}

    def _seq_recalc(self):
        o = self._seq_opts()
        wv = self._waves_flat()
        rows = self._SQ.analyze(wv, self._rails_from_ui(), ref=o["ref"], basis=o["basis"], slave_skew_s=o["skew_s"],
                                direction=o["direction"])
        self._seq_show(rows, wv, getattr(self, "_seq_title", ""))

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
        self.after(1500, self._live_tick)

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
        except queue.Empty:
            pass
        self.after(100, self._pump)

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
        return dict(count=n, trig_timeout_s=to, base=base, prefix=self.v["prefix"].get().strip(), chans=chans,
                    fmt={k: self.v[f"fmt_{k}"].get() for k in ("png", "trc", "csv", "npz", "pm")}, pm=pm,
                    rails=self._rails_from_ui(), seq_on=self.v["seq_save"].get(), legend=self._legend(),
                    trig_text=self._trig_text(), **o)

    def start_capture(self, count=None):
        from .capture import Capture
        if self.cap is not None and self.cap.running:
            return
        cfg = self._capture_cfg(count)
        m = self.master_model.get()
        s = SLAVE_DEF if m == MASTER_DEF else MASTER_DEF
        self.cap = Capture({"master": self.links[m], "slave": self.links[s]}, cfg, log=self.log,
                           emit=lambda k, d: self.q_ui.put(("cap", k, d)))
        n = cfg["count"]
        self.pb.configure(mode="determinate" if n > 0 else "indeterminate", maximum=max(n, 1), value=0)
        if n <= 0:
            self.pb.start(80)
        self.btn_go.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._cap_last = "—"
        self.saved_txt.delete("1.0", "end")
        self.cap.start()

    def stop_capture(self):
        if self.cap is not None:
            self.cap.stop()
            self.log("■ 停止（等這一次結束）")

    STATE_TXT = {"arming": "arm 中（輔 → 主）", "waiting": "已 arm，等觸發", "reading": "讀取 / 存檔中", "paused": "⏸ 暫停"}

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
        for ln in self.links.values():
            ln.close()
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
                        ax.set_title(f"{role}　{TITLE_M[m]}" + ("　EXT 觸發 ✓" if m == SLAVE_DEF else "　C1 ↑ 0.42 V ✓"),
                                     fontsize=10, family=fam, color="#0E9BD8")
                    if k == n - 1:
                        ax.set_xlabel("時間 (ms)", family=fam, fontsize=8)
            self.fig.subplots_adjust(left=0.04, right=0.99, top=0.95, bottom=0.06)
        self.canvas.draw_idle()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="接實機（USB VISA）；預設 = 示範模式（模擬示波器）")
    ap.add_argument("--view", default="stack", choices=("stack", "merge", "seq"), help="開在哪個分頁")
    ap.add_argument("--save-to", default="", help="（測試）照目前顯示存檔到此資料夾")
    ap.add_argument("--scroll", type=float, default=0.0, help="左欄捲到（0–1）")
    ap.add_argument("--shot", default="", help="截圖存檔路徑（給 review 用）")
    ap.add_argument("--shot-after", type=float, default=2.5, help="幾秒後截圖")
    ap.add_argument("--capture", type=int, default=None, help="（測試）啟動後自動開始擷取 N 次（0 = 直到停止）")
    ap.add_argument("--unplug", default="", help="（示範測試）MODEL:秒數@幾秒後，例如 6104B:4@3")
    ap.add_argument("--folder", default="", help="存檔資料夾")
    a = ap.parse_args(argv)
    app = WaveSaverUI(demo=not a.real)
    if a.folder:
        app.v["folder"].set(a.folder)
    if a.view == "merge":
        app.after(300, lambda: app.mon_nb.select(1))
    if a.view == "seq":
        app.after(300, lambda: app.mon_nb.select(2))
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
