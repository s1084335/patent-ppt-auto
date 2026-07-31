"""PPT skill v3 驗收後修正批次（規劃 P1；2026-07-31 使用者放行）。

實機驗收（report_trial_20260730_164919，19 頁）抓到的缺陷與使用者定案的版面升級，
唯一來源：`.agents/context/ppt-visual-rework-spec.md` 六之三節。本檔逐項釘住契約：

| # | 契約 |
|---|---|
| P1-1 | `chart_hero` 版型：大圖＋固定區註解卡＋底部核心結論條；內容頁新預設 |
| P1-2 | 機會評估頁接得到解讀（narratives 掛在 `cluster_topic_table:opportunity_*`，加 alias）|
| P1-3 | 主題分布依通道拆成兩張表格頁（rows 有資料就該用表格，不是降級大數字卡）|
| P1-4 | `table` 頁不再誤報 `narrative_missing`（表格頁本來就不配解讀）|
| P1-5 | 表格中文欄名；`topic_code` 不入畫面；`source_field` 值轉「技術／功效」|
| P1-6 | 研發方向建議移**最後、附錄之前**；動態插頁在它之前 |
| P1-7 | 研發方向合併版：三步色塊流程＋題目卡＋核心結論條；契約結構化 JSON |
| P1-8 | 封面主標用 workspace 名稱；`cover.title` AI slot 退場 |
| P1-10 | 來源註不得再寫「未經人工覆核」（預覽閘門就是人工覆核）|
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "patent-report-ppt"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_ppt_v3fix", SKILL_DIR / "scripts" / "build_ppt.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_ppt_v3fix", module)
    spec.loader.exec_module(module)
    return module


bp = _load_builder()


def _spec_by_topic(layout, topic_fragment):
    return [s for s in layout if topic_fragment in (s.topic or s.title)]


def _report_entry(rows, label_zh="X", report_type="ranking"):
    return {"label": label_zh, "label_zh": label_zh, "report_type": report_type,
            "rows": rows, "row_count": len(rows)}


def _minimal_report_data(**extra):
    data = {
        "parameters": {"version": "v-test", "workspace_name": "拉繩訓練機"},
        "reports": {
            "application_trend": _report_entry(
                [{"application_year": 2020 + i, "patent_count": 3} for i in range(4)],
                "專利申請趨勢", "trend"),
            # 附錄2 需要它才會出頁——附錄存在測試的頁序斷言才有意義。
            "applicant_ranking": _report_entry(
                [{"applicant_display_name": "TSMC", "patent_count": 9}],
                "主要申請人排名"),
        },
        "family_reports": {},
    }
    data["reports"].update(extra)
    return data


CLUSTER_ROWS = [
    {"topic_code": "T001", "label": "拉繩回收", "source_field": "wips_independent_claims",
     "patent_count": 15, "applicant_count": 13},
    {"topic_code": "T001", "label": "提升成效", "source_field": "effect_summary",
     "patent_count": 9, "applicant_count": 7},
]


class DirectionLastTests(unittest.TestCase):
    """P1-6：研發方向建議＝結論，必須在所有內容頁之後、附錄之前。"""

    def _layout(self, data):
        return bp._expand_page_layout(data, None)

    def test_direction_after_content_before_appendix(self):
        layout = self._layout(_minimal_report_data())
        kinds = [s.kind for s in layout]
        direction_at = kinds.index("direction")
        first_appendix = next(i for i, s in enumerate(layout) if s.is_appendix)
        self.assertGreater(direction_at, 0, "研發方向不得在封面前")
        content_pages = [i for i, s in enumerate(layout)
                        if s.kind not in {"cover", "direction"} and not s.is_appendix]
        self.assertTrue(all(i < direction_at for i in content_pages),
                        "還有內容頁排在研發方向之後——結論必須壓軸")
        self.assertLess(direction_at, first_appendix, "研發方向要在附錄之前")

    def test_dynamic_pages_insert_before_direction(self):
        """動態插頁也算證據，必須插在結論（direction）之前。"""
        data = _minimal_report_data(
            lifecycle=_report_entry([{"year": 2020, "patent_count": 2}], "專利生命週期"))
        layout = self._layout(data)
        direction_at = next(i for i, s in enumerate(layout) if s.kind == "direction")
        lifecycle_at = next(i for i, s in enumerate(layout)
                            if "lifecycle" in s.report_keys)
        self.assertLess(lifecycle_at, direction_at,
                        "動態插頁跑到結論之後——證據必須在結論前")


class ClusterTopicTableTests(unittest.TestCase):
    """P1-3：主題分布有 rows 就用表格，依通道拆兩頁；不再降級大數字卡。"""

    def test_split_two_table_pages_by_channel(self):
        data = _minimal_report_data(
            cluster_topic_table=_report_entry(CLUSTER_ROWS, "主題分類統計表", "cluster"))
        layout = bp._expand_page_layout(data, None)
        cluster_pages = [s for s in layout
                         if "cluster_topic_table" in s.report_keys and not s.is_appendix]
        self.assertEqual(len(cluster_pages), 2, "技術／功效應各一頁")
        kinds = {s.kind for s in cluster_pages}
        self.assertEqual(kinds, {"table_with_points"},
                         f"主題分布應為表格版型，實際 {kinds}")
        filters = {json.dumps(s.row_filter, sort_keys=True) for s in cluster_pages}
        self.assertEqual(len(filters), 2, "兩頁應帶不同的通道 row_filter")

    def test_row_filter_applied(self):
        data = _minimal_report_data(
            cluster_topic_table=_report_entry(CLUSTER_ROWS, "主題分類統計表", "cluster"))
        layout = bp._expand_page_layout(data, None)
        page = next(s for s in layout if s.row_filter
                    and "wips_independent_claims" in json.dumps(s.row_filter))
        rows = bp._first_rows(page, {"report_data": data})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "拉繩回收")


class TableColumnTests(unittest.TestCase):
    """P1-5：表格欄名中文化、topic_code 不入畫面、source_field 值轉通道。"""

    def test_column_label_map(self):
        for raw, expected in (("patent_count", "專利件數"), ("applicant_count", "申請人家數"),
                              ("label", "主題"), ("source_field", "通道")):
            with self.subTest(raw=raw):
                self.assertEqual(bp.TABLE_COLUMN_LABELS.get(raw), expected)

    def test_topic_code_excluded(self):
        self.assertIn("topic_code", bp.TABLE_EXCLUDED_COLUMNS,
                      "topic_code 供機制識別，不得入畫面（既有定案）")

    def test_source_field_value_mapped(self):
        self.assertEqual(bp.TABLE_VALUE_LABELS["source_field"]["wips_independent_claims"], "技術")
        self.assertEqual(bp.TABLE_VALUE_LABELS["source_field"]["effect_summary"], "功效")


class ChartHeroTests(unittest.TestCase):
    """P1-1：chart_hero 版型存在且為內容頁預設。"""

    def test_renderer_registered(self):
        self.assertIn("chart_hero", bp.RENDERERS)

    def test_default_kind_for_single_chart(self):
        report = {"report_type": "ranking"}
        self.assertEqual(bp._kind_for_report(report, 1), "chart_hero",
                         "單圖內容頁預設應為 chart_hero（大圖＋結論條）")

    def test_chart_dependent(self):
        """chart_hero 沒圖要降級，不得留空框。"""
        self.assertIn("chart_hero", bp.CHART_DEPENDENT_KINDS)
        self.assertIn("chart_hero", bp.SINGLE_CHART_KINDS)

    def test_conclusion_pick(self):
        """核心結論條：優先 emphasis 那條，其次「意涵」，再退 headline。"""
        points = [{"label": "現況", "text": "A", "emphasis": False},
                  {"label": "意涵", "text": "B", "emphasis": False},
                  {"label": "後續", "text": "C", "emphasis": True}]
        self.assertEqual(bp._conclusion_text("H", points), "C")
        points[2]["emphasis"] = False
        self.assertEqual(bp._conclusion_text("H", points), "B")
        self.assertEqual(bp._conclusion_text("H", []), "H")


class NarrativeAliasTests(unittest.TestCase):
    """P1-2：機會評估的解讀掛在 cluster_topic_table 的 opportunity_* 變體。"""

    NARRATIVES = {"reports": {"cluster_topic_table": {"variants": {
        "topic_table": {"headline": "主題集中", "points": [{"label": "現況", "text": "x"}], "text": "t"},
        "opportunity_tech": {"headline": "技術面機會", "points": [{"label": "意涵", "text": "y"}], "text": "t"},
        "opportunity_effect": {"headline": "功效面機會", "points": [{"label": "意涵", "text": "z"}], "text": "t"},
    }}}}

    def test_opportunity_page_finds_narrative(self):
        spec = bp.PageSpec(page=8, kind="comparison", title="機會評估", topic="機會評估",
                           report_keys=("opportunity_quadrant",),
                           charts=("opportunity_quadrant_tech.svg", "opportunity_quadrant_effect.svg"))
        matched, variant = bp._narrative_entry(self.NARRATIVES, bp._narrative_candidates(spec))
        self.assertTrue(variant, "機會評估頁找不到解讀（alias 未生效）")
        self.assertIn("機會", str(variant.get("headline")))

    def test_topic_table_page_not_hijacked(self):
        """⚠ 主題分布頁仍應拿 topic_table 變體，不得被 opportunity 搶走。"""
        spec = bp.PageSpec(page=6, kind="table_with_points", title="技術主題分布",
                           topic="技術主題分布", report_keys=("cluster_topic_table",))
        _, variant = bp._narrative_entry(self.NARRATIVES, bp._narrative_candidates(spec))
        self.assertEqual(variant.get("headline"), "主題集中")


class TablePageNoNarrativeWarningTests(unittest.TestCase):
    """P1-4：純表格頁（明細）不配解讀，不得誤報 narrative_missing。"""

    def test_no_warning_for_table_kind(self):
        data = _minimal_report_data(
            family_quality_detail=_report_entry(
                [{"a": 1}], "家族完整性明細", "detail"))
        with tempfile.TemporaryDirectory() as tmp:
            report_dir = Path(tmp)
            (report_dir / "report_data.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8")
            (report_dir / "artifact_manifest.json").write_text(
                json.dumps({"artifacts": []}), encoding="utf-8")
            result = bp.build_ppt(report_dir=report_dir, output_dir=report_dir / "out")
        bad = [w for w in result["manifest"]["warnings"]
               if w.get("type") == "narrative_missing"
               and "family_quality_detail" in str(w.get("report_key"))]
        self.assertEqual(bad, [], "明細表格頁誤報 narrative_missing")


class CoverWorkspaceTests(unittest.TestCase):
    """P1-8：封面主標用 workspace 名稱；cover.title AI slot 退場。"""

    def test_slot_keys_only_direction(self):
        self.assertEqual(bp.all_slot_keys(), ["direction.body"],
                         "cover.title 應退場——封面標題由 workspace 名確定性組成")

    def test_cover_title_from_workspace(self):
        """2026-07-31 起補上「專利分析」：單一 workspace 名不像簡報標題。"""
        self.assertEqual(bp._cover_title(_minimal_report_data(), {}), "拉繩訓練機專利分析")

    def test_cover_title_not_doubled(self):
        """workspace 名本身已含「專利分析」時不得再接一次。"""
        data = _minimal_report_data()
        data["parameters"]["workspace_name"] = "拉繩訓練機專利分析"
        self.assertEqual(bp._cover_title(data, {}), "拉繩訓練機專利分析")

    def test_cover_title_fallback(self):
        data = _minimal_report_data()
        data["parameters"].pop("workspace_name")
        self.assertEqual(bp._cover_title(data, {}), "專利情報整合分析")


class FootnoteTests(unittest.TestCase):
    """P1-10：來源註只留可追溯資訊；「未經人工覆核」退場。"""

    def test_no_unverified_disclaimer(self):
        src = (SKILL_DIR / "scripts" / "build_ppt.py").read_text(encoding="utf-8")
        self.assertNotIn("未經人工覆核", src,
                         "預覽閘門就是人工覆核——每頁再蓋此章等於否定定稿流程")
        self.assertIn("報表版本", src.split("def _render_footnote")[1][:1200],
                      "來源註應含報表版本（可追溯）")


class DirectionStructuredTests(unittest.TestCase):
    """P1-7：direction.body 契約結構化（合併版）。"""

    def test_parse_structured(self):
        payload = json.dumps({
            "situation": ["2022 起放量"], "opportunity": ["省空間區 7 件 6 家"],
            "direction": ["阻力調節差異化"],
            "topics": [{"name": "可調阻力", "basis": "必守核心區", "action": "迴避設計"}],
            "conclusion": "現在是投入窗口",
        }, ensure_ascii=False)
        parsed = bp._parse_direction_body(payload)
        self.assertEqual(parsed["conclusion"], "現在是投入窗口")
        self.assertEqual(parsed["topics"][0]["name"], "可調阻力")

    def test_parse_legacy_text(self):
        """⚠ 舊純文字要能過渡：回 None 走舊版面，不炸。"""
        self.assertIsNone(bp._parse_direction_body("整體態勢：…一段長文"))

    def test_content_rules_updated(self):
        rules = (SKILL_DIR / "report_ppt_content_rules.md").read_text(encoding="utf-8")
        for key in ("situation", "opportunity", "direction", "topics", "conclusion"):
            with self.subTest(key=key):
                self.assertIn(key, rules, f"content_rules 缺 direction 結構欄位 {key}")
        # ⚠ cover.title 允許以「退場沿革」形式出現（防止改回去），但不得再是要產的 slot。
        self.assertIn("只產一個 slot", rules, "規則應明示只剩 direction.body 一個 slot")
        self.assertIn("退場", rules, "cover.title 退場沿革要留在規則裡")


class OpportunitySplitTests(unittest.TestCase):
    """2026-07-31 使用者二輪回饋：機會評估同頁比較圖太小——改**分頁**（一象限圖一頁）。"""

    def test_opportunity_splits_into_hero_pages(self):
        spec = bp.PageSpec(page=7, kind="comparison", title="機會評估", topic="機會評估",
                           report_keys=("opportunity_quadrant",),
                           charts=("opportunity_quadrant_tech.svg", "opportunity_quadrant_effect.svg"))
        pages = bp._split_pairs_by_policy([spec], None)
        self.assertEqual(len(pages), 2, "技術面／功效面應各一頁")
        self.assertEqual({s.kind for s in pages}, {"chart_hero"},
                         "拆頁後應用大圖版型，不是要點框版")
        self.assertEqual([len(s.charts) for s in pages], [1, 1])

    def test_ipc_stays_comparison(self):
        """⚠ IPC/CPC 維持同頁比較——只有機會評估改分頁。"""
        self.assertNotIn("ipc_main_distribution", bp.SPLIT_PAIR_REPORTS)
        self.assertIn("opportunity_quadrant", bp.SPLIT_PAIR_REPORTS)


class YearMatrixMainOnlyTests(unittest.TestCase):
    """2026-07-31 使用者二輪回饋：年度矩陣只用前 10 名主表那張，「更多」圖不上 PPT。"""

    def test_more_chart_filtered(self):
        files = ("owner_year_matrix.svg", "owner_year_matrix_more.svg")
        kept = bp._filter_report_charts(("owner_year_matrix",), files)
        self.assertEqual(kept, ("owner_year_matrix.svg",))

    def test_other_reports_unaffected(self):
        files = ("ipc_main_distribution_L4.svg", "ipc_main_distribution_L5.svg")
        self.assertEqual(bp._filter_report_charts(("ipc_main_distribution",), files), files)


if __name__ == "__main__":
    unittest.main()
