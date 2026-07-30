"""有代碼未建組 → 自動建待確認組（規格 applicant-code-grouping-spec.md 批次 b）。

## 問題

WIPS 匯入時，`申請人代表碼` 有值但該代碼尚未建組，現況只丟 `unknown_code`
進待補清單，使用者得四欄全手填。

⚠ 但 WIPS **同時給了代碼與標準化名稱**，四欄中三欄可直接推導：

    代碼        UN226597
    標準化申請人  NANTONG IRONMASTER SPORTING INDUSTRIAL CO., LTD.   ← 現成英文正式名
    原始申請人    NANTONG IRONMASTER SPORTING INDUSTRIAL Co.,Ltd.    ← 別稱

只有 `公司中文名稱` 不能自動（市場慣用名是判斷不是資料）。

## 定案

自動建組，`review_status='review_required'`、中文名留空。

⚠ **不用 `ai_suggested`**：本路徑是確定性規則（代碼＋標準化名稱直接推導），
無 AI 參與。使用者 2026-07-30 明示「先不要讓 AI 建議在這部分出現」。

⚠ 既有消費端都只吃 `confirmed`（待補清單、報表顯示各 1／4 處），
故待確認組不污染正式資料，且該名稱**仍留在待補清單**直到使用者確認。
"""
from __future__ import annotations

import sys
import unittest
from unittest import mock


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    """假連線：只記錄 INSERT 參數，不碰真實 DB（使用者紅線：測試不得寫正式庫）。"""

    def __init__(self, existing):
        self.existing = existing
        self.inserts: list[tuple] = []
        self.sqls: list[str] = []

    def execute(self, sql, params=()):
        self.sqls.append(sql)
        if "SELECT DISTINCT" in sql:      # 待中文化偵測（回兩欄）
            return _Cur([])
        if "SELECT" in sql:                # 既有組（四欄）
            return _Cur(self.existing)
        if "INSERT" in sql:
            self.inserts.append(params)
        return _Cur([])

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(existing, pairs, **kw):
    from backend.app.derived import company_alias_importer as m

    conn = _Conn(existing)
    fake = mock.MagicMock()
    fake.connect.return_value = conn
    with mock.patch.dict(sys.modules, {"psycopg": fake}):
        result = m.govern_company_names(pairs, connect_kwargs={}, **kw)
    return result, conn


# 既有：UN123 已建組（代碼, 中文名, 英文正式名, 別稱）
EXISTING = [("UN123", "喬山健康科技", "JOHNSON HEALTH TECH", "JOHNSON HEALTH TECH CO LTD")]


