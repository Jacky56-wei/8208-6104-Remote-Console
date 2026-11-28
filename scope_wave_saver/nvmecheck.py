# -*- coding: utf-8 -*-
"""上電後自動 rescan 認盤＋判定（純邏輯，可單元測試；SSH 由 nvmehost 提供）。

流程（每次上電事件）：等 settle → PCIe rescan → 輪詢 /dev/nvme*（逾時；掉盤時再 rescan，最多重試 N 次）
→ 收 型號／序號／韌體／容量／PCIe 速度與寬度（＋選用 SMART critical_warning）→ 判定：
  OK            認到碟，連結正常
  掉盤          沒認到（或比基準少顆）
  降速 / 降寬 / 降速降寬   PCIe link 低於預期值（預期值有填用預期值，否則用第一次 OK 的基準）
  SMART 警告    critical_warning ≠ 0
  連線失敗      Linux SSH 連不上／指令失敗（不算掉盤）
"""
from __future__ import annotations

import datetime as _dt
import os
import random
import re
import threading
import time

from . import nvmehost as NH

OK = "OK"
DROP = "掉盤"
DOWN_SPEED = "降速"
DOWN_WIDTH = "降寬"
DOWN_BOTH = "降速降寬"
SMART_WARN = "SMART 警告"
ERR = "連線失敗"
RESULTS = (OK, DROP, DOWN_SPEED, DOWN_WIDTH, DOWN_BOTH, SMART_WARN, ERR)

GEN_GT = {1: 2.5, 2: 5.0, 3: 8.0, 4: 16.0, 5: 32.0}

DEFAULTS = {"auto": False, "settle_s": 3.0, "timeout_s": 15.0, "retries": 2, "poll_s": 1.0,
            "expect": "", "stop_on_drop": False, "remove_first": False}


# ----------------------------------------------------------------- link 解析
def parse_link(s):
    """'16GT/s x4'／'16.0 GT/s PCIe x4'／'Gen4 x4' → (GT/s, 寬度)；認不得 → (None, None)。"""
    t = str(s or "").strip()
    if not t:
        return None, None
    w = re.search(r"x\s*(\d+)", t, re.I)
    width = int(w.group(1)) if w else None
    g = re.search(r"gen\s*(\d)", t, re.I)
    if g and int(g.group(1)) in GEN_GT:
        return GEN_GT[int(g.group(1))], width
    m = re.search(r"([\d.]+)\s*GT/?s", t, re.I)
    return (float(m.group(1)) if m else None), width


def link_text(gt, width):
    if gt is None and width is None:
        return ""
    gen = next((g for g, v in GEN_GT.items() if gt is not None and abs(v - gt) < 0.4), None)
    sp = f"Gen{gen}" if gen else (f"{gt:g}GT/s" if gt is not None else "")
    return " ".join(x for x in (sp, f"x{width}" if width else "") if x)


def drive_key(d):
    return str(d.get("serial") or d.get("bdf") or d.get("dev") or "").strip()


class Baseline:
    """第一次「認盤 OK」的狀態：有哪些碟（序號）與各自的 link。之後用它比對。"""

    def __init__(self):
        self.keys = []          # 順序保留
        self.link = {}          # key → (gt, width)

    @property
    def ready(self):
        return bool(self.keys)

    def learn(self, drives):
        self.keys = [drive_key(d) for d in drives]
        self.link = {drive_key(d): parse_link(d.get("link")) for d in drives}

    def link_for(self, key):
        if key in self.link:
            return self.link[key]
        return next(iter(self.link.values()), (None, None))


