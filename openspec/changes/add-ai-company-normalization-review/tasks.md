## 1. 開工閘門與基線

- [x] 1.1 記錄 branch、HEAD、dirty files、tracking；未碰 `feat/local-gpu-compose`。Alembic current/head 與測試 DB identity 未做，保留到 7.2 正式 DB 驗收。
- [x] 1.2 盤點 `company_aliases` schema/CHECK/index/JSONB、confirmed-only reads、舊 `ai:company_zh_name` 與 SSE mapping；實作沿用既有欄位，未新增 table。
- [x] 1.3 跑 AI bridge、company alias/maintenance、derived guard、SSE、frontend 最小基線並保存既有失敗。

## 2. Slice A：受控候選與禁止 WIPS code（CMP-010）

- [x] 2.1 Red：測 opaque refs、四種 kind、名稱／證據上限與未知欄位；任何 WIPS/company/code override 必須失敗。
- [x] 2.2 Red：測未知 candidate、未知 target、額外 code 欄位、既有權威 code 不得由 AI 改寫。
- [x] 2.3 Green：實作 Backend payload builder、strict parser、private reference resolver；CLI 看不到可回填 WIPS code。
- [x] 2.4 Green：以 WebSearch/WebFetch-only 接既有 `cli_gateway`、`job_repository`、`ai_bridge`；一般 worker 不 claim，不新增 queue/Companion/shell/file/MCP。
- [x] 2.5 Refactor/Regression：共用 JSON extraction 與 timeout；回歸其他 AI job 權限與 dispatch。payload file 未新增，維持 prompt 直送。

## 3. Slice B：公司與法人中文名（CMP-003、CMP-011、CMP-012）

- [x] 3.1 Red：候選涵蓋未 confirmed 變體與有效草稿去重；workspace scope 維持全庫既有治理口徑。
- [x] 3.2 Red：測 market common、registered legal name、來源不足、硬翻／音譯拒絕、既有非空 confirmed 中文名不得自動覆寫。
- [x] 3.3 Red：測 ai_suggested 一建議多變體、kind/name basis/prompt/evidence、非法輸出隔離。
- [x] 3.4 Green：沿用 company_aliases review-only rows；不新增 table、不複製 confirmed writer、不改 raw/core。
- [x] 3.5 Red/Green：confirmed guard 以現有 regression 掃描；待審列不進 confirmed projection。
- [x] 3.6 Regression：舊中文名 job、alias importer、display priority、report refresh 相容。

## 4. Slice C：自然人公司分析變體（CMP-013）

- [x] 4.1 Red：合法角色只含 owner/proprietor/director；founder、CEO、經理、員工、發明人、聯絡人、同名或人物無法辨識必須拒絕。
- [x] 4.2 Red：person evidence 涵蓋人物同一性、公司、角色與 HTTPS 來源；日期／有效期與雙來源 fallback 保留到真 CLI 驗收。
- [x] 4.3 Green：parser/persistence 標為 `person_affiliation`，保存角色與證據，不宣稱法律主體相同，不退化為名稱相似 mapping。
- [x] 4.4 Red/Green：確認前統計警示、明確確認、確認後分析歸戶與 raw/source 追溯；略過不影響統計。
- [x] 4.5 Regression：同名、跨公司董事、任期過期、新證據重跑不得自動改 confirmed mapping。

## 5. Slice D：人工多選、修改與原子確認（CMP-011）

- [x] 5.1 Red：review API 涵蓋 Backend options、單筆／多選、略過、改中英文名、改公司、create_temp、person warning、錯誤。
- [x] 5.2 Red：DB guard：map_existing 只用 target code；known code 保持；create_temp 只由 Backend 產 `TEMP:*`。
- [x] 5.3 Green：驗 target、alias/name 與 person evidence，委派 `apply_confirmed_display_names`；只清成功 drafts。
- [x] 5.4 Red/Green：target 被刪／改名會拒絕；兩使用者併發與 alias 已歸戶整批 rollback 留到 DB 整合驗收。
- [x] 5.5 Red/Green：確認後只 enqueue 一個 refresh_derived；raw/core 不變，同公司 variants 共用 canonical names。
- [x] 5.6 Regression：新增變體、改名、刪組、單筆移除、TEMP promote、company group 維持可用。

## 6. Slice E：集中前端與 SSE（CMP-014）

- [x] 6.1 Red：單一手動鈕、running 防重、預設收合、無建議隱藏、有建議筆數，不恢復第二個中文名 AI 入口。
- [x] 6.2 Green：顯示 raw variant、kind、target、中英文名、name basis、confidence、reason、warning、固定文字 `來源`；person 顯示角色、證據、統計警示；raw JSON 不顯示且 escape。
- [x] 6.3 Red/Green：多選、改 target、改名、確認、略過；不跳頁、不重置展開或未提交選擇。
- [x] 6.4 Red/Green：新 job -> companyAliases、refresh_derived -> browsePatents，沿用 debounce/in-flight/重連/30 秒輪詢。
- [x] 6.5 Regression：company frontend、company group frontend、SSE refresh/connection 無重複 listener 或刷新風暴。

## 7. 組合驗收與交付閘門

- [x] 7.1 跑 OpenSpec strict、新增測試、受影響 company/AI bridge/derived/SSE/frontend 回歸；`scripts/verify_module.py` 未跑（本 worktree uv 需下載依賴且網路受限，改以既有 venv 跑目標測試）。
- [x] 7.2 隔離 PostgreSQL 驗 schema、suggestion、confirmed guard、多選、rollback、TEMP、person metadata、derived refresh；不得清 Supabase 現有資料。
      → 2026-08-21 拋棄式本機 DB（`alembic upgrade head` exit 0）＋ `RUN_DB_TESTS=1`，
      跑 12 個公司正規化／集團／中文名相關測試檔：**129 passed、7 failed**。
      驗畢 `DROP DATABASE`，殘留檢查為「無」；**完全未碰 Supabase**。

      ⚠ 那 7 紅**不屬本 change**：全部是
      `psycopg.errors.UndefinedColumn: column "公司名稱" of relation "company_aliases" does not exist`
      ——該欄在 migration `0041`（2026-07-28）就移除了。已查證：
      `backend/` 僅剩兩處**註解**提到它（非 SQL），且專門守門的
      `test_company_legacy_column_removed.py` **9 passed**。
      ⇒ **測試過時，不是程式壞掉**，歸 `known-issues-optimization.md` 的 **D-6 第三類**。
      🔴 這批測試因 D-4 的 env 污染長期靜默 skip，從 2026-07-28 起就沒真的跑過。
- [ ] 7.3 跑真 Companion/CLI job，驗 heartbeat、Search/Fetch-only、法人中文名與 person/director evidence、非法 code/target 拒絕；保存 job/log/result。
- [ ] 7.4 Playwright 驗桌面與行動：啟動、running、收合／隱藏、法人名依據、person 警示、證據、多選、修改、略過、確認、衝突、SSE、不跳頁、無重疊。
- [ ] 7.5 比較建議前／待審／確認後的 aliases、raw/core、report base、公司／集團統計、cluster assignments；只有人工確認後公司 projection 可變。
- [ ] 7.6 列出 Lightning/Supabase 部署、Companion 重啟、rollback；未部署／未做正式庫 smoke 必須標明。
- [ ] 7.7 工作樹只含本 change 後提交、推分支、建 PR；遠端 checks 不可用時記錄原因，以本機組合驗收證據與使用者允許作合併閘門。
