"""受理局「申請 vs 現存有效」合併頁（2026-08-07 使用者定案）。

p04（受理局分布，件）＋ p06（國家佈局現有保護，存活家族數）合成一張：
每國兩條 bar、口徑「件 vs 件」（申請件數 vs 狀態桶「已授權」件數）、
備註寫清楚定義；家族數降為頁尾註記。判準見 output/p1_jur_merge_criteria.md。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports import chart_runner
from backend.app.reports.report_definitions import REPORT_DEFINITIONS
from backend.app.reports.report_engine import build_report_sql
from backend.app.transforms.legal_status import STATUS_BUCKET_ORDER


ROWS = [
    # (country, legal_status, patent_count)——模擬 SQL 群組結果，含 None 未知。
    {"country_code": "CN", "legal_status": "授权", "patent_count": 20},
    {"country_code": "CN", "legal_status": "到期(Expiration of the term)", "patent_count": 15},
    {"country_code": "CN", "legal_status": "审查中", "patent_count": 3},
    {"country_code": "TW", "legal_status": None, "patent_count": 7},
    {"country_code": "TW", "legal_status": "已核准", "patent_count": 2},
    {"country_code": "US", "legal_status": "授权", "patent_count": 6},
    {"country_code": "EP", "legal_status": "撤回", "patent_count": 2},
]


class DefinitionContractTests(unittest.TestCase):
    def test_country_distribution_groups_status(self):
        """🔴 契約更新（2026-08-07 合併頁定案）：受理局報表加 legal_status 群組，
        SQL 只回原值——桶收斂唯一定義處在 transforms/legal_status。"""
        d = REPORT_DEFINITIONS["country_distribution"]
        self.assertEqual(tuple(d.group_by), ("country_code", "legal_status"))

    def test_sql_has_no_status_literals(self):
        sql, _ = build_report_sql(REPORT_DEFINITIONS["country_distribution"], {}, 1000)
        for literal in ("授权", "已核准", "到期", "granted"):
            self.assertNotIn(literal, sql)

    def test_blank_status_not_excluded(self):
        """未知桶要現形：legal_status 空值不得被 exclude。"""
        d = REPORT_DEFINITIONS["country_distribution"]
        self.assertNotIn("legal_status", tuple(d.exclude_blank_columns or ()))


class PivotTests(unittest.TestCase):
    def test_pivot_four_buckets_and_total(self):
        rows = chart_runner.country_status_pivot(ROWS)
        cn = next(r for r in rows if r["country_code"] == "CN")
        self.assertEqual(cn["已授權"], 20)
        self.assertEqual(cn["已失效"], 15)
        self.assertEqual(cn["審查中"], 3)
        self.assertEqual(cn["未知"], 0)
        self.assertEqual(cn["申請件數"], 38)
        tw = next(r for r in rows if r["country_code"] == "TW")
        self.assertEqual(tw["未知"], 7)
        self.assertEqual(tw["已授權"], 2)

    def test_pivot_column_order_follows_bucket_order(self):
        rows = chart_runner.country_status_pivot(ROWS)
        keys = list(rows[0])
        self.assertEqual(keys[:2], ["country_code", "申請件數"])
        self.assertEqual(keys[2:], list(STATUS_BUCKET_ORDER))

    def test_pivot_sorted_by_application_desc(self):
        rows = chart_runner.country_status_pivot(ROWS)
        totals = [r["申請件數"] for r in rows]
        self.assertEqual(totals, sorted(totals, reverse=True))


class DataTableUsesPivotTests(unittest.TestCase):
    """🔴 數據表改交叉表（2026-08-11 使用者指出「橫向欄位放狀態、縱向放國家」）。

    實機：受理局卡的數據表印 (country_code, legal_status, 件數) 長格式——
    `授权`/`授權` 簡繁並列、三種「到期(...)」各佔一列，14 列讀不動。
    與年度矩陣 07-29「長格式難讀」同型。

    🔴 欄位裁決（使用者附登錄下拉截圖）：**用統一 11 項狀態詞彙**
    （`TW_LEGAL_STATUS_ALLOWED`，＝WIPS 状态欄實測全集轉繁，2026-08-07 定案），
    **不是**四大狀態桶——桶是分析用的粗分類，表要看得到「撤回／拒絕／到期」
    這種細節。到期的括號細節（Non-payment…）折疊進「到期」一欄；
    簡繁折疊走 `display_legal_status`（唯一定義處，不另建對照）。
    **零件數的欄不出現**；狀態未登錄（None）僅非零時以「未知」現形。

    機制收斂為一條：**section 自帶 `rows` ＝ 顯示用轉置**（分群卡既有慣例），
    index 產表優先吃它；沒帶才回 reports 桶查。
    ⚠ 圖不受影響：兩條 bar 的「現存有效」仍走狀態桶 pivot（分析口徑），
    表格走 11 項詞彙（顯示口徑）——兩者語意不同，各自引用各自的唯一來源。
    """

    def _section(self):
        from unittest import mock

        class _Ctx:
            def __init__(self, tmp):
                self.run_dir = Path(tmp)
                self.sections = []
                self.chart_rows = {}

            def report(self, name):
                rows = ROWS if name == "country_distribution" else []
                return {"label_zh": "專利受理局分布", "rows": rows}

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctx = _Ctx(tmp.name)
        with mock.patch.object(chart_runner, "_fetch_family_quality_rows", return_value=[]):
            chart_runner._build_country_map_section(ctx)
        return ctx.sections[0]

    def test_section_carries_status_columns(self):
        """section.rows＝每國一列；欄＝申請件數＋實際出現的狀態（11 項詞彙序）。

        測資狀態：授权(CN20/US6)、到期(Expiration)(CN15)、审查中(CN3)、
        None(TW7)、已核准(TW2→折疊授權)、撤回(EP2)。
        """
        section = self._section()
        rows = section.get("rows") or []
        self.assertTrue(rows, "受理局 section 未帶轉置 rows，數據表會退回長格式")
        cols = list(rows[0])
        self.assertEqual(cols[0], "country_code")
        self.assertEqual(cols[1], "申請件數")
        # 有件數的狀態依 11 項詞彙序出現；未知（未登錄）殿後
        self.assertEqual(cols[2:], ["審查中", "授權", "撤回", "到期", "未知"])
        cn = next(r for r in rows if r["country_code"] == "CN")
        self.assertEqual((cn["申請件數"], cn["授權"], cn["到期"], cn["審查中"]), (38, 20, 15, 3))
        tw = next(r for r in rows if r["country_code"] == "TW")
        self.assertEqual((tw["授權"], tw["未知"]), (2, 7), "已核准應折疊進授權；未登錄以未知現形")

    def test_zero_count_status_absent(self):
        """🔴 沒件數的狀態欄當然不出現（使用者原話）。"""
        rows = self._section().get("rows") or []
        for absent in ("申請", "公開", "即將授權", "放棄", "拒絕", "刪除", "無效"):
            self.assertNotIn(absent, rows[0], f"零件數的「{absent}」欄不該出現")

    def test_index_table_prefers_section_rows(self):
        """index 產表：section 有 rows 就用它，不再回頭撈 reports 桶的長格式。

        🔴 必須穿過 `persistable_sections`（持久化白名單）再渲染——2026-08-11 實機：
        builder 掛了 rows、單元測試全綠，但 `SECTION_PERSIST_KEYS` 沒收 "rows"，
        寫進 report_data.json 時被**靜默丟棄**，成品照樣長格式。
        驗在接縫之前的測試擋不住接縫上的丟失。
        """
        import json

        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run_dir = Path(tmp.name) / "report_trial_t"
        run_dir.mkdir(parents=True)
        section = self._section()
        section["variants"] = [{"label": "Bar", "file": "x.svg", "variant_key": "default"}]
        persisted = chart_runner.persistable_sections([section])
        (run_dir / "report_data.json").write_text(json.dumps({
            "sections": persisted,
            "reports": {"country_distribution": {"label_zh": "專利受理局分布", "rows": ROWS}},
        }, ensure_ascii=False), encoding="utf-8")
        self.assertIn("rows", persisted[0], "持久化白名單把 section rows 丟掉了")
        chart_runner.render_index(run_dir / "index.html", persisted)
        html = (run_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("撤回", html, "表頭應為狀態欄（交叉表）")
        self.assertNotIn("legal_status", html, "長格式的原始狀態欄不得再出現在表上")

    def test_country_code_has_chinese_label(self):
        """⚠ 內部欄名不得上表頭：country_code 要有中文欄名（受理局）。"""
        self.assertEqual(chart_runner.DATA_COLUMN_LABELS.get("country_code"), "受理局")


class PairedBarChartTests(unittest.TestCase):
    def _render(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "jurisdiction_distribution.svg"
        chart_runner.render_paired_bar_chart(
            path, "專利受理局分布", chart_runner.country_status_pivot(ROWS),
            label_key="country_code",
            series=(("申請件數", "申請件數"), ("現存有效", "已授權")),
        )
        return path.read_text(encoding="utf-8")

    def test_two_bars_per_country_with_values(self):
        svg = self._render()
        # CN 兩條：申請 38、現存有效 20；值以「N 件」標示。
        self.assertIn("38 件", svg)
        self.assertIn("20 件", svg)
        # TW：申請 9、有效 2。
        self.assertIn("9 件", svg)
        self.assertIn("2 件", svg)

    def test_legend_defines_both_series(self):
        svg = self._render()
        self.assertIn("申請件數", svg)
        self.assertIn("現存有效", svg)


# ⚠ BuilderIntegrationTests 已隨 PPT 交付線移除（2026-08-10，remove-ppt-delivery-line）。


if __name__ == "__main__":
    unittest.main()