class AutoGroupTests(unittest.TestCase):
    """有代碼未建組要自動建成待確認組。"""

    def test_unknown_code_creates_group(self):
        """🔴 未建組的真代碼要建組，不能只丟 manual_review。"""
        result, conn = _run(EXISTING, [
            ("UN999", "NANTONG XX Co.,Ltd."),
        ], standardized_names={"UN999": "NANTONG XX CO., LTD."})
        self.assertTrue(
            result.get("created_groups"),
            "未建組的真代碼未自動建組——使用者仍得四欄手填")
        self.assertTrue(conn.inserts, "沒有任何寫入")

    def test_created_group_is_review_required(self):
        """⚠ 標記用 `review_required`（既有 CHECK 值），不得用 `ai_suggested`。"""
        _result, conn = _run(EXISTING, [
            ("UN999", "NANTONG XX Co.,Ltd."),
        ], standardized_names={"UN999": "NANTONG XX CO., LTD."})
        flat = " ".join(str(p) for p in conn.inserts) + " ".join(conn.sqls)
        self.assertIn("review_required", flat, "待確認組未標 review_required")
        self.assertNotIn(
            "ai_suggested", flat,
            "不得用 ai_suggested——本路徑無 AI 參與（使用者 2026-07-30 明示）")

    def test_zh_name_left_empty(self):
        """🔴 中文名必須留空——市場慣用名是判斷不是資料，不得自動填。"""
        _result, conn = _run(EXISTING, [
            ("UN999", "NANTONG XX Co.,Ltd."),
        ], standardized_names={"UN999": "NANTONG XX CO., LTD."})
        self.assertTrue(conn.inserts)
        # 參數順序：代碼, 中文名, 英文正式名, 別稱（沿既有 INSERT 契約）
        zh_values = {p[1] for p in conn.inserts}
        self.assertTrue(
            all(v in (None, "") for v in zh_values),
            f"中文名被自動填入：{zh_values}")

    def test_normalized_name_from_wips(self):
        """英文正式名取 WIPS 標準化申請人（現成資料，不自行編）。"""
        _result, conn = _run(EXISTING, [
            ("UN999", "NANTONG XX Co.,Ltd."),
        ], standardized_names={"UN999": "NANTONG XX CO., LTD."})
        en_values = {p[2] for p in conn.inserts}
        self.assertIn("NANTONG XX CO., LTD.", en_values,
                      "未採用 WIPS 標準化申請人當英文正式名")

    def test_no_standardized_name_still_groups(self):
        """⚠ 沒有標準化名稱時仍要建組，英文正式名退回原始寫法。

        不能因為缺一欄就整組不建——那等於回到現況。
        """
        result, conn = _run(EXISTING, [("UN888", "SOME CO LTD")])
        self.assertTrue(result.get("created_groups"),
                        "缺標準化名稱就不建組——過度嚴格")
        en_values = {p[2] for p in conn.inserts}
        self.assertTrue(any(v for v in en_values), "英文正式名全空")

    def test_known_code_still_appends_variant(self):
        """⚠ 已建組的行為不得改變（回歸）。"""
        result, conn = _run(EXISTING, [("UN123", "Johnson Health Tech Co., Ltd.")])
        self.assertEqual(result["inserted"], 1, "已建組補變體行為被破壞")
        self.assertFalse(result.get("created_groups"),
                         "已建組不應被算成新建組")

    def test_no_code_unchanged(self):
        """⚠ 無代碼維持現況（批次 a 才處理），不得在本批次誤動。"""
        result, conn = _run(EXISTING, [("", "NO CODE CO")])
        self.assertEqual(result["inserted"], 0)
        self.assertFalse(result.get("created_groups"))

    def test_conflicting_code_not_auto_grouped(self):
        """⚠ 同代碼對到多組名稱＝資料有問題，維持人工複核、不自動建組。"""
        conflict = [
            ("UN777", "甲公司", "A CORP", "A CORP"),
            ("UN777", "乙公司", "B CORP", "B CORP"),
        ]
        result, _conn = _run(conflict, [("UN777", "C CORP")])
        reasons = [x["reason"] for x in result["manual_review"]]
        self.assertIn("conflicting_code", reasons)
        self.assertFalse(result.get("created_groups"),
                         "衝突代碼不得自動建組")


if __name__ == "__main__":
    unittest.main()


