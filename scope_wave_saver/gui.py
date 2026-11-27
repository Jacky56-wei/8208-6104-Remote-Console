# -*- coding: utf-8 -*-
"""雙示波器波形工具（UI 原型 — 給使用者 review；儀器邏輯尚未接上）

版型照 All in Test tool《UI設計風格說明.md》：u2core.theme.apply、工具列 ▶/■ + action_row、狀態列、
UnifiedBody（左：① 儀器連線 + ②③… 設定卡片；右：執行監看分頁）。
右側主畫面 = 兩部示波器「實圖」（SCREEN_DUMP）同時看：左 = 輔（SLAVE）、右 = 主（MASTER）；可改上下排（時間格對齊）。
「合一張」另一分頁：用讀回的波形資料在 PC 重畫（實圖無法疊成一張）。

    U2_TOOL_DIR=<Autotest_tool 路徑> python -m scope_wave_saver.gui --demo
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tkinter as tk
from tkinter import ttk

import numpy as np

_TOOL = os.environ.get("U2_TOOL_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(_TOOL, "u2core")) and _TOOL not in sys.path:
    sys.path.insert(0, _TOOL)

from .pmeas import NONE, PARAMS, default_rows, inst_max  # noqa: E402

PASS_C, FAIL_C, ERR_C = ("#1a7f37", "#dafbe1"), ("#cf222e", "#ffebe9"), ("#9a6700", "#fff8c5")
MASTER_DEF, SLAVE_DEF = "8208HD", "6104B"
NCH = {"8208HD": 8, "6104B": 4}
TITLE_M = {"8208HD": "WaveRunner 8208HD（8 CH）", "6104B": "HDO6104B（4 CH）"}
KEY = {"8208HD": "wr8000", "6104B": "hdo6104"}
ADDR = {"8208HD": "USB0::0x05FF::0x1023::5003N60268::0::INSTR", "6104B": "USB0::0x05FF::0x1023::5108N13350::0::INSTR"}
# 預設 bus name：同 u2core/scope2 A8_NAMES（主 8 CH）/ B4_NAMES（輔 4 CH）
NAMES = {"8208HD": ["CORE", "DDRVPP", "GPIO", "DDRIO", "PHYIO", "NFIO", "NFCORE", "RSTn"],
         "6104B": ["VIN", "VBUS", "BUSPG", "IBUS"]}
VNOM = {"CORE": 0.84, "DDRVPP": 2.5, "GPIO": 1.8, "DDRIO": 1.2, "PHYIO": 1.2, "NFIO": 1.2, "NFCORE": 0.9, "RSTn": 1.8,
        "VIN": 12.0, "VBUS": 12.0, "BUSPG": 3.3, "IBUS": 0.0}
UNIT_A = {"IBUS"}                 # 電流通道（單位 A；Auto scale 不動）
COLORS = ["#F2C744", "#E0457B", "#3FA9F5", "#3BB273", "#F28C28", "#9B59B6", "#1ABC9C", "#7F8C8D"]   # LeCroy C1–C8 色系


def _demo_waves(model, t0=-2e-3, n=2000):
    """示範：上電時序（觸發在 t = 0）"""
    t = np.linspace(t0, 8e-3, n)
    out = {}
    for i, nm in enumerate(NAMES[model]):
        v = VNOM.get(nm, 1.0) or 1.5
        d = (i * 0.45e-3) if model == MASTER_DEF else (-0.6e-3 + i * 0.25e-3)
        tau = 0.25e-3
        y = v * np.clip((t - d) / tau, 0, 1) if not nm.endswith("n") and nm != "BUSPG" else v * (t > d + 1.2e-3)
        if nm == "IBUS":
            y = 0.8 * np.exp(-np.clip(t - d, 0, None) / 1e-3) * (t > d) + 0.15 * (t > d)
        out[i + 1] = (t, y + 0.004 * v * np.random.randn(n))
    return out


class WaveSaverUI(tk.Tk):
    def __init__(self, demo=True):
        super().__init__()
        self.title("雙示波器波形工具 v1.0（8208HD ＋ 6104B · MASTER / SLAVE）— UI 原型")
        self.geometry("1920x1080+0+0")
        self.demo = demo
        self._style()
        self.v = {}
        self.master_model = tk.StringVar(self, value=MASTER_DEF)
        self._build()

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
        ttk.Button(bar, text="▶ 擷取＋存檔", style="Start.TButton", width=14).pack(side="left")
        ttk.Button(bar, text="■ 停止", style="Stop.TButton", state="disabled", width=10).pack(side="left", padx=6)
        ttk.Button(bar, text="⟳ 實圖即時更新", width=13).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="📷 存兩台畫面", width=13).pack(side="left", padx=(0, 6))
        action_row(bar, self, omit=("save", "detail", "wiring", "report"), folder=lambda: None, export=lambda: None)
        self.lb_v = tk.Label(bar, text="已同步", font=(self.fam, 15, "bold"), fg=PASS_C[0], bg=PASS_C[1], width=8)
        self.lb_v.pack(side="right")
        ttk.Label(bar, text="主 / 輔觸發", style="Muted.TLabel").pack(side="right", padx=4)
        bar2 = ttk.Frame(self, padding=(10, 0, 10, 2))
        bar2.pack(fill="x")
        ttk.Label(bar2, text="21:00:12  兩台已觸發（MASTER 8208HD C1 CORE ↑ 0.42 V → AUX OUT → SLAVE 6104B EXT）"
                             "，已存 14 個檔到 桌面\\scope waveform\\PWRON_DUT1_20261127_210012",
                  anchor="e", foreground="#0969da").pack(side="right", fill="x", expand=True)
        if self.demo:
            ttk.Label(bar2, text="【UI 原型 · 示範資料】", foreground="#9a6700").pack(side="left")

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
        self._trigger(body.card("觸發設定（主要：MASTER 觸發通道 / 準位 / 斜率；SLAVE 跟 Aux Out）", 0, 0, num=2))
        self._channels(body.card("通道 ＋ bus name ＋ Auto scale", 1, 0, num=3))
        self._pm(body.card("量測 P1–P24（每台示波器）", 2, 0, num=4))
        self._save(body.card("存檔（中控電腦）", 3, 0, num=5))
        self._monitor(body.right_nb)

    # ---- ① 儀器連線（自動連線＋Auto scale＋對時）
    def _conn(self, body, InstRow):
        for role, m in (("MASTER 主", MASTER_DEF), ("SLAVE 輔", SLAVE_DEF)):
            st = tk.StringVar(self, value=f"已連線（自動）· {role}")
            r = InstRow(body.conn, "wavesaver", KEY[m], f"{TITLE_M[m]}", addr_var=self._sv(f"addr_{m}", ADDR[m]),
                        connect=lambda: None, connected=lambda: True, status_var=st, app_btn_text=True, btn_text="中斷")
            info = ttk.Frame(body.conn)
            info.pack(fill="x", padx=(22, 0))
            ttk.Label(info, text=("LECROY,WR8208HD,5003N60268 · " if m == MASTER_DEF else "LECROY,HDO6104B,5108N13350 · ")
                      + "🕒 時鐘已對中控 PC 21:00:05（差 0 s）· Auto scale ✓ " + ("8/8" if m == MASTER_DEF else "4/4") + " CH",
                      style="U.Muted.TLabel").pack(side="left")
            try:
                r.dot.set("ok")
            except Exception:  # noqa: BLE001
                pass
        op = ttk.Frame(body.conn)
        op.pack(fill="x", pady=(2, 2))
        ttk.Checkbutton(op, text="開程式自動連線", variable=self._bv("auto_conn", True)).pack(side="left")
        ttk.Checkbutton(op, text="連線後 Auto scale", variable=self._bv("auto_scale", True)).pack(side="left", padx=8)
        ttk.Checkbutton(op, text="連線時同步中控電腦時間", variable=self._bv("timesync", True)).pack(side="left")
        ttk.Button(op, text="🕒 立即對時", style="U.Small.TButton").pack(side="left", padx=6)
        ttk.Button(op, text="⇕ 立即 Auto scale", style="U.Small.TButton").pack(side="left")

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
        ttk.Button(bf, text="⤒ 套用觸發到兩台", style="Primary.TButton").pack(side="left")
        ttk.Button(bf, text="▶ SINGLE（輔先 arm，主再 arm）").pack(side="left", padx=6)
        ttk.Button(bf, text="⤓ 讀回示波器目前觸發", style="U.Small.TButton").pack(side="left")
        ttk.Label(bf, text="讀回 ✓ C1 0.42 V POS", foreground=PASS_C[0]).pack(side="left", padx=8)
        sl = ttk.LabelFrame(f, text=" SLAVE 觸發（Aux Out → EXT，BNC）", padding=(8, 2, 8, 4))
        sl.pack(fill="x", pady=(8, 0))
        r = ttk.Frame(sl)
        r.pack(fill="x")
        ttk.Checkbutton(r, text="主 / 輔硬體觸發同步", variable=self._bv("sync_on", True)).pack(side="left")
        ttk.Label(r, text="  EXT 來源").pack(side="left")
        ttk.Combobox(r, textvariable=self._sv("ext_src", "EX10"), values=["EX10", "EX"], state="readonly",
                     width=6).pack(side="left", padx=2)
        ttk.Label(r, text="準位").pack(side="left")
        ttk.Entry(r, textvariable=self._sv("ext_lv", "0.5"), width=5).pack(side="left", padx=2)
        ttk.Label(r, text="V  斜率 ↑ POS").pack(side="left")
        ttk.Checkbutton(r, text="10 MHz 同步（Ref Out → Ref In）", variable=self._bv("ref10m", False)).pack(side="left", padx=10)
        r2 = ttk.Frame(sl)
        r2.pack(fill="x")
        ttk.Checkbutton(r2, text="輔的 T/div、Delay、記憶體深度跟主一樣", variable=self._bv("align", True)).pack(side="left")
        ttk.Label(r2, text="  MASTER AUX OUT = TriggerOut ✓　SLAVE EXT 觸發 ✓（讀回）", foreground=PASS_C[0]).pack(side="left")
        ttk.Label(sl, text="接線：MASTER 背板 AUX OUT ──BNC── SLAVE 前面板 EXT（勾 10 MHz 同步再加 Ref Out → Ref In）",
                  style="U.Muted.TLabel").pack(anchor="w")

    # ---- ③ 通道＋bus name＋Auto scale
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
            heads = ("存", "CH", "bus name", "標稱 V", "V/div（Auto）", "Offset", "示波器上名稱")
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
        ttk.Label(f, text="Auto scale：依 bus name 的標稱電壓讓波形佔 6 / 8 格（u2core.scopescale）；電流 / 無標稱的通道不動",
                  style="U.Muted.TLabel").pack(anchor="w", pady=(2, 0))

    # ---- ④ P1–P24
    def _pm(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Button(top, text="預設（每 CH MAX / MIN）", style="U.Small.TButton").pack(side="left")
        ttk.Button(top, text="⤒ 寫入示波器", style="U.Small.TButton").pack(side="left", padx=4)
        ttk.Button(top, text="⤓ 讀回示波器", style="U.Small.TButton").pack(side="left")
        ttk.Label(top, text="  8208HD：示波器 P1–P12、P13–P24 PC 計算｜6104B：示波器 P1–P8、P9–P24 PC 計算",
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
            chs = [f"C{i + 1}" for i in range(NCH[m])]
            im = inst_max(m)
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
                    ttk.Label(t, text="示波器" if i < im else "PC", style="U.Muted.TLabel" if i >= im else "TLabel"
                              ).grid(row=rr, column=gx + 6, padx=(2, 0))

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
        ttk.Label(f, text="每次一個資料夾：<前綴>_<日期時間>\\  主_8208HD_C1_CORE.trc / .csv、輔_6104B_C1_VIN.csv、主_8208HD.png、"
                          "pmeas.csv、info.json（IDN、觸發、時基、bus name）",
                  style="U.Muted.TLabel").pack(anchor="w", pady=(4, 0))

    # ---- 右：執行監看（兩台示波器實圖同時看）
    def _monitor(self, nb):
        rf = ttk.Frame(nb, padding=2)
        nb.add(rf, text="  兩台實機畫面（實圖）  ")
        tb = ttk.Frame(rf)
        tb.pack(fill="x")
        ttk.Label(tb, text="排列").pack(side="left")
        self.v_lay = tk.StringVar(self, value="side")
        for txt, val in (("並排（左 輔 ｜ 右 主）", "side"), ("上下（時間格對齊：輔 上 / 主 下）", "stack")):
            ttk.Radiobutton(tb, text=txt, value=val, variable=self.v_lay, command=self._layout_shots).pack(side="left", padx=6)
        ttk.Label(tb, text="  更新").pack(side="left")
        ttk.Combobox(tb, textvariable=self._sv("shot_every", "1 s"), values=["手動", "0.5 s", "1 s", "2 s", "5 s"],
                     state="readonly", width=6).pack(side="left", padx=2)
        ttk.Checkbutton(tb, text="即時更新", variable=self._bv("live", True)).pack(side="left", padx=6)
        ttk.Checkbutton(tb, text="擷取中暫停（不搶 USB）", variable=self._bv("pause_busy", True)).pack(side="left")
        self.shot_area = tk.Frame(rf, bg="#FFFFFF")
        self.shot_area.pack(fill="both", expand=True, pady=(4, 0))
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        self.shots = {}
        for m, role in ((SLAVE_DEF, "輔 SLAVE"), (MASTER_DEF, "主 MASTER")):
            fr = tk.Frame(self.shot_area, bg="#FFFFFF", highlightthickness=1, highlightbackground="#A9DDE0")
            hd = tk.Frame(fr, bg="#0E9BD8")
            hd.pack(fill="x")
            tk.Label(hd, text=f" {role}　{TITLE_M[m]}", bg="#0E9BD8", fg="white", font=(self.fam, 10, "bold")).pack(side="left")
            tk.Label(hd, text=("EXT 觸發 ✓" if m == SLAVE_DEF else "C1 CORE ↑ 0.42 V ✓") + "　SCREEN_DUMP 21:00:12　",
                     bg="#0E9BD8", fg="white", font=(self.fam, 9)).pack(side="right")
            cv = tk.Label(fr, bg="#000000")
            cv.pack(fill="both", expand=True)
            ft = tk.Frame(fr, bg="#FFFFFF")
            ft.pack(fill="x")
            ttk.Button(ft, text="📷 存這張", style="U.Small.TButton").pack(side="left", padx=2, pady=2)
            ttk.Button(ft, text="⤢ 放大", style="U.Small.TButton").pack(side="left", padx=2)
            ttk.Label(ft, text="示意圖（未連實機）" if self.demo else "", foreground="#9a6700").pack(side="left", padx=6)
            self.shots[m] = {"fr": fr, "lab": cv, "src": os.path.join(here, f"placeholder_{m}.png"), "tk": None}
        self.shot_area.bind("<Configure>", lambda _e: self.after_idle(self._fit_shots))
        self._layout_shots()
        # PC 合成波形（合一張只能用讀回的波形資料重畫：兩張實圖的畫面配置 / 格數不同，無法疊成一張實圖）
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure
        wf = ttk.Frame(nb, padding=2)
        nb.add(wf, text="  合一張（PC 合成波形）  ")
        tb2 = ttk.Frame(wf)
        tb2.pack(fill="x")
        self.v_view = tk.StringVar(self, value="merge")
        ttk.Checkbutton(tb2, text="每通道分開排（stack）", variable=self._bv("stack", True), command=self._draw).pack(side="left")
        ttk.Label(tb2, text="用兩台讀回的波形資料（WAVEFORM? ALL）畫在同一時間軸，觸發 t = 0 對齊；實圖請看上一個分頁",
                  style="U.Muted.TLabel").pack(side="left", padx=8)
        self.fig = Figure(figsize=(11, 8.5), dpi=96, facecolor="#FFFFFF")
        self.canvas = FigureCanvasTkAgg(self.fig, wf)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.waves = {m: _demo_waves(m) for m in (MASTER_DEF, SLAVE_DEF)}
        self._draw()
        pf = ttk.Frame(nb, padding=4)
        nb.add(pf, text="  P 量測結果  ")
        tv = ttk.Treeview(pf, columns=("sc", "p", "lab", "inst", "pc", "pct", "ok"), show="headings", height=20)
        for c, h, w in (("sc", "示波器", 80), ("p", "P", 40), ("lab", "量測", 220), ("inst", "示波器值", 110),
                        ("pc", "PC 值", 110), ("pct", "差異 %", 70), ("ok", "比對", 70)):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="center")
        tv.pack(fill="both", expand=True)
        nb.add(ttk.Frame(nb, padding=4), text="  存檔結果  ")
        nb.add(ttk.Frame(nb, padding=4), text="  紀錄  ")
        self.mon_nb = nb

    def _layout_shots(self):
        side = self.v_lay.get() == "side"
        for k, m in enumerate((SLAVE_DEF, MASTER_DEF)):
            fr = self.shots[m]["fr"]
            fr.grid_forget()
            fr.grid(row=0 if side else k, column=k if side else 0, sticky="nsew", padx=2, pady=2)
        for i in range(2):
            self.shot_area.columnconfigure(i, weight=1 if (side or i == 0) else 0, uniform="c" if side else "")
            self.shot_area.rowconfigure(i, weight=1 if (not side or i == 0) else 0, uniform="r" if not side else "")
        self.after(50, self._fit_shots)

    def _fit_shots(self):
        from PIL import Image, ImageTk
        for m, d in self.shots.items():
            lab = d["lab"]
            w, h = max(lab.winfo_width(), 50), max(lab.winfo_height(), 50)
            try:
                im = Image.open(d["src"])
            except Exception:  # noqa: BLE001
                continue
            k = min(w / im.width, h / im.height)
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
            d["tk"] = ImageTk.PhotoImage(im)
            lab.configure(image=d["tk"])

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
    ap.add_argument("--demo", action="store_true", default=True)
    ap.add_argument("--view", default="side", choices=("side", "stack", "merge"))
    ap.add_argument("--scroll", type=float, default=0.0, help="左欄捲到（0–1）")
    ap.add_argument("--shot", default="", help="截圖存檔路徑（給 review 用）")
    a = ap.parse_args(argv)
    app = WaveSaverUI(demo=a.demo)
    if a.view == "merge":
        app.after(300, lambda: app.mon_nb.select(1))
    elif a.view == "stack":
        app.after(300, lambda: (app.v_lay.set("stack"), app._layout_shots()))
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
            x, y = app.winfo_rootx(), app.winfo_rooty()
            ImageGrab.grab(bbox=(0, 0, 1920, 1080), xdisplay=os.environ.get("DISPLAY")).save(a.shot)
            app.destroy()
        app.after(2500, shot)
    app.mainloop()


if __name__ == "__main__":
    main()
