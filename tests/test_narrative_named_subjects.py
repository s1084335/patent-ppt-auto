"""具名發現（Q14／RPT-012，2026-08-05 定案；2026-08-07 依 v10 收斂後實作）。

⚠ 規格收斂：08-05 的「具名發現卡」原含「標籤由內容產生」，該半已被
08-07 使用者定案「不用再定標籤、格式完全不固定」取代（見 decisions）。
剩下的核心＝**具名**：判讀要點名具體對象（申請人、主題、專利號），
不得整頁只講泛稱（「主要申請人」「部分廠商」）。

程式鎖：呼叫端把該變體可具名對象清單（申請人名／主題名／專利號，取自
report rows）傳給 validator，整頁沒命中任何一個就記警告。⚠ 只寫進 prompt
沒有程式檢查＝沒有規則（known-issues C-1）。
"""
from __future__ import annotations

import unittest

from backend.app.worker import ai_narrative_runner as runner


SUBJECTS = {"applicant_ranking:default": ["廈門帝瑪斯健康科技", "曾晴", "祺驊"]}


def _doc(points):
    return {"reports": {"applicant_ranking": {"variants": {"default": {
        "headline": "前二為同一陣營",
        "points": points,
        "text": "\n\n".join(p["text"] for p in points),
    }}}}}


class NamedSubjectLockTests(unittest.TestCase):
    def _warn(self, points, subjects=SUBJECTS):
        return [w for w in runner.validate_narrative_contract(
            _doc(points), subjects=subjects) if "具名" in w]

    def test_generic_only_warns(self):
        pts = [{"text": "主要申請人合計 14 件，占比偏高"},
               {"text": "部分廠商近兩年才進場，反映後進者仍在布局"}]
        self.assertTrue(self._warn(pts), "整頁只有泛稱應記警告")

    def test_named_subject_passes(self):
        pts = [{"text": "廈門帝瑪斯健康科技 13 件居首"},
               {"text": "與曾晴共同申請 12 件，反映兩者實為同一陣營"}]
        self.assertEqual(self._warn(pts), [])

    def test_no_subject_list_skips_lock(self):
        """呼叫端沒給清單（例如無 rows 的頁）不得誤報。"""
        pts = [{"text": "本頁共 8 件，反映布局集中"}]
        self.assertEqual(self._warn(pts, subjects=None), [])

    def test_unknown_variant_skips_lock(self):
        pts = [{"text": "主要申請人合計 14 件"},
               {"text": "反映集中度偏高"}]
        self.assertEqual(self._warn(pts, subjects={"other:default": ["X"]}), [])


class PromptTests(unittest.TestCase):
    def test_prompt_requires_naming(self):
        from pathlib import Path

        prompt = runner.build_prompt(Path("X"), "v")
        self.assertIn("具名", prompt)


if __name__ == "__main__":
    unittest.main()
