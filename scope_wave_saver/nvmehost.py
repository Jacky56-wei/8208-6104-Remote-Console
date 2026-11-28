# -*- coding: utf-8 -*-
"""Linux 主機 NVMe rescan ＋ 認碟（選用，預設關閉）。

啟用後：PCIe rescan（echo 1 > /sys/bus/pci/rescan）→ 列出每顆 NVMe：
  裝置節點 /dev/nvmeXn1、型號、序號、容量、韌體、PCIe BDF、連結速度 / 寬度。
資料來源：優先 `nvme list -o json`；沒有 nvme-cli → /sys/class/nvme/*/{model,serial,firmware_rev} ＋ lsblk；
BDF / link 從 /sys/class/nvme/*/device 與 `lspci -vv`（或 /sys 的 current_link_*）。
paramiko 只在「已啟用且要連實機」時才 import。示範模式不連 SSH，回傳模擬資料。
"""
from __future__ import annotations

import json
import re
import time

RESCAN_CMD = "echo 1 | sudo -n tee /sys/bus/pci/rescan >/dev/null"
LIST_JSON_CMD = "nvme list -o json 2>/dev/null || sudo -n nvme list -o json 2>/dev/null"
SYSFS_CMD = r"""
python3 - << 'PY'
import os, json, subprocess
def rd(p):
    try:
        with open(p) as f:
            return f.read().strip()
    except Exception:
        return ""
out = []
base = "/sys/class/nvme"
if os.path.isdir(base):
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            continue
        dev = d
        # BDF: .../devices/pci.../0000:01:00.0/nvme/nvme0
        bdf = ""
        try:
            real = os.path.realpath(os.path.join(d, "device"))
            for p in real.split(os.sep):
                if re_bdf.match(p):
                    bdf = p
        except Exception:
            pass
        ns, cap = "", ""
        for n in sorted(os.listdir("/sys/class/block")) if os.path.isdir("/sys/class/block") else []:
            if n.startswith(name + "n"):
                ns = "/dev/" + n
                sz = rd(f"/sys/class/block/{n}/size")
                try:
                    cap = str(int(sz) * 512) if sz else ""
                except ValueError:
                    cap = ""
                break
        speed = rd(os.path.join(d, "device", "current_link_speed"))
        width = rd(os.path.join(d, "device", "current_link_width"))
        link = " ".join(x for x in (speed, f"x{width}" if width and not width.startswith("x") else width) if x)
        out.append({"ctrl": name, "dev": ns or f"/dev/{name}n1", "model": rd(os.path.join(d, "model")),
                    "serial": rd(os.path.join(d, "serial")), "fw": rd(os.path.join(d, "firmware_rev")),
                    "capacity_b": cap, "bdf": bdf, "link": link, "src": "sysfs"})
print(json.dumps(out, ensure_ascii=False))
PY
""".replace("re_bdf", "__import__('re').compile(r'^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\\.[0-9a-fA-F]$')")

LSBLK_CMD = "lsblk -b -dn -o NAME,SIZE,TYPE 2>/dev/null | awk '$1 ~ /^nvme/ {print $1, $2, $3}'"
LSPCI_CMD = "lspci -D -vv 2>/dev/null | awk 'BEGIN{RS=\"\\n\\\\n\"} /Non-Volatile memory|NVMe|Non-Volatile Memory/ {print}'"

SIM_DRIVES = [
    {"dev": "/dev/nvme0n1", "model": "DEMO SSD 3840GB", "serial": "SIM-NVME-0001",
     "capacity": "3,840,755,982,336 bytes（3.84 TB）", "capacity_b": 3_840_755_982_336,
     "fw": "1.00", "bdf": "0000:01:00.0", "link": "32GT/s x4", "src": "模擬"},
]


def fmt_bytes(b):
    try:
        b = int(b)
    except (TypeError, ValueError):
        return ""
    if b <= 0:
        return ""
    if b >= 1e12:
        return f"{b:,} bytes（{b / 1e12:.2f} TB）"
    return f"{b:,} bytes（{b / 1e9:.2f} GB）"