def _smart_cw(d):
    v = d.get("smart_cw")
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def judge(drives, baseline=None, expected="", update_baseline=True):
    """drives（list of dict，含 link / smart_cw）→ 結果 dict。

    expected：'Gen4 x4' 這類預期值（空白 = 用基準）。判定優先序：掉盤 > 降速降寬 > 降速／降寬 > SMART 警告 > OK。
    OK 且基準尚未建立 → 記為基準（update_baseline）。
    """
    baseline = baseline if baseline is not None else Baseline()
    drives = [d for d in (drives or []) if isinstance(d, dict)]
    exp_gt, exp_w = parse_link(expected)
    res = {"result": OK, "ok": True, "found": len(drives), "drives": drives, "detail": "",
           "speed": "", "width": "", "link": "", "smart": "", "missing": [], "baseline": ""}
    if drives:
        gt, wd = parse_link(drives[0].get("link"))
        res["speed"] = link_text(gt, None)
        res["width"] = f"x{wd}" if wd else ""
        res["link"] = " | ".join(link_text(*parse_link(d.get("link"))) or "—" for d in drives)
        cws = [_smart_cw(d) for d in drives]
        res["smart"] = " | ".join("" if c is None else f"0x{c:02X}" for c in cws).strip(" |")
    if baseline.ready:
        b_gt, b_w = baseline.link_for(baseline.keys[0])
        res["baseline"] = link_text(b_gt, b_w)

    issues = []
    # 1) 掉盤
    if not drives:
        issues.append(DROP)
        res["missing"] = list(baseline.keys)
        res["detail"] = "rescan 後沒有任何 /dev/nvme*（未認到碟）"
    elif baseline.ready:
        got = {drive_key(d) for d in drives}
        miss = [k for k in baseline.keys if k not in got]
        if miss:
            issues.append(DROP)
            res["missing"] = miss
            res["detail"] = f"比基準少 {len(miss)} 顆：{'、'.join(miss)}"
    # 2) link 降速／降寬（沒掉盤的碟才比）
    if drives and DROP not in issues:
        slow = narrow = False
        notes = []
        for d in drives:
            gt, wd = parse_link(d.get("link"))
            if exp_gt is not None or exp_w is not None:
                want_gt, want_w = exp_gt, exp_w
            elif baseline.ready:
                want_gt, want_w = baseline.link_for(drive_key(d))
            else:
                want_gt, want_w = None, None
            if want_gt is not None and gt is not None and gt < want_gt - 0.4:
                slow = True
                notes.append(f"{link_text(gt, None)} < 預期 {link_text(want_gt, None)}")
            if want_w is not None and wd is not None and wd < want_w:
                narrow = True
                notes.append(f"x{wd} < 預期 x{want_w}")
        if slow and narrow:
            issues.append(DOWN_BOTH)
        elif slow:
            issues.append(DOWN_SPEED)
        elif narrow:
            issues.append(DOWN_WIDTH)
        if notes:
            res["detail"] = "；".join(notes)
    # 3) SMART
    bad_cw = [d for d in drives if (_smart_cw(d) or 0) != 0]
    if bad_cw and DROP not in issues:
        issues.append(SMART_WARN)
        res["detail"] = (res["detail"] + "；" if res["detail"] else "") + \
            "SMART critical_warning " + "、".join(f"{d.get('dev') or '?'}=0x{_smart_cw(d):02X}" for d in bad_cw)
    if issues:
        res["result"] = issues[0]
        res["ok"] = False
    if res["ok"] and update_baseline and not baseline.ready and drives:
        baseline.learn(drives)
        res["baseline"] = link_text(*baseline.link_for(drive_key(drives[0])))
    return res


def error_result(msg):
    return {"result": ERR, "ok": False, "found": 0, "drives": [], "detail": str(msg), "speed": "", "width": "",
            "link": "", "smart": "", "missing": [], "baseline": ""}


# ----------------------------------------------------------------- 設定
def norm_cfg(c):
    """把 GUI 傳進來的 nvme 設定補上預設值、轉型。"""
    o = dict(DEFAULTS)
    o.update({k: v for k, v in (c or {}).items() if k in DEFAULTS and v is not None})
    for k, d in (("settle_s", 3.0), ("timeout_s", 15.0), ("poll_s", 1.0)):
        try:
            o[k] = max(float(o[k]), 0.0)
        except (TypeError, ValueError):
            o[k] = d
    try:
        o["retries"] = max(int(float(o["retries"])), 0)
    except (TypeError, ValueError):
        o["retries"] = 2
    o["poll_s"] = max(o["poll_s"], 0.05)
    return o


