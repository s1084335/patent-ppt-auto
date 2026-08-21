"""用詞統一為「設計」（tasks §4）。

## 為什麼要改

三分法（`transforms/patent_kind`）對使用者講「發明／新型／**設計**」，
報表卻講「**外觀**保護策略」「只走**外觀**」——同一件事兩個名字。
使用者 2026-08-19：「外觀就是用專利種類啊，就算改設計邏輯還是一樣。」
＝純用詞，邏輯不動。

## 為什麼這一節被列為「唯一不可逆」

規格原本假設「外觀」有兩種語意（專利**類型** vs 產品**造形**），誤改會破壞
文獻備註。⚠ **實查 112 處後結論不同**：源碼裡 **112/112 全是類型語意，
0 處造形**。最接近的 `narrative.md`「外觀可能補足產品形態保護」仍是在講
外觀設計這個類型保護產品形態，主詞是類型。

真正的風險**不在源碼在執行期資料**——文獻備註與摘要原文可能含「外觀」指造形。
本節只改標籤與常數、不碰資料，那個風險不成立。

⚠ 但**保留**「外觀設計」這個法律名詞（CN 的正式用語）不改：它是專有名詞，
不是我們的欄名。改動範圍嚴格限於 §4.2 點名的那組。

## ⚠ 做反向驗證時踩到的坑（2026-08-19，記在這裡因為它會再發生）

突變腳本把 `外觀保護策略` 換成 `設計保護策略` 再還原，結果**還原後測試仍紅**，
而檔案內容明明是對的。根因：兩個字串**位元組長度相同**（各 6 個中文字＝18 bytes），
又在同一秒內寫入，CPython 的 `.pyc` 失效判斷是「來源 mtime ＋ size」，
兩者都沒變 → **沿用舊 bytecode**，還原變成隱形的。

⚠ 這比看起來嚴重：等長突變會讓「還原成功」與「還原失敗」長得一模一樣，
於是可能把「閘門守住了」誤讀成「沒守住」，或反過來。
做等長字串突變時要清 `__pycache__`。

## 這個測試守什麼

1. 改過的那組不得殘留舊字
2. **前後端一致**：`index.html` 自己也寫了一次報表中文名——只改後端的話
   畫面會繼續顯示舊名，而且不會有任何東西報錯
3. 「外觀設計」法律名詞未被連坐改掉
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: §4.2 點名要改的那組（舊 → 新）
RENAMES = {
    "外觀保護策略": "設計保護策略",
    "只走外觀": "只走設計",
    "技術+外觀": "技術+設計",
}

#: 改動範圍內的檔案。⚠ 不含 openspec/archive（§4.3：歷史紀錄不改）
SCOPE = [
    ROOT / "backend/app/reports/chart_runner.py",
    ROOT / "backend/app/reports/content_blocks.py",
    ROOT / "backend/app/reports/report_definitions.py",
    ROOT / "backend/app/static/index.html",
    ROOT / "backend/app/worker/prompts/report-narrative-flow.md",
]


class RenamedTermsTests(unittest.TestCase):
    def test_old_terms_gone_from_scope(self):
        for path in SCOPE:
            text = path.read_text(encoding="utf-8")
            for old in RENAMES:
                with self.subTest(file=path.name, term=old):
                    self.assertNotIn(
                        old, text,
                        f"{path.name} 仍有舊用詞「{old}」——"
                        "三分法對使用者講「設計」，報表不該講「外觀」")

    def test_axis_uses_design(self):
        from backend.app.reports.chart_runner import DESIGN_STRATEGY_AXIS

        self.assertEqual(DESIGN_STRATEGY_AXIS, ("技術", "設計"))

    def test_strategy_type_values_use_design(self):
        """驗**產出的值**不只是常數——值才是印在報表上的東西。"""
        from backend.app.reports.content_blocks import design_protection_strategy

        rows = design_protection_strategy([
            {"patent_id": 1, "applicant_display_name": "只設計公司",
             "document_kind": "S", "application_year": 2020},
            {"patent_id": 2, "applicant_display_name": "雙軸公司",
             "document_kind": "S", "application_year": 2021},
            {"patent_id": 3, "applicant_display_name": "雙軸公司",
             "document_kind": "A", "application_year": 2022},
        ])
        kinds = {r["applicant"]: r["strategy_type"] for r in rows}
        self.assertEqual(kinds.get("只設計公司"), "只走設計")
        self.assertEqual(kinds.get("雙軸公司"), "技術+設計")


class FrontendBackendConsistencyTests(unittest.TestCase):
    """🔴 跨語言的同一份知識：報表中文名在前端又寫了一次。

    `index.html` 有 `['design_protection_detail', '外觀保護策略']` 的對照。
    ⚠ 只改後端 `label_zh`，畫面會繼續顯示舊名，而且**不會有任何東西報錯**
    ——與色票那次（前端 CSS 變數）同型，處理方式一致：加一致性測試。
    """

    def test_report_label_matches_between_backend_and_frontend(self):
        from backend.app.reports.report_definitions import REPORT_DEFINITIONS

        definition = REPORT_DEFINITIONS.get("design_protection_detail")
        self.assertIsNotNone(definition, "找不到 design_protection_detail 定義")
        backend_label = definition.label_zh
        html = (ROOT / "backend/app/static/index.html").read_text(encoding="utf-8")
        m = re.search(r"\['design_protection_detail',\s*'([^']+)'\]", html)
        self.assertIsNotNone(m, "前端找不到 design_protection_detail 的中文名對照")
        self.assertEqual(
            m.group(1), backend_label,
            "前後端的報表中文名分岔了——畫面顯示的與規格定義的不是同一個")


class LegalTermPreservedTests(unittest.TestCase):
    """⚠ 「外觀設計」是 CN 的法律名詞，不是我們的欄名，**不得連坐改掉**。"""

    def test_legal_term_still_present(self):
        text = (ROOT / "backend/app/transforms/patent_kind.py").read_text(
            encoding="utf-8")
        self.assertIn(
            "外觀設計", text,
            "法律名詞「外觀設計」被連坐改掉了——那是專有名詞不是我們的用詞")

    def test_kind_label_is_design(self):
        """三分法給使用者看的標籤本來就叫「設計」，這是本節要對齊的目標。"""
        from backend.app.transforms.patent_kind import patent_kind

        self.assertEqual(patent_kind({"document_kind": "S"}), "設計")


if __name__ == "__main__":
    unittest.main()
