# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec：python -m PyInstaller --noconfirm --clean ScopeWaveSaver.spec
# u2core 來自 Autotest_tool（不放進本 repo）→ 用 U2_TOOL_DIR 指過去，打包時一併收進 exe
import os
from PyInstaller.utils.hooks import collect_submodules

TOOL = os.environ.get("U2_TOOL_DIR", r"D:\0.Project\Github_project\Autotest_tool")
if not os.path.isdir(os.path.join(TOOL, "u2core")):
    raise SystemExit(f"找不到 u2core：{TOOL}\\u2core（設 U2_TOOL_DIR=Autotest_tool 路徑）")

# 執行時實際載入的 u2core 模組（其餘 u2core 內的延遲 import 交給 PyInstaller 靜態分析）
U2 = ["u2core", "u2core.alias", "u2core.appdirs", "u2core.autoconn", "u2core.breathbar", "u2core.chartfit",
      "u2core.exporter", "u2core.instruments", "u2core.pagetpl", "u2core.robust", "u2core.scope8208",
      "u2core.scopelabel", "u2core.scopesync", "u2core.shared", "u2core.sshui", "u2core.theme", "u2core.ui",
      "u2core.winfix", "u2core.visapool", "u2core.scopeext", "u2core.scopescale", "u2core.timesync"]
hidden = U2 + ["scope_wave_saver.gui", "scope_wave_saver.capture", "scope_wave_saver.sequencing", "scope_wave_saver.link", "matplotlib.backends.backend_agg", "matplotlib.backends.backend_tkagg", "PIL.ImageTk", "PIL._tkinter_finder", "pyvisa"]
try:
    hidden += collect_submodules("pyvisa_py")
except Exception:  # noqa: BLE001
    pass

a = Analysis(
    ["run_gui.py"],
    pathex=[os.path.abspath("."), TOOL],
    datas=[(os.path.join("scope_wave_saver", "assets"), os.path.join("scope_wave_saver", "assets"))],
    hiddenimports=hidden,
    excludes=["PySide6", "PyQt5", "PyQt6", "scipy", "paramiko", "openpyxl", "pycosmicsdk"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ScopeWaveSaver", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ScopeWaveSaver")
