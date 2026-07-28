"""專利權人代碼補齊區塊（2026-07-28 使用者需求）。

## 背景

使用者截圖：公司×國家交叉表裡 CHI HUA 三種寫法各佔一列、中文名全空。
查清根因＝`company_aliases` **0 筆**，`applicant_display_name` 的四層 COALESCE 全落空、
退回原始「申請人」欄，等於沒正規化。

再查代碼覆蓋率：**60 筆只有 3 筆有 WIPS 代碼**（單一代碼 UN226597）。
所以「自動化能解決的」實際幾乎不存在，57 筆／20+ 種名稱全都要人工處理。

## 使用者定案（逐條確認過）

1. **代碼只能是使用者去 WIPS 查來的**——不得自動產生、不得 AI 建議。
   編一個 MANUAL-001 是假代碼，會污染對照表。
2. **系統不預先分組**——分組＝替使用者決定「這幾家共用同一個 WIPS 代碼」，
   但那要查過 WIPS 才知道。待補清單只作參考與省打字，不暗示任何分組關係。
3. 一組 = 代碼 + 正規化名稱 + **N 個變體**；整張表可有多組。
4. 已 confirmed 的不再出現在待補清單。
5. 代碼**不驗格式**（WIPS 編碼規則未知，擋錯會讓合法代碼輸不進去）；
   但**同一代碼配到不同正規化名稱要警告**——那才是真衝突。
6. AI 只做英文名 → 中文名。
7. 另有收合區塊可展開看 DB 既有代碼，再展開看該代碼下的公司變體。

## 資料模型：不需 migration

既有 `company_aliases` 正好承載：一列一個「別稱」，同組共用
`(申請人代碼, 公司名稱)`。UNIQUE(申請人代碼, 公司名稱, 別稱) 天然防重複。
`A | B` 這類多權利人拆成各自的變體列，是否同組由使用者填相同代碼決定。
"""
from __future__ import annotations

import unittest
from unittest import mock


class PendingListTests(unittest.TestCase):
    """待補清單：去重後的原始名稱，排除已處理過的。"""

    def test_endpoint_exists(self):
        from backend.app.api import company_aliases as api

        self.assertTrue(hasattr(api, "list_pending_company_codes"))

    def test_excludes_confirmed(self):
        """已 confirmed 的名稱不得再出現（使用者定：已處理過不再出現）。"""
        import inspect
        from backend.app.api import company_aliases as api

        src = inspect.getsource(api.list_pending_company_codes)
        self.assertIn("confirmed", src)

    def test_no_grouping_inference(self):
        """不得預先分組——系統不替使用者判斷誰跟誰共用代碼。"""
        import inspect
        from backend.app.api import company_aliases as api

        src = inspect.getsource(api.list_pending_company_codes)
        for banned in ("similar", "suggest_group", "group_by_similarity", "fuzzy"):
            self.assertNotIn(
                banned, src.lower(),
                f"出現 {banned}——系統在推斷分組，違反「代碼只能使用者查 WIPS 給」")


class ExistingCodesTests(unittest.TestCase):
    """既有代碼區：代碼 → 該代碼下的變體（兩層展開）。"""

    def test_endpoint_exists(self):
        from backend.app.api import company_aliases as api

        self.assertTrue(hasattr(api, "list_existing_company_codes"))

    def test_groups_variants_under_code(self):
        """回傳結構要能支撐兩層展開：一層代碼、二層變體。"""
        from backend.app.api import company_aliases as api

        rows = [
            {"申請人代碼": "UN226597", "公司名稱": "南通鐵匠", "別稱": "NANTONG A"},
            {"申請人代碼": "UN226597", "公司名稱": "南通鐵匠", "別稱": "NANTONG B"},
            {"申請人代碼": "X1", "公司名稱": "甲", "別稱": "A CORP"},
        ]
        grouped = api.group_aliases_by_code(rows)
        self.assertEqual(len(grouped), 2)
        first = next(g for g in grouped if g["code"] == "UN226597")
        self.assertEqual(first["company_name"], "南通鐵匠")
        self.assertEqual(len(first["variants"]), 2)


class ConflictGuardTests(unittest.TestCase):
    """同一代碼配到不同正規化名稱＝真衝突，要警告。"""

    def test_detects_conflict_within_submission(self):
        from backend.app.api import company_aliases as api

        groups = [
            {"code": "C1", "company_name": "甲公司", "variants": ["A"]},
            {"code": "C1", "company_name": "乙公司", "variants": ["B"]},
        ]
        conflicts = api.find_code_conflicts(groups, existing={})
        self.assertTrue(conflicts, "同批送出的兩組共用代碼卻不同公司名，未偵測")

    def test_detects_conflict_with_existing_db(self):
        """與 DB 既有代碼衝突同樣要擋。"""
        from backend.app.api import company_aliases as api

        groups = [{"code": "UN226597", "company_name": "新名字", "variants": ["A"]}]
        conflicts = api.find_code_conflicts(groups, existing={"UN226597": "南通鐵匠"})
        self.assertTrue(conflicts)

    def test_same_code_same_name_is_fine(self):
        """同代碼同名＝補變體，正常情境不得誤報。"""
        from backend.app.api import company_aliases as api

        groups = [{"code": "UN226597", "company_name": "南通鐵匠", "variants": ["C"]}]
        self.assertFalse(
            api.find_code_conflicts(groups, existing={"UN226597": "南通鐵匠"}))


