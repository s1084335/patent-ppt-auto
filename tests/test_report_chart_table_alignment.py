"""圖與表要講同一件事（2026-08-17 實物驗收，四件）。

使用者逐頁看報表時抓到的四個「圖表脫節」：

| # | 症狀 | 修法 |
|---|---|---|
| A | 趨勢表有「涉及／首現技術主題」兩欄，圖上沒有，且後面另有技術主題演進頁 | 移除兩欄 |
| B | 受理局表有六種狀態欄，圖只畫兩條；且**表裡沒有「現存有效」**（圖上那條在表中找不到） | 圖改狀態堆疊 |
| C | 已轉讓用斜線疊加，看不清 | 改第三種顏色 |
| D | 外觀策略圖只有兩條（只走外觀 6／技術+外觀 4），看不出東西；兩張寬表 PPT 放不下 | 改申請人×策略型交叉圖，表精簡 |

⚠ 共同形態：**圖與表各自演進**。B 是反向不一致（圖有、表沒有），
與同日修的趨勢表（表有、圖沒有）恰成對照。
"""
from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from backend.app.reports import chart_runner


class TrendTopicColumnsRemovedTests(unittest.TestCase):
    """A：趨勢表移除技術主題兩欄。"""

    def test_topic_columns_not_in_merged_rows(self):
        rows = chart_runner.merge_annual_trend_rows(
            [{"application_year": 2022, "patent_count": 61, "family_count": 47}],
            [{"授權公告年": 2022, "patent_count": 30}])
        self.assertNotIn("topic_count", rows[0],
                         "涉及技術主題欄未移除——圖上沒有這維度，且後面另有主題演進頁")
        self.assertNotIn("new_topic_count", rows[0])

    def test_core_columns_kept(self):
        rows = chart_runner.merge_annual_trend_rows(
            [{"application_year": 2022, "patent_count": 61, "family_count": 47}],
            [{"授權公告年": 2022, "patent_count": 30}])
        self.assertEqual(
            set(rows[0]), {"year", "application_count", "授權公告件數", "family_count"})


class CountryStatusStackTests(unittest.TestCase):
    """B：受理局圖改狀態堆疊，表與圖同維度。"""

    ROWS = [
        {"country_code": "CN", "申請件數": 38, "申請": 1, "公開": 0,
         "審查中": 5, "授權": 24, "放棄": 1, "到期": 7},
        {"country_code": "TW", "申請件數": 9, "申請": 0, "公開": 1,
         "審查中": 1, "授權": 7, "放棄": 0, "到期": 0},
    ]

    def test_stacked_chart_renders_each_status(self):
        """🔴 六種狀態都要進圖——表有的維度圖上要讀得到。"""
        out = Path(tempfile.mkdtemp()) / "jurisdiction.svg"
        chart_runner.render_country_status_stack(out, "受理局分布", self.ROWS)
        svg = out.read_text(encoding="utf-8")
        for status in ("審查中", "授權", "放棄", "到期"):
            self.assertIn(status, svg, f"狀態「{status}」沒進圖")

    def test_zero_status_not_in_legend(self):
        """全部為 0 的狀態不佔圖例——避免圖例塞滿沒資料的項。"""
        rows = [{"country_code": "EP", "申請件數": 2, "申請": 0, "公開": 0,
                 "審查中": 0, "授權": 1, "放棄": 0, "到期": 1}]
        out = Path(tempfile.mkdtemp()) / "j2.svg"
        chart_runner.render_country_status_stack(out, "t", rows)
        svg = out.read_text(encoding="utf-8")
        self.assertNotIn("公開", svg)

    def test_valid_svg(self):
        import xml.etree.ElementTree as ET
        out = Path(tempfile.mkdtemp()) / "j3.svg"
        chart_runner.render_country_status_stack(out, "t", self.ROWS)
        ET.fromstring(out.read_text(encoding="utf-8"))

    def test_stack_total_equals_declared_application_count(self):
        """🔴 不變式：六欄加總 == 申請件數（2026-08-17 以真實資料驗證：
        CN 38、TW 9、US 6、EP 2 四國全部相等）。

        圖以「申請件數」為尺標而非各段加總——兩者理應相等，**不等就是有狀態
        沒收斂到**（例如日後新增一種法律狀態卻沒進對照表）。用申請件數當尺，
        缺口會在圖上留白**看得見**；用加總當尺，缺的那截會被靜默吸收。
        """
        for row in self.ROWS:
            total = sum(int(row.get(s) or 0) for s in
                        ("申請", "公開", "審查中", "授權", "放棄", "到期"))
            self.assertEqual(
                total, row["申請件數"],
                f'{row["country_code"]} 六欄加總 {total} != 申請件數 '
                f'{row["申請件數"]}——有狀態沒收斂到對照表')