class FiveColumnPairsTests(unittest.TestCase):
    """配對來源擴大到五欄（規格 2-6，2026-07-30 使用者「範圍納入到各種專利權人欄位都要」）。

    ⚠ 現況口徑不一致：自動歸戶掃兩欄、待補清單掃五欄——
    專利權人／受讓人欄的名稱看得見卻不會自動歸戶。

    對應代碼欄：
    - 申請人／標準化申請人 → `申請人代表碼`
    - 最近專利權人／標準當前專利權人 → `標準當前專利權人代碼[US,JP,KR,CN,CA,AU]`
    - 最近受讓人 → ⚠ WIPS 無對應代碼欄，只能走批次 (a) 名稱比對
    """

    def test_build_people_pairs_exists(self):
        """要有共用的配對抽取函式，匯入與 sweep 不各寫一份。"""
        from backend.app.derived import company_alias_importer as m

        self.assertTrue(hasattr(m, "build_people_pairs"),
                        "缺共用配對函式——匯入與 sweep 會各維護一份掃描邏輯")

    def test_covers_owner_columns(self):
        """專利權人欄要帶對應的代碼欄一起配對。"""
        from backend.app.derived.company_alias_importer import build_people_pairs

        pairs = build_people_pairs({
            "申請人": "A CO",
            "申請人代表碼": "UN1",
            "最近專利權人[US,JP,KR,CN,CA,AU]": "B CO",
            "標準當前專利權人代碼[US,JP,KR,CN,CA,AU]": "UN2",
        })
        self.assertIn(("UN1", "A CO"), pairs)
        self.assertIn(("UN2", "B CO"), pairs, "專利權人欄未配對其代碼欄")

    def test_splits_pipe_multi_values(self):
        """⚠ `A | B` 要拆開，否則整串被當成一家公司。"""
        from backend.app.derived.company_alias_importer import build_people_pairs

        pairs = build_people_pairs({
            "申請人": "XIAMEN DMASTER CO.,Ltd. | Zeng Qing",
            "申請人代表碼": "UN1",
        })
        names = {n for _c, n in pairs}
        self.assertIn("XIAMEN DMASTER CO.,Ltd.", names)
        self.assertIn("Zeng Qing", names, "`|` 多值未拆開")

    def test_assignee_has_no_code(self):
        """⚠ 最近受讓人無對應代碼欄——配對的 code 應為 None，不得誤用申請人代碼。"""
        from backend.app.derived.company_alias_importer import build_people_pairs

        pairs = build_people_pairs({
            "申請人代表碼": "UN1",
            "最近受讓人[US,KR,CN]": "C CO",
        })
        assignee = [(c, n) for c, n in pairs if n == "C CO"]
        self.assertTrue(assignee, "受讓人未被收集")
        self.assertIsNone(assignee[0][0],
                          "受讓人被誤掛申請人代碼——那是不同欄位的代碼")

    def test_skips_empty(self):
        """空值不產生配對。"""
        from backend.app.derived.company_alias_importer import build_people_pairs

        self.assertEqual(build_people_pairs({"申請人": "", "申請人代表碼": None}), [])


class ChainWiringTests(unittest.TestCase):
    """🔴 `standardized_names` 必須逐層轉傳，不得在中間層被丟棄。

    ⚠ 本專案今日已兩次踩同型坑（前端送 `aliases`／後端欄位 `variants`；
    `report_keys` 被 Pydantic 靜默忽略）——兩次都是**頭尾對、中間斷**，
    測試只驗兩端就照樣全綠。故本測試逐段驗：
    importer → register_known_code_variants → govern_company_names。
    """

    def test_thin_wrapper_forwards(self):
        import inspect

        from backend.app.derived.company_alias_importer import register_known_code_variants

        sig = inspect.signature(register_known_code_variants)
        self.assertIn("standardized_names", sig.parameters,
                      "薄包裝未宣告參數，匯入端傳了會 TypeError 或被吃掉")
        src = inspect.getsource(register_known_code_variants)
        self.assertIn("standardized_names=standardized_names", src,
                      "薄包裝收了參數卻沒轉傳——典型中間層漏接")

    def test_importer_passes_standardized_names(self):
        import inspect

        from backend.app.importers import wips_importer

        src = inspect.getsource(wips_importer.import_wips_file)
        self.assertIn("standardized_names=", src, "匯入端未傳 standardized_names")
        self.assertIn("build_people_pairs", src,
                      "匯入端未改用五欄共用配對函式")

    def test_end_to_end_reaches_insert(self):
        """整條線實跑：匯入端的資料形狀 → 建組時英文正式名正確落地。"""
        from backend.app.derived import company_alias_importer as m

        people = {
            "申請人": "NANTONG XX Co.,Ltd.",
            "標準化申請人": "NANTONG XX CO., LTD.",
            "申請人代表碼": "UN999",
        }
        pairs = m.build_people_pairs(people)
        std = {"UN999": "NANTONG XX CO., LTD."}
        conn = _Conn(EXISTING)
        fake = mock.MagicMock()
        fake.connect.return_value = conn
        with mock.patch.dict(sys.modules, {"psycopg": fake}):
            result = m.register_known_code_variants(
                pairs, connect_kwargs={}, standardized_names=std)
        self.assertTrue(result.get("created_groups"), "整條線未建出組")
        en_values = {p[2] for p in conn.inserts}
        self.assertIn("NANTONG XX CO., LTD.", en_values,
                      "英文正式名沒走到 INSERT——中間某層漏接")


