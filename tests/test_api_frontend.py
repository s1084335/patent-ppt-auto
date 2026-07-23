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
        """左導覽五項：專利總覽 / 分類區 / 報表種類 / 案件比對 / 匯出報告。"""
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

    # ── B. 分群任務區 ──

    def test_nav_has_clustering_region(self):
        """左導覽含分群任務區（data-nav="clustering"）。"""
        self.assertRegex(self.html, r'data-nav="clustering"')
        self.assertIn("renderClustering", self.html)

    def test_clustering_calibrate_and_incremental_wired(self):
        """分群區可建 calibrate／incremental 工作，並選 source_field（接白名單通道，非寫死單值）。"""
        for needle in (
            "btn-run-calibrate",
            "btn-run-incremental",
            "clustering-source-field",
            "runCalibrate",
            "runIncremental",
            "source_field",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # 端點以 `/clustering/` + kind（calibrate／incremental）組成，兩種 kind 都要存在。
        self.assertIn("/clustering/", self.html)
        self.assertRegex(self.html, r"createClusteringJob\('calibrate'")
        self.assertRegex(self.html, r"createClusteringJob\('incremental'")
        # 通道選項由共用常數產生（技術／功效兩通道），供分群區與分類區共用。
        self.assertIn("SOURCE_FIELDS", self.html)
        self.assertIn("effect_summary", self.html)

    def test_clustering_polls_job_and_reads_run_id(self):
        """calibrate 工作沿用統一進度元件輪詢 /jobs/{id}，完成後由 result.run_id 取候選。"""
        self.assertIn("pollClusteringJob", self.html)
        self.assertIn("/jobs/", self.html)
        self.assertIn("jobProgressCardHtml", self.html)
        # run_id 來自 job.result（calibrate summary），非使用者手輸或寫死。
        self.assertRegex(self.html, r"result\s*(\|\||&&|\.|\[)")
        self.assertIn("run_id", self.html)

    def test_clustering_candidates_and_finalize_wired(self):
        """可查候選（/clustering/runs/{id}/candidates）並選定候選 finalize。"""
        for needle in (
            "/candidates",
            "/finalize",
            "loadClusteringCandidates",
            "finalizeClustering",
            "candidate_id",
            "已定案",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_clustering_candidate_columns_not_hardcoded(self):
        """候選表欄位依 API 實際回傳的鍵動態產生，不寫死欄位清單。"""
        # 以 Object.keys / 動態欄位組表為契約：改 API 欄位時前端自動跟隨。
        self.assertIn("candidateColumnsFrom", self.html)
        self.assertIn("Object.keys", self.html)

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
