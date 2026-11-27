# -*- coding: utf-8 -*-
"""PyInstaller / 直接執行的入口：python run_gui.py（等同 python -m scope_wave_saver.gui）

U2_TOOL_DIR = Autotest_tool 路徑（要用它的 u2core：theme / ui / pagetpl …）。
"""
import os
import sys

_TOOL = os.environ.get("U2_TOOL_DIR", r"D:\0.Project\Github_project\Autotest_tool")
if os.path.isdir(os.path.join(_TOOL, "u2core")) and _TOOL not in sys.path:
    sys.path.insert(0, _TOOL)

from scope_wave_saver.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
