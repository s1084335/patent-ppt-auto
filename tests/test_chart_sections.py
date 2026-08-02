"""chart_runner 選擇性出圖（section registry）的單元測試。

不碰 DB：run_report 以 stub 取代，只驗 registry 覆蓋、選擇解析、
選擇性渲染的檔案輸出與唯一輸出資料夾行為。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.reports import chart_runner
from backend.app.reports.report_definitions import REPORT_DEFINITIONS


def fake_report(name: str, rows: list[dict]) -> dict:
    """依報表定義組出與 run_report 回傳同形狀的假結果。"""
    definition = REPORT_DEFINITIONS[name]
    return {
        "report_name": name,
        "label_zh": definition.label_zh,
        "label": definition.label,
        "report_type": definition.report_type,
        "row_count": len(rows),
        "rows": rows,
    }


class SectionRegistryTests(unittest.TestCase):
    """registry 完整性與 resolve_sections 的選擇規則。"""

    def test_removed_reports_have_no_sections_or_artifact_mapping(self):
        """已移出的 report keys 不得再有 section、SVG artifact mapping。"""
        removed = {"recent_assignee_year_matrix", "top_cited_patents", "company_rd_energy"}
        covered = {name for spec in chart_runner.SECTION_SPECS for name in spec.reports}
        artifact_reports = {
            name
            for report_names in chart_runner.CHART_FILE_REPORTS.values()
            for name in report_names
        }
        self.assertFalse(removed & covered)
        self.assertFalse(removed & artifact_reports)
        self.assertNotIn("recent_assignee_year_matrix.svg", chart_runner.CHART_FILE_REPORTS)
        for report_name in removed:
            with self.assertRaises(ValueError):
                chart_runner.resolve_sections([report_name])

    def test_deprecated_builders_removed_from_production_file(self):
        """top_cited_patents/company_rd_energy 的 chart builders 只能留在 archive。"""
        source = Path(chart_runner.__file__).read_text(encoding="utf-8")
        self.assertNotIn("def _build_top_cited_section", source)
        self.assertNotIn("def _build_rd_energy_section", source)
        self.assertNotIn('"top_cited_patents"', source)
        self.assertNotIn('"company_rd_energy"', source)

    def test_registry_covers_all_report_definitions(self):
        # 新報表加進引擎卻沒掛 section 時，這裡會 fail——強制選擇性出圖不漏報表。
        covered = {name for spec in chart_runner.SECTION_SPECS for name in spec.reports}
        self.assertTrue(covered.issuperset(set(REPORT_DEFINITIONS)),
                        "所有 REPORT_DEFINITIONS 必須有對應 section")
        self.assertIn("cluster_analytics", covered,
                      "cluster_analytics 為無對應報表的特殊 section")

    def test_resolve_none_returns_all_sections(self):
        self.assertEqual(chart_runner.resolve_sections(None), chart_runner.SECTION_SPECS)

    def test_resolve_subset_keeps_registry_order(self):
        specs = chart_runner.resolve_sections(["lifecycle", "country_distribution"])
        self.assertEqual([spec.key for spec in specs], ["country_map", "lifecycle"])

    def test_application_growth_section_removed(self):
        """年增率 section 已移除（2026-08-02 使用者定案）。

        小樣本下年增率被極低基期放大到失真——2022 年由前一年 1 件增至 15 件即
        1400%，該圖自己的判讀限制就寫著這句，沒有解釋力。報表與 PPT 都不再產。
        """
        keys = [spec.key for spec in chart_runner.resolve_sections(["application_trend"])]
        self.assertEqual(keys, ["annual_trend"])
        self.assertNotIn("application_growth", {spec.key for spec in chart_runner.SECTION_SPECS})

    def test_recent_assignee_ranking_is_not_used(self):
        """最新受讓人報表已定案不使用；不得要求 section registry 支援它。"""
        covered = {name for spec in chart_runner.SECTION_SPECS for name in spec.reports}
        self.assertNotIn("recent_assignee_ranking", covered)
        with self.assertRaises(ValueError):
            chart_runner.resolve_sections(["recent_assignee_ranking"])

    def test_resolve_family_reports_share_one_section(self):
        keys = [spec.key for spec in chart_runner.resolve_sections(["family_quality_detail"])]
        self.assertEqual(keys, ["family_layout"])

    def test_resolve_unknown_report_raises(self):
        with self.assertRaises(ValueError):
            chart_runner.resolve_sections(["no_such_report"])

    def test_resolve_empty_list_raises(self):
        with self.assertRaises(ValueError):
            chart_runner.resolve_sections([])


class CreateRunDirTests(unittest.TestCase):
    """同秒重複執行時輸出資料夾必須唯一，不可互寫。"""

    def test_same_second_gets_suffixed_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixed = mock.Mock()
            fixed.now.return_value.strftime.return_value = "20260716_120000"
            with mock.patch.object(chart_runner, "datetime", fixed):
                first = chart_runner._create_run_dir(Path(tmp), "report_trial_")
                second = chart_runner._create_run_dir(Path(tmp), "report_trial_")
            self.assertEqual(first.name, "report_trial_20260716_120000")
            self.assertEqual(second.name, "report_trial_20260716_120000_2")
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())


class MatrixChartTests(unittest.TestCase):
    """公司×國家交叉矩陣：前 N 大截取、每列一家公司不混算。"""

    def test_resolve_applicant_country_section(self):
        keys = [s.key for s in chart_runner.resolve_sections(["applicant_country_distribution"])]
        self.assertEqual(keys, ["applicant_country"])

    def test_owner_year_matrix_section_and_artifact_mapping(self):
        keys = [s.key for s in chart_runner.resolve_sections(["owner_year_matrix"])]
        self.assertEqual(keys, ["owner_year_matrix"])
        self.assertEqual(chart_runner.CHART_FILE_REPORTS["owner_year_matrix.svg"], ["owner_year_matrix"])

    def test_matrix_top_limit_and_per_company_cells(self):
        rows = []
        for i in range(25):
            rows.append({"applicant_display_name": f"Co{i:02d}", "country_code": "US", "patent_count": 100 - i})
            rows.append({"applicant_display_name": f"Co{i:02d}", "country_code": "CN", "patent_count": 10})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "matrix.svg"
            meta = chart_runner.render_matrix_chart(
                path, "測試矩陣", rows, row_key="applicant_display_name", col_key="country_code"
            )
            svg = path.read_text(encoding="utf-8")
        self.assertEqual(meta["rows_drawn"], 20)   # 前 20 大截取
        self.assertEqual(meta["rows_total"], 25)
        self.assertEqual(meta["cols"], ["US", "CN"])  # 欄按總量排序
        self.assertIn("Co00", svg)      # 總量最大的公司入圖
        self.assertNotIn("Co24", svg)   # 第 21 名之後被截掉
        self.assertIn(">100<", svg)     # 儲存格是單一公司的值（未跨公司加總）
        self.assertNotIn(">2290<", svg)  # 不出現全欄加總值——確保沒混算


class OpportunityQuadrantLegendTests(unittest.TestCase):
    """regression（2026-07-21）：「單一玩家壟斷型」曾因圖例探針座標錯置重複三次。
    2026-07-21 板狀改版註記：battle 標籤從散點圖例（■ 列）改為 2×2 格 header，
    斷言改為四個戰場語言在全圖各出現恰一次（空格也有 header，恆為四格四次）。"""

    def test_quadrant_color_legend_four_distinct(self):
        data = {
            "rows": [
                {"topic_code": "T01", "label": "散熱防塵", "patent_count": 11,
                 "applicant_count": 6, "leading_applicant_count": 2, "top_applicants": []},
                {"topic_code": "T02", "label": "速度控制", "patent_count": 45,
                 "applicant_count": 9, "leading_applicant_count": 1, "top_applicants": []},
            ],
            "patent_count_median": 20.0,
            "applicant_count_median": 5.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "opportunity.svg"
            chart_runner.render_opportunity_quadrant_svg(path, "機會四象限分析", data)
            svg = path.read_text(encoding="utf-8")
        for battle in ("必守核心戰場", "新興戰場（競爭者已進場）", "待釐清領域", "單一玩家壟斷型"):
            self.assertEqual(svg.count(battle), 1,
                             f"戰場語言「{battle}」應恰出現一次（格 header），實得 {svg.count(battle)}")
        # chip 文字用中文 label 非 code，格式「label 件/家」
        self.assertIn("散熱防塵 11/6", svg)
        self.assertNotIn(">T01 ", svg)


class DisplaySpecTests(unittest.TestCase):
    """2026-07-21 顯示規格（report-requirements.md「顯示規格」節）逐條契約。"""

    @staticmethod
    def _fake_ctx(tmp: str, reports: dict | None = None, ipc_levels=(4, 5)):
        """最小 ctx 替身：cluster/classification/year-matrix builder 只用這些屬性。"""
        from types import SimpleNamespace

        def report(key):
            return reports[key]

        return SimpleNamespace(
            run_dir=Path(tmp), chart_rows={}, sections=[], report=report,
            cluster_data=None, cluster_reports={}, ipc_levels=ipc_levels, cpc_levels=ipc_levels)

    def test_data_table_max_20_rows_no_full_expand(self):
        """數據區最多 20 筆＋總計列；不提供全量展開（2026-07-21 使用者補充），只註記共幾列。
        註記文案同步「保存前 20」定案（2026-07-21）：入庫同前 20、完整可由引擎重算。"""
        rows = [{"applicant_display_name": f"Co{i}", "patent_count": i} for i in range(25)]
        html = chart_runner._data_table_html(rows, "applicant_ranking")
        self.assertEqual(html.count("<tr>") - 1, 20 + 1,  # header 不算、含總計列
                         "數據區最多 20 筆＋總計列")
        self.assertNotIn("<details>", html, "不得提供顯示全部展開")
        self.assertIn("入庫同前 20，完整可重算", html)
        self.assertIn("總列數 25", html)  # 註記共幾列

    def test_cluster_card_three_tabs_board(self):
        """2026-07-21 二次修正（規格變更註記）：原 test_cluster_card_table_only_no_quadrant
        斷言「只留統計表、象限圖暫停展示」；板狀佈局完成後象限圖回歸 index——
        cluster 卡片＝三 tabs（主題統計表＋機會矩陣＋痛點矩陣）；topic rows 仍帶龍頭涉入。"""
        data = {
            "topics": [{"topic_code": "T01", "label": "散熱防塵", "source_field": "wips_independent_claims"}],
            "assignments": [{"topic_code": "T01", "patent_id": 1}],
            "normalized_applicants": [{"patent_id": 1, "applicant_name": "TSMC"}],
            "top_applicants_ws": ["TSMC"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._fake_ctx(tmp)
            ctx.cluster_data = data
            chart_runner._build_cluster_analytics_section(ctx)
            section = ctx.sections[0]
            files = [v["file"] for v in section["variants"]]
            labels = [v["label"] for v in section["variants"]]
            # ⚠ 2026-07-29：主題統計表**不再是圖表變體**（使用者「沒圖表用表格就好，
            # 現在跑兩個表格很難看」）——它與下方數據表是同一份資料，重複呈現。
            # 變體只剩真正的圖（機會板／痛點板）；統計表改由 section 的 rows 走數據表。
            self.assertEqual(
                files,
                # ⚠ 2026-07-30：variants[0]＝主題統計表的解讀掛點（無圖檔），
                # 見 TopicTableNarrativeTests——沒 variant 就掛不了 narrative。
                ["", "opportunity_quadrant.svg"],
                "⚠ 2026-07-29：統計表改走數據表、痛點板停產（市場線未實作），只剩機會板")
            self.assertEqual(labels, ["主題統計表", "機會矩陣"])
        # 龍頭欄不顯示；統計表以主題標籤、專利件數、申請人家數、集中度與前三大申請人為主。
        self.assertIn("top_applicants", ctx.chart_rows["cluster_topic_table"][0],
                      "統計列應保留前三大申請人")
        self.assertTrue(ctx.sections[0].get("rows"),
                        "section 未帶 rows，前端技術／功效切換會沒有資料可切")

    def test_classification_toggle_top20(self):
        """IPC/CPC 三次修正沿革（2026-07-21）：①初版 L4/L5 toggle 全列→②二次修正誤解
        「不收合」為 stacked 堆疊不切換＋每階 20→③三次修正定版：**恢復 L4/L5 切換鈕**
        （兩階對照是核心價值；「不收合」只指不用查看全部式展開，不禁 toggle），每階仍各截前 20。"""
        rows = [{"Orig. IPC(Main)": f"A{i:02d}B {i}/00", "patent_count": 30 - i} for i in range(25)]
        reports = {"ipc_main_distribution": {"label_zh": "IPC 分布", "rows": rows}}
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._fake_ctx(tmp, reports, ipc_levels=(4, 5))
            chart_runner._build_ipc_section(ctx)
            section = ctx.sections[0]
            svg_l4 = (Path(tmp) / "ipc_main_distribution_L4.svg").read_text(encoding="utf-8")
            svg_l5 = (Path(tmp) / "ipc_main_distribution_L5.svg").read_text(encoding="utf-8")
        self.assertFalse(section.get("stacked"), "stacked 取消：恢復 toggle 切換")
        self.assertEqual(len(section["variants"]), 2, "L4/L5 兩 variants＝render_index 出切換鈕")
        for level, svg in (("L4", svg_l4), ("L5", svg_l5)):
            drawn = svg.count('rx="2"')  # 每列一個 bar rect
            self.assertEqual(drawn, 20, f"IPC/CPC {level} 應截前 20 名，實得 {drawn}")

    def test_year_matrix_expand_label_wips_style(self):
        rows = [{"applicant_display_name": f"Co{i:02d}", "application_year": 2020, "patent_count": 30 - i}
                for i in range(15)]
        reports = {"applicant_year_matrix": {"label_zh": "申請人年度矩陣", "rows": rows}}
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._fake_ctx(tmp, reports)
            chart_runner._build_applicant_year_matrix_section(ctx)
            section = ctx.sections[0]
        self.assertTrue(str(section.get("more_label", "")).startswith("＋查看全部"),
                        f"收合鈕應為 WIPS 式「＋查看全部」，實得 {section.get('more_label')!r}")

    def test_ranking_limit_default_20(self):
        import inspect

        sig = inspect.signature(chart_runner.run_chart_trial)
        self.assertEqual(sig.parameters["ranking_limit"].default, 20,
                         "排名數據輸出預設應為前 20 名")

    def test_year_axis_capped_25(self):
        rows = [{"applicant_display_name": "Co", "application_year": 1990 + i, "patent_count": 1}
                for i in range(30)]
        layout = chart_runner.year_bubble_matrix_layout(rows, "applicant_display_name")
        self.assertEqual(len(layout["years"]), 25, "年份軸最多最新 25 年")
        self.assertEqual(layout["years"][-1], 2019)

    def test_segmented_bar_blue_segment_right_aligned(self):
        rows = [{"applicant_display_name": "Co", "patent_count": 10, "recent_assignee_count": 4}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seg.svg"
            chart_runner.render_segmented_bar_chart(
                path, "t", rows, "applicant_display_name",
                total_key="patent_count", segment_key="recent_assignee_count", segment_label="s")
            svg = path.read_text(encoding="utf-8")
        total = re.search(r'class="bar-total" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', svg)
        seg = re.search(r'class="bar-segment" x="([\d.]+)" y="[\d.]+" width="([\d.]+)"', svg)
        self.assertIsNotNone(total); self.assertIsNotNone(seg)
        total_end = float(total.group(1)) + float(total.group(2))
        seg_end = float(seg.group(1)) + float(seg.group(2))
        self.assertAlmostEqual(total_end, seg_end, delta=0.5, msg="藍色區段應靠灰色總量長條右端")


class BoardQuadrantTests(unittest.TestCase):
    """2026-07-21 二次修正：象限圖改板狀佈局（照範例頁 6/7）。
    斷言：chip 恰一次且落正確格、同列 chip x 區間不相交（結構性防重疊）、
    空格 placeholder、痛點 unknown 全在灰帶不落低、圖例三級色。"""

    # chip rect 屬性順序由 renderer 固定輸出，直接以 regex 取回
    _OPP_CHIP = re.compile(
        r'<rect class="chip" data-cell="(q[1-4])" data-topic="([^"]+)" '
        r'x="([\d.]+)" y="([\d.]+)" width="([\d.]+)"')
    _PAIN_CHIP = re.compile(
        r'<rect class="chip" data-band="(\w+)" data-col="(lo|hi)" data-topic="([^"]+)" '
        r'x="([\d.]+)" y="([\d.]+)" width="([\d.]+)"')

    @staticmethod
    def _assert_no_overlap(testcase, groups):
        """同一格（group key）內 y 相同的 chip，x 區間必須兩兩不相交。"""
        for key, chips in groups.items():
            by_row: dict[float, list[tuple[float, float]]] = {}
            for x, y, w in chips:
                by_row.setdefault(y, []).append((x, x + w))
            for y, spans in by_row.items():
                spans.sort()
                for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
                    testcase.assertLessEqual(
                        e1, s2 + 0.01, f"格 {key} 同列 y={y} chip 重疊：{spans}")

    def _opp_data(self):
        # q1（高密度×高廣度）塞 6 個 8 字 CJK label 強迫換行；q4 留空驗 placeholder
        q1 = [
            ("T11", "踏板履帶組裝機構", 45, 9, 3), ("T12", "鋸切支撐調整機構", 40, 8, 2),
            ("T13", "磁磚切割導水平台", 38, 7, 1), ("T14", "跑步機升降架體組", 30, 6, 1),
            ("T15", "橢圓機阻力調節器", 25, 5, 0), ("T16", "過載保護安全開關", 20, 5, 1),
        ]
        rows = [
            {"topic_code": tc, "label": lb, "patent_count": p, "applicant_count": a,
             "leading_applicant_count": lc, "top_applicants": []}
            for tc, lb, p, a, lc in q1
        ]
        rows.append({"topic_code": "T21", "label": "散熱防塵", "patent_count": 11,
                     "applicant_count": 6, "leading_applicant_count": 2, "top_applicants": []})
        rows.append({"topic_code": "T31", "label": "外觀設計", "patent_count": 3,
                     "applicant_count": 2, "leading_applicant_count": 0, "top_applicants": []})
        return {"rows": rows, "patent_count_median": 20.0, "applicant_count_median": 5.0}

    def test_opportunity_board_chips_once_correct_cell_no_overlap(self):
        data = self._opp_data()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "opportunity.svg"
            chart_runner.render_opportunity_quadrant_svg(path, "機會四象限分析", data)
            svg = path.read_text(encoding="utf-8")
        chips = self._OPP_CHIP.findall(svg)
        # 每個主題 chip 恰一次
        topics = [c[1] for c in chips]
        self.assertEqual(sorted(topics), sorted(r["topic_code"] for r in data["rows"]),
                         "每個主題應恰有一個 chip")
        # 依中位數落正確格：q1=高密度高廣度、q2=低密度高廣度、q3=低密度低廣度
        expected_cell = {r["topic_code"]: (
            ("q1" if r["applicant_count"] >= 5 else "q4") if r["patent_count"] >= 20
            else ("q2" if r["applicant_count"] >= 5 else "q3"))
            for r in data["rows"]}
        for cell, topic, *_ in chips:
            self.assertEqual(cell, expected_cell[topic], f"{topic} 應落 {expected_cell[topic]}")
        # 同格同列 x 區間不相交（q1 六個長 label 必換行，驗流式排列）
        groups: dict[str, list[tuple[float, float, float]]] = {}
        for cell, topic, x, y, w in chips:
            groups.setdefault(cell, []).append((float(x), float(y), float(w)))
        self.assertGreater(len({y for _, y, _ in groups["q1"]}), 1, "q1 應發生換行（多列）")
        self._assert_no_overlap(self, groups)

    def test_opportunity_board_empty_cell_placeholder_and_legend(self):
        data = self._opp_data()  # q4 無主題
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "opportunity.svg"
            chart_runner.render_opportunity_quadrant_svg(path, "機會四象限分析", data)
            svg = path.read_text(encoding="utf-8")
        self.assertIn("本案無此類", svg, "空格應顯示斜體說明")
        self.assertIn("色＝龍頭涉入", svg, "圖例缺「色＝龍頭涉入｜數字＝件/家」")
        for color in ("#DC2626", "#F59E0B", "#9CA3AF"):
            self.assertIn(color, svg, f"圖例缺龍頭涉入三級色 {color}")

    def test_pain_board_unknown_gray_band_not_low(self):
        rows = [
            {"topic_code": "P01", "label": "散熱防塵", "patent_count": 11, "severity": "high"},
            {"topic_code": "P02", "label": "能源管理", "patent_count": 2, "severity": "high"},
            {"topic_code": "P03", "label": "收納走線", "patent_count": 1, "severity": "medium"},
            {"topic_code": "P04", "label": "照明裝置", "patent_count": 4, "severity": "low"},
            {"topic_code": "P05", "label": "外觀設計", "patent_count": 11, "severity": "unknown"},
            {"topic_code": "P06", "label": "轉向機構", "patent_count": 8, "severity": "unknown"},
            {"topic_code": "P07", "label": "電控模組", "patent_count": 6, "severity": "unknown"},
        ]
        data = {"rows": rows, "x_median": 6.5}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pain.svg"
            chart_runner.render_pain_point_quadrant_svg(path, "痛點四象限分析", data)
            svg = path.read_text(encoding="utf-8")
        chips = self._PAIN_CHIP.findall(svg)
        self.assertEqual(sorted(c[2] for c in chips), sorted(r["topic_code"] for r in rows))
        band_by_topic = {r["topic_code"]: r["severity"] for r in rows}
        col_by_topic = {r["topic_code"]: ("hi" if r["patent_count"] >= 6.5 else "lo") for r in rows}
        for band, col, topic, *_ in chips:
            self.assertEqual(band, band_by_topic[topic], f"{topic} 應在 {band_by_topic[topic]} 帶")
            self.assertNotEqual((band_by_topic[topic], band), ("unknown", "low"),
                                "unknown 不得落低帶")
            self.assertEqual(col, col_by_topic[topic], f"{topic} 密度欄應為 {col_by_topic[topic]}")
        self.assertIn("待調查（灰帶）", svg, "缺獨立灰帶標示")
        # 四角象限名沿用
        for corner in ("研發優先缺口★", "必守核心→迴避設計", "nice-to-have→防禦即可", "競爭者已過度投入→選擇性"):
            self.assertIn(corner, svg, f"缺象限名 {corner}")
        groups: dict[tuple[str, str], list[tuple[float, float, float]]] = {}
        for band, col, topic, x, y, w in chips:
            groups.setdefault((band, col), []).append((float(x), float(y), float(w)))
        self._assert_no_overlap(self, groups)


class PersistenceTruncationTests(unittest.TestCase):
    """2026-07-21 定案修正：排名類「保存」也只留前 20、年度序列保存只留最新 25 年，
    主題相關數據不截（report-requirements.md「顯示規格」＋decisions.md 定案修正）。
    驗 report_data.json 落檔內容，不驗顯示（顯示已由 DisplaySpecTests 覆蓋）。"""

    @staticmethod
    def _stub_run_report(name, filters=None, limit=None, patent_ids=None):
        if name in ("ipc_main_distribution", "cpc_main_distribution"):
            # 2026-07-23 定案：分類來源改 Orig. Main，stub 的 row key 須跟著報表定義走
            source = REPORT_DEFINITIONS[name].columns[0]
            rows = [{source: f"A{i:02d}B {i}/00", "patent_count": 30 - i} for i in range(25)]
        elif name in ("application_trend", "publication_trend"):
            key = "application_year" if name == "application_trend" else "授權公告年"
            rows = [{key: 1990 + i, "patent_count": i + 1} for i in range(30)]  # 30 年
        elif name == "applicant_year_matrix":
            rows = [{"applicant_display_name": "Co", "application_year": 1990 + i, "patent_count": 1}
                    for i in range(30)]
        elif name == "applicant_country_distribution":
            rows = [{"applicant_display_name": f"Co{i:02d}", "country_code": "US", "patent_count": 30 - i}
                    for i in range(25)]
        else:
            rows = []
        if limit:
            rows = rows[:limit]
        return fake_report(name, rows)

    def _render(self, tmp, **kwargs):
        with mock.patch.object(chart_runner, "run_report", self._stub_run_report):
            result = chart_runner.run_chart_trial(output_dir=Path(tmp), **kwargs)
        run_dir = Path(result["output_dir"])
        return json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))

    def test_ranking_reports_persist_top20_with_rows_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = self._render(tmp, report_names=["ipc_main_distribution", "applicant_country_distribution"])
        for name in ("ipc_main_distribution", "applicant_country_distribution"):
            report = rd["reports"][name]
            self.assertLessEqual(len(report["rows"]), 20, f"{name} 入庫 rows 應 ≤20")
            self.assertEqual(report.get("rows_total"), 25, f"{name} 應保留截取前總數 rows_total")
        # IPC chart_rows（L4/L5 各階）同樣前 20＋總數
        for key in ("ipc_main_distribution_L4", "ipc_main_distribution_L5"):
            self.assertLessEqual(len(rd["chart_rows"][key]), 20, f"{key} 入庫應 ≤20")
            self.assertEqual(rd.get("chart_rows_total", {}).get(key), 25, f"{key} 缺 chart_rows_total")

    def test_year_series_persist_latest_25_years(self):
        with tempfile.TemporaryDirectory() as tmp:
            rd = self._render(tmp, report_names=["application_trend", "applicant_year_matrix"])
        for name, key in (("application_trend", "application_year"),
                          ("publication_trend", "授權公告年"),
                          ("applicant_year_matrix", "application_year")):
            years = {int(r[key]) for r in rd["reports"][name]["rows"]}
            self.assertLessEqual(len(years), 25, f"{name} 入庫年份數應 ≤25")
            self.assertEqual(min(years), 1995, f"{name} 應留最新 25 年（1995–2019）")
            self.assertEqual(rd["reports"][name].get("rows_total"), 30)
        # 年增率序列已隨 section 移除，不得再出現在 chart_rows
        self.assertNotIn("application_growth", rd["chart_rows"])

    def test_topic_rows_not_truncated(self):
        data = {
            "topics": [{"topic_code": f"T{i:03d}", "label": f"主題{i}", "source_field": "wips_independent_claims"}
                       for i in range(25)],
            "assignments": [{"topic_code": f"T{i:03d}", "patent_id": i} for i in range(25)],
            "normalized_applicants": [{"patent_id": i, "applicant_name": "TSMC"} for i in range(25)],
            "top_applicants_ws": ["TSMC"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            rd = self._render(tmp, report_names=["cluster_analytics"], cluster_data=data)
        self.assertEqual(len(rd["chart_rows"]["cluster_topic_table"]), 25,
                         "主題相關數據不截（例外規則）")

    def test_run_full_report_ranking_limit_default_20(self):
        """run_full_report 是 analysis 入庫出圖入口，ranking_limit 預設同步 20。"""
        import inspect

        from backend.app.reports.cluster_data_loader import run_full_report

        sig = inspect.signature(run_full_report)
        self.assertEqual(sig.parameters["ranking_limit"].default, 20)


class TopicSegmentTests(unittest.TestCase):
    """2026-07-21 使用者定案：主題統計「技術、功效不要混」——技術主題（wips_independent_claims）
    與功效分類（effect_summary）分段各自一張表；Source Field 原始欄名不出現在使用者介面；
    矩陣板每個 source_field 各一組（單一來源維持原檔名）。"""

    @staticmethod
    def _fake_ctx(tmp: str):
        from types import SimpleNamespace

        return SimpleNamespace(
            run_dir=Path(tmp), chart_rows={}, sections=[], report=None,
            cluster_data=None, cluster_reports={}, ipc_levels=(4, 5), cpc_levels=(4, 5))

    _TWO_SOURCE_DATA = {
        "topics": [
            {"topic_code": "T001", "label": "散熱防塵", "source_field": "wips_independent_claims"},
            {"topic_code": "T002", "label": "速度控制", "source_field": "wips_independent_claims"},
            {"topic_code": "E001", "label": "降噪效果", "source_field": "effect_summary"},
        ],
        "assignments": [
            {"topic_code": "T001", "patent_id": 1},
            {"topic_code": "T002", "patent_id": 2},
            {"topic_code": "E001", "patent_id": 3},
        ],
        "normalized_applicants": [
            {"patent_id": 1, "applicant_name": "TSMC"},
            {"patent_id": 2, "applicant_name": "UMC"},
            {"patent_id": 3, "applicant_name": "TSMC"},
        ],
        "top_applicants_ws": ["TSMC"],
    }

    def test_table_two_segments_no_source_field_literal(self):
        """兩通道的列都要在，且供前端依 source_field 切換。

        ⚠ 2026-07-29 改驗**數據列**而非 HTML 檔：主題統計表不再產 HTML 變體
        （使用者「沒圖表用表格就好，現在跑兩個表格很難看」），改由 section 的
        rows 單一呈現。原始欄名不出現在畫面是 DATA_TABLE_EXCLUDED_COLUMNS 的職責，
        由 test_data_card_excludes_source_field_column 守。
        """
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._fake_ctx(tmp)
            ctx.cluster_data = self._TWO_SOURCE_DATA
            chart_runner._build_cluster_analytics_section(ctx)
            rows = ctx.sections[0].get("rows") or []
        self.assertTrue(rows, "section 未帶 rows，前端切換沒有資料可切")
        sources = {r.get("source_field") for r in rows}
        self.assertEqual(sources, {"wips_independent_claims", "effect_summary"},
                         "兩通道的列都要在，前端才切得動")
        # ⚠ 每列都要有 source_field：前端 rows.filter(row => row.source_field === sourceField)
        # 對沒有該欄的列會**全部放行**，切換等於失效。
        self.assertTrue(all(r.get("source_field") for r in rows),
                        "有列缺 source_field，前端過濾會放行全部")

    def test_matrix_boards_per_source_with_segment_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._fake_ctx(tmp)
            ctx.cluster_data = self._TWO_SOURCE_DATA
            chart_runner._build_cluster_analytics_section(ctx)
            files = [v["file"] for v in ctx.sections[0]["variants"]]
            tech_opp = (Path(tmp) / "opportunity_quadrant_tech.svg").read_text(encoding="utf-8")
            effect_opp = (Path(tmp) / "opportunity_quadrant_effect.svg").read_text(encoding="utf-8")
            # ⚠ 痛點板已停產（2026-07-29 使用者定案，市場線未實作），不應存在。
            self.assertFalse((Path(tmp) / "pain_point_quadrant_tech.svg").exists(),
                             "痛點板應已停產")
            self.assertFalse((Path(tmp) / "pain_point_quadrant_effect.svg").exists(),
                             "痛點板應已停產")
        # ⚠ 2026-07-30：variants 前段是主題統計表的**解讀掛點**（無圖檔），
        # 其後才是每來源的機會板。掛點的存在理由見
        # test_topic_table_single_render.TopicTableNarrativeTests：
        # main.py 把 narrative 掛在 variant 上，沒 variant 就讀不到解讀。
        #
        # 🔴 2026-07-31：本測資有技術／功效**兩個通道**，故掛點也是兩個
        # （topic_table_tech／topic_table_effect）。動因：PPT 依通道把主題統計表
        # 拆成兩頁，只有一份解讀時兩頁會印出一模一樣的標題與要點。
        # ⚠ 單通道時仍只產一個掛點（chart_runner 以實際存在的通道判斷），
        # 由 test_single_source_keeps_filenames_and_single_segment 守住。
        self.assertEqual(len(files), 4, "統計表解讀掛點 2（雙通道）+ 每來源機會板 2")
        self.assertEqual(files[:2], ["", ""], "前兩個應為主題統計表掛點（無圖檔）")
        for f in ("opportunity_quadrant_tech.svg", "opportunity_quadrant_effect.svg"):
            self.assertIn(f, files)
        self.assertIn("機會四象限分析——技術主題", tech_opp)
        self.assertIn("機會四象限分析——功效分類", effect_opp)

    def test_opportunity_variant_rows_include_quadrant_and_thresholds(self):
        """機會四象限檢視用專屬 rows；不回退主題統計表，也不改 SVG 輸入。"""
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._fake_ctx(tmp)
            ctx.cluster_data = self._TWO_SOURCE_DATA
            chart_runner._build_cluster_analytics_section(ctx)
            topic_rows = ctx.sections[0].get("rows") or []
            tech_variant = next(
                v for v in ctx.sections[0]["variants"]
                if v["variant_key"] == "opportunity_tech"
            )
            chart_rows = ctx.chart_rows["opportunity_quadrant_tech"]["rows"]

        self.assertTrue(topic_rows)
        self.assertNotIn("quadrant", topic_rows[0], "主題統計表 rows 不應被四象限欄位污染")
        self.assertEqual(chart_rows, tech_variant["rows"])
        self.assertEqual(
            list(tech_variant["rows"][0]),
            [
                "label", "patent_count", "applicant_count", "quadrant",
                "leading_applicants", "leading_applicant_count",
            ],
        )
        self.assertIn(tech_variant["rows"][0]["quadrant"], {"必守核心", "新興戰場", "待釐清", "單一玩家壟斷"})
        self.assertEqual(
            tech_variant["thresholds"],
            {"patent_count_median": 1.0, "applicant_count_median": 1.0},
        )

    def test_single_source_keeps_filenames_and_single_segment(self):
        data = {
            "topics": [{"topic_code": "T001", "label": "散熱防塵", "source_field": "wips_independent_claims"}],
            "assignments": [{"topic_code": "T001", "patent_id": 1}],
            "normalized_applicants": [{"patent_id": 1, "applicant_name": "TSMC"}],
            "top_applicants_ws": ["TSMC"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._fake_ctx(tmp)
            ctx.cluster_data = data
            chart_runner._build_cluster_analytics_section(ctx)
            files = [v["file"] for v in ctx.sections[0]["variants"]]
            rows = ctx.sections[0].get("rows") or []
            opp = (Path(tmp) / "opportunity_quadrant.svg").read_text(encoding="utf-8")
        # ⚠ 2026-07-29 統計表不再是變體；單一來源維持原檔名的契約只剩兩張圖。
        self.assertEqual(files, ["", "opportunity_quadrant.svg"],
                         "單一來源維持原檔名（統計表改走數據表、痛點板已停產）")
        self.assertEqual({r.get("source_field") for r in rows}, {"wips_independent_claims"},
                         "只有一種來源時只出現該段的列")
        self.assertIn("機會四象限分析——技術主題", opp, "板標題帶來源段名")

    def test_data_card_excludes_source_field_column(self):
        rows = [{"topic_code": "T001", "label": "散熱防塵", "source_field": "wips_independent_claims",
                 "patent_count": 3, "applicant_count": 2, "top_applicants": [],
                 "leading_applicant_count": 1, "leading_applicants_involved": ["TSMC"]}]
        html = chart_runner._data_table_html(rows, "cluster_topic_table")
        self.assertNotIn("wips_independent_claims", html, "數據卡不得出現 source_field 原始值")
        self.assertNotIn("source_field", html, "數據卡不得出現 source_field 欄")
        # ⚠ 2026-07-29 使用者定案「T001/T002，機制能識別就好，表格和報告不用顯示」
        # → topic_code 進 DATA_TABLE_EXCLUDED_COLUMNS。本行原斷言「主題代碼」要出現，
        # 是定案前的舊契約。改驗其餘欄仍照 DATA_COLUMN_LABELS 顯示中文欄名。
        self.assertNotIn("主題代碼", html, "topic_code 已定案不顯示（機制識別用）")
        self.assertIn("主題標籤", html, "其餘欄仍應照 DATA_COLUMN_LABELS 顯示中文欄名")


class DataTableHumanizeTests(unittest.TestCase):
    """2026-07-21 使用者截圖回饋：數據卡複雜值人類化（嚴禁 raw repr）＋
    總計列只對加總有意義的欄出值（patent_count 類），其餘「—」避免誤導。"""

    def test_list_of_dicts_renders_name_count_semicolon(self):
        rows = [{"topic_code": "T1", "patent_count": 40,
                 "top_applicants": [{"name": "力山工業", "count": 39},
                                    {"name": "LOWE'S COMPANIES, INC.", "count": 1}]}]
        html = chart_runner._data_table_html(rows, "custom_report")
        self.assertIn("力山工業 39；LOWE", html, "list[dict] 應為「名稱 數字」分號連接")
        self.assertNotIn("{&#x27;name&#x27;", html)
        self.assertNotIn("[{", html, "不得輸出 raw repr")

    def test_list_of_str_renders_dun_comma(self):
        rows = [{"topic_code": "T1", "patent_count": 3,
                 "sample_names": ["力山工業", "客戶A"]}]
        html = chart_runner._data_table_html(rows, "custom_report")
        self.assertIn("力山工業、客戶A", html, "list[str] 應為頓號連接")
        self.assertNotIn("[&#x27;", html)

    def test_empty_list_and_none_render_dash(self):
        rows = [{"topic_code": "T1", "patent_count": 3,
                 "top_applicants": [], "leading_applicants_involved": None}]
        html = chart_runner._data_table_html(rows, "custom_report")
        self.assertNotIn("[]", html, "空 list 應顯示 —")
        self.assertNotIn("None", html, "None 應顯示 —")

    def test_totals_only_for_summable_columns(self):
        rows = [
            {"topic_code": "T1", "patent_count": 10, "applicant_count": 4,
             "leading_applicant_count": 2, "application_year": 2020},
            {"topic_code": "T2", "patent_count": 5, "applicant_count": 3,
             "leading_applicant_count": 1, "application_year": 2021},
        ]
        html = chart_runner._data_table_html(rows, "custom_report")
        totals = re.findall(r"<tr>.*?</tr>", html, re.S)[-1]
        cells = re.findall(r"<strong>(.*?)</strong>", totals)
        # 欄序：topic_code, patent_count, applicant_count, leading_applicant_count, application_year
        self.assertEqual(cells[1], "15", "patent_count 加總有意義，應出總計")
        for idx, col in ((2, "applicant_count"), (3, "leading_applicant_count"), (4, "application_year")):
            self.assertEqual(cells[idx], "—", f"{col} 加總無意義（distinct/年份），應顯示 —")


class NarrativeRefreshTests(unittest.TestCase):
    """報表解讀管線系統件（2026-07-21）：sections 持久化進 report_data.json、
    --refresh-index 從持久化 sections 重建 index（narratives.json 有就嵌入、
    版本不符顯示過期、舊 run 無 sections 鍵明確報錯不猜）。"""

    @staticmethod
    def _stub_run_report(name, filters=None, limit=None, patent_ids=None):
        rows = [{"current_assignee_display_name": f"Owner {i:02d}", "application_year": 2020,
                 "patent_count": 30 - i} for i in range(12)]
        return fake_report(name, rows)

    def _make_run(self, tmp: str) -> Path:
        with mock.patch.object(chart_runner, "run_report", self._stub_run_report):
            result = chart_runner.run_chart_trial(output_dir=Path(tmp), report_names=["owner_year_matrix"])
        return Path(result["output_dir"])

    def test_sections_persisted_in_report_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._make_run(tmp)
            rd = json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))
        sections = rd.get("sections")
        self.assertIsInstance(sections, list, "report_data.json 應含 sections 鍵")
        self.assertEqual(len(sections), 1)
        self.assertIn("title", sections[0])
        self.assertEqual(sections[0]["variants"][0]["file"], "owner_year_matrix.svg")

    def test_refresh_index_embeds_narratives_and_clears_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._make_run(tmp)
            narratives = {
                "based_on_version": run_dir.name,
                "reports": {
                    "owner_year_matrix": {
                        "text": "測試解讀文字XYZ",
                        "ai_model": "test-model",
                        "prompt_version": "report_narrative_v1",
                        "generated_at": "2026-07-21T00:00:00",
                        "based_on_version": run_dir.name,
                    }
                },
            }
            (run_dir / "narratives.json").write_text(
                json.dumps(narratives, ensure_ascii=False), encoding="utf-8")
            stats = chart_runner.refresh_index(run_dir)
            index_html = (run_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("測試解讀文字XYZ", index_html, "解讀文字應嵌入每張圖表變體面板內")
        self.assertNotIn("待解讀", index_html, "解讀齊備後不得殘留待解讀佔位")
        self.assertEqual(stats["pending"], [], "無缺漏 key")
        # owner_year_matrix has 2 variants (default + more); v1 direct text serves both
        self.assertEqual(stats["narrated"], 2)

    def test_narrative_lookup_strips_level_suffix(self):
        """IPC/CPC 卡查找鍵由檔名 fallback 帶 _L4 尾巴，narratives 契約鍵不帶層級——
        解讀查找須退基底鍵（2026-07-22 v2 首跑 4 變體待解讀 regression）。"""
        narrs = {"ipc_main_distribution": {"variants": {
            "L4": {"text": "四階解讀"}, "L5": {"text": "五階解讀"}}}}
        entry = chart_runner._narrative_entry(narrs, "ipc_main_distribution_L4")
        self.assertEqual(chart_runner._narrative_text(entry, "L4"), "四階解讀")
        self.assertEqual(chart_runner._narrative_text(entry, "L5"), "五階解讀")
        # 精確鍵優先：同名精確鍵存在時不退基底
        narrs2 = {"foo_L4": {"text": "精確"}, "foo": {"text": "基底"}}
        self.assertEqual(chart_runner._narrative_text(
            chart_runner._narrative_entry(narrs2, "foo_L4"), "default"), "精確")

    def test_refresh_index_version_mismatch_shows_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._make_run(tmp)
            narratives = {"based_on_version": "report_trial_other",
                          "reports": {"owner_year_matrix": {"text": "舊解讀"}}}
            (run_dir / "narratives.json").write_text(
                json.dumps(narratives, ensure_ascii=False), encoding="utf-8")
            chart_runner.refresh_index(run_dir)
            index_html = (run_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("解讀版本過期", index_html)
        self.assertNotIn("舊解讀", index_html, "過期解讀不得當有效內容嵌入")

    def test_refresh_index_old_run_without_sections_fails_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._make_run(tmp)
            rd_path = run_dir / "report_data.json"
            rd = json.loads(rd_path.read_text(encoding="utf-8"))
            rd.pop("sections", None)  # 模擬舊版產出
            rd_path.write_text(json.dumps(rd, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                chart_runner.refresh_index(run_dir)
        self.assertIn("sections", str(ctx.exception), "錯誤訊息應指明缺 sections 鍵")

    def test_cli_refresh_index_mode(self):
        """argparse 接線：--refresh-index <run_dir> 走 refresh、不進出圖路徑。"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._make_run(tmp)
            with mock.patch.object(sys, "argv", ["chart_runner", "--refresh-index", str(run_dir)]):
                chart_runner.main()
            self.assertTrue((run_dir / "index.html").exists())


