"""section 契約：有圖就要有對應的表（2026-08-17 全報表掃描）。

## 病灶

顯示層自 2026-08-11 起**優先吃 `section["rows"]`**（受理局交叉表帶起的改動）。
沒給 `rows` 的 section 會退回 `report_key` 的原始報表——於是「圖畫的是 A、
表顯示的是 B」。同一個病灶今天抓到四次：

| 報表 | 症狀 |
|---|---|
| 專利申請趨勢 | 授權公告件數欄消失（已修） |
| IPC／CPC 主分類分布 | 圖切 4 階、表卻是 5 階 |
| 公司×國家交叉表 | 表非圖的內容 |

⚠ 它會**靜默**發生：圖對、表也「有東西」，只是兩者講的不是同一件事。

## 契約

**有 `variants`（＝有圖）的 section，必須給 `rows`。** 這是結構性條件，
不看語意就能判定，且滿足它的唯一途徑就是把表接上——恆等式類閘門
（deepen design §1.2 三問的 Q2）。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1]
       / "backend" / "app" / "reports" / "chart_runner.py")


class SectionRowsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = SRC.read_text(encoding="utf-8")

    def test_every_section_with_variants_has_rows(self):
        """🔴 有圖必有表——否則顯示層退回原始報表，圖表講不同的事。"""
        missing = []
        for m in re.finditer(r"ctx\.sections\.append\(\{(.{0,1200}?)\}\)",
                             self.src, re.S):
            body = m.group(1)
            if '"variants":' not in body:
                continue
            if '"rows":' in body:
                continue
            line = self.src[:m.start()].count("\n") + 1
            key = re.search(r'"report_key":\s*["\']?([^"\',\n]+)', body)
            missing.append((line, key.group(1) if key else "(推導)"))
        self.assertEqual(
            missing, [],
            "這些 section 有圖卻沒給 rows，顯示層會退回原始報表而與圖脫節："
            + "；".join(f"行 {ln} ({k})" for ln, k in missing))


class ClassificationLevelAlignmentTests(unittest.TestCase):
    """IPC／CPC：表要跟著 tab 的階層走。"""

    def test_variant_rows_declared_per_level(self):
        """每個 variant 要能對到自己那階的 rows（4 階 tab → 4 階表）。"""
        from backend.app.reports import chart_runner

        self.assertTrue(
            hasattr(chart_runner, "classification_variant_rows"),
            "缺 classification_variant_rows：切到 4 階時表格仍顯示 5 階明細")


class WideTableTrimTests(unittest.TestCase):
    """三張寬表精簡——PPT 放得下（使用者 2026-08-17 逐頁驗收）。"""

    def test_kp_profile_drops_internal_ids(self):
        from backend.app.reports import chart_runner

        rows = [{"applicant_display_name": "甲", "patent_count": 5,
                 "family_count": 4, "country_count": 2, "topic_count": 3,
                 "ipc_subclass_count": 2, "patent_ids": [1, 2, 3],
                 "granted_count": 3, "pending_count": 1, "dead_count": 1,
                 "co_applicant_names": "乙", "recent_assignee_count": 0,
                 "kind_summary": "發明 3"}]
        trimmed = chart_runner.kp_profile_table_rows(rows)
        self.assertNotIn("patent_ids", trimmed[0],
                         "patent_ids 是內部識別碼，不給決策者看")
        self.assertLessEqual(len(trimmed[0]), 7,
                             f"仍有 {len(trimmed[0])} 欄：{sorted(trimmed[0])}")
        self.assertIn("applicant_display_name", trimmed[0])
        self.assertIn("patent_count", trimmed[0])

    def test_topic_table_trimmed(self):
        from backend.app.reports import chart_runner

        rows = [{f"col{i}": i for i in range(21)}]
        rows[0].update({"topic_code": "T001", "label": "甲主題",
                        "patent_count": 9, "applicant_count": 7,
                        "top3_share": 0.5, "source_field": "x"})
        trimmed = chart_runner.topic_table_display_rows(rows)
        self.assertLessEqual(len(trimmed[0]), 8,
                             f"主題表仍有 {len(trimmed[0])} 欄——PPT 放不下")

    def test_year_matrix_table_is_readable(self):
        """🔴 年度矩陣表 16 欄年份全展開、滿是空格——「誰看得懂」。

        改為每列一個申請人的**摘要**：件數、活躍年區間、近期是否仍在投入。
        """
        from backend.app.reports import chart_runner

        pivot = [{"applicant_display_name": "甲", "2011": 1, "2013": "",
                  "2020": 4, "2022": 5, "total": 10}]
        rows = chart_runner.year_matrix_summary_rows(pivot)
        cols = set(rows[0])
        self.assertLessEqual(len(cols), 6, f"仍有 {len(cols)} 欄：{sorted(cols)}")
        self.assertNotIn("2011", cols, "年份不該再是欄位")
        self.assertIn("applicant_display_name", cols)


if __name__ == "__main__":
    unittest.main()