def parse_nvme_list(text):
    """nvme list -o json（1.x Devices[] 或 2.x 巢狀）→ list of drive dicts（全部裝置）"""
    try:
        j = json.loads(text)
    except Exception:  # noqa: BLE001
        return []
    found = []

    def walk(o, ctx):
        if isinstance(o, dict):
            c = dict(ctx)
            for k_src, k in (("ModelNumber", "model"), ("SerialNumber", "serial"), ("Firmware", "fw"),
                             ("FirmwareRevision", "fw")):
                if o.get(k_src):
                    c[k] = str(o[k_src]).strip()
            path = o.get("DevicePath") or o.get("NameSpace") or o.get("Namespace")
            if isinstance(path, str) and path.strip():
                c["dev"] = path.strip() if path.startswith("/dev/") else f"/dev/{path.strip().lstrip('/')}"
            if "PhysicalSize" in o:
                try:
                    c["capacity_b"] = int(o["PhysicalSize"])
                except (TypeError, ValueError):
                    pass
            if c.get("dev") or c.get("model") or c.get("serial"):
                if "PhysicalSize" in o or c.get("dev"):
                    found.append(dict(c))
            for v in o.values():
                if isinstance(v, (dict, list)):
                    walk(v, c)
        elif isinstance(o, list):
            for v in o:
                walk(v, ctx)

    walk(j, {})
    # 去重：同一個 DevicePath 只留一筆（較完整的）
    by = {}
    for d in found:
        k = d.get("dev") or d.get("serial") or d.get("model") or str(len(by))
        old = by.get(k)
        if old is None or len(d) >= len(old):
            d.setdefault("src", "nvme list -o json")
            if d.get("capacity_b") and not d.get("capacity"):
                d["capacity"] = fmt_bytes(d["capacity_b"])
            by[k] = d
    return list(by.values())


def parse_sysfs_json(text):
    try:
        rows = json.loads(text)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows or []:
        d = dict(r)
        if d.get("capacity_b") and not d.get("capacity"):
            d["capacity"] = fmt_bytes(d["capacity_b"])
        d.setdefault("src", "sysfs")
        out.append(d)
    return out


def parse_lspci(text):
    """lspci -D -vv 片段 → {bdf: 'Speed x Width'}"""
    links = {}
    cur = None
    for line in str(text or "").splitlines():
        m = re.match(r"^([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])\s", line)
        if m:
            cur = m.group(1)
            continue
        if cur and "LnkSta:" in line:
            sp = re.search(r"Speed\s+([^,]+)", line)
            wd = re.search(r"Width\s+(x\d+)", line)
            parts = []
            if sp:
                parts.append(sp.group(1).strip())
            if wd:
                parts.append(wd.group(1).strip())
            if parts:
                links[cur] = " ".join(parts)
    return links


class NvmeError(RuntimeError):
    pass


class SimHost:
    """示範：不連 SSH，rescan 後回傳模擬 NVMe；探詢回傳模擬主機名稱／核心。

    scenario（自動 rescan 示範用）：ok / drop（一直沒認到）/ late（前 2 次列舉沒有，之後出現）/
    down_speed（Gen5→Gen3）/ down_width（x4→x2）/ smart（critical_warning=0x04）。
    """

    def __init__(self, scenario="ok"):
        self.scenario = scenario or "ok"
        self.polls = 0

    def close(self):
        pass

    def run(self, cmd, timeout=30):
        c = cmd or ""
        if "uname -r" in c and "hostname" in c:
            return 0, "demo-linux\n6.8.0-demo\nroot\n", ""
        return 0, "", ""

    def sim_drives(self):
        self.polls += 1
        sc = self.scenario
        if sc == "drop" or (sc == "late" and self.polls <= 2):
            return []
        rows = [dict(x) for x in SIM_DRIVES]
        if sc == "down_speed":
            rows[0]["link"] = "8GT/s x4"
        elif sc == "down_width":
            rows[0]["link"] = "32GT/s x2"
        elif sc == "smart":
            rows[0]["smart_cw"] = 4
        else:
            rows[0]["smart_cw"] = 0
        return rows


