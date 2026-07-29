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


class AssignmentsCarrySourceFieldTests(unittest.TestCase):
    """assignment 必須在**合併時**打上 source_field（2026-07-29 實機回報）。

    ## 症狀（使用者截圖）

    主題分類統計表／機會四象限／痛點四象限：13 個主題全列出來了，但
    `patent_count`／`applicant_count` **全是 0**，而分類區明明顯示
    「拉繩捲輪回收機構 15、阻力調節拉繩機構 8…」有件數。

    ## 根因＝本檔前一段修正的副作用

    `cluster_data_loader` 的契約明載 **assignment 只含 topic_code／patent_id**
    （DB 的 topic_assignments 表也確實沒有 source_field 欄）。而複合鍵歸戶
    在「assignment 無 source_field」時走保守分支「同 code 唯一才歸戶，否則不猜」
    ——兩通道 code 完全重疊（T001-T005 各有兩個），於是**全部跳過**。

    當初寫那個 fallback 時假設「無 source_field ＝舊資料的少數情況」，
    實際上現行資料流**本來就不帶**——假設錯了，且錯得靜默（件數變 0 不報錯）。

    ## 正確修法

    **在來源分好**：`_merge_cluster_channels` 串接兩通道時，為每個 assignment
    打上它來自哪個通道。歸戶端因此永遠拿得到 source_field，
    「不猜」的保守分支退回真正的邊界情況（單通道舊資料）。
    """

    def test_merged_assignments_carry_source_field(self):
        """合併後每筆 assignment 都帶來源通道。

        ⚠ 從**真正的 DB 載入層**（`load_cluster_workspace_data`）往上跑，
        不 mock `_load_report_cluster_data`——標記點就在它裡面，mock 掉就
        測不到真正該驗的事（本測試初版即如此，假失敗）。
        """
        from unittest import mock

        from backend.app.worker import handlers

        def fake_load_ws(workspace_id, source_field, conn):
            n = 5 if source_field == TECH else 8
            return {
                "topics": [{"topic_code": f"T{i:03d}", "label": f"L{i}",
                            "source_field": source_field} for i in range(1, n + 1)],
                "assignments": [{"topic_code": f"T{i:03d}", "patent_id": 100 + i}
                                for i in range(1, n + 1)],
                "normalized_applicants": [],
                "top_applicants_ws": [],
            }

        with mock.patch("backend.app.reports.cluster_data_loader.load_cluster_workspace_data",
                        fake_load_ws), \
                mock.patch("psycopg.connect"):
            merged = handlers._merge_cluster_channels(1, [TECH, EFFECT], None)

        self.assertIsNotNone(merged)
        by_src: dict[str, int] = {}
        for a in merged["assignments"]:
            self.assertIn("source_field", a,
                          "assignment 沒有 source_field——歸戶端無從分辨兩通道")
            by_src[a["source_field"]] = by_src.get(a["source_field"], 0) + 1
        self.assertEqual(by_src, {TECH: 5, EFFECT: 8})
        # 件數必須真的算出來（實機症狀的直接驗證）
        rows = merged["topic_rows"]
        self.assertEqual(len(rows), 13, f"13 個主題應有 13 列，實得 {len(rows)}")

    def test_counts_survive_overlapping_codes(self):
        """端到端：兩通道 code 重疊時，件數不得歸零（實機症狀的回歸測試）。"""
        from backend.app.reports.cluster_analytics import build_topic_effect_table

        topics = (
            [{"topic_code": f"T{i:03d}", "label": f"技術{i}", "source_field": TECH}
             for i in range(1, 6)]
            + [{"topic_code": f"T{i:03d}", "label": f"功效{i}", "source_field": EFFECT}
               for i in range(1, 9)]
        )
        assignments = (
            [{"topic_code": f"T{i:03d}", "patent_id": 100 + i, "source_field": TECH}
             for i in range(1, 6)]
            + [{"topic_code": f"T{i:03d}", "patent_id": 200 + i, "source_field": EFFECT}
               for i in range(1, 9)]
        )
        rows = build_topic_effect_table(topics, assignments, [])
        self.assertEqual(len(rows), 13)
        zero = [r for r in rows if r["patent_count"] == 0]
        self.assertEqual(zero, [], f"{len(zero)} 個主題件數為 0——歸戶又斷了")

    def test_single_channel_path_also_stamps(self):
        """單通道路徑（payload 指定 source_field）也要標記——兩條路徑同一口徑。

        單通道時 code 天然唯一，「同 code 唯一才歸戶」的 fallback 恰好能過關；
        但那是**靠運氣**：一旦該通道自己出現重複 code（合併／增量後可能發生），
        就再次靜默歸零。兩條路徑都標記，歸戶端才永遠不必猜。
        """
        from unittest import mock

        from backend.app.worker import handlers

        captured = {}

        def fake_load_ws(workspace_id, source_field, conn):
            return {
                "topics": [{"topic_code": "T001", "label": "L", "source_field": source_field}],
                "assignments": [{"topic_code": "T001", "patent_id": 1}],
                "normalized_applicants": [],
                "top_applicants_ws": [],
            }

        def spy_build(topics, assignments, applicants):
            captured["assignments"] = assignments
            return []

        with mock.patch("backend.app.reports.cluster_data_loader.load_cluster_workspace_data",
                        fake_load_ws), \
                mock.patch("backend.app.reports.cluster_analytics.build_topic_effect_table",
                           spy_build), \
                mock.patch("psycopg.connect"):
            handlers._load_report_cluster_data(1, TECH)

        self.assertTrue(captured.get("assignments"), "沒取到 assignments")
        for a in captured["assignments"]:
            self.assertEqual(a.get("source_field"), TECH,
                             "單通道路徑的 assignment 也要帶 source_field")


