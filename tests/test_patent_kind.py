"""專利種類三分法的單一定義處（A4，2026-08-06）。

## 為什麼要有這一層

設計案 11 件已被自動排除在兩個分群外（無獨立項、無效果摘要），
但**簡報上完全沒說**——讀者看到封面 55、分類頁 44 只會覺得數字錯。

## 🔴 判定只能用 `document_kind`，不能用 `patent_type`

`patent_type` 的 WIPS 來源欄是 **`发明专利/实用新型`**——欄名就寫明它只有兩值，
**11 件設計案全被歸進 `P`**。實測：

    patent_type='P' 共 28 件，其中 11 件是設計案 → 實際發明只有 17 件

用 `patent_type` 判定會把設計案當成發明。這不是資料髒，是那一欄的值域本來就只有兩類。

## 三分法（唯一定義處）

    設計 ＝ document_kind = 'S'
    新型 ＝ patent_type   = 'U'
    發明 ＝ patent_type   = 'P' AND document_kind <> 'S'

實測 55 件：設計 11＋新型 27＋發明 17 ＝ 55 ✅
"""
from __future__ import annotations

import unittest


class PatentKindClassificationTests(unittest.TestCase):
    """三分法本身。"""

    def _kind(self, patent_type, document_kind):
        from backend.app.transforms.patent_kind import patent_kind

        return patent_kind({"patent_type": patent_type, "document_kind": document_kind})

    def test_design_is_detected_by_document_kind(self):
        """設計案只認 document_kind='S'。"""
        self.assertEqual(self._kind("P", "S"), "設計")

    def test_design_wins_over_patent_type_p(self):
        """🔴 關鍵案例：WIPS 把設計案標成 P，種類仍必須是設計不是發明。"""
        self.assertEqual(self._kind("P", "S"), "設計",
                         "用 patent_type 判定會把 11 件設計案當成發明")

    def test_utility_model(self):
        self.assertEqual(self._kind("U", "U"), "新型")

    def test_invention_requires_p_and_not_design(self):
        for dk in ("A", "A1", "B", "B1", "B2"):
            with self.subTest(document_kind=dk):
                self.assertEqual(self._kind("P", dk), "發明")

    def test_unknown_when_both_missing(self):
        """⚠ 兩欄皆空回「未標示」，不得預設成發明——那會灌高發明件數。"""
        self.assertEqual(self._kind(None, None), "未標示")
        self.assertEqual(self._kind("", ""), "未標示")

    def test_whitespace_is_treated_as_missing(self):
        self.assertEqual(self._kind("  ", " "), "未標示")

    def test_design_detected_even_when_patent_type_missing(self):
        """document_kind 有 S 就是設計，不管 patent_type 有沒有值。"""
        self.assertEqual(self._kind(None, "S"), "設計")


class KindTallyTests(unittest.TestCase):
    """整批統計與母體說明。"""

    def _rows(self):
        # 對齊實測分布：設計 11／新型 27／發明 17
        return (
            [{"patent_type": "P", "document_kind": "S"}] * 11
            + [{"patent_type": "U", "document_kind": "U"}] * 27
            + [{"patent_type": "P", "document_kind": "A"}] * 17
        )

    def test_tally_matches_measured_distribution(self):
        from backend.app.transforms.patent_kind import kind_tally

        tally = kind_tally(self._rows())
        self.assertEqual(tally["設計"], 11)
        self.assertEqual(tally["新型"], 27)
        self.assertEqual(tally["發明"], 17)
        self.assertEqual(sum(tally.values()), 55, "三類相加必須等於總件數")

    def test_design_exclusion_note(self):
        """設計案的母體說明：要同時交代**排除誰**與**為什麼**。"""
        from backend.app.transforms.patent_kind import design_exclusion_note

        note = design_exclusion_note(self._rows())
        self.assertIn("11", note)
        self.assertIn("設計", note)
        self.assertIn("技術請求項", note, "只寫件數不寫原因，讀者仍不知道為何被排除")

    def test_no_note_when_no_design_patents(self):
        """⚠ 沒有設計案時不得硬印一句——那會讓讀者以為有東西被排除。"""
        from backend.app.transforms.patent_kind import design_exclusion_note

        rows = [{"patent_type": "P", "document_kind": "A"}] * 5
        self.assertEqual(design_exclusion_note(rows), "")

    def test_cover_summary_line(self):
        """封面備註：總量／分析母體／設計案三個數字一句話講完。"""
        from backend.app.transforms.patent_kind import kind_summary

        line = kind_summary(self._rows())
        for token in ("55", "44", "11"):
            self.assertIn(token, line, f"缺少 {token}：{line}")


class KindSummaryEdgeTests(unittest.TestCase):
    """⚠ 沒有設計案時的摘要句不得硬提設計。"""

    def test_summary_without_design_patents(self):
        from backend.app.transforms.patent_kind import kind_summary

        rows = [{"patent_type": "P", "document_kind": "A"}] * 5
        line = kind_summary(rows)
        self.assertEqual(line, "總量 5 件")
        self.assertNotIn("設計", line, "沒有設計案卻提到設計，讀者會以為有東西被排除")


class FetchPatentKindSummaryTests(unittest.TestCase):
    """chart runner 的 DB 接縫：只撈資料，分類仍走唯一定義處。"""

    def test_fetch_summary_uses_rows_from_report_base(self):
        """用 fake connection 驗 SQL 與輸出，不碰本機 postgres。"""
        from unittest import mock

        from backend.app.reports import chart_runner

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, sql):
                self.sql = sql

            def fetchall(self):
                return [
                    {"patent_type": "P", "document_kind": "S"},
                    {"patent_type": "U", "document_kind": "U"},
                    {"patent_type": "P", "document_kind": "A"},
                ]

        class _Conn:
            def __init__(self):
                self.cursor_obj = _Cursor()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def cursor(self):
                return self.cursor_obj

        conn = _Conn()
        with mock.patch.object(chart_runner, "_app_layer_connect", return_value=conn):
            summary = chart_runner.fetch_patent_kind_summary()

        self.assertIn("derived_layer.report_patent_base", conn.cursor_obj.sql)
        self.assertEqual(summary["tally"], {"設計": 1, "新型": 1, "發明": 1})
        self.assertIn("設計 1 件", summary["design_note"])


class SingleDefinitionTests(unittest.TestCase):
    """🔴 不得有第二處自己判定設計案。"""

    def test_no_hardcoded_design_check_outside_this_module(self):
        """全庫搜 `document_kind == 'S'` 這類字面判定，只允許出現在唯一定義處。

        ⚠ 本專案已四次因「同一份知識兩處落點」靜默失敗；判定條件散開後，
        改了一處另一處不會報錯，只會兩邊數字不一樣。
        """
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "backend" / "app"
        allowed = {"patent_kind.py"}
        offenders = []
        for path in root.rglob("*.py"):
            if path.name in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            for marker in ("document_kind == 'S'", 'document_kind == "S"',
                           "document_kind='S'", 'document_kind = \'S\''):
                if marker in text:
                    offenders.append(f"{path.relative_to(root)}: {marker}")
        self.assertEqual(offenders, [],
                         f"設計案判定散落在多處，改一處另一處不會報錯：{offenders}")


# ⚠ CoverDesignNoteTests 已隨 PPT 交付線移除（2026-08-10，remove-ppt-delivery-line）。


if __name__ == "__main__":
    unittest.main()
