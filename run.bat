@echo off
rem 執行 ScopeWaveSaver（雙示波器波形工具）。U2_TOOL_DIR = Autotest_tool 路徑（可先 set 覆寫）
chcp 65001 >nul
cd /d "%~dp0"
if not defined U2_TOOL_DIR set "U2_TOOL_DIR=D:\0.Project\Github_project\Autotest_tool"
if not exist "%U2_TOOL_DIR%\u2core" (
  echo [錯誤] 找不到 %U2_TOOL_DIR%\u2core ，請先 set U2_TOOL_DIR=Autotest_tool 路徑
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [首次執行] 建立 .venv 並安裝套件...
  py -3 -m venv .venv || python -m venv .venv || (pause ^& exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause ^& exit /b 1)
)
".venv\Scripts\python.exe" -m scope_wave_saver.gui %*