class ExistingCodesExposureTests(unittest.TestCase):
    """待確認組要在「資料庫已有的代碼」清單看得到（規格 b3/b4）。

    🔴 2026-07-30 覆查更正：`list_existing_company_codes` **也**只吃 `confirmed`，
    初版規格誤判它無過濾。若不放寬，待確認組建了卻沒有任何地方看得到——
    **比不建更糟**（資料在庫裡但使用者不知情）。
    """

    def test_query_includes_review_required(self):
        import inspect

        from backend.app.api import company_aliases as api

        src = inspect.getsource(api.list_existing_company_codes)
        self.assertNotIn(
            "review_status = 'confirmed'", src,
            "仍只查 confirmed——待確認組不會出現在前端")
        self.assertIn("review_required", src, "未納入待確認組")

    def test_query_selects_review_status(self):
        """要把 review_status 帶給前端，否則畫面分不出哪些待確認。"""
        import inspect

        from backend.app.api import company_aliases as api

        self.assertIn("review_status",
                      inspect.getsource(api.list_existing_company_codes))

    def test_group_helper_keeps_status(self):
        """分組時要保留狀態（一組內全部同狀態）。"""
        from backend.app.api.company_aliases import group_aliases_by_code

        groups = group_aliases_by_code([
            {"id": 1, "申請人代碼": "UN9", "公司中文名稱": None,
             "正規化名稱": "X CO", "別稱": "X Co.", "review_status": "review_required"},
        ])
        self.assertTrue(groups)
        self.assertEqual(groups[0].get("review_status"), "review_required",
                         "分組後遺失 review_status，前端無法標記")


