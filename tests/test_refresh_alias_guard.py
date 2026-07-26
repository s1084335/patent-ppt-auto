"""refresh_report_patent_base 的 company_aliases 護欄契約（純字串檢查，不需 DB）。

動因（2026-07-26 盤點）：0033 migration 檔頭宣稱「AI 草稿天然不進正式顯示欄」，
但該保證只對 `code_alias_names` CTE 成立（它有 `WHERE ca.review_status = 'confirmed'`）。
三個別稱 LATERAL（applicant_alias／owner_alias／assignee_alias）**沒有**任何
review_status 過濾，未確認的 ai_suggested 草稿列可經別稱字面命中而滲入 COALESCE。

當時之所以看不出錯，是因為 verdict='keep_original' 的草稿其「公司名稱」＝英文原文，
代入後顯示字面不變——靠巧合無害，不是靠護欄。本測試把「宣稱」變成「可驗證的契約」。

為何獨立成檔而非併進 test_ai_company_zh_name_db.py：後者需要拋棄式 DB，
DB 不可達時整組 skip；護欄是 SQL 靜態性質，不該被 DB 可用性擋掉。
"""
from __future__ import annotations

import re
import unittest

from backend.app.derived.refresh_report_patent_base import REFRESH_SQL


class AliasGuardContractTests(unittest.TestCase):
    """所有讀 company_aliases 的子查詢都必須排除未確認列。"""

    # 每個從 company_aliases 取值的區塊，其 alias 名稱與用途。
    ALIAS_BLOCKS = (
        ("applicant_alias", "申請人別稱"),
        ("owner_alias", "專利權人別稱"),
        ("assignee_alias", "受讓人別稱"),
    )

    def _block_sql(self, alias_name: str) -> str:
        """取出該 alias 的 LATERAL 子查詢字串。

        ⚠ 不可用非貪婪 `\\((.*?)\\)` 從 `LEFT JOIN LATERAL` 起頭抓——子查詢內含
        `regexp_replace(...)` 等括號，非貪婪會在第一個 `)` 就收尾，抓到的片段
        涵蓋了前一個區塊的內容，導致護欄不存在時測試仍誤判通過。
        改成以「該 alias 的收尾標記」往回取到前一個 `LEFT JOIN LATERAL`，
        邊界由 alias 名稱本身決定，不受內層括號影響。
        """
        end_marker = f") {alias_name} ON true"
        end = REFRESH_SQL.find(end_marker)
        self.assertNotEqual(end, -1, f"找不到 {alias_name} 的 LATERAL 收尾")
        start = REFRESH_SQL.rfind("LEFT JOIN LATERAL", 0, end)
        self.assertNotEqual(start, -1, f"找不到 {alias_name} 的 LATERAL 起頭")
        return REFRESH_SQL[start:end]

    def test_every_alias_lateral_filters_confirmed(self):
        """三個別稱 LATERAL 都要有 review_status = 'confirmed'，草稿不得命中。"""
        for alias_name, purpose in self.ALIAS_BLOCKS:
            with self.subTest(alias=alias_name, purpose=purpose):
                block = self._block_sql(alias_name)
                self.assertIn(
                    "review_status", block,
                    f"{purpose}（{alias_name}）缺 review_status 過濾，草稿會滲入正式顯示名",
                )
                self.assertIn(
                    "'confirmed'", block,
                    f"{purpose}（{alias_name}）未限定 confirmed",
                )

    def test_code_alias_names_still_filters_confirmed(self):
        """既有的代碼路徑護欄不得因本次修改而失效（回歸保護）。"""
        self.assertIn("ca.review_status = 'confirmed'", REFRESH_SQL)

    def test_no_unguarded_company_aliases_read(self):
        """company_aliases 每次被讀，同一區塊內都要出現 confirmed 限制。

        以「出現次數」對齊：讀取次數（FROM derived_layer.company_aliases）
        必須 <= confirmed 護欄次數，確保不會再新增無護欄的讀取點。
        """
        reads = len(re.findall(r"FROM derived_layer\.company_aliases", REFRESH_SQL))
        guards = len(re.findall(r"review_status\s*=\s*'confirmed'", REFRESH_SQL))
        self.assertGreaterEqual(
            guards, reads,
            f"company_aliases 被讀 {reads} 次，但只有 {guards} 處 confirmed 護欄",
        )


if __name__ == "__main__":
    unittest.main()
