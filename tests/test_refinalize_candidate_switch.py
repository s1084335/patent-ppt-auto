"""同一個 run 內改選其他候選方案（2026-07-28 使用者需求）。

實機情境：使用者第一次分類選了「保守 3 個主題」，之後想改試「平衡 5 個主題」。
畫面文案寫「要改用其他分法，請按上方『分類』重跑一次」——**但那條路做不到**：
`auto` 端點以「有無既有主題」判斷，有主題就走 incremental，而 incremental 只處理新專利，
沒有新資料時什麼都不做（實測更慘：artifact pkl 遺失時直接 FileNotFoundError 炸掉）。

真正的阻擋在 `_persist_final_topics`：

    if (dict(scope.get("topic_state_json") or {}).get("topics") or []):
        raise ValueError("workspace source already has topics; use incremental or merge")

已有 topics 就拒絕 → 只能 finalize 一次 → 選擇無法反悔。

**但候選資料一直都在**：calibrate 產出的 k=3／5／8 三個方案完整存在
`topic_runs.topic_state_json.candidates`，finalize 本來就是靠 `candidate_id` 指定。
且 `_write_topic_state` 寫 assignments 前已 `DELETE ... WHERE run_id = %s`，
資料層面早就支援覆蓋——缺的只是放行。

使用者定案：**同一個 run 內想改幾次就改幾次**。

⚠ 切換要連帶清掉的：舊主題的 AI 命名／人工改名、合併與拆分歷史——k=3 的 T001 與 k=5 的 T001
是不同東西，硬留會張冠李戴。**不相干桶的人工裁決要保留**（那是「這篇專利不相干」，
與主題怎麼切無關）。
"""
from __future__ import annotations

import unittest
from unittest import mock


class RefinalizeGuardTests(unittest.TestCase):
    """`_persist_final_topics` 不得因「已有主題」而拒絕重選候選。"""

    def test_existing_topics_does_not_block_refinalize(self):
        """已有 topics 時再次 finalize 應被允許（換候選），不是 raise。"""
        from backend.app.clustering import runner

        # 只驗那道 guard 的判斷，不跑真的 k-means：用 scope 模擬「已有 3 個主題」
        scope = {
            "workspace_id": 1,
            "source_field": "wips_independent_claims",
            "topic_state_json": {"topics": [{"topic_code": "T001"}, {"topic_code": "T002"}]},
        }
        self.assertTrue(
            runner.can_refinalize(scope),
            "已有主題就拒絕 re-finalize——使用者無法改選其他候選方案")

    def test_guard_helper_exists(self):
        """判斷邏輯要抽成可測的函式，不埋在 _persist_final_topics 內。"""
        from backend.app.clustering import runner

        self.assertTrue(hasattr(runner, "can_refinalize"))


class SwitchCleanupTests(unittest.TestCase):
    """切換候選時的連帶清理：主題級的東西要清，專利級的裁決要留。"""

    def test_clears_topic_scoped_artifacts(self):
        """AI 命名／合併歷史等綁在舊主題編號上的資料必須清掉。"""
        from backend.app.clustering import runner

        self.assertTrue(
            hasattr(runner, "clear_topic_scoped_state"),
            "缺少切換清理函式：k=3 的 T001 與 k=5 的 T001 不是同一個主題，"
            "沿用舊標籤會張冠李戴")

    def test_keeps_patent_scoped_exclusions(self):
        """不相干桶的人工裁決是專利級的，與主題切分無關，不得清掉。

        ⚠ 只檢查實際送出的 SQL，不搜整份原始碼——註解裡本來就會提到這張表
        （說明「為何不清它」），搜原始碼會被註解餵飽而誤判。
        本測試初版即犯此錯，正是 decisions.md 2026-07-28「斷言必須鎖在語意正確的
        最小範圍」那條的又一例。
        """
        from backend.app.clustering import runner

        executed: list[str] = []

        class _Cur:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params=None): executed.append(sql)
            def fetchall(self): return []

        class _Conn:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def cursor(self): return _Cur()

        with mock.patch.object(runner.psycopg, "connect", return_value=_Conn()), \
                mock.patch.object(runner, "get_connection_kwargs", return_value={}):
            runner.clear_topic_scoped_state(1)

        joined = " ".join(executed)
        self.assertNotIn(
            "workspace_excluded_patents", joined,
            "切換候選不得動不相干桶——那是「這篇專利不相干」的人工裁決，"
            "與主題怎麼切無關")
        self.assertIn("topic_runs", joined, "應該有查下游 run")