class SelectiveRenderTests(unittest.TestCase):
    """選擇性出圖端到端（run_report stub）：只產選中的檔、只查依賴的報表。"""

    def test_application_trend_only(self):
        fetched: list[str] = []

        def stub_run_report(name, filters=None, limit=None, patent_ids=None):
            fetched.append(name)
            rows = {
                "application_trend": [
                    {"application_year": 2019, "patent_count": 3},
                    {"application_year": 2020, "patent_count": 6},
                ],
                "publication_trend": [
                    {"授權公告年": 2020, "patent_count": 4},
                ],
            }[name]
            return fake_report(name, rows)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", stub_run_report):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp),
                    report_names=["application_trend"],
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["sections_rendered"], ["annual_trend"])
            # 只查趨勢 section 依賴的兩張報表（公告補齊雙線），沒有其他報表被查。
            self.assertEqual(sorted(set(fetched)), ["application_trend", "publication_trend"])
            # 產出檔只有選中 sections 的圖＋固定兩檔。
            self.assertEqual(
                sorted(result["files"]),
                sorted([
                    "annual_trend.svg",
                    "report_data.json",
                    "index.html",
                    "artifact_manifest.json",
                    "version_meta.json",
                ]),
            )
            run_dir = Path(result["output_dir"])
            for filename in result["files"]:
                self.assertTrue((run_dir / filename).is_file(), filename)
            report_data = json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))
            annual_rows = report_data["chart_rows"]["annual_trend"]
            self.assertEqual(
                annual_rows,
                [
                    {"year": 2019, "application_count": 3, "授權公告件數": 0},
                    {"year": 2020, "application_count": 6, "授權公告件數": 4},
                ],
            )
            self.assertEqual(
                {key: chart_runner.DATA_COLUMN_LABELS[key] for key in ("year", "application_count", "授權公告件數")},
                {"year": "年份", "application_count": "申請件數", "授權公告件數": "授權公告件數"},
            )
            # 未選的 section（如受理局地圖）不得落檔。
            self.assertFalse((run_dir / "country_bubble.svg").exists())

    def test_report_cache_deduplicates_fetch(self):
        calls: list[str] = []

        def stub_run_report(name, filters=None, limit=None, patent_ids=None):
            calls.append(name)
            return fake_report(name, [{"application_year": 2020, "patent_count": 1}])

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", stub_run_report):
                chart_runner.run_chart_trial(
                    output_dir=Path(tmp),
                    report_names=["application_trend", "publication_trend"],
                )
        # application_trend 同時被趨勢圖與（在同一批查詢裡的）其他 section 用到，
        # 但快取後只實際查一次 DB。
        self.assertEqual(calls.count("application_trend"), 1)

    def test_manifest_hash_and_snapshot_metadata(self):
        """小型 fixture 驗證 SVG、hash、filters 與 patent_ids 快照 metadata。"""

        def stub_run_report(name, filters=None, limit=None, patent_ids=None):
            self.assertEqual(patent_ids, [7, 9])
            rows = {
                "applicant_ranking": [
                    {"applicant_display_name": "REXON", "patent_count": 2},
                ],
            }[name]
            return fake_report(name, rows)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "fetch_analysis_patent_ids", return_value=[7, 9]), \
                    mock.patch.object(chart_runner, "run_report", stub_run_report), \
                    mock.patch.object(chart_runner, "record_exports", return_value=0):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp),
                    report_names=["applicant_ranking"],
                    filters={"country_code": "TW"},
                    analysis_id=5,
                )

            run_dir = Path(result["output_dir"])
            svg_path = run_dir / "applicant_ranking.svg"
            manifest_path = run_dir / "artifact_manifest.json"
            self.assertTrue(svg_path.is_file())
            self.assertTrue(manifest_path.is_file())

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            metadata = manifest["metadata"]
            self.assertEqual(metadata["analysis_id"], 5)
            self.assertEqual(metadata["filters"], {"country_code": "TW"})
            self.assertEqual(metadata["scope"], "patent_ids_snapshot")
            self.assertEqual(metadata["patent_ids_count"], 2)
            self.assertEqual(metadata["patent_ids_sha256"], hashlib.sha256(b"7,9").hexdigest())
            by_file = {item["file"]: item for item in manifest["artifacts"]}
            self.assertEqual(by_file["applicant_ranking.svg"]["report_name"], "applicant_ranking")
            self.assertEqual(by_file["applicant_ranking.svg"]["sha256"], chart_runner.sha256_file(svg_path))
            self.assertIn("artifact_manifest.json", result["files"])

    def test_applicant_ranking_outputs_transfer_segment_and_detail_fields(self):
        """申請人排名圖表、JSON 保留最新受讓人統計與公司明細。"""

        def stub_run_report(name, filters=None, limit=None, patent_ids=None):
            self.assertEqual(name, "applicant_ranking")
            return fake_report(name, [{
                "applicant_display_name": "REXON",
                "patent_count": 5,
                "recent_assignee_count": 2,
                "recent_assignee_display_names": "Acme; Beta",
            }])

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", stub_run_report):
                result = chart_runner.run_chart_trial(output_dir=Path(tmp), report_names=["applicant_ranking"])

            run_dir = Path(result["output_dir"])
            svg = (run_dir / "applicant_ranking.svg").read_text(encoding="utf-8")
            report_data = json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))

        self.assertIn("有最新受讓人", svg)
        self.assertIn("2 / 5", svg)
        self.assertIn("Acme", svg)
        self.assertIn("Beta", svg)
        total_rect = re.search(r'<rect class="bar-total" x="([0-9.]+)"[^>]+width="([0-9.]+)"', svg)
        segment_rect = re.search(r'<rect class="bar-segment" x="([0-9.]+)"[^>]+width="([0-9.]+)"', svg)
        self.assertIsNotNone(total_rect)
        self.assertIsNotNone(segment_rect)
        self.assertGreater(float(segment_rect.group(1)), float(total_rect.group(1)))
        self.assertLess(float(segment_rect.group(2)), float(total_rect.group(2)))
        row = report_data["reports"]["applicant_ranking"]["rows"][0]
        self.assertEqual(row["recent_assignee_count"], 2)
        self.assertEqual(row["recent_assignee_display_names"], "Acme; Beta")

    def test_owner_year_matrix_outputs_bubble_svg_json_and_expand_html(self):
        """專利權人 × 年份矩陣使用泡泡圖，JSON 不因圖表前 20 家而裁切。"""

        matrix_rows = [
            {"current_assignee_display_name": f"Owner {index:02d}", "application_year": 2020, "patent_count": 30 - index}
            for index in range(1, 23)
        ]

        def stub_run_report(name, filters=None, limit=None, patent_ids=None):
            self.assertEqual(name, "owner_year_matrix")
            return fake_report(name, matrix_rows)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", stub_run_report):
                result = chart_runner.run_chart_trial(output_dir=Path(tmp), report_names=["owner_year_matrix"])

            run_dir = Path(result["output_dir"])
            svg = (run_dir / "owner_year_matrix.svg").read_text(encoding="utf-8")
            more_svg = (run_dir / "owner_year_matrix_more.svg").read_text(encoding="utf-8")
            index_html = (run_dir / "index.html").read_text(encoding="utf-8")
            report_data = json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))

        self.assertEqual(result["sections_rendered"], ["owner_year_matrix"])
        self.assertIn("owner_year_matrix.svg", result["files"])
        self.assertIn("owner_year_matrix_more.svg", result["files"])
        self.assertIn("<circle", svg)
        self.assertIn("<title>Owner 01 / 2020 / 29</title>", svg)
        self.assertIn(">29</text>", svg)
        combined_svg = svg + more_svg
        self.assertIn('#14B8A6', combined_svg)
        self.assertIn('#F59E0B', combined_svg)
        self.assertIn('#DC2626', combined_svg)
        self.assertIn("件數色階", svg)
        self.assertIn("Owner 10", svg)
        self.assertNotIn("Owner 11", svg)
        self.assertIn("Owner 11", more_svg)
        self.assertIn("Owner 20", more_svg)
        self.assertNotIn("Owner 21", more_svg)
        # 2026-07-21 顯示規格：收合鈕文案改 WIPS 式「＋查看全部（第 11～20 名）」（原「顯示第 11～20 名」）
        self.assertIn("＋查看全部（第 11～20 名）", index_html)
        self.assertIn("data-expand-target", index_html)
        self.assertIn("2020", svg)
        rows = report_data["reports"]["owner_year_matrix"]["rows"]
        self.assertEqual(len(rows), 22)
        self.assertEqual(rows[0]["current_assignee_display_name"], "Owner 01")

    def test_applicant_year_matrix_outputs_bubbles_and_keeps_full_rows(self):
        matrix_rows = [
            {"applicant_display_name": f"Applicant {index:02d}", "application_year": 2021, "patent_count": 25 - index}
            for index in range(1, 23)
        ]

        def stub_run_report(name, filters=None, limit=None, patent_ids=None):
            self.assertEqual(name, "applicant_year_matrix")
            return fake_report(name, matrix_rows)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", stub_run_report):
                result = chart_runner.run_chart_trial(output_dir=Path(tmp), report_names=["applicant_year_matrix"])

            run_dir = Path(result["output_dir"])
            svg = (run_dir / "applicant_year_matrix.svg").read_text(encoding="utf-8")
            more_svg = (run_dir / "applicant_year_matrix_more.svg").read_text(encoding="utf-8")
            report_data = json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))

        self.assertIn("applicant_year_matrix_more.svg", result["files"])
        self.assertIn("<circle", svg)
        self.assertIn("<title>Applicant 01 / 2021 / 24</title>", svg)
        self.assertIn("Applicant 10", svg)
        self.assertNotIn("Applicant 11", svg)
        self.assertIn("Applicant 11", more_svg)
        self.assertNotIn("Applicant 21", more_svg)
        self.assertEqual(len(report_data["reports"]["applicant_year_matrix"]["rows"]), 22)

    def test_year_bubble_matrix_uses_latest_25_years_and_large_bubbles(self):
        matrix_rows = [
            {"current_assignee_display_name": "Owner A", "application_year": 2000 + index, "patent_count": index + 1}
            for index in range(30)
        ] + [
            {"current_assignee_display_name": "Owner B", "application_year": 2000 + index, "patent_count": 1}
            for index in range(30)
        ]

        def stub_run_report(name, filters=None, limit=None, patent_ids=None):
            self.assertEqual(name, "owner_year_matrix")
            return fake_report(name, matrix_rows)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", stub_run_report):
                result = chart_runner.run_chart_trial(output_dir=Path(tmp), report_names=["owner_year_matrix"])

            run_dir = Path(result["output_dir"])
            svg = (run_dir / "owner_year_matrix.svg").read_text(encoding="utf-8")
            report_data = json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))

        self.assertIn("owner_year_matrix.svg", result["files"])
        for year in range(2000, 2005):
            self.assertNotIn(f">{year}<", svg)
            self.assertNotIn(f"/ {year} /", svg)
        for year in (2005, 2029):
            self.assertIn(f">{year}<", svg)
            self.assertIn(f"/ {year} /", svg)
        radii = [float(value) for value in re.findall(r' r="([0-9.]+)"', svg)]
        self.assertGreaterEqual(min(radii), 9.0)
        self.assertGreaterEqual(max(radii), 27.0)
        height = float(re.search(r'<svg[^>]+height="([0-9.]+)"', svg).group(1))
        self.assertGreaterEqual(height, 125 + 2 * 56 + 34)
        # 2026-07-21 定案修正（規格變更註記）：年度序列「保存」只留最新 25 年——
        # fixture 30 年×2 家＝60 列，入庫截為 25 年×2 家＝50 列（原斷言 60）。
        self.assertEqual(len(report_data["reports"]["owner_year_matrix"]["rows"]), 50)
        self.assertEqual(report_data["reports"]["owner_year_matrix"]["rows_total"], 60)
        # font-size 放大（年標籤用 17，公司名用 17）
        self.assertIn('font-size="17"', svg)
        # left margin 擴大（340 使 SVG 寬度擴大，原 width 約 250+NumYears*82+34）
        svg_width = float(re.search(r'<svg[^>]+width="([0-9.]+)"', svg).group(1))
        self.assertGreaterEqual(svg_width, 340 + 1 * 82 + 34)


