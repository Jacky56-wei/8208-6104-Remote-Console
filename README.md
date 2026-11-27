# 8208-6104 Remote Console

Teledyne LeCroy **WaveRunner 8208HD（主 MASTER，8 CH）＋ HDO6104B（輔 SLAVE，4 CH）** 雙示波器遠端控制工具：
觸發設定、bus name、P1–P24 量測；連線後背景自動對時、Auto scale、主 Aux Out → 輔 EXT 主從觸發，兩台實圖同時看，波形存到中控電腦。

Keysight **N6705** ARB uses one shared template (Ramp / Brownout margin / Instant ON-OFF-ON) applied to a **single** selected channel (CH1 / CH3 selector, default **CH3 VIN 12V**; CH2/CH4 under Advanced). Default master trigger is **C8 RSTn**. Window sized for 1920×1080. Sub-panels (①–⑨, timing table/plot, spec compare, log, …) collapse, drag-reorder, sash-resize, or detach; layout is saved. The **DUT 規格** selector (U.2 / M.2 + 電源設計表 dropdown + overall verdict) sits in a fixed bar above ① 儀器連線. Card ⑨ is the collapsible measured-vs-spec table (Autotest_tool `dut_power` JSON, `powerprofile.rng` + `designspec.compare`); selecting the form factor sets N6705 CH3 VIN to 12 V or 3.3 V. Optional Linux NVMe rescan is off by default; when enabled, the connection strip also shows **Linux <host IP>** (dot: green / gray / red, SSH auto-reconnect, settings in ⚙) and a one-line drive summary (`nvme0n1 ｜ Model ｜ SN ｜ Capacity ｜ FW ｜ Gen4 x4 ｜ PASS`) appears under ① on the main page, and the full table stays in the collapsible NVMe card.  Signal lists (timing table/plot/CSV, spec compare, merged-wave legend, `summary.csv` rail/P columns, P-measurement tabs) put **slave 6104B first** (VIN, VBUS, BUSPG, IBUS) then master 8208HD (CORE … RSTn); the timing **順序** column stays chronological vs VIN. Scope-side P1–P12 / P1–P8 numbering is unchanged. `summary.csv` records **ARB 通道** plus **規格／電源設計表／規格總判定／規格明細**.

預設為示範模式（模擬示波器 ＋ 模擬 N6705 跑完整流程：ARB＋擷取、連續擷取、上 / 下電時序與 slew rate、USB 自動重連）；`--real` 接實機，尚未實機測試。完整說明：[`README_zh-TW.md`](README_zh-TW.md)。

## Run (Windows)

Needs Python 3.10–3.13, NI-VISA or Keysight IO Libraries, and Autotest_tool (for `u2core`).

```bat
run.bat
REM or manually:
py -3 -m pip install -r requirements.txt
set U2_TOOL_DIR=D:\0.Project\Github_project\Autotest_tool
py -3 -m scope_wave_saver.gui
```

## Build exe (Windows)

```bat
build.bat
REM or manually:
set U2_TOOL_DIR=D:\0.Project\Github_project\Autotest_tool
py -3 -m pip install -r requirements.txt pyinstaller
py -3 -m PyInstaller --noconfirm --clean ScopeWaveSaver.spec
REM -> dist\ScopeWaveSaver\ScopeWaveSaver.exe
```
