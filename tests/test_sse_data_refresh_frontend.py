"""SSE 資料自動刷新的前端契約（complete-sse-data-refresh task 2.3–2.5）。

矩陣與異常行為定稿見 change 的 design.md「事件×刷新矩陣」。本檔鎖五件事：

1. `JOB_REFRESH_TARGETS`＝job type → 資源的**唯一來源**，且必須涵蓋後端
   `JOB_TYPES` 全集——新增 job 型別漏接刷新時這裡先紅，不是等使用者發現。
2. 只有 `succeeded` 觸發資料刷新；終結事件以 event_id 去重。
3. 資源刷新有 debounce 與 in-flight 合併；依當前頁面 gating。
4. 瀏覽表格刷新保留已展開的詳情列（WSP-007）。
5. 斷線重連後做一次補償刷新（pg_notify 無補送，這是決策 3 的 fallback）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from backend.app.db.job_repository import JOB_TYPES

INDEX_HTML = (Path(__file__).resolve().parents[1] / "backend" / "app"
              / "static" / "index.html")


def _extract_mapping_keys(html: str) -> set[str]:
    """抓 JOB_REFRESH_TARGETS 字面量的鍵集合（單引號鍵）。"""
    match = re.search(r"const JOB_REFRESH_TARGETS = \{(.*?)\n\};", html, re.DOTALL)
    assert match, "找不到 const JOB_REFRESH_TARGETS = {...};"
    return set(re.findall(r"'([^']+)':", match.group(1)))


class MappingSingleSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_mapping_covers_every_backend_job_type(self):
        """🔴 跨層一致性：後端每個 job type 都要在前端 mapping 表態
        （沒有可見面就明確給空陣列），新增型別漏接時此測試先紅。"""
        keys = _extract_mapping_keys(self.html)
        missing = set(JOB_TYPES) - keys
        self.assertEqual(missing, set(), f"JOB_REFRESH_TARGETS 缺 job type：{sorted(missing)}")

    def test_mapping_has_no_stale_job_types(self):
        """反向：mapping 不得殘留已刪除的 job type（如 ai:report_ppt）。"""
        keys = _extract_mapping_keys(self.html)
        stale = keys - set(JOB_TYPES)
        self.assertEqual(stale, set(), f"JOB_REFRESH_TARGETS 殘留未知型別：{sorted(stale)}")

    def test_resource_refreshers_exist_with_nav_gating(self):
        """資源 → 刷新函式＋適用頁；停留無關頁面不刷（WSP-007 scenario 2）。"""
        self.assertIn("const RESOURCE_REFRESHERS", self.html)
        for res in (
            "browsePatents",
            "noteCoverage",
            "topics",
            "reports",
            "workspaces",
            "companyGroups",
        ):
            self.assertIn("'" + res + "'", self.html, f"缺資源 {res}")
        match = re.search(r"function scheduleResourceRefresh\(.*?\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(match, "缺 scheduleResourceRefresh")

    def test_import_refreshes_workspace_dropdown(self):
        """匯入成功會建新 workspace，頂列下拉必須跟著刷新。"""
        match = re.search(r"'patent_import':\s*\[([^\]]*)\]", self.html)
        self.assertIsNotNone(match)
        self.assertIn("workspaces", match.group(1))


class EventDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        match = re.search(r"function maybeRefreshFromEvent\(.*?\n\}", cls.html, re.DOTALL)
        assert match, "缺 maybeRefreshFromEvent"
        cls.fn = match.group(0)

    def test_only_succeeded_triggers_refresh(self):
        """failed/cancelled 只動任務卡，不得拿失敗結果刷資料區（PRT-005）。"""
        self.assertRegex(self.fn, r"status\s*!==\s*'succeeded'",
                         "刷新必須以 succeeded 守門")

    def test_terminal_events_deduped_by_event_id(self):
        self.assertIn("event_id", self.fn)
        self.assertIn("seenRefreshEvents", self.html, "缺去重集合")

    def test_debounce_and_inflight_merge(self):
        """同資源短時間多事件合併一次 refresh；in-flight 中再收到記待重跑。"""
        self.assertIn("REFRESH_DEBOUNCE_MS", self.html)
        self.assertIn("refreshInFlight", self.html)
        self.assertIn("refreshQueued", self.html)

    def test_sse_onmessage_dispatches_to_refresh(self):
        """connectSSE 收 run 事件除任務卡外必須進刷新分派。"""
        match = re.search(r"function connectSSE\(.*?\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(match)
        self.assertIn("maybeRefreshFromEvent", match.group(0))

    def test_company_group_data_event_dispatches_to_registry_refresh(self):
        """集團資料事件必須沿用既有 refresh scheduler，不能直接重繪整頁。"""
        self.assertRegex(
            self.html,
            r"'companyGroups':\s*\{\s*navs:\s*\['browse'\],\s*run:\s*renderCompanyGroupRegistry",
        )
        match = re.search(r"function connectSSE\(.*?\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(match)
        self.assertIn("maybeRefreshDataEvent", match.group(0))
        self.assertIn("function maybeRefreshDataEvent", self.html)
        self.assertRegex(
            self.html,
            r"ev\.resource\s*===\s*'companyGroups'.*scheduleResourceRefresh\('companyGroups'\)",
        )


class BrowsePreserveExpandedTests(unittest.TestCase):
    """WSP-007：文獻備註完成自動出現，但已展開的其他專利詳情保留。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_detail_rows_carry_patent_id(self):
        """詳情列要帶 data-pid——index 型 id 重繪後對不回同一件專利。"""
        self.assertRegex(self.html, r'patent-detail-row"[^>]*data-pid=',
                         "patent-detail-row 缺 data-pid")

    def test_browse_refresher_reopens_expanded_rows(self):
        match = re.search(
            r"async function refreshBrowsePreservingDetails\(.*?\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(match, "缺 refreshBrowsePreservingDetails")
        fn = match.group(0)
        self.assertIn("data-pid", fn)
        self.assertIn("loadBrowsePatents", fn)


class ReconnectCompensationTests(unittest.TestCase):
    """pg_notify 無法補送歷史事件——重連成功後必須補償刷新一次可見資源。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_reconnect_triggers_visible_refresh(self):
        match = re.search(r"function connectSSE\(.*?\n\}", self.html, re.DOTALL)
        self.assertIsNotNone(match)
        fn = match.group(0)
        self.assertIn("sseWasDown", fn, "缺斷線標記——首連與重連要能區分")
        self.assertIn("refreshVisibleResources", fn, "重連後未補償刷新")

    def test_refresh_visible_resources_exists(self):
        self.assertIn("function refreshVisibleResources", self.html)

    def test_sse_actually_reconnects_after_error(self):
        """🔴 既有實作 onerror 關掉 EventSource 後**從不重連**（connectSSE 只在
        啟動時呼叫一次）——斷線後永遠停在 30 秒輪詢，補償刷新成死碼、
        「即時回流」名存實亡。必須排程重連（退避上限 60s，連上重設）。"""
        match = re.search(r"function connectSSE\(.*?\n\}", self.html, re.DOTALL)
        fn = match.group(0)
        self.assertIn("scheduleSseReconnect", fn, "onerror 未排程重連")
        self.assertIn("function scheduleSseReconnect", self.html)
        self.assertRegex(self.html, r"sseReconnectDelayMs\s*=\s*Math\.min",
                         "重連間隔缺退避上限")


class MappingContentTests(unittest.TestCase):
    """矩陣抽查：design.md 定稿的幾條關鍵路由。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def _targets(self, job_type: str) -> str:
        match = re.search(r"'" + re.escape(job_type) + r"':\s*\[([^\]]*)\]", self.html)
        self.assertIsNotNone(match, f"mapping 缺 {job_type}")
        return match.group(1)

    def test_patent_note_refreshes_browse_and_coverage(self):
        targets = self._targets("ai:patent_note")
        self.assertIn("browsePatents", targets)
        self.assertIn("noteCoverage", targets)

    def test_clustering_chain_refreshes_topics(self):
        for jt in ("clustering_calibrate", "clustering_incremental",
                   "clustering_finalize", "topic_merge", "topic_unmerge",
                   "ai:topic_label", "ai:irrelevant_filter"):
            self.assertIn("topics", self._targets(jt), f"{jt} 未刷分類區")

    def test_report_generate_refreshes_reports(self):
        self.assertIn("reports", self._targets("report_generate"))

    def test_narrative_keeps_existing_path(self):
        """ai:narrative 走既有 maybeRefreshReportNarratives（帶版本守門），
        mapping 給空陣列＋既有路徑保留——不重複接第二條刷新線。"""
        self.assertEqual(self._targets("ai:narrative").strip(), "")
        self.assertIn("maybeRefreshReportNarratives", self.html)


if __name__ == "__main__":
    unittest.main()
