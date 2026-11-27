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
from . import sequencing as SQ
from .lecroy import parse_wavedesc
from .link import Disconnected
from .rails import SLAVE_DEF

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

    def __init__(self, links, cfg, log=print, emit=None):
        self.links = links                     # {"master": Link, "slave": Link}
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

    def _wait_links(self, seq, n_tot):
        down = [ln for ln in self.links.values() if not ln.up]
        if not down:
            return
        t0 = time.time()
        names = "、".join(ln.model for ln in down)
        self.log(f"⏸ 擷取暫停（第 {seq} 次前）：等 {names} 重連")
        self.emit("progress", {"n": self.done, "N": n_tot, "state": "paused", "msg": f"等 {names} 重連"})
        for ln in self.links.values():
            if not ln.wait_up(stop_evt=self.stop_evt):
                return
        gap = time.time() - t0
        self.gaps.append((seq, gap, names))
        self._gap_note = f"第 {seq} 次前斷線 {gap:.1f} s（{names}）"
        self.log(f"▶ 繼續擷取：{names} 已重連，空檔 {gap:.1f} s")

    # ---------------------------------------------------------------- 一次
    def once(self, seq, n_tot=0):
        c = self.cfg
        ms, ss = self.links["master"], self.links["slave"]
        prog = lambda st, **kw: self.emit("progress", dict({"n": self.done, "N": n_tot, "seq": seq, "state": st}, **kw))  # noqa: E731
        prog("arming")
        a_s = arm_single(ss, self.stop_evt)                        # 輔先 arm
        a_m = arm_single(ms, self.stop_evt)                        # 主再 arm
        if not a_s or not a_m:
            self.log(f"⚠ 第 {seq} 次 arm 未確認（輔 {a_s}、主 {a_m}）：TRIG_MODE? 沒讀到 SINGLE")
        settle = max(pretrig_s(ms), pretrig_s(ss))
        settle = settle + max(0.2, 0.05 * settle)
        if self.stop_evt.wait(settle):
            raise Stopped()
        prog("waiting", msg="已 arm（輔 → 主），等觸發")
        if not wait_trig(ms, float(c.get("trig_timeout_s") or 0), self.stop_evt):
            raise TriggerTimeout(f"等主觸發逾時 {c.get('trig_timeout_s')} s")
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
        # ---- P 量測（示波器值 ＋ PC 值）
        pm_rows = []
        for m, ln in ((ms.model, ms), (ss.model, ss)):
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
        if c.get("seq_on", True):
            files.append(SQ.save_csv(sq_rows, os.path.join(folder, f"{pre}{dtx}時序.csv"), c.get("ref", "VIN"), c.get("basis", "hi")))
            files.append(SQ.save_png(sq_rows, waves, os.path.join(folder, f"{pre}{dtx}時序.png"), ref=c.get("ref", "VIN"),
                                     title=f"#{seq}  {t_trig:%Y-%m-%d %H:%M:%S}  {dtx}時序 ＋ slew rate（參考 {c.get('ref', 'VIN')}）"
                                           + (f"  → {verdict}" if verdict else "")))
        info = {"seq": seq, "time": t_trig.isoformat(timespec="milliseconds"), "slave_triggered": s_ok,
                "idn": idn, "trig": c.get("trig_text", ""), "gap_before": self._gap_note,
                "channels": {m: c["chans"][m] for m in c["chans"]}, "sequencing_verdict": verdict}
        p = os.path.join(folder, f"{pre}info.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        files.append(p)
        self._summary(seq, t_trig, s_ok, folder, sq_rows, verdict, pm_rows, len(files))
        self._gap_note = ""
        self.log(f"✓ 第 {seq}{'' if n_tot <= 0 else f'/{n_tot}'} 次 {t_trig:%H:%M:%S}：存 {len(files)} 個檔 → {folder}"
                 + ("" if s_ok else "（輔未觸發）") + f"；{dtx}" + (f"時序 {verdict}" if verdict else ""))
        return {"seq": seq, "N": n_tot, "time": t_trig.strftime("%H:%M:%S"), "folder": folder, "files": files,
                "stack": out, "pngs": {ss.model: sp, ms.model: mp}, "waves": waves, "seq_rows": sq_rows,
                "verdict": verdict, "pm_rows": pm_rows, "slave_ok": s_ok, "direction": SQ.direction_of(sq_rows)}

    def _summary(self, seq, t_trig, s_ok, folder, sq_rows, verdict, pm_rows, nfiles):
        path = os.path.join(self.run_dir, "summary.csv")
        srt = sorted(sq_rows, key=lambda x: (x["model"] != SLAVE_DEF, x["ch"]))
        rail_cols = [f"{x['role']}{x['name']} 延遲 ms" for x in srt if x["kind"] != "current"] + \
                    [f"{x['role']}{x['name']} slew {'A' if x['kind'] == 'current' else 'V'}/ms" for x in srt]
        pm_cols = [f"{r['model']} {r['label']}" for r in pm_rows]
        if self._sum_cols is None:
            self._sum_cols = ["序號", "觸發時間", "方向", "輔觸發", "時序判定", "順序", "檔案數", "資料夾", "備註"] + rail_cols + pm_cols
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(self._sum_cols)
        d = {"序號": seq, "觸發時間": t_trig.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "輔觸發": "Y" if s_ok else "N",
             "方向": SQ.DIR_TXT.get(SQ.direction_of(sq_rows), ""), "時序判定": verdict, "順序": " → ".join(x["name"] for x in sq_rows if x.get("order")),
             "檔案數": nfiles, "資料夾": os.path.basename(folder), "備註": self._gap_note}
        for x in sq_rows:
            d[f"{x['role']}{x['name']} 延遲 ms"] = "" if x.get("delay") is None else f"{x['delay'] * 1e3:.4f}"
            d[f"{x['role']}{x['name']} slew {'A' if x['kind'] == 'current' else 'V'}/ms"] = \
                "" if x.get("slew") is None else f"{x['slew']:.5g}"
        for r in pm_rows:
            v = r["scope"] if r["scope"] is not None else r["pc"]
            d[f"{r['model']} {r['label']}"] = "" if v is None else f"{v:.6g}"
        with open(path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([d.get(k, "") for k in self._sum_cols])