class FrontendPendingBadgeTests(unittest.TestCase):
    """前端顯示（規格 b4）：待確認標記＋確認鈕，且不得動到既有互動。"""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        cls.html = (Path(__file__).resolve().parents[1]
                    / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")

    def test_pending_badge_rendered(self):
        """無中文名時顯示英文正式名＋「待補中文名」標記（使用者定，不留空白）。"""
        self.assertIn("待補中文名", self.html, "缺待確認標記")

    def test_confirm_button_exists(self):
        self.assertIn("confirmPendingCodeGroup", self.html, "缺一鍵確認入口")

    def test_no_ai_wording(self):
        """🔴 待確認組的措辭不得出現 AI／建議／草稿（使用者 2026-07-30 明示）。

        ⚠ 只掃 `existingGroupHtml` 這個函式，不掃全檔——全檔掃會誤中：
        - L967 的註解本身就在寫「不讓 AI 建議代碼」（正是我們要遵守的規則）
        - 分群區的「AI 篩不相干」是另一個功能的既有文字
        測試範圍錯會逼人去改無關的正確程式碼。
        """
        import re

        match = re.search(r"function existingGroupHtml\(g\) \{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match, "找不到 existingGroupHtml")
        # ⚠ 只掃**輸出給使用者的字串**，排除註解——說明「不得出現 AI」的註解
        # 本身含這些字，掃註解會逼人刪掉正確的規則說明。
        code_lines = [
            line for line in match.group(0).split("\n")
            if not line.strip().startswith("//")
        ]
        block = "\n".join(code_lines)
        for bad in ("AI", "建議", "草稿"):
            self.assertNotIn(bad, block, f"待確認組的顯示文字出現禁用措辭：{bad}")

    def test_magnifier_preserved(self):
        """⚠ 待補清單的 🔍 篩選入口不得被移除（2026-07-29 使用者需求）。"""
        self.assertIn("showPendingNamePatents", self.html,
                      "🔍 篩選入口被移除——那是使用者明確要的功能")
        self.assertIn("chip-find", self.html, "🔍 圖示樣式被移除")


class ImportSummaryNoticeTests(unittest.TestCase):
    """匯入結果要顯示歸戶統計（規格 b5）。

    ⚠ `summary["alias_variants"]` 早就存在，但前端**零顯示**——
    自動歸戶了幾筆、建了幾組待確認組，使用者完全看不到。
    這正是「東西做了但沒人知道」的靜默成功。
    """

    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        cls.html = (Path(__file__).resolve().parents[1]
                    / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")

    def test_import_result_shows_alias_stats(self):
        import re

        match = re.search(r"function importResultHtml\(j\) \{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match, "找不到 importResultHtml")
        body = match.group(0)
        self.assertIn("alias_variants", body,
                      "匯入結果未顯示歸戶統計——使用者不知道系統做了什麼")

    def test_shows_created_groups_count(self):
        """待確認組數要單獨顯示，使用者才知道有東西要確認。

        ⚠ 渲染拆在 `aliasVariantsHtml`（importResultHtml 只負責呼叫），
        故斷言要看那支函式——初版找錯函式導致假失敗。
        """
        import re

        match = re.search(r"function aliasVariantsHtml\(av\) \{.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match, "找不到 aliasVariantsHtml")
        body = match.group(0)
        self.assertIn("created_groups", body, "未顯示待確認組數")
        self.assertIn("待確認代碼組", body, "缺使用者看得懂的標籤")

    def test_notice_tells_where_to_confirm(self):
        """⚠ 有待確認組時要指出去哪確認，否則使用者不知道有事情待辦。"""
        import re

        body = re.search(r"function aliasVariantsHtml\(av\) \{.*?\n\}", self.html, re.S).group(0)
        self.assertIn("資料庫已有的代碼", body, "未指出確認位置")

    def test_notice_has_no_ai_wording(self):
        """措辭不得出現 AI／建議／草稿（只看輸出字串，不看註解）。"""
        import re

        body = re.search(r"function aliasVariantsHtml\(av\) \{.*?\n\}", self.html, re.S).group(0)
        code = "\n".join(l for l in body.split("\n") if not l.strip().startswith("//"))
        for bad in ("AI", "建議", "草稿"):
            self.assertNotIn(bad, code, f"通知文字出現禁用措辭：{bad}")


class FirstNameOnlyCarriesCodeTests(unittest.TestCase):
    """🔴 `A公司 | Zeng Qing` 拆開後，**只有第一個名稱帶代碼**（2026-07-30 使用者定案）。

    ## 為什麼

    WIPS 的代碼欄是**整列一個**（`申請人代表碼`），拆名稱時無法知道哪個名稱
    對應那個代碼。若拆出的每一筆都掛同一個代碼，第二個名稱（常是自然人，
    例如 `Zeng Qing`）會被**自動併進公司組**——那跨過了使用者定的紅線
    「系統不預先分組」，即使結果可能正確也該由使用者按一下。

    ## 依據

    WIPS 慣例第一個是主申請人（`refresh_report_patent_base` 的
    `split_part(..., '|', 1)` 就是這個假設）。故第一個帶代碼、其餘 None，
    後者走無代碼路徑進待補清單。
    """

    def test_second_name_has_no_code(self):
        from backend.app.derived.company_alias_importer import build_people_pairs

        pairs = build_people_pairs({
            "申請人": "XIAMEN DMASTER HEALTH TECH Co.,Ltd. | Zeng Qing",
            "申請人代表碼": "UN226597",
        })
        by_name = {n: c for c, n in pairs}
        self.assertEqual(by_name["XIAMEN DMASTER HEALTH TECH Co.,Ltd."], "UN226597",
                         "第一個名稱應帶代碼（WIPS 慣例＝主申請人）")
        self.assertIsNone(by_name["Zeng Qing"],
                          "第二個名稱不得帶代碼——會被自動併進公司組")

    def test_single_name_still_carries_code(self):
        """⚠ 只有一個名稱時照常帶代碼（不得誤傷單值的正常情況）。"""
        from backend.app.derived.company_alias_importer import build_people_pairs

        pairs = build_people_pairs({
            "申請人": "NANTONG XX Co.,Ltd.",
            "申請人代表碼": "UN226597",
        })
        self.assertEqual(pairs, [("UN226597", "NANTONG XX Co.,Ltd.")])

    def test_rule_applies_per_column(self):
        """⚠ 每一欄各自算「第一個」——專利權人欄的第一個也要帶它自己的代碼。"""
        from backend.app.derived.company_alias_importer import build_people_pairs

        pairs = build_people_pairs({
            "申請人": "A CO | A2 PERSON",
            "申請人代表碼": "UN1",
            "最近專利權人[US,JP,KR,CN,CA,AU]": "B CO | B2 PERSON",
            "標準當前專利權人代碼[US,JP,KR,CN,CA,AU]": "UN2",
        })
        by_name = {n: c for c, n in pairs}
        self.assertEqual(by_name["A CO"], "UN1")
        self.assertIsNone(by_name["A2 PERSON"])
        self.assertEqual(by_name["B CO"], "UN2", "專利權人欄的第一個要帶其代碼欄")
        self.assertIsNone(by_name["B2 PERSON"])


class PendingListCoverageTests(unittest.TestCase):
    """拆出來的名稱要留在待補清單，直到使用者確認（規格 2-3）。

    ⚠ 兩件事容易被誤「優化」掉：
    1. 待補清單的 SQL 必須**掃五欄且拆 `|`**——否則拆出的第二個名稱
       （`A公司 | Zeng Qing` 的 `Zeng Qing`）看不見。
    2. 排除條件必須是 `review_status = 'confirmed'`，**不能放寬成「在表裡就排除」**
       ——那會讓本批自動建的待確認組一建立就從清單消失，使用者不知道要確認。

    實測（2026-07-30，庫內 60 筆專利）：待補清單 11 項，
    含 `Zeng Qing`（12 筆）與 `SKI-ROW INC DBA ENERGYFIT`（1 筆），
    來源欄位涵蓋申請人／最近專利權人／最近受讓人。
    """

    @classmethod
    def setUpClass(cls):
        from backend.app.api.company_aliases import _PENDING_CODES_SQL

        cls.sql = _PENDING_CODES_SQL

    def test_scans_five_columns(self):
        for col in ("申請人", "標準化申請人", "最近專利權人",
                    "標準當前專利權人", "最近受讓人"):
            with self.subTest(column=col):
                self.assertIn(col, self.sql, f"待補清單未掃 {col}")

    def test_splits_pipe(self):
        """⚠ 不拆 `|` 的話，拆出的第二個名稱永遠不會出現在清單。"""
        self.assertIn("regexp_split_to_table", self.sql,
                      "待補清單未拆 `|` 多值")

    def test_only_confirmed_excluded(self):
        """🔴 只排除 confirmed——待確認組要留在清單直到使用者確認。"""
        self.assertIn("review_status = 'confirmed'", self.sql,
                      "排除條件被放寬，待確認組會一建立就從清單消失")
        self.assertNotIn("review_required", self.sql,
                         "待補清單不該排除 review_required——那正是要使用者確認的")
