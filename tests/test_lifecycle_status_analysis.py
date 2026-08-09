"""lifecycle 改版「專利狀態分析」（2026-08-07 使用者定案，openspec improve-report-professionalism）。

## 🔴 2026-08-09 現況：`lifecycle` 報表已由使用者裁決刪除

registry、SECTION_SPECS、CHART_FILE_REPORTS、讀圖說明、母體登記與前端項目均已移除。
本檔仍然成立的部分是**與該報表無關的共用零件**：`transforms/legal_status` 的狀態桶
（`country_distribution` 仍在用）與 `render_matrix_chart`（公司×國家矩陣仍在用）。

## 🔴 2026-08-10：死程式與其測試已一併移除

`chart_runner._build_lifecycle_section` 與 `lifecycle_status_pivot` 沒有任何
SectionSpec 指得到，已是死程式，故連同 `LifecycleSectionTests` 與
`LifecycleBuilderIntegrationTests` 一起刪除。

⚠ 為什麼不「留測試綠著就好」：那兩組測試在守一條永遠不會執行的路徑——它們綠著，
卻證明不了任何線上行為，反而讓人以為該功能仍有覆蓋。裁撤理由逐處留在原位註解。

## 定案（2026-08-07，已於 2026-08-09 被刪除決策取代）

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

    def test_tw_curation_nine_values_bucket_correctly(self):
        """🔴 銜接 openspec `add-tw-legal-status-curation`（Codex 線）：
        人工登錄的九項 TW 狀態字面必須各自落進正確桶。

        ⚠ 沒有這一段，「已核准」會被判成**未知**——登錄了等於白登。
        桶邏輯唯一定義處在本模組；Codex 端只消費、不得另寫 mapping
        （它 spec 裡的 pending/alive/dead/unknown 彙總即對應本表四桶）。
        """
        expected = {
            "已申請": "審查中",     # pending
            "已公開": "審查中",     # pending
            "審查中": "審查中",     # pending
            "已核准": "已授權",     # alive
            "放棄": "已失效",       # dead
            "核駁": "已失效",       # dead
            "撤回": "已失效",       # dead
            "已失效": "已失效",     # dead
            "屆滿失效": "已失效",   # dead
        }
        for value, bucket in expected.items():
            with self.subTest(value=value):
                self.assertEqual(self._bucket(value), bucket)

    def test_bucket_order_is_stable(self):
        """桶順序是呈現契約（堆疊段序／圖例序），固定不得隨 dict 序漂。"""
        from backend.app.transforms.legal_status import STATUS_BUCKET_ORDER

        self.assertEqual(STATUS_BUCKET_ORDER, ("已授權", "審查中", "已失效", "未知"))


class LifecycleDefinitionTests(unittest.TestCase):
    """🔴 2026-08-09 契約變更：`lifecycle` 報表由使用者裁決**刪除**。

    原因：申請人×法律狀態交叉後每格件數極少，圖上讀不出模式；法律狀態的判讀
    改由 `country_distribution`（國別×法律狀態）承接。

    ⚠ 原 `test_definition_reshaped` 隨之裁撤——registry 已無此鍵，
    「確實移除」由 tests/test_report_catalog_and_population.py 統一守著，
    本檔不重複第二份。前端項目移除則保留下方測試把關。
    """

    def test_frontend_report_item_removed(self):
        from pathlib import Path

        html = (Path(__file__).resolve().parents[1] / "backend" / "app" / "static"
                / "index.html").read_text(encoding="utf-8")
        flat = html.replace('"', "'")
        self.assertNotIn("'lifecycle'", flat, "前端仍列著已刪除的 lifecycle 報表")
        # 舊名反向鎖照留：改名後不得又冒出來（2026-08-07 起）。
        self.assertNotIn("專利生命週期", html)
        self.assertNotIn("專利狀態分析", html)


# 🔴 LifecycleSectionTests 已整個裁撤（2026-08-10）：它守的
# `chart_runner.lifecycle_status_pivot` 已隨報表刪除一併移除。
#
# ⚠ 那個函式在 2026-08-09 之後就沒有任何 SectionSpec 指得到（唯一呼叫端是
# `_build_lifecycle_section`，而它自己也沒人叫），本組測試等於在守一條永遠不會
# 執行的路徑——測試綠著，卻證明不了任何線上行為。
#
# 它原本涵蓋的兩件事現在各有歸屬：狀態桶收斂由 `StatusBucketTests`（本檔上方，
# `country_distribution` 仍在用）守；「報表確實已移除」由
# `test_report_catalog_and_population.py` 守。
#
# 🔴 test_over_counting_note_applies 亦已於 2026-08-09 裁撤（同上，登記已清）。


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


# 🔴 LifecycleBuilderIntegrationTests 已裁撤（2026-08-10）：它端到端呼叫
# `chart_runner._build_lifecycle_section`，而該 builder 已隨 lifecycle 報表刪除
# 一併移除（沒有任何 SectionSpec 指得到它，是死程式）。
#
# ⚠ 它驗的兩件事仍有守門者：矩陣渲染器 `render_matrix_chart` 由上方
# `StatusMatrixSvgTests` 守（公司×國家矩陣仍在用同一支）；「報表確實已從
# registry／population 移除」由 `test_report_catalog_and_population.py` 守。


if __name__ == "__main__":
    unittest.main()