class ClassificationSourceColumnTests(unittest.TestCase):
    """IPC/CPC 分析來源欄（2026-07-23 使用者定案）：一律改用 Orig. Main。

    定案要點：來源固定為 `Orig. IPC(Main)`／`Orig. CPC(Main)`，
    **不因該欄目前無值而 fallback 回 Curr.**——空值是資料批次問題，
    報表出空是正確行為。四階／五階支援維持不變。
    """

    def test_report_definitions_use_orig_main(self):
        """報表定義的分類來源欄（columns/group_by/order/exclude_blank）須為 Orig.。"""
        for report_name, column in (
            ("ipc_main_distribution", "Orig. IPC(Main)"),
            ("cpc_main_distribution", "Orig. CPC(Main)"),
        ):
            definition = REPORT_DEFINITIONS[report_name]
            self.assertEqual(definition.columns, (column,))
            self.assertEqual(definition.group_by, (column,))
            self.assertEqual(definition.exclude_blank_columns, (column,))
            self.assertIn(column, [col for col, _ in definition.default_order])

    def test_classification_sections_read_orig_main(self):
        """_build_ipc_section／_build_cpc_section 取的 row key 須為 Orig. Main。"""
        for builder, report_key, column in (
            (chart_runner._build_ipc_section, "ipc_main_distribution", "Orig. IPC(Main)"),
            (chart_runner._build_cpc_section, "cpc_main_distribution", "Orig. CPC(Main)"),
        ):
            rows = [{column: "H04L-051/02", "patent_count": 3}]
            with tempfile.TemporaryDirectory() as tmp:
                ctx = self._fake_ctx(tmp, {report_key: {"label_zh": "分布", "rows": rows}})
                builder(ctx)
            collapsed = ctx.chart_rows[f"{report_key}_L4"]
            self.assertEqual(collapsed, [{column: "H04L", "patent_count": 3}])

    def test_allowed_filter_columns_use_orig_main(self):
        """filter 白名單同步改 Orig.，否則前端／API 帶分類 filter 會被擋。"""
        from backend.app.reports.report_definitions import (
            ALLOWED_FILTER_COLUMNS,
            allowed_filter_columns_for_report,
        )

        self.assertIn("Orig. IPC(Main)", ALLOWED_FILTER_COLUMNS)
        self.assertIn("Orig. CPC(Main)", ALLOWED_FILTER_COLUMNS)
        self.assertNotIn("Curr. IPC(Main)", ALLOWED_FILTER_COLUMNS)
        self.assertNotIn("Curr. CPC(Main)", ALLOWED_FILTER_COLUMNS)
        trend = allowed_filter_columns_for_report(REPORT_DEFINITIONS["application_trend"])
        self.assertIn("Orig. IPC(Main)", trend)
        self.assertIn("Orig. CPC(Main)", trend)

    def test_orig_cpc_format_levels(self):
        """合成資料驗 Orig. CPC 分階：該欄實測 0% 填充，無法用實資料驗證。

        涵蓋 CPC 4 位群組（H10P-0072/0616）、IPC 3 位群組（H04L-051/02），
        以及 Orig. CPC 可能出現的無前導零／無分隔／帶 suffix 寫法。
        """
        cases = [
            # (原始碼, L4 subclass, L5 main group)
            ("H10P-0072/0616", "H10P", "H10P-0072"),  # CPC 4 位群組
            ("H04L-051/02", "H04L", "H04L-051"),      # IPC 3 位群組
            ("H10P-72/0616", "H10P", "H10P-72"),      # 無前導零
            ("H01M 10/0525", "H01M", "H01M 10"),      # 空白分隔
            ("A01D-0034/416", "A01D", "A01D-0034"),
            ("Y02E-0060/10", "Y02E", "Y02E-0060"),    # Y 段 CPC-only
        ]
        for symbol, level4, level5 in cases:
            self.assertEqual(chart_runner.classification_level_key(symbol, 4), level4, symbol)
            self.assertEqual(chart_runner.classification_level_key(symbol, 5), level5, symbol)

    def _fake_ctx(self, tmp: str, reports: dict):
        return DisplaySpecTests._fake_ctx(tmp, reports, ipc_levels=(4, 5))


