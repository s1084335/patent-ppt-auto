"""申請人口徑唯一定義處：主題表與排名表不得各用一套。

## 症狀（2026-08-20 割草機報表全覆蓋驗收實測）

同一份報表對同一件事給出兩種答案：

    技術 T001  申請人家數 17 家（主題表） vs 14 家（排名表口徑）
               最大一家   16%           vs 35%

16% 讀作「這個主題沒有壟斷者」，35% 讀作「一家佔三分之一」——**方向相反**。
30 個主題中 15 個受影響，且會傳到 `derive_thresholds` 的 max_share 中位數
與象限 Y 軸切線，於是行動建議也跟著變。

## 根因

`cluster_data_loader` 讀 `derived_layer.report_patent_base.applicant_display_name`
（**未歸集團**），而 `applicant_ranking`／`applicant_year_matrix`／設計保護策略
都走 `report_patent_applicant_expanded_with_groups.applicant_group_display_name`
（**已歸集團**）。集團正規化把「泉峰」旗下多個法人合為一家，兩邊自然不同。

⚠ 這是全域規則「同一份知識只能有一個定義處」的典型症狀：不一致本身不報錯，
症狀出現在完全無關的地方（象限落點），每次看起來都像新 bug。

## 契約

主題表的申請人口徑**跟隨該份報表的 `report_scope`**，與排名表同一個換算規則
（`report_engine.scoped_column` / `scoped_source_table`，唯一定義處）。
不是在 loader 裡寫死集團欄——那只是把不一致換個方向：company scope 的報表會
變成排名表用原始名、主題表用集團名。

⚠ 光改 loader 不夠：`report_scope` 必須從報表 job 的 payload 一路傳到 loader，
否則預設值會讓實機再度分岔（前端送的是 group）。
"""
from __future__ import annotations

import unittest
from unittest import mock

from backend.app.reports.cluster_data_loader import load_cluster_workspace_data

GROUP_COLUMN = "applicant_group_display_name"
GROUP_VIEW = "report_patent_applicant_expanded_with_groups"
PLAIN_COLUMN = "applicant_display_name"
PLAIN_VIEW = "derived_layer.report_patent_applicant_expanded"


