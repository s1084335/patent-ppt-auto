"""lifecycle 改版「專利狀態分析」（2026-08-07 使用者定案，openspec improve-report-professionalism）。

## 定案

1. 「lifecycle 改成專利狀態分析」——report key **沿用** `lifecycle`（前端／PPT／
   artifact 對照鍵不連動），內容由「件數×家數散點」改為**狀態組成**。
2. 「並套用前十大申請人名單」——主體＝**前十大申請人 × 狀態桶**堆疊長條，
   讓「某申請人 N 件 0% 授權」這類敘述有確定性資料源。

## 口徑

- 申請人口徑＝**展開 VIEW**（共同申請各自計數，同 applicant_ranking；
  母體註記標「含共同申請」）。
- 狀態桶收斂為四類（唯一定義處 `transforms/legal_status.py`）：
  **已授權／審查中／已失效／未知**。
  ⚠ 原值收斂實據（2026-08-07 DB 實查）：`授权`（簡體）→已授權；
  `审查中`／`申请`→審查中；`到期(...)` **三種括號寫法**＋`放弃`→已失效；
  None／空白→未知（7 件新型無狀態值是資料現實，不得吞掉——吞掉會虛增授權率）。
"""
from __future__ import annotations

import unittest


class StatusBucketTests(unittest.TestCase):
    """狀態桶純函式：簡轉繁、多寫法收斂、空值誠實。"""

    def _bucket(self, value):
        from backend.app.transforms.legal_status import status_bucket

        return status_bucket(value)

    def test_granted(self):
        self.assertEqual(self._bucket("授权"), "已授權")
        self.assertEqual(self._bucket("授權"), "已授權")

    def test_pending(self):
        self.assertEqual(self._bucket("审查中"), "審查中")
        self.assertEqual(self._bucket("申请"), "審查中")

    def test_dead_variants_collapse(self):
        """三種「到期」寫法＋放棄都是同一件事：權利已不存續。"""
        for v in ("到期(Non-payment of Renewal / Annual fee)",
                  "到期(Expiration of the term)",
                  "到期(Termination of patent right due to unpaid annual fee)",
                  "放弃", "放棄"):
            with self.subTest(value=v):
                self.assertEqual(self._bucket(v), "已失效")

    def test_unknown_is_honest(self):
        """⚠ None／空白→「未知」，不得歸進任何實桶——吞掉會虛增授權率。"""
        for v in (None, "", "  "):
            with self.subTest(value=v):
                self.assertEqual(self._bucket(v), "未知")

    def test_unrecognized_value_is_unknown_not_crash(self):
        """沒見過的字面（未來 WIPS 新增）→ 未知，不炸、不猜。"""
        self.assertEqual(self._bucket("某種新狀態"), "未知")

    def test_bucket_order_is_stable(self):
        """桶順序是呈現契約（堆疊段序／圖例序），固定不得隨 dict 序漂。"""
        from backend.app.transforms.legal_status import STATUS_BUCKET_ORDER

        self.assertEqual(STATUS_BUCKET_ORDER, ("已授權", "審查中", "已失效", "未知"))