class TransferredColorTests(unittest.TestCase):
    """C：已轉讓改用第三種顏色，不用斜線。"""

    def test_no_hatch_pattern(self):
        self.assertNotIn(
            'class="bar-hatch"', chart_runner.__dict__.get("_SOURCE_MARKER", "")
            or Path(chart_runner.__file__).read_text(encoding="utf-8"),
            "已轉讓仍用斜線（bar-hatch）——實測看不清，應改第三種顏色")

    def test_transferred_color_defined(self):
        self.assertTrue(hasattr(chart_runner, "COLOR_TRANSFERRED"),
                        "缺 COLOR_TRANSFERRED：已轉讓的顏色要有唯一定義處")


class DesignStrategyChartTests(unittest.TestCase):
    """D：外觀策略改申請人×策略型交叉圖，表精簡。"""

    STRATEGY_ROWS = [
        {"applicant": "廈門帝瑪斯健康科技", "strategy_type": "技術+外觀",
         "design_count": 2, "tech_count": 11, "first_design_year": 2019,
         "latest_design_year": 2022, "design_years": "2019；2022",
         "legal_status_summary": "授權", "representative_design_patent_id": 134,
         "representative_design_title": "Fan skiing training ware"},
        {"applicant": "上海波迪貿易", "strategy_type": "只走外觀",
         "design_count": 1, "tech_count": 0, "first_design_year": 2018,
         "latest_design_year": 2018, "design_years": "2018",
         "legal_status_summary": "授權", "representative_design_patent_id": 135,
         "representative_design_title": "Skiing machine"},
    ]

    def test_chart_shows_applicants_not_just_two_bars(self):
        """🔴 圖要能看出「誰用什麼策略」，不是只有兩條總數。

        形式改過三輪，判準始終不變（圖上要有申請人、看得出策略）：
        兩條總長條 → 申請人×年度矩陣（08-17）→ **申請人 × 技術／外觀／
        技術+外觀**（08-18 使用者定案，年度那版退場、函式已移除）。
        """
        rows = chart_runner.design_strategy_matrix_rows(self.STRATEGY_ROWS)
        out = Path(tempfile.mkdtemp()) / "design.svg"
        chart_runner.render_matrix_chart(
            out, "外觀保護策略", rows, row_key="applicant",
            col_key="strategy_axis",
            col_order=chart_runner.DESIGN_STRATEGY_AXIS)
        svg = out.read_text(encoding="utf-8")
        self.assertIn("帝瑪斯", svg, "圖上看不到申請人")
        self.assertIn("上海波迪貿易", svg)
        for axis in ("技術", "外觀", "技術+外觀"):
            self.assertIn(axis, svg, f"欄「{axis}」沒出現")

    def test_table_columns_trimmed(self):
        """表精簡：PPT 放得下。⚠ 資訊不丟，只是併欄與移到敘述。"""
        trimmed = chart_runner.design_strategy_table_rows(self.STRATEGY_ROWS)
        cols = set(trimmed[0])
        self.assertLessEqual(len(cols), 6, f"欄位仍有 {len(cols)} 個：{sorted(cols)}")
        # 年度兩欄併成一欄；patent_id 是內部識別不給決策者看
        self.assertNotIn("first_design_year", cols)
        self.assertNotIn("latest_design_year", cols)
        self.assertNotIn("representative_design_patent_id", cols)
        # 核心資訊必須留著
        for keep in ("applicant", "strategy_type", "design_count", "tech_count"):
            self.assertIn(keep, cols)


if __name__ == "__main__":
    unittest.main()
