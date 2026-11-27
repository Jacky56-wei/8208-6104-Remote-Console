# -*- coding: utf-8 -*-
"""兩台示波器的通道 / bus name / 標稱值（gui、上電時序、模擬示波器共用）"""
from __future__ import annotations

MASTER_DEF, SLAVE_DEF = "8208HD", "6104B"
# 列表／圖例／CSV／計算遍歷：輔機 6104B 訊號在前，再主機 8208HD（與上下疊圖「輔上主下」一致）
MODELS = (SLAVE_DEF, MASTER_DEF)
ROLE = {MASTER_DEF: "主", SLAVE_DEF: "輔"}
NCH = {"8208HD": 8, "6104B": 4}
# 預設 bus name：同 u2core/scope2 A8_NAMES（主 8 CH）/ B4_NAMES（輔 4 CH）
NAMES = {"8208HD": ["CORE", "DDRVPP", "GPIO", "DDRIO", "PHYIO", "NFIO", "NFCORE", "RSTn"],
         "6104B": ["VIN", "VBUS", "BUSPG", "IBUS"]}
# 列表列序（與示波器 CH 編號分開）：輔機 VIN, VBUS, IBUS, BUSPG，再主機 CORE … RSTn
# 6104B 硬體通道仍是 C1 VIN、C2 VBUS、C3 BUSPG、C4 IBUS（NAMES 不變）
SIGNAL_ORDER = ("VIN", "VBUS", "IBUS", "BUSPG",
                "CORE", "DDRVPP", "GPIO", "DDRIO", "PHYIO", "NFIO", "NFCORE", "RSTn")
VNOM = {"CORE": 0.84, "DDRVPP": 2.5, "GPIO": 1.8, "DDRIO": 1.2, "PHYIO": 1.2, "NFIO": 1.2, "NFCORE": 0.9, "RSTn": 1.8,
        "VIN": 12.0, "VBUS": 12.0, "BUSPG": 3.3, "IBUS": 0.0}
UNIT_A = {"IBUS"}                 # 電流通道（單位 A；Auto scale 不動；上電時序只報峰值）
LOGIC = {"RSTn", "BUSPG"}         # 邏輯訊號：上電時序用單一「高準位門檻」（預設 0.7 × 標稱 = CMOS V_IH）


def kind(name):
    return "current" if name in UNIT_A else "logic" if name in LOGIC else "rail"


def all_names():
    """列表用通道名：輔機 VIN / VBUS / IBUS / BUSPG，再主機 CORE … RSTn。"""
    return list(SIGNAL_ORDER)


def name_rank(name):
    """通道名在 SIGNAL_ORDER 的位置（未知名排最後）。"""
    key = str(name or "").strip()
    try:
        return SIGNAL_ORDER.index(key)
    except ValueError:
        up = key.upper()
        for i, nm in enumerate(SIGNAL_ORDER):
            if nm.upper() == up:
                return i
        return len(SIGNAL_ORDER)


def sort_key(row):
    """dict（含 name 或 ch）或通道名 → 依 SIGNAL_ORDER。"""
    if isinstance(row, dict):
        nm = row.get("name") or row.get("ch") or row.get("rail")
        # ch 若是數字，改用 name
        if nm is not None and not isinstance(nm, str):
            nm = row.get("name")
        if not nm and row.get("model") and row.get("ch"):
            m, c = row["model"], int(row["ch"])
            names = NAMES.get(m) or []
            if 1 <= c <= len(names):
                nm = names[c - 1]
        return (name_rank(nm),)
    return (name_rank(row),)


def sort_rails(rows):
    """列表／圖例／時序 CSV：VIN, VBUS, IBUS, BUSPG，再 CORE … RSTn。"""
    return sorted(list(rows or []), key=sort_key)