class SectionReportKeyTests(unittest.TestCase):
    """數據卡查找鍵 regression（2026-07-27）：index 卡片的「數據表為空」。

    症狀：年趨勢／受理局分布／國家佈局／公司×國家四張卡圖表有、數據表卻是
    「無資料」，但 report_data.json 內對應報表明明有 rows（實測 analysis_1
    輸出：application_trend 33 列、country_distribution 6 列、
    family_country_layout 2 列、applicant_country_distribution 12 列）。

    根因：section 沒宣告 report_key 時，_section_report_name 退回「第一個
    variant 檔名去副檔名」當查找鍵。這四張卡的 SVG 檔名（annual_trend、
    jurisdiction_distribution、family_country_distribution、
    applicant_country_matrix）與報表鍵（application_trend、
    country_distribution、family_country_layout、
    applicant_country_distribution）不同名，查找必然落空。

    修法：section 一律顯式帶 report_key，不靠檔名與報表鍵恰好同名。本測試
    同時釘住「每個 section 的查找鍵都必須在 report_data.json 取得到 rows」，
    避免日後改檔名又悄悄退化。
    """

    # 四張卡各給一列夠用的假資料；欄位形狀依報表定義的 columns。
    _ROWS = {
        "application_trend": [{"application_year": 2020, "patent_count": 5}],
        "publication_trend": [{"授權公告年": 2021, "patent_count": 3}],
        "country_distribution": [{"country_code": "TW", "patent_count": 7}],
        "family_country_layout": [{"country_code": "US", "patent_count": 2}],
        "family_quality_detail": [{
            "family_id": "F1", "is_surrogate_family": False, "member_rows": 2,
            "expected_counts_raw": "", "family_incomplete": False,
            "unknown_status_count": 0, "pending_status_count": 0,
            "ep_in_transition_count": 0, "ep_missing_epc_count": 0,
            "non_country_row_count": 0,
        }],
        "applicant_country_distribution": [
            {"applicant_display_name": "REXON", "country_code": "TW", "patent_count": 4}
        ],
    }

    def _stub_run_report(self, name, filters=None, limit=None, patent_ids=None):
        return fake_report(name, self._ROWS.get(name, []))

    def _render(self, report_names):
        """跑一次出圖並回傳 (report_data dict, index.html 文字)。"""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(chart_runner, "run_report", self._stub_run_report):
                result = chart_runner.run_chart_trial(
                    output_dir=Path(tmp), report_names=report_names
                )
            run_dir = Path(result["output_dir"])
            rd = json.loads((run_dir / "report_data.json").read_text(encoding="utf-8"))
            index_html = (run_dir / "index.html").read_text(encoding="utf-8")
        return rd, index_html

    def test_four_cards_declare_matching_report_key(self):
        """四張卡的 report_key 必須是報表引擎的鍵，不得退回檔名 fallback。"""
        expected = {
            "專利申請趨勢與專利授權公告趨勢": "annual_trend",
            "專利受理局分布": "country_distribution",
            "國家佈局（現有保護）": "family_country_layout",
            "公司×國家交叉表": "applicant_country_distribution",
        }
        rd, _ = self._render(list(self._ROWS))
        by_title = {s["title"]: s for s in rd["sections"]}
        for title, report_key in expected.items():
            self.assertIn(title, by_title, f"缺少卡片 {title}")
            self.assertEqual(
                by_title[title].get("report_key"), report_key,
                f"卡片「{title}」應顯式宣告 report_key={report_key}，"
                f"實得 {by_title[title].get('report_key')!r}（未宣告＝退回檔名 fallback）",
            )

    def test_every_section_lookup_key_resolves_to_rows(self):
        """通則：每個 section 的查找鍵都要能在 report_data.json 取到 rows。

        這是「數據表為空」的直接契約——查找鍵落空就等於卡片顯示無資料。
        """
        rd, _ = self._render(list(self._ROWS))
        available = (
            set(rd.get("reports", {}))
            | set(rd.get("family_reports", {}))
            | set(rd.get("chart_rows", {}))
        )
        for section in rd["sections"]:
            key = chart_runner._section_report_name(section)
            self.assertIn(
                key, available,
                f"卡片「{section['title']}」查找鍵 {key!r} 在 report_data.json 找不到，"
                f"數據表會顯示無資料；可用鍵={sorted(available)}",
            )

    def test_four_cards_render_non_empty_data_table(self):
        """端到端：四張卡在 index.html 的數據區不得是「無資料」。"""
        _, index_html = self._render(list(self._ROWS))
        blocks = re.findall(r'<section class="report-section">.*?</section>', index_html, re.S)
        by_title = {}
        for block in blocks:
            title = re.search(r"<h2>(.*?)</h2>", block).group(1)
            by_title[title] = block
        for title in ("專利申請趨勢與專利授權公告趨勢", "專利受理局分布",
                      "國家佈局（現有保護）", "公司×國家交叉表"):
            self.assertIn(title, by_title, f"缺少卡片 {title}")
            self.assertNotIn(
                "data-empty", by_title[title],
                f"卡片「{title}」數據表為空（顯示無資料），但該報表有 rows",
            )