class ConcentrationColumnsTests(unittest.TestCase):
    """主題統計表加集中度兩欄（2026-07-29 使用者定案）。

    ## 為何要兩欄而非一欄

    使用者原話：「兩欄都要」。實測他的資料，只看單一指標會誤判：

    | 主題 | 前三大占比 | 最大一家 | 態勢 |
    |---|---|---|---|
    | 風阻磁阻調節 | 83% | 33% | 集中但**三家均分**，還有空間 |
    | 馬達捲繩自鎖 | 88% | **62%** | 集中且**一家獨大**，要迴避設計 |

    只看「前三大占比」兩者都是 8x%，看起來一樣——但競爭態勢完全相反。

    ## 同時移除

    - `leading_applicant_count`／`leading_applicants_involved`（龍頭涉入兩欄）
      ——使用者：「有前三大申請人好像就不用龍頭涉入了」
    - `topic_code` 改為**不顯示**（機制仍需它識別，走 DATA_TABLE_EXCLUDED_COLUMNS）
    """

    def test_top3_share_and_max_share(self):
        from backend.app.reports.cluster_analytics import build_topic_effect_table

        topics = [{"topic_code": "T001", "label": "L", "source_field": TECH}]
        # 5 家：3、2、2、1、1 共 9 件 → 前三大 7/9=78%、最大 3/9=33%
        assignments = [{"topic_code": "T001", "patent_id": i, "source_field": TECH}
                       for i in range(1, 10)]
        apps = ([{"patent_id": i, "applicant_name": "A"} for i in (1, 2, 3)]
                + [{"patent_id": i, "applicant_name": "B"} for i in (4, 5)]
                + [{"patent_id": i, "applicant_name": "C"} for i in (6, 7)]
                + [{"patent_id": 8, "applicant_name": "D"},
                   {"patent_id": 9, "applicant_name": "E"}])
        row = build_topic_effect_table(topics, assignments, apps)[0]
        self.assertEqual(row["top3_share"], 78, f"前三大占比錯：{row['top3_share']}")
        self.assertEqual(row["max_share"], 33, f"最大一家錯：{row['max_share']}")

    def test_zero_patents_no_division_error(self):
        """件數 0 的主題不得除以零。"""
        from backend.app.reports.cluster_analytics import build_topic_effect_table

        row = build_topic_effect_table(
            [{"topic_code": "T001", "label": "L", "source_field": TECH}], [], [])[0]
        self.assertEqual(row["top3_share"], 0)
        self.assertEqual(row["max_share"], 0)

    def test_leading_columns_removed_from_display(self):
        """龍頭涉入兩欄不再顯示（使用者定案移除）。"""
        from backend.app.reports.chart_runner import DATA_TABLE_EXCLUDED_COLUMNS

        excluded = DATA_TABLE_EXCLUDED_COLUMNS.get("cluster_topic_table", ())
        for col in ("topic_code", "leading_applicant_count", "leading_applicants_involved"):
            with self.subTest(col=col):
                self.assertIn(col, excluded, f"{col} 應從顯示中排除（資料仍保留）")
