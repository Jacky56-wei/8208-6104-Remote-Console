# 8208-6104 Remote Console — 雙示波器波形工具

主 **WaveRunner 8208HD（MASTER，8 CH）** ＋ 輔 **HDO6104B（SLAVE，4 CH）**：
觸發設定、bus name、P1–P24 量測、Auto scale、對時、Aux Out → EXT 主從觸發，兩台**實圖（SCREEN_DUMP）**同時看，波形存到中控電腦。

> **目前狀態：預設 = 示範模式（模擬示波器 `SimScope` ＋ 模擬 N6705 `SimN6705`）**，整個流程（連線 → 背景設定 → arm → N6705 ARB → 觸發 → 讀 → 存 → 上 / 下電時序 → 斷線重連）
> 都用模擬儀器跑過；畫面上的示波器圖是 **PLACEHOLDER 示意圖**。`--real` 接實機（USB VISA），**尚未實機測試**。

## 畫面

| 檔案 | 內容 |
|---|---|
| `screenshots/ui_review_stack.png` | 主畫面：兩台實圖**上下無縫接合**（上 = 輔 6104B、下 = 主 8208HD；各裁到格線區，T/div、Delay 一樣 → 時間格對齊） |
| `screenshots/ui_review_merge_tab.png` | 「合一張（PC 合成波形）」分頁：只看，用讀回的波形資料在 PC 重畫 |
| `screenshots/ui_review_settings_scrolled.png` | 左欄捲到下方：P1–P24、存檔 |
| `screenshots/ui_capture_paused.png` | 連續擷取中拔掉 6104B USB（示範）：6104B 紅燈、「USB 中斷 — 背景重連中」、擷取 **暫停** |
| `screenshots/ui_seq_powerup.png` / `ui_seq_powerdown.png` | 「上 / 下電時序」分頁：表格（順序、上 / 下電時間、10↔90 % 時間、slew rate、延遲、PASS / FAIL）＋ 時序圖 |
| `screenshots/ui_arb_RAMP.png` / `ui_arb_BROWNOUT.png` / `ui_arb_ONOFFON.png` | ⑧ N6705 ARB：三種範本的參數欄位 ＋ **即時預覽**（掃描值疊圖：實線 = 第一個、虛線 = 其他） |
| `screenshots/ui_arb_seq_onoffon.png` | ARB ON-OFF-ON 擷取後的「上 / 下電時序」分頁 |
| `screenshots/ui_settings_capture_seq.png` | 左欄 ⑥ 連續擷取（N 次、進度）、⑦ 上電時序設定（每條 rail 門檻、上 / 下電延遲限制） |

## 畫面存檔

排列只有**上下**一種（使用者：「排列只留上下即可」）。按 **▶ 擷取＋存檔** 或 **📷 存上下實圖**：

- `畫面_上下.png`：兩張實圖裁到格線區、寬度對齊、上下直接接合（中間沒有分隔）；上方標題列（含觸發說明）、下方一列通道 bus name / 時基
- 另存兩台原始實圖 `輔_6104B_實圖.png`、`主_8208HD_實圖.png`
- 波形資料（.trc / CSV / NPZ / P 量測）照 ⑤ 存檔的格式勾選另存（接實機後）

範例輸出：`samples/`。格線區自動偵測（灰色格線外框），偵測不到改用 `composite.GRID_FRAC`（實機畫面位置待確認）。

## ⑥ 連續擷取（觸發次數）

- **觸發次數 N**（0 = 一直到 ■ 停止）、**等觸發逾時**（0 = 一直等；逾時 → 重新 arm 同一個序號）；▶ 擷取＋存檔 開始，■ 停止 = 做完這一次就停
- 每一次（`capture.py`，順序照 `u2core/scope8208` arm_single / wait_acq、`u2core/scope2` wait_b）：
  輔先 arm（STOP → INR? → TRIG_MODE SINGLE → 讀回 SINGLE）→ 主再 arm → 等 pre-trigger 填滿 → 等主觸發（INR? bit0 / TRIG_MODE? = STOP）
  → 等輔跟著觸發（≤ 3 s，沒觸發 → 紀錄提醒檢查 BNC，照存主的）→ 兩台 WaitUntilIdle → 讀實圖、波形、P 值 → 存
- 每次一個序號資料夾：`<存到>\<前綴>_<日期時間>_連續\0001_210012\0001_畫面_上下.png`、`0001_主_8208HD_C1_CORE.trc / .csv`、
  `0001_pmeas.csv`、`0001_上電時序.csv / .png`（或 `下電時序`）、`0001_info.json`