class ApplicantCaliberTests(unittest.TestCase):
    """loader 取申請人時的來源與欄名由 report_scope 決定。"""

    def _state(self, topics: list[dict]) -> dict:
        return {"workspace_id": 1, "source_field": "claims",
                "run_id": 2, "state_run_id": 1, "topics": topics}

    def _conn(self, applicant_rows, top_rows):
        cur = mock.MagicMock()
        cur.execute.return_value = cur
        cur.fetchall.side_effect = [applicant_rows, top_rows]
        conn = mock.MagicMock()
        conn.cursor.return_value = cur
        return conn, cur

    def _run(self, report_scope: str = "group"):
        """跑一次 loader，回傳（結果, 送出去的 SQL 列表）。"""
        with mock.patch(
            "backend.app.reports.cluster_data_loader.PostgresTopicStateRepository"
        ) as repo:
            repo.return_value.get_latest_topic_state.return_value = self._state([
                {"topic_code": "T01", "label": "半導體製程", "status": "active",
                 "topic_kind": "model", "doc_count": 2, "patent_ids": [101, 102]},
            ])
            # loader 一律把名稱欄 alias 成 applicant_name，呼叫端不必知道走哪一欄。
            conn, cur = self._conn(
                [{"patent_id": 101, "applicant_name": "泉峰"},
                 {"patent_id": 102, "applicant_name": "泉峰"}],
                [{"applicant_name": "泉峰", "cnt": 2}],
            )
            result = load_cluster_workspace_data(
                1, "claims", conn, report_scope=report_scope)
        sqls = [str(call[0][0]) for call in cur.execute.call_args_list]
        return result, sqls

    def test_group_scope_reads_group_normalized_source(self):
        """🔴 group scope：申請人名稱取自集團歸戶的展開 VIEW。"""
        _, sqls = self._run("group")
        applicant_sql = sqls[0]
        self.assertIn(GROUP_COLUMN, applicant_sql,
                      f"仍用未歸集團的申請人名，與排名表對不起來：{applicant_sql}")
        self.assertIn(GROUP_VIEW, applicant_sql,
                      f"沒走集團歸戶的來源：{applicant_sql}")

    def test_top_applicants_query_uses_the_same_caliber(self):
        """前十大申請人同口徑——它是象限「龍頭涉入」的判定依據。"""
        _, sqls = self._run("group")
        top_sql = sqls[1]
        self.assertIn(GROUP_COLUMN, top_sql, f"前十大口徑不一致：{top_sql}")
        self.assertIn(GROUP_VIEW, top_sql, f"沒走集團歸戶的來源：{top_sql}")

    def test_company_scope_reads_plain_names(self):
        """company scope 不得偷偷升成集團——那只是把不一致換個方向。"""
        _, sqls = self._run("company")
        for sql in sqls[:2]:
            self.assertNotIn(GROUP_COLUMN, sql, f"company scope 卻用了集團欄：{sql}")
            self.assertIn(PLAIN_COLUMN, sql, sql)

    def test_applicant_source_is_the_expanded_view_not_patent_base(self):
        """⚠ 來源要與排名表同一張展開 VIEW（申請人粒度），不是專利粒度的 base。"""
        _, sqls = self._run("company")
        self.assertIn(PLAIN_VIEW, sqls[0], sqls[0])

    def test_top_applicants_order_is_deterministic_on_ties(self):
        """⚠ 同件數者要有次要排序，否則同一批資料重跑會得到不同的前十大。

        `top_applicants_ws` 是象限「龍頭涉入」的判定依據——第 10 名若有多家
        平手，誰進榜逐次變動，而報表看起來完全正常（實測割草機第 9／10 名
        各 4 件會互換）。
        """
        _, sqls = self._run("group")
        order_clause = sqls[1].split("ORDER BY", 1)[1]
        self.assertIn(",", order_clause,
                      f"只有單一排序鍵，平手時順序不確定：{order_clause}")
        self.assertIn(GROUP_COLUMN, order_clause, order_clause)

    def test_normalized_applicants_carry_the_scoped_names(self):
        """輸出的 applicant_name 是換算後的名稱——下游只認這一欄。"""
        result, _ = self._run("group")
        names = {a["applicant_name"] for a in result["normalized_applicants"]}
        self.assertEqual(names, {"泉峰"})
        self.assertEqual(result["top_applicants_ws"], ["泉峰"])


class ReportScopeThreadingTests(unittest.TestCase):
    """🔴 payload 的 report_scope 必須真的傳到 loader。

    只改 loader 而沒接線，預設值會讓實機維持舊行為——而測試全綠。
    """

    def test_payload_scope_reaches_the_loader(self):
        from backend.app.worker import handlers

        seen: dict = {}

        def _fake(workspace_id, source_field, report_scope="company"):
            seen[source_field] = report_scope
            return None

        with mock.patch.object(handlers, "_load_report_cluster_data", _fake):
            handlers._merge_cluster_channels(11, ["claims"], report_scope="group")
        self.assertEqual(seen, {"claims": "group"})

    def test_resolve_reads_scope_from_payload(self):
        from backend.app.worker import handlers

        seen: dict = {}

        def _fake(workspace_id, source_fields_, report_scope="company"):
            seen["scope"] = report_scope
            return None

        context = mock.MagicMock()
        with mock.patch.object(handlers, "_merge_cluster_channels", _fake):
            handlers._resolve_report_cluster_data(
                {"workspace_id": 11, "source_field": "wips_independent_claims",
                 "report_scope": "group"}, context)
        self.assertEqual(seen.get("scope"), "group",
                         "payload 的 report_scope 沒傳下去，實機會維持舊口徑")


if __name__ == "__main__":
    unittest.main()
