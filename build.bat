@echo off
rem 打包成 dist\ScopeWaveSaver\ScopeWaveSaver.exe（目標電腦不需 Python，但仍需 NI-VISA / Keysight IO Libraries）
chcp 65001 >nul
cd /d "%~dp0"
if not defined U2_TOOL_DIR set "U2_TOOL_DIR=D:\0.Project\Github_project\Autotest_tool"
if not exist "%U2_TOOL_DIR%\u2core" (
  echo [錯誤] 找不到 %U2_TOOL_DIR%\u2core ，請先 set U2_TOOL_DIR=Autotest_tool 路徑
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv || python -m venv .venv || (pause ^& exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (pause ^& exit /b 1)
)
".venv\Scripts\python.exe" -m pip install pyinstaller || (pause ^& exit /b 1)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean ScopeWaveSaver.spec || (pause ^& exit /b 1)
echo.
echo Done: dist\ScopeWaveSaver\ScopeWaveSaver.exe
pause