- 同一個資料夾的 **`summary.csv`**：每次一列（序號、觸發時間、上電 / 下電、輔有沒有觸發、時序判定、順序、每條 rail 延遲與 slew rate、每個 P 值、斷線備註）
- 進度：⑥ 進度條 ＋「n / N · 最後觸發 hh:mm:ss · 狀態（arm 中 / 等觸發 / 讀取存檔中 / ⏸ 暫停）」，工具列右上狀態也一起顯示
- 範例輸出：`samples/連續擷取_示範/`（2 次：上電 ＋ 下電）

## ⑦ 上 / 下電時序 ＋ slew rate（PC 計算）

- 用存下來的波形（兩台同一條時間軸，t = 0 = 主觸發；輔由 AUX OUT → EXT 觸發，可填「輔時間修正 ns」）
- **方向**：自動判斷（看各 rail 波形頭尾是升還是降）／上電／下電
- 每條 rail：**10 % / 90 %**（可改）× 標稱 → 穿越時間；上升 / 下降時間；**slew rate V/ms**（下電為負；IBUS 電流 A/ms：上電 = 10→90 % 峰值，下電 = 起始電流 90→10 %）
- RSTn、BUSPG = 邏輯訊號：上 / 下電時間用 **邏輯門檻 V**（預設 0.7 × 標稱，可改）
- 上 / 下電時間 = 高門檻（power good，預設）或低門檻；**順序**、對 **參考 rail（預設 VIN）的延遲**
- 可選：每條 rail **上電延遲下限 / 上限**、**下電延遲下限 / 上限**（ms）→ PASS / FAIL（範例：CORE、RSTn 已填，可清掉）
- 右側「上 / 下電時序」分頁：表格 ＋ 時序圖（每條 rail 一列、標 10 / 90 % 點、紅線 = 觸發、藍虛線 = 參考）；每次擷取存 CSV ＋ PNG

## USB 自動重連（`link.py`）

- 每台一個 Link：所有指令走同一把鎖；VISA 錯誤 / 逾時（VisaIOError、VI_ERROR_TMO / CONN_LOST / RSRC_NFOUND、OSError…）→ **標成斷線（紅燈 ＋ ① 該台下方「USB 中斷 — 背景重連中」）**
- 背景重試，退避 1、2、4、8、15、30 s；重連成功 → 先重新做背景設定（對時、主 AUX OUT / 輔 EXT、bus name、**上次「寫入儀器設定」的內容**；沒寫過才做 Auto scale）→ 才標回已連線
- 閒置時每 3 s 心跳（`*OPC?`；即時更新開著就用 SCREEN_DUMP）→ 沒在擷取也會發現被拔
- 連續擷取中斷線 → **暫停**，重連後 **重做這一次**（不跳號），空檔寫紀錄與 summary 備註；所有事件寫紀錄分頁 ＋ log 檔
- 示範：① 每台有「模擬拔 USB 5 s（示範）」按鈕；或 `--unplug 6104B:4@3`（3 s 後拔 6104B 4 s）

## ⑧ N6705 ARB 啟動＋擷取（`arbtpl.py`、`n6705.py`）

不用編點，三種**參數式範本**（每個 N6705 通道各選一個；預設 CH3 = VIN 12V，同 All in Test tool「DUT 12V 預設 CH3」）：

| 範本 | 參數 | 下載成 |
|---|---|---|
| **Ramp** | 起始 V、結束 V、延遲、上升時間、停留、下降時間、結束停留、重複 | N6705 原生 Trapezoid（`ARB:FUNC:SHAP TRAP`） |
| **Brownout margin** | 標稱 V、**掉到**（V 或 % margin，可掃描 `-5%/-10%/-20%`）、掉落時間、下降 / 回升時間、前段停留、間隔、重複 | 單一準位 = Trapezoid（同 `arbmodule.from_powercut`）；「全部放在同一個 ARB」= 各段串成 User Defined（`arbmodule.merge`） |
| **Instant ON-OFF-ON** | ON 準位、先 ON 停留、**OFF 時間**（可掃描 `1ms/2ms/5ms`）、再 ON 停留、重複 | User Defined 3 點（ON → 0 V → ON，結束停在最後值） |

