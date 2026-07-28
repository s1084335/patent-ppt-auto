"""文獻備註的三級來源順位（2026-07-28 使用者定案）。

## 決策沿革（同一天內兩次調整，都有實據）

1. 早上 `fd5458f`：備註由「主權項」改讀「獨立項」——理由是與分群技術通道同源。
   當時發現主權項 49 筆／獨立項 40 筆，差額 9 筆未查國別。
2. 晚間查清那 9 筆**全是 TW**：`獨立項[KR,JP,US,CN,EP,IN]` 欄名列的六國不含 TW，
   TW 0/9。而使用者要用備註當「無獨立項專利的 AI 補分輸入」——只讀獨立項會讓
   TW 兩邊皆空，補分機制自我堵死。
3. 再查兩欄皆空的 11 筆＝CN 外觀設計（洛迦諾分類 21-02／19-07），權利要求四欄
   全空是專利類型本質；但它們有 `abstract` 11/11、最長 530 字。

使用者定案：**獨立項 → 所有權利要求 → abstract** 三級，全庫 60/60 覆蓋。

## 實測覆蓋率

    國別  總   獨立項  所有權利要求  abstract
    CN    39    28        28         39
    US     9     9         9          9
    EP     3     3         3          3
    TW     9     0         9          9      ← 第二級救回
    兩欄皆空的 11 筆 CN 外觀設計 ← 第三級救回

## 技術通道分群不受影響

分群**固定讀獨立項、不 fallback**（使用者明示），維持 40 筆的純淨切分。
備註覆蓋較廣是刻意的——多出來的那 20 筆正是要交給 AI 補分的對象。
"""
from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFRESH_SQL = (PROJECT_ROOT / "backend" / "app" / "derived"
               / "refresh_report_patent_base.py").read_text(encoding="utf-8")


class DerivedCarriesAbstractTests(unittest.TestCase):
    """derived 要搬 abstract（第三級來源）。"""

    def test_abstract_selected(self):
        self.assertIn(
            "abstract", REFRESH_SQL,
            "derived 沒搬 abstract——core 有值（11 筆外觀設計 530 字）但備註取不到")

    def test_abstract_in_insert_list(self):
        """SELECT、欄位清單、外層 SELECT 至少三處。"""
        self.assertGreaterEqual(
            REFRESH_SQL.count("abstract"), 3,
            "abstract 出現次數不足——多半漏了 INSERT 欄位清單或外層 SELECT")


class NoteSourceTierTests(unittest.TestCase):
    """備註三級順位，且只有一處定義。"""

    def test_tier_constant_exists(self):
        """順位要有具名常數，不得散落在 runner 內各自寫 COALESCE。"""
        from backend.app.clustering import sources

        self.assertTrue(
            hasattr(sources, "PATENT_NOTE_SOURCE_COLUMNS"),
            "缺少備註來源順位的單一定義——散寫會重演本專案的多處實作分岔")

    def test_first_tier_is_the_clustering_source(self):
        """第一級必須等於分群技術通道的來源欄——不得兩處各寫一份欄名。

        ⚠ 通用性：不鎖字面欄名。分群來源日後若改（例如 WIPS 換欄名），備註第一級
        要自動跟著改，而不是留在這裡等人手動同步——本專案已多次因兩處各自寫死而分岔。
        """
        from backend.app.clustering.sources import (
            PATENT_NOTE_SOURCE_COLUMNS,
            SOURCE_FIELD_TECHNICAL,
            get_source_spec,
        )

        self.assertEqual(
            PATENT_NOTE_SOURCE_COLUMNS[0],
            get_source_spec(SOURCE_FIELD_TECHNICAL).source_column,
            "備註第一級與分群來源不同——有獨立項的專利，備註內容會與分群依據不一致")

    def test_has_three_tiers(self):
        """三級：分群來源 → 較寬的權利要求 → 摘要（全類型都有的保底）。"""
        from backend.app.clustering.sources import PATENT_NOTE_SOURCE_COLUMNS

        self.assertEqual(len(PATENT_NOTE_SOURCE_COLUMNS), 3)

    def test_last_tier_is_universal_fallback(self):
        """最後一級必須是所有專利類型都有的欄位。

        外觀設計沒有任何權利要求（實測 CN 11 筆四欄全空），只有摘要 11/11、
        最長 530 字。若最後一級仍是權利要求類，那批專利永遠沒有備註、
        AI 補分也拿不到輸入。
        """
        from backend.app.clustering.sources import PATENT_NOTE_SOURCE_COLUMNS

        last = PATENT_NOTE_SOURCE_COLUMNS[-1]
        self.assertNotIn("權利", last, "最後一級仍是權利要求類——外觀設計取不到")
        self.assertNotIn("獨立項", last)

    def test_main_claim_excluded(self):
        """使用者明示排除主權項——它涵蓋附屬項，語意比獨立項雜。"""
        from backend.app.clustering.sources import PATENT_NOTE_SOURCE_COLUMNS

        for col in PATENT_NOTE_SOURCE_COLUMNS:
            self.assertNotIn("主權項", col)

    def test_columns_exist_in_derived(self):
        """三級欄位都必須真的存在於 derived 寬表——寫錯欄名會靜默取不到值。

        ⚠ 這條是通用性的實際護欄：日後改順位或換欄名時，若 derived 沒有該欄，
        這裡當場 red，不會等到使用者發現備註是空的。
        """
        from backend.app.clustering.sources import PATENT_NOTE_SOURCE_COLUMNS

        for col in PATENT_NOTE_SOURCE_COLUMNS:
            with self.subTest(col=col):
                self.assertIn(
                    f'"{col}"' if col != "abstract" else "abstract", REFRESH_SQL,
                    f"{col} 不在 derived refresh 的搬運清單——備註會取不到值")

    def test_runner_uses_the_constant(self):
        """runner 讀常數，不自己寫死欄名。"""
        import inspect
        from backend.app.worker import ai_patent_note_runner as r

        src = inspect.getsource(r)
        self.assertIn("PATENT_NOTE_SOURCE_COLUMNS", src)


class ClusteringStillIndependentOnlyTests(unittest.TestCase):
    """分群技術通道固定獨立項，不得跟著 fallback。"""

    def test_technical_source_column_unchanged(self):
        from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL, get_source_spec

        self.assertEqual(
            get_source_spec(SOURCE_FIELD_TECHNICAL).source_column,
            "獨立項[KR,JP,US,CN,EP,IN]",
            "分群技術通道被改成 fallback——使用者明示只能用獨立項")


if __name__ == "__main__":
    unittest.main()