class LifecycleDefinitionTests(unittest.TestCase):
    """報表定義改版：展開 VIEW、申請人×狀態聚合、label 改名。"""

    def test_definition_reshaped(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        d = REPORT_DEFINITIONS["lifecycle"]
        self.assertEqual(d.label_zh, "專利狀態分析")
        self.assertEqual(d.source_table, "derived_layer.report_patent_applicant_expanded")
        self.assertEqual(d.group_by, ("applicant_display_name", "legal_status"))

    def test_frontend_label_updated(self):
        from pathlib import Path

        html = (Path(__file__).resolve().parents[1] / "backend" / "app" / "static"
                / "index.html").read_text(encoding="utf-8")
        self.assertIn("'lifecycle', '專利狀態分析'", html.replace('"', "'"))
        self.assertNotIn("專利生命週期", html)


class LifecycleSectionTests(unittest.TestCase):
    """section builder：前十大 × 狀態桶 pivot；含共同申請註記。"""

    ROWS = [
        # applicant, raw status, count —— 造 12 家測前十截取
        *[{"applicant_display_name": f"公司{chr(65 + i)}", "legal_status": "授权",
           "patent_count": 20 - i} for i in range(12)],
        {"applicant_display_name": "公司A", "legal_status": "审查中", "patent_count": 3},
        {"applicant_display_name": "公司A", "legal_status": "到期(Expiration of the term)",
         "patent_count": 2},
        {"applicant_display_name": "公司B", "legal_status": None, "patent_count": 4},
    ]

    def _pivot(self):
        from backend.app.reports.chart_runner import lifecycle_status_pivot

        return lifecycle_status_pivot(self.ROWS, limit=10)

    def test_top10_by_total_and_bucketed(self):
        rows = self._pivot()
        self.assertEqual(len(rows), 10, "前十大截取")
        a = rows[0]
        self.assertEqual(a["applicant_display_name"], "公司A")   # 20+3+2=25 最大
        self.assertEqual(a["已授權"], 20)
        self.assertEqual(a["審查中"], 3)
        self.assertEqual(a["已失效"], 2)
        self.assertEqual(a["未知"], 0)
        self.assertEqual(a["patent_count"], 25)

    def test_unknown_bucket_counted(self):
        rows = self._pivot()
        b = [r for r in rows if r["applicant_display_name"] == "公司B"][0]
        self.assertEqual(b["未知"], 4)
        self.assertEqual(b["patent_count"], 23)

    def test_over_counting_note_applies(self):
        """展開口徑 → 母體註記「含共同申請」（同 applicant_ranking）。"""
        from backend.app.reports.population import OVER_COUNTING_REPORTS

        self.assertIn("lifecycle", OVER_COUNTING_REPORTS)


class StatusMatrixSvgTests(unittest.TestCase):
    """渲染契約：🔴 2026-08-07 使用者定案「不要做 bar，像公司×國家交叉表的形式」。

    複用 render_matrix_chart：列＝申請人、欄＝狀態桶（語意序）、儲存格＝件數。
    ⚠ 原堆疊長條版（render_status_stacked_chart）連同段內標數、四桶類別色一併
    刪除——矩陣格值天生就是件數，「看不出各狀態件數」的問題由形式本身解掉。
    """

    LONG_ROWS = (
        {"applicant_display_name": "公司A", "status_bucket": "已授權", "patent_count": 9},
        {"applicant_display_name": "公司A", "status_bucket": "審查中", "patent_count": 2},
        {"applicant_display_name": "公司A", "status_bucket": "未知", "patent_count": 2},
        {"applicant_display_name": "公司B", "status_bucket": "已失效", "patent_count": 2},
        {"applicant_display_name": "公司B", "status_bucket": "未知", "patent_count": 3},
    )

    def _svg(self, rows):
        import tempfile
        from pathlib import Path

        from backend.app.reports.chart_runner import render_matrix_chart
        from backend.app.transforms.legal_status import STATUS_BUCKET_ORDER

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.svg"
            render_matrix_chart(p, "專利狀態分析", list(rows),
                                row_key="applicant_display_name",
                                col_key="status_bucket",
                                col_order=STATUS_BUCKET_ORDER)
            return p.read_text(encoding="utf-8")

    def test_columns_follow_semantic_order(self):
        """欄序＝語意序（已授權→未知），不按量排——按量排會讓每份報告欄序不同。"""
        svg = self._svg(self.LONG_ROWS)
        positions = [svg.index(f">{b}<") for b in ("已授權", "審查中", "已失效", "未知")]
        self.assertEqual(positions, sorted(positions), "欄序未照語意序")

    def test_cells_carry_counts(self):
        svg = self._svg(self.LONG_ROWS)
        for v in (">9<", ">3<"):
            self.assertIn(v, svg, f"儲存格缺件數 {v}")

    def test_absent_bucket_column_omitted(self):
        """全場沒有某桶（例如無失效案）→ 該欄不出現，不畫整欄空格。"""
        rows = [r for r in self.LONG_ROWS if r["status_bucket"] != "已失效"]
        svg = self._svg(rows)
        self.assertNotIn(">已失效<", svg)

    def test_country_matrix_order_unchanged(self):
        """⚠ 反向鎖：col_order 不給時維持按量排——公司×國家矩陣行為不得被波及。"""
        import tempfile
        from pathlib import Path

        from backend.app.reports.chart_runner import render_matrix_chart

        rows = [
            {"applicant_display_name": "A", "country_code": "TW", "patent_count": 1},
            {"applicant_display_name": "A", "country_code": "CN", "patent_count": 9},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.svg"
            render_matrix_chart(p, "t", rows, row_key="applicant_display_name",
                                col_key="country_code")
            svg = p.read_text(encoding="utf-8")
        self.assertLess(svg.index(">CN<"), svg.index(">TW<"), "量大的欄應在前")


class LifecycleBuilderIntegrationTests(unittest.TestCase):
    """builder 端到端：假 ctx＋stub 報表 → 矩陣 SVG＋pivot 數據表＋section note。"""

    def test_builder_renders_matrix_and_pivot(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        from backend.app.reports import chart_runner

        rows = [
            {"applicant_display_name": "公司A", "legal_status": "授权", "patent_count": 9},
            {"applicant_display_name": "公司A", "legal_status": "审查中", "patent_count": 2},
            {"applicant_display_name": "公司B", "legal_status": None, "patent_count": 3},
        ]

        def report(_key):
            return {"report_name": "lifecycle", "label_zh": "專利狀態分析", "rows": rows}

        with tempfile.TemporaryDirectory() as tmp:
            ctx = SimpleNamespace(run_dir=Path(tmp), chart_rows={}, sections=[],
                                  report=report, cluster_data=None, cluster_reports={},
                                  meta={}, ipc_levels=(4,), cpc_levels=(4,))
            chart_runner._build_lifecycle_section(ctx)
            svg = (Path(tmp) / "lifecycle.svg").read_text(encoding="utf-8")
        self.assertIn(">已授權<", svg)
        self.assertIn(">未知<", svg)
        pivot = ctx.chart_rows["lifecycle"]
        self.assertEqual(pivot[0]["已授權"], 9)
        note = ctx.sections[0]["note"]
        self.assertIn("含共同申請", note)
        self.assertIn("未知", note)


if __name__ == "__main__":
    unittest.main()
