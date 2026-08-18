## Context

`derived_layer.company_aliases` 已以一列一個 alias 保存 WIPS code、中英文正式名、來源與 review status；`apply_confirmed_display_names` 是 confirmed 唯一寫入點，derived/report 已有 confirmed guard。`ai:company_zh_name` 能產 `ai_suggested` 中文名草稿，但只處理既有代碼組且前端入口已移除。公司集團建議已證明既有 Companion、受控白名單、WebSearch/WebFetch、人工確認與 SSE 模式可用，但公司 alias 與集團 membership 必須維持不同 mapping。

## Goals / Non-Goals

**Goals:**

- 對每個未歸戶變體提出可追溯的公司身分與中英文正規化建議。
- 納入有 WIPS code 但缺中文名者，允許有來源的市場慣用名或法人登記中文名。
- 允許具確切 owner／proprietor／董事證據的自然人，經人工確認後作為公司分析變體。
- AI 只產 review-only 建議；Backend 掌握候選、目標與所有 WIPS code。
- 沿用 queue、Companion、company_aliases、confirmed writer 與 SSE。

**Non-Goals:**

- 不讓 AI 產生或修改 WIPS code，不自動確認，不改 raw/core 原始字面。
- 不因 founder、CEO、員工、發明人或同名就把自然人歸入公司。
- 不宣稱自然人與法人法律上為同一主體；person mapping 只代表分析歸戶。
- 不新增 suggestion table、獨立 queue、獨立 Companion 或通用 entity-resolution framework。

## Architecture And Data Flow

```text
Browser manual trigger
  -> workflow_runs(ai:company_normalization_suggestion)
  -> existing Companion / ai_bridge
  -> Backend controlled payload
       candidates: opaque refs, raw variants, known identity state
       targets: opaque refs, confirmed names, private authoritative-code map
  -> CLI (WebSearch/WebFetch only)
  <- strict suggestions without writable WIPS-code fields
  -> validate refs, kind, names, evidence and person-role threshold
  -> persist company_aliases(ai_suggested) + metadata
  -> commit -> SSE companyAliases

Browser review
  -> select variants / change target / edit names / confirm
  -> transaction revalidation
  -> apply_confirmed_display_names
  -> enqueue refresh_derived
  -> companyAliases then browsePatents SSE refresh
```

跨行程唯一交換媒介是既有 PostgreSQL job queue 與結構化 payload/result。CLI 不取得 DB、repo、NAS、shell 或任意 MCP；SSE 只做 invalidation，前端重新查詢權威 API。

## Decisions

### 1. 單一廣義 job

新增 `ai:company_normalization_suggestion` 作為唯一可見入口，註冊到既有 `job_repository`、`ai_bridge`、`cli_gateway`。舊 `ai:company_zh_name` 保持相容但不恢復第二個前端入口。

### 2. AI 只使用 opaque reference

Backend 建立 `candidate_ref`、`target_ref`，並在 private reference map 保留權威 WIPS code。CLI schema 不含 `wips_code`、`company_code`、`code` 或 override。未知、過期、非 confirmed reference 及任何額外 code 欄位皆拒絕；理由文字中的代碼永不解析成資料。

### 3. 四種 suggestion kind

- `map_existing`：公司字面加入白名單既有公司，code 與 canonical names 由 Backend 帶入。
- `update_names`：補／修中英文名稱，不改 code。
- `create_temp`：無權威 code 時由 Backend 確定性產生 `TEMP:*`。
- `person_affiliation`：自然人分析歸戶至既有公司，只接受 owner、proprietor 或董事。

### 4. 法人中文名是可辨識 fallback

有 code 但缺中文名者納入候選。AI 必須帶 `zh_name_basis=market_common_name|registered_legal_name`。法人登記名可在無市場慣用名時使用，但 UI 必須明示；單純翻譯、音譯或模型記憶不得形成可確認建議，既有非空 confirmed 中文名不得自動覆蓋。

### 5. 自然人變體採高證據與警示

`person_affiliation` 至少需要人物同一性、目標公司、owner/proprietor/director 角色、來源 URL、來源日期或有效期。只有 founder、CEO、經理、員工、發明人、聯絡人或同名均不足。若無政府／公司登記等一手來源，至少需要兩個獨立且一致的可信來源。確認前顯示「該個人名下相關專利將納入此公司統計」；metadata 永久保留角色與證據。

