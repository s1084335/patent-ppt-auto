"""不歸戶清單（規格 applicant-code-grouping-spec.md 批次 c）。

## 問題

待補清單有些名稱**永遠歸不掉**——實測 11 項中 7 個是自然人
（`Zeng Qing`、`Liu Qun`…），另有機構（`SKI-ROW INC DBA ENERGYFIT`）。
使用者手動也歸不進任何公司組，於是永遠掛著，每次看到都要重新判斷。

⚠ 實測 L1/L2 對這 11 項**全部無命中**——它們與既有 21 組無任何字面關聯，
自動化永遠處理不了，只能標記。

## 落點（B2，不動 schema）

| 欄位 | 值 |
|---|---|
| `申請人代碼` | NULL |
| `別稱` | 要不歸戶的名稱 |
| `正規化名稱` | **填該名稱本身** ← B2 的核心 |
| `source_type` | `filter`（既有 CHECK 值，語意＝被篩掉） |
| `review_status` | `confirmed`（→ 自動離開待補清單） |

## 為何正規化名稱填自身

報表用**別稱字面反查** `confirmed` 列，`display_name = COALESCE(中文名, 正規化名稱)`。
留空的話 `Zeng Qing` 會被反查命中卻顯示**空白**。填自身 → 顯示原名，
**與標記前完全相同**，且不必改報表 SQL（4 處）。

## 措辭：用「不歸戶」

⚠ 不用「忽略」或「個人」——那些名稱不只是個人（含機構），
「不歸戶」描述的是**動作**不是身分。
"""
from __future__ import annotations

import inspect
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "backend" / "app" / "static" / "index.html"


class NotGroupedApiTests(unittest.TestCase):
    """後端：標記／還原／列出不歸戶名稱。"""

    def test_routes_registered(self):
        """三支端點都要註冊，且路徑不被 `/company-codes/{code}` 吃掉。

        ⚠ 本專案踩過「看起來註冊了卻打不到」——`/reports/{job_id}` 宣告 int
        參數時，`/reports/ppt-layout` 會先比對到它而回 422。
        `not-grouped` 同樣是靜態字串路徑，故檢查它**排在動態路徑之前**。
        ⚠ 不用 TestClient 實打：那會連 DB（本機 .env 指向 pooler），
        測試不得依賴外部連線；路由順序用 app.routes 檢查即可。
        """
        from backend.app.main import app

        # ⚠ 用 OpenAPI schema 而非 `app.routes`：後者對 include_router 進來的
        # 子路由讀不到 `path` 屬性（實測全是空字串），會誤判成「未註冊」。
        spec = app.openapi()["paths"]
        for expected, method in (
            ("/api/v1/company-codes/not-grouped", "get"),
            ("/api/v1/company-codes/not-grouped", "post"),
            ("/api/v1/company-codes/not-grouped/{alias_id}", "delete"),
        ):
            with self.subTest(path=expected, method=method):
                self.assertIn(expected, spec, f"{expected} 未註冊")
                self.assertIn(method, spec[expected], f"{method.upper()} {expected} 未註冊")

    def test_source_type_in_db_whitelist(self):
        """🔴 寫入的 source_type 必須在 DB CHECK 白名單內。

        ⚠ 2026-07-30 實機 500：原本寫 `'filter'`，但實際白名單是
        excel_seed／wips_lookup／manual／ai_suggested——**沒有 filter**。
        我先前查到的 filter 是**別張表**的約束，未核對表名就用了。

        ⚠ 本測試初版只斷言「原始碼含 'filter' 字串」，那**驗不到能不能寫入**——
        字串在、CHECK 擋掉，測試照樣綠。改為比對實際白名單。
        """
        from backend.app.api import company_aliases as api

        # DB CHECK 白名單（migration 0021 起）
        allowed = {"excel_seed", "wips_lookup", "manual", "ai_suggested"}
        src = inspect.getsource(api.mark_name_not_grouped)
        used = set(re.findall(r"'(\w+)', 'confirmed'\)", src))
        self.assertTrue(used, "找不到寫入的 source_type")
        self.assertTrue(
            used <= allowed,
            f"source_type {used - allowed} 不在 CHECK 白名單 {allowed}——寫入會 500")

    def test_normalized_name_filled_with_self(self):
        """🔴 B2 核心：正規化名稱填該名稱本身。

        留空的話報表反查會命中卻顯示空白（display_name 兩欄皆 NULL）。
        """
        from backend.app.api import company_aliases as api

        src = inspect.getsource(api.mark_name_not_grouped)
        # 別稱與正規化名稱應綁同一個值
        self.assertRegex(
            src, r'"正規化名稱".*"別稱"|name,\s*name',
            "正規化名稱未填自身——報表會顯示空白")

    def test_marked_is_confirmed(self):
        """標記為 confirmed 才會自動離開待補清單（其排除條件即 confirmed）。"""
        from backend.app.api import company_aliases as api

        self.assertIn("confirmed", inspect.getsource(api.mark_name_not_grouped))


class NotGroupedUiTests(unittest.TestCase):
    """前端：待補清單第三個入口 ＋ 可收合的不歸戶清單。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_chip_has_third_entry(self):
        self.assertIn("markNameNotGrouped", self.html, "待補標籤缺「不歸戶」入口")

    def test_existing_entries_preserved(self):
        """🔴 原有兩個入口不得受影響（規格「不做的事」明列）。"""
        self.assertIn("fillVariantFromPending(", self.html,
                      "點擊填入變體的行為被移除")
        self.assertIn("showPendingNamePatents(", self.html,
                      "🔍 篩選入口被移除——那是使用者明確要的功能")
        self.assertIn("chip-find", self.html, "🔍 圖示樣式被移除")

    def test_not_grouped_section_rendered(self):
        self.assertIn("不歸戶", self.html, "缺不歸戶清單區塊")
        self.assertIn("renderNotGroupedNames", self.html, "缺清單渲染函式")

    def test_restore_available(self):
        """⚠ 標記不是刪除——要能還原回待補清單。"""
        self.assertIn("restoreNotGroupedName", self.html, "缺還原入口")

    def test_wording_not_ignore_or_person(self):
        """⚠ 措辭用「不歸戶」，不用「忽略」「個人」（使用者 2026-07-30 定）。"""
        match = re.search(r"function renderNotGroupedNames\(.*?\n\}", self.html, re.S)
        self.assertIsNotNone(match, "找不到 renderNotGroupedNames")
        code = "\n".join(
            l for l in match.group(0).split("\n") if not l.strip().startswith("//"))
        for bad in ("忽略", "個人"):
            self.assertNotIn(bad, code, f"措辭不得用「{bad}」")


if __name__ == "__main__":
    unittest.main()
