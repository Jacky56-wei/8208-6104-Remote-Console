# 設計規格資料（跟隨 Autotest_tool）

本目錄的 `dut_power/*.json` 是從 **Autotest_tool**（GitHub `Jacky56-wei/Autotest_tool`）`origin/main` 的同名檔 **原樣複製**，格式與 `u2core.powerprofile` 相同：

```json
{ "note": "設計值，非正式規格", "rails": [{ "ch", "nom", "min", "max", "tol_pct", "unit" }] }
```

| 檔案 | 來源 |
|---|---|
| `dut_power/U2_12V_PowerIC_DesignSpec.json` | Autotest_tool `dut_power/`（EQ1 U.2 12 V Power IC 設計表） |
| `dut_power/M2_3V3_PowerIC_DesignSpec.json` | 同上（PJ2-GW3T84P + RT5147WSC-01 POR，VIN 3.3 V ±5 %） |
| `public_input.json` | 本工具另標的公開輸入預設（U.2 12V ±8%、M.2 3.3V ±5%），與設計表分開 |
| `timing_ref.json` | 從 `u2core.designspec` 的 soft-start／RSTn 延遲抄出，只記錄 |

判比邏輯：優先 `import u2core.powerprofile` / `u2core.dutspec` / `u2core.designspec.compare`（`U2_TOOL_DIR` 有的話）；沒有就用本套檔案與相同的 `rng`／`compare` 對照。

設計表數值是「設計值，非正式規格」。缺 min／max 的軌（例 U.2 PHYIO、VBUS、BUSPG、RSTn、IBUS）標 **待填**，不臆造。