# ----------------------------------------------------------------- 單次檢查
def run_check(host, cfg, baseline=None, delay_s=0.0, sleep=time.sleep, clock=time.monotonic,
              stop_evt=None, log=None):
    """等 delay_s（settle）→ rescan → 輪詢 → 判定；掉盤時重 rescan（最多 retries 次）。→ 結果 dict（含 attempts）。"""
    cfg = norm_cfg(cfg)
    baseline = baseline if baseline is not None else Baseline()
    log = log or (lambda *_: None)

    def stopped():
        return stop_evt is not None and stop_evt.is_set()

    def nap(s):
        t_end = clock() + s
        while clock() < t_end and not stopped():
            sleep(min(0.25, max(t_end - clock(), 0.0)))

    nap(max(float(delay_s or 0), 0.0))
    res, attempts = None, 0
    for attempt in range(1 + cfg["retries"]):
        if stopped():
            return None
        attempts = attempt + 1
        try:
            bdfs = None
            if cfg.get("remove_first"):
                bdfs = [d.get("bdf") for d in NH.list_drives(host) if d.get("bdf")]
            NH.rescan(host, wait_s=0.5, remove_bdfs=bdfs)
            need = max(len(baseline.keys), 1)
            t_end = clock() + cfg["timeout_s"]
            drives = []
            while True:
                drives = NH.list_drives(host)
                if len(drives) >= need or clock() >= t_end or stopped():
                    break
                sleep(cfg["poll_s"])
            if drives:
                NH.attach_smart(host, drives)
        except Exception as e:  # noqa: BLE001
            res = error_result(e)
            res["attempts"] = attempts
            log(f"認盤失敗（第 {attempts} 次嘗試）：{e}")
            return res
        res = judge(drives, baseline, cfg.get("expect", ""))
        res["attempts"] = attempts
        if res["result"] != DROP:
            break
        log(f"第 {attempts} 次 rescan 沒認到碟，{'重試' if attempt < cfg['retries'] else '放棄'}")
    return res


# ----------------------------------------------------------------- 示範用情境
SIM_SCENARIOS = ("ok", "drop", "late", "down_speed", "down_width", "smart")
_SIM_WEIGHTS = (("drop", 0.4), ("late", 0.15), ("down_speed", 0.15), ("down_width", 0.15), ("smart", 0.15))


def pick_sim_scenario(fail_rate, rng=None):
    """示範：機率 fail_rate 出狀況（掉盤／晚到／降速／降寬／SMART），否則 ok。"""
    rng = rng or random
    try:
        p = min(max(float(fail_rate or 0), 0.0), 1.0)
    except (TypeError, ValueError):
        p = 0.0
    if rng.random() >= p:
        return "ok"
    x, acc = rng.random(), 0.0
    for name, w in _SIM_WEIGHTS:
        acc += w
        if x < acc:
            return name
    return "drop"


