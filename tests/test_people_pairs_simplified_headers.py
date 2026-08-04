"""簡體欄名的 WIPS 檔，公司名治理必須照常運作（2026-08-03 實機驗收發現）。

## 怎麼發現的

使用者要求用 `data/raw/小檔案測試.xlsx` 驗收「有代碼未建組 → 自動建組」（規格批次 b）。
實機匯入 10 筆（6 個相異代碼、與現有 22 組零交集）後，
`company_aliases` **完全沒變**（72 列 / 22 組，新增 0）。

## 根因

`build_people_pairs` 回傳 **0 對** → `govern_company_names` 收到空清單 → 整條治理管線空轉。

`PEOPLE_NAME_CODE_COLUMNS` 寫死**繁體**欄名，而該檔的 `people` 是**簡體**：

| 定義（繁體） | 實際（簡體） |
|---|---|
| `申請人` | `申请人` |
| `標準化申請人` | `标准化申请人` |
| `申請人代表碼` | `申请人代表码` |
| `最近專利權人[...]` | `最近专利权人[...]` |

一個都對不上。

⚠ 諷刺的是 `standardized_by_code` **抓得到**（`UN191973 → HUSQVARNA AB`），
因為它走 `wips.py` 的 `PEOPLE_FIELD_COLUMNS` 簡→繁對照表。
但 `people` dict 保留 WIPS 原始欄名，`PEOPLE_NAME_CODE_COLUMNS` 是**另一處寫死的清單**
——「同一資訊兩處落點」，本專案第 7 次。

## 影響

任何簡體欄名的 WIPS 檔，自動建組、變體註冊、待中文化偵測**全部不運作**，
而且**不報錯**：匯入 summary 的 `alias_variants` 顯示 0，
看起來像「沒有新變體」而不是「一個都沒掃到」。

滑雪機那批是繁體檔，所以一直沒暴露；而 `data/raw` 顯示**簡體才是常態**。
"""
from __future__ import annotations

import unittest

from backend.app.derived.company_alias_importer import build_people_pairs
from backend.app.mappings.wips import PEOPLE_FIELD_COLUMNS

#: 實機檔（`小檔案測試.xlsx`）第一筆的實際欄名與值。
LIVE_SIMPLIFIED = {
    "申请人": "HUSQVARNA AB",
    "标准化申请人": "HUSQVARNA AB",
    "申请人代表码": "UN191973",
    "最近专利权人[US,JP,KR,CN,CA,AU]": "HUSQVARNA AB",
    "标准当前专利权人[US,JP,KR,CN,CA,AU]": "HUSQVARNA AB",
    "标准当前专利权人代码[US,JP,KR,CN,CA,AU]": "UN191973",
}

TRADITIONAL = {
    "申請人": "祺驊股份有限公司",
    "標準化申請人": "CHI HUA FITNESS CO., LTD.",
    "申請人代表碼": "UN226597",
}


class SimplifiedHeaderTests(unittest.TestCase):
    def test_simplified_headers_yield_pairs(self):
        """🔴 簡體欄名要抽得出 (代碼, 名稱)——這是實機零建組的直接原因。"""
        pairs = build_people_pairs(LIVE_SIMPLIFIED)
        self.assertTrue(pairs, "簡體欄名抽不出任何配對——治理管線會整條空轉")
        codes = {code for code, _name in pairs if code}
        self.assertIn("UN191973", codes, "沒抓到申請人代表碼")

    def test_traditional_still_works(self):
        """⚠ 繁體不得因此壞掉——滑雪機那批就是繁體檔。"""
        pairs = build_people_pairs(TRADITIONAL)
        self.assertTrue(pairs)
        self.assertIn("UN226597", {code for code, _n in pairs if code})

    def test_columns_derive_from_single_source(self):
        """欄名對照要取自 `wips.py` 的 `PEOPLE_FIELD_COLUMNS`，不另寫一份。

        ⚠ 這正是本 bug 的成因：同一組欄名在兩處各寫一次，其中一處只寫了繁體。
        """
        import inspect

        from backend.app.derived import company_alias_importer as mod

        src = inspect.getsource(mod)
        self.assertIn("PEOPLE_FIELD_COLUMNS", src,
                      "沒有沿用 wips.py 的欄名對照表——簡繁會再次分岔")

    def test_every_configured_column_has_simplified_form(self):
        """設定裡的每個繁體欄名，都要能從對照表反查到簡體來源。"""
        from backend.app.derived.company_alias_importer import PEOPLE_NAME_CODE_COLUMNS

        trad_to_simp = {v: k for k, v in PEOPLE_FIELD_COLUMNS.items()}
        for name_col, code_col in PEOPLE_NAME_CODE_COLUMNS:
            for col in (name_col, code_col):
                if col is None:
                    continue
                with self.subTest(column=col):
                    self.assertIn(col, trad_to_simp,
                                  f"{col} 不在 PEOPLE_FIELD_COLUMNS，簡體檔會漏掉這一欄")


class SourceTypeWhitelistTests(unittest.TestCase):
    """自動建組寫入的 `source_type` 必須在 DB 的 CHECK 白名單內。

    🔴 2026-08-03 實機驗收：原本寫 `'import'`，**不在白名單**，
    一送出就 `CheckViolation`——而規格 2-2 節把它記成「既有值」。

    ⚠ 8 支單元測試全綠卻沒抓到，因為它們沒有真的送進 DB。
    這條白名單是 DB 端的約束，**只有實際寫入才會撞到**。
    本測試把白名單釘在程式碼裡，讓下次改值時當場紅，不必等實機。
    """

    #: 與 `derived_layer.company_aliases` 的 CHECK 一致（2026-08-03 實查）。
    DB_WHITELIST = {"excel_seed", "wips_lookup", "manual", "ai_suggested"}

    def test_auto_group_source_type_in_whitelist(self):
        from backend.app.derived.company_alias_importer import AUTO_GROUP_SOURCE_TYPE

        self.assertIn(AUTO_GROUP_SOURCE_TYPE, self.DB_WHITELIST,
                      "自動建組的 source_type 不在 DB CHECK 白名單內，寫入必然失敗")

    def test_not_ai_suggested(self):
        """⚠ 本路徑是確定性規則、無 AI 參與（使用者 2026-07-30 明示）。"""
        from backend.app.derived.company_alias_importer import AUTO_GROUP_SOURCE_TYPE

        self.assertNotEqual(AUTO_GROUP_SOURCE_TYPE, "ai_suggested")

    def test_no_hardcoded_import_literal(self):
        """`'import'` 這個字面不得再出現在 INSERT 裡。"""
        import inspect

        from backend.app.derived import company_alias_importer as mod

        src = inspect.getsource(mod.govern_company_names)
        self.assertNotIn("'import', 'review_required'", src,
                         "仍在寫死不合法的 source_type='import'")


if __name__ == "__main__":
    unittest.main()
