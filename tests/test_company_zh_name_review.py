"""公司中文名草稿確認流程（列草稿 → 逐筆裁決 → 寫回）的契約測試。

動因（2026-07-26 盤點 → 2026-07-27 補實作）：`ai:company_zh_name` 產得出草稿，
但整條線斷在「使用者確認」——`apply_confirmed_display_names()` 零生產呼叫端，
草稿寫進 DB 後沒有任何 API／UI 能列出或確認它，`wips_metadata_json->'zh_name_verdict'`
寫了沒有讀取端。結果：報表 COALESCE 第 1／2 順位（confirmed 對照名）永遠命不中，
公司名收斂實際只做到第 3 層（庫內統計名），中文名永遠出不來。

規格＝`.agents/context/company-zh-name-confirm-spec.md`，但使用者於 2026-07-27 修正四點：

1. **手動觸發**（推翻規格書「匯入後自動觸發」）：理由是自動觸發容易失敗且無補救入口，
   沿 `ai:patent_note` 2026-07-27 同一個改手動的教訓。故 `POST .../generate` 保留。
2. **確認才刪草稿、略過保留**：未裁決的草稿不能消失，否則使用者當下沒空處理就再也找不到。
3. **前端放瀏覽專利頁**（推翻規格書「左導覽獨立項」）：低頻維護作業不佔常駐導覽位置，
   且該頁表格本身就顯示申請人欄，確認前後的變化直接對照得到。
4. **不帶 `current_name`**：它取的是該代碼 confirmed 的公司名稱，與專利表「申請人」欄
   同源（皆走 `code_alias_names` → COALESCE 第一順位），搬到瀏覽專利頁後冗餘。
   改帶規格書要的 `original_name`（收斂前原始字面），那才是確認中文名時需要的對照。

DB 整合行為另見 test_ai_company_zh_name_db.py。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)
PREFIX = "/api/v1"
STATIC_INDEX = Path(__file__).resolve().parents[1] / "backend" / "app" / "static" / "index.html"


class DraftStoreContractTests(unittest.TestCase):
    """列草稿查詢：只讀 ai_suggested，帶出 verdict 與原文名。"""

    def test_store_module_exposes_list_drafts(self):
        """草稿查詢要有單一入口，供 API 呼叫（不讓 API 層自己拼 SQL）。"""
        from backend.app.derived import company_alias_importer as mod

        self.assertTrue(
            hasattr(mod, "list_zh_name_drafts"),
            "company_alias_importer 缺 list_zh_name_drafts",
        )

    def test_list_drafts_sql_filters_ai_suggested(self):
        """只列 AI 草稿（ai_suggested），不得混入 confirmed 或其他態。"""
        from backend.app.derived.company_alias_importer import _LIST_DRAFTS_SQL

        self.assertIn("ai_suggested", _LIST_DRAFTS_SQL)

    def test_list_drafts_sql_exposes_verdict(self):
        """帶出 zh_name_verdict：前端要能區分 translated 與 keep_original。"""
        from backend.app.derived.company_alias_importer import _LIST_DRAFTS_SQL

        self.assertIn("zh_name_verdict", _LIST_DRAFTS_SQL)

    def test_list_drafts_sql_exposes_original_name(self):
        """帶出原文名：確認中文名時要看得到收斂前的原始字面。"""
        from backend.app.derived.company_alias_importer import _LIST_DRAFTS_SQL

        self.assertIn("original_name", _LIST_DRAFTS_SQL)

    def test_list_drafts_supports_pagination(self):
        """支援 limit/offset（規格 B：大量代碼要分頁，不一次全吐）。"""
        import inspect

        from backend.app.derived.company_alias_importer import list_zh_name_drafts

        sig = inspect.signature(list_zh_name_drafts)
        for param in ("limit", "offset"):
            with self.subTest(param=param):
                self.assertIn(param, sig.parameters, f"list_zh_name_drafts 缺 {param}")


class DraftApiContractTests(unittest.TestCase):
    """API：三支端點的路由與驗證契約。

    ⚠ 路由存在性以 openapi() 檢查，不打 HTTP——打了會真的連 DB，
    測試環境無 DB 時錯誤訊息會蓋掉真正要驗的「路由有沒有註冊」。
    也不可用 `app.routes`：本版 FastAPI 以 `_IncludedRouter` 包裝 include 進來的 router，
    `app.routes` 不會攤平成個別路由物件，掃 `.path` 一律掃不到（實測全部 router 皆然）。
    """

    def _paths(self) -> set[str]:
        return set(app.openapi()["paths"].keys())

    def test_list_drafts_endpoint_registered(self):
        """GET /company-zh-drafts 已註冊（規格 B 的路徑）。"""
        self.assertIn(f"{PREFIX}/company-zh-drafts", self._paths())

    def test_confirm_endpoint_registered(self):
        """POST /company-zh-drafts/confirm 已註冊（規格 C 的路徑）。"""
        self.assertIn(f"{PREFIX}/company-zh-drafts/confirm", self._paths())

    def test_generate_endpoint_registered(self):
        """POST /company-zh-drafts/generate 已註冊（使用者定案：手動觸發，不自動）。"""
        self.assertIn(f"{PREFIX}/company-zh-drafts/generate", self._paths())

    def test_confirm_requires_action(self):
        """action 為必填三態之一；缺漏或非法值一律 422。"""
        resp = client.post(
            f"{PREFIX}/company-zh-drafts/confirm",
            json={"items": [{"code": "A1"}]},
        )
        self.assertEqual(resp.status_code, 422, "action 缺漏應 422")

    def test_confirm_rejects_unknown_action(self):
        """非 confirm/reject/edit 的 action 一律 422，不猜語意。"""
        resp = client.post(
            f"{PREFIX}/company-zh-drafts/confirm",
            json={"items": [{"code": "A1", "action": "delete"}]},
        )
        self.assertEqual(resp.status_code, 422)

    def test_edit_action_requires_name(self):
        """edit＝以使用者改過的名字確認，缺 name 就沒東西可寫 → 422。"""
        resp = client.post(
            f"{PREFIX}/company-zh-drafts/confirm",
            json={"items": [{"code": "A1", "action": "edit"}]},
        )
        self.assertEqual(resp.status_code, 422)

    def test_confirm_empty_items_is_noop(self):
        """items 為空＝無事可做，回 0 而非報錯（不進 DB，故測試環境可驗）。"""
        resp = client.post(f"{PREFIX}/company-zh-drafts/confirm", json={"items": []})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["confirmed"], 0)


class ConfirmSemanticsTests(unittest.TestCase):
    """裁決語意：確認才寫入與刪草稿，略過一律保留。"""

    def setUp(self):
        self.src = (
            Path(__file__).resolve().parents[1]
            / "backend" / "app" / "api" / "company_aliases.py"
        ).read_text(encoding="utf-8")

    def test_uses_existing_apply_function(self):
        """確認走既有 apply_confirmed_display_names——寫入規則只有一份，不自寫 SQL 繞過。"""
        self.assertIn("apply_confirmed_display_names", self.src)

    def test_reject_keeps_draft(self):
        """⚠ 略過（reject）必須保留草稿：使用者當下沒空處理，下次還要看得到。

        這是使用者 2026-07-27 明示要求——未裁決的草稿不能消失。
        """
        self.assertIn("reject", self.src)
        self.assertIn("略過", self.src, "reject 語意須在程式碼中載明為『略過、草稿保留』")

    def test_confirm_enqueues_refresh_derived(self):
        """確認後必須重跑 refresh，否則專利表／報表看起來「沒變」。

        收斂名存在 derived_layer.report_patent_base，confirmed 只寫進 company_aliases
        對照表——兩者之間靠 refresh_report_patent_base 銜接。少了這步，使用者確認完
        回專利表會發現公司名還是舊的，誤以為確認失敗。
        """
        self.assertIn("refresh_derived", self.src)


# ⚠ FrontendReviewUiTests（原在此）已於 2026-07-29 移除：使用者定案「整個中文名草稿
# 區塊都拿掉」，前端 renderZhNameDrafts／裁決按鈕／#zh-name-drafts 容器均已不存在，
# 該 class 驗的是已移除的 UI。中文名改由使用者在代碼組「公司中文名稱」欄直接填。
# 本檔其餘 class（DraftStore／DraftApi／ConfirmSemantics）驗的是**後端**，後端保留不動。

if __name__ == "__main__":
    unittest.main()