class WriteIntegrationTests(unittest.TestCase):
    """⚠ 整合既有機制，不另造第三套（使用者：「和現有代碼機制以及中文重新整合」）。

    既有 `apply_confirmed_display_names(mapping)` 的簽名恰好就是本需求的資料形狀：
        {申請人代碼: {"canonical": 正規化名, "aliases": [變體, ...]}}
    它已處理去重（(代碼, normalize_lookup(別稱)) 同一把 key）、既有列 re-canonicalize、
    review_status 轉 confirmed、source_type='manual'。**本端點只做形狀轉換與委派**，
    不自寫 INSERT——複寫必然與那份規則漂移（該檔 docstring 亦明載此戒律）。
    """

    def test_confirm_endpoint_exists(self):
        from backend.app.api import company_aliases as api

        self.assertTrue(hasattr(api, "confirm_company_codes"))

    def test_delegates_to_existing_writer(self):
        """不得自寫 SQL——一律委派 apply_confirmed_display_names。"""
        import inspect
        from backend.app.api import company_aliases as api

        src = inspect.getsource(api.confirm_company_codes)
        self.assertIn("apply_confirmed_display_names", src)
        for banned in ("INSERT INTO", "insert into"):
            self.assertNotIn(banned, src, "自寫 INSERT——會與既有去重規則漂移")

    def test_groups_to_mapping_shape(self):
        """轉成既有寫入端的 mapping 形狀。"""
        from backend.app.api import company_aliases as api

        mapping = api.groups_to_alias_mapping([
            {"code": "C1", "company_name": "甲", "variants": ["A CORP", "A CO LTD"]},
        ])
        self.assertEqual(set(mapping), {"C1"})
        self.assertEqual(mapping["C1"]["canonical"], "甲")
        self.assertEqual(len(mapping["C1"]["aliases"]), 2)

    def test_blank_variants_skipped(self):
        """空白輸入格不得成為別稱（UI 的 ＋ 會留下未填的空格）。"""
        from backend.app.api import company_aliases as api

        mapping = api.groups_to_alias_mapping([
            {"code": "C1", "company_name": "甲", "variants": ["A", "", "   "]},
        ])
        self.assertEqual(mapping["C1"]["aliases"], ["A"])

    def test_writer_called_with_valid_signature(self):
        """真的呼叫一次，確認參數名對得上。

        ⚠ 只斷言「原始碼含 apply_confirmed_display_names」抓不到參數名錯誤——
        本測試初版即如此，實機才炸 TypeError: unexpected keyword argument 'source_file'
        （正確是位置參數 source_label）。這正是本專案反覆出現的「靜默失敗」同型：
        看起來接上了，實際呼叫才知道沒接對。
        """
        from backend.app.api import company_aliases as api

        captured = {}

        def _fake_writer(mapping, source_label, connect_kwargs=None):
            captured["mapping"] = mapping
            captured["label"] = source_label
            return {"inserted": 1, "updated": 0}

        body = api.ConfirmCodesRequest(groups=[
            {"code": "C1", "company_name": "甲", "variants": ["A CORP"]},
        ])
        with mock.patch(
            "backend.app.derived.company_alias_importer.apply_confirmed_display_names",
            _fake_writer,
        ), mock.patch.object(api, "create_job", return_value=1),              mock.patch("psycopg.connect") as conn:
            conn.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
            result = api.confirm_company_codes(body)

        # ⚠ 2026-07-28 四欄拆分：mapping 的名稱鍵由單一 `canonical` 改為
        # `zh_name`／`normalized_name` 兩鍵。舊單欄輸入（company_name）走
        # groups_to_alias_mapping 的相容路徑，仍要能送到 writer。
        self.assertEqual(captured["mapping"]["C1"]["canonical"], "甲")
        self.assertTrue(captured["label"].startswith("display_name_curation"))
        self.assertEqual(result["groups"], 1)

    def test_refresh_enqueued_after_write(self):
        """寫完要刷 derived，否則使用者看到「表格沒變」（既有線同一教訓）。"""
        import inspect
        from backend.app.api import company_aliases as api

        src = inspect.getsource(api.confirm_company_codes)
        self.assertIn("refresh_derived", src)


class SharedWithZhNameLineTests(unittest.TestCase):
    """與既有中文名確認線共用同一套判定，不得各自一份。"""

    def test_same_confirmed_marker(self):
        """「已處理過」的判定沿用既有 CONFIRM_SOURCE_LABEL 前綴，不另立標記。"""
        from backend.app.api import company_aliases as api

        self.assertTrue(api.CONFIRM_SOURCE_LABEL.startswith("display_name_curation"))

    def test_pending_list_respects_that_marker(self):
        import inspect
        from backend.app.api import company_aliases as api

        src = inspect.getsource(api.list_pending_company_codes)
        self.assertIn("display_name_curation", src,
                      "待補清單沒沿用既有已裁決標記——會與中文名確認線各判一套")


class FrontendTests(unittest.TestCase):
    """瀏覽專利下的新區塊。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from backend.app.main import app

        cls.html = TestClient(app).get("/").text

    def test_section_present(self):
        self.assertIn("company-code-registry", self.html)

    def test_add_variant_control(self):
        """變體可加格（＋）。"""
        self.assertRegex(self.html, r"function\s+addCodeVariant\s*\(")

    def test_add_group_control(self):
        """可新增多組。"""
        self.assertRegex(self.html, r"function\s+addCodeGroup\s*\(")

    def test_existing_codes_collapsed(self):
        """既有代碼區平時收合（details）。"""
        self.assertIn("existing-codes-details", self.html)

    def test_no_auto_generate_code(self):
        """不得有自動產生代碼的功能——代碼只能查 WIPS 取得。"""
        for banned in ("autoGenerateCode", "MANUAL-00", "generateCode("):
            self.assertNotIn(banned, self.html, f"出現 {banned}——代碼不得自動產生")


if __name__ == "__main__":
    unittest.main()
