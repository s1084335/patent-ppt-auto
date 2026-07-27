"""文獻備註的來源欄必須與分群技術通道一致（2026-07-27 使用者指出）。

實機發現：`ai_patent_note_runner.CLAIM_COLUMN = "主權項"`，但分群技術通道用的是
`獨立項[KR,JP,US,CN,EP,IN]`（見 `clustering/sources.py` 的 source_column）。
兩者**不是同一份文字**：

    有主權項 49 筆｜有獨立項 40 筆｜只有主權項 9 筆｜只有獨立項 0 筆

也就是「主權項」涵蓋較廣（含附屬項等），獨立項是它的子集。後果：

1. **來源不一致**：備註描述的內容與分群依據的文字不同，看備註判斷主題會失準。
2. **9 筆備註「多出來」**：那 9 筆沒有獨立項、本來就不該進技術分群，卻有備註——
   若拿它去補技術通道的分類建議，等於用主權項的內容冒充獨立項。

使用者定案：**改讀獨立項，舊備註全清重產**（來源與分群一致優先）。
副作用是那 9 筆將不再有備註，但它們本來就不進技術分群。

⚠ 本測試鎖「兩處來源欄相同」而非鎖死字面值——日後若分群改欄，備註要跟著改，
不該再出現兩邊各自寫死、悄悄分岔的情況（本專案本日已 13 次同型斷鏈）。
"""
from __future__ import annotations

import unittest


class PatentNoteSourceColumnTests(unittest.TestCase):
    def test_note_source_matches_technical_clustering_source(self):
        """備註來源欄 == 技術通道的 source_column。"""
        from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL, get_source_spec
        from backend.app.worker.ai_patent_note_runner import CLAIM_COLUMN

        expected = get_source_spec(SOURCE_FIELD_TECHNICAL).source_column
        self.assertEqual(
            CLAIM_COLUMN, expected,
            "文獻備註的來源欄與分群技術通道不一致——備註描述的內容會與分群依據不同")

    def test_source_is_independent_claim_not_main_claim(self):
        """明確不是「主權項」：那欄涵蓋較廣，不是分群用的獨立項。"""
        from backend.app.worker.ai_patent_note_runner import CLAIM_COLUMN

        self.assertNotEqual(
            CLAIM_COLUMN, "主權項",
            "備註不得讀主權項——分群技術通道讀的是獨立項，兩者不是同一份文字")
        self.assertIn("獨立項", CLAIM_COLUMN)


if __name__ == "__main__":
    unittest.main()