def connect(host, port=22, user="root", password="", key_filename="", timeout=8, sim=False, sim_scenario=None):
    """連 Linux 主機。sim=True → 不 import paramiko。只在實機連線時才 import paramiko。"""
    if sim:
        return SimHost(sim_scenario or "ok")
    try:
        import paramiko  # noqa: WPS433  延遲載入：未啟用 / 示範模式不會 import
    except ImportError as e:
        raise NvmeError("沒有 paramiko（pip install paramiko）— 無法 SSH 到 Linux 主機") from e
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw = dict(hostname=host, port=int(port or 22), username=user or "root", timeout=float(timeout),
              allow_agent=False, look_for_keys=not bool(password))
    if password:
        kw["password"] = password
    if key_filename:
        kw["key_filename"] = key_filename
    try:
        cli.connect(**kw)
    except Exception as e:  # noqa: BLE001
        raise NvmeError(f"SSH 連不上 {user}@{host}:{port}：{e}") from e

    class _Ssh:
        def close(self):
            try:
                cli.close()
            except Exception:  # noqa: BLE001
                pass

        def run(self, cmd, timeout=30):
            _in, out, err = cli.exec_command(cmd, timeout=timeout)
            return out.channel.recv_exit_status(), out.read().decode(errors="replace"), err.read().decode(errors="replace")

    return _Ssh()


_BDF_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]$")


def rescan(host, wait_s=2.0, remove_bdfs=None):
    """PCIe rescan，然後稍等列舉。remove_bdfs：rescan 前先把這些 BDF 從 PCIe 樹移除
    （echo 1 > /sys/bus/pci/devices/<BDF>/remove），才抓得到「重新上電後沒回來」的碟。選用，預設不做。"""
    if not isinstance(host, SimHost):
        for bdf in remove_bdfs or []:
            if _BDF_RE.match(str(bdf or "")):
                host.run(f"echo 1 | sudo -n tee /sys/bus/pci/devices/{bdf}/remove >/dev/null", timeout=20)
    rc, out, err = host.run(RESCAN_CMD, timeout=20)
    if not isinstance(host, SimHost) and rc not in (0, None):
        raise NvmeError(f"PCIe rescan 失敗 rc={rc}：{(err or out).strip()[:200]}")
    if not isinstance(host, SimHost):
        time.sleep(max(float(wait_s or 0), 0.0))
    return rc, (out or "").strip(), (err or "").strip()


def list_drives(host):
    """→ list[dict]；示範主機回傳 SIM_DRIVES。"""
    if isinstance(host, SimHost):
        return host.sim_drives()
    rows = []
    rc, out, err = host.run(LIST_JSON_CMD, timeout=20)
    if rc == 0 and out.strip():
        rows = parse_nvme_list(out)
    if not rows:
        rc2, out2, _e2 = host.run(SYSFS_CMD, timeout=20)
        if out2.strip():
            rows = parse_sysfs_json(out2)
        if not rows:
            rc3, out3, _e3 = host.run(LSBLK_CMD, timeout=15)
            for line in (out3 or "").splitlines():
                p = line.split()
                if len(p) >= 2 and p[0].startswith("nvme"):
                    rows.append({"dev": f"/dev/{p[0]}", "model": "", "serial": "", "fw": "",
                                 "capacity_b": int(p[1]) if p[1].isdigit() else "",
                                 "capacity": fmt_bytes(p[1]) if p[1].isdigit() else "",
                                 "bdf": "", "link": "", "src": "lsblk"})
    # 補 BDF / link
    need_pci = any(not (r.get("bdf") and r.get("link")) for r in rows)
    links = {}
    if need_pci:
        _rc, lout, _e = host.run(LSPCI_CMD, timeout=20)
        links = parse_lspci(lout)
        # 若 sysfs 沒給 BDF，用 lspci 第一張 NVMe
        only = list(links.keys())
        for r in rows:
            if not r.get("bdf") and len(only) == 1:
                r["bdf"] = only[0]
            if r.get("bdf") and not r.get("link") and r["bdf"] in links:
                r["link"] = links[r["bdf"]]
    for r in rows:
        if r.get("capacity_b") and not r.get("capacity"):
            r["capacity"] = fmt_bytes(r["capacity_b"])
        r.setdefault("model", "")
        r.setdefault("serial", "")
        r.setdefault("fw", "")
        r.setdefault("bdf", "")
        r.setdefault("link", "")
        r.setdefault("dev", "")
    return rows


SMART_CMD = "nvme smart-log {dev} -o json 2>/dev/null || sudo -n nvme smart-log {dev} -o json 2>/dev/null"


