r"""多值申請人（`A | B`）拆分與 curation 標記誤擋（2026-07-28 使用者實機發現）。

## 問題一：`|` 分隔的多申請人被當成單一名稱

使用者原話：「像這種 | 隔開的要拆成兩筆」

WIPS 匯出用 ` | ` 分隔同一筆專利的多個申請人／專利權人／受讓人。系統把整串
當一個名稱，實測 60 筆庫內就有：

| 欄位 | 含 `\|` 筆數 |
|---|---|
| 申請人 | 14 |
| 最近專利權人 | 10 |
| 最近受讓人 | 1 |

造成三個後果：

1. 待補清單出現「XIAMEN DMASTER HEALTH TECH Co.,Ltd. | Zeng Qing」這種
   **不存在的公司**，使用者要拿它去 WIPS 查代碼會查不到
2. `Zinur Akhmetov | Alfiya Sharipova` 兩個自然人被當成一家公司
3. 同一家公司因「後面接的共同申請人不同」散成多筆
   （`... | Zeng Qing` 與 `... | TSENG, CHING` 是兩筆），**收斂不起來**

## 定案（使用者選定）

- **顯示只取主申請人**＝`|` 前的第一個。WIPS 慣例第一個是主申請人。
- **待補清單不濾自然人**：全部列出，由使用者決定要不要建組；
  系統不推斷誰是公司誰是個人（沿「系統不預先分組」紅線）。

## 問題二：curation 標記把「剛建組、還沒中文名」的組也擋掉

使用者實機：AI 中文名草稿 job #79／#80 都 succeeded 但只跑 3.4 秒，
畫面顯示「目前沒有待確認的中文名草稿」。

根因：`PENDING_SQL` 用 `source_file LIKE 'display_name_curation%'` 判定
「已裁決過中文名」，但該標記現在有**兩種**寫入時機：

| 誰寫的 | 意思 | 該不該擋 |
|---|---|---|
| 中文名確認流程 | 已裁決（含 keep_original） | ✅ 該擋 |
| **代碼補齊區塊「確定寫入資料庫」** | 只是建組，中文名還空著 | 🔴 **不該擋** |

第二種是 2026-07-28 新加的功能，沿用同一個標記（當時理由是「整合而非另造」），
沒發現會誤觸這條排除。同一個標記承載兩種語意——本日第 19 次同型問題。

⚠ 不能單純拿掉那條：keep_original 裁決後中文欄仍是空的，只有 curation 標記
能區分「查過查無」與「還沒查」。修法是**分辨兩種 curation 來源**。
"""
from __future__ import annotations

import unittest


class SplitMultiValueTests(unittest.TestCase):
    """`split_multi_value` 拆分函式的契約。"""

    def test_splits_on_pipe(self):
        from backend.app.transforms.text import split_multi_value

        self.assertEqual(
            split_multi_value("XIAMEN DMASTER HEALTH TECH Co.,Ltd. | Zeng Qing"),
            ["XIAMEN DMASTER HEALTH TECH Co.,Ltd.", "Zeng Qing"],
        )

    def test_strips_surrounding_space(self):
        """分隔符前後空白不得留在名稱裡——留著會讓 lookup key 對不上。"""
        from backend.app.transforms.text import split_multi_value

        self.assertEqual(split_multi_value("A  |  B"), ["A", "B"])

    def test_single_value_returns_one(self):
        from backend.app.transforms.text import split_multi_value

        self.assertEqual(split_multi_value("Acme Corp"), ["Acme Corp"])

    def test_empty_and_none(self):
        from backend.app.transforms.text import split_multi_value

        self.assertEqual(split_multi_value(""), [])
        self.assertEqual(split_multi_value(None), [])

    def test_drops_empty_segments(self):
        """`A |  | B` 中間的空段丟掉，不得產生空字串名稱。"""
        from backend.app.transforms.text import split_multi_value

        self.assertEqual(split_multi_value("A |  | B"), ["A", "B"])

    def test_three_values(self):
        from backend.app.transforms.text import split_multi_value

        self.assertEqual(split_multi_value("A | B | C"), ["A", "B", "C"])

    def test_primary_is_first(self):
        """主申請人＝第一個（使用者定案：顯示只取主申請人）。"""
        from backend.app.transforms.text import primary_value

        self.assertEqual(
            primary_value("XIAMEN DMASTER HEALTH TECH Co.,Ltd. | Zeng Qing"),
            "XIAMEN DMASTER HEALTH TECH Co.,Ltd.",
        )
        self.assertIsNone(primary_value(""))


class PendingListSplitsTests(unittest.TestCase):
    """待補清單要列拆分後的個別名稱，不是整串。"""

    def test_pending_sql_splits_pipe(self):
        """待補清單 SQL 必須對名稱做 `|` 展開。

        ⚠ 驗 SQL 有沒有展開運算式，不驗字面——用 regexp_split_to_table
        或 unnest(string_to_array(...)) 都可以。
        """
        import inspect

        from backend.app.api import company_aliases as m

        src = inspect.getsource(m)
        self.assertTrue(
            "regexp_split_to_table" in src or "string_to_array" in src,
            "待補清單沒有對 `|` 做展開，整串會被當成一個公司名",
        )