class FinalizableStatusSingleSourceTests(unittest.TestCase):
    """可 finalize 的狀態集合只能有一份定義（原本三處各寫一份）。"""

    def test_completed_is_finalizable(self):
        """completed 必須可 finalize——否則改選候選會被 409 擋下。"""
        from backend.app.clustering.runner import FINALIZABLE_STATUSES

        self.assertIn("completed", FINALIZABLE_STATUSES)
        self.assertIn("needs_review", FINALIZABLE_STATUSES)

    def test_api_guard_uses_shared_constant(self):
        """API 層的 409 守門不得自己寫一份狀態集合。"""
        import inspect
        from backend.app.api import clustering as api

        src = inspect.getsource(api)
        self.assertIn("FINALIZABLE_STATUSES", src)
        self.assertNotIn(
            '{"needs_review", "failed"}', src,
            "API 層仍自寫一份可 finalize 狀態——與 runner 兩處各自維護會再度分岔")


class FrontendSwitchUiTests(unittest.TestCase):
    """前端要能真的按下去換方案。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.app.main import app

        cls.html = TestClient(app).get("/").text

    def test_stale_copy_removed(self):
        """舊文案「請按上方『分類』重跑一次」必須拿掉——那條路走 incremental，做不到換方案。"""
        self.assertNotIn("要改用其他分法，請按上方「分類」重跑一次", self.html)

    def test_switch_button_exists(self):
        """已定案時點其他候選要出現「改用這組分類」。"""
        self.assertIn("改用這組分類", self.html)

    def test_pick_differs_helper_defined(self):
        """有判斷「選的是否不同於採用中那組」的函式，否則鈕永遠停用或永遠可按。"""
        self.assertRegex(self.html, r"function\s+candidatePickIsDifferent\s*\(")

    def test_switch_warns_before_submit(self):
        """切換前要警告會清掉主題名稱與合併歷史。"""
        import re
        body = re.search(r"async function submitFinalizeCandidate\s*\([^)]*\)\s*\{(.*?)\n\}",
                         self.html, re.S)
        self.assertIsNotNone(body)
        self.assertIn("confirm(", body.group(1))


class LabelCharLimitTests(unittest.TestCase):
    """主題標籤字數（2026-07-28 使用者定：建議 4 到 6、硬上限 10）。"""

    def test_label_limits(self):
        from backend.app.clustering import workspace_service as ws

        self.assertEqual(ws.LABEL_SUGGESTED_RANGE, "4 到 6")
        self.assertEqual(ws.LABEL_MAX_CHARS, 10)

    def test_both_channels_share_the_limit(self):
        """技術與功效共用同一組常數——topic_labeling_payload 不分通道取值。"""
        import inspect
        from backend.app.clustering import workspace_service as ws

        src = inspect.getsource(ws.topic_labeling_payload)
        self.assertNotIn(
            "effect_summary", src,
            "標籤字數不得依通道分岔——兩邊必須共用 LABEL_* 常數")


class ApiAllowsRefinalizeTests(unittest.TestCase):
    """API 層要能被重複呼叫（前端『改用此分類』鈕會打同一支）。"""

    def test_finalize_endpoint_accepts_repeat_call(self):
        """finalize 端點沒有「只能呼叫一次」的限制。"""
        from backend.app.api import clustering as api

        import inspect
        src = inspect.getsource(api)
        self.assertNotIn(
            "already finalized", src,
            "API 層不應擋重複 finalize")


if __name__ == "__main__":
    unittest.main()
