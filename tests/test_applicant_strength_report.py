"""四面向聚合報表（KP 象限的引擎端配套；2026-08-07 使用者：「所以這五項也不會有報表嗎」）。

規格（`ppt-skill-creator-prompt.md`「引擎端配套」）：
> KP 象限的兩軸資料：per-applicant「布局地區數／技術主題數／家族數」聚合報表

⚠ 先前只做成 `content_blocks` 的 Python 函式——CLI（P2）與畫圖引擎讀的是
`report_data.json`，拿不到函式輸出，KP 象限（範例滑雪機 V2 p7）因此畫不出來。
本報表把同一份計算接進報表管線：**計算仍在 content_blocks 唯一定義處**，
這裡只負責取資料與落進 chart_rows。

⚠ 這是資料層，**不是簡報上的表格**——四面向在簡報上的形狀是象限座標
（橫軸國數／縱軸主題數／泡泡家族數）與數字卡，見 content_standard.md。
"""
from __future__ import annotations

import unittest


class ReportRegistrationTests(unittest.TestCase):
    def test_definition_registered_with_cluster_type(self):
        """走 cluster 型別：資料要 join 分群指派，不是單表 SQL 能出的。"""
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        d = REPORT_DEFINITIONS["applicant_strength_profile"]
        self.assertEqual(d.report_type, "cluster")
        # ⚠ 2026-08-10 改名：原「申請人四面向」與實際畫出來的**泡泡象限圖**
        # 名實不符。2026-08-07 定案原文：「四面向就是這張圖的座標」——四面向是
        # 資料維度，圖本身是範例（滑雪機 V2 p7）的「Key Players 競爭定位」。
        self.assertEqual(d.label_zh, "Key Players 競爭定位")

    def test_section_registry_covers_it(self):
        """新報表必須掛進某個 section spec（否則 registry 覆蓋測試會紅）。"""
        from backend.app.reports.chart_runner import SECTION_SPECS

        names = {name for spec in SECTION_SPECS for name in spec.reports}
        self.assertIn("applicant_strength_profile", names)


class RowsShapeTests(unittest.TestCase):
    """rows 形狀＝KP 象限兩軸＋泡泡＋定位所需欄位。"""

    ROWS = [
        {"applicant_display_name": "帝瑪斯", "patent_id": 1, "country_code": "CN",
         "family_id": "F1", "legal_status": "授权", "patent_type": "P",
         "document_kind": "A", "topic_key": "T001", "ipc_subclass": "A63B"},
        {"applicant_display_name": "帝瑪斯", "patent_id": 2, "country_code": "US",
         "family_id": "F2", "legal_status": "审查中", "patent_type": "P",
         "document_kind": "A", "topic_key": "T002", "ipc_subclass": "F03G"},
        {"applicant_display_name": "扭矩", "patent_id": 3, "country_code": "EP",
         "family_id": "F3", "legal_status": "到期", "patent_type": "P",
         "document_kind": "A", "topic_key": "T001", "ipc_subclass": "A63B"},
    ]

    def test_rows_carry_quadrant_axes(self):
        from backend.app.reports.chart_runner import applicant_strength_rows

        rows = applicant_strength_rows(self.ROWS, ranking=["帝瑪斯", "扭矩"])
        first = rows[0]
        for key in ("applicant_display_name", "patent_count", "family_count",
                    "country_count", "topic_count", "ipc_subclass_count",
                    "granted_count", "dead_count", "kind_summary"):
            self.assertIn(key, first, f"缺 KP 象限需要的欄位 {key}")
        self.assertEqual(first["applicant_display_name"], "帝瑪斯")
        self.assertEqual((first["country_count"], first["topic_count"],
                          first["family_count"]), (2, 2, 2))

    def test_kind_summary_is_display_string(self):
        """種類三分在報表列上是可直接顯示的字串（dict 進不了表格欄）。"""
        from backend.app.reports.chart_runner import applicant_strength_rows

        rows = applicant_strength_rows(self.ROWS, ranking=["帝瑪斯"])
        self.assertIsInstance(rows[0]["kind_summary"], str)
        self.assertIn("發明", rows[0]["kind_summary"])

    def test_follows_ranking_order(self):
        """名單與順序以排名頁為準（2026-08-07 定案），本報表不另排一次。"""
        from backend.app.reports.chart_runner import applicant_strength_rows

        rows = applicant_strength_rows(self.ROWS, ranking=["扭矩", "帝瑪斯"])
        self.assertEqual([r["applicant_display_name"] for r in rows], ["扭矩", "帝瑪斯"])

    def test_reuses_content_blocks_definition(self):
        """計算不得在此重寫——必須呼叫 content_blocks（唯一定義處）。"""
        import inspect

        from backend.app.reports import chart_runner

        src = inspect.getsource(chart_runner.applicant_strength_rows)
        self.assertIn("key_player_profiles", src)
        self.assertNotIn("TRAJECTORY_MIN_YEARS", src)


if __name__ == "__main__":
    unittest.main()


class SourceScopeTests(unittest.TestCase):
    """🔴 2026-08-07 真資料抓到：來源列母體要是**該 workspace 全部專利**，
    不是「有主題指派的那些」——布局量（件／族／國）要算設計案等未分群件，
    否則與排名頁 55 件口徑對不上（實測曾晴少 1、帝瑪斯少 2）。
    主題數則自然只計有指派者（LEFT JOIN 取不到即 0）。"""

    def test_query_scopes_by_workspace_membership(self):
        import inspect

        from backend.app.reports import cluster_data_loader

        src = inspect.getsource(cluster_data_loader.load_cluster_workspace_data)
        idx = src.index("四面向的來源列")
        block = src[idx:idx + 1600]
        self.assertIn("app_layer.workspaces", block, "四面向來源未以 workspace 成員圈定")
        self.assertNotIn("ANY(%s)', \n            (source_field, workspace_id, all_patent_ids",
                         block)
        self.assertIn("LEFT JOIN LATERAL", block, "主題應 LEFT JOIN（未分群件也要進來）")