class EncodingNoteAccuracyTests(unittest.TestCase):
    """F-5：編碼說明必須描述那張圖**實際**怎麼畫，不得沿用舊版型或憑鍵名想像。"""

    def test_ipc_cpc_notes_do_not_claim_side_by_side(self):
        """🔴 拆頁後 IPC/CPC 各階層獨立成頁，不得再寫「左右為不同階層」。

        2026-07-31 批 3 把並排頁拆成一圖一頁（修「CPC 兩張從未畫出」），
        但編碼說明沒跟著改——實機 p8–p11 四頁都還寫著「左右為不同階層，
        非同圖合成」，而畫面上根本沒有左右兩張圖。
        """
        for key in ("ipc_main_distribution", "cpc_main_distribution"):
            note = chart_runner.CHART_ENCODING_NOTES[key]
            self.assertNotIn("左右", note, f"{key} 的編碼說明仍描述並排版型")

    def test_lifecycle_note_matches_how_chart_is_drawn(self):
        """🔴 生命週期是**依年份**連線，不是「同一技術群」。

        同檔 `render_lifecycle_chart` 的 docstring 與 SVG 副題（connected by year）
        都寫著依年份，說明卻寫成技術群——2026-08-02 使用者當場抓到。
        """
        note = chart_runner.CHART_ENCODING_NOTES["lifecycle"]
        self.assertIn("年份", note)
        self.assertNotIn("技術群", note)


