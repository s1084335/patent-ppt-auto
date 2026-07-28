"""兩通道共用 topic_code 命名空間導致整批覆蓋（2026-07-28 驗收 agent 發現）。

## 症狀

Codex 做完「分群報表技術／功效切換」後驗收，實測 `build_topic_effect_table`：

    輸入 4 個主題（技術 T001/T002 ＋ 功效 T001/T002）
    輸出 2 列 —— 只剩功效，技術通道全滅

## 根因

`topic_code` 由 `clustering/runner.py:1018` 產出 `f"T{position:03d}"`，
**兩個通道各自從 T001 開始編號**，共用同一命名空間。

`cluster_analytics.py` 有三處以 topic_code 當 dict key：

| 行 | 結構 | 後果 |
|---|---|---|
| 51-53 | `topic_patents: dict[str, set]` | 兩通道同 code 的專利集合被**合併** |
| 72 | `topic_map = {t["topic_code"]: t}` | 後出現的通道**整批覆蓋**前者 |
| 75 | `for tc in sorted(topic_patents.keys())` | 只輸出去重後的 code 數 |

worker 端 `handlers.py:311` 把兩通道的 `topic_rows` 直接串接，正是觸發點。

## 為何一直沒被發現

分群報表 2026-07-28 上午才接通（`4d5a2d0`），接通後只驗了「報表產得出來」，
沒驗「兩個通道的資料都在」。技術通道被蓋掉後，報表仍然正常產出、不報錯——
又一次靜默失敗。Codex 的切換鈕正好踩到它：切到「技術」永遠是空的。

## 定案

歸戶鍵改 **(topic_code, source_field) 複合鍵**。`source_field` 缺漏時退回空字串，
維持舊資料（單通道時期）可讀。

⚠ 不改 `topic_code` 的產生規則——那會影響 artifact、增量分群、合併/拆分等
一整條線；此處只修「報表層把兩通道當成同一組」這個錯誤。
"""
from __future__ import annotations

import unittest


TECH = "wips_independent_claims"
EFFECT = "effect_summary"


def _topics():
    """兩通道各兩個主題，code 刻意相同——這正是實機的樣子。"""
    return [
        {"topic_code": "T001", "label": "技術A", "source_field": TECH},
        {"topic_code": "T002", "label": "技術B", "source_field": TECH},
        {"topic_code": "T001", "label": "功效A", "source_field": EFFECT},
        {"topic_code": "T002", "label": "功效B", "source_field": EFFECT},
    ]


class TopicNamespaceTests(unittest.TestCase):
    """兩通道的主題不得互相覆蓋。"""

    def test_both_channels_survive(self):
        from backend.app.reports.cluster_analytics import build_topic_effect_table

        rows = build_topic_effect_table(_topics(), [], [])
        self.assertEqual(len(rows), 4, f"4 個主題應出 4 列，實得 {len(rows)}")

        by_source: dict[str, list[str]] = {}
        for r in rows:
            by_source.setdefault(r["source_field"], []).append(r["label"])
        self.assertEqual(sorted(by_source), sorted([TECH, EFFECT]),
                         "兩個通道都要在")
        self.assertEqual(sorted(by_source[TECH]), ["技術A", "技術B"])
        self.assertEqual(sorted(by_source[EFFECT]), ["功效A", "功效B"])

    def test_patent_sets_not_merged(self):
        """同 code 不同通道的專利集合不得互相污染。

        技術 T001 有專利 1；功效 T001 有專利 2、3。
        修前兩者共用同一個 set，各自都會看到 3 筆。
        """
        from backend.app.reports.cluster_analytics import build_topic_effect_table

        topics = [
            {"topic_code": "T001", "label": "技術A", "source_field": TECH},
            {"topic_code": "T001", "label": "功效A", "source_field": EFFECT},
        ]
        assignments = [
            {"topic_code": "T001", "patent_id": 1, "source_field": TECH},
            {"topic_code": "T001", "patent_id": 2, "source_field": EFFECT},
            {"topic_code": "T001", "patent_id": 3, "source_field": EFFECT},
        ]
        rows = build_topic_effect_table(topics, assignments, [])
        counts = {r["source_field"]: r["patent_count"] for r in rows}
        self.assertEqual(counts.get(TECH), 1, "技術 T001 應只有 1 筆")
        self.assertEqual(counts.get(EFFECT), 2, "功效 T001 應有 2 筆")

    def test_legacy_rows_without_source_field_still_work(self):
        """舊資料（單通道時期，assignment 無 source_field）不得因改鍵而壞掉。"""
        from backend.app.reports.cluster_analytics import build_topic_effect_table

        topics = [{"topic_code": "T001", "label": "舊主題", "source_field": TECH}]
        assignments = [{"topic_code": "T001", "patent_id": 1}]  # 無 source_field
        rows = build_topic_effect_table(topics, assignments, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["patent_count"], 1,
                         "assignment 沒帶 source_field 時要能歸到唯一同 code 的主題")

    def test_ordering_is_deterministic(self):
        """輸出順序穩定：先技術後功效，各自依 topic_code。

        ⚠ 使用者定案「技術先、功效後」（chart_runner._source_segments 同口徑）。
        """
        from backend.app.reports.cluster_analytics import build_topic_effect_table

        rows = build_topic_effect_table(_topics(), [], [])
        self.assertEqual(
            [(r["source_field"], r["topic_code"]) for r in rows],
            [(TECH, "T001"), (TECH, "T002"), (EFFECT, "T001"), (EFFECT, "T002")],
        )


