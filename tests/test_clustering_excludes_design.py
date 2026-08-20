"""分群母體必須**照身分**排除設計案，不得靠「有沒有獨立項」當代理（2026-08-20）。

## 病徵（實測）

| | 設計案 | 進技術分群 | 其中設計案 |
|---|---|---|---|
| 滑雪機 | 11 | 44 ＝ 55−11 | **0** ✓ |
| 割草機 | 10 | **217** ≠ 226−10＝216 | **1 件（patent_id 452）** ❌ |

滑雪機看起來正確是**巧合**——它 11 件設計案剛好都沒有獨立項文字，於是
「篩掉沒有獨立項的」恰好等於「篩掉設計案」。割草機有 1 件設計案帶了獨立項文字，
就漏進技術分群。

⚠ 這是**代理指標**：拿「有沒有獨立項文字」代理「是不是技術案」。
兩者在甲批等價、在乙批分岔，而分岔**完全靜默**——不報錯、不警告，
只有母體對帳行的數字會差 1。

⚠ 而且系統對外**宣稱**已經排除了：`patent_kind.design_exclusion_note` 印的是
「設計 N 件無技術請求項，不列入主題分類」。對 452 那件而言，這句話是假的。

## 判準

1. 排除依據 SHALL 是 `document_kind`（`is_design` 的唯一入口），不是文字有無
2. 規則**只能有一個定義處**——SQL 不得自己寫 `'S'`／`'S1'`，
   要把 `DESIGN_DOCUMENT_KINDS` 當參數餵進去
3. 兩件事要分開驗：查詢**有**排除子句、參數**來自**那個常數
"""
from __future__ import annotations

import re
import unittest

from backend.app.clustering import runner
from backend.app.transforms.patent_kind import DESIGN_DOCUMENT_KINDS


class DesignExclusionInCorpusTests(unittest.TestCase):
    def test_corpus_query_excludes_design(self):
        """語料查詢必須帶設計案排除子句。"""
        self.assertTrue(hasattr(runner, "build_corpus_query"),
                        "應把查詢組裝抽成可測的函式")
        query, params = runner.build_corpus_query(
            source_field="wips_independent_claims", workspace_id=11)
        text = str(query)
        self.assertIn("document_kind", text,
                      "查詢沒有依 document_kind 排除設計案——"
                      "只篩文字有無是代理指標")

    def test_design_kinds_come_from_the_single_definition(self):
        """⚠ SQL 不得自己寫 'S'／'S1'：規則的唯一定義處是 `DESIGN_DOCUMENT_KINDS`。

        散開後改一處另一處不會報錯——`patent_kind` 的模組說明已經寫過這條。
        """
        query, params = runner.build_corpus_query(
            source_field="wips_independent_claims", workspace_id=11)
        text = str(query)
        for literal in ("'S'", "'S1'", '"S"', '"S1"'):
            self.assertNotIn(literal, text,
                             f"查詢裡出現寫死的 {literal}——應由參數帶入")
        flat = [x for p in params for x in (p if isinstance(p, (list, tuple, set)) else [p])]
        self.assertTrue(
            set(DESIGN_DOCUMENT_KINDS) <= set(map(str, flat)),
            f"參數沒帶 DESIGN_DOCUMENT_KINDS：{params}")

    def test_workspace_scope_still_applied(self):
        """⚠ 反向：加排除不得弄掉 workspace 範圍——那會讓分群吃到全庫。"""
        query, params = runner.build_corpus_query(
            source_field="wips_independent_claims", workspace_id=11)
        self.assertIn("wp.patent_id", str(query))
        self.assertIn(11, list(params))

    def test_global_scope_has_no_workspace_join(self):
        """全庫範圍不帶 workspace join，但**仍要**排除設計案。"""
        query, params = runner.build_corpus_query(
            source_field="wips_independent_claims", workspace_id=None)
        text = str(query)
        self.assertNotIn("wp.patent_id", text)
        self.assertIn("document_kind", text)

    def test_text_filter_kept(self):
        """⚠ 反向：排除設計案**不取代**文字有無的篩選。

        沒有文字的案子仍然分不了群——兩個條件是 AND 不是二選一。
        """
        query, _ = runner.build_corpus_query(
            source_field="wips_independent_claims", workspace_id=11)
        self.assertIn("IS NOT NULL", str(query))


if __name__ == "__main__":
    unittest.main()