class RefreshUsesPrimaryApplicantTests(unittest.TestCase):
    """refresh SQL：顯示名與比對都要取主申請人，原始欄位保留完整值。"""

    @staticmethod
    def _sql() -> str:
        from backend.app.derived.refresh_report_patent_base import REFRESH_SQL

        return REFRESH_SQL

    def test_display_name_takes_primary(self):
        """三個 display_name 的原值 fallback 都要 split_part 取第一段。"""
        for alias in ("applicant_display_name", "current_assignee_display_name",
                      "recent_assignee_display_name"):
            line = next(l for l in self._sql().splitlines() if f"AS {alias}" in l)
            with self.subTest(alias=alias):
                self.assertIn("split_part", line,
                              f"{alias} 的原值段沒取主申請人，`A | B` 會整串顯示")

    def test_alias_matching_takes_primary(self):
        """別稱比對也要取主值——拿整串比永遠對不上，收斂靜默失效。"""
        sql = self._sql()
        # 三處 LATERAL 的比對條件都在 `lower(regexp_replace(BTRIM(...)))` 裡
        matching = [l for l in sql.splitlines()
                    if "lower(regexp_replace(BTRIM(" in l and 'b."' in l]
        self.assertTrue(matching, "找不到別稱比對條件")
        for line in matching:
            with self.subTest(line=line.strip()[:60]):
                self.assertIn("split_part", line, "比對用的是整串，含 | 的永遠對不上")

    def test_raw_columns_keep_full_value(self):
        """原始欄位不得被截成主申請人——那是 WIPS 原文，要保留完整。"""
        sql = self._sql()
        for col in ('b."申請人",', 'b."標準化申請人",', 'b."最近受讓人[US,KR,CN]",'):
            with self.subTest(col=col):
                self.assertIn(f"    {col}", sql,
                              f"{col} 應原樣輸出（未被 split_part 包住）")


class CurationBlocksNewGroupTests(unittest.TestCase):
    """剛建組（中文名還空著）不得被 curation 標記擋在 AI 草稿之外。"""

    def test_pending_sql_distinguishes_curation_sources(self):
        """PENDING_SQL 不得用寬鬆的 `display_name_curation%` 一律排除。

        代碼補齊區塊建組時也帶這個前綴，會把「還沒有中文名的新組」誤擋。
        """
        from backend.app.worker.ai_company_zh_name_runner import CompanyZhNameStore

        # ⚠ 去掉 SQL 註解行再驗——新寫的說明註解裡就引用了舊寫法（解釋為何改掉），
        # 直接掃整段會被自己的註解餵飽（本測試初版即如此）。
        sql = "\n".join(
            line for line in CompanyZhNameStore.PENDING_SQL.splitlines()
            if not line.strip().startswith("--")
        )
        self.assertNotIn(
            "'display_name_curation%%'", sql,
            "仍用寬鬆前綴排除——剛建組的代碼會被誤擋，AI 永遠抓不到它",
        )
        self.assertIn("zh_review_prefix", sql, "應改用具名參數帶入裁決來源前綴")

    def test_new_group_without_zh_name_is_pending(self):
        """建組但中文欄空 → 應出現在待中文化清單。"""
        rows = _run_pending([
            # (代碼, 中文名, 正規化名, 別稱, review_status, source_file)
            ("TEMP:x", None, "DRILL MASTER UNIVERSAL CORP.", "DRILL MASTER UNIVERSAL CORP.",
             "confirmed", "display_name_curation:code_registry"),
        ])
        self.assertEqual([r[0] for r in rows], ["TEMP:x"],
                         "剛建組、無中文名的代碼必須列入待中文化")

    def test_already_decided_keep_original_is_not_pending(self):
        """已裁決 keep_original（查過查無）→ 不得重複問 AI。"""
        rows = _run_pending([
            ("TEMP:y", None, "SOME CORP", "SOME CORP",
             "confirmed", "display_name_curation:zh_name_review"),
        ])
        self.assertEqual(rows, [], "已裁決過的不得再列入")

    def test_group_with_zh_name_is_not_pending(self):
        """已有中文名 → 不必再問。"""
        rows = _run_pending([
            ("TEMP:z", "喬山健康科技", "Chi Hua Fitness Co., Ltd.", "CHI HUA",
             "confirmed", "display_name_curation:code_registry"),
        ])
        self.assertEqual(rows, [])


def _run_pending(rows):
    """純 Python 重現 PENDING_SQL 的**排除條件**，不連任何 DB。

    ⚠ 絕不連 Supabase 正式庫（使用者明令）。這裡驗的是「哪些代碼該被排除」
    這段判斷邏輯，不需要 PostgreSQL——`mode()` 只影響選哪個名字顯示，
    與「該不該列入」無關。

    ⚠ 這是 SQL 的**平行實作**，有漂移風險。故另有
    `test_pending_sql_distinguishes_curation_sources` 直接掃 SQL 字面，
    兩者一起才構成防護：這支驗語意、那支驗 SQL 沒退回舊寫法。
    """
    from backend.app.worker.ai_company_zh_name_runner import (
        CODE_REGISTRY_SOURCE_SUFFIX,
    )

    by_code: dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r[0], []).append(r)

    out = []
    for code, group in by_code.items():
        # ① 已有中文名 → 不必問
        if any((g[1] or "").strip() for g in group):
            continue
        # ② 已經過「中文名裁決」→ 不重複問。
        #    ⚠ 只認裁決來源；代碼補齊區塊建組用的來源不算裁決。
        if any(
            (g[5] or "").startswith("display_name_curation")
            and not (g[5] or "").endswith(CODE_REGISTRY_SOURCE_SUFFIX)
            for g in group
        ):
            continue
        # ③ 已有草稿 → 不重複產
        if any(g[4] == "ai_suggested" for g in group):
            continue
        name = next((g[2] or g[3] for g in group if (g[2] or g[3])), None)
        if name:
            out.append((code, name))
    return sorted(out)


if __name__ == "__main__":
    unittest.main()
