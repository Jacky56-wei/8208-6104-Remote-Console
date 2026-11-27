# -*- coding: utf-8 -*-
"""示意用「示波器實圖」佔位圖（沒接實機時顯示；實機改用 SCREEN_DUMP 抓到的 PNG）。
仿 LeCroy MAUI 黑底畫面（HARDCOPY_SETUP …BCKG,BLACK…AREA,DSOWINDOW）：10 × 8 格、通道色、下方 descriptor 方框、
右下時基 / 觸發方框，並蓋上「PLACEHOLDER 示意圖（未連實機）」字樣。"""
from __future__ import annotations

import os

import numpy as np

COLORS = ["#F2C744", "#E0457B", "#3FA9F5", "#3BB273", "#F28C28", "#9B59B6", "#1ABC9C", "#D0D0D0"]


def make(path, model, names, units=None, size=(1920, 1080), trig="C8 ↓ 0.90 V", ext=False, tdiv="1.00 ms/div",
         delay="-3.00 ms"):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    units = units or {}
    w, h = size
    fig = Figure(figsize=(w / 100, h / 100), dpi=100, facecolor="black")
    ax = fig.add_axes([0.01, 0.20, 0.98, 0.76], facecolor="black")
    t = np.linspace(-5, 5, 2000)               # 格（10 格；觸發點在畫面左 2 格 → delay）
    n = len(names)
    for i, nm in enumerate(names):
        base = 3.5 - i * (7.0 / max(n - 1, 1))
        d = (-3 + 0.5 * i) if not ext else (-3.6 + 0.25 * i)
        if units.get(nm) == "A":
            y = base + 0.9 * np.exp(-np.clip(t - d, 0, None) / 1.2) * (t > d) + 0.15 * (t > d)
        elif nm.endswith("n") or nm.endswith("PG"):
            y = base + 0.8 * (t > d + 1.2)
        else:
            y = base + 0.8 * np.clip((t - d) / 0.3, 0, 1)
        y = y + 0.01 * np.random.randn(t.size)
        ax.plot(t, y, color=COLORS[i % 8], lw=1.0)
        ax.text(-4.95, base + 0.12, f"{i + 1}", color="black", fontsize=8, weight="bold",
                bbox=dict(boxstyle="square,pad=0.15", fc=COLORS[i % 8], ec="none"))
    for x in range(-5, 6):
        ax.axvline(x, color="#3a3a3a", lw=0.6)
    for yv in range(-4, 5):
        ax.axhline(yv, color="#3a3a3a", lw=0.6)
    ax.axvline(-3, color="#ff8800", lw=0.8, ls=":")
    ax.set_xlim(-5, 5)
    ax.set_ylim(-4, 4)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#777")
    fam = ["Noto Sans CJK JP", "DejaVu Sans"]
    bw = 0.98 / (n + 1)
    for i, nm in enumerate(names):            # 下方 descriptor 方框（C1 … Cn）
        bx = fig.add_axes([0.01 + i * bw, 0.03, bw * 0.95, 0.14], facecolor="#111")
        bx.set_xticks([])
        bx.set_yticks([])
        for s in bx.spines.values():
            s.set_color(COLORS[i % 8])
        bx.text(0.05, 0.72, f"C{i + 1}  {nm}", color=COLORS[i % 8], fontsize=9, weight="bold", transform=bx.transAxes)
        bx.text(0.05, 0.40, "200 mV/div" if units.get(nm) != "A" else "200 mA/div", color="#ddd", fontsize=8,
                transform=bx.transAxes)
        bx.text(0.05, 0.10, "-700 mV ofst" if units.get(nm) != "A" else "0 A ofst", color="#aaa", fontsize=8,
                transform=bx.transAxes)
    tb = fig.add_axes([0.01 + n * bw, 0.03, bw * 0.95, 0.14], facecolor="#111")
    tb.set_xticks([])
    tb.set_yticks([])
    for s in tb.spines.values():
        s.set_color("#999")
    tb.text(0.05, 0.72, f"Timebase {delay}", color="#ddd", fontsize=8, transform=tb.transAxes)
    tb.text(0.05, 0.42, f"{tdiv}  10 MS  1 GS/s", color="#ddd", fontsize=8, transform=tb.transAxes)
    tb.text(0.05, 0.12, ("Trigger EX10 ↑ 0.50 V" if ext else f"Trigger {trig}") + "  Stopped", color="#ffb000",
            fontsize=8, transform=tb.transAxes, family=fam)
    fig.text(0.01, 0.975, f"Teledyne LeCroy  {model}", color="#9ad", fontsize=10, va="top")
    fig.text(0.99, 0.975, "11/27/2026 9:00:12 PM", color="#9ad", fontsize=9, va="top", ha="right")
    fig.text(0.5, 0.58, "PLACEHOLDER 示意圖（未連實機）\n實機 = SCREEN_DUMP 實圖", color="#ffffff", alpha=0.35,
             fontsize=int(h / 22), ha="center", va="center", family=fam, weight="bold")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, facecolor="black")
    return path


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    make(os.path.join(here, "placeholder_8208HD.png"), "WaveRunner 8208HD",
         ["CORE", "DDRVPP", "GPIO", "DDRIO", "PHYIO", "NFIO", "NFCORE", "RSTn"], size=(1920, 1080))
    make(os.path.join(here, "placeholder_6104B.png"), "HDO6104B", ["VIN", "VBUS", "BUSPG", "IBUS"], {"IBUS": "A"},
         size=(1280, 800), ext=True)
    print("ok")
