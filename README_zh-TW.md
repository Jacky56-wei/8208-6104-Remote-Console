# 8208-6104 Remote Console — 雙示波器波形工具（UI 原型，待 review）

主 **WaveRunner 8208HD（MASTER，8 CH）** ＋ 輔 **HDO6104B（SLAVE，4 CH）**：
觸發設定、bus name、P1–P24 量測、Auto scale、對時、Aux Out → EXT 主從觸發，兩台**實圖（SCREEN_DUMP）**同時看，波形存到中控電腦。

> **目前狀態：UI 原型（示意資料）。** 畫面上的示波器圖是 **PLACEHOLDER 示意圖**，還沒接實機；
> `lecroy.py`、`busname.py`、`pmeas.py` 是儀器邏輯草稿（照 All in Test tool 寫法），**尚未接到 UI、未實機測試**。

## 畫面

| 檔案 | 內容 |
|---|---|
| `screenshots/ui_review_realimg_side.png` | 並排：左 = 輔 6104B 實圖、右 = 主 8208HD 實圖 |
| `screenshots/ui_review_realimg_stack.png` | 上下：輔在上、主在下（兩台 T/div、Delay 一樣 → 時間格上下對齊） |
| `screenshots/ui_review_realimg_merge.png` | 合一張：用讀回的波形資料在 PC 重畫（實圖無法疊成一張） |

## 版面

- **工具列**：▶ 擷取＋存檔、■ 停止、⟳ 實圖即時更新、📷 存兩台畫面、結果資料夾 / 匯出；右邊主 / 輔觸發狀態
- **① 儀器連線**：開程式自動連線、連線後 Auto scale、連線時同步中控電腦時間（＋立即對時 / 立即 Auto scale）
- **② 觸發設定（主要）**：MASTER 選 8208HD / 6104B；觸發通道（C1 CORE … C8 RSTn）、準位 V、斜率 ↑ Rising / ↓ Falling / ↕ Either、模式、T/div、Delay；
  SLAVE：Aux Out → EXT（EX10、0.5 V、↑）、10 MHz 同步（選用）、輔時基跟主一樣
- **③ 通道 ＋ bus name ＋ Auto scale**：主 C1–C8 = CORE、DDRVPP、GPIO、DDRIO、PHYIO、NFIO、NFCORE、RSTn；輔 C1–C4 = VIN、VBUS、BUSPG、IBUS（電流 A）
- **④ 量測 P1–P24（每台）**：8208HD 示波器 P1–P12、6104B 示波器 P1–P8，其餘 PC 用同一筆波形計算
- **⑤ 存檔**：預設 `桌面\scope waveform\<前綴>_<日期時間>\`；PNG 實圖、.trc、CSV、NPZ、P 量測 CSV、info.json

## 執行（需要 All in Test tool 的 `u2core`，版型與元件共用）

```bat
REM Windows：把 scope_wave_saver\ 放在 Autotest_tool 根目錄旁（或設 U2_TOOL_DIR）
set U2_TOOL_DIR=D:\0.Project\Github_project\Autotest_tool
py -3 -m scope_wave_saver.gui --view side        REM side / stack / merge
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
