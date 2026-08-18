"""設計案標籤（P1 五之三，2026-08-05 定案；2026-08-07 動工）。

設計案不進分群但要有標籤：判定唯一入口＝transforms/patent_kind（document_kind
優先，不可用 patent_type 判設計）；種類用既有兩欄組合推導，不新增 DB 欄。
落點：兩個專利清單 API 帶 patent_kind_display（發明/新型/設計/未標示），
前端專利表顯示「專利種類」欄——11 件設計案的技術/功效分類空白因此可被
讀者理解為「設計案本來就不分」，不是漏分。
"""
from __future__ import annotations

import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "backend" / "app" / "static"
        / "index.html").read_text(encoding="utf-8")


class ListApiKindTests(unittest.TestCase):
    """⚠ 2026-08-18 改寫：原本斷言函式原始碼裡出現 `patent_kind_display` 字串。

    那種寫法有兩個問題：①推導一旦被收斂成共用函式（正確的方向）測試就紅，
    ②它其實**沒有驗到行為**——字串在、值錯了照樣綠。而且它只盯 `list_patents`
    與 `list_workspace_patents` 兩支，正是因此漏掉 `list_topic_patents`，
    分類區點進主題後「專利種類／專利狀態」整欄空白（使用者 2026-08-18 回報）。

    改為驗真行為（推導函式的輸出），三支清單有沒有呼叫它由
    `test_patent_list_display_fields_gate.py` 一起守。
    """

    def test_kind_display_is_derived_not_raw(self):
        from backend.app.app_layer.patent_queries import attach_display_fields

        rows = [
            {"document_kind": "S", "patent_type": "P"},      # 設計
            {"document_kind": "A", "patent_type": "P"},      # 發明
            {"document_kind": "A", "patent_type": "U"},      # 新型
            {},                                              # 兩欄皆缺
        ]
        attach_display_fields(rows)
        self.assertEqual([r["patent_kind_display"] for r in rows[:3]],
                         ["設計", "發明", "新型"])
        self.assertIn("patent_kind_display", rows[3],
                      "來源無值時仍須保留欄位（欄位一律呈現）")

    def test_every_list_uses_the_shared_derivation(self):
        """三支清單都要補——漏一支就是整欄空白。"""
        import inspect

        from backend.app.app_layer import patent_queries as q
        from backend.app.app_layer import workspace_queries as wq

        for fn in (q.list_patents, wq.list_workspace_patents, wq.list_topic_patents):
            with self.subTest(function=fn.__name__):
                self.assertIn("attach_display_fields", inspect.getsource(fn))

    def test_kind_uses_single_definition(self):
        """推導一律走 transforms/patent_kind.patent_kind，不得另寫組合條件。"""
        from backend.app.transforms.patent_kind import patent_kind

        self.assertEqual(patent_kind({"document_kind": "S", "patent_type": "P"}), "設計")
        self.assertEqual(patent_kind({"document_kind": "A", "patent_type": "P"}), "發明")
        self.assertEqual(patent_kind({"document_kind": "U", "patent_type": "U"}), "新型")
        self.assertEqual(patent_kind({}), "未標示")


class FrontendKindColumnTests(unittest.TestCase):
    def test_patent_table_has_kind_column(self):
        self.assertIn("'patent_kind_display'", HTML)
        self.assertIn("專利種類", HTML)

    def test_raw_patent_type_column_removed(self):
        """🔴 2026-08-07 使用者定案：兩層都只用專利種類，原始 patent_type 欄收掉
        （P 同時蓋發明與設計）；表頭帶推導說明 tooltip。"""
        self.assertNotIn("{ label: '專利類型', key: 'patent_type'", HTML)
        self.assertIn("由 document_kind＋patent_type 推導", HTML)


if __name__ == "__main__":
    unittest.main()