def parse_smart_cw(text):
    """nvme smart-log（json 或文字）→ critical_warning（int）；讀不到 → None。"""
    t = str(text or "")
    try:
        j = json.loads(t)
        if isinstance(j, dict) and "critical_warning" in j:
            return int(j["critical_warning"])
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"critical_warning\s*:?\s*(0x[0-9a-fA-F]+|\d+)", t)
    return int(m.group(1), 0) if m else None


def attach_smart(host, drives):
    """選用：有 nvme-cli 才讀 SMART critical_warning，放進每顆的 smart_cw（讀不到 = None，不影響判定）。"""
    if isinstance(host, SimHost):
        return drives
    seen = {}
    for d in drives:
        m = re.match(r"/dev/(nvme\d+)", str(d.get("dev") or ""))
        if not m:
            d["smart_cw"] = None
            continue
        ctrl = "/dev/" + m.group(1)
        if ctrl not in seen:
            try:
                _rc, out, _e = host.run(SMART_CMD.format(dev=ctrl), timeout=15)
                seen[ctrl] = parse_smart_cw(out)
            except Exception:  # noqa: BLE001
                seen[ctrl] = None
        d["smart_cw"] = seen[ctrl]
    return drives


def rescan_and_list(host, wait_s=2.0):
    """rescan → 列舉。回傳 (drives, note)"""
    rescan(host, wait_s=wait_s)
    rows = list_drives(host)
    note = f"rescan 後 {len(rows)} 顆 NVMe" + ("（模擬）" if isinstance(host, SimHost) else "")
    return rows, note


COLS = ("dev", "model", "serial", "capacity", "fw", "bdf", "link")
COL_ZH = {"dev": "裝置", "model": "型號", "serial": "序號", "capacity": "容量",
          "fw": "韌體", "bdf": "PCIe BDF", "link": "Link"}

# PCIe 速率 → Generation（CEM）；對不到就原樣。
_LINK_GEN = ((32.0, 5), (16.0, 4), (8.0, 3), (5.0, 2), (2.5, 1))
LINE_SEP = " ｜ "


def fmt_link(s):
    """'32GT/s x4' / '16 GT/s Width x4' → 'Gen5 x4'／'Gen4 x4'；認不得就原樣。"""
    t = str(s or "").strip()
    if not t:
        return ""
    m = re.search(r"([\d.]+)\s*GT/?s", t, re.I)
    w = re.search(r"x\s*(\d+)", t, re.I)
    if not m:
        return t
    gt = float(m.group(1))
    gen = next((g for thresh, g in _LINK_GEN if abs(gt - thresh) < 0.4), None)
    width = f" x{w.group(1)}" if w else ""
    return f"Gen{gen}{width}" if gen else t


def fmt_cap_short(d):
    """主頁一行用短容量：3.84 TB；沒有 capacity_b 就從『（3.84 TB）』括號取。"""
    b = d.get("capacity_b") if isinstance(d, dict) else None
    try:
        b = int(b) if b not in (None, "") else None
    except (TypeError, ValueError):
        b = None
    if b and b > 0:
        if b >= 1e12:
            return f"{b / 1e12:.2f} TB"
        if b >= 1e9:
            return f"{b / 1e9:.2f} GB"
        if b >= 1e6:
            return f"{b / 1e6:.0f} MB"
        return f"{b} B"
    cap = str((d or {}).get("capacity") or "")
    m = re.search(r"（([^）]+)）", cap) or re.search(r"\(([^)]+)\)", cap)
    return m.group(1) if m else cap


def drive_line(d, verdict="PASS"):
    """單一顆：nvme0n1 ｜ Model ｜ SN ｜ 3.84 TB ｜ FW ｜ Gen4 x4 ｜ PASS"""
    dev = str((d or {}).get("dev") or "").replace("/dev/", "").strip() or "—"
    parts = [dev,
             str((d or {}).get("model") or "").strip() or "—",
             str((d or {}).get("serial") or "").strip() or "—",
             fmt_cap_short(d or {}) or "—",
             str((d or {}).get("fw") or "").strip() or "—",
             fmt_link((d or {}).get("link") or "") or "—"]
    if verdict:
        parts.append(verdict)
    return LINE_SEP.join(parts)


