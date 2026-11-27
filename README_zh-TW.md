# 8208-6104 Remote Console — 雙示波器波形工具（UI 原型，待 review）

主 **WaveRunner 8208HD（MASTER，8 CH）** ＋ 輔 **HDO6104B（SLAVE，4 CH）**：
觸發設定、bus name、P1–P24 量測、Auto scale、對時、Aux Out → EXT 主從觸發，兩台**實圖（SCREEN_DUMP）**同時看，波形存到中控電腦。

> **目前狀態：UI 原型（示意資料）。** 畫面上的示波器圖是 **PLACEHOLDER 示意圖**，還沒接實機；
> `lecroy.py`、`busname.py`、`pmeas.py` 是儀器邏輯草稿（照 All in Test tool 寫法），**尚未接到 UI、未實機測試**。

## 畫面

| 檔案 | 內容 |
|---|---|
| `screenshots/ui_review_stack.png` | 主畫面：兩台實圖**上下無縫接合**（上 = 輔 6104B、下 = 主 8208HD；各裁到格線區，T/div、Delay 一樣 → 時間格對齊） |
| `screenshots/ui_review_merge_tab.png` | 「合一張（PC 合成波形）」分頁：只看，用讀回的波形資料在 PC 重畫 |
| `screenshots/ui_review_settings_scrolled.png` | 左欄捲到下方：P1–P24、存檔 |

## 畫面存檔

排列只有**上下**一種（使用者：「排列只留上下即可」）。按 **▶ 擷取＋存檔** 或 **📷 存上下實圖**：

- `畫面_上下.png`：兩張實圖裁到格線區、寬度對齊、上下直接接合（中間沒有分隔）；上方標題列（含觸發說明）、下方一列通道 bus name / 時基
- 另存兩台原始實圖 `輔_6104B_實圖.png`、`主_8208HD_實圖.png`
- 波形資料（.trc / CSV / NPZ / P 量測）照 ⑤ 存檔的格式勾選另存（接實機後）

範例輸出：`samples/`。格線區自動偵測（灰色格線外框），偵測不到改用 `composite.GRID_FRAC`（實機畫面位置待確認）。

## 版面

- **工具列**：▶ 擷取＋存檔、■ 停止、⟳ 實圖即時更新、📷 存兩台畫面、結果資料夾 / 匯出；右邊主 / 輔觸發狀態
- **工具列 ⤓ 讀回儀器設定 / ⤒ 寫入儀器設定**（兩台 / 主 / 輔）：觸發（來源 / 準位 / 斜率 / 模式）、時基（T/div、Delay）、通道（開關、V/div、Offset、bus name）、P1–P24、Aux Out / EXT 同步（`settings.py`；寫入前先檢查，不支援的不送）
- **① 儀器連線**：只顯示連線狀態。開程式自動連線；對時（中控電腦時間）與 Auto scale 在背景自動做，只寫紀錄（紀錄分頁 ＋ `桌面\scope waveform\scope_wave_saver.log`），**失敗才**在該台下方顯示紅字
- **② 觸發設定（主要）**：MASTER 選 8208HD / 6104B；觸發通道（C1 CORE … C8 RSTn）、準位 V、斜率 ↑ Rising / ↓ Falling / ↕ Either、模式、T/div、Delay；
  SLAVE：Aux Out → EXT（EX10、0.5 V、↑）、10 MHz 同步（選用）、輔時基跟主一樣
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

## 執行（需要 All in Test tool 的 `u2core`，版型與元件共用）

```bat
REM Windows：把 scope_wave_saver\ 放在 Autotest_tool 根目錄旁（或設 U2_TOOL_DIR）
set U2_TOOL_DIR=D:\0.Project\Github_project\Autotest_tool
py -3 -m scope_wave_saver.gui        REM --view merge = 開在合一張分頁；--save-to <資料夾> 測試存檔
```

需要：Python 3.10+、numpy、matplotlib、pillow（之後接實機另需 pyvisa ＋ NI-VISA / Keysight IO）。
重產示意圖：`py -3 -m scope_wave_saver.demo_screens`。

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