class BubbleLegendSpanTests(unittest.TestCase):
    """F-6：泡泡圖圖例級距不得出現「下限大於上限」或把單值寫成區間。

    🔴 2026-08-02 實機 p17（max_value=3）印出「低 **1–0**」——下限 1、上限 0。
    根因：級距由 `ceil(下界×max)`／`floor(上界×max)` 直接串接，max 小的時候
    某一階完全落不到任何整數上（0–0.75 件），floor 就小於 ceil。
    p16（max_value=5）則是把單值印成「1–1／2–2／3–3」。
    """

    def _spans(self, max_value: int):
        return chart_runner.bubble_legend_spans(max_value)

    def test_no_inverted_span(self):
        """任何 max_value 下都不得出現下限 > 上限。"""
        for max_value in range(1, 31):
            for _color, label, span in self._spans(max_value):
                bounds = [int(x) for x in span.split("–")]
                self.assertLessEqual(bounds[0], bounds[-1],
                                     f"max_value={max_value} 的「{label}」級距顛倒：{span}")

    def test_empty_band_is_dropped(self):
        """max_value=3 時最低階涵蓋不到任何整數件數，該階不列入圖例。"""
        labels = [label for _c, label, _s in self._spans(3)]
        self.assertNotIn("低", labels)
        self.assertEqual([s for _c, _l, s in self._spans(3)], ["1", "2", "3"])

    def test_single_value_not_written_as_range(self):
        """單值級距寫「1」，不寫「1–1」。"""
        spans = [s for _c, _l, s in self._spans(5)]
        self.assertEqual(spans, ["1", "2", "3", "4–5"])

    def test_spans_cover_every_count_once(self):
        """級距要連續且不重疊——每個實際件數只能落在一階。"""
        for max_value in (3, 5, 8, 11, 20):
            covered: list[int] = []
            for _c, _l, span in self._spans(max_value):
                bounds = [int(x) for x in span.split("–")]
                covered.extend(range(bounds[0], bounds[-1] + 1))
            self.assertEqual(covered, list(range(1, max_value + 1)),
                             f"max_value={max_value} 的級距沒有完整覆蓋 1..{max_value}")