def homepage_line(drives, error=None, result=None):
    """主頁一行＋tooltip＋判定（PASS / WARN / FAIL）。多顆：前綴顆數＋第一顆，其餘放 tooltip。
    result = nvmecheck 的自動 rescan 結果 dict：一行改成「… ｜ Gen4 x4 ｜ 認盤 OK」或「掉盤 …」，附第幾次與觸發時機。"""
    if result:
        return _result_line(result)
    if error:
        return f"NVMe：失敗（{error}）", str(error), "FAIL"
    rows = [d for d in (drives or []) if isinstance(d, dict)]
    if not rows:
        return "NVMe：未認到碟", "", "FAIL"
    first = drive_line(rows[0], "PASS")
    if len(rows) == 1:
        return first, first, "PASS"
    shown = f"{len(rows)} 顆  {first}"
    tip = "\n".join(drive_line(d, "PASS") for d in rows)
    return shown, tip, "PASS"


def _result_line(r):
    res = r.get("result", "")
    tag = f"#{r['seq']} {r.get('phase', '')}".strip() if r.get("seq") else str(r.get("phase", ""))
    rows = [d for d in (r.get("drives") or []) if isinstance(d, dict)]
    tries = f"，rescan {r['attempts']} 次" if (r.get("attempts") or 1) > 1 else ""
    tip_extra = "\n".join(x for x in (
        f"判定：{res}　{r.get('detail', '')}".strip(), f"基準 link：{r.get('baseline') or '—'}",
        f"SMART critical_warning：{r.get('smart') or '—'}", f"{tag}{tries}") if x)
    if res == "OK":
        verdict = "認盤 OK"
    elif res == "掉盤":
        verdict = "掉盤"
    elif res in ("降速", "降寬", "降速降寬"):
        verdict = f"{res}（基準 {r.get('baseline') or '—'}）"
    elif res == "SMART 警告":
        verdict = f"SMART 警告 {r.get('smart') or ''}".strip()
    else:
        verdict = res or "—"
    level = "PASS" if res == "OK" else "WARN" if res in ("降速", "降寬", "降速降寬", "SMART 警告") else "FAIL"
    if rows:
        main = drive_line(rows[0], verdict)
        if len(rows) > 1:
            main = f"{len(rows)} 顆  {main}"
        tip = "\n".join(drive_line(d, verdict) for d in rows) + "\n" + tip_extra
    else:
        main = f"NVMe：{verdict}" + (f"（{r['detail']}）" if r.get("detail") and res in ("連線失敗",) else "（未認到碟）" if res == "掉盤" else "")
        tip = tip_extra
    return f"{main}　〔{tag}{tries}〕" if tag else main, tip, level


def rows_for_csv(drives):
    """summary.csv / 紀錄用的一行摘要（多顆用 ' | ' 串）"""
    if not drives:
        return {"NVMe 裝置": "", "NVMe 型號": "", "NVMe 序號": "", "NVMe 容量": "",
                "NVMe 韌體": "", "NVMe BDF": "", "NVMe Link": "", "NVMe 數": 0}
    join = lambda k: " | ".join(str(d.get(k, "") or "") for d in drives)  # noqa: E731
    return {"NVMe 裝置": join("dev"), "NVMe 型號": join("model"), "NVMe 序號": join("serial"),
            "NVMe 容量": join("capacity"), "NVMe 韌體": join("fw"), "NVMe BDF": join("bdf"),
            "NVMe Link": join("link"), "NVMe 數": len(drives)}


PROBE_CMD = "hostname; uname -r; id -un"
# 示範連線列上顯示的位址（未填主機時）
DEMO_HOST = "10.0.0.50"


def probe(host):
    """SSH 探詢：主機名稱、核心（uname -r）、帳號。失敗 → NvmeError。"""
    rc, out, err = host.run(PROBE_CMD, timeout=8)
    if rc not in (0, None):
        raise NvmeError(f"SSH 探詢失敗 rc={rc}：{(err or out).strip()[:200]}")
    lines = [x.strip() for x in (out or "").splitlines() if x.strip()]
    if len(lines) < 3:
        raise NvmeError("SSH 探詢回覆不完整（需要 hostname、uname -r、id -un）")
    return {"hostname": lines[0], "kernel": lines[1], "user": lines[2]}


def is_ssh_error(e):
    """這個例外代表 SSH 連線出問題（應標紅燈並重連）。"""
    if isinstance(e, (NvmeError, TimeoutError, ConnectionError, OSError, EOFError)):
        return True
    name = type(e).__name__
    if name in ("AuthenticationException", "SSHException", "NoValidConnectionsError",
                "socket.timeout", "gaierror"):
        return True
    s = str(e).lower()
    return any(k in s for k in ("ssh", "timed out", "connection reset", "no route",
                                "name or service not known", "authentication"))


