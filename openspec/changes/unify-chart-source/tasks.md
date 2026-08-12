# Tasks — unify-chart-source

分支：`refactor/unify-chart-source`（自 master `4690804` 開出）。

## 1. 盤點與舊 change 收尾

- [ ] 1.1 語意全庫搜殘餘 PPT 消費者（`profiles`／`.web`／`chart_scale`／
      144dpi／PPT 尺寸常數字面），確認退場清單無漏
- [ ] 1.2 `separate-web-and-ppt-chart-profiles` 加作廢標頭（註明由本 change 取代、
      web 段成果去向）移入 `archive/2026-08-12-*`

## 2. TDD：引擎單一來源

- [ ] 2.1 Red：新契約測試——run_dir 每張圖恰一檔（無 `.web.svg`）、不產
      `profile_manifest.json`、原檔名內容為 WEB 尺寸（字級 15px 斷言）、
      `resolve_web_asset` 舊版本（雙檔）／新版本（單檔）雙路徑
- [ ] 2.2 Green：翻轉渲染——單輪 WEB sizing 寫原檔名；拔
      `render_sections_all_profiles` 第二輪、`build_profile_manifest`＋上傳項、
      `resolve_ppt_asset`、profile 中綴邏輯；`chart_sizing.PPT` 留定義加退役註記
- [ ] 2.3 幾何預設值測試逐支更新（預期紅一批，每支註記契約變更原因與日期）
- [ ] 2.4 Refactor：`chart_profiles` 縮編後檢查是否已成淺模組（只剩轉手就併回
      `chart_sizing`／`chart_runner`，刪除測試原則）

## 3. 組合驗收

（deck intake 接軌原列本 change，2026-08-12 移至 `add-deck-delivery-line`
task 1.4——它本來就是 deck 流程第 1 步，且 skill 將遷產品 repo，一次到位。）

- [ ] 3.1 OpenSpec strict、目標測試、範圍回歸（chart／report／sizing 關鍵字）
- [ ] 3.2 實物：產一版新報表→網頁逐卡看（新舊版本各開一份對照）→CLI 解讀一輪
- [ ] 3.3 使用者接受後 archive；合 master 後 Lightning 重部署生效
