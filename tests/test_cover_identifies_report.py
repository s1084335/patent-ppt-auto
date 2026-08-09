"""封面要讓主管一眼看出「這是哪一份報表」（2026-08-09 使用者要求）。

使用者原話：「簡報標題還是要有報表的名稱，不然主管看到第一時間也不知道是啥」。

現況根因：`_cover_title` 的第一順位本來就是 workspace 名稱（2026-07-31 定案
「封面頁主題要顯示成 workspace 名稱配上專利分析」），但 `report_data` 的
`parameters` 只帶 `workspace_id` 沒帶 `workspace_name`，於是每次都退到後面的
順位。⚠ 不是版面問題，是**資料沒帶到**。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_ppt_cover_identity", SKILL_DIR / "scripts" / "build_ppt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_ppt_cover_identity", module)
    spec.loader.exec_module(module)
    return module


bp = _load_builder()


class WorkspaceNameReachesParametersTests(unittest.TestCase):
    """產圖時要把 workspace 名稱寫進 parameters，封面才取得到。"""

    def test_name_resolved_from_id_when_not_given(self):
        from backend.app.reports import chart_runner as cr

        params = cr.build_workspace_identity(
            workspace_id=3, workspace_name=None, name_fetcher=lambda wid: "滑雪機")
        self.assertEqual(params["workspace_name"], "滑雪機")

    def test_explicit_name_wins_without_lookup(self):
        """呼叫端明確給名稱時不查 DB（人工指定優先於推導）。"""
        from backend.app.reports import chart_runner as cr

        def _never(_wid):
            raise AssertionError("已給名稱就不該再查")

        params = cr.build_workspace_identity(
            workspace_id=3, workspace_name="自訂名", name_fetcher=_never)
        self.assertEqual(params["workspace_name"], "自訂名")

    def test_missing_name_is_omitted_not_faked(self):
        """查不到就不放這個欄位——封面自有後續順位，不硬湊假名稱。"""
        from backend.app.reports import chart_runner as cr

        params = cr.build_workspace_identity(
            workspace_id=None, workspace_name=None, name_fetcher=lambda wid: None)
        self.assertNotIn("workspace_name", params)

    def test_lookup_failure_does_not_break_rendering(self):
        """⚠ 查名稱失敗不得讓整個產圖掛掉——它只是封面的一個字串。"""
        from backend.app.reports import chart_runner as cr

        def _boom(_wid):
            raise RuntimeError("DB 掛了")

        params = cr.build_workspace_identity(
            workspace_id=3, workspace_name=None, name_fetcher=_boom)
        self.assertNotIn("workspace_name", params)


class CoverShowsBothIdentityAndTopicTests(unittest.TestCase):
    """主標＝報表名稱（主管認得的識別），小字＝這份簡報在講什麼。"""

    def _data(self, **params):
        return {
            "parameters": params,
            "slide_plan": {"slides": [{"narrative": [
                {"text": "健身阻力訓練裝置專利布局與競爭分析（本批 55 件）"}]}]},
        }

    def test_title_uses_workspace_name(self):
        data = self._data(workspace_id=3, workspace_name="滑雪機")
        self.assertEqual(bp._cover_title(data, {}), "滑雪機專利分析")

    def test_eyebrow_carries_plan_headline(self):
        """⚠ 上方小字原本寫死「專利情報整合分析」，沒有任何資訊量。

        改放規劃寫出的主題句：主標回答「哪一份」，小字回答「在講什麼」。
        """
        data = self._data(workspace_id=3, workspace_name="滑雪機")
        self.assertEqual(bp._cover_eyebrow(data),
                         "健身阻力訓練裝置專利布局與競爭分析（本批 55 件）")

    def test_eyebrow_falls_back_when_no_plan(self):
        self.assertEqual(bp._cover_eyebrow({"parameters": {}}), bp.COVER_EYEBROW)

    def test_title_and_eyebrow_never_identical(self):
        """兩處撞名就等於白佔一行（2026-08-09 實機出現過）。"""
        data = self._data(workspace_id=3, workspace_name="滑雪機")
        self.assertNotEqual(bp._cover_title(data, {}), bp._cover_eyebrow(data))


if __name__ == "__main__":
    unittest.main()
