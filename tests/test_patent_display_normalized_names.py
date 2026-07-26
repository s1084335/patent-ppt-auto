"""專利顯示欄位的公司名口徑契約（純字串檢查，不需 DB）。

定案（2026-07-26 使用者）：專利表格顯示**正規化後**的公司名（與報表同一口徑），
原始字面移到點開的詳情層保留，沿既有「標題／標題(原文)」「摘要／摘要(原文)」模式。

動因：先前表格顯示 `patent_people` 原始字面（只做 NULLIF+BTRIM，未收斂），
報表卻顯示 `report_patent_base` 的 COALESCE 收斂名——同一件專利兩處兩個名字，
且搜尋走正規化名，使用者照表格字面搜尋會搜不到。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from backend.app.app_layer import patent_queries


STATIC_INDEX = Path(__file__).resolve().parents[1] / "backend" / "app" / "static" / "index.html"


class NormalizedNameProjectionTests(unittest.TestCase):
    """三個公司欄位的正規化名都要被投影出來，供前端顯示。"""

    # (顯示欄 key, report_patent_base 來源欄)
    NORMALIZED_FIELDS = (
        ("applicant", "applicant_display_name"),
        ("current_owner", "current_assignee_display_name"),
        ("recent_assignee", "recent_assignee_display_name"),
    )

    def test_projection_exposes_normalized_company_names(self):
        """正規化名須在顯示欄位定義中，前端才拿得到（非僅供搜尋）。"""
        keys = patent_queries.display_field_keys()
        for display_key, _source in self.NORMALIZED_FIELDS:
            with self.subTest(field=display_key):
                self.assertIn(display_key, keys, f"顯示欄位缺 {display_key}")

    def test_original_values_kept_as_separate_fields(self):
        """原始字面另存 *_original 欄，詳情層據此對照，不因正規化而遺失來源。"""
        keys = patent_queries.display_field_keys()
        for display_key, _source in self.NORMALIZED_FIELDS:
            with self.subTest(field=display_key):
                self.assertIn(
                    f"{display_key}_original", keys,
                    f"缺 {display_key}_original，詳情層看不到原始字面",
                )

    def test_normalized_names_come_from_report_patent_base(self):
        """正規化名取自 report_patent_base（與報表同一口徑），非自行再算一套。"""
        cte = patent_queries._CANDIDATES_CTE
        for _display_key, source in self.NORMALIZED_FIELDS:
            with self.subTest(source=source):
                self.assertIn(source, cte, f"CTE 未取 rpb.{source}")


class FrontendCompanyColumnTests(unittest.TestCase):
    """前端欄位定義：正規化名在列表、原文在詳情層。"""

    def setUp(self):
        self.html = STATIC_INDEX.read_text(encoding="utf-8")

    def _column_block(self, label: str) -> str:
        """取 PATENT_COLUMNS 中該 label 的那一段定義字串。"""
        match = re.search(r"\{[^{}]*label:\s*'" + re.escape(label) + r"'[^{}]*\}", self.html)
        self.assertTrue(match is not None, f"PATENT_COLUMNS 找不到欄位：{label}")
        return match.group(0)

    def test_company_columns_use_normalized_keys(self):
        """申請人／最近專利權人欄綁正規化 key，不再綁原始字面欄。"""
        for label, key in (("申請人", "applicant"), ("最近專利權人", "current_owner")):
            with self.subTest(label=label):
                block = self._column_block(label)
                self.assertTrue(
                    f"key: '{key}'" in block,
                    f"{label} 欄未綁正規化 key '{key}'，實際定義：{block}",
                )

    def test_original_company_columns_exist_in_detail(self):
        """原文欄存在（詳情層可對照原始 WIPS 字面）。

        ⚠ 斷言不可用 assertIn(needle, self.html)——失敗訊息會把整份 HTML 印出來
        （逾萬字），真正的訊號被淹沒。改用布林斷言＋自寫訊息。
        """
        for label in ("申請人(原文)", "最近專利權人(原文)"):
            with self.subTest(label=label):
                self.assertTrue(
                    f"label: '{label}'" in self.html, f"PATENT_COLUMNS 缺原文欄：{label}"
                )


class FullColumnListingTests(unittest.TestCase):
    """全欄位攤在列表、橫向捲動（2026-07-26 定案，推翻原「只 listOnly 上列表」）。"""

    def setUp(self):
        self.html = STATIC_INDEX.read_text(encoding="utf-8")

    def test_table_does_not_filter_by_list_only(self):
        """patentTableHtml 不得再以 listOnly 過濾欄位——那會讓多數欄退回第二層。"""
        self.assertTrue(
            "cols.filter(c => c.listOnly)" not in self.html,
            "patentTableHtml 仍以 listOnly 過濾，全欄位不會出現在列表",
        )

    def test_table_wrap_supports_horizontal_scroll(self):
        """橫向捲動由 .table-wrap 的 overflow-x 提供，全欄呈現才不會爆版。"""
        self.assertTrue(
            "overflow-x: auto" in self.html,
            ".table-wrap 缺 overflow-x: auto，全欄位會撐破版面且無法捲動",
        )


class PatentDetailLayoutTests(unittest.TestCase):
    """詳情層版面：比照專利公報書目資料（標籤欄＋值欄逐列，分節）。"""

    def setUp(self):
        self.html = STATIC_INDEX.read_text(encoding="utf-8")

    def test_detail_uses_single_column_rows(self):
        """不再用多欄 grid：值長度差異大，多欄會讓長值擠窄格、短值留空白。"""
        self.assertTrue(
            "grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))" not in self.html,
            "詳情層仍是多欄 grid 版面",
        )

    def test_detail_label_column_styled(self):
        """標籤欄有固定寬與底色（公報樣式的辨識特徵）。"""
        self.assertTrue("--detail-label-bg" in self.html, "缺標籤欄底色變數")
        self.assertTrue(
            "--detail-label-bg" in self.html.split('data-theme="dark"')[1],
            "暗色主題未定義標籤欄底色",
        )

    def test_detail_sections_defined(self):
        """分節存在，且第二節以 keys: null 收容其餘欄位（新增欄位免同步）。"""
        self.assertTrue("DETAIL_SECTIONS" in self.html, "缺詳情分節定義")
        self.assertTrue("keys: null" in self.html, "缺收容其餘欄位的節")


if __name__ == "__main__":
    unittest.main()
