# -*- coding: utf-8 -*-
"""兩台示波器的通道 / bus name / 標稱值（gui、上電時序、模擬示波器共用）"""
from __future__ import annotations

MASTER_DEF, SLAVE_DEF = "8208HD", "6104B"
NCH = {"8208HD": 8, "6104B": 4}
# 預設 bus name：同 u2core/scope2 A8_NAMES（主 8 CH）/ B4_NAMES（輔 4 CH）
NAMES = {"8208HD": ["CORE", "DDRVPP", "GPIO", "DDRIO", "PHYIO", "NFIO", "NFCORE", "RSTn"],
         "6104B": ["VIN", "VBUS", "BUSPG", "IBUS"]}
VNOM = {"CORE": 0.84, "DDRVPP": 2.5, "GPIO": 1.8, "DDRIO": 1.2, "PHYIO": 1.2, "NFIO": 1.2, "NFCORE": 0.9, "RSTn": 1.8,
        "VIN": 12.0, "VBUS": 12.0, "BUSPG": 3.3, "IBUS": 0.0}
UNIT_A = {"IBUS"}                 # 電流通道（單位 A；Auto scale 不動；上電時序只報峰值）
LOGIC = {"RSTn", "BUSPG"}         # 邏輯訊號：上電時序用單一「高準位門檻」（預設 0.7 × 標稱 = CMOS V_IH）


def kind(name):
    return "current" if name in UNIT_A else "logic" if name in LOGIC else "rail"
