"""AI 中文名草稿的「掃描範圍」提示（2026-07-28 使用者實機困惑）。

## 背景

使用者問：「AI 中文名是上面要先寫入資料庫再按 AI 草稿?」——答案是對，但 UI 沒說清楚。

根因：runner 的 `PENDING_SQL` 只掃 DB 內 `review_status='confirmed'` 的列，
**看不到使用者正在編輯、尚未寫入的組**。使用者填完一組還沒按「確定寫入資料庫」
就去按「產生公司中文名草稿」，得到「目前沒有待確認的中文名草稿」，不知道為什麼。
流程說明有寫「AI 只看已寫入的代碼」，但那行小字在捲軸上方，下方編輯區看不到。

## 兩個改動

① 「產生公司中文名草稿」鈕旁說明掃描範圍＝**已寫入資料庫**、尚無中文名的代碼組。
② `companyCodeGroups` 有未寫入的編輯中組時，主動警示「AI 不會處理它們」。

⚠ 本檔一律用 `js_function()`（括號深度取真實 function body）在**函式本體內**斷言，
不數整份 HTML 的字串次數——本專案今天已因此假性通過一次（把說明註解也算進去）。
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


class ZhDraftScopeHintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = client.get("/").text

    def js_function(self, name: str) -> str:
        """用括號深度取出真實 JS function body，避免只被註解餵飽。"""
        idx = self.html.find(f"function {name}(")
        if idx < 0:
            idx = self.html.find(f"async function {name}(")
        self.assertGreaterEqual(idx, 0, f"找不到 {name}() 定義")
        start = self.html.find("{", idx)
        depth = 0
        for pos in range(start, len(self.html)):
            if self.html[pos] == "{":
                depth += 1
            elif self.html[pos] == "}":
                depth -= 1
                if depth == 0:
                    return self.html[idx:pos + 1]
        self.fail(f"{name}() 大括號未閉合")

    def _body_without_comments(self, name: str) -> str:
        """函式本體去掉 `//` 行註解——說明性註解不算數，要真的畫在畫面上。"""
        body = self.js_function(name)
        return "\n".join(
            line.split("//", 1)[0] if line.strip().startswith("//") else line
            for line in body.splitlines())

    # ── ① 掃描範圍說明 ──────────────────────────────

    def test_scope_hint_says_written_to_db(self):
        """鈕旁必須說明 AI 掃的是**已寫入資料庫**的代碼組。"""
        body = self._body_without_comments("renderZhNameDrafts")
        self.assertIn("已寫入資料庫", body,
                      "「產生公司中文名草稿」鈕旁未說明掃描範圍＝已寫入資料庫的組")

    def test_scope_hint_is_next_to_the_button(self):
        """說明要跟那顆鈕在同一段（不是塞在捲軸上方的流程說明裡）。"""
        body = self._body_without_comments("renderZhNameDrafts")
        self.assertIn("triggerZhNameDrafts()", body)
        btn = body.index("triggerZhNameDrafts()")
        hint = body.index("已寫入資料庫")
        self.assertLess(abs(hint - btn), 900,
                        "掃描範圍說明離觸發鈕太遠，使用者在鈕旁看不到")

    # ── ② 未寫入警示 ────────────────────────────────

    def test_unsaved_warning_helper_exists(self):
        """需有判斷「有未寫入編輯中組」的函式。"""
        self.assertRegex(self.html, r"function unsavedCodeGroupCount\s*\(",
                         "缺 unsavedCodeGroupCount()——無法判斷有沒有未寫入的組")

    def test_unsaved_count_covers_all_four_fields(self):
        """⚠ 四欄拆分後 blankCodeGroup() 多了欄位，判斷必須涵蓋新欄。

        鎖真實行為：函式本體要真的檢查 code／zh_name／normalized_name／variants，
        少檢查一欄就會漏判（使用者只填了中文名時仍算「有未寫入」）。
        """
        body = self._body_without_comments("unsavedCodeGroupCount")
        for field in ("code", "zh_name", "normalized_name", "variants"):
            self.assertIn(field, body, f"unsavedCodeGroupCount() 未檢查 {field} 欄")

    def test_blank_group_fields_all_checked(self):
        """blankCodeGroup() 的每個欄位都要出現在 unsavedCodeGroupCount() 裡。

        用 blankCodeGroup() 的真實定義推導欄位清單，日後再加欄這條會自動抓到漏檢。
        """
        import re

        blank = self.js_function("blankCodeGroup")
        fields = set(re.findall(r"(\w+)\s*:", blank))
        body = self._body_without_comments("unsavedCodeGroupCount")
        missing = sorted(f for f in fields if f not in body)
        self.assertEqual(missing, [],
                         f"blankCodeGroup() 有欄位未納入未寫入判斷：{missing}")

    def test_warning_rendered_in_draft_area(self):
        """草稿區**兩個分支**（有草稿／無草稿）都要真的畫出警示。

        追整條鏈：renderZhNameDrafts 呼叫 warning helper → 該 helper 呼叫
        unsavedCodeGroupCount → 文案指出補救動作。只斷言「有這個字串」會被
        helper 存在但沒被呼叫的情況騙過。
        """
        body = self._body_without_comments("renderZhNameDrafts")
        self.assertIn("unsavedCodeGroupsWarningHtml()", body,
                      "renderZhNameDrafts() 沒呼叫未寫入警示——警示不會出現")
        # 兩個分支各自把警示接進 innerHTML（無草稿分支 return 早，漏掉就看不到）。
        branches = body.count("unsavedWarn")
        self.assertGreaterEqual(
            branches, 3,
            f"警示未同時掛在有草稿／無草稿兩個分支（unsavedWarn 出現 {branches} 次）")

        helper = self._body_without_comments("unsavedCodeGroupsWarningHtml")
        self.assertIn("unsavedCodeGroupCount()", helper,
                      "警示 helper 沒用到未寫入計數")
        self.assertIn("確定寫入資料庫", helper,
                      "警示未指出補救動作（按「確定寫入資料庫」）")


if __name__ == "__main__":
    unittest.main()
