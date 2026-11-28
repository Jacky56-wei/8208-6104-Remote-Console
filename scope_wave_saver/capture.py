# -*- coding: utf-8 -*-
"""連續擷取（觸發次數 N 或一直到 ■ 停止）：每次觸發自動存一組，帶序號，外加跨次數的 summary.csv。

每一次（照 u2core/scope8208 arm_single / wait_acq、u2core/scope2 wait_b 的順序）：
  1. 兩台都要已連線（斷線 → 暫停，等重連；空檔寫紀錄 ＋ summary 備註）
  2. 輔 SLAVE 先 arm：STOP → INR?（清除）→ TRIG_MODE SINGLE → 讀回 TRIG_MODE? = SINGLE
  3. 主 MASTER 再 arm（同上）→ 等 pre-trigger 填滿（5·T/div + TRIG_DELAY，+ max(0.2 s, 5 %)）→「等觸發」
  4. 等主觸發：每 15 ms INR? bit0（每第 10 次改問 TRIG_MODE? = STOP）
  5. 主觸發後等輔（≤ SLAVE_WAIT_S）→ 兩台 WaitUntilIdle → 才讀
  6. 讀：兩台 SCREEN_DUMP、開啟通道 Cn:WAVEFORM? ALL、P 量測（示波器值 ＋ PC 值）
  7. 存：<序號>_畫面_上下.png（上下無縫）、兩台原始實圖、.trc / CSV / NPZ、P 量測 CSV、上 / 下電時序（含 slew rate）CSV ＋ PNG、info.json
  ARB（N6705，cfg["arb"]）：兩台 arm 完 → 下載各通道 ARB（每次擷取用掃描的下一個值）→ OUTP ON → INIT:TRAN → *TRG
     → 等主觸發（逾時 = ARB 長度 + 1 s；沒觸發 = DUT 沒掉電 / 觸發條件沒成立 → summary 記「未觸發」、換下一個值）
     停止 / 錯誤 → N6705 安全中止（ABOR:TRAN → OUTP OFF，或維持最後電壓）
  7b. 突波／雜訊（noise.py：過衝、欠衝、漣波 Vpp，PC 算）→ 突波雜訊.csv／.png、summary.csv 欄位
  8. summary.csv 加一列（序號、時間、上電 / 下電、輔有沒有觸發、時序判定、順序、每條 rail 延遲與 slew rate、每個 P 值）
資料夾：<存到>\\<前綴>_<日期時間>_連續\\0001_210012\\0001_畫面_上下.png …、summary.csv 在 _連續 資料夾。
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import threading
import time

import numpy as np

from . import composite as CP
from . import pmeas as PM
from . import noise as NZ
from . import sequencing as SQ
from .lecroy import parse_wavedesc
from .link import Disconnected
from .speccmp import SPEC_CSV
from .rails import SLAVE_DEF, sort_rails

POLL_S = 0.015
ARM_CONFIRM_S = 5.0
SLAVE_WAIT_S = 3.0
INR_NEW_SIGNAL = 0x0001


def _num(s):
    import re
    m = re.search(r"[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?", str(s))
    return float(m.group()) if m else None


class TriggerTimeout(RuntimeError):
    pass


class Stopped(RuntimeError):
    pass


def arm_single(link, stop_evt=None):
    """STOP → INR?（清除）→ TRIG_MODE SINGLE → 讀回 TRIG_MODE? = SINGLE → True / None（未確認）"""
    link.w("STOP")
    try:
        link.q("INR?")
    except Disconnected:
        raise
    except Exception:  # noqa: BLE001
        pass
    link.w("TRIG_MODE SINGLE")
    t_end = time.time() + ARM_CONFIRM_S
    while time.time() < t_end:
        if stop_evt is not None and stop_evt.is_set():
            raise Stopped()
        if "SING" in str(link.q("TRIG_MODE?")).upper():
            return True
        time.sleep(0.01)
    return None


def pretrig_s(link):
    try:
        td = _num(link.q("TIME_DIV?")) or 0.0
        dl = _num(link.q("TRIG_DELAY?")) or 0.0
    except Disconnected:
        raise
    except Exception:  # noqa: BLE001
        return 0.0
    return min(max(5.0 * td + dl, 0.0), 10.0 * td) if td > 0 else 0.0


def wait_trig(link, timeout_s, stop_evt=None):
    """True = 已觸發；False = 逾時（timeout_s ≤ 0 → 一直等到停止）"""
    t0, k = time.time(), 0
    while timeout_s <= 0 or time.time() - t0 < timeout_s:
        if stop_evt is not None and stop_evt.is_set():
            raise Stopped()
        if k % 10 == 9:
            hit = "STOP" in str(link.q("TRIG_MODE?")).upper()
        else:
            hit = bool(int(_num(link.q("INR?")) or 0) & INR_NEW_SIGNAL)
        if hit:
            return True
        k += 1
        time.sleep(POLL_S)
    return False


def wait_idle(link, to=10.0):
    try:
        r = str(link.q(f"VBS? 'return=app.WaitUntilIdle({to:g})'")).strip()
        return r.upper().replace("VBS", "").strip() not in ("0", "FALSE", "")
    except Disconnected:
        raise
    except Exception:  # noqa: BLE001
        try:
            return str(link.q("*OPC?")).strip().startswith("1")
        except Disconnected:
            raise
        except Exception:  # noqa: BLE001
            return None


class Capture:
    """背景執行緒跑連續擷取；emit(kind, dict) 給 UI（UI 用 queue 收，Tk 只在主執行緒更新）"""

    def __init__(self, links, cfg, log=print, emit=None, psu=None):
        self.links = links                     # {"master": Link, "slave": Link}
        self.psu = psu                         # N6705 Link（cfg["arb"] 有設才用）
        self.cfg = cfg                         # UI 開始時的設定快照（見 gui._capture_cfg）
        self.log = log
        self.emit = emit or (lambda *_: None)
        self.stop_evt = threading.Event()
        self.th = None
        self.run_dir = None
        self.done = 0
        self.gaps = []
        self._gap_note = ""
        self._sum_cols = None
        self._nz_last = []
        self._arb_txt, self._arb_info = "", None
        self._pending = []

    # ---------------------------------------------------------------- 控制
    def start(self):
        self.th = threading.Thread(target=self._run, name="capture", daemon=True)
        self.th.start()

    def stop(self):
        self.stop_evt.set()

    @property
    def running(self):
        return self.th is not None and self.th.is_alive()

    # ---------------------------------------------------------------- 主迴圈
    def _run(self):
        c = self.cfg
        n_tot = int(c.get("count") or 0)
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = os.path.join(c["base"], "_".join(x for x in (c.get("prefix", ""), stamp) if x)
                                    + ("_連續" if n_tot != 1 else ""))
        os.makedirs(self.run_dir, exist_ok=True)
        self.log(f"▶ 開始擷取：{'一直到 ■ 停止' if n_tot <= 0 else f'{n_tot} 次'} → {self.run_dir}")
        seq = 1
        try:
            while not self.stop_evt.is_set() and (n_tot <= 0 or seq <= n_tot):
                self._wait_links(seq, n_tot)
                if self.stop_evt.is_set():
                    break
                try:
                    res = self.once(seq, n_tot)
                except Disconnected as e:
                    self.log(f"⚠ 第 {seq} 次擷取中斷線（{e}）→ 暫停，重連後重做第 {seq} 次")
                    self.emit("progress", {"n": self.done, "N": n_tot, "state": "paused", "msg": str(e)})
                    continue
                except TriggerTimeout as e:
                    if self._arb_on():                       # ARB：沒觸發也是結果（例 Brownout 沒造成 reset）
                        self.log(f"⚠ 第 {seq} 次：{e}（ARB {self._arb_txt}）→ 記為未觸發，換下一個")
                        self._summary_miss(seq, str(e))
                        self.done = seq
                        self.emit("captured", {"seq": seq, "N": n_tot, "missed": True, "msg": str(e), "arb": self._arb_txt,
                                               "nvme": getattr(self, "_nvme_last", None)})
                        seq += 1
                    else:
                        self.log(f"⚠ 第 {seq} 次：{e} → 重新 arm")
                    continue
                self.done = seq
                self.emit("captured", res)
                seq += 1
        except Stopped:
            pass
        except Exception as e:  # noqa: BLE001
            self.log(f"✗ 擷取停止（錯誤）：{type(e).__name__}: {e}")
            self.emit("error", {"msg": str(e)})
        finally:
            try:
                self._flush_pending(final=True)
            except Exception:  # noqa: BLE001
                pass
            if self._arb_on():
                self._arb_safe("停止" if self.stop_evt.is_set() else "結束")
            for ln in self.links.values():
                try:
                    if ln.up:
                        ln.w("STOP")
                except Exception:  # noqa: BLE001
                    pass
            self.log(f"■ 擷取結束：完成 {self.done}{'' if n_tot <= 0 else f' / {n_tot}'} 次"
                     + (f"，斷線空檔 {len(self.gaps)} 次（共 {sum(g[1] for g in self.gaps):.1f} s）" if self.gaps else "")
                     + f" → {self.run_dir}")
            self.emit("finished", {"n": self.done, "N": n_tot, "dir": self.run_dir, "gaps": list(self.gaps)})

    def _all_links(self):
        return list(self.links.values()) + ([self.psu] if self._arb_on() else [])

    def _arb_on(self):
        return self.psu is not None and bool((self.cfg.get("arb") or {}).get("chans"))

    def _arb_safe(self, why):
        a = self.cfg["arb"]
        chans = [x["ch"] for x in a["chans"]]
        keep = {x["ch"]: x["keep_v"] for x in a["chans"] if a.get("end") == "keep" and x.get("keep_v") is not None}
        from . import n6705 as N
        try:
            if self.psu.up:
                self.psu.call(lambda d: N.abort(d, chans, output_off=True, keep=keep))
                self.log(f"N6705 安全中止（{why}）：ABOR:TRAN → " + ("維持電壓 " + "、".join(f"CH{k} {v:g} V" for k, v in keep.items())
                                                                  if keep else "OUTP OFF") + f"（CH {chans}）")
            else:
                self.log(f"⚠ N6705 斷線，無法送安全中止（{why}）— 請手動確認輸出")
        except Exception as e:  # noqa: BLE001
            self.log(f"✗ N6705 安全中止失敗：{e} — 請手動按 N6705 Output Off")

    def _arb_go(self, seq):
        """下載（這次的掃描值）→ 同一個 *TRG 啟動所有通道。回傳 (info dict, 長度 s)"""
        from . import arbtpl as T
        from . import n6705 as N
        from u2core import arbmodule as AM
        a = self.cfg["arb"]
        info, dur, txt = {"sweep_mode": a.get("sweep_mode", "each"), "channels": []}, 0.0, []
        for x in a["chans"]:
            ns = T.n_sweep(x["tpl"], x["p"])
            idx = ((seq - 1) % ns) if a.get("sweep_mode", "each") == "each" else None
            cfg = T.build(x["tpl"], x["p"], ch=x["ch"], idx=idx, name=x.get("label", ""))
            cmds = self.psu.call(lambda d, cfg=cfg, lb=x.get("label", ""): N.download(d, cfg, lb))
            d_s = float(AM.duration(cfg) or 0.0)
            dur = max(dur, d_s)
            de = T.describe(x["tpl"], x["p"], idx)
            info["channels"].append(dict(de, ch=x["ch"], label=x.get("label", ""), shape=cfg.shape, duration_s=d_s,
                                         repeat=cfg.repeat, scpi=cmds))
            txt.append(f"CH{x['ch']} {x.get('label', '')} {de['template_name']}" + (f" [{de['sweep']}]" if de.get("sweep") else ""))
        self.psu.call(lambda d: N.start(d, [x["ch"] for x in a["chans"]]))
        self._arb_txt = "；".join(txt)
        self._arb_info = info
        self.log(f"⚡ 第 {seq} 次 N6705 ARB 啟動：{self._arb_txt}（{dur * 1e3:.1f} ms）")
        return info, dur

    def _wait_links(self, seq, n_tot):
        down = [ln for ln in self._all_links() if not ln.up]
        if not down:
            return
        t0 = time.time()
        names = "、".join(ln.model for ln in down)
        self.log(f"⏸ 擷取暫停（第 {seq} 次前）：等 {names} 重連")
        self.emit("progress", {"n": self.done, "N": n_tot, "state": "paused", "msg": f"等 {names} 重連"})
        for ln in self._all_links():
            if not ln.wait_up(stop_evt=self.stop_evt):
                return
        gap = time.time() - t0
        self.gaps.append((seq, gap, names))
        self._gap_note = f"第 {seq} 次前斷線 {gap:.1f} s（{names}）"
        self.log(f"▶ 繼續擷取：{names} 已重連，空檔 {gap:.1f} s")

    # ---------------------------------------------------------------- 一次
    def _apply_trig(self, link):
        """把這次擷取快照的主觸發（預設 C8 RSTn）寫到主示波器；示範模式改 SimBus.trig"""
        t = self.cfg.get("trig") or {}
        src = str(t.get("src") or "C8").split()[0]
        level = t.get("level")
        slope = str(t.get("slope") or "NEG").upper()
        if level is None:
            level = 0.9
        dev = getattr(link, "dev", None)
        if dev is not None and getattr(dev, "sim", False) and getattr(dev, "bus", None):
            dev.bus.set_trig(src, float(level), slope)
            return
        from . import pmeas as PM
        from . import settings as ST
        ch = PM.ch_of(src) or 8
        for cmd in ST.trigger_cmds(ch, level, slope):
            link.w(cmd)

    def _nvme_after(self, seq):
        """選用：ARB 後 Linux PCIe rescan ＋ 列舉 NVMe。未啟用 → None（不連線、不 import paramiko）"""
        ncfg = self.cfg.get("nvme") or {}
        if not ncfg.get("on"):
            return None
        from . import nvmehost as NH
        try:
            h = NH.connect(ncfg.get("host") or "127.0.0.1", port=ncfg.get("port") or 22,
                           user=ncfg.get("user") or "root", password=ncfg.get("password") or "",
                           sim=bool(ncfg.get("sim", True)))
            rows, note = NH.rescan_and_list(h, wait_s=float(ncfg.get("wait_s") or 2))
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
            self.log(f"Linux NVMe rescan（第 {seq} 次）：{note}")
            for d in rows:
                self.log("  " + "　".join(f"{NH.COL_ZH[k]} {d.get(k) or '—'}" for k in NH.COLS))
            return rows
        except Exception as e:  # noqa: BLE001
            self.log(f"⚠ Linux NVMe rescan 失敗（第 {seq} 次）：{e}")
            return {"error": str(e)}

    def once(self, seq, n_tot=0):
        c = self.cfg
        ms, ss = self.links["master"], self.links["slave"]
        prog = lambda st, **kw: self.emit("progress", dict({"n": self.done, "N": n_tot, "seq": seq, "state": st}, **kw))  # noqa: E731
        self._apply_trig(ms)
        prog("arming")
        a_s = arm_single(ss, self.stop_evt)                        # 輔先 arm
        a_m = arm_single(ms, self.stop_evt)                        # 主再 arm
        if not a_s or not a_m:
            self.log(f"⚠ 第 {seq} 次 arm 未確認（輔 {a_s}、主 {a_m}）：TRIG_MODE? 沒讀到 SINGLE")
        settle = max(pretrig_s(ms), pretrig_s(ss))
        settle = settle + max(0.2, 0.05 * settle)
        if self.stop_evt.wait(settle):
            raise Stopped()
        arb_info, tmo = None, float(c.get("trig_timeout_s") or 0)
        if self._arb_on():
            prog("arb", msg="已 arm（輔 → 主）→ N6705 ARB 啟動")
            arb_info, dur = self._arb_go(seq)
            tmo = dur + 1.0
        prog("waiting", msg="已 arm（輔 → 主），等觸發" + (f"（ARB {self._arb_txt}）" if arb_info else ""))
        if not wait_trig(ms, tmo, self.stop_evt):
            raise TriggerTimeout(f"等主觸發逾時 {tmo:g} s" + ("（ARB 跑完主都沒觸發）" if arb_info else ""))
        t_trig = _dt.datetime.now()
        s_ok = wait_trig(ss, SLAVE_WAIT_S, self.stop_evt)
        if not s_ok:
            self.log(f"⚠ 第 {seq} 次：主已觸發但輔 {SLAVE_WAIT_S:g} s 內沒觸發（檢查 主 AUX OUT → 輔 EXT BNC、輔有沒有 arm）")
        prog("reading", last=t_trig.strftime("%H:%M:%S"))
        wait_idle(ms)
        if s_ok:
            wait_idle(ss)
        folder = os.path.join(self.run_dir, f"{seq:04d}_{t_trig:%H%M%S}")
        os.makedirs(folder, exist_ok=True)
        pre = f"{seq:04d}_"
        files, waves, raws, pngs, idn = [], {}, {}, {}, {}
        for role, ln in (("輔", ss), ("主", ms)):
            m = ln.model
            pngs[m] = ln.call(lambda d: d.screen_png())
            p = os.path.join(folder, f"{pre}{role}_{m}_實圖.png")
            with open(p, "wb") as f:
                f.write(pngs[m])
            files.append(p)
            idn[m] = getattr(ln.dev, "idn", "") if ln.dev is not None else ""
            if m == ss.model and not s_ok:
                continue
            for ch, nm in c["chans"][m]:
                raw = ln.call(lambda d, ch=ch: d.read_raw_wave(f"C{ch}"))
                t, y, meta = parse_wavedesc(raw)
                waves[(m, ch)] = (t, y)
                raws[(m, ch)] = (raw, meta, nm)
        # ---- 畫面：上下無縫實圖
        sp, mp = os.path.join(folder, f"{pre}輔_{ss.model}_實圖.png"), os.path.join(folder, f"{pre}主_{ms.model}_實圖.png")
        out = os.path.join(folder, f"{pre}畫面_上下.png")
        CP.save(out, "stack", sp, mp, right=c.get("trig_text", "") + f"　#{seq}  {t_trig:%H:%M:%S}",
                legend=c.get("legend", ""), slave_model=ss.model, master_model=ms.model)
        files.insert(0, out)
        fmt = c.get("fmt", {})
        # ---- 波形資料
        for (m, ch), (raw, meta, nm) in raws.items():
            role = "主" if m == ms.model else "輔"
            stem = os.path.join(folder, f"{pre}{role}_{m}_C{ch}_{nm}")
            if fmt.get("trc", True):
                with open(stem + ".trc", "wb") as f:
                    f.write(raw)
                files.append(stem + ".trc")
            if fmt.get("csv", True):
                t, y = waves[(m, ch)]
                np.savetxt(stem + ".csv", np.column_stack([t, y]), delimiter=",", fmt="%.9g",
                           header=f"time_s,{nm}_{meta.get('vunit', 'V')}", comments="")
                files.append(stem + ".csv")
        if fmt.get("npz", False):
            for m, role in ((ms.model, "主"), (ss.model, "輔")):
                d = {f"t_C{ch}": waves[(mm, ch)][0] for (mm, ch) in waves if mm == m}
                d.update({f"y_C{ch}": waves[(mm, ch)][1] for (mm, ch) in waves if mm == m})
                if d:
                    p = os.path.join(folder, f"{pre}{role}_{m}.npz")
                    np.savez_compressed(p, **d)
                    files.append(p)
        # ---- P 量測（示波器值 ＋ PC 值；列表：輔機在前，每台 P 編號不變）
        pm_rows = []
        for m, ln in ((ss.model, ss), (ms.model, ms)):
            slots = c.get("pm", {}).get(m) or []
            if not slots or (m == ss.model and not s_ok):
                continue
            im = PM.inst_max(m)
            inst = ln.call(lambda d, slots=slots, im=im: PM.read(d.q, slots, im))
            wv = {ch: waves[(mm, ch)] for (mm, ch) in waves if mm == m}
            for r in PM.measure(slots, wv, inst):
                pm_rows.append(dict(r, model=m))
        if fmt.get("pm", True) and pm_rows:
            p = os.path.join(folder, f"{pre}pmeas.csv")
            with open(p, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["示波器", "P", "量測", "在哪算", "示波器值", "PC 值", "單位", "差異 %", "比對"])
                for r in pm_rows:
                    w.writerow([r["model"], r["p"], r["label"], "示波器" if r["where"] == "scope" else "PC",
                                "" if r["scope"] is None else f"{r['scope']:.6g}", "" if r["pc"] is None else f"{r['pc']:.6g}",
                                r["unit"], "" if r["pct"] is None else f"{r['pct']:.2f}", r["ok"]])
            files.append(p)
        # ---- 上電時序
        sq_rows = SQ.analyze(waves, c.get("rails"), ref=c.get("ref", "VIN"), basis=c.get("basis", "hi"),
                             slave_skew_s=float(c.get("skew_s") or 0.0), direction=c.get("direction", "auto"))
        verdict = SQ.overall(sq_rows)
        dtx = SQ.DIR_TXT.get(SQ.direction_of(sq_rows), "上電")
        if c.get("seq_on", True) or self._arb_on():
            files.append(SQ.save_csv(sq_rows, os.path.join(folder, f"{pre}{dtx}時序.csv"), c.get("ref", "VIN"), c.get("basis", "hi")))
            files.append(SQ.save_png(sq_rows, waves, os.path.join(folder, f"{pre}{dtx}時序.png"), ref=c.get("ref", "VIN"),
                                     title=f"#{seq}  {t_trig:%Y-%m-%d %H:%M:%S}  {dtx}時序 ＋ slew rate（參考 {c.get('ref', 'VIN')}）"
                                           + (f"  → {verdict}" if verdict else "")))
        # ---- 突波／雜訊（PC 算；每條電源 rail 的過衝、欠衝、漣波 Vpp）
        nz_cfg = c.get("noise") or {}
        nz_rows = []
        if nz_cfg.get("on"):
            try:
                nz_rows = NZ.analyze(waves, c.get("rails"), nz_cfg)
                files.append(NZ.save_csv(nz_rows, os.path.join(folder, f"{pre}突波雜訊.csv")))
                if nz_cfg.get("save", True) and fmt.get("png", True):
                    files.append(NZ.save_png(nz_rows, waves, os.path.join(folder, f"{pre}突波雜訊.png"),
                                             title=f"#{seq}  {t_trig:%Y-%m-%d %H:%M:%S}  突波／雜訊（淡藍 = 穩態窗口、虛線 = Vss、"
                                                   f"▲ 過衝、▼ 欠衝、★ 最差）"))
            except Exception as e:  # noqa: BLE001
                self.log(f"⚠ 第 {seq} 次突波／雜訊量測失敗：{type(e).__name__}: {e}")
                nz_rows = []
        self._nz_last = nz_rows
        nvme_rows = self._nvme_after(seq) if self._arb_on() else None
        info = {"seq": seq, "time": t_trig.isoformat(timespec="milliseconds"), "slave_triggered": s_ok,
                "idn": idn, "trig": c.get("trig_text", ""), "gap_before": self._gap_note,
                "channels": {m: c["chans"][m] for m in c["chans"]}, "sequencing_verdict": verdict}
        if arb_info:
            info["arb"] = arb_info
        if nvme_rows is not None:
            info["nvme"] = nvme_rows if isinstance(nvme_rows, list) else nvme_rows
        self._nvme_last = nvme_rows
        p = os.path.join(folder, f"{pre}info.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        files.append(p)
        self._summary(seq, t_trig, s_ok, folder, sq_rows, verdict, pm_rows, len(files), waves)
        self._gap_note = ""
        self.log(f"✓ 第 {seq}{'' if n_tot <= 0 else f'/{n_tot}'} 次 {t_trig:%H:%M:%S}：存 {len(files)} 個檔 → {folder}"
                 + ("" if s_ok else "（輔未觸發）") + f"；{dtx}" + (f"時序 {verdict}" if verdict else ""))
        return {"seq": seq, "N": n_tot, "time": t_trig.strftime("%H:%M:%S"), "folder": folder, "files": files,
                "stack": out, "pngs": {ss.model: sp, ms.model: mp}, "waves": waves, "seq_rows": sq_rows,
                "verdict": verdict, "pm_rows": pm_rows, "noise_rows": nz_rows, "slave_ok": s_ok, "direction": SQ.direction_of(sq_rows),
                "nvme": getattr(self, "_nvme_last", None)}

    def _summary(self, seq, t_trig, s_ok, folder, sq_rows, verdict, pm_rows, nfiles, waves=None):
        path = os.path.join(self.run_dir, "summary.csv")
        srt = sort_rails(sq_rows)
        # 與 ⑦ 同一套：延遲（對參考 = Δt）、10↔90 % 時間、slew rate
        rail_cols = [f"{x['role']}{x['name']} 延遲 ms" for x in srt if x["kind"] != "current"] + \
                    [f"{x['role']}{x['name']} 10-90% ms" for x in srt] + \
                    [f"{x['role']}{x['name']} slew {'A' if x['kind'] == 'current' else 'V'}/ms" for x in srt]
        pm_cols = [f"{r['model']} {r['label']}" for r in pm_rows]
        nz_cols = (list(NZ.NOISE_CSV) + NZ.summary_col_names(getattr(self, "_nz_last", None))) \
            if (self.cfg.get("noise") or {}).get("on") else []
        if self._sum_cols is None:
            nvme_cols = ["NVMe 數", "NVMe 裝置", "NVMe 型號", "NVMe 序號", "NVMe 容量", "NVMe 韌體", "NVMe BDF", "NVMe Link"] \
                if (self.cfg.get("nvme") or {}).get("on") else []
            self._sum_cols = ["序號", "觸發時間", "方向", "輔觸發", "時序判定", "順序", "檔案數", "資料夾", "備註"] + \
                list(SPEC_CSV) + \
                (["ARB 通道", "ARB 範本", "ARB 掃描值", "ARB 參數"] if self._arb_on() else []) + nvme_cols + rail_cols + nz_cols + pm_cols
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(self._sum_cols)
            self._flush_pending()
        d = {"序號": seq, "觸發時間": t_trig.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "輔觸發": "Y" if s_ok else "N",
             "方向": SQ.DIR_TXT.get(SQ.direction_of(sq_rows), ""), "時序判定": verdict, "順序": SQ.sequence_text(sq_rows),
             "檔案數": nfiles, "資料夾": os.path.basename(folder), "備註": self._gap_note}
        for x in sq_rows:
            d[f"{x['role']}{x['name']} 延遲 ms"] = "" if x.get("delay") is None else f"{x['delay'] * 1e3:.4f}"
            d[f"{x['role']}{x['name']} 10-90% ms"] = "" if x.get("rise") is None else f"{x['rise'] * 1e3:.4f}"
            d[f"{x['role']}{x['name']} slew {'A' if x['kind'] == 'current' else 'V'}/ms"] = \
                "" if x.get("slew") is None else f"{x['slew']:.5g}"
        d.update(self._arb_cols())
        d.update(self._nvme_cols())
        d.update(self._spec_cols(waves, sq_rows, pm_rows))
        if (self.cfg.get("noise") or {}).get("on"):
            d.update(NZ.summary_cols(getattr(self, "_nz_last", None)))
        for r in pm_rows:
            v = r["scope"] if r["scope"] is not None else r["pc"]
            d[f"{r['model']} {r['label']}"] = "" if v is None else f"{v:.6g}"
        with open(path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([d.get(k, "") for k in self._sum_cols])

    def _spec_cols(self, waves, sq_rows, pm_rows):
        """⑨ 設計規格判比 → summary.csv 四欄（規格／電源設計表／規格總判定／規格明細）。"""
        fn = self.cfg.get("spec_compare")
        if not callable(fn):
            return {k: "" for k in SPEC_CSV}
        try:
            return fn(waves, sq_rows, pm_rows, noise_rows=getattr(self, "_nz_last", None) if waves is not None else None) \
                or {k: "" for k in SPEC_CSV}
        except Exception as e:  # noqa: BLE001
            return {"規格總判定": "N/A", "規格明細": f"判比失敗：{type(e).__name__}: {e}"}

    def _arb_cols(self):
        if not self._arb_on() or not getattr(self, "_arb_info", None):
            return {}
        chs = self._arb_info["channels"]
        par = "；".join(f"CH{x['ch']} " + " ".join(f"{k}={x[k]}" for k in x
                                                    if k not in ("template", "template_name", "sweep", "ch", "label", "shape",
                                                                 "duration_s", "repeat", "scpi")) for x in chs)
        chlab = "；".join(
            (f"CH{x['ch']}" + (f" {x['label']}" if x.get("label") else "")).strip() for x in chs)
        return {"ARB 通道": chlab,
                "ARB 範本": "；".join(x["template_name"] for x in chs),
                "ARB 掃描值": "；".join(f"CH{x['ch']} {x.get('sweep', '')}" for x in chs if x.get("sweep")), "ARB 參數": par}

    def _nvme_cols(self):
        from . import nvmehost as NH
        rows = getattr(self, "_nvme_last", None)
        if isinstance(rows, list):
            return NH.rows_for_csv(rows)
        if isinstance(rows, dict) and rows.get("error"):
            return {"NVMe 數": "", "NVMe 裝置": "", "NVMe 型號": "", "NVMe 序號": "", "NVMe 容量": "",
                    "NVMe 韌體": "", "NVMe BDF": "", "NVMe Link": "", "備註": (rows.get("error") or "")}
        return {}

    def _summary_miss(self, seq, msg):
        """ARB 跑完主沒觸發：summary 一列（沒有檔案）。欄位還沒定（第一次就沒觸發）→ 先暫存，等第一個有資料的一列一起寫"""
        d = {"序號": seq, "觸發時間": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "方向": "未觸發",
             "輔觸發": "N", "時序判定": "", "檔案數": 0, "備註": (msg + "；" + self._gap_note).strip("；")}
        d.update(self._arb_cols())
        d.update(self._spec_cols(None, [], []))
        if self._arb_on():
            self._nvme_last = self._nvme_after(seq)
            d.update(self._nvme_cols())
        self._gap_note = ""
        self._pending.append(d)
        if self._sum_cols is not None:
            self._flush_pending()

    def _flush_pending(self, final=False):
        if not self._pending:
            return
        path = os.path.join(self.run_dir, "summary.csv")
        if self._sum_cols is None:
            if not final:
                return
            nvme_cols = ["NVMe 數", "NVMe 裝置", "NVMe 型號", "NVMe 序號", "NVMe 容量", "NVMe 韌體", "NVMe BDF", "NVMe Link"] \
                if (self.cfg.get("nvme") or {}).get("on") else []
            nz_cols = list(NZ.NOISE_CSV) if (self.cfg.get("noise") or {}).get("on") else []
            self._sum_cols = ["序號", "觸發時間", "方向", "輔觸發", "時序判定", "順序", "檔案數", "資料夾", "備註"] + \
                list(SPEC_CSV) + ["ARB 通道", "ARB 範本", "ARB 掃描值", "ARB 參數"] + nvme_cols + nz_cols
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(self._sum_cols)
        with open(path, "a", newline="", encoding="utf-8") as f:
            for d in self._pending:
                csv.writer(f).writerow([d.get(k, "") for k in self._sum_cols])
        self._pending = []