- 數值可帶單位（`2ms`、`100u`、`12 V`；`arbmodule.parse_num`）；每個掃描值都先用 `arbmodule.validate` 檢查（N6705 點數 / dwell / 電壓上限），有錯在卡片紅字顯示、不會下載
- **即時預覽**：改任何參數 0.25 s 後重畫（掃描值疊圖），下面一行顯示形狀、點數、長度
- 掃描：**每次擷取換下一個值**（預設；序號 1 → 第 1 個值…）或全部放在同一個 ARB；**循環 N 次** → 總擷取次數 = N × 掃描值數（0 = 直到 ■ 停止）
- 每次流程：輔 arm → 主 arm → 下載 ARB（`ABOR:TRAN` → 形狀 / 參數 → `ARB:COUN` → `ARB:TERM:LAST ON` → `TRIG:ARB:SOUR BUS` → `SYST:ERR?` / `*OPC?`）
  → `OUTP ON` → `INIT:TRAN (@ch…)` → **一個 `*TRG` 所有通道同時開始** → 等主觸發（逾時 = ARB 長度 + 1 s）→ 讀 / 存
- **ARB 跑完主沒觸發**（例 Brownout -10 % 沒掉到 UVLO、CORE 沒動）→ summary 記一列「未觸發」＋ 掃描值，換下一個值（= margin 結果：哪個準位開始 reset）
- info.json 加 `arb`（每通道範本、參數、這次的掃描值、形狀、長度、送出的 SCPI）；summary.csv 加「ARB 範本 / ARB 掃描值 / ARB 參數」
- **安全中止**：■ 停止、擷取錯誤、關視窗 → `ABOR:TRAN` → `OUTP OFF`（或選「維持最後電壓」）；卡片 **⛔ N6705 安全中止** 隨時可按
- N6705 也在 ① 儀器連線：背景自動連線 ＋ USB 自動重連（同示波器的 Link）；連線後只做 `*CLS` 與對時（`u2core.timesync`），**不動輸出**
- 示範：模擬 N6705（`SimN6705`）的 VIN 輸出帶動模擬 DUT（VIN ≥ 9.6 V 依序上電、< 9.0 V 依序下電），兩台示波器的波形由 ARB 算出；
  `--arb 1 --arb-tpl BROWNOUT` 自動跑一次。範例輸出：`samples/ARB_Brownout_示範/`（-10 %、-20 % 未觸發，-50 % 觸發）、`samples/ARB_ON-OFF-ON_示範/`

## 版面

- **工具列**：▶ 擷取＋存檔、■ 停止、⟳ 實圖即時更新、📷 存上下實圖、結果資料夾 / 匯出；右邊主 / 輔觸發狀態
- **工具列 ⤓ 讀回儀器設定 / ⤒ 寫入儀器設定**（兩台 / 主 / 輔）：觸發（來源 / 準位 / 斜率 / 模式）、時基（T/div、Delay）、通道（開關、V/div、Offset、bus name）、P1–P24；主 / 輔同步用固定值一併寫入（主 AUX OUT = Trigger Out、輔 EX10 / 0.5 V / ↑；不碰 10 MHz Ref In/Out）（`settings.py`；寫入前先檢查，不支援的不送）
- **① 儀器連線**：只顯示連線狀態。開程式自動連線；對時（中控電腦時間）與 Auto scale 在背景自動做，只寫紀錄（紀錄分頁 ＋ `桌面\scope waveform\scope_wave_saver.log`），**失敗才**在該台下方顯示紅字
- **② 觸發設定（MASTER 觸發通道 / 準位 / 斜率）**：MASTER 選 8208HD / 6104B；觸發通道（C1 CORE … C8 RSTn）、準位 V、斜率 ↑ Rising / ↓ Falling / ↕ Either、模式、T/div、Delay；
  主 / 輔同步（接線：主 8208HD AUX OUT ──BNC── 輔 6104B EXT）：主 AUX OUT = Trigger Out、輔 EX10、0.5 V、↑，連線後背景自動設，輔時基跟主一樣（不用 10 MHz Ref In/Out）；UI 不顯示，失敗才顯示
- **③ 通道 ＋ bus name**：主 C1–C8 = CORE、DDRVPP、GPIO、DDRIO、PHYIO、NFIO、NFCORE、RSTn；輔 C1–C4 = VIN、VBUS、BUSPG、IBUS（電流 A）
- **④ 量測 P1–P24（每台）**：每列標「示波器」或「PC 計算」，寫入前列出會改 PC 計算的列

### P1–P24 兩台能吃進多少（已核對）

