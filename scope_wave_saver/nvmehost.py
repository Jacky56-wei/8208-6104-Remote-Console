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
    """示範：不連 SSH，rescan 後回傳模擬 NVMe"""

    def close(self):
        pass

    def run(self, cmd, timeout=30):
        return 0, "", ""


def connect(host, port=22, user="root", password="", key_filename="", timeout=8, sim=False):
    """連 Linux 主機。sim=True → 不 import paramiko。只在實機連線時才 import paramiko。"""
    if sim:
        return SimHost()
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


def rescan(host, wait_s=2.0):
    """PCIe rescan，然後稍等列舉。"""
    rc, out, err = host.run(RESCAN_CMD, timeout=20)
    if not isinstance(host, SimHost) and rc not in (0, None):
        raise NvmeError(f"PCIe rescan 失敗 rc={rc}：{(err or out).strip()[:200]}")
    time.sleep(max(float(wait_s or 0), 0.0))
    return rc, (out or "").strip(), (err or "").strip()


def list_drives(host):
    """→ list[dict]；示範主機回傳 SIM_DRIVES。"""
    if isinstance(host, SimHost):
        return [dict(x) for x in SIM_DRIVES]
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


def rescan_and_list(host, wait_s=2.0):
    """rescan → 列舉。回傳 (drives, note)"""
    rescan(host, wait_s=wait_s)
    rows = list_drives(host)
    note = f"rescan 後 {len(rows)} 顆 NVMe" + ("（模擬）" if isinstance(host, SimHost) else "")
    return rows, note


COLS = ("dev", "model", "serial", "capacity", "fw", "bdf", "link")
COL_ZH = {"dev": "裝置", "model": "型號", "serial": "序號", "capacity": "容量",
          "fw": "韌體", "bdf": "PCIe BDF", "link": "Link"}


def rows_for_csv(drives):
    """summary.csv / 紀錄用的一行摘要（多顆用 ' | ' 串）"""
    if not drives:
        return {"NVMe 裝置": "", "NVMe 型號": "", "NVMe 序號": "", "NVMe 容量": "",
                "NVMe 韌體": "", "NVMe BDF": "", "NVMe Link": "", "NVMe 數": 0}
    join = lambda k: " | ".join(str(d.get(k, "") or "") for d in drives)  # noqa: E731
    return {"NVMe 裝置": join("dev"), "NVMe 型號": join("model"), "NVMe 序號": join("serial"),
            "NVMe 容量": join("capacity"), "NVMe 韌體": join("fw"), "NVMe BDF": join("bdf"),
            "NVMe Link": join("link"), "NVMe 數": len(drives)}