class LinuxLink:
    """背景 SSH 連線／探詢／失敗重連（同儀器 Link 的退避）。paramiko 只在實機 connect() 時才 import。"""

    BACKOFF = (1, 2, 4, 8, 15, 30)
    KEEPALIVE_S = 8.0

    def __init__(self, cfg_fn, log=print, on_state=None, sim=False):
        self.cfg_fn = cfg_fn                          # () → {host, port, user, password}
        self.log = log or (lambda *_: None)
        self.on_state = on_state or (lambda *_: None)  # (state, detail, info)
        self.sim = sim
        self.dev = None
        self.info = {}
        self.last_check = None
        self.state = "idle"                            # idle / connecting / up / down
        self.detail = ""
        self.lock = __import__("threading").RLock()
        self._quit = __import__("threading").Event()
        self._th = None

    @property
    def up(self):
        return self.state == "up"

    def _set(self, state, detail="", info=None):
        self.state, self.detail = state, detail
        if info is not None:
            self.info = info
        try:
            self.on_state(state, detail, dict(self.info), self.last_check)
        except Exception:  # noqa: BLE001
            pass

    def start(self):
        self._quit.clear()
        self._spawn()

    def _spawn(self):
        import threading
        if self._th is not None and self._th.is_alive():
            return
        self._th = threading.Thread(target=self._loop, name="link-Linux", daemon=True)
        self._th.start()

    def stop(self):
        self._quit.set()
        self._close_dev()
        self.info, self.last_check = {}, None
        self._set("idle", "已停止")

    def _close_dev(self):
        with self.lock:
            h, self.dev = self.dev, None
        if h is not None:
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass

    def _cfg(self):
        try:
            return dict(self.cfg_fn() or {})
        except Exception:  # noqa: BLE001
            return {}

    def _loop(self):
        import time
        k = 0
        while not self._quit.is_set():
            cfg = self._cfg()
            host = str(cfg.get("host") or "").strip()
            if not host and not self.sim:
                self._close_dev()
                self.info, self.last_check = {}, None
                self._set("idle", "未填主機")
                if self._quit.wait(1.0):
                    return
                continue
            self._set("connecting", "連線中" if k == 0 else f"重連中（第 {k + 1} 次）")
            try:
                h = connect(host or DEMO_HOST,
                            port=cfg.get("port") or 22,
                            user=cfg.get("user") or "root",
                            password=cfg.get("password") or "",
                            sim=self.sim)
                info = probe(h)
                with self.lock:
                    self.dev = h
                self.last_check = time.time()
                who = f"{info.get('hostname') or host}  {info.get('kernel') or ''}".strip()
                self.log(f"[Linux] {'已連線' if k == 0 else '已重新連線'}：{who}")
                self._set("up", "已連線", info)
                k = 0
                while not self._quit.is_set():
                    if self._quit.wait(self.KEEPALIVE_S):
                        return
                    with self.lock:
                        cur = self.dev
                    if cur is None:
                        break
                    info = probe(cur)
                    self.last_check = time.time()
                    self._set("up", "已連線", info)
            except Exception as e:  # noqa: BLE001
                self._close_dev()
                wait = self.BACKOFF[min(k, len(self.BACKOFF) - 1)]
                self.log(f"[Linux] 連線失敗（第 {k + 1} 次）：{e} → {wait} s 後重試")
                self._set("down", f"{e}")
                k += 1
                if self._quit.wait(wait):
                    return

    def call(self, fn, *a, **kw):
        """在已連線的 SSH 上跑 fn(host, ...)；斷線 → NvmeError。"""
        with self.lock:
            h = self.dev
            if not self.up or h is None:
                raise NvmeError("Linux 尚未連線")
            try:
                return fn(h, *a, **kw)
            except Exception as e:  # noqa: BLE001
                if is_ssh_error(e):
                    raise NvmeError(f"Linux SSH：{e}") from e
                raise

    def restart(self):
        """設定改了：停掉再連。"""
        was = not self._quit.is_set() or (self._th is not None and self._th.is_alive())
        self.stop()
        if was:
            self._quit.clear()
            self.start()
