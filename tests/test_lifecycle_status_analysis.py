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


class StatusChartSvgTests(unittest.TestCase):
    """渲染器直測：圖例四桶、單位在條尾、截斷註記有無、零值段不畫。"""

    def _svg(self, rows, total):
        import tempfile
        from pathlib import Path

        from backend.app.reports.chart_runner import render_status_stacked_chart

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.svg"
            render_status_stacked_chart(p, "專利狀態分析", rows, rows_total=total)
            return p.read_text(encoding="utf-8")

    ROW = {"applicant_display_name": "公司A", "已授權": 9, "審查中": 2,
           "已失效": 1, "未知": 2, "patent_count": 14}

    def test_legend_and_total_label(self):
        svg = self._svg([self.ROW], total=1)
        for bucket in ("已授權", "審查中", "已失效", "未知"):
            self.assertIn(f">{bucket}</text>", svg, f"圖例缺 {bucket}")
        self.assertIn(">14件</text>", svg, "條尾件數要帶單位")

    def test_truncation_note_only_when_truncated(self):
        self.assertNotIn("顯示前", self._svg([self.ROW], total=1))
        self.assertIn("顯示前 1/25 名", self._svg([self.ROW], total=25))

    def test_zero_segment_not_drawn(self):
        """零值段不畫（畫 0 寬 rect 只是 DOM 垃圾，還可能蓋掉相鄰段邊界）。"""
        row = {"applicant_display_name": "公司B", "已授權": 5, "審查中": 0,
               "已失效": 0, "未知": 0, "patent_count": 5}
        svg = self._svg([row], total=1)
        from backend.app.reports.chart_runner import STATUS_BUCKET_COLORS

        body = svg.split("</text>", 6)[-1]   # 跳過圖例區只看條區
        self.assertIn(STATUS_BUCKET_COLORS["已授權"], body)
        self.assertNotIn(STATUS_BUCKET_COLORS["審查中"], body)


class SegmentCountLabelTests(unittest.TestCase):
    """🔴 2026-08-07 使用者指正：「看不出各狀態件數」——段內要標件數。

    規則：段寬放得下數字才標（窄段不硬塞——塞了會疊到相鄰段更不可讀，
    完整數字永遠在網頁報表的數據表）；字色依段底色用 readable_text_on 決定，
    並帶 data-on-fill 讓 PPT 深色轉色後重算字色。
    """

    def _svg(self, rows):
        import tempfile
        from pathlib import Path

        from backend.app.reports.chart_runner import render_status_stacked_chart

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.svg"
            render_status_stacked_chart(p, "專利狀態分析", rows, rows_total=len(rows))
            return p.read_text(encoding="utf-8")

    def test_wide_segments_carry_counts(self):
        svg = self._svg([{"applicant_display_name": "公司A", "已授權": 9, "審查中": 2,
                          "已失效": 1, "未知": 2, "patent_count": 14}])
        import re

        seg_labels = re.findall(r'data-on-fill="[^"]+"[^>]*>(\d+)<', svg)
        self.assertIn("9", seg_labels, "已授權段內要有件數 9")
        self.assertIn("2", seg_labels, "審查中段內要有件數 2")

    def test_segment_label_pairs_fill_for_recolor(self):
        """段內字必須帶 data-on-fill＝該段底色——深色轉色後字色才會重算。"""
        svg = self._svg([{"applicant_display_name": "公司A", "已授權": 9, "審查中": 0,
                          "已失效": 0, "未知": 0, "patent_count": 9}])
        from backend.app.reports.chart_runner import STATUS_BUCKET_COLORS

        self.assertIn(f'data-on-fill="{STATUS_BUCKET_COLORS["已授權"]}"', svg)

    def test_narrow_segment_skips_label(self):
        """極窄段（大母體下 1 件）不硬塞數字。"""
        rows = [{"applicant_display_name": "巨量公司", "已授權": 200, "審查中": 1,
                 "已失效": 0, "未知": 0, "patent_count": 201}]
        svg = self._svg(rows)
        import re

        seg_labels = re.findall(r'data-on-fill="[^"]+"[^>]*>(\d+)<', svg)
        self.assertIn("200", seg_labels)
        self.assertNotIn("1", seg_labels, "1 件段在 201 件尺度下塞不進數字，應略過")


if __name__ == "__main__":
    unittest.main()
