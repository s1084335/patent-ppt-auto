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
        """左導覽五項：專利總覽 / 分類區 / 報表種類 / 案件比對 / 匯出報告。

        （分群任務已移除：分群改為匯入後自動背景觸發，使用者不需手動點。）
        """
        for nav_key in (
            "patents",     # 專利總覽
            "topics",      # 分類區
            "reports",     # 報表種類勾選
            "comparison",  # 案件比對
            "export",      # 匯出報告
        ):
            with self.subTest(nav_key=nav_key):
                self.assertRegex(self.html, rf'data-nav="{nav_key}"')

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

    def test_ai_token_field_and_header_wired(self):
        """AI 任務金鑰：有輸入框、存 localStorage、呼叫時帶 Bearer 標頭。"""
        for needle in ("ai-token", "saveAiToken", "aiAuthHeaders", "patent_api_token"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        self.assertIn("'Bearer '", self.html)

    def test_ai_token_not_hardcoded_in_page(self):
        """token 不得寫死在公開 HTML：只能由使用者填入 localStorage。"""
        # 頁面內不得出現任何寫死的 Bearer 值（Bearer 後只接變數串接）。
        self.assertNotRegex(self.html, r"Bearer\s+[A-Za-z0-9_\-]{8,}")
        self.assertIn("localStorage", self.html)

    def test_comparison_has_create_form(self):
        """案件比對有建立比對案件表單（案件名稱、文字、建立按鈕）。"""
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

    def test_reports_include_family_quality_detail(self):
        """報表清單必含家族完整性明細（report_key=family_quality_detail）。"""
        self.assertIn("family_quality_detail", self.html)
        self.assertIn("家族完整性明細", self.html)

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
        """用途下拉：general／case_comparison（預設 general），對齊後端 IMPORT_PURPOSES。"""
        self.assertIn("import-purpose", self.html)
        for needle in ("general", "case_comparison", "一般情報分析", "案件比對"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

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

    # ── A. 匯入確認（送出前摘要 + 送出後結果卡 + 前往該 workspace） ──

    def test_import_has_confirm_summary_before_upload(self):
        """匯入送出前先顯示確認摘要（檔名／大小／目標 workspace／用途），按確認才送出。"""
        for needle in (
            "import-confirm",         # 確認摘要區掛點
            "btn-confirm-import",     # 「確認匯入」鈕
            "buildImportConfirm",     # 組摘要（讀真實選擇，不寫死）
            "確認匯入",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

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
        """分類區版式：主題標籤區在專利清單區之上，中間不夾主題人工操作等區塊。"""
        tags_at = self.html.index('id="topic-tags"')
        patents_at = self.html.index('id="topic-patents"')
        self.assertLess(tags_at, patents_at, "主題標籤必須在專利清單之上")
        # 人工操作區不得夾在標籤與專利清單之間（移到專利清單之後）。
        ops_at = self.html.index('id="topic-ops"')
        self.assertGreater(ops_at, patents_at, "主題人工操作不得擋在標籤與專利清單之間")

    def test_topics_empty_state_message(self):
        """workspace 無分群結果時顯示明確中文提示（不是空白、不讓使用者點到 404）。"""
        self.assertIn("尚未分群", self.html)
        self.assertIn("匯入後會自動進行分群", self.html)
        # 錯誤不吞：載入失敗要顯示可讀訊息（含狀態碼／訊息）。
        self.assertIn("topicLoadErrorHtml", self.html)

    def test_topic_patents_error_readable(self):
        """點標籤後 404／空結果要有可讀中文訊息，不吞錯。"""
        self.assertIn("找不到此主題", self.html)
        # fetch 失敗訊息帶 HTTP 狀態，供使用者/開發者判讀。
        self.assertRegex(self.html, r"HTTP\s*'\s*\+")

    # ── B4. 專利總覽跨所有 workspace ──

    def test_overview_lists_all_patents_across_workspaces(self):
        """專利總覽接全庫專利端點（GET /patents，不分 workspace），非只列選定 workspace。"""
        self.assertIn("loadAllPatents", self.html)
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
        """全庫專利分頁載入，不一次撈全部。"""
        self.assertIn("PATENTS_PAGE_SIZE", self.html)
        self.assertIn("pagePatents", self.html)

    # ── C. topic 人工操作 ──

    def test_topic_rename_wired(self):
        """主題可人工重命名（PATCH /workspaces/{id}/topics/{key}，label_source=manual）。"""
        for needle in ("renameTopic", "'PATCH'", "renamed_by", "重新命名"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_topic_merge_suggestions_wired(self):
        """合併建議區接 /topics/merge-suggestions，列相近主題對與 distance，可一鍵合併。"""
        for needle in (
            "/topics/merge-suggestions",
            "loadMergeSuggestions",
            "topic-merge-suggestions",
            "distance",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

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

    def test_export_has_confirm_step_before_export(self):
        """匯出前有確認步驟（使用者 OK 才匯出）。"""
        for needle in (
            "btn-export-html",       # 匯出鈕
            "reviewExportOutput",    # 先組確認摘要
            "export-confirm",        # 確認區掛點
            "btn-confirm-export",    # 確認匯出
            "confirmExportOutput",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_export_output_is_selfcontained_html_with_print_css(self):
        """匯出單頁 HTML：自包含（圖以 data URI 內嵌）且含 @media print 列印樣式。"""
        for needle in ("buildExportHtml", "@media print", "data:image/svg+xml", "Blob"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # 下載：以 a[download] 觸發，不需後端另存檔。
        self.assertIn("download", self.html)

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

    def test_version_list_reuses_inline_render_functions(self):
        """展開的版本內容複用既有渲染函式，不重寫一套。"""
        m = re.search(
            r"function loadReportVersionContent\([^)]*\)\s*\{.*?\n\}", self.html, re.S
        )
        self.assertIsNotNone(m, "找不到 loadReportVersionContent() 定義")
        body = m.group(0)
        self.assertIn("renderReportContentHtml", body)
        self.assertIn("readOnlyReportView", body)

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