# ----------------------------------------------------------------- 背景執行
class AutoRescan:
    """每次上電事件開一條背景執行緒做 run_check，不卡畫面也不卡 GUI；結果用 wait() 取。
    基準（第一次 OK）在整輪擷取中共用。"""

    def __init__(self, cfg, run_dir_fn=None, log=print, stop_evt=None, sleep=time.sleep):
        self.cfg = dict(cfg or {})
        self.n = norm_cfg(self.cfg)
        self.run_dir_fn = run_dir_fn or (lambda: None)
        self.log = log
        self.stop_evt = stop_evt
        self.sleep = sleep
        self.baseline = Baseline()
        self.count = 0
        self._job = None            # (seq, thread, box)
        self._rng = random.Random(self.cfg.get("sim_seed"))
        self._seq_list = [s for s in str(self.cfg.get("sim_seq") or "").replace("，", ",").split(",") if s.strip()]

    @property
    def enabled(self):
        return bool(self.cfg.get("on") and self.n.get("auto"))

    def _scenario(self):
        if self._seq_list:
            return self._seq_list[(self.count - 1) % len(self._seq_list)].strip()
        return pick_sim_scenario(self.cfg.get("fail_rate"), self._rng)

    def _work(self, seq, phase, delay_s, scenario, box):
        sim = bool(self.cfg.get("sim", True))
        h = None
        try:
            h = NH.connect(self.cfg.get("host") or "127.0.0.1", port=self.cfg.get("port") or 22,
                           user=self.cfg.get("user") or "root", password=self.cfg.get("password") or "",
                           sim=sim, sim_scenario=scenario if sim else None)
            slp = (lambda s: time.sleep(min(s, 0.15))) if sim else self.sleep
            n = dict(self.n, timeout_s=min(self.n["timeout_s"], 1.5)) if sim else self.n   # 示範：不要真的等 15 s
            res = run_check(h, n, self.baseline, delay_s=delay_s if not sim else min(delay_s, 0.5),
                            sleep=slp, stop_evt=self.stop_evt, log=self.log)
        except Exception as e:  # noqa: BLE001
            res = error_result(e)
            res["attempts"] = 1
        finally:
            try:
                if h is not None:
                    h.close()
            except Exception:  # noqa: BLE001
                pass
        if res is not None:
            res["seq"], res["phase"] = seq, phase
            res["time"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if sim and scenario and scenario != "ok":
                res["detail"] = (res.get("detail") or "") + f"（模擬情境：{scenario}）"
        box["res"] = res

    def kick(self, seq, phase, delay_s=0.0):
        """開始背景檢查。delay_s = 距離現在還要等多久才開始 rescan（含 settle）。"""
        if not self.enabled:
            return False
        self.count += 1
        box = {}
        th = threading.Thread(target=self._work, args=(seq, phase, max(delay_s, 0.0), self._scenario(), box),
                              name=f"nvme-auto-{seq}", daemon=True)
        self._job = (seq, th, box)
        th.start()
        return True

    def pending(self, seq=None):
        return self._job is not None and (seq is None or self._job[0] == seq)

    def wait(self, seq, timeout=None):
        """等 seq 的檢查完成 → 結果 dict；被停止／逾時 → None。同時寫 nvme_check.log。"""
        if self._job is None or self._job[0] != seq:
            return None
        _s, th, box = self._job
        if timeout is None:
            timeout = self.n["settle_s"] + (self.n["timeout_s"] + 5.0) * (1 + self.n["retries"]) + 30.0
        t_end = time.monotonic() + timeout
        while th.is_alive() and time.monotonic() < t_end:
            th.join(0.1)
        self._job = None
        res = box.get("res")
        if res is not None:
            self.write_log(res)
        return res

    def write_log(self, res):
        d = self.run_dir_fn()
        if not d:
            return
        try:
            with open(os.path.join(d, "nvme_check.log"), "a", encoding="utf-8") as f:
                f.write(f"{res.get('time', '')}\t#{res.get('seq', '')}\t{res.get('phase', '')}\t{res['result']}\t"
                        f"找到 {res.get('found', 0)} 顆\tlink {res.get('link') or '—'}"
                        f"（基準 {res.get('baseline') or '—'}）\tSMART {res.get('smart') or '—'}\t"
                        f"嘗試 {res.get('attempts', 1)} 次\t{res.get('detail', '')}\n")
                for x in res.get("drives") or []:
                    f.write("\t" + "　".join(f"{NH.COL_ZH[k]} {x.get(k) or '—'}" for k in NH.COLS) + "\n")
        except OSError:
            pass


def csv_cols(res):
    """summary.csv 欄位（res = run_check 結果；None → 空）。"""
    if not res:
        return {k: "" for k in CSV_COLS}
    return {"NVMe 判定": res["result"], "NVMe 速度": res.get("speed", ""), "NVMe 寬度": res.get("width", ""),
            "NVMe SMART": res.get("smart", ""), "NVMe 嘗試": res.get("attempts", ""),
            "NVMe 說明": res.get("detail", ""), "NVMe 觸發": res.get("phase", "")}


CSV_COLS = ("NVMe 判定", "NVMe 速度", "NVMe 寬度", "NVMe SMART", "NVMe 嘗試", "NVMe 說明", "NVMe 觸發")