### 6. suggestion 與正式 mapping 同表不同態

每個 alias 使用 `review_status='ai_suggested'`、`source_type='ai_suggested'`；`wips_metadata_json` 保存 suggestion id/kind、target ref、confidence、reason、evidence、warnings、name basis、person role、model/prompt version。多變體以 suggestion id 關聯多列。正式 projection 只讀 confirmed。

### 7. canonical names 屬於公司身分

原始 alias 各自保留字面；中英文 canonical names 由同公司全部 confirmed 變體共用。使用者改名時由 confirmed writer 在同一交易 re-canonicalize，不讓每個 alias 形成不同正式名。

### 8. 原子確認與 freshness guard

確認 request 攜帶 suggestion version。交易內驗候選仍待審、target 仍 confirmed、alias 未歸到其他公司、canonical names 無衝突、person evidence 完整。任一 selected item 衝突時整批失敗。略過保留草稿；相同 freshness identity 不重送 AI。

### 9. SSE 單一路徑

新 job 成功與 suggestion/confirm commit invalidates `companyAliases`；`refresh_derived` 成功沿用 `browsePatents`。前端共用既有 EventSource、debounce、in-flight、重連與 30 秒輪詢。

## Program Locations

- API：`backend/app/api/company_aliases.py`
- 候選、草稿、confirmed writer：`backend/app/derived/company_alias_importer.py`
- Runner：`backend/app/worker/ai_company_normalization_suggestion_runner.py`
- CLI：`backend/app/worker/cli_gateway.py`、`ai_payload_file.py`
- Dispatch：`backend/app/db/job_repository.py`、`backend/app/worker/ai_bridge.py`
- Guard：`backend/app/derived/refresh_report_patent_base.py`
- Frontend/SSE：`backend/app/static/index.html`

## Output Contract

Backend-to-CLI 僅提供 opaque refs、raw aliases、known identity state、target 中英文名稱及查證要求；WIPS code 只存在 Backend private map。

CLI suggestion 至少包含 `suggestion_kind`、`candidate_refs`、可選 `target_ref`、`suggested_zh_name`、`suggested_normalized_name`、`zh_name_basis`、`confidence`、`reason`、`evidence[]`、`warnings[]`。`person_affiliation` 另須 `person_identity_evidence[]`、`relationship_role`、`relationship_evidence[]`；role 白名單只有 owner、proprietor、director。

Parser 嚴格拒絕未知欄位，尤其 code/override；只接受 HTTPS evidence，文字有長度限制。Review API 回傳 Backend company options，前端不維護第二份白名單。

## Test Mapping

- CMP-003：pending isolation、skip retention、invalid output atomicity。
- CMP-010：no-code schema、opaque whitelist、Backend TEMP、promote compatibility。
- CMP-011：per-variant、多選、target/name edit、raw preservation、conflict rollback。
- CMP-012：有 code 缺中文名、name basis、來源門檻、不得硬翻。
- CMP-013：person identity、owner/proprietor/director、雙來源 fallback、警示與 metadata。
- CMP-014：manual-only、collapsed/hidden UI、readable evidence、SSE/reconnect。

## Risks / Trade-offs

- [自然人與法人非同一法律主體] -> kind、警示、證據與 raw 永久保留，只能人工確認分析歸戶。
- [董事任期會變] -> 保存來源日期／有效期；過期標 warning，不自動改 confirmed mapping。
- [法人登記名與市場名不同] -> name basis 明示，使用者可編輯。
- [同表草稿誤進報表] -> confirmed guard 全庫 contract scan。
- [模型夾帶 code] -> strict schema + private map，自由文字不解析。
- [審核期間資料改變] -> freshness token + transaction revalidation。

## Migration / Activation / Rollback

1. 先證明既有欄位、CHECK、JSONB 足夠；預期無 migration，不符先回寫規格。
2. 依 TDD 完成 schema guard、persistence、review API、UI、SSE。
3. 部署 backend、worker/Companion contract、frontend；不重匯、不重跑 embedding/分群。
4. Rollback 停用 UI 與新 job；ai_suggested 保留且不影響正式 projection，confirmed 不自動刪除。

## Open Questions

無。
