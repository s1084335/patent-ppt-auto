"""既有代碼組新增變體（2026-07-29 使用者需求「新增變體到既有組，做出來」）。

## 問題

「資料庫已有的代碼」區塊只有四個操作：儲存公司名／補上代碼／刪除整組／
**移除**變體——**沒有新增變體**。

使用者要把新寫法歸進既有組，只能繞路：用上方「新增一組」的表單填**既有代碼**，
且中文名與正規化名稱必須跟現有完全一致，否則觸發 409
「同一代碼對應到不同正規化名稱」。

## 為什麼後端不用改

`apply_confirmed_display_names` 對每個變體：
- 別稱已存在 → UPDATE 那列
- **別稱不存在 → INSERT 新列**

已經支援「加變體不覆蓋既有」。缺的只是一個不必重打名稱的入口。

## 定案（使用者：「新增變體做成集中入口」「一個區塊下拉選組，按鈕統一」）

**一個集中區塊**，不是每組各一個入口：

    ── 新增變體 ──
      歸入哪組 [UN226597 — 南通鐵人運動用品 ▾]
      變體     [____________________]
      [新增]

好處：不必展開各組去找按鈕；代碼／中文名／正規化名稱由選中的組自動帶入
（使用者只填變體字面），完全避開 409。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


def _js_function(html: str, name: str) -> str:
    m = re.search(
        r"^(async\s+)?function " + re.escape(name) + r"\([^)]*\) \{(.*?)^\}",
        html, re.S | re.M)
    assert m, f"找不到函式 {name}"
    return m.group(2)


class AddVariantUiTests(unittest.TestCase):
    """既有組要有新增變體的入口。"""

    def test_function_exists(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("function addVariantToGroup", html,
                      "缺新增變體函式——使用者只能繞路走『新增一組』表單")

    def test_central_block_rendered(self):
        """集中區塊：一個下拉（選組）＋一個輸入框（變體）＋一顆按鈕。

        ⚠ 不是每組各一個入口——使用者定「做成集中入口」，
        免得要展開六組才找得到按鈕。
        """
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("add-variant-group-select", html, "缺選組下拉")
        self.assertIn("add-variant-alias", html, "缺變體輸入框")
        self.assertIn("addVariantToGroup()", html, "缺新增按鈕的呼叫")

    def test_select_lists_all_groups(self):
        """下拉要列出全部既有組，且顯示得出是哪一組（代碼＋名稱）。"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("addVariantGroupOptionsHtml", html,
                      "缺選項產生函式——下拉會是空的")


class AddVariantBehaviourTests(unittest.TestCase):
    """行為：自動帶入該組現值，使用者只填變體。"""

    @staticmethod
    def _body() -> str:
        return _js_function(INDEX_HTML.read_text(encoding="utf-8"), "addVariantToGroup")

    def test_payload_keys_match_backend_model(self):
        """🔴 payload 欄位名必須與後端 `CodeGroup` model 完全一致。

        實測踩過：前端送 `aliases`、後端欄位叫 `variants`——**Pydantic 對未知
        欄位靜默忽略**，變體被丟掉、只更新兩個正式名的列，API 照回 200
        （inserted:0 updated:2）。使用者以為新增成功，資料庫裡沒有。

        ⚠ 本測試初版只驗「payload 裡有 aliases 這個字串」，正好鎖住了錯的名字
        ——驗欄位名必須以**後端 model 為準**，不能自己列一份。
        """
        from backend.app.api.company_aliases import CodeGroup

        body = self._body()
        model_fields = set(CodeGroup.model_fields)
        for key in ("code", "zh_name", "normalized_name", "variants"):
            with self.subTest(key=key):
                self.assertIn(key, model_fields, f"{key} 不是後端 model 的欄位")
                self.assertIn(key + ":", body, f"payload 缺 {key}")
        self.assertNotIn("aliases:", body,
                         "`aliases` 不是 CodeGroup 的欄位，會被 Pydantic 靜默丟棄")

    def test_reuses_confirm_endpoint(self):
        """走既有 /company-codes/confirm，不另造端點。

        後端已支援 upsert（別稱不存在就 INSERT），新增端點只會是第二個落點。
        """
        body = self._body()
        self.assertIn("/company-codes/confirm", body,
                      "應複用既有 confirm 端點（後端零改動）")

    def test_rejects_empty_variant(self):
        """沒填變體就送出＝無意義請求，前端先擋。"""
        body = self._body()
        self.assertRegex(body, r"if\s*\(\s*!\s*\w+\s*\)",
                         "缺空值檢查")

    def test_refreshes_after_add(self):
        """新增後要重載清單——否則使用者看不到剛加的變體。"""
        body = self._body()
        self.assertTrue(
            "loadExistingCompanyCodes" in body or "callGroupMaintenance" in body,
            "新增後沒有刷新，畫面不會更新")


if __name__ == "__main__":
    unittest.main()


class ActiveVariantTargetTests(unittest.TestCase):
    """待補清單填入目標要**看得見且可指定**（2026-07-29 使用者實機回報）。

    使用者原話：「目前按待補代碼區的標籤只會出現在第一組，如果我想同時寫兩組
    就會沒辦法」。

    ## 根因

    `activeVariant`（填入目標）只由變體輸入框的 `onfocus` 更新。使用者若**沒先
    點過任何變體格**，它停在初始值 `{g:0, v:0}` → 點待補清單永遠填到第 1 組。

    而畫面上**沒有任何提示**告訴使用者「現在會填到哪一組」——這是隱性狀態，
    使用者無從得知也無從改變（除非碰巧先點了那一格）。

    ## 定案

    每組加「填這組」按鈕：明示目前目標（高亮），且可直接指定，不必先點輸入框。
    """

    @staticmethod
    def _html() -> str:
        return INDEX_HTML.read_text(encoding="utf-8")

    def test_setter_function_exists(self):
        self.assertIn("function setActiveVariant", self._html(),
                      "缺設定目標的函式——狀態只能靠 onfocus 隱性更新")

    def test_group_shows_target_button(self):
        """每組要有可點的目標按鈕。"""
        body = _js_function(self._html(), "codeGroupHtml")
        self.assertIn("setActiveVariant(", body, "組標題列沒有指定目標的入口")
        self.assertIn("填這組", body, "缺目標按鈕文字")

    def test_active_group_highlighted(self):
        """目前目標要在畫面上看得出來——這正是使用者說「不知道會填到哪」的解方。"""
        body = _js_function(self._html(), "codeGroupHtml")
        self.assertIn("activeVariant.g === idx", body,
                      "沒有依目前目標切換樣式，使用者仍看不出填到哪組")

    def test_focus_uses_setter(self):
        """輸入框 onfocus 改走同一個 setter，狀態只有一處寫入。"""
        body = _js_function(self._html(), "codeGroupHtml")
        self.assertIn('onfocus="setActiveVariant(', body)
        self.assertNotIn("onfocus=\"activeVariant =", body,
                         "仍有直接賦值的舊寫法——同一狀態兩處寫入")