| | WaveRunner 8208HD | HDO6104B |
|---|---|---|
| 示波器量測 P | **P1–P12**（8000HD datasheet：Display up to 12 measurement parameters） | **P1–P8**（HDO6104B datasheet：Display up to 8 measurement parameters） |
| 數學通道（V×I 用 Product） | F1–F12 | F1–F8 |
| 參數（兩台 datasheet 都有） | Max、Min、Mean、PkPk、Rise、Fall、Width、Frequency、Delay、Δ Time@level | 同左 |
| 實機確認（Autotest_tool） | Rise、Fall、Max、Min、Mean、PkPk、Delay | 尚無紀錄（寫入後讀回比對） |

超過 P 上限、數學通道用完、或型號不支援的參數 → **不送示波器**，改 PC 用同一筆波形計算，寫入前在 UI 與紀錄提醒（`pmeas.parse / warnings`）。
- **⑤ 存檔**：預設 `桌面\scope waveform\<前綴>_<日期時間>\`；PNG 實圖、.trc、CSV、NPZ、P 量測 CSV、info.json

## 執行（Windows）

需要：Python 3.10–3.13（64 位元）、NI-VISA 或 Keysight IO Libraries，以及 All in Test tool（Autotest_tool）的 `u2core`（版型與元件共用，不放在本 repo）。
套件見 `requirements.txt`：numpy、matplotlib、pillow、pyvisa、pyvisa-py。

**最簡單：雙擊 `run.bat`**（第一次會建 `.venv` 並裝套件；`U2_TOOL_DIR` 預設 `D:\0.Project\Github_project\Autotest_tool`，可先 `set` 覆寫）。

手動：

```bat
git clone https://github.com/Jacky56-wei/8208-6104-Remote-Console.git
cd 8208-6104-Remote-Console
py -3 -m pip install -r requirements.txt
set U2_TOOL_DIR=D:\0.Project\Github_project\Autotest_tool
py -3 -m scope_wave_saver.gui        REM 示範模式；接實機加 --real（run.bat --real 也可以）
```

參數：`--real` 接實機（預設示範模式）；`--view merge|seq` 開在合一張 / 上下電時序分頁；`--capture N` 啟動後自動擷取 N 次；
`--unplug 6104B:4@3`（示範）模擬拔 USB；`--folder <資料夾>` 存檔位置；`--save-to <資料夾>` 測試存檔。重產示意圖：`py -3 -m scope_wave_saver.demo_screens`。

## 打包成 exe（Windows）

**雙擊 `build.bat`** → `dist\ScopeWaveSaver\ScopeWaveSaver.exe`（目標電腦不需 Python，但仍需 NI-VISA / Keysight IO）。

手動：

```bat
set U2_TOOL_DIR=D:\0.Project\Github_project\Autotest_tool
py -3 -m pip install -r requirements.txt pyinstaller
py -3 -m PyInstaller --noconfirm --clean ScopeWaveSaver.spec
```

`ScopeWaveSaver.spec`：入口 `run_gui.py`；從 `U2_TOOL_DIR` 把用到的 `u2core` 模組收進 exe（exe 執行時不需要 Autotest_tool）、
`scope_wave_saver\assets` 一併打包；PySide6 / scipy 等 Autotest_tool 才用的大套件排除。`dist\`、`build\`、`.venv\` 不進 git。

## 參考（All in Test tool 的做法）

| 功能 | 參考模組 |
|---|---|
| 實圖 | `u2core/scope8208.py`：`HARDCOPY_SETUP DEV,PNG,BCKG,BLACK,DEST,REMOTE,AREA,DSOWINDOW` → `SCREEN_DUMP` |
| 波形 | `u2tools/timing/scope_hdo.py`：`Cn:WAVEFORM? ALL`、WAVEDESC 解析 |
| bus name | `u2core/scopesync.py`：`app.Acquisition.Cn.Alias` / `LabelsText` + `ViewLabels` |
| P1–P24 | `u2core/pmtable.py`：`app.Measure.Pn.ParamEngine / Source1 / Source2` |
| Auto scale | `u2core/scopescale.py` |
| 主從觸發 | `u2core/scope2.py`：`AUXILIARY_OUTPUT TRIGGER_OUT`、SLAVE `TRIG_SELECT EDGE,SR,EX10` |
| 觸發 / Either | `u2core/trigpanel.py`：`Cn:TRIG_SLOPE WINDOW` → VBS `Trigger.Cn.Slope = "Either"` |
| 對時 | `u2core/timesync.py` / `scopeext.py`：`DATE` |
| 自動連線 | `u2core/autoconn.py`；位址 `u2core/instruments.py` KNOWN_ADDR |
