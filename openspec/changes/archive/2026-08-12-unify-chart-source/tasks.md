# Tasks — unify-chart-source

分支：`refactor/unify-chart-source`（自 master `4690804` 開出）。

## 1. 盤點與舊 change 收尾

- [x] 1.1 語意全庫搜殘餘 PPT 消費者——生產端僅 main.py 的 `resolve_web_asset`
      （保留項）；`resolve_ppt_asset`／`profile_manifest` 實查零消費者。
      測試基線 241 passed 全綠後動工
- [x] 1.2 `separate-web-and-ppt-chart-profiles` 作廢標頭＋移入
      `archive/2026-08-12-*`（全庫 strict 23 項綠）

## 2. TDD：引擎單一來源

- [x] 2.1 Red：`test_unify_chart_source.py` 真 Red 6 failed（sizing 綁定、
      scale 恆 1、單檔輸出、迴圈/manifest/resolver 退場、相容雙路徑）
- [x] 2.2 Green：chart_runner 綁定 PPT→WEB、chart_scale 恆 1.0（介面不變）、
      `_write_svg` 去中綴、單輪渲染、manifest 鏈移除 → 9 passed
- [x] 2.3 幾何測試逐支更新（每支帶契約註記）：font_target 改 15px 均一＋
      note=data、sizing_profile 綁 WEB、sections 檔案清單／四位數年份、
      sparse 1 列下限 0.28→0.26（量測後校準：max 高 +21.7%＞列高 +14.3%，
      機制零改動，已於回報揭露）；守死機制的 `test_chart_profiles`／
      `test_dual_profile_rendering` 隨機制刪除（新契約檔接守）
- [x] 2.4 Refactor：`active_sizing` 轉手層併回（chart_runner 直綁 WEB）；
      `chart_profiles` 縮編至 `resolve_web_asset` 單一職責（CC=A(3)、
      新增行 ruff 歸零）

## 3. 組合驗收

（deck intake 接軌原列本 change，2026-08-12 移至 `add-deck-delivery-line`
task 1.4——它本來就是 deck 流程第 1 步，且 skill 將遷產品 repo，一次到位。）

- [x] 3.1 strict 全庫 23 項綠；範圍回歸 232 passed（chart／profile／sizing／
      index／svg／version）
- [x] 3.2 實物（證據 `output/_verify/unify_chart_source/`）：
      走 `handle_report_generate` 生產路徑產 `report_trial_20260812_101344`
      （滑雪機，14 檔 SVG 單一來源、全 15px、DB 上傳 18 檔 vs 舊 34）；
      三時代版本 content API 相容驗證（新版 0 `.web`／雙 profile 版 14 個
      `.web` fallback 命中／前 web 版退原檔，asset 皆 200）；
      前端 10 檢視輪切零破圖零 `.web` 引用＋截圖；CLI 解讀一輪
      （RUN#345 succeeded，based_on_version 綁對、headline＋3 points）。
      ⚠ 揭露：產版未走佇列（Lightning 容器 worker 跑舊碼會搶工），
      以 stub context 直呼同一支 handler（引擎／分群載入／上傳皆生產程式）
- [x] 3.3 使用者接受後 archive；合 master 後 Lightning 重部署生效
      （使用者 2026-08-12 驗收通過，標的 report_trial_20260812_133901，
      含驗收期追加修正與檢視選單五變體、版本下拉）