class MatrixBuildersTests(unittest.TestCase):
    """象限矩陣吃的是 `build_topic_effect_table` 的輸出，不自行歸戶。

    ⚠ 本測試初版誤以為兩支矩陣也吃原始 topics（簽名是
    `(topic_rows, top_applicants_ws)` 與 `(topic_rows, pain_data, x_median)`），
    寫成 3 個位置參數而 TypeError。改為驗真正該驗的事：
    **上游修好後，兩通道的主題都會流進矩陣**——即根源只有一處，不必分頭修。
    """

    def test_matrix_receives_both_channels_after_upstream_fix(self):
        from backend.app.reports.cluster_analytics import (
            build_opportunity_matrix,
            build_topic_effect_table,
        )

        topic_rows = build_topic_effect_table(_topics(), [], [])
        result = build_opportunity_matrix(topic_rows, [])
        points = result.get("points") or result.get("rows") or []
        self.assertEqual(len(points), 4,
                         f"上游給 4 列，矩陣應有 4 點，實得 {len(points)}")



class PptQuadrantPageTests(unittest.TestCase):
    """PPT 象限頁要走得到分群版型（2026-07-28 驗收 agent 4c）。

    ## 問題

    `pptSectionsForPage` 以 `keys.includes(section.report_key)` 精確比對，
    但後端只產**一個** cluster section（`report_key='cluster_topic_table'`，
    見 `chart_runner.py`），象限的圖表是該 section 底下的 **variants**。

    而 `_expand_ppt_pages_with_active_reports`（`api/reports.py`）會為
    `opportunity_quadrant`、`pain_point_quadrant` 各建一頁、`report_keys=[name]`。

    → 那兩頁 `pptSectionsForPage` 回 `[]` → `pptIsClusterPage` 為 false
    → 不進 `pptClusterSplitSlideHtml`，退回 `pptTableSlideHtml` 且無 section。

    **三個分群報表在報表頁都正常（`clusterReportViews` 從 variants 判斷），
    PPT 只有 `cluster_topic_table` 那頁正常——兩邊行為不一致。**
    """

    def test_ppt_sections_falls_back_to_variants(self):
        """找不到同名 section 時，要退而用 variants 認出分群 section。"""
        import re
        from pathlib import Path

        html = (Path(__file__).resolve().parents[1]
                / "backend/app/static/index.html").read_text(encoding="utf-8")
        m = re.search(r"function pptSectionsForPage\(content, page\) \{(.*?)\n\}",
                      html, re.S)
        self.assertIsNotNone(m, "找不到 pptSectionsForPage")
        body = m.group(1)
        self.assertIn("hasClusterVariant", body,
                      "象限頁的 report_key 在 sections 裡不存在，"
                      "必須用 variants 認——否則那兩頁永遠走不到分群版型")

if __name__ == "__main__":
    unittest.main()


class RowsTruncationTests(unittest.TestCase):
    """`rows[:20]` 會把後段通道整個切掉（2026-07-28 驗收 agent 4b）。

    ## 為何是 4b′ 修好後才浮現

    修前兩通道互相覆蓋，列數只有單通道的量，碰不到 20 的上限。
    改成複合鍵後列數變兩倍——技術主題 ≥ 20 個時，`rows[:20]` 取完技術就滿了，
    **功效通道一列都不剩**。

    ## 為何前端擋不住、還更隱蔽

    `sectionForReportView` 是 `rows.length ? rows : (section.rows || [])`：
    濾空後**退回未過濾的全部列** → 使用者按「功效」看到的是技術資料，
    畫面正常、無任何提示。比直接空白更難發現。

    ## 定案

    後端按 source_field 分組後**各取上限**，不是整體取前 N。
    上限沿用既有 20（對齊引擎數據卡），不改語意只改分配方式。
    """

    LIMIT = 20

    @staticmethod
    def _rows(tech_n, effect_n):
        rows = [{"topic_code": f"T{i:03d}", "source_field": TECH} for i in range(tech_n)]
        rows += [{"topic_code": f"T{i:03d}", "source_field": EFFECT} for i in range(effect_n)]
        return rows

    def test_each_channel_gets_its_own_quota(self):
        """技術 25 筆、功效 5 筆 → 兩通道都要有列，功效不得被切光。"""
        from backend.app.main import _limit_rows_per_source

        out = _limit_rows_per_source(self._rows(25, 5), self.LIMIT)
        by_src: dict[str, int] = {}
        for r in out:
            by_src[r["source_field"]] = by_src.get(r["source_field"], 0) + 1
        self.assertEqual(by_src.get(TECH), self.LIMIT, "技術取滿上限")
        self.assertEqual(by_src.get(EFFECT), 5, "功效 5 筆全留，不得因技術佔滿而消失")

    def test_under_limit_unchanged(self):
        """未超上限時原樣回傳，不改變既有行為。"""
        from backend.app.main import _limit_rows_per_source

        rows = self._rows(3, 2)
        self.assertEqual(_limit_rows_per_source(rows, self.LIMIT), rows)

    def test_rows_without_source_field(self):
        """非分群報表的 rows 沒有 source_field，走原本的整體上限。"""
        from backend.app.main import _limit_rows_per_source

        rows = [{"x": i} for i in range(30)]
        self.assertEqual(len(_limit_rows_per_source(rows, self.LIMIT)), self.LIMIT)

    def test_order_preserved_within_channel(self):
        """通道內順序不變（上游已排好技術先功效後）。"""
        from backend.app.main import _limit_rows_per_source

        out = _limit_rows_per_source(self._rows(25, 3), self.LIMIT)
        tech = [r["topic_code"] for r in out if r["source_field"] == TECH]
        self.assertEqual(tech, [f"T{i:03d}" for i in range(self.LIMIT)])
