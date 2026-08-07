"""四區前端骨架靜態契約測試（不需 DB）。

只驗 GET / 回傳的 index.html 是否含四區骨架、workspace 切換、各導覽區塊、
主題/暗色主題、進度元件與 AI 助手所需的 DOM 掛點與 API 路徑字串。純靜態斷言，
不驅動瀏覽器、不連 DB；動態行為由各 API 契約測試（workspaces/events/jobs）覆蓋。

以「頁面必含哪些 id／字串」為契約：前端 JS 靠這些 id 掛資料，改動即會讓對應區塊
失效，因此以 id 存在性作為骨架回歸護欄。
"""
from __future__ import annotations

import re
import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


class FrontendSkeletonTests(unittest.TestCase):
    """GET / 回傳單一 HTML，含四區骨架與必要掛點。"""

    @classmethod
    def setUpClass(cls):
        resp = client.get("/")
        cls.resp = resp
        cls.html = resp.text

    def js_function(self, name: str) -> str:
        """用括號深度取出真實 JS function body，避免只被註解或 docstring 餵飽。"""
        idx = self.html.find(f"function {name}(")
        if idx < 0:
            idx = self.html.find(f"async function {name}(")
        self.assertGreaterEqual(idx, 0, f"找不到 {name}() 定義")
        start = self.html.find("{", idx)
        self.assertGreaterEqual(start, 0, f"找不到 {name}() 起始大括號")
        depth = 0
        for pos in range(start, len(self.html)):
            char = self.html[pos]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return self.html[idx:pos + 1]
        self.fail(f"{name}() 大括號未閉合")

    def test_root_serves_html_ok(self):
        """GET / 回 200 且是完整 HTML 文件。"""
        self.assertEqual(self.resp.status_code, 200)
        self.assertIn("text/html", self.resp.headers.get("content-type", ""))
        self.assertIn("<!DOCTYPE html>", self.html)
        # 繁中：lang 與標題
        self.assertIn('lang="zh-TW"', self.html)

    def test_four_regions_present(self):
        """四區骨架：頂列 workspace 切換、左導覽、中主內容、右 AI 助手。"""
        for region_id in (
            "topbar",           # 頂列
            "workspace-select",  # workspace 切換下拉
            "nav-panel",        # 左導覽區
            "main-panel",       # 中主內容區
            "ai-panel",         # 右 AI 助手區
        ):
            with self.subTest(region_id=region_id):
                self.assertRegex(self.html, rf'id="{region_id}"')

    def test_nav_items_present(self):
        """左導覽（workspace 內功能）：分類區 / 報表種類 / 匯出報告 / 系統狀態。

        （分群任務已移除：分群改為匯入後自動背景觸發，使用者不需手動點。）
        （專利總覽於 2026-07-24 移至頂列：它是跨 workspace 的全庫視角，與左導覽的
        workspace 內功能不同層——見 test_overview_moved_to_topbar_not_nav。）
        （🔴 案件比對於 2026-08-03 使用者定案**先移除入口**——見
        test_comparison_entry_removed。後端 API 與前端渲染程式保留，只收起入口。）
        """
        for nav_key in (
            "topics",      # 分類區
            "reports",     # 報表種類勾選
            "export",      # 匯出報告
            "status",      # 系統狀態
        ):
            with self.subTest(nav_key=nav_key):
                self.assertRegex(self.html, rf'data-nav="{nav_key}"')

    def test_comparison_entry_removed(self):
        """🔴 2026-08-03 使用者：「案件比對區塊先移除掉」。

        ⚠ 只收**入口**：`renderComparison()` 與後端 `/api/comparison` 都保留，
        要恢復時把導覽鈕加回來即可。刪掉整條線的話，之後要用得重寫。
        """
        self.assertNotRegex(self.html, r'data-nav="comparison"',
                            "案件比對導覽鈕仍在")

    def test_theme_toggle_and_dark_styles(self):
        """亮暗雙主題：切換鈕與 dark 主題樣式都在。"""
        self.assertRegex(self.html, r'id="theme-toggle"')
        # data-theme="dark" 選擇器存在（暗色覆寫）
        self.assertIn('data-theme="dark"', self.html)

    def test_workspace_api_wired(self):
        """workspace 切換接 GET /workspaces（API 前綴常數 + 相對路徑組合）。"""
        # 前端以 `const API = '/api/v1'` + `API + '/workspaces'` 組路徑，
        # 故驗前綴常數與呼叫片段各自存在，而非字面全串。
        self.assertRegex(self.html, r"""API\s*=\s*['"]/api/v1['"]""")
        self.assertIn("/workspaces", self.html)

    def test_progress_component_present(self):
        """統一進度元件：進度條 + 階段文字 + 已耗時。"""
        self.assertIn("progress-bar-fill", self.html)
        self.assertIn("progress_percent", self.html)
        self.assertIn("current_stage", self.html)

    def test_ai_panel_channel_wired(self):
        """右欄 AI 助手接 AI 任務入口 /ai-tasks / tasks / events(SSE)。"""
        self.assertIn("/ai-tasks", self.html)
        self.assertIn("/ai-tasks/", self.html)
        self.assertIn("/tasks", self.html)
        self.assertIn("/events", self.html)
        self.assertIn("ai:narrative", self.html)
        # 改名澄清：不得再出現舊的 companion 端點路徑。
        self.assertNotIn("/companion/", self.html)

    def test_ai_token_ui_removed(self):
        """AI 任務金鑰 UI 已移除（2026-07-26 定案）。

        端點改為 opt-in 認證（未設 PATENT_API_TOKEN 即不驗證），使用者不需填金鑰。
        原測試守的是「有輸入框＋存 localStorage＋帶 Bearer」的舊契約，已隨定案作廢。
        """
        for needle in ("ai-token", "saveAiToken", "getAiToken", "AI_TOKEN_KEY"):
            with self.subTest(needle=needle):
                self.assertTrue(needle not in self.html, f"金鑰相關程式碼未清除：{needle}")

    def test_ai_auth_header_injection_point_kept(self):
        """保留 aiAuthHeaders 單一注入點：要重啟保護時只需改這一處。"""
        self.assertIn("aiAuthHeaders", self.html)

    def test_ai_send_button_kept(self):
        """送出功能保留（使用者要求）：ai:narrative 任務仍可從前端建立。

        ⚠ 2026-07-30 入口換位，原斷言的 `btn-ai-send`／`sendAiRequest`／`ai-input`
        （AI 助手側欄）已移除——使用者定案「只拿掉輸入框，解讀照樣自動產」，
        AI 助手欄「單純任務進度表就好」。

        本測試的**用意仍然成立**（前端要建得了 ai:narrative），故改驗新入口：
        - `btn-run-all-narrative`：一次跑全部（初次用）
        - `btn-run-narrative`：各報表獨立重產（可帶該張的 prompt）
        兩者走同一支 runNarrative，差別只在有沒有帶 report_keys。
        """
        for needle in ("btn-run-all-narrative", "btn-run-narrative",
                       "runNarrative", "narrative-prompt"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_ai_token_not_hardcoded_in_page(self):
        """token 不得寫死在公開 HTML（本頁對所有人可見）。"""
        # 頁面內不得出現任何寫死的 Bearer 值（Bearer 後只接變數串接）。
        self.assertNotRegex(self.html, r"Bearer\s+[A-Za-z0-9_\-]{8,}")

    def test_comparison_has_create_form(self):
        """案件比對的建立表單仍在（入口雖已收起，實作不得被刪）。

        ⚠ 2026-08-03 移除的是導覽入口，不是功能本身——這支測試因此仍然有效，
        它守的正是「先移除」的『先』：之後要恢復時東西還在。
        """
        for needle in (
            "btn-create-comparison",
            "comp-case-title",
            "comp-case-text",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_comparison_has_target_form(self):
        """案件比對有被比對標的表單（類型、標題、說明、simulated、儲存按鈕）。"""
        for needle in (
            "comp-target-type",
            "comp-target-title",
            "comp-target-description",
            "comp-target-simulated",
            "btn-save-target",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_comparison_has_understanding_form(self):
        """案件比對有 AI 理解稿表單（特徵、假設、來源、儲存按鈕）。"""
        for needle in (
            "comp-understanding-features",
            "comp-understanding-assumptions",
            "comp-understanding-source",
            "btn-save-understanding",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_comparison_has_approve_section(self):
        """案件比對有核准理解稿按鈕與核准者輸入。"""
        for needle in (
            "comp-approved-by",
            "btn-approve-understanding",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_comparison_has_source_labels(self):
        """比對頁面區分「被比對來源」（patent/claim）與「比對來源」（product/target）。"""
        for needle in ("被比對來源", "比對來源"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_comparison_can_load_existing_state(self):
        """比對頁面可依 job_id 載入既有資料並回填 element_analysis。"""
        for needle in ("loadComparisonState", "comp-load-job-id", "fillComparisonFromResponse"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_comparison_has_element_analysis_form(self):
        """案件比對有逐要素比對表單（JSON 輸入區、儲存按鈕、版本顯示）。"""
        for needle in (
            "comp-element-analysis-json",
            "btn-save-element-analysis",
            "comp-version-element-analysis",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_comparison_has_display_section(self):
        """案件比對有顯示區（job_id、status、stage、progress、版本號）。"""
        for needle in (
            "comp-job-info",
            "comp-job-id",
            "comp-job-status",
            "comp-current-stage",
            "comp-progress-percent",
            "comp-version-target",
            "comp-version-understanding",
            "comp-version-approval",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_topics_region_wired_to_topic_patents_endpoint(self):
        """分類區接 topic patents 端點（.../topics/{key}/patents），非用 label 當 keyword 搜。"""
        # 端點路徑片段：前端以 topic_key 組 `/topics/<key>/patents`。
        self.assertRegex(self.html, r"/topics/[^'\"`]*?/patents")
        # 移除舊錯誤邏輯：不得再用「topic label 當 patents keyword」的注解或呼叫。
        self.assertNotIn("用其 label 作為 keyword", self.html)

    def test_overview_has_topic_column(self):
        """專利總覽表格含「所屬主題」欄，顯示每筆專利歸屬主題。"""
        self.assertIn("所屬主題", self.html)
        # 每筆專利以 topic_label 顯示歸屬（未分類另標示）。
        self.assertIn("topic_label", self.html)

    def test_task_list_filters_succeeded(self):
        """任務區顯示邏輯：succeeded 過濾不顯示（保留 running/queued/failed）。"""
        # 過濾標記：以隱藏狀態集合 + 過濾函式作為契約，改動即會讓斷言失效。
        self.assertIn("HIDDEN_TASK_STATUSES", self.html)
        self.assertRegex(self.html, r"HIDDEN_TASK_STATUSES\s*=\s*new Set\(\['succeeded'\]\)")
        self.assertIn("isHiddenTask", self.html)

    def test_task_cards_clickable_for_detail(self):
        """failed／running 任務卡可點開詳情：掛 toggleTaskDetail 且 failed 讀 error_message。"""
        self.assertIn("toggleTaskDetail", self.html)
        self.assertIn("task-clickable", self.html)
        # failed 詳情讀 job 的 error_message 欄；running/queued 詳情讀 current_stage。
        self.assertIn("error_message", self.html)
        self.assertIn("current_stage", self.html)

    def test_reports_exclude_removed_catalog_entries(self):
        """RPT-011（2026-08-06）反轉原契約：三張已刪報表不得再出現在前端清單，
        留著任何一張都會讓「全選」整批 400（07-29 受讓人排名同型事故）。"""
        for name in ("family_quality_detail", "owner_ranking", "owner_year_matrix"):
            self.assertNotIn(f"'{name}'", self.html)

    def test_reports_default_all_checked(self):
        """報表種類預設全部勾選（首次進報表區把全部填入選取集合）。"""
        self.assertIn("ensureReportSelectionDefault", self.html)
        # 預設全勾語意：把全部 REPORT_TYPES 加入 reportSelection。
        self.assertRegex(self.html, r"state\.reportSelection\.add")

    def test_topbar_has_import_button(self):
        """頂列（workspace-select 旁）有「匯入」鈕掛點，點開匯入面板。"""
        # 匯入鈕以 id 掛 onclick，開啟匯入對話框；文案含「匯入」。
        self.assertIn("btn-open-import", self.html)
        self.assertIn("openImportDialog", self.html)
        self.assertIn("匯入", self.html)

    def test_import_panel_has_file_input(self):
        """匯入面板含檔案輸入，accept 對齊後端白名單副檔名（.xlsx/.csv/.txt/.xml）。"""
        self.assertIn("import-file", self.html)
        self.assertRegex(self.html, r'type="file"')
        for ext in (".xlsx", ".csv", ".txt", ".xml"):
            with self.subTest(ext=ext):
                self.assertIn(ext, self.html)

    def test_import_panel_workspace_choice_exclusive(self):
        """workspace 二選一：新建（輸入名稱）或加入既有（下拉），互斥且接真實 workspaces API。"""
        for needle in (
            "import-ws-mode",          # 二選一模式切換（new / existing）
            "import-new-ws-name",      # 新建 workspace 名稱輸入
            "import-existing-ws",      # 既有 workspace 下拉
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # 既有 workspace 下拉接真實 API（沿用 loadWorkspaces / /workspaces），非寫死清單。
        self.assertIn("/workspaces", self.html)

    def test_import_panel_has_purpose_options(self):
        """用途下拉：目前只有 general。

        🔴 2026-08-03 使用者定案：case_comparison 隨案件比對入口一併收起——
        選得到卻沒地方比對是更糟的狀態。
        ⚠ 後端 `IMPORT_PURPOSES` 仍接受 case_comparison，既有資料的標記不受影響；
        這裡收的只是**前端選項**。
        ⚠ 用 `<option value=...>` 精準比對，不用整份 HTML 搜字串——
        後者會被註解裡的說明文字騙過去（本次實際發生）。
        """
        self.assertIn("import-purpose", self.html)
        options = re.findall(r'<option value="([^"]+)"', self.html)
        self.assertIn("general", options)
        self.assertNotIn("case_comparison", options,
                         "案件比對用途仍在下拉選項中")

    def test_import_calls_imports_endpoint(self):
        """開始匯入以 raw body 呼叫 POST /api/v1/imports（帶 filename/purpose/workspace 參數）。"""
        self.assertIn("startImport", self.html)
        # 端點路徑片段（前端以 API 前綴 + '/imports' 組路徑）與 query 參數名。
        self.assertIn("/imports", self.html)
        for qs in ("filename=", "purpose="):
            with self.subTest(qs=qs):
                self.assertIn(qs, self.html)
        # workspace 二選一參數名各自存在（互斥擇一帶入）。
        self.assertIn("workspace_id=", self.html)
        self.assertIn("new_workspace_name=", self.html)
        # body 直送檔案（raw body 串流），非 multipart 打包。
        self.assertIn("pollImportJob", self.html)

    def test_import_completion_reads_job_result_fields(self):
        """完成顯示 job result 的匯入統計欄（inserted/matched_existing/updated/patent_ids）。"""
        for field in ("inserted", "matched_existing", "updated", "patent_ids"):
            with self.subTest(field=field):
                self.assertIn(field, self.html)

    # ── A. 匯入單一動作鈕（無二次確認 + 送出後結果卡 + 前往該 workspace） ──

    def test_import_modal_is_single_action_no_confirm_step(self):
        """匯入 Modal 只留單一動作鈕（decisions.md 2026-07-23「匯入 Modal 只留單一動作鈕」）：
        移除兩段式的「確認匯入／返回修改」與送出前確認摘要，選檔後按「開始匯入」直接上傳。"""
        # 兩段式殘留一律不得再出現。
        for gone in ("btn-confirm-import", "btn-back-import", "確認匯入", "import-confirm"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.html)
        # 只留單一動作鈕，直接呼叫實際上傳邏輯 startImport。
        self.assertIn("btn-start-import", self.html)
        self.assertIn('id="btn-start-import" onclick="startImport()"', self.html)
        # 送出後 disable 防連點並改字「匯入中…」。
        self.assertIn("匯入中…", self.html)

    def test_import_result_card_shows_target_workspace_and_shortcut(self):
        """匯入結果卡提示已加入哪個 workspace，並提供前往該 workspace 專利總覽的快捷。"""
        for needle in (
            "btn-goto-imported-ws",   # 快捷鈕掛點
            "gotoImportedWorkspace",  # 切 workspace + navTo('patents')
            "已加入 workspace",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    # ── B. 分群任務導覽項已移除（分群改為匯入後自動背景觸發） ──

    def test_clustering_nav_item_removed(self):
        """左導覽不得再有「分群任務」項：分群改為匯入後自動觸發，使用者不需手動點。"""
        self.assertNotRegex(self.html, r'data-nav="clustering"')
        self.assertNotIn("分群任務", self.html)
        # 手動觸發分群的入口一併移除（不再由使用者按鈕建 calibrate／finalize 工作）。
        for needle in ("btn-run-calibrate", "btn-run-incremental", "btn-finalize-clustering"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, self.html)

    # ── B2. 分類區：技術／功效兩分頁 ──

    def test_topics_has_technical_and_effect_tabs(self):
        """分類區以 tab 切換技術／功效兩通道，兩通道值都掛在頁面上。"""
        for needle in (
            "topic-tabs",              # tab 容器掛點
            "switchTopicSource",       # 切換通道
            "wips_independent_claims",  # 技術通道
            "effect_summary",           # 功效通道
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_topic_source_fields_not_hardcoded_per_site(self):
        """兩通道集中在單一常數（SOURCE_FIELDS），分類區由它產生 tab，不在多處散寫。"""
        self.assertIn("SOURCE_FIELDS", self.html)
        # tab 由常數 map 產生（非逐個寫死 <button> 文字）。
        self.assertRegex(self.html, r"SOURCE_FIELDS\.map")
        # 通道值取自 state（可切換），不再是固定的單一常數。
        self.assertIn("state.topicSourceField", self.html)

    # ── B3. 分類區版式：標籤在上、專利清單緊接其下 ──

    def test_topics_layout_tags_above_patents(self):
        """分類區版式：標籤 → 概覽 → 進階操作（收合）→ 專利清單。

        2026-07-27 使用者定案：進階操作改放專利清單**之上**（原本在其下方），
        免得每次都要捲過整張表才找得到；但維持 <details> 收合，使用者要用才點開，
        所以不會擋住標籤與專利。
        """
        tags_at = self.html.index('id="topic-tags"')
        patents_at = self.html.index('id="topic-patents"')
        self.assertLess(tags_at, patents_at, "主題標籤必須在專利清單之上")
        adv_at = self.html.index('id="topic-advanced"')
        self.assertLess(adv_at, patents_at, "進階操作應在專利清單之上（2026-07-27 定案）")
        # 仍必須是收合的 <details>，不得展開佔版面
        self.assertIn('<details class="topic-advanced"', self.html)

    def test_topics_empty_state_message(self):
        """workspace 無分群結果時顯示明確中文提示（不是空白、不讓使用者點到 404）。"""
        self.assertIn("尚未分群", self.html)
        # 2026-07-26 定案分群改手動觸發，不再「匯入後自動分群」；文案同步更正。
        self.assertIn("請按上方「分類」開始分群", self.html)
        # 錯誤不吞：載入失敗要顯示可讀訊息（含狀態碼／訊息）。
        self.assertIn("topicLoadErrorHtml", self.html)

    def test_topic_patents_error_readable(self):
        """點標籤後 404／空結果要有可讀中文訊息，不吞錯。"""
        self.assertIn("找不到此主題", self.html)
        # fetch 失敗訊息帶 HTTP 狀態，供使用者/開發者判讀。
        self.assertRegex(self.html, r"HTTP\s*'\s*\+")

    # ── B4. 專利總覽跨所有 workspace ──

    def test_overview_lists_all_patents_across_workspaces(self):
        """全庫視角接全庫專利端點（GET /patents，不分 workspace）。

        🔴 2026-08-07 契約更新：專利總覽頁已刪（與瀏覽專利選全庫重複，
        同一概念兩處落點）——全庫清單由 loadBrowsePatents 承接，端點不變。"""
        self.assertIn("loadBrowsePatents", self.html)
        self.assertNotIn("loadAllPatents", self.html)
        # 端點：API 前綴 + '/patents'（全庫清單），帶分頁參數。
        self.assertRegex(self.html, r"""API\s*\+\s*['"]/patents\?""")
        for qs in ("limit=", "offset="):
            with self.subTest(qs=qs):
                self.assertIn(qs, self.html)

    def test_overview_shows_workspace_membership(self):
        """總覽每筆專利標示所屬 workspace（可多個）。"""
        self.assertIn("所屬 Workspace", self.html)
        self.assertIn("workspaces", self.html)
        self.assertIn("workspacesCell", self.html)

    def test_overview_paginated_not_full_load(self):
        """全庫專利分頁載入，不一次撈全部。

        🔴 2026-08-07：總覽刪除後分頁器＝瀏覽專利的 pageBrowse（原 pagePatents 死碼一併清）。"""
        self.assertIn("PATENTS_PAGE_SIZE", self.html)
        self.assertIn("pageBrowse", self.html)
        self.assertNotIn("pagePatents", self.html)

    # ── B5. 專利顯示欄位（2026-07-23 定案）＋版面歸屬（2026-07-24） ──

    def test_patent_columns_single_source_of_truth(self):
        """顯示欄位以單一定義驅動表頭與資料列，不散落硬編。

        使用者紅線「簡單≠寫死」：欄位清單只有一份 PATENT_COLUMNS，表頭與資料列都由它
        map 產生；後端增減欄位時前端只改這一處。
        """
        self.assertIn("PATENT_COLUMNS", self.html)
        # 表頭與資料列都必須由同一份定義 map 出來（非逐欄寫死 <th>／<td>）。
        self.assertRegex(self.html, r"PATENT_COLUMNS\s*\n?\s*\.?\s*filter|PATENT_COLUMNS\.map")
        # 欄位總數不得寫死（26／27 皆為規格沿革數字，非程式常數）。
        self.assertNotRegex(self.html, r"(?:26|27)\s*(?:欄位|個欄|columns)")

    def test_patent_columns_cover_spec_order(self):
        """欄位定義涵蓋規格全部顯示欄，且照使用者指定順序排列。

        順序＝使用者原始指定（主附圖→…→文圖像文件(PDF)連結），分類標籤依 2026-07-24
        定案拆成技術分類／功效分類兩欄置於第 5、6 位。
        """
        m = re.search(r"const PATENT_COLUMNS\s*=\s*\[(.*?)\n\];", self.html, re.S)
        self.assertIsNotNone(m, "找不到 PATENT_COLUMNS 定義")
        block = m.group(1)
        expected_order = [
            "主附圖", "申請國家", "專利種類", "專利狀態",  # 2026-08-07 類型→種類（P 蓋發明與設計，原欄收掉）
            "技術分類", "功效分類",
            "文獻備註", "申請人", "標題", "標題(原文)", "摘要", "摘要(原文)",
            "申請號", "申請日", "申請年", "未審查的公開號", "未審查的公開日",
            "授權公告號", "授權公告日", "授權公告年", "發明人", "優先權號", "優先權國家", "優先權日",
            "最近專利權人", "Orig. IPC", "詳細查看連結", "文圖像文件(PDF)連結",
        ]
        positions = []
        for label in expected_order:
            idx = block.find("'" + label + "'")
            with self.subTest(label=label):
                self.assertGreaterEqual(idx, 0, f"欄位定義缺少「{label}」")
            positions.append(idx)
        self.assertEqual(positions, sorted(positions), "欄位順序與使用者指定順序不符")

    def test_patent_columns_read_backend_field_names(self):
        """欄位定義讀的 JSON 欄名對齊後端 GET /patents 回應（顯示欄位契約）。"""
        for field in (
            "patent_type", "legal_status", "patent_note", "applicant",
            "title_original", "abstract_original", "application_number",
            "application_date", "application_year", "publication_number",
            "publication_date", "grant_number", "grant_date", "grant_year", "inventor",
            "priority_number", "priority_country", "priority_date",
            "current_owner", "orig_ipc_main", "detail_url", "pdf_url",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.html)

    def test_patent_topic_columns_split_by_channel(self):
        """技術／功效分類兩欄的欄名由 SOURCE_FIELDS 推導，不寫死兩個字面 key。"""
        self.assertIn("topicLabelKey", self.html)
        self.assertRegex(self.html, r"topic_label_'\s*\+|topic_label_\$\{")

    def test_patent_list_shows_scan_columns_and_row_expands(self):
        """列表只顯示辨識用欄位，點列展開完整欄位（26+ 欄橫向會爆）。"""
        for needle in (
            "listOnly",            # 欄位定義上的列表旗標
            "togglePatentDetail",  # 點列展開／收合
            "patent-detail-row",   # 詳情列
            "patent-row-clickable",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_patent_long_text_truncated_in_list_only(self):
        """摘要等長文字欄在列表截斷，詳情才全文。"""
        self.assertIn("truncate", self.html)
        self.assertIn("patentDetailHtml", self.html)

    def test_patent_link_columns_render_anchor_and_skip_empty(self):
        """連結欄輸出可點 <a target=_blank rel=noopener>；無值不輸出空連結。"""
        self.assertIn("linkCell", self.html)
        self.assertIn('target="_blank"', self.html)
        self.assertIn('rel="noopener"', self.html)

    def test_two_regions_share_one_table_implementation(self):
        """專利總覽與分類區共用同一份表格實作，只以 scope 決定欄位差異（不做兩套）。"""
        self.assertIn("function patentTableHtml(data, scope)", self.html)
        self.assertIn("function patentColumns(scope)", self.html)
        # 共用的證據＝只有一份實作、所有呼叫點都經它。
        # ⚠ 舊斷言逐一比對三個「字面」呼叫（patentTableHtml(data, 'topic') 等），但實作已
        # 改為 scope 用變數傳入（`patentTableHtml(data, scope)`，三處共用一支）——那比
        # 三個字面呼叫更符合「不做兩套」的本意，斷言卻因鎖死字面而長期紅燈。
        # 改鎖行為：實作只有一份，且沒有第二套平行的表格產生器。
        self.assertEqual(
            self.html.count("function patentTableHtml("), 1,
            "patentTableHtml 有多份實作——分類區與總覽又走回兩套")
        self.assertIn("patentTableHtml(data, scope)", self.html)
        # 🔴 2026-08-07 總覽刪除：'overview' 欄位組由瀏覽專利選全庫時傳入
        # （browsePatentsTableHtml(data, isGlobalSelected() ? 'overview' : 'topics')）。
        self.assertIn("isGlobalSelected() ? 'overview' : 'topics'", self.html)
        # 「所屬 Workspace」欄只在全庫視角出現，且以資料驅動（不是硬寫在某個表頭字串裡）。
        self.assertRegex(self.html, r"'所屬 Workspace',\s*key:\s*'workspaces',\s*scope:\s*\['overview'\]")

    def test_overview_merged_into_workspace_dropdown_not_topbar(self):
        """專利總覽併進 Workspace 下拉，全庫為第一項；頂列獨立藍鈕收掉（2026-07-24 定案，
        推翻 07-23「總覽放頂列」）。

        - 頂列不得再有 navTo('patents') 的獨立按鈕（藍鈕收掉）。
        - Workspace 下拉第一項＝全庫（固定哨兵 GLOBAL_WS_VALUE '__global__'）。
        - 選全庫＝原總覽視角（renderPatents 全庫）；左導覽仍留 workspace 內五項。
        """
        top = re.search(r'<header id="topbar">(.*?)</header>', self.html, re.S)
        self.assertIsNotNone(top, "找不到 topbar")
        self.assertNotIn("navTo('patents')", top.group(1), "頂列獨立總覽藍鈕須收掉")
        # 全庫哨兵常數與「全庫（所有專利）」下拉項文案。
        self.assertRegex(self.html, r"GLOBAL_WS_VALUE\s*=\s*'__global__'")
        self.assertIn("全庫（所有專利）", self.html)
        # 左導覽保留 workspace 內五項，不放 patents。
        nav = re.search(r'<nav id="nav-panel">(.*?)</nav>', self.html, re.S)
        self.assertIsNotNone(nav, "找不到 nav-panel")
        self.assertNotIn('data-nav="patents"', nav.group(1))
        # ⚠ comparison 於 2026-08-03 收起入口（見 test_comparison_entry_removed），
        # 故不列入；其餘 workspace 內功能仍須留在左導覽。
        for nav_key in ("topics", "reports", "export", "status"):
            with self.subTest(nav_key=nav_key):
                self.assertIn(f'data-nav="{nav_key}"', nav.group(1))

    def test_selecting_global_shows_overview(self):
        """選全庫（下拉第一項）＝原總覽視角：切到 patents 頁並走全庫 renderPatents。

        onWorkspaceChange 判斷選到全庫哨兵時，導向專利總覽（跨 workspace 全庫清單）。
        """
        self.assertIn("isGlobalSelected", self.html)
        # onWorkspaceChange 內含全庫哨兵判斷與導向 patents。
        m = re.search(r"function onWorkspaceChange\([^)]*\)\s*\{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 onWorkspaceChange() 定義")
        self.assertIn("GLOBAL_WS_VALUE", m.group(0))

    def test_no_empty_workspace_state_since_global_always_present(self):
        """全庫恆為可選第一項後，不得再有「尚無 workspace」空狀態／「請先選擇 workspace」。

        2026-07-24 定案第 3 點：全庫永遠可選，空狀態與 HTTP 500 一併解掉。
        """
        self.assertNotIn("（尚無 workspace）", self.html)
        self.assertNotIn("請先選擇 workspace", self.html)
        # loadWorkspaces 仍把全庫哨兵置頂（第一項）。
        m = re.search(r"function loadWorkspaces\([^)]*\)\s*\{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 loadWorkspaces() 定義")
        self.assertIn("GLOBAL_WS_VALUE", m.group(0))

    # ── c. 市場資料上傳區塊（綁 workspace；全庫隱藏） ──

    # 🔴 2026-08-04：test_market_upload_block_present_and_wired 已刪除——市場線整個移除（使用者定案），規格沒了測試就失去存在理由

    # 🔴 2026-08-04：test_market_upload_hidden_for_global_workspace 已刪除——市場線整個移除（使用者定案），規格沒了測試就失去存在理由

    # 🔴 2026-08-04：test_market_upload_supports_new_and_existing_workspace 已刪除——市場線整個移除（使用者定案），規格沒了測試就失去存在理由

    # 🔴 2026-08-04：test_report_shows_market_side_by_side_accepted_only 已刪除——市場線整個移除（使用者定案），規格沒了測試就失去存在理由

    def test_no_market_evidence_frontend_usage(self):
        """前端不得引用舊 market-evidence（v3 deep-research）API 或區塊。"""
        self.assertNotIn("/market-evidence", self.html)
        self.assertNotIn("marketEvidence", self.html)

    # ── f. 報表種類列出現況全部可用報表 ──

    def test_report_types_list_all_backend_definitions(self):
        """報表種類清單須列出全部**現況支援**的報表，不隱藏任一種。

        ⚠ 2026-07-29 加例外：`requires_market_data` 的報表（痛點四象限）在市場線
        （上傳→AI 摘要→使用者確認）實作前刻意不列出——使用者定案「整個藏起來，等市場線
        做好再放出來」。缺資料時痛點軸全標「待調查」，產出的圖看不出不完整、匯進 PPT
        會被讀成「痛點都很低」，比不產更糟。那類報表在市場線實作前不算「現況支援」。

        本測試原本要求「前端 ⊇ 後端全部定義」，與同日新增的
        tests/test_report_types_frontend_backend_parity.py（前端 ⊆ 後端，避免出現
        按了會 400 的死選項）在痛點這一項上直接衝突。兩支各自守一個方向，
        例外必須寫在同一個判準上（requires_market_data），否則兩套契約會互相拉扯。
        """
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        m = re.search(r"const REPORT_TYPES\s*=\s*\[(.*?)\n\];", self.html, re.S)
        self.assertIsNotNone(m, "找不到 REPORT_TYPES 定義")
        block = m.group(1)
        for name, definition in REPORT_DEFINITIONS.items():
            if definition.requires_market_data:
                continue
            with self.subTest(report=name):
                self.assertIn("'" + name + "'", block, f"報表種類缺少 {name}")

    # ── g. AI 助手欄縮窄 ──

    def test_ai_panel_narrowed(self):
        """右側 AI 助手欄縮窄（讓中間專利顯示更寬）：flex-basis 不得再是舊的 300px。"""
        m = re.search(r"#ai-panel\s*\{[^}]*\}", self.html)
        self.assertIsNotNone(m, "找不到 #ai-panel 樣式")
        css = m.group(0)
        # 取 flex 基準寬度數字，須明顯小於原 300px。
        w = re.search(r"flex:\s*0\s+0\s+(\d+)px", css)
        self.assertIsNotNone(w, "#ai-panel 須有固定 flex-basis 寬度")
        self.assertLess(int(w.group(1)), 300, "AI 助手欄須比原 300px 窄")

    # ── h. 專利顯示加捲軸、body 不橫向捲動 ──

    def test_patent_table_scroll_container_capped(self):
        """專利顯示表格在自身 overflow-x:auto 容器內捲動，且容器 max-width 不撐爆版面。"""
        m = re.search(r"\.table-wrap\s*\{[^}]*\}", self.html)
        self.assertIsNotNone(m, "找不到 .table-wrap 樣式")
        css = m.group(0)
        self.assertIn("overflow-x", css)
        self.assertIn("max-width", css)

    # ── C. topic 人工操作 ──

    def test_topic_rename_wired(self):
        """主題可人工重命名（PATCH /workspaces/{id}/topics/{key}，label_source=manual）。"""
        for needle in ("renameTopic", "'PATCH'", "renamed_by", "重新命名"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    # 🔴 2026-08-04 C9i：test_topic_merge_suggestions_wired 已刪除——合併建議入口移除
    #（v1 無相似度來源，端點刻意回空），規格沒了測試就失去存在理由。目標記於 decisions.md。

    def test_topic_manual_merge_wired(self):
        """可手動選兩個主題送出合併（POST /topics/merge，body 帶 topic_keys 兩個）。"""
        for needle in (
            "/topics/merge",
            "submitTopicMerge",
            "topic_keys",
            "requested_by",
            "topic-merge-a",
            "topic-merge-b",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_topic_merge_history_and_unmerge_wired(self):
        """合併歷史區接 /topics/merge-history，可對某筆 unmerge 還原（帶 merge_run_id）。"""
        for needle in (
            "/topics/merge-history",
            "/topics/unmerge",
            "loadMergeHistory",
            "submitTopicUnmerge",
            "merge_run_id",
            "can_unmerge",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    # ── D. AI 任務結果顯示 ──

    def test_ai_task_result_rendered_not_discarded(self):
        """AI 任務完成後結果要 render 在 AI 助手區，不得抓完丟棄。"""
        for needle in ("ai-task-result", "renderAiTaskResult", "pollAiTask"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # 既有缺陷：取回 /ai-tasks/{run_id} 後用 .catch(() => null) 丟棄，不得再出現。
        self.assertNotRegex(self.html, r"/ai-tasks/'\s*\+[^;]*\.catch\(\(\)\s*=>\s*null\)")

    def test_ai_task_result_scrollable(self):
        """AI 結果過長可捲動／摺疊，不撐爆右欄。"""
        self.assertIn("ai-result-body", self.html)
        self.assertRegex(self.html, r"\.ai-result-body\s*\{[^}]*overflow-y")

    # ── E. 匯出報告：完整預覽 + 編輯模式 + 匯出 HTML/PDF ──

    def test_export_has_full_report_preview_container(self):
        """匯出報告＝預覽工作台：有預覽容器與封面區，內容由 report-latest/content 動態載入。"""
        for needle in (
            "export-preview",           # 完整報告預覽容器
            "export-cover",             # 封面區掛點
            "loadExportPreview",        # 載入結構化報表內容
            "/report-latest/content",   # 結構化內容端點（一次取回，不逐卡打 API）
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_export_preview_cards_dynamic_not_hardcoded(self):
        """卡片（數據表→圖→解讀三區）由回傳的 sections 動態產生，不寫死報表清單。"""
        for needle in ("exportCardHtml", "sections", "variants", "narrative"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # 三區都要在卡片內：數據表 / 圖 / 解讀
        for cls_name in ("export-card-data", "export-card-chart", "export-card-narrative"):
            with self.subTest(cls_name=cls_name):
                self.assertIn(cls_name, self.html)

    def test_export_has_edit_mode_toggle(self):
        """編輯模式開關：關＝純預覽、開＝可編輯。"""
        for needle in ("export-edit-toggle", "toggleExportEditMode", "編輯模式"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_export_edit_three_editable_kinds(self):
        """可編輯三類掛點：解讀文案／封面資訊／自由段落。"""
        for needle in (
            "export-narrative-edit",   # 1. 解讀文案就地編輯
            "saveNarrativeEdit",
            "export-cover-title",      # 2. 封面標題
            "export-cover-scope",      # 分析範圍說明
            "export-cover-date",       # 日期
            "export-cover-count",      # 專利件數
            "export-note-block",       # 3. 自由段落／備註
            "addExportNote",
            "removeExportNote",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_export_edit_keeps_ai_original(self):
        """AI 原稿不得被覆蓋：人工修改另存欄位，原稿保留可還原。"""
        for needle in ("ai_original", "manual_text", "resetNarrativeEdit"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_export_preview_tab_before_download(self):
        """匯出前先過目（2026-07-31 改分頁預覽：按下開新分頁看完整報告，
        分頁內才有下載——取代舊確認框摘要）。細部契約見 test_export_html_preview_tab.py。"""
        for needle in (
            "btn-export-html",       # 預覽鈕
            "reviewExportOutput",    # 開分頁預覽
            "injectExportToolbar",   # 分頁內下載／列印工具列
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        self.assertNotIn("confirmExportOutput", self.html, "舊確認框流程應已退場")

    def test_export_output_is_selfcontained_html_with_print_css(self):
        """匯出單頁 HTML：自包含（圖以 data URI 內嵌）且含 @media print 列印樣式。"""
        for needle in ("buildExportHtml", "@media print", "data:image/svg+xml", "Blob"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # 下載：以 a[download] 觸發，不需後端另存檔。
        self.assertIn("download", self.html)

    # ── E2. 匯出報告：輸出 PPT（預覽閘門後送 ai:report_ppt → SSE 回流 → 下載連結） ──

    def test_export_has_output_ppt_button(self):
        """匯出工作台除既有「匯出報告」（單頁 HTML）外，新增「輸出 PPT」鈕。

        接線非重寫：既有 reviewExportOutput()／單頁 HTML 保留；PPT 是新增分支。
        """
        for needle in ("btn-export-ppt", "requestExportPpt"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # 既有單頁 HTML 匯出鈕與流程仍在（兩種都給）。
        self.assertIn("btn-export-html", self.html)
        self.assertIn("reviewExportOutput", self.html)
        self.assertIn("buildExportHtml", self.html)

    def test_export_ppt_sends_ai_report_ppt_task(self):
        """輸出 PPT＝送 POST /ai-tasks，task_type=ai:report_ppt，帶 report version／workspace_id。

        ⚠ workspace_id 放進 params（to_payload exclude 具名 workspace_id）；
        報表版本以 based_on_version 帶入（runner 解析報表目錄）。
        """
        self.assertIn("ai:report_ppt", self.html)
        m = re.search(r"function requestExportPpt\([^)]*\)\s*\{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 requestExportPpt() 定義")
        body = m.group(0)
        self.assertIn("/ai-tasks", body)
        self.assertIn("ai:report_ppt", body)
        self.assertIn("params", body)
        self.assertIn("based_on_version", body)

    def test_export_ppt_preview_gate_not_skipped(self):
        """預覽閘門不跳過：輸出 PPT 需已有預覽內容（exportPreview.content）才送。"""
        m = re.search(r"function requestExportPpt\([^)]*\)\s*\{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 requestExportPpt() 定義")
        self.assertIn("exportPreview.content", m.group(0))

    def test_export_ppt_polls_and_shows_download_link(self):
        """PPT job 完成後（經輪詢／SSE 回流）顯示 .pptx 下載連結（打下載路由）。"""
        body = self.js_function("pollExportPptJob")
        self.assertIn("pollExportPptJob", self.html)
        # 下載連結走 report-latest/ppt 下載路由。
        self.assertIn("/report-latest/ppt/", body)
        self.assertIn("loadExportPptFiles(version)", body)

    def test_export_ppt_uses_vendored_real_pptx_renderer(self):
        """批次二：匯出報告 PPT 預覽必須用 repo 內 vendored renderer，不走 CDN。"""
        self.assertIn("/static/vendor/pptx-renderer/aiden0z-pptx-renderer.browser.es.js", self.html)
        self.assertIn("PptxViewer", self.html)
        self.assertNotIn("unpkg.com/@aiden0z/pptx-renderer", self.html)

    def test_vendored_pptx_renderer_asset_is_served(self):
        """批次二：repo 內 renderer asset 必須能由 FastAPI static route 取到。"""
        resp = client.get("/static/vendor/pptx-renderer/aiden0z-pptx-renderer.browser.es.js")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("PptxViewer", resp.text)

    def test_export_preview_loads_ppt_files_and_renders_latest_pptx(self):
        """批次二：選報表版本後，從 /ppt-files 取最新 .pptx 並以真實檔案預覽。"""
        load_body = self.js_function("loadExportPreview")
        ppt_body = self.js_function("loadExportPptFiles")
        real_body = self.js_function("renderRealPptPreview")

        self.assertIn("loadExportPptFiles", load_body)
        self.assertIn("/reports/versions/", ppt_body)
        self.assertIn("/ppt-files", ppt_body)
        self.assertIn("renderRealPptPreview", ppt_body)
        self.assertIn("download_url", real_body)

    def test_export_preview_without_ppt_shows_generate_button(self):
        """批次二：該版本沒有 PPT 時顯示「請先產生 PPT」與產生按鈕。"""
        body = self.js_function("renderMissingPptState")

        self.assertIn("請先產生 PPT", body)
        self.assertIn("btn-generate-ppt", body)
        self.assertIn("requestExportPpt", body)

    def test_export_ppt_missing_narrative_chains_narrative_before_ppt(self):
        """按產生 PPT 時，缺 narrative 要先送 ai:narrative，並帶接續旗標。

        🔴 契約變更（R-5，2026-08-05）：接續派 `ai:report_ppt` 由**前端輪詢**改為
        **worker 端**（`_enqueue_chained_report_ppt`）——原做法在使用者關掉分頁時
        整條鏈斷在解讀完成，PPT 任務從未建立（實測 #200 後無 #201）。
        故本測試改為斷言「前端送旗標、且輪詢不再重複派工」；
        兩邊都派會產生兩個 PPT 任務。
        """
        request_body = self.js_function("requestExportPpt")
        chain_body = self.js_function("runNarrativeThenExportPpt")
        poll_body = self.js_function("pollNarrativeThenExportPpt")

        self.assertIn("exportReportHasNarratives", request_body)
        self.assertIn("runNarrativeThenExportPpt", request_body)
        self.assertIn("ai:narrative", chain_body)
        self.assertIn("then_export_ppt", chain_body)
        self.assertIn("pollNarrativeThenExportPpt", chain_body)
        self.assertNotIn("requestExportPpt({ skipNarrativeCheck: true })", poll_body)

    # ── 2026-07-30 起：CSS 模擬版面移除，預覽一律走真實 .pptx 渲染 ──
    # 原本這裡有五支測試斷言「模擬投影片渲染器存在」（loadPptLayout／
    # renderPptPagePreviewHtml／pptSlideStyle／pptClusterSplitSlideHtml…）。
    # ⚠ 使用者定案移除模擬版面後，那些測試的契約整個反轉——模擬版還留著早已
    # 定案不印的浮水印，證明兩套版面必然分岔。舊測試改寫如下。

    def test_export_preview_has_no_css_simulation(self):
        """匯出預覽不得再有 CSS 模擬投影片——PPT 長相一律看真實 .pptx 渲染。"""
        for gone in ("loadPptLayout", "renderPptPagePreviewHtml", "pptSlideStyle",
                     "pptSlideBodyHtml", "pptClusterSplitSlideHtml"):
            with self.subTest(gone=gone):
                self.assertNotIn(f"function {gone}", self.html,
                                 f"模擬版面的 {gone} 仍在")
        self.assertNotIn("exportPreview.pptLayout", self.html,
                         "pptLayout state 仍在——前端已不需要版型座標")

    def test_export_preview_renders_content_view(self):
        """匯出預覽沿用 #export-preview，畫的是內容視圖（與報表種類頁共用）。"""
        body = self.js_function("renderExportPreview")

        self.assertIn("renderReportContentHtml(exportPreview)", body)
        self.assertIn("export-preview", self.html)

    def test_edit_mode_toggles_between_content_and_real_pptx(self):
        """編輯模式開＝內容視圖可改；關＝回到真實 .pptx 預覽。"""
        body = self.js_function("toggleExportEditMode")

        self.assertIn("renderExportPreview", body)
        self.assertIn("loadExportPptFiles", body,
                      "關閉編輯模式沒回到真實 PPT 預覽")

    def test_ppt_theme_colors_not_hardcoded_in_frontend(self):
        """⚠ 沿用舊測試仍有效的部分：PPT 色票不得寫死在前端（防與產檔風格分叉）。"""
        for hardcoded in ("#1F5C3D", "#14402B", "#1C2B22", "#C24437", "#8FAA99", "#eaf2ed"):
            with self.subTest(hardcoded=hardcoded):
                self.assertNotIn(hardcoded, self.html)

    def test_report_viewer_splits_cluster_reports_by_user_selected_reports(self):
        """報表檢視下拉要拆開正式報表，不可把三個分群報表都合成分群分析。"""
        fill_body = self.js_function("fillReportViewSelect")
        options_body = self.js_function("buildReportViewOptions")
        cluster_views_body = self.js_function("clusterReportViews")
        render_body = self.js_function("renderReportViewer")

        self.assertIn("buildReportViewOptions", fill_body)
        self.assertIn("clusterReportViews", options_body)
        # 痛點板已刪（2026-08-04），分群檢視剩兩種。
        for key in ("cluster_topic_table", "opportunity_quadrant"):
            with self.subTest(key=key):
                self.assertIn(key, cluster_views_body)
        self.assertIn("source_field", options_body)
        self.assertIn("reportViewOptions", render_body)
        self.assertIn("reportSingleHtml", render_body)

    def test_report_viewer_uses_compact_annual_table_and_data_first_quadrants(self):
        """年度趨勢表格需 compact；機會/痛點四象限需數據→圖表→解讀。"""
        data_first_body = self.js_function("reportDataFirstLayout")
        single_body = self.js_function("reportSingleHtml")
        section_body = self.js_function("sectionForReportView")

        self.assertIn("report-single-compact-data", self.html)
        self.assertIn("viewSection.report_key === 'annual_trend'", single_body)
        self.assertIn("'opportunity_quadrant'", data_first_body)
        self.assertNotIn("key === 'annual_trend'", data_first_body)
        self.assertIn("variant.rows", section_body)
        self.assertIn("thresholds", section_body)
        self.assertIn("reportThresholdHtml", single_body)

    def test_report_viewer_chart_only_and_narrative_refresh(self):
        """公司×國家只留圖表；ai:narrative 成功後只重抓報表 content。"""
        single_body = self.js_function("reportSingleHtml")
        task_body = self.js_function("renderTaskList")
        refresh_body = self.js_function("maybeRefreshReportNarratives")

        self.assertIn("report-single-chart-only", self.html)
        self.assertIn("reportChartOnlyLayout", single_body)
        self.assertIn("chartOnly ? ''", single_body)
        self.assertIn("chartShown", single_body)
        self.assertIn("maybeRefreshReportNarratives", task_body)
        self.assertIn("ai:narrative", refresh_body)
        self.assertIn("reloadCurrentReportContentOnly", refresh_body)

    def test_export_ppt_edit_overrides_are_separated(self):
        """PPT 編輯覆寫必須分 slots/layout_overrides/position_overrides 三個 key。"""
        default_body = self.js_function("exportEditsDefault")
        request_body = self.js_function("requestExportPpt")

        for key in ("slots", "layout_overrides", "position_overrides"):
            with self.subTest(key=key):
                self.assertIn(key, default_body)
                self.assertIn(key, request_body)
        self.assertIn("exportPptApprovalOverrides", self.html)
        self.assertIn("approval_overrides", request_body)

    def test_export_ppt_drag_editing_removed(self):
        """⚠ 拖曳定位（2026-07-29 已定案取消）與模擬版面的就地編輯一併移除。

        舊測試斷言 attachPptDragHandlers／savePptPositionOverride 存在——
        那是在假投影片上拖曳，模擬版面移除後整組退場。
        編輯模式改建在真實 PPT 架構上（方案見 ppt-visual-rework-spec.md 四之二節）。
        """
        for gone in ("attachPptDragHandlers", "savePptPositionOverride",
                     "savePptSlotEdit", "pptSlotText", "pptLayoutSelectHtml"):
            with self.subTest(gone=gone):
                self.assertNotIn(f"function {gone}", self.html,
                                 f"模擬版面的編輯機制 {gone} 仍在")
        self.assertNotIn("draggable-ppt-box", self.html)

    def test_export_edits_structure_kept_for_future_edit_mode(self):
        """⚠ edits 資料結構保留——編輯模式改建在真實 PPT 上時沿用同一結構。"""
        default_body = self.js_function("exportEditsDefault")

        for key in ("slots", "layout_overrides", "position_overrides"):
            with self.subTest(key=key):
                self.assertIn(key, default_body)

    # ── E3. 市場×專利同列並排（對標範例第 9 頁；報表顯示區同一 flex row） ──

    def test_market_and_patent_reports_same_row(self):
        """市場側摘要與專利側報表卡同一列左右並排（非上下分區）。

        report-inline-view 併進與 market-side-by-side 同一 flex row 容器。
        """
        # 同列容器掛點存在。
        self.assertIn("report-market-row", self.html)
        # CSS：該容器為橫向 flex row（左右並排）。
        m = re.search(r"#report-market-row\s*\{[^}]*\}", self.html)
        self.assertIsNotNone(m, "找不到 #report-market-row 樣式")
        css = m.group(0)
        self.assertIn("flex", css)
        self.assertNotIn("column", css)

    def test_report_page_has_inline_report_container(self):
        """報表種類頁：job succeeded 後把完整報表直接渲染在主內容區（內嵌容器掛點）。"""
        for needle in (
            "report-inline-view",      # 內嵌報表容器掛點
            "loadInlineReport",        # 載入 report-latest/content 並就地渲染
            "/report-latest/content",  # 結構化內容端點（一次取回，不逐卡打 API）
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_report_not_opened_in_new_tab(self):
        """報表不得以另開分頁呈現：頁面內不應有 target="_blank" 開報表的用法。"""
        for match in re.finditer(r"<a\b[^>]*>", self.html):
            tag = match.group(0)
            if 'target="_blank"' not in tag and "target='_blank'" not in tag:
                continue
            with self.subTest(tag=tag):
                self.assertNotIn("report-latest", tag)

    def test_inline_report_reuses_export_render_functions(self):
        """內嵌報表複用匯出工作台的渲染函式，不重寫一套（共用純渲染 + view 參數）。"""
        for needle in ("exportCardHtml", "exportCoverHtml", "renderReportContentHtml"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # 渲染函式接受 view 狀態參數（read-only 供內嵌用、可編輯供工作台用），
        # 而非直接綁死單一全域編輯狀態。
        self.assertIn("readOnlyReportView", self.html)

    def test_inline_report_loading_and_error_feedback(self):
        """內嵌報表有載入中提示與可讀中文錯誤（不吞錯、不留白）。"""
        for needle in ("載入報表內容中", "載入報表內容失敗"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_inline_report_shows_version_and_lazy_images(self):
        """版本號顯示給使用者；圖以 asset 端點載入且 lazy loading。"""
        self.assertIn("報表版本", self.html)
        self.assertIn("/report-latest/asset/", self.html)
        self.assertIn('loading="lazy"', self.html)

    def test_inline_report_keeps_export_entry(self):
        """保留前往匯出報告（編輯/匯出）的入口，讓使用者要編輯時能過去。"""
        self.assertIn("navTo('export')", self.html)

    def test_report_page_auto_loads_latest_on_entry(self):
        """進「報表種類」頁即自動載入既有最新報表（不用先產製、也不重新產製）。

        契約：renderReports() 內含載入呼叫（loadReportVersions），且載入路徑是
        讀取既有內容的端點，不是 POST /reports 產製。
        """
        m = re.search(r"function renderReports\(\)\s*\{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 renderReports() 定義")
        body = m.group(0)
        self.assertIn("loadReportVersions", body)
        self.assertNotIn("submitReports()", body.replace('onclick="submitReports()"', ""))

    def test_report_version_list_container_and_endpoint(self):
        """版本列表：有掛點容器、呼叫 versions 端點、指定版本內容端點路徑片段。"""
        for needle in (
            "report-version-list",        # 版本列表容器掛點
            "loadReportVersions",         # 載入版本清單
            "/reports/versions",          # 列版本端點
            "/content",                   # 指定版本內容端點尾段
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_old_versions_collapsed_and_expandable(self):
        """最新版本預設展開、舊版本收合可點開（收合展開掛點與切換函式都在）。"""
        for needle in (
            "toggleReportVersion",        # 展開／收合切換
            "report-version-body-",       # 各版本內容容器 id 前綴
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # 以 <details>/<summary> 或 open 狀態表達最新預設展開
        self.assertRegex(self.html, r"report-version-item|report-version-row")

    def test_old_version_content_lazy_loaded(self):
        """lazy：展開舊版本才載入該版本內容，不一進頁就把所有版本載回來。"""
        m = re.search(r"function loadReportVersions\(\)\s*\{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 loadReportVersions() 定義")
        # 清單載入函式本身不得逐版本抓 content（那是展開時才做）
        self.assertNotIn("forEach", m.group(0).split("versions")[0])
        self.assertIn("loadReportVersionContent", self.html)

    def test_version_timestamp_is_humanized(self):
        """版本標示可讀化（時間戳轉成 2026-07-22 00:10 之類），不是只丟目錄名。"""
        self.assertIn("fmtReportVersionLabel", self.html)

    def test_version_body_delegates_to_menu_driven_viewer(self):
        """版本區只放 PPT 清單，報表本體交給選單驅動的 viewer（R9，2026-07-27 改版）。

        ⚠ 本測試前身為 test_version_list_reuses_inline_render_functions，斷言版本區複用
        `renderReportContentHtml`／`readOnlyReportView`。R7/R8/R9 改版後兩頁分家、版本區
        不再自己攤開整份報告（那正是使用者說的「不同報表混在一起」），該前提已作廢，
        斷言隨之過期而長期紅燈。改鎖現行契約：版本區只掛 PPT 清單容器 ＋ 把 content
        餵給選單。
        """
        m = re.search(
            r"function loadReportVersionContent\([^)]*\)\s*\{.*?\n\}", self.html, re.S
        )
        self.assertIsNotNone(m, "找不到 loadReportVersionContent() 定義")
        body = m.group(0)
        self.assertIn("report-ppt-list-", body, "版本區應只掛 PPT 清單容器")
        self.assertIn("fillReportViewSelect", body, "版本內容應餵給上方選單驅動的 viewer")
        self.assertNotIn(
            "renderReportContentHtml", body,
            "版本區不得再自己攤開整份報告——R9 已改為選單一次顯示一份")

    # ── F. 分群候選：後端自解析最新 run（不再掃全域 /tasks） ──

    def test_candidates_use_backend_latest_resolution(self):
        """候選查詢直接打後端解析端點（帶 workspace_id + source_field），不繞 /tasks 過濾。"""
        self.assertIn("/clustering/candidates?", self.html)
        for qs in ("workspace_id=", "source_field="):
            with self.subTest(qs=qs):
                self.assertIn(qs, self.html)
        # 舊繞路已移除：不得再掃 /tasks 找 clustering_calibrate 的 run_id。
        self.assertNotIn("findLatestCalibrateRunId", self.html)
        self.assertNotIn("CANDIDATE_TASK_SCAN_LIMIT", self.html)
        # 候選載入函式內不得再出現以 job_type 過濾 /tasks 的邏輯。
        m = re.search(
            r"async function loadTopicCandidates\([^)]*\)\s*\{.*?\n\}", self.html, re.S
        )
        self.assertIsNotNone(m, "找不到 loadTopicCandidates() 定義")
        body = m.group(0)
        self.assertNotIn("/tasks", body)
        self.assertNotIn("clustering_calibrate", body)
        self.assertIn("/clustering/candidates?", body)

    def test_candidates_distinguish_no_run_from_query_error(self):
        """找不到候選要能區分「真的沒跑過分群」（404）與「查詢失敗」（其他錯誤）。"""
        self.assertIn("尚未分群", self.html)
        self.assertIn("查詢分群候選失敗", self.html)

    # ── G. 全域 timer 註冊表：切 workspace／切頁一次清空 ──

    def test_timer_registry_api_present(self):
        """有註冊／註銷／清空 API 的統一 timer 註冊表（非各自散管 setTimeout）。"""
        for needle in ("registerTimer", "clearTimer", "clearAllTimers"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_all_polls_go_through_registry(self):
        """六個輪詢點的 setTimeout 一律經註冊表；不得留裸 setTimeout(tick, …)。"""
        # 排程重試一律走 registerTimer(…, tick, ms)，不得再出現直接 setTimeout(tick, …)
        self.assertNotRegex(self.html, r"setTimeout\(\s*tick\s*,")
        self.assertRegex(self.html, r"registerTimer\([^)]*tick")
        # 舊的單一 import 專用 timer 變數已併入註冊表，不留兩套機制
        self.assertNotIn("importPollTimer", self.html)

    def test_registry_cleared_on_workspace_and_nav_change(self):
        """兩個清空時機：切 workspace（onWorkspaceChange）與切導覽頁（navTo）。"""
        for fn_name in ("onWorkspaceChange", "navTo"):
            with self.subTest(fn_name=fn_name):
                m = re.search(
                    rf"function {fn_name}\([^)]*\)\s*\{{.*?\n\}}", self.html, re.S
                )
                self.assertIsNotNone(m, f"找不到 {fn_name}() 定義")
                self.assertIn("clearAllTimers", m.group(0))

    def test_registry_really_clears_timeout_not_just_flag(self):
        """清空必須真的 clearTimeout（旗標擋不住已排程的 tick）。"""
        m = re.search(r"function clearAllTimers\([^)]*\)\s*\{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(m, "找不到 clearAllTimers() 定義")
        self.assertIn("clearTimeout", m.group(0))

    def test_registry_not_hardcoded_poll_names(self):
        """註冊表不得寫死既有輪詢函式名（日後新增輪詢自動受管）。"""
        m = re.search(
            r"function registerTimer\([^)]*\)\s*\{.*?\n\}"
            r".*?function clearAllTimers\([^)]*\)\s*\{.*?\n\}",
            self.html,
            re.S,
        )
        self.assertIsNotNone(m, "找不到 registerTimer()…clearAllTimers() 區段")
        registry_src = m.group(0)
        for poll_name in (
            "pollFinalizeJob", "pollTopicOpJob", "pollReportJob",
            "pollExportJob", "pollAiTask", "pollImportJob",
        ):
            with self.subTest(poll_name=poll_name):
                self.assertNotIn(poll_name, registry_src)

    def test_object_url_cleanup_not_registered(self):
        """revokeObjectURL 是資源清理不是輪詢，不得收進註冊表（清掉會洩漏 objectURL）。"""
        m = re.search(r".*revokeObjectURL[^\n]*", self.html)
        self.assertIsNotNone(m, "找不到 revokeObjectURL 呼叫")
        line = m.group(0)
        self.assertIn("setTimeout(", line)
        self.assertNotIn("registerTimer", line)

    def test_no_hardcoded_fake_patent_rows(self):
        """主內容區不得寫死假專利資料列（資料一律來自真實 API）。"""
        # 佔位提示允許；但不得出現硬寫的假專利號樣式（如 US1234567B2 之類寫死列）
        # 以「主內容初始為載入/空狀態提示」作為契約：main-panel 初始不含 <tr> 資料列
        main_match = re.search(
            r'id="main-panel".*?</(?:main|div|section)>',
            self.html,
            re.S,
        )
        self.assertIsNotNone(main_match)


if __name__ == "__main__":
    unittest.main()