class PointLabelPlacementTests(unittest.TestCase):
    """E7：資料點標籤不得互相重疊，也不得壓在資料點上。

    🔴 2026-08-02 實機 p4 生命週期：左下角兩個年份標籤疊成「20**」讀不出來，
    2015／2019／2023 也各自擠在點旁。

    07-31 的第一版避讓只看「折線在此點往上還往下」，把標籤放到線的另一側——
    那解的是「被折線壓過」，解不了**標籤之間**與**標籤壓到別的點**。
    資料點密集時（本案 60 件裡多年落在 1–2 家、1–2 件）兩者才是主因。
    """

    def _place(self, items, obstacles=()):
        return chart_runner.place_point_labels(list(items), list(obstacles))

    def test_two_close_labels_do_not_overlap(self):
        placed = self._place([(100.0, 100.0, "2011"), (104.0, 102.0, "2012")])
        self.assertEqual(len(placed), 2)
        self.assertTrue(all(p is not None for p in placed), "兩個標籤都該放得下（換位置即可）")
        (x1, y1), (x2, y2) = placed
        self.assertFalse(chart_runner.boxes_overlap(
            chart_runner.label_box(x1, y1, "2011"), chart_runner.label_box(x2, y2, "2012")),
            "相鄰兩年的標籤仍然重疊")

    def test_label_does_not_cover_data_point(self):
        """標籤不得蓋住任何資料點（包含不是它自己的那些）。"""
        obstacles = [(100.0, 100.0, 4.0), (118.0, 94.0, 4.0)]
        placed = self._place([(100.0, 100.0, "2022")], obstacles)
        self.assertIsNotNone(placed[0])
        box = chart_runner.label_box(*placed[0], "2022")
        for ox, oy, r in obstacles:
            self.assertFalse(chart_runner.boxes_overlap(box, (ox - r, oy - r, ox + r, oy + r)),
                             "標籤壓在資料點上")

    def test_gives_up_instead_of_stacking(self):
        """四個候選位置全被占滿時回 None（不標），不得硬疊上去。"""
        crowd = [(100.0 + i * 2, 100.0, f"20{i:02d}") for i in range(12)]
        placed = self._place(crowd)
        self.assertIn(None, placed, "極度擁擠時應放棄部分標籤，而不是全部硬放")
        kept = [(p, item) for p, item in zip(placed, crowd) if p]
        for i, (p1, it1) in enumerate(kept):
            for p2, it2 in kept[i + 1:]:
                self.assertFalse(chart_runner.boxes_overlap(
                    chart_runner.label_box(*p1, it1[2]), chart_runner.label_box(*p2, it2[2])),
                    "保留下來的標籤之間仍有重疊")


if __name__ == "__main__":
    unittest.main()
